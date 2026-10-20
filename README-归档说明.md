# D:\code\pixelforge —— 现在只剩模型权重

原 PixelForge 像素工坊项目已于 **2026-10-20 归档**。

## 东西去哪了

| 内容 | 位置 |
|---|---|
| **代码**（后端 + 前端 + 文档 + git 历史） | `D:\code\archive\pixelforge-2026-10\` |
| **模型权重**（215 GB，5 个底座） | `D:\code\pixelforge\models\` ← 就在这里，没动 |
| ~~运行时数据 `data/`（248 MB）~~ | 已删除（全是可再生的生成产物） |

## 要看文档先读这两份

- `D:\code\archive\pixelforge-2026-10\docs\项目总览.md`
  —— 功能清单（干什么/大致思路）、已知缺陷、待完成、参考了哪些外部项目
- `D:\code\archive\pixelforge-2026-10\docs\新项目起点.md`
  —— 四个候选起步方向的对比，建议与下一步

其次是 `ARCHIVE.md`（归档说明：怎么把归档项目跑起来）。

## 模型底座清单

| 目录 | 用途 | 备注 |
|---|---|---|
| `z-image-turbo` | 文生图（中文原生） | 默认底座 |
| `qwen-image-edit-2511` | 指令式编辑（去水印/延展/画风转换） | |
| `qwen-image-edit-2511-lightning` | 同上，8/4 步 LoRA 加速版 | |
| `qwen-image-layered` | 图层生成 | 57.7 GB |
| `wan2.2-ti2v-5b` | 视频生成 | 31.9 GB |

模型路径由使用方自己配置：填绝对路径，或建目录联接
`mklink /J <项目>\models D:\code\pixelforge\models`。