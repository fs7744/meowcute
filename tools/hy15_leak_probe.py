"""Review 用：检查 hy15 三项优化有没有资源泄漏。

重点查三类：
1. i2v ↔ t2v 管线切换（get_pipeline 会重建管线）——旧管线的显存是否真的归还。
   accelerate 的 cpu_offload 钩子会给所有 submodule 注册 hook，若有全局 registry
   持有引用，旧管线就释放不掉，连续切换会一路涨到 OOM。
2. 分辨率反复切换——target_size 写在管线单例上，确认每次都会重算、不会串味。
3. 连续多次生成——显存/进程内存是否单调增长。

每轮都打印 allocated（真占用）与 reserved（PyTorch 缓存器持有）两个值：
reserved 偏高是正常的（缓存复用），**allocated 必须能回落到基线**。
"""
import gc
import sys

sys.path.insert(0, ".")

import time
from pathlib import Path

PROMPT = "角色侧视行走：双腿交替迈步，步态平稳。"
NEG = "模糊, 闪烁"
REF = Path("data/projects/p-0d3f091b4c3c/images/vs-20261025-221119-46635a63.png")
ROUND_FRAME = 13          # 最小帧数，只为触发完整流程，不为画质
ROUND_STEPS = 6


def snap(tag):
    import torch

    alloc = torch.cuda.memory_allocated() / 2**30
    reserved = torch.cuda.memory_reserved() / 2**30
    peak = torch.cuda.max_memory_allocated() / 2**30
    try:
        import psutil

        rss = psutil.Process().memory_info().rss / 2**30
        rsstxt = f"进程RSS={rss:5.2f}G"
    except Exception:
        rsstxt = "进程RSS=?"
    print(f"    [{tag:<22}] 显存 占用={alloc:5.2f}G 缓存={reserved:5.2f}G 峰值={peak:5.2f}G  {rsstxt}",
          flush=True)
    return alloc


def main():
    import torch
    from PIL import Image

    from backend.app.providers import hy15

    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    emb = hy15.encode_texts(PROMPT, NEG)
    print("== 泄漏检查：连续调用后 allocated 是否回落 ==\n")

    allocs = []

    def run_i2v_once(tag, *, width=0, height=0, window=0, seed=1):
        t0 = time.time()
        frames, meta = hy15.run_i2v(img, emb, frames=ROUND_FRAME, steps=ROUND_STEPS,
                                    guidance=1.0, seed=seed, window=window,
                                    width=width, height=height)
        dt = time.time() - t0
        size = meta.get("size")
        print(f"  {tag}: {len(frames)}帧 size={size} target={getattr(hy15._PIPE, 'target_size', '?')} {dt:5.1f}s")
        return frames, meta

    # 1) 基线：首次加载
    run_i2v_once("第1次 I2V（含加载）")
    a = snap("第1次后")
    allocs.append(a)

    # 2) 同分辨率重复（看纯调用是否涨）
    for i in (2, 3):
        run_i2v_once(f"第{i}次 I2V 同参数")
        allocs.append(snap(f"第{i}次后"))

    # 3) 切分辨率（target_size 会被改写，确认下次不传会复原）
    run_i2v_once("切 256x256", width=256, height=256)
    allocs.append(snap("256x256 后"))
    run_i2v_once("切回 384x512", width=384, height=512)
    allocs.append(snap("384x512 后"))
    run_i2v_once("不传分辨率(应回默认)", width=0, height=0)
    allocs.append(snap("回默认后"))

    # 4) 切 T2V（触发管线重建）—— 这是最可能泄漏的一步
    frames_t, meta_t = hy15.run_t2v(emb, frames=ROUND_FRAME, steps=ROUND_STEPS,
                                    guidance=1.0, seed=1, width=320, height=192)
    print(f"  切 T2V(320x192): {len(frames_t)}帧 size={meta_t.get('size')}")
    at = snap("切到 T2V 后")
    allocs.append(at)

    # 5) 切回 I2V（再重建一次）
    run_i2v_once("切回 I2V", width=384, height=512)
    allocs.append(snap("切回 I2V 后"))

    # 6) 再切 T2V，如果旧管线没释放，这一步最容易 OOM
    frames_t2, meta_t2 = hy15.run_t2v(emb, frames=ROUND_FRAME, steps=ROUND_STEPS,
                                      guidance=1.0, seed=2)
    print(f"  第2次切 T2V: {len(frames_t2)}帧 size={meta_t2.get('size')}")
    allocs.append(snap("第2次 T2V 后"))

    # 7) release
    hy15.release()
    gc.collect()
    torch.cuda.empty_cache()
    allocs.append(snap("release 后"))

    print("\n== allocated 序列（GB）==")
    print("   ", [round(x, 2) for x in allocs])
    base = allocs[1] if len(allocs) > 1 else allocs[0]
    steady = max(allocs[1:5]) - min(allocs[1:5]) if len(allocs) > 5 else 0.0
    print(f"\n  稳定期（同参数连跑）波动 max-min = {steady*1024:.0f}MB")
    print(f"  release 后占用 = {allocs[-1]:.2f}G  (期望 ≈ 0，说明管线真的释放了)")
    print("\n  判据：")
    print("   - allocated 在同参数重复调用间不应单调增长；")
    print("   - i2v→t2v 再回来，占用不应翻倍（否则旧管线没释放=泄漏）；")
    print("   - release 后应接近 0。")


def _rss():
    try:
        import psutil

        return psutil.Process().memory_info().rss / 2**30
    except Exception:
        return -1.0


def _inventory():
    """对象级盘点：直接数「还剩几个管线实例 + 多少张量字节」。

    为什么不用 psutil RSS：Windows 上 RSS 量的是 Working Set，长时间没访问的页
    会被换出到 pagefile，读数会掉到 0.2G 这种明显不合理的值（已实测到），
    完全没法用来判断泄漏。对象级盘点与换页无关，才可信。
    """
    import torch

    n_pipe, tensor_bytes = 0, 0
    for o in gc.get_objects():
        t = type(o).__name__
        if "Pipeline" in t and t.startswith("HunyuanVideo15"):
            n_pipe += 1
        elif torch.is_tensor(o):
            try:
                tensor_bytes += o.numel() * o.element_size()
            except Exception:
                pass
    return n_pipe, tensor_bytes


def main_objects(rounds: int = 3):
    """回到 engineer 语义：管线重建后，旧管线到底有没有被 Python 回收。"""
    from PIL import Image

    from backend.app.providers import hy15

    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    emb = hy15.encode_texts(PROMPT, NEG)

    print(f"== 对象级泄漏检查：{rounds} 轮 i2v↔t2v（管线每次都会重建）==\n")
    import torch

    for r in range(rounds):
        frames, _ = hy15.run_i2v(img, emb, frames=ROUND_FRAME, steps=ROUND_STEPS, seed=r + 1)
        nb = len(frames)
        del frames
        gc.collect()
        np_, tb = _inventory()
        print(f"  轮{r+1} I2V({nb}帧): 管线实例={np_}  张量总量={tb/2**30:5.2f}G  "
              f"显存={torch.cuda.memory_allocated()/2**30:.2f}G", flush=True)

        frames, _ = hy15.run_t2v(emb, frames=ROUND_FRAME, steps=ROUND_STEPS, seed=r + 1)
        del frames
        gc.collect()
        np_, tb = _inventory()
        print(f"  轮{r+1} T2V:        管线实例={np_}  张量总量={tb/2**30:5.2f}G  "
              f"显存={torch.cuda.memory_allocated()/2**30:.2f}G", flush=True)

    hy15.release()
    gc.collect()
    np_, tb = _inventory()
    print(f"  release 后:   管线实例={np_}  张量总量={tb/2**30:5.2f}G")
    print("\n  判据：管线实例应恒为 1（切?=已废弃的旧实例若没回收会 >1）；")
    print("        张量总量在多轮间应保持同一水平，不逐轮累加。")


def main_cycle(rounds: int = 4):
    """多轮 i2v→t2v→i2v 切换，看进程 RSS 是收敛成平台还是一路爬升。

    显存侧（allocated）在 main() 里已经证明不涨；这里专测**主机内存**：
    offload 模式下模型权重常驻 CPU RAM，管线反复重建时 glibc/torch allocator
    未必把页还给 OS，表现就是 RSS 只升不降。要区分「涨到历史峰值就平台」
    （可接受，不是泄漏）和「每轮都涨」（真泄漏）。
    """
    import torch
    from PIL import Image

    from backend.app.providers import hy15

    img = Image.open(REF).convert("RGBA") if REF.exists() else Image.new("RGB", (512, 512), (200, 200, 200))
    emb = hy15.encode_texts(PROMPT, NEG)

    print(f"== 主机内存周期检查：来回切换 {rounds} 轮 i2v↔t2v ==\n")
    rss_i2v, rss_t2v = [], []
    for r in range(rounds):
        frames, _ = hy15.run_i2v(img, emb, frames=ROUND_FRAME, steps=ROUND_STEPS,
                                 seed=r + 1, width=384, height=512)
        del frames
        gc.collect()
        rss_i2v.append(_rss())
        print(f"  轮{r + 1} I2V后: 进程RSS={rss_i2v[-1]:5.2f}G  显存占用="
              f"{torch.cuda.memory_allocated()/2**30:.2f}G", flush=True)

        frames, _ = hy15.run_t2v(emb, frames=ROUND_FRAME, steps=ROUND_STEPS, seed=r + 1)
        del frames
        gc.collect()
        rss_t2v.append(_rss())
        print(f"  轮{r + 1} T2V后: 进程RSS={rss_t2v[-1]:5.2f}G  显存占用="
              f"{torch.cuda.memory_allocated()/2**30:.2f}G", flush=True)

    print(f"\n  I2V 后 RSS 序列: {[round(x, 2) for x in rss_i2v]}")
    print(f"  T2V 后 RSS 序列: {[round(x, 2) for x in rss_t2v]}")
    if len(rss_i2v) >= 3:
        d_last = rss_i2v[-1] - rss_i2v[-2]
        d_first = rss_i2v[1] - rss_i2v[0]
        print(f"\n  I2V 增量: 第1→2轮 {d_first:+.2f}G，最后两轮 {d_last:+.2f}G")
        print("  判据：若末段增量接近 0 → 已收敛到历史峰值（allocator 缓存，不是泄漏）；"
              "若持续明显为正 → 真泄漏。")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "cycle":
        rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 4
        main_cycle(rounds)
    elif len(sys.argv) > 1 and sys.argv[1] == "objects":
        rounds = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        main_objects(rounds)
    else:
        main()
