"""Provider 抽象：所有生成后端统一接口。

当前唯一实现是 diffusers_local（本地真模型）。接口保持三件套
（prompt / refs / 尺寸），未来接第三方接口（fal.ai / OpenRouter 等）时
只加实现类，不动调用方——这是 perfectpixel-studio 验证过的解耦方式。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PIL import Image


@dataclass
class ProviderResult:
    images: List[Image.Image]
    meta: Dict[str, Any] = field(default_factory=dict)


class BaseProvider:
    id = "base"
    label = "Base"

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg or {}

    async def generate(
        self,
        prompt: str,
        width: int = 512,
        height: int = 512,
        steps: Optional[int] = None,
        seed: Optional[int] = None,
        n: int = 1,
        ref: Optional[Image.Image] = None,
        refs: Optional[List[Image.Image]] = None,
        task_id: Optional[str] = None,
    ) -> ProviderResult:
        """refs 参考图入参：

        - Z-Image 是纯文生图，喂参考图会自动改走编辑底座（autoRouted），
          而不是把参考图悄悄丢掉；
        - Qwen-Image-Edit 走指令式编辑，refs 支持多张（最多 4 张）语义组合。
        """
        raise NotImplementedError

    async def test(self) -> Dict[str, Any]:
        return {"ok": True, "message": "未实现连接测试"}

    @staticmethod
    def unload() -> None:
        pass
