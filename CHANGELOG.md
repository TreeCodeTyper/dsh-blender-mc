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

[Unreleased]: https://github.com/TreeCodeTyper/dsh-blender-mc/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/TreeCodeTyper/dsh-blender-mc/releases/tag/v0.1.0
