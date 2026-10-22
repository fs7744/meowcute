"""四视图回归：多张正视图批量推导 + 背视图披风（背覆体积）守恒 A/B。

跑法（真机，需要 GPU）：
    .venv/Scripts/python.exe tools/test_view_batch.py
产物落 .verify/view-batch/，日志落 .verify/view-batch.log。

两个待验证的假设：
1. **抽卡配对**：正视图 n 张 → 侧/背/右各 n 张，结果带 pairIndex 指回源图；
   拼板按 pairIndex 成组挑图，角色才不会串。抽 2 张正视图时应该拿到 2 张
   背视图，且第 1 张的参考是 front-0、第 2 张的参考是 front-1。
2. **背覆体积守恒**：披风这类「正面只看见领口一条边」的部件，转背视图最
   容易被模型省掉。新指令加了一条通用几何条款（从肩背垂下/盖住后背/
   向两侧张开 → 连接点、下垂高度、张开宽度不变，至少与肩同宽）。
   A/B 用同图同种子对比「删掉该条款」的旧指令，用红色像素占比当客观指标。
"""
import asyncio
import pathlib
import sys

sys.path.insert(0, ".")

from PIL import Image

from backend.app.config import load_settings
from backend.app.pipelines import threeview as tv
from backend.app.providers.diffusers_local import DiffusersProvider

OUT = pathlib.Path(".verify/view-batch")
FRONTS = [
    pathlib.Path(".verify/view-consistency/front-gray-0.png"),
    pathlib.Path(".verify/view-consistency/front-gray-1.png"),
]
CHAR = ("灰白英短猫武士，红色短披风，左腰侧挎武士刀，皮质护腕，圆耳竖立，"
        "琥珀色眼睛，三头身比例，正面站立")
SEED = 7


def cape_ratio(img: Image.Image) -> float:
    """红色（披风）像素占比：披风丢没丢的客观代理指标。

    像素画的红披风是暗红 #B32431 一类，判据取「R 明显高于 G/B 且不太暗」，
    背视图里除了披风没有别的红色部件（刀柄是深棕），所以这个比例可以当作
    「背覆部件还在不在」的量化信号。
    """
    a = img.convert("RGB")
    w, h = a.size
    px = a.load()
    n = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y]
            if r > 100 and r - g > 45 and r - b > 45:
                n += 1
    return n / ((w // 2) * (h // 2))


def old_instruction(view: str) -> str:
    """复刻「没有背覆体积条款」的旧指令：把该段整段切掉。

    直接按文案首尾标记切，而不是维护两份指令——两份文案一定会漂移，
    实验就不再是 A/B 了。
    """
    ins = tv._view_instruction(view, CHAR)
    head = "参考图里如果有从肩膀"
    tail = "变短、变窄或挪开。"
    i = ins.find(head)
    if i < 0:
        return ins
    j = ins.find(tail, i)
    return ins[:i] + (ins[j + len(tail):] if j > 0 else "")


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    cfg = (load_settings().get("providers") or {}).get("diffusers") or {}
    provider = DiffusersProvider(cfg)
    width, height = 768, 768

    # --- 1) 批量：2 张正视图 → 2 张背视图（模拟前端一次提交多源） ---
    print("== 批量推导：2 张正视图 → 2 张背视图 ==", flush=True)
    fronts = [Image.open(p).convert("RGBA") for p in FRONTS]
    results = []
    for i, base in enumerate(fronts):
        img, meta = await tv.derive_view(
            "back", base, background="gray", character=CHAR,
            width=width, height=height, seed=SEED,
            provider_cfg=cfg, batch_index=i, batch_total=len(fronts),
        )
        p = OUT / f"new-back-{i}.png"
        img.save(p)
        results.append((img, cape_ratio(img), meta))
        print(f"[batch {i}] {p.name} 红像素 {results[-1][1]:.2%} "
              f"指令 {len(meta['instruction'])} 字 [{meta['seconds']}s]", flush=True)

    # --- 2) A/B：同图同种子，旧指令（切掉背覆条款） ---
    print("== 对照：旧指令（无背覆体积条款） ==", flush=True)
    old_instr = tv._apply_background(old_instruction("back"), "gray")
    res = await provider.generate(
        prompt=old_instr, width=width, height=height, steps=40, seed=SEED,
        n=1, refs=[fronts[0]], negative=tv.VIEW_NEGATIVE,
        ref_bg=tv._ref_fill("gray"),
        max_seq_len=tv._fit_seq_len(old_instr, None),
    )
    old_img = res.images[0].convert("RGBA")
    old_path = OUT / "old-back-0.png"
    old_img.save(old_path)
    print(f"[old] {old_path.name} 红像素 {cape_ratio(old_img):.2%} "
          f"指令 {len(old_instr)} 字", flush=True)

    # --- 3) 拼板：按组取图（第 1 组 = front0 + new-back-0；侧/右复用到正视图占位） ---
    board, bmeta = tv.compose_view_board(
        [fronts[0], results[0][0], results[0][0], fronts[0]], bg=None)
    board.save(OUT / "board-group1.png")
    print("board", board.size, [v["matte"] for v in bmeta["views"]], flush=True)

    print("\n== 结论 ==")
    print(f"新指令（含背覆条款）红像素 {results[0][1]:.2%}；"
          f"旧指令 {cape_ratio(old_img):.2%}")
    print(f"批量张数 {len(results)}（应等于正视图数量 {len(fronts)}）")


if __name__ == "__main__":
    asyncio.run(main())
