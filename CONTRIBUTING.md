# 参与贡献

**感谢你想帮忙！** 先说清楚最重要的一条：

> ## ⚠️ 只有维护者能直接推送到本仓库。其他人一律走 **Fork → Pull Request → 审核 → 合并**。

这是**强制**的，不是建议。原因很简单：这个项目会读你的存档、会在你机器上跑 Blender，
一个没审过的改动可能让别人的渲染出问题。所以每一行进主分支的代码都得有人看过。

---

## 目录

- [贡献流程（一步步来）](#贡献流程一步步来)
- [审核标准](#审核标准)
- [提交信息规范](#提交信息规范)
- [代码规范](#代码规范)
- [特别欢迎的贡献](#特别欢迎的贡献)
- [本地开发](#本地开发)
- [报告 Bug](#报告-bug)
- [分支保护规则](#分支保护规则)

---

## 贡献流程（一步步来）

### 0. 先搜一下，别重复劳动

- [已有的 Issue](https://github.com/TreeCodeTyper/dsh-blender-mc/issues)
- [已有的 PR](https://github.com/TreeCodeTyper/dsh-blender-mc/pulls)

**大改动请先开 Issue 讨论**，别写完 500 行才说 —— 万一方向不对，你的时间就白费了。

### 1. Fork 本仓库

点右上角的 **Fork** 按钮，得到 `https://github.com/<你的 GitHub 用户名>/dsh-blender-mc`。

### 2. 克隆**你自己的 fork**（不是源仓库）

```bash
git clone https://github.com/<你的 GitHub 用户名>/dsh-blender-mc.git
cd dsh-blender-mc
```

顺手把源仓库加成远端，方便后面同步：

```bash
git remote add upstream https://github.com/TreeCodeTyper/dsh-blender-mc.git
```

### 3. 建分支

**不要直接在 `main` 上改。** 分支名用这个格式：

```bash
git checkout -b feat/加个夜景机位        # 新功能
git checkout -b fix/中文路径读图失败      # 修 bug
git checkout -b docs/英文文档             # 文档
git checkout -b refactor/拆开审图模块     # 重构
```

| 前缀 | 用途 |
|---|---|
| `feat/` | 新功能 |
| `fix/` | 修 bug |
| `docs/` | 只改文档 |
| `refactor/` | 重构，不改行为 |
| `test/` | 加测试 |
| `chore/` | 杂活（依赖升级、配置） |

### 4. 改代码

在 [代码规范](#代码规范) 和 [审核标准](#审核标准) 的框架内自由发挥。

### 5. 自测（**这步不能跳**）

```bash
# ① 条件齐全吗
python py/cli.py doctor

# ② 插件能加载、6 个工具都注册上了吗
node scripts/smoke.mjs

# ③ 审图模块还有效吗（拿几张你渲出来的图喂它）
python py/cli.py check <你渲的图.png>

# ④ 你的改动影响出图吗？跑一遍端到端
python py/cli.py shoot <存档> --area x0,x1,z0,z1 --out ./测试出图
```

**PR 里请贴上这几条命令的输出。** 没有自测证据的 PR 会被直接打回。

### 6. 提交并推送

```bash
git add .
git commit -m "feat: 新增夜景机位预设"
git push origin feat/加个夜景机位
```

### 7. 开 Pull Request

回到 GitHub，你 fork 的页面上会出现 **Compare & pull request** 按钮。

**PR 描述里必须包含：**

```markdown
## 做了什么
一句话说清楚

## 为什么
解决什么问题 / 满足什么需求

## 怎么测的
粘贴 doctor / smoke / check / shoot 的输出

## 影响范围
- [ ] 改了命令行参数
- [ ] 改了审图判据
- [ ] 改了 Blender 侧脚本
- [ ] 只在文档

## 截图 / 出图
（如果是视觉相关的改动，贴改前改后的对比图）
```

### 8. 等审核

维护者会看。可能出现的情况：

| 状态 | 意思 | 你要做什么 |
|---|---|---|
| ✅ Approved | 通过了 | 等合并 |
| 💬 Changes requested | 要改 | 在**同一个分支**上继续提交，PR 会自动更新 |
| ❌ Closed | 不合并 | 看关闭理由；有疑问就在 PR 里问 |

**改完不用重开 PR**，往同一个分支推就行：

```bash
git add .
git commit -m "review: 按意见改成用 eval_bbox"
git push origin feat/加个夜景机位
```

### 9. 审核通过 → 维护者合并

合并由**维护者**做（Squash merge，把你这堆提交压成一个）。
合并后你可以删掉自己的分支。

---

## 审核标准

维护者会按这几条看。**提前对齐能省好几轮来回。**

### 必须满足

- [ ] **`python py/cli.py doctor` 通过**
- [ ] **`node scripts/smoke.mjs` 通过**
- [ ] **没有把 `vendor/` 或渲染产物提交进来**（`.gitignore` 已排除，但别用 `-f` 强推）
- [ ] **新加的非直觉判断写了「为什么」**，不只写"做了什么"
- [ ] **改了行为就更新 `CHANGELOG.md`**
- [ ] **不引入新的必需依赖**（除非在 PR 里说明理由并获认可）

### 强烈建议

- 视觉相关的改动贴**改前/改后对比图**
- 修 bug 的改动**说清复现步骤**（最好加个回归检查）
- 新参数**给个合理默认值**，让不填也能跑

### 会被打回的

- ❌ 把第三方资产（`.blend`、`.zip`、贴图）提交进仓库
- ❌ 硬编码只有你机器上才有的绝对路径
- ❌ 重构别人没让你重构的模块（分开提 PR）
- ❌ 只改格式/空格的巨型 diff
- ❌ 提交信息是 "update"、"fix"、"111" 这种

### 关于「坑注释」

本项目有个约定：**踩过的坑要在代码里留痕**，格式是：

```python
# ⚠️ 坑 59：o.bound_box 不算 Armature 修改器的形变 ——
#   拿到的是静止姿态包围盒，拿它算落地角色会浮在半空
#   （实测腿底 65.75 而地面 65，浮了 0.75 格）。
#   必须走 evaluated_get(dg).to_mesh() 取形变后的顶点。
```

**为什么这么要求**：这类知识最容易丢。不写下来，半年后有人（包括你自己）
会原样再踩一遍 —— 本项目在"判断哪些网格算身体"这一个问题上就错了**四次**。

**你修 bug 时请照这个格式补注释。** 这是本项目最有价值的产出之一。

---

## 提交信息规范

用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
<类型>: <简短描述>

<可选：详细说明>
```

| 类型 | 用途 |
|---|---|
| `feat` | 新功能 |
| `fix` | 修 bug |
| `docs` | 文档 |
| `refactor` | 重构 |
| `test` | 测试 |
| `chore` | 杂活 |
| `perf` | 性能 |

**例子：**

```
feat: 支持指定渲染分辨率

原来固定 1280x720，现在可以用 --res 传 "1920x1080"。
分辨率变化时相机的取景会自适应（按传感器宽度换算）。

Closes #12
```

```
fix: BPS 骨架的隐藏变体网格污染包围盒

BPS V4 带两套手臂（经典 + Alex 细手臂），后者没被摆位、
躺在世界原点且 hide_render=True。求值包围盒把它算进去，
导致角色被抬高 0.75 格（看起来悬浮）。

判据改成「有指向该骨架的 Armature 修改器 且未隐藏」。
```

---

## 代码规范

### Python（`py/`）

- **标准库优先。** 除了 `opencv-python` 和 `numpy`，别加新依赖
- 4 空格缩进，行宽别太夸张
- 函数写 docstring，**说清"为什么"**
- 中文注释可以，本项目主要读者是中文用户
- 别用 emoji 打印（Windows 的 GBK 控制台会 `UnicodeEncodeError`）

### JavaScript（`lib/`）

- ESM（`import` / `export`），不用 CommonJS
- 保持 `export const name` / `export const inject` / `export function apply(ctx, config)` 三个导出
- `ctx.tools.register()` 的返回值要收进 `disposers` 数组，并在 `ctx.on('dispose', ...)` 里释放
- **工具的 `description` 就是给模型看的"提示词"** —— 写清楚什么时候该用它，直接决定它会不会被正确调用

### Blender 侧脚本（`py/blender/`）

- **跨版本兼容走 `_compat.py`**，别自己写 `try/except` 链
- 需要 Blender 5.x 的行为时，注释里写清是哪个版本改的、报什么错
- 脚本要能 `blender -b --python xxx.py -- <args>` 无界面跑通

---

## 特别欢迎的贡献

不知道从哪下手？这几个都是明确需要的：

| 方向 | 说明 | 难度 |
|---|---|---|
| 🌍 **英文文档** | 现在只有中文。`README.en.md` 完全是空的 | 低 |
| 🎬 **更多动画预设** | 现在只有"杀戮光环"。走位、挖掘、战斗都行 | 中 |
| 👁️ **更多审图判据** | "主体是否被前景遮挡"、"角色是否在画面外" | 中 |
| 🧱 **MiBlend 接入** | 它是 MCprep 的替代方案，有 `Lazy Biome Color Fix` 等功能 | 中 |
| 🎨 **景深 / 运镜** | 现在相机是固定的，可以加空物体对焦 + 推轨 | 中 |
| 🔧 **渲染速度** | 现在是 EEVEE 256 采样；试参数时可以更快 | 低 |
| 📦 **更多方块/物品** | 手持物品现在只认钻石剑 | 低 |

**第一次贡献？** 从 `docs` 或 `README.en.md` 开始最好，不用碰代码逻辑。

---

## 本地开发

> ⚠️ **先读 [交接文档](docs/交接.md) 第 3 节「三棵源码树：谁是真身」。**
> 改代码只改仓库；`node_modules/dsh-blender-mc` 是安装出来的生产副本，
> 在那里改的东西既进不了 git，也会在下次安装时被覆盖。

### 挂成 DSH 开发插件

在 `~/.dsh/profiles/<profile>/package.json` 里：

```json
{
  "dependencies": {
    "dsh-blender-mc": "file:E:/你的路径/dsh-blender-mc"
  },
  "dsh": { "profile": { "bundles": ["...", "dsh-blender-mc"] } }
}
```

然后 `pnpm install`，**开新会话**生效。

> ⚠️ 插件在**会话启动时**加载，改完代码要开新会话（刷新无效）。

### 快速验证插件本身

```bash
node scripts/smoke.mjs
```

它会用一个假的 `ctx` 调 `apply()`，检查工具是否都注册上了（目前 9 个）、`apply` 不抛异常。
**不用启动 DSH 就能验证插件外壳。** 新增工具时记得同步 `scripts/smoke.mjs` 里的 `EXPECTED`。

### 只测 Python 侧

```bash
python py/cli.py doctor
python py/cli.py check <图> 
python py/cli.py scan <存档> --area x0,x1,z0,z1
```

---

## 报告 Bug

**不要提"跑不了"就完了。** 请带上：

1. **`python py/cli.py doctor` 的完整输出**
2. **你跑的命令**（完整，包括所有参数）
3. **完整报错原文**（不要截断、不要只贴最后一行）
4. **期望的行为** vs **实际的行为**
5. 如果能，**贴一张出问题的图**

**特别有价值的 Bug 报告**：带上**复现步骤**和**你排查过的方向**（哪怕没查出来）。
本项目的很多坑注释就是这么来的。

---

## 分支保护规则

维护者在 GitHub 上开启了这些保护（你可以核对自己遇到的行为）：

| 规则 | 效果 |
|---|---|
| `main` 禁止直接推送 | 只能通过 PR 进 |
| PR 需要至少 1 个 Approve | 没人看过的代码进不去 |
| PR 需要 CI 通过 | `doctor` + `smoke` 必须绿 |
| 需要 resolving conversation | 讨论没结束合不了 |
| 禁止 force push 到 `main` | 历史不会被改写 |

**这些规则对本项目的所有成员生效**（包括维护者，除了紧急修复）。

---

<div align="center">

**有疑问？** 开个 [Discussion](https://github.com/TreeCodeTyper/dsh-blender-mc/discussions) 或 Issue，别憋着。

[← 回 README](README.md) · [使用教程 →](docs/使用教程.md)

</div>
