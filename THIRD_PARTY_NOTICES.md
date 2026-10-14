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

## 商标

本项目与 Mojang Studios、Microsoft **无任何隶属或背书关系**。

"Minecraft" 是 Mojang Studios 的商标。
