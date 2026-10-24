# 动画配方（presets/*.json）

这里每一个 `.json` 就是一支动画。**新增一支片子 = 在这里放一个 JSON，不用写 Python。**

调用：

```
python py/cli.py anim <封面工程.blend> --preset <名字> --out <输出目录> [--preview 1,60,120]
python py/cli.py film --plan 分镜.json --out <输出目录>     # 分镜里 preset 填名字
```

引擎是 `py/blender/anim_kit.py`：读配方 → `build()` → 逐段搭出来。`animate_recipe.py`
只是 40 行的壳，`animate_yujian.py` 也只剩转发（老调用仍可用）。

---

## 配方结构

```jsonc
{
  "name": "yujian",
  "title": "日志里显示的名字",
  "blend_name": "御剑飞天.blend",   // 产物工程名
  "fps": 24,
  "duration": 240,                  // 总帧数（10 秒 @24fps）

  "motion":  { ... },               // 必填：角色怎么动
  "poses":   { "骨骼名": [[帧, [x,y,z]], ...] },   // 姿态（度）
  "weapon":  { ... },               // 可选：武器/道具
  "fx":      [ { "type": "...", ... } ],           // 可选：特效列表
  "camera":  { ... },               // 必填：机位
  "lights":  { ... },               // 可选：布光跟随
  "render":  { ... }                // 必填：渲染设置
}
```

单位：角度用**度**，距离用**格**（1 格 = 1 方块），时间用**帧**。

### motion —— 运动形状

目前内置 `"type": "fly"`（沿一条水平方向飞，可带转身/前倾/侧倾）：

| 键 | 含义 |
|---|---|
| `az` | 飞行方位角（0 = +X，20 = 近似正东） |
| `lift` / `travel` | 升空高度 / 前移距离（格） |
| `progress` | `[起帧, 止帧]`，这段时间里完成全部位移 |
| `turn.amount` | 转身角度（180 = 掉头） |
| `turn.start/end` | 转身窗口 |
| `hop` / `pre` | 起跳抬升 / 起跳前的下蹲脉冲 |
| `tilt` | 前倾：`{scale: 11, terms: [[60,40,1.0],[198,34,-0.55]]}` = 11° × Σ权重·smoothstep |
| `bank` | 侧倾（绕前进轴），让剑面翻出来 |
| `key_step` | 每几帧打一次关键帧（2 最稳） |

`terms` 里每项是 `[起点帧, 时长, 权重]`：`权重 × smoothstep((f-起点)/时长)`。
`smoothstep` 保证首尾速度为 0 —— 也就是天然的缓入缓出。

### poses —— 姿态

骨骼名就是 BPS 骨架里的名字（`Torso` / `Head` / `Top Arm.R` / `Bottom Leg.L` …）。
值是欧拉角的**度**，按帧打关键帧。

> ⚠️ BPS 骨架的骨骼本地 **Z 轴是"往外张"**：Z 给多了手臂会横成 T 型僵尸。
> **前后摆用 X 轴**，左右才用 Z，且小角度。

### weapon —— 武器

`"type": "ridden_sword"`：把手里那把剑复制一把放大横在脚下当飞行器，原剑溶进光里。

| 键 | 含义 |
|---|---|
| `source` | 手持剑对象名前缀，默认 `"Sword"` |
| `scale` / `thick` | 放大倍数 / 沿刀面法线加厚（薄片均匀放大是"一根线"，必须单独加厚） |
| `desired` | 相对角色脚底的落位 `[x, y, z]` |
| `spawn_scale` / `spawn_z` | 出场：从地里浮起的过程 `[[帧, 值], ...]` |
| `hand_scale` | 原手持剑缩没 |
| `glow` / `edge` | 剑身自发光 / 刀脊亮条 |
| `flash` | 脱手瞬间的闪光 |

### fx —— 特效列表

按数组顺序执行，`type` 决定用哪个 builder：

| type | 作用 | 关键参数 |
|---|---|---|
| `rune_ring` | 地面符阵圆环（旋转+缩放） | `major/minor/z/color/strength/scale/spin` |
| `shockwave` | 起飞瞬间的冲击波 | `frames: [58,96,134]`，`scale` 里的帧是**相对**该帧的偏移 |
| `trail` | 剑尾光带（延迟采样轨迹） | `n/delay/start/radius/min/offset` |
| `streaks` | 高速风线 | `n/seed/dir/rad/zoff/len/start/travel` |
| `particles` | 螺旋粒子团 | `n/color/radius/spin/rise/start` |

### camera —— 机位

四条轨道（`[[帧, 值], ...]`）+ 几个常数：

```jsonc
"camera": {
  "az":   [[1, 200], [240, 158]],   // 方位角：从哪边看
  "dist": [[1, 6.4], [240, 10.5]],  // 距离
  "lens": [[1, 45],  [240, 68]],    // 焦距 mm
  "rise": [[1, 0.0], [240, 0.62]],  // 相机跟随升空的比例（0=贴地不动，1=完全跟着升）
  "z_base": 2.30,                   // 相对角色脚底的基准高度
  "aim_h": 1.00,                    // 瞄准胸口的高度
  "dof": 2.4,                       // 光圈（0 = 关掉景深）
  "key_step": 2
}
```

`film` 的多镜头就靠覆盖这一组：环境变量 `DSH_YJ_CAM_AZ_ADD` / `_D_SCALE` /
`_LENS_SCALE` / `_RISE_ADD` / `_Z_ADD` —— 同一套动作，不同景别，剪辑时可以直接对切。

### lights / render

```jsonc
"lights": { "key_step": 4, "fill": {"energy": 900, "size": 6, "forward": 2.2, "up": 1.2} },
"render": {
  "res": "960x540", "samples": 32, "exposure": 0.10, "motion_blur": 0.45,
  "view_transform": "AgX", "look": "AgX - Punchy",
  "sky": [[1, null], [70, 0.16], [150, 0.26]],   // null = 沿用工程原值
  "glare": {"type": "FOG_GLOW", "threshold": 1.0, "size": 7, "mix": -0.35},
  "hide_fog": true                                // 关掉封面工程的雾盒（升空后会把天空糊成灰）
}
```

## 环境变量覆盖（临时调参 / 分镜机位）

`DSH_YJ_AZ` `DSH_YJ_LIFT` `DSH_YJ_TRAVEL` `DSH_YJ_LEAN` `DSH_YJ_BANK`
`DSH_YJ_SWORD` `DSH_YJ_THICK` `DSH_YJ_LOCAL`
`DSH_YJ_RES` `DSH_YJ_SAMPLES` `DSH_YJ_FOG`
`DSH_YJ_CAM_AZ_ADD` `DSH_YJ_CAM_D_SCALE` `DSH_YJ_CAM_LENS_SCALE`
`DSH_YJ_CAM_RISE_ADD` `DSH_YJ_CAM_Z_ADD`

（名字里的 `YJ` 是历史包袱，对所有配方都生效。）

## 什么时候还得写代码

内核只认「运动形状 / 武器姿态 / 特效种类」三类可插拔件：

- `MOTIONS = {'fly': ...}` — 需要"绕柱子螺旋上升"这种全新运动 → 加一个函数
- `WEAPONS = {'ridden_sword': ...}` — 需要"双剑交叉"这种新载具 → 加一个函数
- `FX = {...}` — 需要全新视觉元素 → 加一个函数

**只要这三类够用，新动画就是纯配置。** 这三张表就是复用边界：要么用现成的组合，
要么往表里加一个函数（加完所有旧配方都不受影响）。
