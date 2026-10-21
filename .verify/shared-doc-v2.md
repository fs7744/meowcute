# Godot 商业游戏 · 精灵图自研生成方案

> 目标：用**本地开源模型 + 自研程序**生成精灵图，直接进 **Godot** 做商业游戏。
> 约束：**不训练 LoRA、不依赖 ComfyUI/A1111/Mixamo 等现成软件、美术零基础可用、产出可商用**。
> 生成日期：2026-10-21

---

## 一、目标与约束

| 项 | 要求 |
|----|------|
| 引擎 | **Godot 4.x** |
| 部署 | **本地**，不调云端 API |
| 训练 | **不自训 LoRA**（省显卡/时间） |
| 软件依赖 | 不用现成 GUI 软件，自己写程序编排 |
| 美术能力 | 不需要手绘（姿态用代码/AI 生成） |
| 商用 | 产出可进商业游戏，授权干净、可过 Steam |

---

## 二、方案总览

**用自己写的 Python 程序，直接调用开源模型权重**，把"生成精灵图"做成一条可重复的生产管线，输出 Godot 可直接吃的透明帧 / 精灵表。

一致性靠三件套替代"训练 LoRA"：
- **IP-Adapter** 锁角色身份（喂一张设定图）
- **ControlNet（OpenPose）** 锁姿态（骨架由代码/AI 生成，不用手画）
- **固定 seed + 锁定提示词前缀** 锁风格

```
[角色设定图] ──┐
              ├─→ [自研 Python 程序] ──→ [透明 PNG 帧 / 精灵表 + 元数据] ──→ [Godot]
[姿态骨架]   ──┘        │
                 SDXL + ControlNet + IP-Adapter + LayerDiffuse (diffusers)
```

---

## 三、技术栈与授权（逐个可商用）

| 环节 | 选型 | 开源库 | 授权 | Godot 对接 |
|------|------|--------|------|-----------|
| 基座 | **SDXL 1.0** 官方权重 | `diffusers` | ✅ OpenRAIL++-M | 生成 PNG |
| 像素风 | **SDXL Retro Pixel People** LoRA（55MB） | `diffusers` | ✅ OpenRAIL++-M Addendum | 生成 PNG |
| 身份锁 | **IP-Adapter**（SDXL） | `diffusers` / `ip_adapter` | 开源 | 生成 PNG |
| 姿态 | **ControlNet OpenPose（SDXL）** | `diffusers` | 开源 | 生成条件 |
| 骨架提取 | `controlnet-aux`（OpenPose） | `controlnet-aux` | 开源 | 产出骨架图 |
| 透明底 | **LayerDiffuse**（SDXL 透明变体） | `layerdiffuse` / diffusers | 开源 | 直接出 RGBA |
| 拼表 | `Pillow` + `numpy` | Pillow/numpy | 开源 | 生成精灵表 |
| 运行库 | Python + `torch` | PyTorch | 开源 | — |

> 授权核对铁律：模型页 `License:` 列为 `OpenRAIL` / `openrail` / `CC BY` / `MIT` → 可用；含 `nc` / `non-commercial` / `Flux.1 [dev]` / "未修改不得商用" → 不用。
> Civitai 的"允许商用"勾选**无法律约束力**，以底层协议为准。

---

## 四、自研程序架构（你自己的代码）

```
sprite_pipeline/                # 你的工程
 ├─ models/                    # 存放下载的开源权重（SDXL / ControlNet / IP-Adapter / LoRA）
 ├─ core/
 │   ├─ loaders.py             # 用 diffusers 加载 SDXL + ControlNet + IP-Adapter + LayerDiffuse
 │   ├─ pose.py                # controlnet-aux 提骨架；或读内置 JSON 骨架库渲染
 │   ├─ generate.py            # IP-Adapter(设定图)+ControlNet(骨架)+seed+动作词 → 透明 PNG
 │   ├─ sheet.py               # Pillow 拼精灵表 + 切片 + 写元数据 JSON
 │   └─ godot_export.py       # 生成 Godot 可吃入的 .tres / 导入配置
 ├─ assets/
 │   ├─ character_sheet.png    # 角色设定图（IP-Adapter 锚点）
 │   └─ poses/                 # 各动作骨架图 或 poses.json 骨架库
 ├─ config.yaml               # 模型路径、seed、动作列表、帧数、画布尺寸
 └─ run.py                    # 入口：批量生成整套精灵表
```

**模块职责**
- `loaders.py`：加载模型到 GPU，启用 `enable_model_cpu_offload()` 省显存。
- `pose.py`：两种姿态来源，都**不用手绘**：
  1. AI 出一张角色摆姿图 → `controlnet-aux` 提 OpenPose 骨架；
  2. 代码内置 `poses.json` 定义一套骨架关节坐标（走/跑/攻击），直接渲染成骨架图。
- `generate.py`：核心推理，逐帧调用，固定 seed。
- `sheet.py`：把透明帧拼成精灵表，输出 `sheet.png` + `sheet.json`（列数/行数/帧宽高/fps/动画名）。
- `godot_export.py`：可选，直接生成 `.tres` SpriteFrames 资源，实现 Godot 拖入即用。

---

## 五、完整生成工作流

1. **出角色设定图（不用画）**：SDXL + 像素 LoRA 纯文字出一张正面全身图 → `character_sheet.png`，作为 IP-Adapter 身份锚点。
2. **准备姿态（不用画）**：`pose.py` 用 AI 图提骨架，或读 `poses.json` 渲染骨架；每动作 4–8 帧。
3. **逐帧生成**：`generate.py` 循环 → IP-Adapter(设定图, 0.6–0.8) + ControlNet(骨架, 0.8) + **固定 seed** + 只改动作词 → LayerDiffuse 出透明 PNG。
4. **人工精修 ≥30%**：用 inpaint / 局部重绘修五官、手部、配色（同时拿版权 + 提质量）。
5. **拼表导出**：`sheet.py` 拼成 `sheet.png` + `sheet.json`；`godot_export.py` 生成 `.tres`。
6. **进 Godot**：见第六节。

> 一致性技巧：IP-Adapter 权重 0.6–0.8（别拉满）；固定 seed 只换动作词；生成 150% 量，人工筛掉漂移帧。

---

## 六、Godot 集成（关键步骤）

### 6.1 像素风工程设置
- **项目设置 → 渲染 → 纹理 → 默认纹理过滤 = 最近邻（Nearest）**（像素清晰，不糊）。
- 精灵纹理导入时关闭 **Mipmap**（像素艺术不需要）。
- `CanvasItem` 材质纹理过滤设为 Nearest；如需整数缩放，开启 `Rendering → 2D → Snap → 使用 GPU 像素对齐`。

### 6.2 导入精灵表（推荐方式 A：单表 + 切片）
1. 把 `sheet.png`（含透明）拖入 `res://`（Godot 自动导入 PNG，alpha 保留）。
2. 选中该 PNG → **导入（Import）面板** → 展开 `Sprite Frames` 相关项，按 `sheet.json` 填：
   - 列数 / 行数、每帧宽高、边距/间距、水平/垂直方向。
3. 新建 `AnimatedSprite2D` 节点 → 其 `SpriteFrames` 属性加载该表 → 自动按网格切成帧，建动画轨道。

### 6.3 导入逐帧 PNG（推荐方式 B：独立帧，最干净）
- 输出每个动作独立文件夹：`walk/walk_00.png …`、`attack/attack_00.png …`。
- Godot 中建 `SpriteFrames` 资源，逐个动画添加对应 PNG 帧（`texture` 用原图，alpha 已含）。
- 优势：无需切片计算，帧边界零误差；适合动作数量多、尺寸不一的情况。

### 6.4 直接生成 `.tres`（自研进阶）
- `godot_export.py` 读取 `sheet.json`，写出 `character_sheet.tres`（类型 `SpriteFrames`），内含各动画的帧引用与帧时长。
- 拖入 Godot 即被识别为 SpriteFrames 资源，`AnimatedSprite2D` 直接引用，**零手动配置**。

### 6.5 播放
- `AnimatedSprite2D` + `SpriteFrames` 最简；需要状态机/混合用 `AnimationPlayer` 配合。
- 8 方向 RPG：按方向分动画（`walk_down`/`walk_up`/…），程序切换。

---

## 七、一致性策略（无训练）

| 手段 | 作用 | 参数 |
|------|------|------|
| IP-Adapter | 跨帧复用角色长相/配色 | scale 0.6–0.8 |
| ControlNet OpenPose | 逐帧锁动作骨架 | conditioning_scale 0.8 |
| 固定 seed | 锁整体风格 | 全帧同一 seed |
| 锁定提示词前缀 | 风格/画风不变 | 只换动作词 |
| 多生成多筛 | 补 IP-Adapter 轻微漂移 | 生成 150%，留 100% |
| 人工精修 | 修翻车帧 + 护版权 | ≥30% 修改 |

> 诚实取舍：无训练方案一致性比自训 LoRA 略松，靠"多筛 + 多修"补足；但省去数小时训练与显卡成本，授权最干净。

---

## 八、商用合规（上架前必做）

> **四件套：可溯源 · 有授权凭证 · 有人工投入 · 披露明确**

1. **可溯源**：保留每个模型名称、版本、`License:` 截图与下载来源。
2. **有授权凭证**：只用 OpenRAIL / CC BY / MIT 等明确可商用模型；自建 `models/` 清单备案。
3. **有人工投入**：纯 AI 原图在美/欧可能**不享版权**（别人抄你难维权）→ 人工精修 ≥30%（五官重画、手部修正、配色统一）；核心资产（Logo、主角立绘、主宣图）建议手绘或深度改。
4. **披露明确**：Steam 要求申报**随游戏发布、玩家可见的 AI 生成内容**（美术/声音/文本）；纯幕后效率工具（代码助手、概念图 PS 填充）不用披露。截至 2025.12 已有超 1 万款（约 8%）标注 AI 使用，属正常；**诚实披露**即可。

红线：别让 AI 角色撞知名 IP；别用指向特定在世画家的提示词；别用来路不明的"名人 LoRA"。

---

## 九、自研边界说明（避免误解）

- **"自研"= 自研应用/编排层，不是自研底层模型**。SDXL/ControlNet 等用开源预训练权重直接跑，是行业常态。
- **仍需基础运行库**（torch、diffusers、numpy）——这是你程序的依赖，不算"其他软件"。纯自己实现扩散数学不现实。
- **Mixamo 可彻底去掉**：用 `controlnet-aux` 从 AI 图提骨架，或内置 `poses.json` 骨架库，全程零外部软件。
- 你的程序 + OpenRAIL 权重 = 可**发布自己的软件产品并商用**，遵守 RAIL 使用限制即可；你的代码不改变权重本身授权。

---

## 十、代码骨架（最小可运行示例）

```python
# 依赖: torch, diffusers, controlnet_aux, Pillow, numpy
# 仅示意核心调用，非完整工程
from diffusers import StableDiffusionXLControlNetPipeline, ControlNetModel, AutoencoderKL
from diffusers.utils import load_image
from controlnet_aux import OpenposeDetector
from PIL import Image
import torch

# 1) 加载基座 + ControlNet + VAE（透明底用 LayerDiffuse 变体/管道替换此处）
controlnet = ControlNetModel.from_pretrained(
    "diffusers/controlnet-openpose-sdxl-1.0", torch_dtype=torch.float16)
vae = AutoencoderKL.from_pretrained("madebyollin/sdxl-vae-fp16-fix", torch_dtype=torch.float16)
pipe = StableDiffusionXLControlNetPipeline.from_pretrained(
    "stabilityai/SDXL-1.0-base", controlnet=controlnet, vae=vae,
    torch_dtype=torch.float16).to("cuda")
pipe.enable_model_cpu_offload()

# 2) 加载 IP-Adapter（身份锚点）
pipe.load_ip_adapter("h94/IP-Adapter", subfolder="sdxl_models",
                     weight_name="ip-adapter_sdxl.bin")

# 3) 姿态提取（替代手绘 / Mixamo）
pose_detector = OpenposeDetector.from_pretrained("lllyasviel/Annotators")
pose_image = pose_detector(load_image("ref_pose.png"))   # 或读 poses.json 渲染

# 4) 逐帧生成（固定 seed）
image = pipe(
    prompt="pixel art character, full body, 8-bit, retro game sprite",
    ip_adapter_image=load_image("character_sheet.png"),
    ip_adapter_scale=0.7,
    image=pose_image,
    controlnet_conditioning_scale=0.8,
    num_inference_steps=30, seed=42,
).images[0]
image.save("frame_00.png")
```

> 透明底：将上面管道替换为 LayerDiffuse 的 SDXL 透明管道（如 `layerdiffuse` 包），其余调用一致。
> 批量：把第 3–4 步包进循环，遍历 `poses/` 与动作词，固定 seed，输出帧后用 `sheet.py` 拼表。

---

## 十一、验证清单（交付前自检）

- [ ] 所有模型 `License:` 为 OpenRAIL / CC BY / MIT（无 nc / non-commercial / Flux dev）
- [ ] 角色设定图 + 每帧均有人工精修痕迹（≥30%）
- [ ] 输出 PNG 含 alpha（Godot 导入透明正常）
- [ ] 精灵表 `sheet.json` 列/行/帧尺寸与 Godot 导入参数一致
- [ ] Godot 纹理过滤 = Nearest、Mipmap 关
- [ ] 生成日志留存（模型名/版本/seed/动作词）用于合规
- [ ] Steam 商店页 AI 披露已填（若上 Steam）

---

## 十二、下一步可交付物

1. **完整可运行 `sprite_pipeline` 工程**（含 `loaders/pose/generate/sheet/godot_export` 与 `config.yaml`）。
2. **`poses.json` 骨架库 + 渲染脚本**（彻底去除 Mixamo）。
3. **`godot_export.py` 生成 `.tres` SpriteFrames** 的参考实现。
4. **Godot 示例工程**：含像素设置、AnimatedSprite2D + SpriteFrames 接线演示。
