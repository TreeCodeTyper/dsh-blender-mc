# Changelog

本文件记录所有值得注意的改动。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### 计划中
- 英文本地化（`README.en.md`）
- 更多动画预设（现在只有"杀戮光环"）
- 景深 / 相机运镜（用空物体对焦 + 推轨）
- 更多审图判据："主体是否被前景遮挡"
- MiBlend 接入（MCprep 的替代方案）

---

## [0.2.0] - 2026-10-20

**把"某个作品的模型导入"改成了通用的「自带模型 / 模型提取」。**

原先 `build_cover.py` 里有一段只服务于单一作品的模型导入代码：环境变量、
材质名、日志全都带着作品名；而且这段代码只存在于工作区，没进 npm 包 ——
面板调用和本地源码行为不一致。现在整条链路改成通用能力：任何符合 Bedrock
几何格式的模型都能进来，插件本身**不含任何作品名，也不附带任何模型或贴图**。

### 新增

- `python py/cli.py extract <资源目录> --out <目录>`：批量把 `.geo.json` + 贴图转成 OBJ，
  自动按 `identifier` 命名、猜贴图、写 `models.json` 清单（来源 / 面数 / 包围盒 / 失败项）
- `py/bedrock.py`：Bedrock 几何 → OBJ 转换器（纯 Python，不需要 Blender）
- `py/extract.py`：批量提取 + 贴图匹配 + 单模型元信息
- `py/model_spec.py`：`DSH_MODELS` 描述文件 / `DSH_MODEL1_*` 槽位的统一解析
- 新工具 `mc_extract_models`（DSH 侧第 7 个工具）
- 插件 config 新增 `models:`，可以写 `models.json` 路径，也可以直接写数组
- `docs/自带模型.md`：参数表、常见问题、授权与合规
- `scripts/test_extract.py`：不依赖任何素材的提取链路自测（CI 里跑）

### 修复

- **分辨率 / 采样没接线**：`step_render` 只传了 8 个参数，`params['res']` 从头到尾没生效 ——
  `结果.json` 写 `1280x720`，实际可能出 `960x540`。现在通过 `DSH_RES_X/Y` + `DSH_SAMPLES`
  传给 build_cover，渲染后**读回 PNG 实际尺寸**写进 `结果.json`（`res_requested` / `res_actual`），
  不一致会在日志里告警。`shoot` 新增 `--res` / `--samples`，`mc_shoot` 同步开放。
- **doctor 对 MCprep 假绿**：只看 addon 目录在不在就报「✅ 已安装」，
  实际 `prep_materials` 可能没注册上。现在 doctor 会真跑一次 Blender 探测操作符，
  失败时明确提示「会自动走内置材质兜底」（不阻断 —— 兜底能出可用图）。
- **`install_to_profile.py` 版本写死 `^0.1.1`**：改成读 `package.json` 的 version，
  以后发版不用再改脚本。
- **`animate()` 的 docstring 说"导出 MP4/GIF"**：实际只出帧序列，改成如实描述
  （转视频由 cli 打印 ffmpeg 命令）。
- `build_cover.py` 头部注释过期（"相对 NPM 原版的两处改动"）→ 改成当前运行方式与坑索引。
- 删掉未跟踪的 `py.bak_原版/`（有 git 历史，不需要目录备份）；`.gitignore` 补测试临时目录。
- **握剑姿势**：之前只把剑 bone-parent 到 `Hand.R`，**完全没摆手臂** —— 手垂在胯边，
  1.4 格长的剑只能横在胸前，看起来就是"没握住"。现在按课程笔记 4.4（BPS V4 手臂默认 FK）
  转 `Top Arm.R` / `Bottom Arm.R` / `Hand.R` 三根 pose bone 摆出举剑姿势，
  抓握点改用**摆好姿势后的** `Hand.R` 骨（网格 bbox 不含骨架形变，手抬起来它还停在原位）。
  可用 `DSH_ARM_POSE=0` 关掉，`DSH_ARM_SHOULDER/ELBOW/WRIST` 微调角度。
- **握持不是一个点，而是一段**：第一版把柄尾顶在掌心，剑整把都在手上方，看着像"托着"。
  现在沿刃轴把剑下沉 `DSH_SWORD_SINK`（默认 0.55 本地单位 ≈ 0.28 方块），
  手掌包住一段柄、柄尾从拳下露出、刀刃从拳上伸出 —— 手和剑真正相交。
- **动画第一帧会把手臂打回垂手**：`animate_aura.py` 的关键帧原本是绝对值，
  现在改成**相对封面姿势的增量**，挥砍从举剑姿势起手。
- **相对路径的输出目录写不进图**：Blender 按工作目录解析相对路径，只报
  `No such file or directory`，图白渲一遍。`shoot()` / `animate()` 现在统一转绝对路径。
- **MCprep 没注册上时画面发白**：MC 的草顶 / 树叶 / 水贴图本身是灰的，颜色靠群系 tint；
  MCprep 的 `tracker.json` 写盘被拒会导致 addon 注册中断、`bpy.ops.mcprep.*` 全部消失，
  草地就渲染成一片灰白。现在检测到 `prep_materials` 跑不通时会走**内置兜底**：
  贴图直连 + 群系染色 + 对齐 MCprep 的材质参数（Roughness 0.72 / Specular 0），
  日志会打印 `⚠️ MCprep 没跑通 → 用内置兜底…`。MCprep 正常时不会触发。
- `scripts/test_extract.py` 的合成贴图改为纯标准库写 PNG（带条纹）：
  之前走 cv2 时中文路径写不进、静默退化成纯色，验证不出 UV 是否对

### 变更

- `build_cover.py`：模型导入改为通用 `import_model()`，日志前缀 `[模型 N]`、
  材质名 `ModelN`，落位逻辑（朝向 → 缩放 → 包围盒贴地）不变
- 环境变量统一为 `DSH_MODEL1_* / DSH_MODEL2_* / DSH_MODEL3_*`
  （i=1 也接受不带数字的 `DSH_MODEL_*`）

### 迁移

- v0.1.x 的旧环境变量前缀仍然可用，日志里会提示迁移，**下个主版本移除**
- 旧的作品专用导入函数已删除，不再读取任何作品相关命名

### 合规

- npm 包依旧只含代码与文档：`files` 白名单里没有任何资产目录
- CI 的 `hygiene` 继续拦截 `vendor/`、`.blend`、渲染产物、模型/贴图提交
- README / 使用教程 / `docs/自带模型.md` 增加"素材授权自行确认、插件不分发"的说明

---

## [0.1.1] - 2026-10-15

**修的是"装成 npm 包后插件根本连不上配置"的问题。**

v0.1.0 发布后准备实际安装时才发现：`cordis.patch.yml` 里配的东西
**一个都没生效**。两个独立的缺口叠在一起。

### 修复

- **`lib/index.js`：把插件 config 映射成环境变量传给 Python**
  Python 是独立进程，读不到 cordis 的 config。之前只通过命令行传了
  `blender` 一个参数 —— 也就是说 `rig` / `item_rig` / `textures` / `ffmpeg`
  这四项**配了也是白配**，用户会以为配好了，插件还在自己瞎猜路径。
  现在统一映射成 `DSH_MC_*` 环境变量。

- **`py/pipeline.py`：`resolve_assets()` 认环境变量**
  优先级改成：环境变量 > 显式 config > `vendor/manifest.json` > 现场扫描

- **`py/setup_assets.py`：`default_sources()` 改成"按特征找"而不是"按层数找"**
  本地开发布局是 `<工作区>/04_工具脚本/dsh-blender-mc/py`，往上三层正好是
  工作区根。但 npm 安装后变成 `<profile>/node_modules/dsh-blender-mc/py`，
  往上三层是 **profile 目录** —— 在那儿找 `pack/` 永远找不到。
  现在逐级往上找含 `pack/` 或 `03_素材库/` 的目录，最多 8 层。

- **新增 `DSH_MC_SOURCES` / `DSH_MC_VENDOR`**
  装成 npm 包时插件目录会被重装覆盖，`vendor/` 放里面会被冲掉；
  用 `DSH_MC_VENDOR` 可以指到稳定的地方。

### 三种布局都实测过

| 场景 | 结果 |
|---|---|
| 本地开发布局 | ✅ 自动找到工作区（3 个源） |
| npm 布局，无环境变量 | ⚠️ 找不到 —— **所以才必须填 `sources`** |
| npm 布局 + `DSH_MC_SOURCES` | ✅ 正常 |

### 记录

- 新增坑 69（配置没接通）写进坑清单

---

## [0.1.0] - 2026-02-XX

首个可用版本。

### 新增

**核心流水线**
- `mc_scan` —— 在存档里找适合拍摄的机位。要求成片同高度平地，并排除视线遮挡。
  **扫描范围外的格子按「有遮挡」处理**，避免选出被建筑包围的假阳性机位
- `mc_shoot` —— 从存档出封面。自动找机位 → 导出几何 → 搭 Blender 场景 → 渲染
- `mc_anim` —— 出 10 秒「杀戮光环」动画（挥剑两刀 + 旋转能量环 + 斩击弧光）
- `mc_check` —— 只审图，不跑 Blender
- `mc_doctor` —— 检查运行条件，**缺什么就给出完整安装指引**
- `mc_setup` —— 从本地资源目录装配第三方资产

**★ OpenCV 审图闭环**
- 渲完自动检查：朝向（正面/背面）、接地、曝光、布光、构图
- 不合格按问题类型**自动调参重渲**，最多 N 轮
- 输出标注图（框出识别到的角色 + 结论分数）

**Blender 侧**
- `anvil.py` —— 纯标准库读 `.mca`，不需要 Blender 也不需要 Mineways
- `export_obj.py` —— 存档 → OBJ，**材质名用 MC 贴图名**（供 MCprep 识别）
- `build_cover.py` —— 导入世界 + MCprep 材质染色 + 角色骨架 + 手持物品挂 `Hand.R`
  + 物理天空 + 三点布光 + 相机
- `animate_aura.py` —— 骨骼关键帧 + 旋转光环 + 斩击弧光
- `_compat.py` —— **Blender 版本兼容层**，把反复踩的版本坑集中到一处

**工程化**
- `requirements.py` —— 必需/可选分清楚，缺可选的降级继续跑而不是报错
- `setup_assets.py` —— 自动挑对版本（BPS 选 V4.0.9 避开会闪烁的 BETA1）、
  ffmpeg 只记路径不复制、跳过用不上的大件
- `scripts/smoke.mjs` —— 不启动 DSH 就能验证插件外壳
- GitHub Actions CI（外壳 + Python + 仓库卫生 + 文档链接）

### 说明

**为什么不打包第三方资产**：MCprep（GPL）、BPS Rig、MC 素材都属他人作品，
再分发不合适；而且大部分用不上（Mineways 251 MB、Cycles Rig 整包 551 MB，
实际只用其中 1 个 15.7 MB 文件）。改为从用户本地资源目录"装配"，
`vendor/` 排除在版本库和 npm 包之外。

### 已知限制

- 只支持 Java 版存档（Anvil 格式），**不支持基岩版**
- 扫描偏向 +x 方向（范围边界外被当作有遮挡，靠左边界的点只有朝 +x 看才有足够数据）
- 「接地」检查是全图颜色统计，纯色超平坦地形下判据会失效
- 角色骨架和物品库需要自备
- 动画只有"杀戮光环"一种

---

## 坑注释清单

本项目的代码里标了 `⚠️ 坑 N`，写明**报什么错、为什么错、正确判据是什么**。
这是本项目最有价值的产出之一 —— 这类知识最容易丢，不写下来半年后会原样再踩一遍。

| 坑 | 内容 |
|---|---|
| 17 | Blender 4.4 起 `Action` 没有 `.fcurves`，改成 `layers → strips → channelbags` |
| 35 | Blender 5.2 把天空 `NISHITA` 改名 `MULTIPLE_SCATTERING` |
| 53/57 | 「标准」视图转换无高光压缩 → 物理天空削顶成白、动态范围被占满、布光完全显不出来 |
| 55 | 判断"哪些网格算身体"错了四次，最终判据：**有指向该骨架的 Armature 修改器 且未隐藏** |
| 58 | 扫描时**范围外必须当作有东西**，否则会选出被房子包围的假阳性机位 |
| 59 | `o.bound_box` **不算修改器形变** → 拿它算落地角色会浮在半空；且迭代会发散（depsgraph 不刷新） |
| 62 | Windows 下 `cv2.imread` **不支持中文路径**，必须走 `imdecode` + `np.fromfile` |
| 63 | 审图找主体的判据：**只有角色有皮肤色**，海面和天空没有 |
| 64 | 审图判朝向只取顶部 **22%**（MC 角色头只占身高 25%），取 35% 会把手臂算进来 |
| 65 | 扫描的提前退出判据必须覆盖**所有**资产种类，否则漏掉后面的目录 |
| 66 | Windows 控制台默认 GBK → 打印 ✅ ❌ ⚠️ 这类符号会 `UnicodeEncodeError` 崩掉。`cli.py` 启动时强制把 stdout/stderr 切到 UTF-8（用 `errors='replace'` 兜底），不要求用户记得设 `PYTHONIOENCODING` |
| 67 | **`package.json` 的 `files` 白名单里，`.npmignore` 和 `.gitignore` 都排除不掉东西。** 所以写裸目录名（`"py"`）会把 `__pycache__/*.pyc` 一起带进 npm 包（实测 13 个文件 95 KB）。必须写 glob（`"py/**/*.py"`）。加 `.npmignore` 反而更糟 —— 它的存在让 npm 不再参考 `.gitignore`，`.pyc` 从 4 个变成 13 个 |
| 68 | **npm 账号开了 2FA 时，Classic Token 的 "Publish" 类型不绕过 2FA**，`npm publish` 报 `403 Two-factor authentication ... required`。只有 "Automation" 类型（或勾了 "Bypass 2FA" 的 Granular Token）能免验证码发布；否则每次都要 `--otp=123456`。注意 `npm publish --dry-run` **不会**触发这个检查，所以它通过不代表真发布能过 |
| 69 | **DSH 插件里，JS 侧的 config 不会自动到 Python 侧。** Python 是独立进程，读不到 cordis 的 config —— 必须自己用环境变量或命令行参数传。v0.1.0 只传了 `blender` 一个，导致 `cordis.patch.yml` 里配的 rig/textures/ffmpeg 全是摆设，配了也不生效 |
| 70 | **"按层数往上找目录"在插件装成 npm 包后必然失效。** 本地布局 `<工作区>/04_工具脚本/插件/py` 往上三层是工作区根，但 npm 布局 `<profile>/node_modules/插件/py` 往上三层是 profile 目录。要**按特征找**（遇到含 `pack/` 的目录就当工作区根），不能按固定层数 |

[Unreleased]: https://github.com/TreeCodeTyper/dsh-blender-mc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/TreeCodeTyper/dsh-blender-mc/releases/tag/v0.1.0
