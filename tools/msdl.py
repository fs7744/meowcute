# -*- coding: utf-8 -*-
"""ModelScope 分段下载（hf-mirror 挂掉时的替代通道）。

2026-10-27：hf-mirror 在拉完 ~30GB 后连不上了（connect timeout），
modelscope.cn 单流 11MB/s 正常。URL 形态和 HF 不一样：
    https://modelscope.cn/api/v1/models/{repo}/repo?Revision=master&FilePath={path}
（302 到 CDN，必须跟随跳转；Range 由 CDN 支持）

用法：python tools/msdl.py <repo> <out_dir> <path1> [path2 ...]
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import requests

SUB = 32 * 2**20
API = "https://modelscope.cn/api/v1/models/{repo}/repo"


def file_size(repo: str, path: str) -> int:
    url = API.format(repo=repo)
    with requests.head(url, params={"Revision": "master", "FilePath": path},
                       allow_redirects=True, timeout=60,
                       proxies={"http": None, "https": None}) as r:
        r.raise_for_status()
        return int(r.headers["Content-Length"])


def fetch(repo: str, path: str, file_path: str, start: int, end: int,
          state, lock) -> None:
    """把 [start, end] 写进 file_path。

    **每个线程各自 open 一个句柄**（2026-10-27 血的教训）：多线程共享同一个
    file object 时 `fh.seek(pos)` + `fh.write()` 不是原子的——两个线程会抢同一个
    文件指针，写出来的文件开头一片 0、长度还比预期大几十 MB。第一版就是这么把
    3.3GB 的 ControlNet 权重写成一堆 0 的（大小校验居然还能"通过"到最后一刻才炸）。

    这里没有用 os.pwrite：Windows 上的 CPython 根本不导出这个函数
    （AttributeError: module 'os' has no attribute 'pwrite'），所以改成
    「每个线程独立打开 + buffering=0 原地 seek」，各自有独立的文件偏移。
    """
    url = API.format(repo=repo)
    params = {"Revision": "master", "FilePath": path}
    sess = requests.Session()
    fh = open(file_path, "r+b", buffering=0)
    try:
        pos = start
        while pos <= end:
            sub_end = min(pos + SUB - 1, end)
            for attempt in range(10):
                try:
                    with sess.get(url, params=params, stream=True,
                                  headers={"Range": f"bytes={pos}-{sub_end}"},
                                  timeout=(20, 180),
                                  proxies={"http": None, "https": None}) as r:
                        r.raise_for_status()
                        fh.seek(pos)
                        got = 0
                        for chunk in r.iter_content(2**20):
                            fh.write(chunk)
                            got += len(chunk)
                    if got == sub_end - pos + 1:
                        pos = sub_end + 1
                        with lock:
                            state["done"] += got
                        break
                    raise IOError(f"短读 {got} != {sub_end - pos + 1}")
                except Exception:
                    if attempt == 9:
                        raise
                    time.sleep(2 * (attempt + 1))
    finally:
        fh.close()


def download(repo: str, path: str, out: Path, threads: int = 8) -> bool:
    size = file_size(repo, path)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.is_file() and out.stat().st_size == size:
        print(f"  跳过 {path}（已完整 {size / 2**20:.0f}MB）", flush=True)
        return True
    if out.is_file():
        print(f"  重下 {path}（现有 {out.stat().st_size / 2**20:.0f}MB "
              f"≠ 期望 {size / 2**20:.0f}MB）", flush=True)
    state = {"done": 0}
    lock = threading.Lock()
    t0 = time.time()
    with open(out, "wb") as fh:
        fh.truncate(size)          # 预分配：先占好长度，各线程只往自己的段里写
    step = (size + threads - 1) // threads
    bounds = [(i * step, min((i + 1) * step - 1, size - 1)) for i in range(threads)]
    bounds = [b for b in bounds if b[0] <= b[1]]
    ts = [threading.Thread(target=fetch, args=(repo, path, str(out), s, e, state, lock))
          for s, e in bounds]
    for t in ts:
        t.start()
    while any(t.is_alive() for t in ts):
        time.sleep(10)
        with lock:
            d = state["done"]
        print(f"  {path} {d / 2**30:.2f}/{size / 2**30:.2f}GB "
              f"{d / max(time.time() - t0, 1e-9) / 2**20:.1f}MB/s", flush=True)
    for t in ts:
        t.join()
    ok = out.stat().st_size == size
    print(f"  {'OK' if ok else '坏'} {path} {out.stat().st_size / 2**30:.2f}GB "
          f"{time.time() - t0:.0f}s", flush=True)
    return ok


def main() -> None:
    repo, out_dir = sys.argv[1], Path(sys.argv[2])
    paths = sys.argv[3:]
    bad = [p for p in paths if not download(repo, p, out_dir / p, 8)]
    if bad:
        print("失败:", bad, flush=True)
        sys.exit(1)
    print("ALL_DONE", flush=True)


if __name__ == "__main__":
    main()
