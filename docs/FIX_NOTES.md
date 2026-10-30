# dsh-blender-mc 修复说明（背景 / 物品）

针对「背景和物品调用始终不行」这次改动，按**根因**列，不按文件列。

## 一、为什么以前一直「没装骨架 / 没装物品库」

`py/pipeline.py`

1. `resolve_assets()` 早就算出了 rig / item，但 `step_render()` **从来没把它们传进
   Blender 进程**。`build_cover.py` 只能吃自己硬编码的开发者路径
   `PACK = E:\游戏与自媒体\AI自媒体工作区\pack`，换台机器就是「没装 BPS 角色骨架 → 只出风景空镜」。
   → 现在 `step_render()` 把 `DSH_BPS_RIG` / `DSH_ITEM_RIG` / `DSH_MC_TEXTURES` 真正塞进 env。
2. `resolve_assets()` 里 item 返回的是 **`vendor/item` 这个目录**，上层当文件路径用 →
   Blender 报 “Cannot read file .../vendor/item”。而 vendor 里其实躺着 `Minecraft_Item_Rig.blend`。
   → 新增 `pick_blend()`：目录一律递归收敛成真实存在的 `.blend`。
3. `vendor_dir()` 原来只认 `DSH_MC_VENDOR` / 插件根 `vendor`，**不认 DSH 数据目录**。
   直接跑 `python py/cli.py shoot` 时没人设这个环境变量，于是判定「什么都没装」。
   → 现在按 `DSH_MC_VENDOR → setup_assets.VENDOR → ~/.dsh/blender-mc/vendor → 插件根/vendor` 找，
   并把命中的目录写回 `DSH_MC_VENDOR`，让 `setup_assets.load_manifest()` 能读到
   manifest（`textures` / `ffmpeg` **只**记在 manifest 里，缺它就拿不到贴图库）。
4. `resolve_assets()` 里连着写了两遍一字不差的 vendor 扫描（`:145-179`），删掉一份。

## 二、为什么「背景一团黑 / 像二维码 / 一坨」

`py/blender/build_cover.py`

* **黑块根因**：带 alpha 的方块贴图（树叶、灌木、花草）材质里 `Principled.Alpha` 停在默认
  1.0 且没接贴图 alpha，EEVEE 把整块四边形当成**不透明黑块**画出来 —— 就是画面里那片
  跟方块对齐的黑色区域（原始 v0.4.6 也有，不是这次引入的）。
  → 新增一段：`Image.depth==32 or channels==4` 的材质把 `tex.Alpha → Principled.Alpha`，
  并 `surface_render_method='DITHERED'`。日志会打「★ 透明贴图 N 个材质已接 Alpha」。
* **糊 / 二维码感**：贴图采样默认线性过滤 + 远处放大。
  → 新增 `DSH_TEX_FILTER`（默认 `nearest`），把 44 个采样节点设成 `Closest`。
* **整片黑屏**：没有时段概念，夜里拿太阳参数渲。
  → 新增 `DSH_HOUR`（0-24，或 `morning|noon|dusk|night`），换算太阳仰角 / 灯光强度 / 色温 /
  天空强度；夜里断开物理天空、背景改深蓝并抬高天空强度，不再死黑。
* **「花错位 / 地上是透明的」**（`py/blender/export_obj.py`）：花、草、蘑菇、树苗这类 sprite
  方块原来也被当成【立方体】导出 —— 同一朵花贴在 6 个面上（正面一份、侧面一份、顶面还平摊
  在地上），Alpha 一镂空就露出这些重影；而且它作为实心方块会把脚下草地的顶面剔掉。
  → 现在按 MC 原版 **十字面片**（两块对角平面）导出，并且**不参与遮挡剔除**
  （剔除集合改用 `occl`，植物不在里面）；日志会打「其中 N 个植物方块按十字面片导」。
* **地面探测偶发打空**：原来只在世界包围盒 xy 中心竖直打 **一条** 射线，正巧落在方块边界
  或花丛十字面的交线上就会「没命中」，`GROUND` 退化成世界最低点 —— 相机被塞到地形底下，
  整张图几乎全空（渲染时间也会异常短）。
  → 改成 9×9 打 81 条取众数，日志「射线向下探测地面: 命中 N/81 条 → 地面 z=…」。

## 三、物品：左右手想拿什么就拿什么

`build_cover.py` 里的钻石剑那段整体换成通用「手持物品」：

* `DSH_ITEM_R` / `DSH_ITEM_L`：左右手各一个。
  * 先模糊匹配物品库（376 个对象，忽略大小写/空格/下划线），命中就用库里的模型
    （剑/斧/镐/盔甲…，保持 hero 举持姿势）；
  * 库里斯没有的（铁砧、工作台、熔炉…）→ 用**原版方块贴图**现场建带 UV 的立方体，
    边长 `DSH_BLOCK_SCALE`（默认 0.42 格）。
* 顺手修了两个把物品彻底卡死的 bug：
  * `KeyError: '+z'` —— 方块面名（`top/side/front`）和轴别名（`+z/-y`）没做映射，两只手都挂不上；
  * `NameError: sw` —— 挂完物品后的视线检测还在引用旧变量名。

## 四、树林：身后真的能长出树

* `DSH_TREES` / `DSH_TREE_DIST`(默认 8,45) / `DSH_TREE_H`(默认 4,7)。
* 树按 MC 原版形状建：橡木/白桦 = 一柱树干 + 5×5 去四角 / 3×3 / 顶盖；云杉 = 圆锥。
* 落点不是绕人物画圈（105mm 长焦水平视角只有 ~19°，绕圈等于把树种到画外），
  而是**从机位往画面里的像素点打射线**，落在能站树的地面才种；
  打到台地立面时，往面里退半格把树种到那一列的顶面（MC 里树就长在台地边上）。
* 射线只对世界网格打（`_ray_world`）—— 用 `scene.ray_cast` 会被 5000×5000 的云层平面挡住，
  法线朝下，120 条射线 105 条被误判成「坡壁」，一棵都种不下。

## 四点五、取景：不再把镜头怼到太阳上

视线方位自动选择（`_eye_azimuth_pick`）原来给「正对太阳」+34 分，实测会把镜头扭到逆光，
天空过曝成白板、人物变剪影（审图直接报 `hot_ratio=0.88`）。改成：

* 与太阳夹角 < 18° → **-20 分**（逆光惩罚）；
* 夹角 ≈ 55°（侧逆光）→ **+12 分**（原有的话最漂亮的轮廓光）；
* 换方位的门槛从「高 4 分」提到「高 12 分」，避免地面高度只差半格就整个构图翻转。

想要固定机位就设 `DSH_MC_AUTO_AZ=0`。

## 五、新参数怎么用

工具 `mc_shoot`：

```
mc_shoot(map="26.3 新世界", items="R=anvil,L=crafting_table", trees=3, hour="noon")
```

`item` 的四种写法：

```
item="anvil"                      // 只给右手
item="R=anvil,L=crafting_table"   // 左右手一起给
item=""                           // 明确空手
items={ right: "anvil" }          // 对象写法
```

其余：`trees`(0-100) / `hour`(数字或 morning|noon|dusk|night) / `fog`(≥0，0=关雾) / `clouds`(true/false)。

CLI 等价：

```
python py/cli.py shoot <存档> --area x0,x1,z0,z1 --out ./出图 ^
  --item-right anvil --item-left crafting_table --trees 3 --hour 12
```

**不传任何新参数时，行为与 v0.4.6 完全一致**（右手钻石剑、无树、原时段参数）。

## 六、debug 开关

* `DSH_DEBUG_TREES=1`：打印每个候选视线方位的射线统计（打空 / 坡壁 / 距离 / 太挤 / 无地面 / 被挡）。

---

## 五、穿盔甲（v0.5.1 新增 `armor`）

以前只能「拿」物品，穿不了。现在 `mc_shoot` / `py/cli.py shoot` 都有 `armor`：

| 写法 | 效果 |
| --- | --- |
| `--armor diamond`（或 `armor: "diamond"`） | **整套**：头盔 + 胸甲 + 两只袖套 + 护腿 + 靴子 |
| `--armor helmet=Netherite,chest=Gold` | 分开指定 |
| `--armor none` | 不穿（等同不传） |

可用材质：`leather` / `iron` / `gold` / `diamond` / `netherite`（`chain` 只有扁平图标，穿不了，会自动跳过并提示）。

做法：物品库（`Minecraft_Item_Rig.blend`）里本来就有可穿的 3D 件，按 **parent_type='BONE'**
挂到 BPS 骨架的 `Head` / `Torso` / `Top Arm.L|R` / `Top Leg.L|R` / `Bottom Leg.L|R` 骨上：

| 部位 | 物品库对象 | 骨骼 |
| --- | --- | --- |
| 头盔 | `Helmet:<材质>` | `Head` |
| 胸甲 | `Chestplate:<材质>` | `Torso` |
| 袖套 | `Chestplate:<材质>-Arm.L|R` | `Top Arm.L|R` |
| 护腿 | `Leg.<材质>.L|R`（netherite 是 `NetheritelegunderL|R`） | `Top Leg.L|R` |
| 靴子 | `Boot.<材质>.L|R` | `Bottom Leg.L|R` |

只有 diamond 全五种部位都有自己的 3D 件；netherite 的头/胸/袖是 `.001` 后缀的同名件，
护腿叫 `Netheritelegunder*`；iron / gold / leather 的腿和靴子**借用 diamond 的形状 + 换一张 armor layer 贴图**
（腿靴的 UV 布局跟材质无关，`diamond_2.002` → `iron_2.002` / `leather_2.002` / `Armour:Gold2.002` /
`netherite_layer_2.png` 就是对应材质）。

三层兜底（都写进日志，出问题能一眼看出来）：
1. **贴图按需加载**：armor layer 的 image 不会跟着对象自动 append（只有被对象引用的才会），
   所以 `_ensure_image()` 会显式从物品库把 image 数据块拉一遍 —— 不然 iron 套会穿着钻石裤子（实测 bug）。
2. **贴图丢了 → 纯色**：`Helmet/Chestplate:Netherite` 引用的贴图在物品库里是空壳（size 0×0），
   Blender 会画成洋红 → `_patch_broken_mats()` 换成对应材质的纯色（下界合金 = 深灰）。
3. **皮革贴图是灰模**：MC 皮革盔甲靠染色，直接贴是灰的 → `_tint_mats()` 给 Base Color 乘上皮革色。

没有 3D 件也没有 layer 图的材质（`chain`）就不穿腿，日志里会写明「跳过靴子/护腿」。

---
