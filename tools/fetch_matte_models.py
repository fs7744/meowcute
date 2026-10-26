"""下载更强的抠图底座（BiRefNet / ISNet）到 U2NET_HOME。

为什么单独一个脚本：`backend/app/pipelines/matte.py` 里的规矩是**权重缺失时绝不
联网**（rembg 的自动下载会卡住请求几十分钟，实测过）。所以下载必须是显式动作。

为什么不用 rembg 自己的下载：它走 GitHub release 直连，实测被 CDN 限到 70~160 kB/s
（224MB 要约半小时），而且间歇 502。这里改成**多分片并行 + 可镜像**：
  python tools/fetch_matte_models.py                # 默认 birefnet-general-lite
  python tools/fetch_matte_models.py isnet-anime    # 换一个
  GHPROXY=https://gh-proxy.com/ python tools/fetch_matte_models.py

下完的文件放 `~/.u2net/<name>.onnx`，rembg 直接认。之后 `engine_status()` 会自动把
preferred 切成它（想强制回 u2net：`MEOWCUTE_MATTE_MODEL=u2net`）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BASE = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
# 名字 → (文件名, 大小)。大小写死是为了校验分片合并后的完整性（少一个字节就是不完整）
FILES = {
    "birefnet-general-lite": ("BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx", 224005088),
    "birefnet-general": ("BiRefNet-general-epoch_244.onnx", 0),
    "isnet-anime": ("isnet-anime.onnx", 176133574),
}
HOME = Path(os.environ.get("U2NET_HOME") or (Path.home() / ".u2net"))
PARTS = 8


def _url(name: str) -> str:
    proxy = (os.environ.get("GHPROXY") or "").strip()
    url = BASE + FILES[name][0]
    return (proxy + url) if proxy else url


def _size(url: str) -> int:
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        cr = r.headers.get("Content-Range") or ""
    if "/" not in cr:
        raise RuntimeError("拿不到文件大小（镜像不支持 Range？换 GHPROXY 再试）")
    return int(cr.rsplit("/", 1)[1])


def _fetch(url: str, start: int, end: int, dst: Path) -> None:
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dst, "wb") as f:
        shutil.copyfileobj(r, f, 1 << 20)


def download(name: str) -> Path:
    if name not in FILES:
        raise SystemExit(f"未知模型：{name}；可选 {', '.join(FILES)}")
    HOME.mkdir(parents=True, exist_ok=True)
    out = HOME / f"{name}.onnx"
    if out.exists() and out.stat().st_size > (FILES[name][1] or 1):
        print(f"[skip] 已存在：{out}（{out.stat().st_size/2**20:.0f}MB）")
        return out
    url = _url(name)
    print(f"[1/3] 探测大小：{url}")
    total = _size(url)
    expect = FILES[name][1] or total
    if total != expect:
        print(f"      注意：远端 {total} 字节，预期 {expect}")
    print(f"[2/3] {total/2**20:.0f}MB，{PARTS} 分片并行下载…")
    chunk = (total + PARTS - 1) // PARTS
    tmp = Path(tempfile.mkdtemp(prefix="matte-dl-"))
    jobs = []
    with ThreadPoolExecutor(max_workers=PARTS) as ex:
        for i in range(PARTS):
            s = i * chunk
            e = min(s + chunk - 1, total - 1)
            if s > e:
                continue
            part = tmp / f"p{i}"
            jobs.append((part, ex.submit(_fetch, url, s, e, part)))
        for part, fut in jobs:
            fut.result()
    got = sum(p.stat().st_size for p, _ in jobs)
    print(f"[3/3] 合并 {got/2**20:.0f}MB → {out}")
    if got != total:
        raise SystemExit(f"分片不完整：{got} != {total}")
    with open(out, "wb") as f:
        for part, _ in jobs:
            f.write(part.read_bytes())
    shutil.rmtree(tmp, ignore_errors=True)
    print(f"完成。\n提示：默认会自动优先用它；想切回 u2net 用 MEOWCUTE_MATTE_MODEL=u2net")
    return out


if __name__ == "__main__":
    names = sys.argv[1:] or ["birefnet-general-lite"]
    for n in names:
        download(n)
