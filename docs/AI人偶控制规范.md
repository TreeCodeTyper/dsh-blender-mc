# AI 人偶控制规范 v0.1

目标：**提示词 → CharacterSpec → AI 可操纵人偶**。

不是让模型直接写骨骼角度，而是让模型输出一份稳定的控制意图；由 rig 的 IK/约束去解算。
这样同一个提示词可以换 rig 执行，也不会因为模型“自由发挥”把骨骼转飞。

## 1. 坐标约定

- Z 向上，角色面向 **-Y**，角色左侧为 **+X**。
- 单位：Minecraft 格（1 格 ≈ 1 米）。
- `foot` / `hand` / `knee_hint` / `elbow_hint` 都是**世界坐标**；`pelvis.height` 是骨盆离地高度。
- `pitch` 绕 X（正数低头），`yaw` 绕 Z，`roll` 绕 Y。

## 2. CharacterSpec

```json
{
  "version": "0.1",
  "character": "ai-rig-v0",
  "action": "sit",
  "root": {"pos": [0, 0, 0], "yaw": 0},
  "pelvis": {"height": 0.15, "pitch": 0, "roll": 0, "yaw": 0},
  "legs": {
    "mode": "ik",
    "left":  {"foot": [0.18, -0.34, 0], "knee_hint": [0.20, -0.30, 0.55], "toe": 0},
    "right": {"foot": [-0.18, -0.62, 0], "knee_hint": [-0.20, -0.40, 0.22], "toe": 0}
  },
  "arms": {
    "mode": "ik",
    "left":  {"hand": [0.18, -0.38, 0.42], "elbow_hint": [0.42, -0.25, 0.45]},
    "right": {"hand": [-0.30, 0.25, 0.12], "elbow_hint": [-0.48, 0.10, 0.35]}
  },
  "spine": {"pitch": 6, "roll": 0, "yaw": 0},
  "head": {"pitch": 0, "yaw": 18, "roll": 8},
  "face": {"expression": "calm", "eye_open": 0.82, "brow": 0, "mouth": 0},
  "camera": {"framing": "9:16", "distance": 7, "azimuth": 20, "height": 0.95, "aim_height": 0.55, "lens": 60},
  "lighting": {"mood": "soft_side", "key": 1.0, "fill": 0.5},
  "source": "原始提示词",
  "notes": ["one knee up, one leg relaxed"]
}
```

### 字段说明

| 字段 | 含义 | 范围 |
|---|---|---|
| `action` | stand / sit / run / jump / wave / point | 枚举 |
| `pelvis.height` | 骨盆离地高度 | 0–1.4 |
| `legs.*.foot` | 脚 IK 目标 | 世界坐标 |
| `legs.*.knee_hint` | 膝盖极向提示 | 世界坐标 |
| `arms.*.hand` | 手 IK 目标 | 世界坐标 |
| `arms.*.elbow_hint` | 肘极向提示 | 世界坐标 |
| `spine/head` | 三轴旋转 | -90–90 |
| `face.expression` | neutral/happy/sad/angry/surprised/curious/calm | 枚举 |
| `face.eye_open` | 眼皮开合 | 0–1 |
| `face.brow` | 眉毛（负=压，正=抬） | -1–1 |
| `face.mouth` | 嘴角（负=下，正=上） | -1–1 |
| `camera.framing` | 9:16 / 16:9 / 1:1 / 4:5 | 枚举 |
| `camera.distance/azimuth/height/lens` | 机位 | 见校验 |

## 3. 提示词编译

`py/char_control.py` 是**确定性**编译器，不调用模型：

```bash
python py/char_control.py --prompt "坐在地上，一条腿弯，一条腿放松" --out spec.json
python py/char_control.py --spec spec.json --validate
```

它识别：

- 动作：sit / run / jump / wave / point / stand；
- 坐姿细节：one leg bent、cross-legged、盘腿；
- 表情：calm / happy / sad / angry / surprised / curious；
- 头部：head tilted、looking away、looking down；
- 画幅：9:16 / vertical、16:9、1:1；
- 光线：soft side light、haze/mist。

它不追求理解任意长文；复杂提示词由上层模型先产出 spec（同样是这个 JSON），再交给 `validate_spec()` 校验。
这样“模型理解”和“骨骼执行”解耦，执行层永远只看到合法数值。

## 4. 后端适配

新 `ai-rig` 和旧 BPS 都实现同一个函数：

```python
def apply_spec(arm, spec):
    """把 CharacterSpec 应用到骨架上；不返回渲染结果。"""
```

- `ai-rig`：标准骨名 `pelvis / thigh.L / shin.L / foot.L / upper_arm.L / forearm.L / hand.L / spine / head`，腿/手用 IK 目标，脊柱/头用 FK。
- `bps-temp`：临时适配器，只用于验证 spec；不作为最终人偶。
- 旧预设（run/jump/sit）逐步改成 spec 的预设，不再维护两套角度表。

## 5. 具身反馈（M3）

```text
observe()  ->  关节角、脚接触、COM、支撑多边形、碰撞距离、视线
plan()     ->  意图到目标关键点（脚放哪、手放哪、看向哪）
control()  ->  全身 IK / QP + 平衡约束 + 脚底接触约束
critique() ->  渲染后视觉/接触/平衡检查，不达标重解
```

第一版用基于模型的 IK/QP，不训练 RL；后续可接 motion matching / 扩散策略 / RL。

## 6. 当前状态

- ✅ `py/char_control.py`：提示词编译、spec 校验、CLI。
- ✅ 单测 `py/test_char_control.py`：editorial 提示词能编译成 sit / 9:16 / pelvis=0.15 / calm。
- ✅ `ai-rig` M1：程序化方块人、骨架、权重、解析 IK、`apply_spec()`；`mc_ai_char` 已接。
- ✅ M2 第一版：64×64 Steve/Alex 皮肤 UV 贴到全部身体块，脸直接来自皮肤；默认 rig 为 `AI_Character_Rig_v0.3.blend`。
- ⏳ 风格/皮肤/脸：M2。
- ⏳ observe/control/critique：M3。
- ✅ 公开参考声明：`THIRD_PARTY_NOTICES.md`（BPS/BSS/Simple/Mob/MiBlend/MCBlend 只参考思路，不复制资产/代码）。

## 7. 公开参考

本规范的骨骼组织、IK 目标、FK/IK 切换思路参考了 BPS Character Rig、Cycles Minecraft Rig
BSS Edit、Minecraft Simple Rig / Mob Rig 与 MiBlend / MCBlend 的公开工作流；
`ai_rig.py` 的网格、骨架、权重、`apply_spec()` 与 `char_control.py` 的 CharacterSpec
均为原创实现，不复制上述项目的资产或代码。详见 `THIRD_PARTY_NOTICES.md`。
