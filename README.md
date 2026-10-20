# 喵喵叫的可爱助手 · MEOWCUTE

> 一个**本地运行**的 AI 游戏素材生成台：用本地模型出图，用确定性管线把图规整成真正的像素画。
> 无限画布操作 —— 每个项目是一块画布，生成卡和结果卡摆开，随时回来继续。

项目名 `pixelforge`，界面名「喵喵叫的可爱助手」。

---

## 快速开始

```bash
# Windows
start.bat

# macOS / Linux / Git Bash
./start.sh
```

首次会自动装核心依赖并构建前端，然后打开 `http://127.0.0.1:8788`。

**没装 GPU 也能跑**：项目管理、提示词示例库、画风浏览、像素精修（纯 numpy/PIL）都可用，
只有「生成」需要 GPU 推理依赖：

```bash
start.bat --gpu      # 或 ./start.sh --gpu
```

开发模式（前端热更新）：

```bash
start.bat --dev      # 后端 8788 + Vite 5173
```

### 启动脚本的两个硬约束（Windows）

`start.bat` 必须保持 **CRLF 换行 + GBK（cp936）编码**。两条看起来古怪，都是被 cmd.exe 逼的：

- **只给 LF 换行**：cmd 按 CRLF 切分语句，会把相邻两行粘成一条命令执行；
- **存成 UTF-8**：cmd 在行内按字节偏移解析，中文会把紧随其后的命令吃掉，
  于是报 `'xxx' 不是内部或外部命令`，同时中文全是乱码。

编辑器的「另存为 UTF-8」是这个脚本的头号杀手。`.gitattributes` 已锁 `*.bat eol=crlf`，编码只能靠自觉。

### 找不到 / 选错 Python

脚本按这个顺序挑解释器：`PYTHON` 环境变量 → `.venv/` → `python` → `py -3.13` → `-3.12` → `-3.11` → `py -3`。
顺序不是随便定的，**越新越容易装不上**。两个高频坑：

- **`where python` 指向 `...\WindowsApps\python.exe`** —— 那是微软商店的占位程序，执行返回 9009，不是真
  Python。脚本用 `-c "import sys"` 实跑验证，所以能跳过它。想彻底解决：设置 → 应用 → 高级应用设置 →
  应用执行别名，关掉 `python.exe` 和 `python3.exe`。
- **系统 Python 太新** —— `torch 2.6` / `diffusers 0.38` 只发到 cp313 的 wheel，3.14+ 会退化成源码编译，
  没装 MSVC 就报 `Microsoft Visual C++ 14.0 or greater is required`。用 3.12 最省事。

要强制指定解释器：

```bat
set PYTHON=C:\Python312\python.exe
start.bat
```

---

## 三个页面

| 页面 | 干什么 |
|---|---|
| **模板** | 列举所有工作流模板，说明每个模板的输入、四步流程和产出。第一个模板已可用。 |
| **项目** | 项目列表。新建项目必须先选工作流模板；点开进无限画布。 |
| **设置** | 环境体检、模型与默认值、**优秀提示词示例维护**、画风与背景预设、快捷键。 |

### 无限画布

滚轮以光标为中心缩放 · 空白处拖拽平移 · 拖卡头移动节点 · `Delete` 删除选中 · `0` 复位 · `Esc` 关预览。
画布位置和节点全部自动保存（800ms 防抖），关掉浏览器再打开回到原位。

### 生成卡（第一个模板）

1. **文字描述** —— 可点选内置示例（角色描述 / 场景描述 / 道具图标 / 动作姿态 / 画风补充）
2. **参考图** —— 可选，最多 4 张；带参考图会自动路由到编辑模型
3. **图片格式** —— 1/2/3/4 张、4 档尺寸、seed、步数
4. **背景** —— 绿幕 / 中灰（默认）/ 无
5. **画风** —— 15 种，分 5 组，像素类会打标
6. **像素精修** —— 开关、色数、固定色板（GameBoy / NES / 自动）

两个按钮：**预览提示词**（看最终拼出来的四段结构）和**生成**（SSE 推进度，可取消）。

---

## 项目文件夹：输入和产出都在里面

```
data/projects/<项目 id>/
  README.txt        自动生成的说明，翻目录时不用猜
  project.json      画布状态：每个生成卡的描述/参考图引用/尺寸/张数/背景/画风/像素参数，
                    每个结果卡的图片引用与元数据，以及画布缩放平移位置
  refs/             输入：你上传的参考图（ref-*.png）
  images/           产出：模型原图（out-*.png）+ 像素精修成品（px-*.png）
```

文件名带时间戳和序号，例如 `out-20261021-093012-2-a1b2.png` = 那次生成的第二张原图。
**整个文件夹拷走就能换机器继续**，路径无关。

界面里画布顶栏的「文件夹」按钮能直接看到清单并在文件管理器中打开。

---

## 像素风为什么能干净

光写一句 pixel art 是出不了像素图的 —— 模型只会给你一张「看起来像像素」的模糊图。
这里拆成三道互相兜底的关卡：

1. **提示词契约**（生成侧）：把网格尺寸、每格单色、限定色板、无抗锯齿、硬轮廓写进提示词，
   先让模型往网格上画。风格词放在 prompt 最前面 —— Z-Image 的编码器是因果结构，尾部 token 权重最弱。
2. **FFT 网格对齐**（后处理）：FFT 找出画面里真实的像素块大小 → Sobel 梯度峰吸附网格线 →
   每格多数表决采样。不靠猜，块大小是量出来的。
3. **共享调色板**（后处理）：频次加权最远点采样压到 16 色内。用面积占比做权重会让背景白占满色位，
   所以按出现频次加权、并用感知距离（2/4/3）挑色。

后两步纯 numpy/PIL，不碰模型，几秒钟出结果，没有 GPU 也能测。

---

## 目录结构

```
backend/
  app/
    main.py           全部 HTTP 端点
    config.py         运行时配置、模型扫描
    projects.py       项目 CRUD + 画布状态白名单清洗
    storage.py        项目目录内资产落盘、文件夹清单
    styles.py         15 种画风、背景预设、像素契约、提示词拼装
    prompt_examples.py 内置 12 条示例（可增删改、软删除）
    templates.py      工作流模板定义
    progress.py       任务进度与取消
    doctor.py         15 项环境体检
    providers/        diffusers 本地底座（Z-Image / Qwen-Edit 家族互斥换出）
    pipelines/        perfect_pixel（网格）· palette（色板）· refine（精修）
  smoke_test.py       73 项断言，不需要 GPU
frontend/
  src/views/          Templates · Projects · Canvas · Settings
  src/components/     GenNode · ResultNode · PreviewOverlay · PromptModal · ProjectFiles
models/               本地模型（z-image-turbo / qwen-image-edit-2511 / ...）
data/projects/        项目数据
```

## 测试

```bash
cd backend
python smoke_test.py     # 73 项，不需要 GPU
python gpu_test.py       # 真实出图验证，需要 GPU
```

## 图标

标签页那只猫不是手绘的，是本项目自己的管线生成的 —— 相当于把产品拿来当自己的第一个用户：

```bash
python tools/gen_icon.py          # 模型按像素契约出 4 张候选 → 精修到 32×32 → tools/icon_candidates.png
python tools/gen_icon.py --pick 1 # 选第 1 张，写成 favicon.ico / favicon-32.png / favicon-192.png
```

流程是：Z-Image 按「32×32 网格 + 12 色 + 硬边」的契约出图 → FFT 网格对齐精修 → 泛洪抠掉中灰背景。
16px 那一档不能用最近邻硬缩（32→16 会把轮廓打断），走 2×2 块多数表决降采样。

## 技术栈与版本约束

FastAPI + React 18 + TypeScript + Vite。GPU 推理依赖版本互相咬得很紧，已验证可用的组合：

```
torch 2.6.0+cu124 · torchvision 0.21.0 · diffusers 0.38.0
transformers 4.57.6 (<5，5.x 会破 diffusers 的 import)
torchao 0.16.0 · accelerate 1.15.0
```

显存策略：家族互斥换出（Z-Image 与 Qwen 不同时驻留）、Qwen transformer fp8 权重量化（39GB → 20GB）
并落盘缓存、`enable_model_cpu_offload`。
