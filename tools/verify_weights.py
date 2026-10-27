"""权重完整性快检：safetensors 头能不能解析、数据区是否落在文件内。

为什么需要：下载器把文件写成"长度对、内容全 0"，只看 size 是发现不了的
（2026-10-27 踩过：3.3GB 的 ControlNet 权重开头 64 字节全是 0）。头部 8 字节是
header 长度，正常文件远小于 1MB 且能解析成 JSON；全 0 的文件这里立刻报出来。

用法：.venv/Scripts/python.exe tools/verify_weights.py [目录...]
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(p: Path) -> tuple[bool, str]:
    size = p.stat().st_size
    if size == 0:
        return False, "0 字节"
    with open(p, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        if not (8 < n < 200 * 2**20):
            return False, f"头部长度异常 {n}（很可能是全 0 写入）"
        raw = f.read(n)
    try:
        hdr = json.loads(raw)
    except Exception as e:
        return False, f"头部不是 JSON（{e}）"
    end = 8 + n
    for k, v in hdr.items():
        if k == "__metadata__":
            continue
        e = int(v["data_offsets"][1])
        if e > end + 1:
            end = e
    if end + 0 > size:
        return False, f"数据区超出文件（需要 {end} > {size}）"
    keys = len([k for k in hdr if k != "__metadata__"])
    return True, f"{keys} 个张量"


def main() -> None:
    dirs = [Path(a).resolve() for a in sys.argv[1:]] or [
        ROOT / "models/qwen-image-20b", ROOT / "models/qwen-image-controlnet-union"]
    bad = 0
    for d in dirs:
        for p in sorted(d.rglob("*.safetensors")):
            ok, msg = check(p)
            shown = p.resolve().relative_to(ROOT) if p.resolve().is_relative_to(ROOT) else p
            print(f"{'OK ' if ok else '坏 '} {shown}  {p.stat().st_size / 2**30:.2f}GB  {msg}")
            bad += 0 if ok else 1
    print(f"\n{'全部通过' if not bad else f'{bad} 个文件有问题'}")


if __name__ == "__main__":
    main()
