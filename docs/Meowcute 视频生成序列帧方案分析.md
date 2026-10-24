# Meowcute 视频生成序列帧方案评审

> **文档状态**：技术方案评审版  
> **评审日期**：2026-10-24  
> **项目路径**：`D:\code\github\meowcute-main`

## 结论

当前视频生成链路适合做动作预览，不适合承担正式游戏精灵的唯一生产职责。正式生产建议采用：

```text
AI 生成或整理角色部件
        ↓
标准化 2D 骨架
        ↓
复用 MotionClip 动作
        ↓
确定性渲染精灵帧
```

视频生成保留为可选的 **AI Motion 预览模式**，用于探索动作和提取关键姿势。这样可以把耗时、闪烁和角色漂移从正式交付链路中移出。

## 目录

- [1. 评审范围和判断标准](#1-评审范围和判断标准)
- [2. 当前实现和主要问题](#2-当前实现和主要问题)
- [3. 替代方案比较](#3-替代方案比较)
- [4. 推荐架构](#4-推荐架构)
- [5. 免费动作素材和工具来源](#5-免费动作素材和工具来源)
- [6. 授权和素材入库规范](#6-授权和素材入库规范)
- [7. 近期实施计划](#7-近期实施计划)
- [8. 验收指标](#8-验收指标)
- [9. 最终决策](#9-最终决策)

## 1. 评审范围和判断标准

本评审只关注“角色参考图 → 行走/动作精灵帧”的本地生产链路，不评价普通视频创作质量。判断标准按游戏素材的实际要求排列：

1. **帧间一致性**：脸、服装、配件、轮廓和颜色不能随机变化。
2. **耗时可预测**：同参数重复运行应接近，不能因为隐藏重试产生数倍波动。
3. **可复用**：动作应能套到多个角色，而不是每个角色重新生成一段视频。
4. **可编辑**：能够调整关节点、动作幅度、帧数和方向。
5. **像素质量**：整数像素对齐，避免抗锯齿、半透明边缘和插值模糊。
6. **授权清晰**：素材来源、作者、许可证和修改记录可追溯。

## 2. 当前实现和主要问题

### 2.1 当前链路

相关代码：

- `backend/app/pipelines/walkvideo.py`：方向计划、视频生成、动作评分和自动重试。
- `backend/app/providers/hy15.py`：HunyuanVideo-1.5 的帧数对齐、步数和 offload。
- `backend/app/providers/diffusers_local.py`：视频管线加载、显存策略和推理。
- `backend/app/pipelines/sequence.py`：周期检测、抽帧、去重、糊帧剔除和逐帧抠图。
- `backend/app/pipelines/matte.py`：u2net、GrabCut、颜色键和 alpha 清理。

当前流程：

```text
参考图
  ↓
方向立绘推导
  ↓
HunyuanVideo / Wan 生成视频
  ↓
GIF 解码
  ↓
动作周期检测
  ↓
抽帧、去重、剔除糊帧
  ↓
逐帧抠图
  ↓
保存透明 PNG
  ↓
精灵表
```

八方向模式通常是 5 个真实方向加 3 个镜像方向。真实方向需要分别生成立绘和视频，方向之间基本串行执行。

### 2.2 耗时波动来源

#### 自动重试

`generate_walk()` 会根据 `motionThreshold` 判断动作幅度。动作不足时，系统会重新执行完整的视频推理：

- HunyuanVideo 增加步数；
- Wan 增加 CFG 和步数；
- 每次重试都会重新生成整段视频。

当前默认 `retries=2`，因此一次任务最多可能运行三次视频生成。动作质量越不稳定，耗时长尾越明显。

#### 时序长度非线性

项目已有实测记录：

| HunyuanVideo 帧数 | 项目记录的单步耗时 |
|---:|---:|
| 21 | 约 2.4 秒 |
| 33 | 约 5 秒 |
| 61 | 约 47 秒，基本不可用 |

因此帧数不是线性成本。对 HunyuanVideo 来说，默认范围应收敛到 17～21 帧；超过 33 帧不应作为常规生产参数。

#### 模型加载、offload 和串行方向

视频底座需要 CPU offload。方向立绘和视频又分为两个阶段，以避免显存互相驱逐，但会带来模型加载、权重搬运和显存回收的长尾。真实方向逐个执行，单方向波动会直接累加到整批任务。

#### 逐帧抠图

默认约为：

```text
8 个方向 × 每方向 8 帧 = 64 次抠图
```

每帧可能经过：

```text
u2net → GrabCut → 颜色键
```

CPU 负载、图片尺寸和 fallback 路径都会影响耗时。该阶段还可能损伤脚、披风、武器等细小部件。

#### 尾部 I/O

视频推理完成后仍需 GIF 解码、PNG 保存、拼条图生成和 GIF 再保存。当前总耗时没有拆出这些阶段，用户无法判断慢在模型、抠图还是磁盘 I/O。

## 3. 替代方案比较

| 方案 | 耗时稳定性 | 帧间一致性 | 动作复用 | 实现成本 | 结论 |
|---|---|---|---|---|---|
| AI 拆部件 + 2D 骨骼 | 高 | 高 | 高 | 中 | **正式生产首选** |
| 短视频 13～21 帧 + 插帧 | 中 | 中 | 低到中 | 中 | 预览或非严格像素素材 |
| 关键帧图片 + 确定性插值 | 中高 | 中高 | 中 | 中高 | 复杂动作的折中方案 |
| 更换视频模型 | 不确定 | 不确定 | 低 | 高 | 不解决结构性问题 |
| 逐帧图像重绘 | 低 | 低 | 低 | 中 | 不建议 |

## 4. 推荐架构

### 4.1 Stable Sprite 正式生产模式

```text
角色参考图
  ↓
AI 生成爆炸拆解图或透明部件
  ↓
人工确认部件和关节点
  ↓
保存 CharacterRig
  ↓
套用 MotionClip
  ↓
按方向 Profile 重定向
  ↓
确定性渲染 8～16 帧
  ↓
PNG、GIF、精灵表、Godot 资源
```

项目已经有相关实验基础：

- `tools/rigwalk_spike.py`
- `tools/rigwalk2.py`
- `tools/rigwalk3.py`
- `tools/rigwalk_ghibli.py`
- `tools/exp_exploded_parts.py`
- `tools/godotrig/build_rig.py`

### 4.2 格式策略：行业交换格式加内部归一化层

这里需要纠正一个容易产生误解的命名：`MotionClip` 应该是项目内部的**归一化数据模型**，不应该被当作新的行业标准。对外交换、导入免费动作和兼容第三方工具时，应优先保留行业格式；项目内部再将它们转换成统一的骨架轨道。

2D 骨骼动画目前没有一个覆盖 Spine、DragonBones、Godot、Rive 等工具的通用交换标准。各格式的定位不同：

| 格式 | 定位 | 是否推荐作为 Meowcute 的主交换格式 |
|---|---|---|
| Spine JSON | 2D 骨骼事实上的主流交换格式之一，包含 bones、slots、skins、attachments 和 animations | **推荐作为首个外部导入适配器**；需要锁定 Spine 数据版本，并注意 Runtime 和素材授权 |
| DragonBones JSON | 免费 2D 骨骼编辑器和运行时生态，支持 JSON、纹理图集和动作数据 | **推荐作为免费优先的首个生产候选**；需锁定数据版本，并选择 Godot 插件或自有导入器 |
| Godot AnimationLibrary / AnimationPlayer | Godot 运行时和编辑器格式 | 推荐作为 Godot 导出目标，不推荐作为跨工具交换格式 |
| Rive `.riv` | Rive Runtime 的二进制格式，面向 Rive 编辑器和运行时 | 不作为本项目的中间格式；版本与运行时绑定，且更偏向 Rive 的交互动画体系 |
| glTF 2.0 | 开放的节点变换、网格和骨骼动画格式 | 不作为本项目 2D 主格式；缺少 2D slot、skin、draw order、事件等核心语义，除非额外定义扩展 |

推荐的转换链路：

```text
Spine JSON / DragonBones JSON / 其他来源
                ↓
       Import Adapter
                ↓
   Meowcute Normalized Rig v1
                ↓
   Godot AnimationLibrary / PNG 精灵帧
```

这样做的好处是：第三方动作仍保留原始文件，项目内部可以统一重命名骨骼、处理像素坐标和方向镜像，未来更换运行时也不必重做所有动作。

### 4.3 DragonBones 能否直接复用

可以，但“直接复用”有三个层次：

| 复用层次 | 条件 | Meowcute 的处理 |
|---|---|---|
| 直接播放 | 动作和角色使用同一套 DragonBones 骨架、数据版本和纹理图集 | 直接用 DragonBones Runtime 或 Godot-DragonBones 插件播放 |
| 换皮肤复用 | 骨骼名称和插槽一致，只替换部件图片 | 复用原动作，替换 texture atlas 或 slot attachment |
| 跨骨架重定向 | 两个角色的骨骼名称、层级和比例不同 | 做 bone mapping、比例换算、旋转方向修正和遮挡层级转换 |

DragonBones 的软件和运行时许可证，与动作包里 PNG、纹理图集和角色设计的版权是两件事。官方 DragonBones GitHub 组织列出了 JS、C#、C++ 等运行时，以及 Godot 4 插件；多个运行时仓库标注 MIT。官方站点也说明 DragonBones Pro 可免费下载，并能导出 JSON 动画数据。使用前仍应锁定具体仓库、版本和 LICENSE 文件：

- [DragonBones GitHub 组织](https://github.com/DragonBones)
- [DragonBones 官方动画和导出说明](https://dragonbones.github.io/en/animation.html)
- [DragonBones 官方下载说明](https://dragonbones.github.io/en/download.html)
- [DragonBones Godot 4 插件](https://github.com/DragonBones/Godot-DragonBones)

因此，项目可以把 DragonBones 作为第一阶段的免费外部格式，先实现：

```text
DragonBones JSON + texture atlas
              ↓
Godot-DragonBones 播放验证
              ↓
项目自己的像素渲染器 / PNG 精灵表导出
```

只有在需要跨工具统一骨骼命名、做动作重定向或摆脱运行时依赖时，才把它转换为内部 `Normalized Rig v1`。不建议一开始就重新发明一套完整的骨骼文件格式。

### 4.4 角色数据 CharacterRig

保存角色外观、部件和锚点：

```json
{
  "skeleton": "humanoid_2d_v1",
  "parts": {},
  "anchors": {},
  "bones": {},
  "directionProfiles": {}
}
```

#### 动作数据 MotionClip 内部模型

动作不保存角色图片，只保存骨骼轨道。它是导入 Spine/DragonBones 后的内部中间结构，不是对外声称的通用文件标准：

```json
{
  "name": "walk",
  "skeleton": "humanoid_2d_v1",
  "length": 0.88,
  "loop": true,
  "tracks": {
    "Pelvis": {},
    "Spine": {},
    "ArmL": {},
    "ArmR": {},
    "LegL": {},
    "LegR": {}
  },
  "events": [
    {"time": 0.22, "name": "footstep"},
    {"time": 0.66, "name": "footstep"}
  ]
}
```

#### 方向数据 DirectionProfile

方向不应该复制整套动作，而应该描述镜像、遮挡、缩放和层级：

```json
{
  "name": "left",
  "mirrorOf": null,
  "layerOrder": ["backArm", "backLeg", "torso", "frontLeg", "frontArm"],
  "rotationScale": 1.0,
  "hiddenParts": []
}
```

推荐先支持 `front`、`side`、`back`、`diagonal` 四类方向，再通过镜像补齐八方向。

### 4.5 动作重定向

动作轨道不能直接使用角色绝对像素值，应根据角色比例重定向：

```text
抬腿高度 = 角色身高 × 0.08
身体起伏 = 角色身高 × 0.015
手臂摆动 = 角度值
```

重定向至少应考虑：

- 角色身高；
- 躯干长度；
- 大腿和小腿长度；
- 肩宽和髋宽；
- 角色是否为正面、侧面或背面。

## 5. 免费动作素材和工具来源

这里必须区分“动作数据”和“图片素材”。

- **动作数据**：包含骨骼、关键帧、插槽或动画曲线，可以重定向到别的角色。
- **图片素材**：只有 PNG、GIF 或精灵表，只能复用原角色外观或作为动作参考，不能直接套到新角色骨骼。

### 5.1 来源对比

| 来源 | 适合获取 | 是否通常包含可复用动作 | 授权判断 |
|---|---|---|---|
| [Kenney Assets](https://kenney.nl/assets) | CC0 角色部件、像素素材、精灵图和测试素材 | 通常不提供通用 2D 骨骼动作 | Kenney 官方说明资产页上的游戏素材为 CC0，可用于商业项目且不强制署名；仍应保存下载时的许可证文件 |
| [OpenGameArt](https://opengameart.org/) | 精灵表、角色图、动画帧和部分引擎资源 | 取决于单个资源，很多只有 PNG/GIF | 每个资源单独核验 CC0、CC-BY、CC-BY-SA、GPL 等许可证 |
| [Godot Asset Library](https://godotengine.org/asset-library/) | Godot 插件、Skeleton2D 示例、AnimationPlayer 工具和示例工程 | 可复用 Godot 场景或动画资源，但不是专门的动作素材库 | 以资源页面许可证为准，优先选择 Godot 4 资源 |
| [Spine JSON 文档](https://us.esotericsoftware.com/spine-json-format) | skeleton、skin、slot、attachment 和 animation 数据格式 | 格式本身支持动作复用，但该页面不是免费动作素材库 | 第三方 Spine 动作包需要单独核验商用授权；“免费”不等于可商用 |
| itch.io 免费资源 | 像素角色、动画帧、部件包和少量 Spine/DragonBones 工程 | 多数是图片帧，少数包含可编辑工程 | 逐个查看项目页的 License、商用限制和署名要求 |
| GitHub 开源项目 | 动画编辑器、Godot 导入器、转换器和示例数据 | 取决于仓库 | 代码许可证和美术素材许可证必须分开检查 |

### 5.2 对本项目最有价值的使用顺序

```text
1. DragonBones JSON 工程和动作数据，优先验证 Godot-DragonBones 插件
2. Spine JSON 工程和动作数据，作为兼容性扩展
3. Godot AnimationPlayer / Skeleton2D 示例和动作数据
4. 只有精灵表的素材，拆成部件后重新绑定
5. 3D 动作库，只作为节奏和关键姿势参考
```

Kenney 更适合提供 CC0 部件和测试素材，不应被当作完整的人形动作库。OpenGameArt 资源更丰富，但许可证必须逐个核验。Godot Asset Library 更适合寻找 Godot 4 的 Skeleton2D、AnimationPlayer 和导入工具。Godot 官方文档说明 Skeleton2D/Bone2D 可以组成 2D 骨架，AnimationTree 可用于动作混合：[Godot 2D Skeleton](https://docs.godotengine.org/en/stable/tutorials/animation/2d_skeletons.html)、[Godot AnimationTree](https://docs.godotengine.org/en/4.0/tutorials/animation/animation_tree.html)。

### 5.3 不能直接复用精灵表的原因

精灵表通常绑定了原角色的身高、关节位置、画布尺寸、遮挡关系、帧数和帧率。它可以作为动作参考，但不能像 `walk.json` 一样直接套给另一个角色。跨角色复用必须拿到骨骼动作数据，或者先拆部件并重新绑定。

## 6. 授权和素材入库规范

项目要商用时，不要只记录下载页面。每个动作或部件都保存一份 `asset_manifest.json`：

```json
{
  "name": "example-walk",
  "source": "https://example.com/asset",
  "author": "author-name",
  "license": "CC-BY-4.0",
  "commercialUse": true,
  "attributionRequired": true,
  "modified": true,
  "downloadedAt": "2026-10-24",
  "notes": "动作数据已重定向到 humanoid_2d_v1"
}
```

至少保留：

- 作者和原始链接；
- 许可证名称和原文或许可证文件；
- 下载日期；
- 是否修改、重定向或混合；
- 是否需要署名；
- 是否允许商用和再分发。

### 许可证快速判断

| 许可证 | 商用判断 | 处理建议 |
|---|---|---|
| CC0 / Public Domain | 通常可商用且不强制署名 | 保留来源记录，方便审计 |
| CC-BY | 通常可商用 | 在项目 credits 或许可证文件中署名 |
| CC-BY-SA | 可能允许商用，但衍生作品有相同许可要求 | 在引入前确认是否与项目发布方式兼容 |
| GPL / LGPL | 需要结合代码和素材的具体使用方式判断 | 不要直接混入商业资产包，先做许可审查 |
| 仅写 Free、Royalty-Free 或 Personal Use | 不能直接判定可商用 | 取得明确授权或换资源 |

## 7. 近期实施计划

### P0 建立可测基线

为每个方向拆出以下阶段耗时：

```text
load_model
derive_view
generate_video
decode_video
sample_frames
matte
save_assets
```

同一角色连续跑 5 次，记录平均值、P50、P95、最大值、重试次数和显存峰值。

### P0 收敛当前视频参数

- HunyuanVideo 默认限制为 17 或 21 帧；
- 默认 `retries=0`，动作不足只标记，不自动完整重跑；
- 超过 33 帧时给出明确警告；
- 按参考图和参数 hash 缓存立绘、视频帧和序列帧。

### P1 建立项目自己的 MotionClip 格式

先实现：

```text
idle
walk
run
attack
hurt
death
```

每个动作只依赖 `humanoid_2d_v1` 骨架，不绑定角色图片。角色第一次完成关节点校准后保存 `rig.json`，后续动作修改不再重新生成角色图。

### P1 接入 Stable Sprite 节点

复用 `tools/rigwalk_ghibli.py` 和 `tools/godotrig/build_rig.py`，把实验脚本提升为正式 pipeline：

```text
CharacterRig + MotionClip + DirectionProfile
        ↓
render_rig_animation()
        ↓
PNG / GIF / 精灵表 / Godot 资源
```

### P2 优化抠图和视频预览

- 首帧或关键帧使用 u2net；
- 后续帧使用颜色键和掩码传播；
- 已有 alpha 的输入跳过重复抠图；
- 视频只作为动作预览，选定动作后再进入骨骼渲染。

## 8. 验收指标

| 指标 | 目标 |
|---|---|
| 同参数重复运行 | P95 / P50 ≤ 1.5 |
| 自动重试 | 默认 0 次；手动重试最多 1 次 |
| 跨帧一致性 | 固定部件的颜色、轮廓和配件不随机变化 |
| 循环闭合 | 骨骼相位闭合，不重复保存首帧造成跳变 |
| 像素质量 | 整数像素对齐，最近邻缩放，无抗锯齿边缘 |
| 失败恢复 | 单方向失败不丢弃其他方向，可从缓存继续 |
| 可编辑性 | 可重新绑定关节点和动作曲线 |
| 授权可追溯 | 每个外部资产都有来源和许可证记录 |

## 9. 最终决策

1. **正式生产**：采用 AI 拆部件、标准骨架、DragonBones/Godot 动作数据和确定性渲染。
2. **动作探索**：保留 HunyuanVideo 17～21 帧短视频预览。
3. **动作格式**：免费优先先接入 DragonBones JSON；再接入 Spine JSON；项目内部只保留一个版本化的归一化中间层，不把自定义 `MotionClip` 宣称为通用标准。
4. **免费资源**：优先使用 CC0 部件和可明确授权的动作数据；OpenGameArt、itch.io 等资源必须逐项核验许可证。
5. **项目建设顺序**：先定义行业格式导入、归一化、Godot 导出三段转换，再把现有 `rigwalk` 实验提升为正式节点。

这套架构能直接复用动作素材，也能让同一个动作套用到多个角色，同时把视频扩散模型从正式交付链路中移到更适合它的位置：动作参考和创意预览。

## 参考链接

- [Kenney Assets](https://kenney.nl/assets)
- [Kenney 许可证说明](https://kenney.nl/support)
- [OpenGameArt](https://opengameart.org/)
- [Godot Asset Library](https://godotengine.org/asset-library/)
- [Godot 2D Skeleton](https://docs.godotengine.org/en/stable/tutorials/animation/2d_skeletons.html)
- [Godot AnimationTree](https://docs.godotengine.org/en/4.0/tutorials/animation/animation_tree.html)
- [Spine JSON 格式](https://us.esotericsoftware.com/spine-json-format)
- [LTX-Video](https://github.com/Lightricks/ltx-video)
- [AnimateDiff](https://github.com/guoyww/AnimateDiff)
- [HunyuanVideo-1.5](https://github.com/Tencent-Hunyuan/HunyuanVideo-1.5)
