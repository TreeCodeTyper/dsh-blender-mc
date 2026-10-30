# 新增参数契约（v0.5.0 开发中 · 唯一权威）

> 本文件是本次改动的**接口契约**。所有 Python / JS 分支必须严格按这里的名字实现，
> 不允许自行改名。改前先读这一页。

## 一、要交付的四件事（用户 m00001 原话：「背景和物品调用始终不行」）

1. **物品（items）**：`mc_shoot` 能直接指定左手 / 右手拿什么 —— 物品库 `.blend` 里有的就用库里的
   （376 件：`Sword: Diamond` 等），库里没有的（`crafting_table`、`anvil`、`furnace`…）
   用 `03_素材库/方块贴图/minecraft/block` 的原版贴图现场建带 UV 的方块。
2. **背景树林（trees）**：在人物身后按 MC 原版形状（树干 + 分层树冠）种 N 棵树，
   可调数量 / 距离 / 高度；不再是「一堆贴树叶的方块团」。
3. **天空与大气（hour / fog / clouds）**：早晨、正午、黄昏、夜晚可直接选；雾浓度可调；云层可去。
4. **渲染质量（贴图过滤 / 背景虚化 / 曝光）**：背景发糊、像二维码、黑屏要修掉。

## 二、环境变量（Python 侧唯一通道；build_cover.py 读）

| 变量 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `DSH_ITEM_R` | str | `Sword: Diamond` | 右手物品。可写物品库对象名（模糊匹配、忽略大小写/空格/下划线），也可写 MC 方块 id（`crafting_table`）或物品 id（`anvil`、`furnace`）。空字符串 = 空手 |
| `DSH_ITEM_L` | str | `""` | 左手物品，规则同上 |
| `DSH_TREES` | int | `0` | 人物身后种几棵树，0 = 不种 |
| `DSH_TREE_DIST` | str | `"7,20"` | 树的水平距离范围（格），`min,max` |
| `DSH_TREE_H` | str | `"4,7"` | 树干高度范围（格），`min,max` |
| `DSH_HOUR` | float | `8` | 时刻 0–24。=> 太阳仰角/色温/天空强度/雾色 |
| `DSH_FOG` | float | 沿用 `DSH_MC_FOG` | >0 是雾浓度倍率（1 = 基准）。`DSH_MC_FOG=0` 仍表示关闭 |
| `DSH_CLOUDS` | int | `1` | 0 = 隐藏 `CloudLayer` / `FogVolume` / 方块云 |
| `DSH_TEX_FILTER` | str | `nearest` | 世界贴图过滤：`nearest` / `linear` |
| `DSH_BG_BLUR` | float | `0` | >0 时给世界加背景虚化（DOF f-stop 换算），0 = 关 |
| `DSH_BPS_RIG` / `DSH_ITEM_RIG` | path | — | 已存在但**从未被接线**：必须真正决定骨架 / 物品库的加载路径；`DSH_ITEM_RIG` 可以是 `.blend` 文件，也可以是包含 `.blend` 的目录（自动挑第一个） |

`DSH_HOUR` 与旧变量 `DSH_SUN_ELEV` / `DSH_SUN_INT` / `DSH_SUN_ROT` 共存：显式设了旧变量就尊重旧变量。

## 三、CLI（py/cli.py · `shoot` 子命令）

```
--item-right TEXT     右手物品（同 DSH_ITEM_R）
--item-left  TEXT     左手物品（同 DSH_ITEM_L）
--items      TEXT     "R=anvil,L=crafting_table" 的简写；给了就覆盖上面两个
--trees      INT      身后树数量
--tree-dist  TEXT     "min,max"
--tree-h     TEXT     "min,max"
--hour       FLOAT    0-24 或 预设名 morning|noon|dusk|night
--fog        FLOAT    雾浓度倍率
--clouds     INT      1/0
```

全部可选、默认值 = 上表默认；不给就与旧行为**完全一致**（右手持钻石剑、无树、8 点）。

## 四、DSH 工具参数（lib/index.js · `mc_shoot`）

```ts
item?: string            // 右手，或 "R=anvil,L=crafting_table" 简写
items?: string[] | { right?: string; left?: string }   // 更显式的写法
trees?: number           // 身后树数量
hour?: number | 'morning' | 'noon' | 'dusk' | 'night'
fog?: number
clouds?: boolean
```

`mc_film` / `mc_anim` 这两个后续版本再接（本次只要 `mc_shoot` + `mc_scan`）。

## 五、装配链路（pipeline.py）

- `resolve_assets()` 的 `item` 必须返回**`.blend` 文件路径**（vendor/item/ 下面是目录，要递归找
  `*.blend`）。删掉 :145-179 那段重复代码。
- `step_render()` 必须把 `DSH_BPS_RIG` / `DSH_ITEM_RIG` 真正塞进 env（现在完全没设，
  所以 `build_cover.py` 只能吃它自己硬编码的 `PACK` 路径）。
- `build_cover.py` 里 `PACK` 那段硬编码开发者路径必须删掉，改成「环境变量 > vendor > 报错」。

## 六、验收（Lead 负责）

1. `mc_shoot(map="26.3 新世界", item="R=anvil,L=crafting_table", trees=3, hour=noon)` 出图，
   人物双手各拿一个原版方块、身后有 3 棵像样的树、天空是白天；
2. 不给任何新参数时，出的图和 v0.4.6 行为一致（右手持钻石剑）；
3. `python py/cli.py shoot ... --item-right "Sword: Iron"` 走物品库；
4. `mc_doctor` 仍全绿；`python -m compileall py` 无语法错误。

## 附加（v0.5.1）

| 环境变量 | 含义 |
| --- | --- |
| `DSH_ARMOR` | 盔甲：`leather|iron|gold|diamond|netherite`，或 `helmet=Netherite,chest=Gold`，`none` 关闭 |
| `DSH_ARMOR_YAW` | 盔甲模型朝向微调（度，默认 0） |

CLI：`--armor`；工具：`mc_shoot(armor=...)`。

## DSH_ARMOR 的三种写法（m01473 之后）

| 写法 | 例子 | 效果 |
| --- | --- | --- |
| 材质名 | `diamond` / `钻石` | 整套：头盔 + 胸甲 + 两只袖套 + 护腿 + 靴子 |
| 分部位 | `helmet=Netherite,chest=Gold,legs=Iron,boots=Gold` | 只穿点名的部位 |
| MC 物品 id | `minecraft:diamond_helmet,minecraft:diamond_chestplate,minecraft:diamond_leggings,minecraft:diamond_boots` | 只穿给的件（就写一件也行，如 `minecraft:iron_boots`） |

`none` / `off` / `0` = 不穿。材质可用：netherite / diamond / iron / gold / leather（chain 物品库里没有可穿戴模型，会被跳过）。
手持物品同样吃 id：`--item-right minecraft:diamond_sword`、`item="R=minecraft:anvil,L=minecraft:crafting_table"`。

## 染色（0.4.9）

皮革盔甲在游戏里靠染色，插件这边两种写法：

```
--armor "leather#8B4513"                      # 皮革染成鞍棕色
--armor "helmet=leather#ff8800,legs=iron"     # 只染头盔
--armor "diamond" --armor-color "#00ff00"     # 整身套一层绿（CLI: --armor-color）
DSH_ARMOR_COLOR=#8B4513                        # 环境变量形式（给 mc_shoot/mc_film 用）
```

- 颜色写在材质名后面（`leather#RGB` / `leather#RRGGBB`），也可以单独给 `#RRGGBB` 表示整身。
- 染色是「贴图 × 颜色」，会保留盔甲的明暗层次；`chain` / `netherite` 因为库里缺贴图，
  先用钻石的形状 + 去饱和 + 乘色。
