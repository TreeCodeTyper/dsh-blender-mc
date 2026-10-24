# Changelog

本文件记录所有值得注意的改动。
格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

---

## [0.4.2] - 2026-10-24

「上传了，但认不出文件」——这一版就是把这条链路从头到尾补上反馈。

线上数据库里两条真实存档（`鉴于王国整合包地图1.zip`、`方可梦世界地图.zip`）的
`levelName` **都是「新的世界」**，而面板卡片只显示体积和范围，于是传完两张
长得一模一样、分不清哪张是哪个 zip；重命名的「保存」按钮其实是坏的
（mousedown 先触发 input 的 blur，React 把按钮卸载掉，click 永远到不了提交函数），
想改个名字只能靠回车；地图字节传完 = 100%，后端还在解压读 level.dat，
界面上毫无动静，像卡死；`warnings`（「zip 里有多个存档，已选…」）一个字都不显示；
皮肤传上去不会自动启用，出图还是 Steve。后端本身没问题 —— 拿 358 MB / 943 MB
的真实 zip 直传 HTTP 接口都是 201，坏的都在前端与「传完之后的反馈」。

### 修复

- **重命名的「保存」按钮能用了**：按钮加 `onMouseDown: preventDefault()` 保住
  输入框焦点，`onBlur` 从「丢弃改动」改成「提交改动」（Esc 用 `cancelRef` 挡住，
  不会误提交）；按钮文案从 `card.renamingHint`（那是「Enter 保存，Esc 取消」的
  提示语）改成真正的「保存」。
- **卡片能认出是哪份文件**：地图卡片补上 `版本 / N 个 region / 源文件 xxx.zip`，
  存档内名（`levelName`）与显示名不同时额外标一行 —— 两张「新的世界」不会再混淆。
- **报错说人话**：后端错误码是带命名空间的（`save.not-found`、`map.bad-ext`、
  `zip.unreadable`、`skin.bad-size` …），原来直接拼 `errors.<code>` 查词表，
  一个都命不中，永远显示「出错了：<Python 原文>」。现在加了 `ERROR_ALIASES`
  映射，`save.not-found` 会明确告诉你*要选到含 level.dat 和 region/ 的那一层*。
- **传完到入库之间不再装死**：进度到 100% 后切到「上传完成，正在解压、识别存档…」，
  并隐藏「取消上传」（那之后取消已经没有意义）。
- **入库摘要与警告都显示出来**：成功提示改成「已入库「名字」· 版本 · N 个 region
  文件」，后面逐条列出后端返回的 `warnings`（多存档、出生点扩范围等）。
- **新皮肤自动设为默认**：`addSkin` 只在「库里还没有默认」时才写 `defaultId`，
  第二张之后传的皮肤永远不会生效。现在 `POST /skins` 上传完顺手
  `setDefaultSkin`，响应里带 `defaultId`，面板提示「已上传「名字」并设为默认」。

### 说明

- 新增中英文文案键：`card.save` / `card.version` / `card.regions` / `card.source` /
  `card.levelName`、`upload.importing` / `upload.recognized` /
  `upload.recognizedNoVersion` / `upload.warning`、`errors.save_not_found` /
  `errors.save_no_region` / `errors.zip_empty` / `errors.zip_encrypted` /
  `errors.missing_world` / `errors.bad_type` / `errors.length_required` /
  `errors.skin_bad_ext`、`skins.doneDefault`。
- 客户端与路由都只改了 JS，没有动 Python 侧的解压/识别逻辑 —— 那条链路本来就对。
- 装完要 **Ctrl+Shift+R 硬刷新** DSH Web 才会加载新 bundle（没跑 `dev:web` 时
  客户端插件不热更新）。

## [0.4.1] - 2026-10-24

装完插件，AI 自己先说"你还缺什么"。

以前要用户主动想起 `mc_doctor`，才会看到一屏红线；新手往往先撞上
`mc_shoot` 的报错，才知道缺 MCprep / cv2 / 骨架。这一版加了一次**便宜的自检**，
结果缓存到 `<DSH_HOME>/blender-mc/readiness.json`，有缺口时作为一段运行时上下文
喂给模型 —— 用户下一次开口，AI 就会主动列出缺哪几项、每项一句怎么装。

### 新增

- **`py/readiness.py`**：不跑 Blender 的条件下体检七项（Python 依赖 / Blender /
  MCprep / 角色骨架 / 物品骨架 / 方块贴图 / ffmpeg）。只读目录 + 导入模块，
  单次通常几十毫秒。`--deep` 才额外问一次 Blender（会慢 5-10 秒，仅供手动排查）。
- **`cli.py readiness`** 子命令：`--json` 出快照、`--no-write` 只打印不落盘。
- **`systemPrompt` 运行时上下文**：只在确有缺口时注册（`dsh-blender-mc/readiness`），
  装齐了就一个字都不说。宿主没有该服务时静默跳过。
- 新配置（都可省略）：`startupCheck: 'cache' | 'full' | 'off'`（默认 `cache`）、
  `readinessTtlHours`（默认 12）。
- `mc_doctor` 跑完会顺手刷新这份缓存，所以"装完 → 自检 → 结论"会立刻同步。

### 说明

- **绝不在会话启动或提示词组装路径里起子进程**。缓存过期只在后台补刷，
  失败一律静默 —— 自检是锦上添花，不能拖慢任何一次对话。
- 缓存按"插件版本 + Blender 路径 + Python 路径"判定是否还作数；升级插件后
  旧结论不会被拿去提示人。

### 修掉的两个 bug（在真实宿主里才暴露出来）

- **插件可能整个加载不起来**：原来用 `ctx.timeout` 排后台探测。`ctx.timeout` 跑在
  Cordis 的 `timer` 服务背后，`inject` 里没声明就直接抛
  `cannot get property "timer" without inject`，插件 entry 当场 `did not activate`。
  改成原生 `setTimeout`（带 `unref`，dispose 时清掉），不再依赖宿主服务。
- **显式配了 `blender` 路径的用户，所有工具调用都会失败**：`runCli` 把
  `--blender <路径>` 插到了 `python` 与 `cli.py` 中间，实际命令成了
  `python --blender X cli.py …`，python 自己不认识这个选项，直接
  `Unknown option: --blender` 退出 2。改成插在 `cli.py` 之后。两种写法都实测过。
- 顺手：`spawn` 同步失败（比如受限环境里的 `EPERM`）不再变成未处理 rejection，
  而是照常返回 `code: -1` 和 `[启动失败] …` 说明。

### 顺带修掉的 7 个老 bug（这轮审计出来的）

- **没装 opencv 时，doctor / readiness 会把已经装好的 Blender 报成「找不到」**，
  还打印整套「去 blender.org 下载」，并连带把骨架/贴图/ffmpeg 全判缺失。
  根因是 `pipeline.py` 顶层 `from vision_check import check_image`（vision_check 顶层
  `import cv2`），而 `requirements._find_blender()` 用 `except Exception: return None`
  把 import 失败吞成了「没有 Blender」。现在 `check_image` 改成用到时才 import，
  `_find_blender` 再多一层不依赖第三方库的兜底搜索。**这条不修，0.4.1 的主动提示
  会对没装 cv2 的人满口假话。**
- 空的 `MCprep_addon/` 残留目录被判成「已安装」（`requirements._has_mcprep` 只看目录名）。
  现在要求目录里真有 `.py` 文件。
- `readiness --deep` 的探测分支永远走不到（`if mp_dir` 写在 `elif deep` 前面，
  只要目录存在就直接返回 True）—— 加了等于没加。
- 截断的 `.mca`（<8 KiB）让 `anvil.Region.chunk_offsets()` 直接 `IndexError`，
  崩掉 mc_scan / mc_shoot。现在当「这个区域没区块」跳过。
- 坏 `.geo.json` 抛的是 `SystemExit`，而 `extract.py` 只 `except Exception` ——
  一份坏模型让整批中止、`models.json` 都不写，跟注释说的「单个失败不影响整批」相反。
- 审图读图失败时 `check_image` 只回 `{'ok': False}`（没有 `verdict`/`score`），
  `step_check` 直接取 `r['verdict']` → 一个半截 png 就 `KeyError`。
- `--blender` 写在子命令后面（`doctor --blender X`）会被 argparse 当成未知参数拒掉，
  exit 2。现在解析前先把任意位置的 `--blender` 提到最前面。

### 升级

```bash
npm i -g dsh-blender-mc@0.4.1   # 或在 DSH 里重装插件
python py/cli.py readiness      # 想看当前结论可以直接跑
```

老版本配置不用改：两个新键缺省即按上面的默认值工作。

---

## [0.4.0] - 2026-10-24

这一版把"每支片子一个脚本"改成"一个内核 + 一份 JSON 配方"，顺带修掉两个一直存在的 bug。

### 修掉的两个 bug

- **`mc_shoot` 必崩 `defaultSkinsDir is not defined`**（0.3.0 起一直存在）：
  `lib/index.js` 里调用了 `defaultSkinsDir` / `resolveSkinPath` / `defaultSkinPath`，
  但这三个函数从来没被 import 过（它们一直在 `lib/skin-store.js` 里）。
  地图面板走 `map-routes.js` 所以是好的，只有出封面这条路一直是死的。
- **动画脚本崩了却报成功**：Blender 里 `--python` 抛异常时进程退出码仍是 0，
  而 `pipeline.animate()` 只看退出码 —— 结果是拿着上一次的旧工程继续渲，
  或者报一个和真实原因无关的错。现在按"这一步有没有新写出 .blend"判断，
  并且错误信息里带上 Blender 的 traceback。

### 新增

- **`py/film.py` + `mc_film` 工具**：分镜成片。一份分镜表（JSON）→ 逐镜建工程、
  按帧区间渲、重编号、ffmpeg 拼接 → 一支 MP4 + `分镜表.md`。
  同一套动作、每个镜头自己的机位，所以"一个动作三种景别"不用写新动画。
- **动画内核 `py/blender/anim_kit.py`**：动作 / 武器 / 特效 / 相机 / 灯光 / 渲染全在内核里，
  配方再也不用各自复制粘贴脚手架。三张可插拔表 `MOTIONS` / `WEAPONS` / `FX`。
- **`py/blender/animate_recipe.py`**：不到 60 行的壳，读 JSON → `anim_kit.build()`。
- **`presets/`**：`yujian.json`（御剑飞天）、`dash.json`（踏剑冲刺）、`README.md`（配方格式）。
  `animate_yujian.py` 退化成 20 行转发壳。
- `lift` / `travel` 现在既可以是数字（随进度涨到该值），也可以是关键帧轨道
  `[[帧, 值], ...]`，配 `progress` 就能写"先贴地掠飞再拉升"这种分段运动。
- `cli.py anim --preset` 不再限定枚举，`presets/` 里放什么就能用什么。
- `cli.py shoot --skin <路径|名字>`：以前皮肤只能靠 `DSH_SKIN` 环境变量。

### 已知限制

- 动作库仍然只有三支（killaura / aura / yujian）+ 一份 dash 演示配方；
  `MOTIONS` 里只有 `fly` 一种运动形状，表演类动作（走、跑、挥砍连招）这套程序化烘焙做不了。
- 分镜表的每个镜头共享同一条预设时间轴，是"同场多机位"，不是"每镜各自表演"。

### 计划中

- 英文本地化（`README.en.md`）
- 更多动画预设（现在只有"杀戮光环"）
- 景深 / 相机运镜（用空物体对焦 + 推轨）
- 更多审图判据："主体是否被前景遮挡"
- MiBlend 接入（MCprep 的替代方案）

## [0.3.1] - 2026-10-23

只改「别人找到它、看懂它」的部分，渲染与功能行为零变化。

### 文档与分发

- **README 首屏改版**：第一屏就是成品图（上传的地图 + 上传的皮肤渲出来的封面），
  紧跟 `0.2.1 → 0.3.1` 对比图和 10 秒动画截图，然后是「30 秒上手」三步命令。
  之前的版本要滚过徽章、说明、pipeline 图才看到第一张图。
- 新增 `assets/cover-skin.jpg`（换皮肤后的封面）、`assets/before-0.2.1.jpg` /
  `assets/after-0.3.1.jpg`（渲染链重做前后对比）。
- `README.en.md` 同步首屏结构（英文）。
- `package.json` 的 `description` 从 300+ 字的技术说明改成一句话：
  「Minecraft 存档 → Blender 封面与 10 秒动画 …… 支持在 DSH 设置页上传地图 zip 与人物皮肤。」
- `keywords` 从 13 个补到 20 个（补 `minecraft-java` / `blender-render` / `mca` /
  `cover` / `game-art` / `thumbnail` / `bilibili` 等搜索词）。

### 为什么单独发一版

npm 与插件市场卡片读的是**发布时**的 `description` / `keywords` / README 首图，
单纯改仓库不会让市场里的旧文案更新 —— 要发一次新版本才会重新抓取。

## [Unreleased]

## [0.3.0] - 2026-10-22

**两个大件：① 在 DSH 设置页就能上传背景地图（不用再翻存档路径）；② 渲染质量按「色彩管理 → 太阳 → 天空 → 三灯 → 相机 → 雾 → 审图」整条链重做。**

### 新增：背景地图上传（DSH 面板）

装好插件后 **设置 → 背景地图**：把整合包/存档目录压成的 zip 拖进去，插件自己解压、识别、索引、出缩略图。

- **真正解压 + 识别存档**，不是只存个 zip：从 zip 里找 `level.dat` + 同级 `region/*.mca`，
  按「区域数 / 维度 / datapacks / icon / 备份关键词」打分，选出真存档；
  zip 里套一层文件夹也能认（`方可梦世界/新的世界/level.dat`）。
- 一次上传里如果有多个候选存档，返回 `save.ambiguous` 让玩家点选，不瞎猜。
- 读 `level.dat` 拿 **LevelName / MC 版本 / DataVersion / 出生点 / 包围盒**，自动算一个
  机位搜索范围（`suggest_area`），读不到 `icon.png` 时用存档几何着色一张 256×256 缩略图。
- 卡片操作：**设为默认 / 重命名 / 删除**（删除是软删，进 `trash/`，7 天内可找回）。
- 数据目录 `%USERPROFILE%\.dsh\blender-mc\maps\`（可用 `mapsDir` 覆盖），**和插件目录分开**，
  重装插件不丢地图。
- 单包上限 2 GiB / 解压后 6 GiB / 20 万条目 / 压缩比 100，超限直接拒（`413`），不落盘。

### 新增：`map` 参数（不用再报路径）

`mc_shoot` / `mc_scan` 现在可以直接点名上传过的地图：

```jsonc
{ "map": "方可梦世界", "out": "E:/出图/封面" }   // 名字、前缀、或 m_xxxxxxxxxxxx 都行
```

`map` 会自动补上 `save`（解压出来的存档目录）和 `area`（按包围盒推荐的搜索范围），
显式传 `save` / `area` 仍然优先。命令行等价：`python py/cli.py map list|import|resolve|rename|delete|default|doctor`。

### 渲染：从「平光站桩」到「有光有雾有景深」

- **色彩管理**：`Standard` → **AgX + Punchy**（`VT_CANDIDATES` 顺序改成 AgX → Filmic → Standard）。
  这是整条链的根因：Standard 没有高光压缩，物理天空只能压到 `strength 0.03`（实测天空均值仅 0.392），
  环境光≈0，只剩三盏世界坐标硬光打到身上 —— 于是平光、无投影。换 AgX 后天空才敢开到 0.09 以上。
- **太阳**：仰角 45° → 8°（黄金时刻），颜色 `(1.0, 0.70, 0.42)`、能量 3.5 → 6.0、角径 2.5° → 1.6°，
  天空 `sun_size` / `sun_intensity` 同步；`dust_density`（5.2 已不存在，被 `except: pass` 静默吞掉）→ `aerosol_density`。
- **三灯改成相机基向量**：原来的三盏面光写在**世界坐标**里，换个机位布光就全错（Key 跑到角色背后）。
  现在 Key/Fill/Rim 用 `C ± fw*right*up` 跟着相机走，能量 210 / 130 / 700 W、色温分别是暖白 / 冷蓝 / 暖金，
  `use_shadow_jitter` 打开 —— 抹掉 EEVEE 面光的硬边。
- **接触阴影**：开 `use_raytracing`（SCREEN）+ `use_fast_gi`（distance 0.45）+ shadow ray/step 加档，
  脚底下终于有影子，不再「悬浮」。
- **体积雾**：EEVEE 5.2 下 world 的 Volume **必黑**（实测 6 组参数全是 0.0013），改成**有限体积盒**
  （150 格立方、density 0.0026、anisotropy 0.52、暖白色），并设 `volumetric_start/end/samples/tile_size`
  与 `use_volumetric_shadows`。远景终于有空气感，而不是一张贴图铺到天边。
- **相机**：机位从「站远处拍小人」改成 50mm / 距 8.2 格 / 相机高 1.30 / 看向 1.00 / 侧移 0.58，
  主体占画高从 0.431 → 0.55，头顶留白 0.314 → 0.207；加 DOF 对焦头部（`focus_object` 不行时退回几何算的
  `focus_distance`，避免焦点落在空气里）。
- **选机位连带选方位角**：只用「开阔度」排序会挑到最没信息量的地方（`scan.json` 里 17 个候选全是
  `openness 1.0 / flat 961`，`shots[0]` 直接退化成字典序）。现在每 15° 做一次视线采样，
  把「看不到墙、能看到天空」算进评分，角色朝向跟着相机方位角一起补偿。

### 审图：从「5 项」到「9 项」，并把误判修掉

- **接地判据修错**：原来脚底色差 52.7 被判成 `floating`（其实那正是接触阴影造成的差值）。
  新判据按色相 + 亮度比：`d_hue < 18` 且 `L_under ≤ L_side×1.02` 就算 grounded，并输出 `shadow_strength`。
- 新增 `check_sky`（地平线渐变 / 高光占比）、`check_depth`（主体 vs 背景清晰度，`dof_score`）、
  `check_rim`（轮廓光占比）、`check_rule_of_thirds`（三分法），并给负分权重。
- `subject_height_ratio` 理想区间 0.55~0.68。
- `check_image` 现在接受 Blender 用 `world_to_camera_view` 投影出来的 `subject_box`
  （写进 `结果.json`），不再靠颜色猜角色位置 —— 暖光下颜色检测会退化。

### 修复

- 渲染参数 `light_scale` 一直没被传下去（`pipeline.py` 的 `auto_adjust` 里那个 ×1.8 是空转的）。
- 剑穿模：`blade_w` 写死世界方向，改成「先摆姿势 → update → 读 pose bone 算握点 → 只设一次 matrix_world」。
- **朝向对不上镜头（拍成侧脸/后脑勺却照样高分）**：原来想用「相机方位变了多少度、yaw 就反向补多少度」
  来维持正面，实测证明它不是不变量（`cam_az 200 / yaw 25` 是正面，`cam_az 345 / yaw 173` 却是背面，
  两者相对角一样）。现在改成**几何量测搜索**：角色进场后，用「面→后脑方向」与「主体→相机方向」的
  夹角做目标函数，全周每 3° 扫一遍，取最接近 45°（标准 3/4 侧脸）的 yaw。日志会打
  「`★ 朝向搜索：yaw … 夹角 … → 正面/侧脸/背面`」，出图前就能看出朝向对不对。
- **剑挡脸**：`blade_w` / `normal_w` 是**角色局部系**里的方向（+X 右手侧 / +Z 上 / −Y 正面），
  以前直接当世界方向用；朝向搜索把 yaw 改到 300° 这类大角度后，刀身就横过来从脸前面劈过去。
  现在先按 `yaw` 绕 Z 旋转再进 `basis_from`，剑始终在手边、剑尖落在头外侧。
- 天空过曝：`sky_hot_ratio` 实测 0.79（整片近白），`--sky` 默认降到 0.12 后回到有层次的暖色渐变。
- `mc_scan` / `mc_shoot` 的 `required` 调整（`scan` 可全空、`shoot` 只要 `out`），配合 `map` 参数。
- 教程补「第 1.5 步：把存档传进 DSH」。
- **插件在装好后整条加载失败**（`cannot get property "webServer" without inject`）：曾用 `ctx.get('webServer')`
  探测可选服务，而 cordis 的 `ctx.get(名字)` 要求该服务在 `inject` 列表里，否则直接抛错，
  整个加载器条目报 Error、7 个 `mc_*` 工具和设置页一起消失。现在只走
  `ctx.inject(['webServer'], scope => mountMapRoutes(scope))`（服务在场才回调，缺席就安静跳过）。
- **上传入口一条请求都进不来**：`lib/index.js` 把外层 `ctx` 传给了 `registerMapRoutes`，
  在它上面读 `ctx.webServer` 抛错被 try/catch 吞成一条 warn，prefix 路由根本没注册
  （`GET /api/blender-mc/maps` 落到宿主兜底的 401）。改用注入作用域后复测 200。
- **地图库多套了一层 `maps/maps/`**：`default_root()` 与前端 `defaultMapsDir()` 都返回
  `<DSH_HOME>/blender-mc/maps`，而布局本身还要拼 `maps/<id>`。统一成库根
  `<DSH_HOME>/blender-mc`（存档在 `maps/<id>/world`），并给 `resolve_world` 加路径失效自愈。
- **大存档被前端挡在门外**：`lib/client.js` 的 `DEFAULT_MAX_UPLOAD` 兜底写 200 MB，宿主默认 2048 MB。
  列表请求一旦没拿到 `limits`，989 MB 的整合包被判「超过上限」且**不发请求**，看起来像插件没反应。
- **世界范围算成 1300 万格**：`_region_stats()` 用 min/max，整合包残留的离群 region
  （`r.25931.-58593.mca` 这种）把包围盒撑爆。改为按「相距 ≤8 个 region 的主簇」取包围盒，
  丢弃数记进 `bounds.outliers`；出生点落在主簇外时扩展范围而不是丢掉出生点。
- **带 BOM 的 job 文件解析失败**：`_read_json()` 改 `utf-8-sig`（PowerShell 5.1 写 BOM），
  此前会误报 `map.no-file 缺少 --zip`。
- **整包地图找不到机位（0 个）**：`py/scan.py` 的地表判定把树冠/植物/水面也算成「最高方块」，
  森林里整片地表都是 `oak_leaves`，「15×15 同高度草地」一个都不满足。现在跳过
  `DECOR`（叶子/水/草/花/雪片…）；仍然找不到时逐级放宽（15×15 → 7×7 → 4×4 → 2×2，
  草地要求放宽到任意可站方块），最后兜底用「5×5 内至少 9 格同高度」+ 开阔度挑落点。
  实测「方可梦世界」：15×15 一个都没有 → 4×4 找到 3 个。

### 新增：10 秒动画（两种预设）

- **`--preset killaura`（新的默认值）**：把「杀戮光环」按它真正的画面感做出来 ——
  站定摆姿势 → **双手各拔出一把钻石剑**（左手剑是右手剑沿 X 轴镜像出的副本，绑在 `Hand.L` 上）→
  **整个人像陀螺一样原地转 5 圈** → **边转边升空 3.4 格**，脚下法阵同时张开，
  镜头跟着抬高、退远，始终把人框在画面里。
  - 拔剑时两只手上各爆一下白光；旋转期间开运动模糊（shutter 0.5），剑身被拖成长弧。
  - **踩过的坑**：清 `animation_data` 后从 0 开始打旋转关键帧，会把封面工程摆好的 3/4 朝向清零、
    人变成后脑勺 —— 必须先读 `arm.rotation_euler.z` 当基准再加旋转量。
  - **坑 2**：相机注视点不能用封面构图里的 `aim0`（本来就偏一侧），要正对角色的中轴，
    否则人一升空就跑到画面左边；升空阶段还要把相机退到 1.42 倍距离，不然腿会被裁掉。
- **`--preset aura`**：旧的「挥剑 + 头顶光环 + 地面魔法阵」。
- 两个预设共用 `py/pipeline.py` 的 `animate(..., kind=...)`；`kind=='killaura'` 走
  `py/blender/animate_killaura.py`，否则走 `animate_aura.py`。
- **色彩管理和封面不再是两个世界**：`animate_aura.py` 原来按 `('Standard','Filmic','AgX')` 顺序试
  视图变换，Standard 永远成功 → 动画一直走 Standard、灰扑扑。改成直接调
  `_compat.set_view_transform(scene, prefer='AgX')`，并加 `look='AgX - Punchy'`。
- **光环从「纸板风车」改成「头顶光环 + 地面魔法阵」**：主环原来 r=1.15 挂在胸口，正对镜头就是
  一根横穿人物的白条 —— 现在挪到肩上方（`CHEST + 0.95`、r=0.70、压扁 0.55、emission 1.8）；
  品红环原来在 `CHEST - 0.55` 切大腿（像呼啦圈），改成贴地的魔法阵（`AZ + 0.06`、r=1.44）。
- **刃片**：6 片 `0.05×0.22×0.58`（像大板子）→ 8 片 `0.03×0.07×0.30`，emission 2.2。
- **斩击弧光停用**：旧的水平圆环 r=1.55 在正面机位糊成一根过曝白条，比没有更糟。
- **运镜 + 运动模糊**：相机按「朝角色推进 2.6 格 + 升高 0.22」打关键帧（原来完全静止），
  开 `use_motion_blur`（shutter 0.45、CENTER），挥砍终于有拖影。
- 已知限制：这个 Blender 版本没有可用的合成器节点树（`Scene.node_tree` 不存在，
  `compositing_node_group` 也没有），所以**没有辉光**，发光件边缘是硬边；
  105mm 镜头下靠镜头一侧的僵尸会占据很大前景，取景仍偏挤。

---

### 第二批：人物皮肤 + 插件自身 bug 修复（同版发布）

### 修复（插件自身 bug 排查，只动输入校验与错误出口）

- **`map import --json` 失败时退出码是 0**：`maplib._emit()` 的 JSON 分支无条件 `return 0`，
  上传校验失败也能「成功」返回 → 现在按 `ok` 返回 0/1，`lib/map-routes.js` 不再把失败当成功。
- **坏 zip 要等建完临时目录才炸**：`import_zip()` 在解压前先 `zipfile.is_zipfile()`，
  不合法直接回 `zip.unreadable`（HTTP 415），不碰磁盘。
- **`--center a,b` / `--y x,y` 甩 `ValueError` traceback**：改成 argparse 类型校验，提示
  「要写成 两个整数，用逗号分隔，例如 100,-40」。
- **`--res abc`**：现在校验「宽x高」且 16~7680 / 16~4320，不再一路传到渲染才失败。
- **`--samples 0` / `--attempts 0`**：原先静默渲出废图，现在要求 ≥1。
- **`scan` / `shoot` 指向不存在的存档**：预先检查目录与 `region/`，给出「存档目录不存在 /
  这个目录里没有 region/」而不是 `FileNotFoundError` traceback。
- **`anim` 指向不存在的 .blend**：先判存在，提示「先把封面渲出来（shoot）」。
- **`check` 传了非图片文件**：`check_image()` 捕获读图异常返回 `{ok:false,error}`，
  `cmd_check` 打印 `❌ 不是可识别的图像` 并以 1 退出，不再抛 `ValueError`。
- **`--area` 起止写反**（如 `40,0,40,0`）：自动交换并提示，不再静默得到 0 个机位。

### 背景世界（从动画素材里反查出来的三个问题）

- **人物动作**：新增姿势预设 DSH_POSE=hero|guard|raise|legacy（默认 hero：躯干朝镜头拧 12°、头再补 8°、左臂配平、右手在体侧举剑）；刃向改成**按「头球距离」自动选倒向**，不会再从脸中间劈过去。

- **握持点取错**：抓握点原来用 Hand.R 骨中点（BPS 手骨偏腕侧），剑看着像插在手腕上；现在改用骨架形变后「拳头网格里离手骨最近的顶点群」的中心，剑柄真正握进拳头。刃向也从角色局部系改成**视图系**（向上/向下 + 朝手那一侧倒），不会再从脸中间劈过去。

- **重装插件会把装配好的素材一起删掉**（最严重）：`vendor/` 原来默认在插件包目录里，
  而插件是按 npm 包安装的，升级/重装会整包重建 → 上次 `mc_setup` 装好的 BPS 角色骨架、
  物品库、ffmpeg 全没了。表现是「明明装过，出图却只有风景没有人」。
  现在 `vendor` 默认指向 `<DSH_HOME 或 ~/.dsh>/blender-mc/vendor`；
  只有那里还没有 `manifest.json` 时才回落到插件内的 `vendor/`（本地开发布局）。
- **素材清单（manifest.json）和代码对不上**：装配时只写了 `textures`，
  清单里手持物品叫 `item_rig`，而代码只查 `item` 这个名字 —— 于是
  「素材明明在 vendor 里，插件偏说没装」。现在按别名逐个查，
  而且只要 `vendor/rig/*.blend`、`vendor/item/` 实际存在就直接认。
- **推荐扫描范围被冻结在入库那一刻**：`suggest_area()` 的 `half` 改了也没用，
  因为 `meta.json` 里存着导入时算好的 `area`，之后 `mc_scan/mc_shoot` 一直用它
  → ±240 时代入库的地图永远只扫出生点旁边那一小块（全是石砖广场）。
  现在 `dto()` 每次按 `bounds + spawn` 现算。

- **机位选在湖里**：水在 `DECOR` 里被跳过，于是「湖底/海底」的列会退回到下面的沙子，
  被当成可站立地面 → 人物站在水面上，背景是一整片没有细节的水面。
  现在记录每列最高的液面，**水面高于地表的列整列剔除**（方可梦世界实测剔掉 46,634 列）。
- **机位选在树冠底下**：叶子同样是 `DECOR`，被跳过之后只看得到地面，
  头顶有没有树冠完全不知道 → 画面上半幅全是树叶、人物半埋在植被里（实测 476,-49）。
  现在记录每列**最高方块（含装饰）**，头顶 3 格内有东西的列剔除（实测剔掉 15,722 列）。
- **开阔度 1.0 反而最难看**：`best_shots()` 原来按开阔度排序，等于挑「最空的空地」，
  在整合包地图上那就是玩家自己铺的石砖广场。
  新增 `relief()`（视野锥里 3 格以上的东西占多少）与 `_shot_score()`：
  开阔度落在 0.60~0.98、背景有 3%~25% 的东西时才拿高分。
- **默认扫描范围太小**：`maplib.suggest_area()` 的 `half` 240 → 384。
  出生点附近往往是玩家铺的广场，±240 里挑不出自然地形（实测 5 个候选全是石砖广场，
  放大后才扫到草地/围墙/花丛）。
- **补上 MC 的云**：`build_cover.py` 新增 `[6.5] 云层` —— 5000 格见方的噪声平面、
  地表 +62 格、不投影、`DSH_MC_CLOUDS=0` 可关。⚠️ 云面必须铺得极远：
  105mm 竖直视野只有约 11°，900 格远的地方才升到 43 格高，小云面整块都在画面外。
  ⚠️ 天空本来就被曝到接近纯白时，白云的对比度很低 —— 这项是「有比没有好」，不是救命稻草。

### 新增：人物皮肤上传（DSH 面板）

- 设置页新增「人物皮肤」区块：拖 `64×64` PNG 上传、缩略图预览、设为默认、删除；
  和「背景地图」在同一个页面（`设置 → 背景地图` 往下滚）。
- `mc_shoot` 新增 `skin` 参数（皮肤库名字 / `s_` 开头的 id / PNG 绝对路径都行）；
  不传就用皮肤库里的默认皮肤，再没有就回原版 Steve。
- HTTP 接口 `GET|POST /api/blender-mc/skins`、`POST .../skins/default|delete|clear`、
  `GET .../skins/<id>/thumb`；只收 PNG，≤ 2 MiB，边长必须是 64/128/256/512。
- 皮肤存 `<DSH_HOME>/blender-mc/skins/`（和地图一样在插件目录外，重装不丢）。
- 限制：只支持经典模型（4 像素臂）皮肤，Slim（1.8）与 1.7 的 64×32 会错位。

**皮肤链路的边角修复**（同一版）：

- **索引里有、文件没了**：手动清过 `skins/` 目录或上次写盘失败时，重传同一张图原来只更新名字、
  不补文件，结果「上传成功但列表里没有」；现在会按新内容把 png 补回去。
- **文件名带路径**：浏览器有时把整条路径塞进 `filename`（拖拽 / 某些壳），
  名字会变成 `CUsersme…`；现在只取最后一段（basename）。
- **缩略图声明了 ETag 却不理 `If-None-Match`**：每次刷新都重下；现在命中就回 `304`。
- **皮肤错误码两套写法**：服务端是 `skin.bad-size`，前端表里是 `errors.skin_bad_size`，
  两边对不上，失败时只能显示兜底文案；现在点分码会别名到本地文案。

## [0.2.1] - 2026-10-20

**修掉「装上这个插件，DSH 就再也打不开，只能重装」的致命问题 —— 责任在安装脚本，不在渲染逻辑。**

### 修复

- **`scripts/install_to_profile.py` 会写坏 `cordis.patch.yml`（致命）**：profile 模板里的补丁层初始内容是一个空的 `[]` 占位符，旧脚本直接往文件末尾追加 `- id: blender-mc` 条目，于是文件里同时出现两个顶层元素 ——
  ```yaml
  []

  - id: blender-mc
    name: 'dsh-blender-mc'
  ```
  这不是合法 YAML。DSH 启动时 `parsePatchList()` 抛 `YAMLException: end of the stream or a document separator is expected`，异常一路冒到 `prepareProfile()`，Electron 主进程直接退出：双击图标窗口一闪就没，GUI 也进不去，用户视角只有「重装 DSH」一条路。
  现在的脚本会先把 `[]` 占位符改写成 `# []`（注释掉，保留提示），再追加条目；写入前用 PyYAML 自检「解析结果必须是顶层数组」；写完还会跑一次 `dsh <profile> --dump-config` 做门禁，失败就自动回滚。重复执行是幂等的，`--remove` 会把文件还原成单个 `[]`。
- **补丁层只剩注释/空文件也会起不来**：`parsePatchList()` 对空文档的解析结果是 `null`，报 `must be a top-level YAML array of loader patch entries`。`--remove`、`repair_profile.py` 都会确保文件里留一个 `[]`。
- 新增 **`scripts/repair_profile.py`**：给已经被写坏、现在打不开的机器用。`python scripts/repair_profile.py` 只体检；加 `--apply` 自动修（改前备份成 `cordis.patch.yml.bak-<时间戳>`）；加 `--restore` 回滚到最近一次备份。**不用重装 DSH。**
- **工具全部注册失败（DSH 0.2.0-rc.1 起）**：`dsh-tools` 现在强制要求 `output` 声明 `render`，否则 `ctx.tools.register()` 抛
  `TypeError: tool "mc_doctor" must declare output { schema, render, presentationMeta? }`。
  0.1.2 / 0.2.0 只写了 `output: { schema: { type: 'object' } }`，于是在新宿主上 7 个工具一个都注册不上（`mc_doctor` 第一个就炸，`apply()` 中断）。现在统一用一个 `OUTPUT` 常量（含 `schema` + `render`），渲染成 markdown 文本块。
- 单个工具注册失败不再拖垮整个插件：`apply()` 里逐个 try/catch，失败的只记一条 warn，其余工具照常可用。
- `mc_shoot` 的参数名转 CLI 参数从 `replace('_','-')` 改成 `replaceAll('_','-')`（下划线多于一个的参数不会被截断）。
- `peerDependencies` 全部标成 optional，版本范围放宽到 `^0.2.0-rc.1 || ^0.2.1-rc.1`（cordis 为 `^4.0.1`），并补上 `dsh.engines.dsh` / `dsh.compatibility.dshReleases` —— 与市场里其它插件一致，避免安装器对新宿主直接拒绝。
- `files` 补上 `scripts/*.py`、`scripts/*.mjs`，安装/修复脚本会随包发布。

### 排错备忘

- 插件条目自己激活失败**不会**让 DSH 起不来（只有 `agent-loop` / `webserver` / `modules` / `connection` / `headless-runner` / `acp` / `sdk-jsonrpc-server` 这些核心条目失败才致命），只在日志里打一行 `dsh: warning: 1 entry did not activate`。真正让人「只能重装」的是补丁层的 YAML 非法。
- 手工编辑 `cordis.patch.yml` 的两条铁律：一个文件只能有**一份**顶层列表；没有条目时必须留 `[]`（别留空文件、别只留注释）。

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
