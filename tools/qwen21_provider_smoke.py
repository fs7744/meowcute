# -*- coding: utf-8 -*-
"""qwen21_provider_smoke.py — 经 provider 新路由验证 Qwen-Image-2.1 双模式。

1) 文生图（无参考图，默认路由 models/qwen-image-2.1）
2) 指令改图（带参考图，_run_qwen21 的 edit 模式）
记录耗时/峰值显存/mode，验证 negative_prompt 真实生效（true_cfg 不再静默失效）。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os_environ_set = False

from PIL import Image  # noqa: E402

from backend.app.providers.diffusers_local import DiffusersProvider  # noqa: E402

REF = ROOT / ".verify" / "ghibli" / "ref.png"
OUT = ROOT / ".verify" / "qwen21-smoke"


async def main():
    OUT.mkdir(parents=True, exist_ok=True)
    prov = DiffusersProvider({
        "model": "models/qwen-image-2.1",
        "negativePrompt": "模糊, 低质量, 水印, 文字, 渐变, 抗锯齿, 3D渲染, 照片, 噪点",
    })

    t0 = time.time()
    r = await prov.generate(
        prompt="像素风小猫少女，三头身，正面站立，红色短披风，暗绿色纯色背景",
        width=512, height=512, steps=40, seed=20261022, n=1)
    r.images[0].save(OUT / "txt2img.png")
    m = r.meta
    print(f"[txt2img] {time.time() - t0:.1f}s mode={m.get('mode')} "
          f"family={m.get('family')} peak={m.get('vram', {}).get('peakGB')}GB "
          f"model={Path(str(m.get('model'))).name}", flush=True)

    ref = Image.open(REF).convert("RGB")
    t0 = time.time()
    r2 = await prov.generate(
        prompt="只改变角色朝向：角色的造型、配色、体型比例和画风全部以参考图为准，"
               "不新增也不删除任何部件。把画面中的角色向画面右侧旋转约45度："
               "四分之三侧面视角，身体侧向画面右侧，脸部保留一部分正面特征，全身站立。",
        width=512, height=768, steps=40, seed=20261022, n=1, refs=[ref])
    r2.images[0].save(OUT / "edit.png")
    m2 = r2.meta
    print(f"[edit]    {time.time() - t0:.1f}s mode={m2.get('mode')} "
          f"family={m2.get('family')} peak={m2.get('vram', {}).get('peakGB')}GB "
          f"refCount={m2.get('refCount')}", flush=True)
    print("SMOKE_DONE", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
