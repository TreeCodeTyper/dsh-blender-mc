"""AI 人偶控制层（v0.1）。

这一层不绑定具体 rig：它只做两件事。
1. 把自然语言提示词编译成稳定的 CharacterSpec（JSON）。
2. 校验/规范化 spec，交给后端适配器去驱动骨骼。

设计原则：
- AI 不直接写骨骼角度；只给 root / pelvis / 脚 IK / 手 IK / 脊柱 / 头 / 脸 的目标。
- 坐标：Z 向上，角色面向 -Y，角色左侧为 +X，单位是 Minecraft 格（1 格 ≈ 1 米）。
- 所有字段都是可选的，缺省值见 DEFAULT_SPEC。
- 提示词解析是确定性的关键词提取，不调用模型；模型/agent 可以直接给 spec，
  这里负责校验，避免"模型自由发挥"导致 rig 崩掉。

用法：
    python py/char_control.py --prompt "坐在地上，一条腿弯，一条腿放松" --out spec.json
    python py/char_control.py --spec spec.json --validate
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "0.1"

DEFAULT_SPEC: dict[str, Any] = {
    "version": SCHEMA_VERSION,
    "character": "ai-rig-v0",
    "action": "stand",
    "root": {"pos": [0.0, 0.0, 0.0], "yaw": 0.0},
    "pelvis": {"height": 1.0, "pitch": 0.0, "roll": 0.0, "yaw": 0.0},
    "legs": {
        "mode": "ik",
        "left": {
            "foot": [0.18, -0.05, 0.0],
            "knee_hint": [0.20, -0.12, 0.45],
            "toe": 0.0,
            "straight": False,
        },
        "right": {
            "foot": [-0.18, -0.05, 0.0],
            "knee_hint": [-0.20, -0.12, 0.45],
            "toe": 0.0,
            "straight": False,
        },
    },
    "arms": {
        "mode": "ik",
        "left": {"hand": [0.28, -0.12, 0.95], "elbow_hint": [0.42, -0.05, 1.05]},
        "right": {"hand": [-0.28, -0.12, 0.95], "elbow_hint": [-0.42, -0.05, 1.05]},
    },
    "spine": {"pitch": 0.0, "roll": 0.0, "yaw": 0.0},
    "head": {"pitch": 0.0, "yaw": 0.0, "roll": 0.0},
    "face": {
        "expression": "neutral",
        "eye_open": 1.0,
        "brow": 0.0,
        "mouth": 0.0,
    },
    "camera": {
        "framing": "9:16",
        "distance": 8.0,
        "azimuth": 20.0,
        "height": 1.05,
        "aim_height": 0.70,
        "lens": 60.0,
    },
    "lighting": {"mood": "soft_side", "key": 1.0, "fill": 0.35},
    "source": "",
    "notes": [],
}

# 关键词 -> 动作。顺序很重要：sit 优先于 stand。
_ACTION_WORDS = (
    ("sit", ("sit", "sitting", "seated", "坐", "坐下", "盘腿")),
    ("run", ("run", "running", "跑", "奔跑")),
    ("jump", ("jump", "jumping", "跳", "跳跃")),
    ("wave", ("wave", "waving", "挥手", "招手")),
    ("point", ("point", "pointing", "指向", "指")),
    ("stand", ("stand", "standing", "站", "站立")),
)

_EXPRESSIONS = (
    ("happy", ("happy", "smile", "smiling", "笑", "微笑", "愉快")),
    ("sad", ("sad", "难过", "悲伤", "低落")),
    ("angry", ("angry", "生气", "愤怒")),
    ("surprised", ("surprised", "惊讶", "吃惊")),
    ("curious", ("curious", "好奇", "挑眉")),
    ("calm", ("calm", "平静", "安静", "distant", "淡然")),
)


def _has(text: str, words: tuple[str, ...]) -> bool:
    return any(w in text for w in words)


def _set_legs_sit(spec: dict[str, Any], text: str) -> None:
    """坐姿：默认一膝立起 + 一条腿前伸；可识别盘腿/双腿前伸。"""
    spec["action"] = "sit"
    spec["pelvis"]["height"] = 0.15
    spec["spine"]["pitch"] = 6.0
    spec["camera"]["height"] = 0.95
    spec["camera"]["aim_height"] = 0.70
    spec["camera"]["distance"] = 7.0
    spec["camera"]["lens"] = 70.0

    legs = spec["legs"]
    # 盘腿：两只脚都收到身体前方中线附近。
    if _has(text, ("cross-legged", "cross legged", "盘腿", "cross")):
        legs["left"]["foot"] = [0.16, -0.28, 0.0]
        legs["left"]["knee_hint"] = [0.30, -0.10, 0.30]
        legs["right"]["foot"] = [-0.16, -0.28, 0.0]
        legs["right"]["knee_hint"] = [-0.30, -0.10, 0.30]
        spec["notes"].append("cross-legged sit")
        return

    # 一膝立起：左膝抬起，右腿前伸放松。
    legs["left"]["foot"] = [0.18, -0.34, 0.0]
    legs["left"]["knee_hint"] = [0.20, -0.30, 0.55]
    legs["right"]["foot"] = [-0.18, -0.62, 0.0]
    legs["right"]["knee_hint"] = [-0.20, -0.40, 0.22]
    spec["notes"].append("one knee up, one leg relaxed")

    # 手：左手搭在立起的膝盖上，右手撑在身后地面。
    spec["arms"]["left"]["hand"] = [0.38, -0.38, 0.50]
    spec["arms"]["left"]["elbow_hint"] = [0.55, -0.20, 0.95]
    spec["arms"]["right"]["hand"] = [-0.32, 0.22, 0.12]
    spec["arms"]["right"]["elbow_hint"] = [-0.55, 0.10, 0.85]


def _set_legs_stand(spec: dict[str, Any]) -> None:
    spec["pelvis"]["height"] = 1.0
    spec["spine"]["pitch"] = 0.0


def _set_face(spec: dict[str, Any], text: str) -> None:
    for name, words in _EXPRESSIONS:
        if _has(text, words):
            spec["face"]["expression"] = name
            break
    if _has(text, ("calm", "平静", "淡然", "distant")):
        spec["face"]["eye_open"] = 0.82
    if _has(text, ("smile", "微笑", "笑")):
        spec["face"]["mouth"] = 0.35
    if _has(text, ("sad", "难过", "悲伤")):
        spec["face"]["mouth"] = -0.35
    if _has(text, ("surprised", "惊讶")):
        spec["face"]["eye_open"] = 1.0
        spec["face"]["mouth"] = 0.5
    if _has(text, ("angry", "生气", "愤怒")):
        spec["face"]["brow"] = -0.5
        spec["face"]["mouth"] = -0.4
    if _has(text, ("curious", "好奇", "挑眉")):
        spec["face"]["brow"] = 0.4


def _set_head(spec: dict[str, Any], text: str) -> None:
    if _has(text, ("head tilted", "head tilt", "歪头", "头倾斜", "tilted head")):
        spec["head"]["roll"] = 8.0
        spec["head"]["yaw"] = 10.0
    if _has(text, ("looking away", "看向别处", "视线", "distant")):
        spec["head"]["yaw"] = 18.0
    if _has(text, ("looking down", "低头")):
        spec["head"]["pitch"] = -8.0


def _set_camera(spec: dict[str, Any], text: str) -> None:
    if "9:16" in text or "vertical" in text:
        spec["camera"]["framing"] = "9:16"
    if "16:9" in text or "landscape" in text:
        spec["camera"]["framing"] = "16:9"
    if "1:1" in text or "square" in text:
        spec["camera"]["framing"] = "1:1"
    if _has(text, ("side light", "侧光", "soft side")):
        spec["lighting"]["mood"] = "soft_side"
    if _has(text, ("haze", "mist", "雾", "柔光")):
        spec["lighting"]["fill"] = 0.5
        spec["notes"].append("soft haze")


def compile_prompt(prompt: str, character: str = "ai-rig-v0") -> dict[str, Any]:
    """把提示词编译成 CharacterSpec；不调用模型，结果可复现。"""
    text = re.sub(r"\s+", " ", (prompt or "").strip().lower())
    spec = deepcopy(DEFAULT_SPEC)
    spec["character"] = character
    spec["source"] = prompt or ""

    action = "stand"
    for name, words in _ACTION_WORDS:
        if _has(text, words):
            action = name
            break
    spec["action"] = action

    if action == "sit":
        _set_legs_sit(spec, text)
    else:
        _set_legs_stand(spec)
        if action == "run":
            spec["spine"]["pitch"] = 16.0
            spec["head"]["pitch"] = -6.0
            spec["camera"]["azimuth"] = 62.0
            spec["legs"]["left"]["foot"] = [0.18, -0.46, 0.08]
            spec["legs"]["left"]["knee_hint"] = [0.20, -0.50, 0.60]
            spec["legs"]["right"]["foot"] = [-0.18, 0.34, 0.12]
            spec["legs"]["right"]["knee_hint"] = [-0.20, 0.38, 0.55]
            spec["arms"]["left"]["hand"] = [0.38, -0.40, 1.05]
            spec["arms"]["left"]["elbow_hint"] = [0.55, -0.30, 1.20]
            spec["arms"]["right"]["hand"] = [-0.38, 0.36, 1.00]
            spec["arms"]["right"]["elbow_hint"] = [-0.55, 0.28, 1.15]
            spec["notes"].append("run: stride + arm swing")
        elif action == "jump":
            spec["pelvis"]["height"] = 1.2
            spec["spine"]["pitch"] = 4.0
            spec["camera"]["height"] = 1.2
            spec["camera"]["aim_height"] = 0.9
            spec["legs"]["left"]["foot"] = [0.22, 0.02, 0.35]
            spec["legs"]["left"]["knee_hint"] = [0.22, 0.02, 0.70]
            spec["legs"]["left"]["straight"] = True
            spec["legs"]["right"]["foot"] = [-0.22, 0.02, 0.35]
            spec["legs"]["right"]["knee_hint"] = [-0.22, 0.02, 0.70]
            spec["legs"]["right"]["straight"] = True
            spec["arms"]["left"]["hand"] = [0.40, -0.18, 1.32]
            spec["arms"]["right"]["hand"] = [-0.40, -0.18, 1.32]
            spec["notes"].append("jump: pelvis up, both feet off ground")
        elif action == "wave":
            spec["arms"]["left"]["hand"] = [0.45, -0.25, 1.88]
            spec["arms"]["left"]["elbow_hint"] = [0.55, -0.08, 1.45]
        elif action == "point":
            spec["arms"]["left"]["hand"] = [0.30, -0.75, 1.15]

    _set_face(spec, text)
    _set_head(spec, text)
    _set_camera(spec, text)
    # ★ R07：外观词（发色/服装/武器）本来就【没有】消费方 —— 这个 rig 只有
    #   Steve/Alex 两张皮肤，长袍长什么样是皮肤贴图决定的。以前这些词被
    #   静默吞掉，用户看到「银发女剑客」却出 Steve 完全不知道为什么。
    _APPEAR = ('发', '袍', '外套', '盔甲', '甲胄', '衣', '裙', '靴', '帽', '斗篷',
               '红', '蓝', '金', '银', '紫', '绿', '黑', '白', '灰', '棕',
               '剑', '盾', '弓', '法杖', '少年', '少女', '女', '男')
    _hit = [w for w in _APPEAR if w in text]
    if _hit:
        spec.setdefault('warnings', []).append(
            '外观词不生效：%s —— 本 rig 只有 steve/alex 两张皮肤，'
            '发色/服装/武器由皮肤贴图决定，请用 --skin <64x64 PNG> 指定'
            % '、'.join(_hit[:6]))
    return normalize_camera(spec)



def normalize_camera(spec: dict[str, Any]) -> dict[str, Any]:
    """按画幅把相机参数写进 spec，确保 spec 就是最终相机。"""
    c = spec.get('camera', {})
    if c.get('framing') == '16:9':
        c['distance'] = max(float(c.get('distance', 8.0)), 11.0)
        c['lens'] = min(float(c.get('lens', 60.0)), 55.0)
        c['height'] = max(float(c.get('height', 1.0)), 1.15)
        c['aim_height'] = max(float(c.get('aim_height', 0.7)), 0.85)
    return spec


def _num(value: Any, name: str, errors: list[str], lo: float, hi: float) -> None:
    if not isinstance(value, (int, float)):
        errors.append(f"{name} 必须是数字")
    elif not (lo <= float(value) <= hi):
        errors.append(f"{name} 超出范围 [{lo}, {hi}]：{value}")


def _vec3(value: Any, name: str, errors: list[str]) -> None:
    if not (isinstance(value, (list, tuple)) and len(value) == 3):
        errors.append(f"{name} 必须是 [x, y, z]")
        return
    for i, v in enumerate(value):
        if not isinstance(v, (int, float)):
            errors.append(f"{name}[{i}] 必须是数字")


def validate_spec(spec: dict[str, Any]) -> list[str]:
    """返回错误列表；空列表表示可以执行。"""
    errors: list[str] = []
    if not isinstance(spec, dict):
        return ["spec 必须是对象"]
    if spec.get("version") != SCHEMA_VERSION:
        errors.append(f"version 必须是 {SCHEMA_VERSION}")
    if not isinstance(spec.get("character"), str) or not spec["character"]:
        errors.append("character 必须是非空字符串")
    if not isinstance(spec.get("action"), str):
        errors.append("action 必须是字符串")

    root = spec.get("root", {})
    _vec3(root.get("pos"), "root.pos", errors)
    _num(root.get("yaw", 0), "root.yaw", errors, -360, 360)

    pelvis = spec.get("pelvis", {})
    _num(pelvis.get("height"), "pelvis.height", errors, 0.0, 1.4)
    for k in ("pitch", "roll", "yaw"):
        _num(pelvis.get(k, 0), f"pelvis.{k}", errors, -90, 90)

    legs = spec.get("legs", {})
    if legs.get("mode") not in ("ik", "fk"):
        errors.append("legs.mode 必须是 ik/fk")
    for side in ("left", "right"):
        leg = legs.get(side, {})
        _vec3(leg.get("foot"), f"legs.{side}.foot", errors)
        _vec3(leg.get("knee_hint", [0, 0, 0]), f"legs.{side}.knee_hint", errors)
        _num(leg.get("toe", 0), f"legs.{side}.toe", errors, -45, 45)

    arms = spec.get("arms", {})
    if arms.get("mode") not in ("ik", "fk"):
        errors.append("arms.mode 必须是 ik/fk")
    for side in ("left", "right"):
        arm = arms.get(side, {})
        _vec3(arm.get("hand"), f"arms.{side}.hand", errors)
        _vec3(arm.get("elbow_hint", [0, 0, 0]), f"arms.{side}.elbow_hint", errors)

    for group in ("spine", "head"):
        for k in ("pitch", "roll", "yaw"):
            _num(spec.get(group, {}).get(k, 0), f"{group}.{k}", errors, -90, 90)

    face = spec.get("face", {})
    if not isinstance(face.get("expression"), str):
        errors.append("face.expression 必须是字符串")
    _num(face.get("eye_open", 1), "face.eye_open", errors, 0, 1)
    _num(face.get("brow", 0), "face.brow", errors, -1, 1)
    _num(face.get("mouth", 0), "face.mouth", errors, -1, 1)

    cam = spec.get("camera", {})
    if cam.get("framing") not in ("9:16", "16:9", "1:1", "4:5"):
        errors.append("camera.framing 必须是 9:16/16:9/1:1/4:5")
    _num(cam.get("distance", 8), "camera.distance", errors, 2, 40)
    _num(cam.get("azimuth", 0), "camera.azimuth", errors, -360, 360)
    _num(cam.get("height", 1), "camera.height", errors, 0, 6)
    _num(cam.get("aim_height", 0.7), "camera.aim_height", errors, 0, 6)
    _num(cam.get("lens", 60), "camera.lens", errors, 18, 200)

    light = spec.get("lighting", {})
    if not isinstance(light.get("mood"), str):
        errors.append("lighting.mood 必须是字符串")
    _num(light.get("key", 1), "lighting.key", errors, 0, 5)
    _num(light.get("fill", 0.3), "lighting.fill", errors, 0, 5)
    return errors


def summarize(spec: dict[str, Any]) -> str:
    a = spec.get("action", "?")
    p = spec.get("pelvis", {}).get("height", "?")
    f = spec.get("face", {}).get("expression", "?")
    c = spec.get("camera", {})
    return (
        f"{spec.get('character','?')} / {a} / pelvis={p} / face={f} / "
        f"{c.get('framing','?')} dist={c.get('distance','?')} "
        f"az={c.get('azimuth','?')}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="提示词 -> AI 人偶控制 spec")
    ap.add_argument("--prompt", help="自然语言提示词")
    ap.add_argument("--spec", help="读取现有 spec JSON 做校验")
    ap.add_argument("--out", help="输出 spec JSON")
    ap.add_argument("--character", default="ai-rig-v0")
    ap.add_argument("--validate", action="store_true", help="只校验")
    ap.add_argument("--json", action="store_true", help="stdout 输出 JSON")
    args = ap.parse_args(argv)

    if args.spec:
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    else:
        if not args.prompt:
            ap.error("需要 --prompt 或 --spec")
        spec = compile_prompt(args.prompt, character=args.character)

    errors = validate_spec(spec)
    if errors:
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.out:
        Path(args.out).write_text(
            json.dumps(spec, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"spec -> {args.out}")
    if args.json or not args.out:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
    else:
        print(summarize(spec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
