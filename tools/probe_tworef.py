"""姿势保真度实验：四种喂法对比，看哪种能把源动作真正带过来。

问题：12 帧身份已经一致，但姿势没跟着行走周期走（源是正面/侧面/侧面/背面四行，
生成的大多停在 3/4 侧）。

四种喂法：
  A 基准    图1=角色立绘        控制图=黑底白剪影
  B 双参考  图1=立绘 图2=动作帧  控制图=黑底白剪影
  C 换控制  图1=角色立绘        控制图=**源帧原图**（不是剪影）
  D 双+原图 图1=立绘 图2=动作帧  控制图=源帧原图

B/D 的想法：Edit 会把「图2」也当成条件，与其让控制网孤军奋战，不如让模型直接
看动作帧。风险是它可能连源帧的**光头小胖体型**一起抄。

用法：.venv/Scripts/python.exe tools/probe_tworef.py [帧号,帧号,...]
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "tools"))

from app.pipelines import poseclone as pc                      # noqa: E402
from app.pipelines.qwen20_edit import (DEFAULT_NEGATIVE, Qwen20EditControl)  # noqa: E402
from tools_path import board_cells, sheet_cells                 # noqa: E402

OUT = ROOT / "out"
ROW_CELL = [0, 0, 0, 1, 1, 1, 1, 1, 1, 2, 2, 2]

P_ONE = ("把图1 里的角色画成控制图里那个姿势：同一个角色（发型、发色、服装配色、配饰、武器"
         "完全照图1），但身体朝向、重心、四肢方位、手脚落点全部照控制图。"
         "全身角色立绘，纯白背景，不要文字、不要水印、不要多个角色。")
P_TWO = ("图1 是这个角色的立绘，图2 是这个角色的动作参考。"
         "请把图1 的角色画成图2 里的姿势：身份、发型、发色、服装配色、配饰、武器完全照图1，"
         "但身体朝向、重心、四肢方位、手脚落点全部照图2。"
         "全身角色立绘，纯白背景，不要文字、不要水印、不要多个角色。")


def main() -> None:
    want = [int(x) for x in sys.argv[1].split(",")] if len(sys.argv) > 1 else [4, 10]
    cells = [pc._ensure_fg(c)[0] for c in sheet_cells()]
    refs = [pc._ensure_fg(c)[0] for c in board_cells()]

    eng = Qwen20EditControl(size=512, steps=30, cfg=4.0)
    eng.ensure()
    cache = eng.warm_prompts([P_ONE, P_TWO, DEFAULT_NEGATIVE])

    def embeds(p: str) -> dict:
        pos, pos_mask = cache[p]
        neg, neg_mask = cache[DEFAULT_NEGATIVE]
        return {"pos": pos, "pos_mask": pos_mask, "neg": neg, "neg_mask": neg_mask}

    print("[probe] 提示词就绪", flush=True)
    for k in want:
        ref, src = refs[ROW_CELL[k]], cells[k]
        plans = [
            ("A", dict(control=src, ref=ref, kind="black", embeds=embeds(P_ONE))),
            ("B", dict(control=src, ref=[ref, src], kind="black", embeds=embeds(P_TWO))),
            ("C", dict(control=src, ref=ref, kind="src", embeds=embeds(P_ONE))),
            ("D", dict(control=src, ref=[ref, src], kind="src", embeds=embeds(P_TWO))),
        ]
        for tag, kw in plans:
            img, meta = eng.run_frame(seed=1000 + k, **kw)
            img.save(OUT / f"tr_{tag}_{k:02d}.png")
            print(f"[probe] {tag} 帧{k} {meta}", flush=True)


if __name__ == "__main__":
    main()
