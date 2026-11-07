# 第三方资产声明

本仓库的 [MIT 许可](LICENSE) **仅覆盖本仓库自身的代码与文档**。

本插件在**运行时**需要若干第三方资产。它们**不由本项目分发**，
需要使用者自行获取：

| 资产 | 许可 / 归属 |
|---|---|
| [Blender](https://www.blender.org/) | GPL |
| [MCprep](https://github.com/Moo-Ack-Productions/MCprep) | GPL |
| [BPS Character Rig](https://archive.blackplasma.xyz/assets/bps-character-rig/) | 归 BlenderPlasticStudio 所有 |
| Cycles Minecraft Rig BSS Edit | 归其作者所有 |
| Minecraft 相关贴图与素材 | © Mojang Studios，All Rights Reserved |
| [ffmpeg](https://ffmpeg.org/) | LGPL / GPL |
| [opencv-python](https://github.com/opencv/opencv-python) | Apache 2.0 |
| [numpy](https://numpy.org/) | BSD-3-Clause |

## 这些资产是怎么进来的

跑 `python py/cli.py setup` 时，插件会从**使用者本地已有的资源目录**
扫描并复制这些资产到 `vendor/` 目录。

`vendor/` 同时被两处排除：

- **`.gitignore`** —— 不进版本库
- **`package.json` 的 `files` 白名单** —— 不进 npm 包

CI 里有一条 `hygiene` 检查专门拦这个（见 `.github/workflows/ci.yml`）。

**理由有两条：**

1. **版权** —— 这些是别人的作品，再分发不合适
2. **体积** —— 光是 BPS 骨架 + 物品库就有 16 MB，整包资源（Mineways 251 MB、
   Cycles Rig 551 MB）更是没必要 —— 本项目用纯 Python 直接读 `.mca`，
   **不需要 Mineways 那套 GUI 导出流程**

## 背景地图导入的参考与原创声明

`mc_map_import` / `py/blender/import_map.py` 的**工作流**参考了以下公开工具
的通用做法：把 Minecraft 世界导出为 OBJ + 贴图，在 Blender 里导入、准备材质、
归入 `MAP` 集合，再继续做角色与动画：

- [MCprep](https://github.com/Moo-Ack-Productions/MCprep)（GPL）—— 可选的材质准备后端
- [Mineways](https://www.realtimerendering.com/erich/minecraft/public/mineways/) —— OBJ 导出工作流参考
- [MiBlend](https://github.com/miguel1049/MiBlend) —— 材质后处理工作流参考
- [MCBlend](https://github.com/Nusiq/mcblend) —— 基岩版模型/动画插件，工作流参考

本项目的**代码是原创实现**：`py/anvil.py` 直接解析 `.mca`，
`py/blender/export_obj.py` 生成 OBJ/MTL，`py/pipeline.py` 负责区域计算与缓存，
`py/blender/import_map.py` 负责场景组织。仓库不包含、也不分发上述项目的代码；
运行时只在用户本机已安装 MCprep 时调用它的 `prep_materials`，没有它就走内置兜底。
上述工具的名称与商标归各自作者所有，本项目与它们无隶属或背书关系。

## 原创 AI 人偶（ai-rig）的参考与原创声明

`py/blender/ai_rig.py` 与 `py/char_control.py` 是**原创 AI 人偶**的实验性实现。
它的设计**参考**了以下公开 rig / 工作流的通用思路：

- [BPS Character Rig](https://archive.blackplasma.xyz/assets/bps-character-rig/) ——
  面板控制器、FK/IK 切换、脚 IK 目标的组织方式
- Cycles Minecraft Rig BSS Edit —— 眼/眉 IK、分段面部、Advanced Mode 的分层思路
- Minecraft Simple Rig / Minecraft Mob Rig —— 极简骨架与四足 IK 的取舍
- [MiBlend](https://github.com/miguel1049/MiBlend) /
  [MCBlend](https://github.com/Nusiq/mcblend) —— 材质后处理与游戏内绑定的工作流参考

**本项目没有复制上述项目的网格、骨骼、权重、贴图或代码。**
`ai_rig.py` 用程序化方块拼出角色与骨架，`char_control.py` 定义原创的
CharacterSpec 控制契约（root / pelvis / 脚 IK / 手 IK / 脊柱 / 头 / 脸）。
上述项目的名称与商标归各自作者所有，本项目与它们无隶属或背书关系。
本项目整体以 MIT 许可开源，欢迎在保留本声明的前提下复用与修改。

## 使用者自备的模型与贴图

本插件提供**模型提取与摆放**能力，但**不附带任何模型或贴图**：

- 提取（`python py/cli.py extract`）只转换使用者自己目录里的
  Bedrock 几何（`.geo.json`）与贴图，产物落在使用者指定的目录
- 插件不上传、不下载、不内置任何模型；代码里也不含任何作品名
- 提取产物能否分发 / 商用，取决于原素材的许可 —— 需要使用者自行确认

CI 的 `hygiene` 检查会拦下 `.geo.json` / `.obj` / `.mtl` 等模型文件的提交，
防止有人把素材误提交进仓库。

## 商标

本项目与 Mojang Studios、Microsoft **无任何隶属或背书关系**。

"Minecraft" 是 Mojang Studios 的商标。
