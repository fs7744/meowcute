# PCS v1 · Part Contract Sheet（部件契约帧表）

> 一句话：**动画是数据，不是代码。**
> 学 LPC 的「契约」思路（不是抄它的几千张图），部件交给 AI 生成，
> 骨骼与时间轴对齐开源方案 **DragonBones 5.5 的数据模型**（MIT），
> 输出既是我们自己的标准帧表，也能直接导出给 DragonBonesJS 运行时播放。

## 1. 为什么需要这个标准

LPC（Universal-LPC-Spritesheet-Character-Generator）真正的护城河不是素材量，
而是**统一网格契约**：所有部件画在同一套坐标上，所以新增 1000 件衣服不用各写
一遍走路动画，它们天然复用同一份坐标规则。

但它有个隐含前提：**图是人按同一具身体对齐画的**。AI 生成的部件做不到——
头身比例、关节位置、裁切边界每次都不一样。所以本标准的重点不是复刻网格数字，
而是把「对齐信息」从画里拿出来，写成数据：

| 问题 | LPC 的做法 | PCS 的做法 |
|---|---|---|
| 关节在哪 | 画师对齐到同一具身体 | 部件声明**归一化锚点**；不声明就按**分类兜底** |
| 比例不同怎么办 | 不允许（只有 6 种体型） | 骨架拓扑固定，**关节位置按部件实测比例自动贴合** |
| 动作怎么复用 | 每个部件预画 4 方向 × 13 帧 | **动作是骨骼关键帧**，任何部件套上去就动 |
| 动作数据长什么样 | 帧图 + cycle 数组 | **DragonBones 时间轴**（bone/slot/skin/rotate/translate/scale） |

## 2. 三份契约

```
<partset>/
  pcs.part.json      # 部件集：有哪些部件、分类、锚点、z 序
  *.png              # 部件图（AI 出的透明 PNG，任意分辨率）
tools/pcs/
  rigs/biped.default.json   # 骨架：节点树（相对父节点的局部位移）
  anims/{idle,walk,run}.json # 动作：glTF 风格 channels/samplers 关键帧
  spec/*.schema.json         # 三份机器可读 schema
```

### 2.1 部件（`pcs.part.json`）

```json
{
  "pcs": "1.0",
  "id": "ghibli-girl",
  "frame": { "w": 192, "h": 192 },
  "rig": "biped.default",
  "bodyFit": 0.92,
  "parts": [
    { "id": "leg_l", "category": "leg_l", "image": "leg_l.png", "zPos": 5 },
    { "id": "head",  "category": "head",  "image": "head.png",  "zPos": 50 }
  ]
}
```

`category` 决定三件事（**这就是 AI 出图不需要人工标锚点的原因**）：

| 分类 | 挂到骨骼 | 默认锚点 | 默认 zPos |
|---|---|---|---|
| `head` | head | 底边中点（脖子） | 50 |
| `hair` / `face` / `ear` | head | 中心 | 51~55 |
| `torso` | spine | 底边中点（髋） | 40 |
| `arm_l` / `arm_r` | 同名骨骼 | 顶边中点（肩） | 45 |
| `leg_l` / `leg_r` | 同名骨骼 | 顶边中点（髋） | 5 |
| `cape` | spine | 顶边中点 | 10 |
| `weapon` | arm_r | 中心 | 70 |

锚点口径：`x 0..1` 左→右，`y 0..1` 上→下，相对**图片内容 bbox**（渲染前会自动裁掉透明边）。

### 2.2 骨架（`rigs/biped.default.json`）

glTF 2.0 的节点语义：**每个节点只写相对父节点的位移/旋转/缩放**，世界变换由父子链相乘。
坐标单位是 `bodyHeight`（1.0 = 头顶到脚底），与像素尺寸解耦。

```json
{
  "id": "biped.default", "unit": "bodyHeight", "up": "screen",
  "nodes": [
    { "name": "root",  "parent": null,    "translation": [0.0, 0.56] },
    { "name": "spine", "parent": "root",  "translation": [0.0, 0.0], "length": 0.26 },
    { "name": "neck",  "parent": "spine", "translation": [0.0, -0.26] },
    { "name": "head",  "parent": "neck",  "translation": [0.0, 0.0], "length": 0.24 },
    { "name": "arm_l", "parent": "spine", "translation": [-0.115, -0.02], "length": 0.26 },
    { "name": "leg_l", "parent": "root",  "translation": [-0.06, 0.0], "length": 0.44 }
  ]
}
```

> 文件里的数值是**参考比例**。渲染时会用部件的实测高度重排关节
> （`Rig.fit_measurements`），所以同一副骨架能套 8 头身和 Q 版，不用改文件。

### 2.3 动作（`anims/*.json`）

glTF 的 `channels` + `samplers` 关键帧结构，**没有任何程序化公式**：

```json
{
  "id": "walk", "fps": 12, "duration": 0.666667, "loop": true,
  "channels": [
    { "node": "leg_l", "path": "rotation",
      "sampler": { "interpolation": "LINEAR", "input": [0, 0.083, 0.167],
                   "output": [3, 2, 0] } },
    { "node": "leg_l", "path": "translation",
      "sampler": { "input": [0, 0.083], "output": [[0, 0], [0, -0.025]] } }
  ],
  "perDirection": {
    "left": { "channels": [ /* 侧视：腿在平面内摆 ±20° */ ] }
  }
}
```

三条硬约定（都是踩出来的）：

1. **`translation` 是偏移量，不是绝对值**——绝对位移会把某副骨架的髋高写死，
   换比例就错位。偏移量让同一份动作适配任何体型。
2. **`rotation` 是绝对角度**（度，屏幕坐标顺时针为正）。
3. **必须按朝向分档**（`perDirection`）：侧视靠平面内摆腿，正视只能靠抬腿 + 挤压；
   同一套角度套到正视，两条腿会交叉成 X。

**朝向回退**：部件只提供了 `down` 时，其它朝向自动退回正视动作档
（`sample_pose(..., available=facings)`），不会把正视的腿转开。

## 3. 三种输入 → 同一张帧表

| 模式 | 输入 | 命令 | 原理 |
|---|---|---|---|
| **render** | AI 静态部件 + 骨架 + 动作 | `pcs.py sheet` | 骨骼矩阵变形出每一帧 |
| **compose** | 现成逐帧素材（LPC spritesheet，行=朝向 列=帧） | `pcs.py lpc` | 按契约裁切合成 |
| **export** | 任意模式的结果 | `pcs.py dragonbones` | 导出给 DragonBonesJS 播放 |

帧表布局与 LPC 同语义：

```
行 = 动作 × 朝向   row = anim.row + 朝向序号   （朝向顺序 down / left / right / up）
列 = 帧序号        cell = (col * frame.w, row * frame.h)
```

`sheet.json` 里带 `rows[]`（每行的动作/朝向/行号）、`cols`、`frame`，
消费方不需要猜。

## 4. 与开源方案的字段映射

| PCS | DragonBones 5.5 | LPC | glTF 2.0 |
|---|---|---|---|
| `parts[]` | `slot` + `skin[].slot[].display[]` | 一层 PNG | mesh + material |
| `category` 的默认骨 | `slot.parent` | （无此概念，靠画师对齐） | node |
| `anchor.joint` | `display.pivot`（归一化） | （隐含在网格里） | 顶点偏移 |
| `zPos` | `slot` 数组顺序 / `zIndex` | `zPos` 字段 | 绘制顺序 |
| `rig.nodes[]` | `bone[]`（`transform.x/y/skX`） | （无骨架） | node TRS |
| `anim.channels[]` | `animation[].bone[].rotate/translate/scale` | 帧图 + `cycle` | animation channel |
| `fps` | `frameRate`（导出统一重采样到 12） | 固定 8~10 fps | — |
| `loop` | `playTimes: 0` | `loop=0` | — |

导出单位换算：`bone.transform.x/y = PCS 单位 × body_h`（图集像素）；
`display.transform.scX/scY = body_h / 角色原始像素高`。

## 5. 用法

> `tools/pcs/examples/ghibli/` 是一套可直接跑的示例部件（see-through 拆出的
> 6 个语义件：头/躯干/双臂/双腿），下面命令原样可跑。

```bash
# 校验部件集
python tools/pcs/pcs.py validate tools/pcs/examples/ghibli

# 渲染标准帧表（3 动作 × 4 朝向）+ 每格预览 GIF
python tools/pcs/pcs.py sheet tools/pcs/examples/ghibli \
    --anims idle,walk,run --dirs down,left,right,up --frame 192 \
    --out tools/pcs/examples/ghibli/out

# 导出 DragonBones 5.5（数据 + 图集），交给 DragonBonesJS 播放
python tools/pcs/pcs.py dragonbones tools/pcs/examples/ghibli \
    --anims idle,walk,run --dirs down --out tools/pcs/examples/ghibli/db

# 吃现成的 LPC 逐帧素材（同一张帧表格式；LPC 单动画表 = 9 列 × 4 行 @64px）
python tools/pcs/pcs.py lpc \
    --layers body_walk.png:10 --anim walk --frames 9 \
    --dirs down,left,right,up --frame 64 --out .verify/pcs/lpc
```

产物：`sheet.png` + `sheet.json` + `<动作>.<朝向>.gif`；
`dragonbones/` 下是 `dragonbones_data.json` + `texture_atlas.json` + `atlas.png`。

## 6. 已知边界（诚实清单）

1. **单朝向部件只能出正视动作**。侧视/背视需要按视角分别拆部件（正/左/背/右
   各跑一次拆件）。当前 see-through 对侧视图拆件质量不稳（单数 `handwear`、
   无 `legwear`），所以本仓库的 demo 四方向都用正视档。
2. **只有刚性变换**（旋转/平移/缩放），没有 FFD 网格变形、没有 IK。
   DragonBones 支持 `ffd`，导出结构里已留空数组，后续接。
3. **部件必须共享一套比例**：靠全局等比缩放保证；单个部件如果本身画歪了
   （比如手臂比腿还长），标准救不了，要在出图或拆件阶段解决。
4. **动画是按「相位」手写的关键帧**，不是动作捕捉。要更自然的走跑，建议
   用 Blender 摆骨后导出 glTF 再转 PCS（通道结构一致，转换是纯字段重命名）。
