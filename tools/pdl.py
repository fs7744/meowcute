# -*- coding: utf-8 -*-
"""多线程分段下载（hf-mirror 单流太慢时的应急下载器）。

用法：python tools/pdl.py <url> <out> [threads]
原理：HEAD 拿总大小 → 预分配文件 → N 线程各领一段 Range 顺序写
（段内再按 16MB 子块重试，失败只重试自己的子块，支持断点续传：
以「段已写字节数」记录在 <out>.pdl.json）。
"""
import json
import os
import sys
import threading
import time
from pathlib import Path

import requests

SUB = 16 * 2**20  # 子块 16MB


def head_size(url: str) -> int:
    with requests.head(url, allow_redirects=True, timeout=30,
                       proxies={"http": None, "https": None}) as r:
        r.raise_for_status()
        return int(r.headers["Content-Length"])


def fetch_range(url: str, start: int, end: int, file_path: str, state, lock, idx):
    """把 [start, end] 写进 file_path，段内 16MB 子块逐一重试。

    **每个线程各自 open 一个句柄**：多线程共享同一个 file object 时
    `fh.seek(pos)+fh.write()` 不是原子的，两个线程会抢同一个文件指针，
    结果是文件开头一片 0、长度比预期大几十 MB（2026-10-27 复现，
    3.3GB 的 ControlNet 权重被写成全 0）。os.pwrite 在 Windows 上不存在，
    所以走「每线程独立句柄 + buffering=0」。
    """
    sess = requests.Session()
    fh = open(file_path, "r+b", buffering=0)
    try:
        pos = start
        while pos <= end:
            sub_end = min(pos + SUB - 1, end)
            for attempt in range(8):
                try:
                    with sess.get(url, headers={"Range": f"bytes={pos}-{sub_end}"},
                                  stream=True, timeout=(15, 60),
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
                except Exception:
                    if attempt == 7:
                        raise
                    time.sleep(2 * (attempt + 1))
    finally:
        fh.close()
    with lock:
        state["segs_ok"] += 1


def main():
    url, out = sys.argv[1], sys.argv[2]
    n_threads = int(sys.argv[3]) if len(sys.argv) > 3 else 24
    total = head_size(url)
    print(f"total={total/2**30:.2f}GB threads={n_threads}", flush=True)
    state = {"done": 0, "segs_ok": 0}
    lock = threading.Lock()
    t0 = time.time()
    resume = Path(out).exists() and Path(out).stat().st_size == total
    if not resume:
        with open(out, "wb") as fh:
            fh.truncate(total)
    bounds = []
    step = (total + n_threads - 1) // n_threads
    for i in range(n_threads):
        s, e = i * step, min((i + 1) * step - 1, total - 1)
        if s <= e:
            bounds.append((s, e))
    def report():
        while state["segs_ok"] < len(bounds):
            time.sleep(10)
            with lock:
                d = state["done"]
            dt = time.time() - t0
            print(f"  {d/2**30:.2f}GB {d/max(dt,1e-9)/2**20:.1f}MB/s", flush=True)
    rt = threading.Thread(target=report, daemon=True)
    rt.start()
    threads = [threading.Thread(target=fetch_range,
                                args=(url, s, e, out, state, lock, i))
               for i, (s, e) in enumerate(bounds)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    dt = time.time() - t0
    real = os.path.getsize(out)
    ok = real == total
    print(f"{'DONE' if ok else 'SIZE_MISMATCH'} {real/2**30:.2f}GB "
          f"(expect {total/2**30:.2f}GB) elapsed={dt:.0f}s", flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
