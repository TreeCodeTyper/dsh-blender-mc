# -*- coding: utf-8 -*-
"""自带模型（BYO models）的入参解析

插件本身**不附带任何模型或贴图**：模型来自使用者自己的资源包 / 模组 / 自制资产。
这里只做一件事 —— 把"从哪读、放哪、多大、朝哪"解析成统一结构，交给 Blender 侧摆放。

两种输入方式
------------
1) 描述文件（推荐，数量不限）
       DSH_MODELS=/path/models.json
   内容是一个数组：
       [{"name": "木桶", "obj": "/a/barrel.obj", "tex": "/a/barrel.png",
         "scale": 1.0, "back": 4.0, "side": 0.0, "z": 0.0, "yaw": 0.0}, ...]
   也接受直接把这段 JSON 写在环境变量里。

2) 分槽位环境变量（最多 3 个）
       DSH_MODEL1_OBJ / _TEX / _SCALE / _BACK / _SIDE / _Z / _YAW / _NAME
   i=1 时也接受不带数字的写法：DSH_MODEL_OBJ ...
   旧名 DSH_POKE_* 仍然可用，但会在日志里提示迁移。

坐标约定
--------
back / side / z 都是相对"机位中心 + 地面"的偏移，单位是方块；yaw 是绕 Z 轴的朝向角度。
全是可选，缺省时模型站在机位中心、贴地、正对相机。
"""
import json
import os

# 槽位默认值 —— 和视觉检查无关，只影响摆放
DEFAULTS = {
    "scale": 1.0,   # 缩放（OBJ 已经是方块单位时用 1.0）
    "back": 4.0,    # 在机位中心"后方"多少格（= 离相机更远）
    "side": 0.0,    # 侧向偏移，正数偏右（相对视线）
    "z": 0.0,       # 抬高/下压，0 = 底面贴地
    "yaw": 0.0,     # 朝向角度
}

MAX_SLOTS = 3


def _num(raw, key, default):
    """环境变量里的数字：写错了不崩，退回默认值。"""
    v = raw.get(key)
    if v is None or str(v).strip() == "":
        return float(default)
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _slot(env, prefixes, fallback_name):
    """按前缀顺序找一组 *_OBJ / *_TEX / ...；都没有 OBJ 就返回 None。"""
    for pre in prefixes:
        obj = (env.get(pre + "OBJ") or "").strip()
        if not obj:
            continue
        tex = (env.get(pre + "TEX") or "").strip()
        name = (env.get(pre + "NAME") or "").strip() or fallback_name
        return {
            "name": name,
            "obj": obj,
            "tex": tex,
            "scale": _num(env, pre + "SCALE", DEFAULTS["scale"]),
            "back": _num(env, pre + "BACK", DEFAULTS["back"]),
            "side": _num(env, pre + "SIDE", DEFAULTS["side"]),
            "z": _num(env, pre + "Z", DEFAULTS["z"]),
            "yaw": _num(env, pre + "YAW", DEFAULTS["yaw"]),
        }
    return None


def _normalize(raw, base_dir, index):
    """把描述文件里的一个对象补全成统一结构。"""
    if not isinstance(raw, dict):
        return None, f"第 {index} 项不是对象，已跳过"
    obj = str(raw.get("obj") or "").strip()
    if not obj:
        return None, f"第 {index} 项没有 obj，已跳过"
    if base_dir and not os.path.isabs(obj):
        obj = os.path.normpath(os.path.join(base_dir, obj))
    tex = str(raw.get("tex") or "").strip()
    if tex and base_dir and not os.path.isabs(tex):
        tex = os.path.normpath(os.path.join(base_dir, tex))
    name = str(raw.get("name") or "").strip() or f"model{index}"

    def num(k):
        v = raw.get(k)
        if v is None or v == "":
            return float(DEFAULTS[k])
        try:
            return float(v)
        except (TypeError, ValueError):
            return float(DEFAULTS[k])

    return {
        "name": name,
        "obj": obj,
        "tex": tex,
        "scale": num("scale"),
        "back": num("back"),
        "side": num("side"),
        "z": num("z"),
        "yaw": num("yaw"),
    }, None


def _from_spec(spec):
    """DSH_MODELS：文件路径 或 内联 JSON。返回 (列表, 基准目录, 备注)。"""
    notes = []
    text = spec.strip()
    base_dir = None
    if text[:1] not in ("[", "{"):
        if not os.path.isfile(text):
            return [], None, [f"DSH_MODELS 指向的文件不存在: {text}"]
        base_dir = os.path.dirname(os.path.abspath(text))
        with open(text, "r", encoding="utf-8") as f:
            text = f.read()
    try:
        data = json.loads(text)
    except Exception as e:
        return [], base_dir, [f"DSH_MODELS 不是合法 JSON: {e}"]
    if isinstance(data, dict):
        data = data.get("models") or [data]
    if not isinstance(data, list):
        return [], base_dir, ["DSH_MODELS 应该是数组（或 {\"models\": [...]}）"]
    models = []
    for i, item in enumerate(data, 1):
        m, note = _normalize(item, base_dir, i)
        if note:
            notes.append(note)
        if m:
            models.append(m)
    return models, base_dir, notes


def collect(env=None):
    """解析当前环境，返回 (models, notes)。

    models: [{'name','obj','tex','scale','back','side','z','yaw'}, ...]
    notes:  ['旧名 DSH_POKE_* 已改名 …', ...] —— 给调用方打印，不抛异常。
    """
    env = os.environ if env is None else env
    notes = []

    spec = (env.get("DSH_MODELS") or "").strip()
    if spec:
        models, _base, spec_notes = _from_spec(spec)
        notes.extend(spec_notes)
        if models:
            for m in models:
                if not os.path.isfile(m["obj"]):
                    notes.append(f"模型 {m['name']} 的 OBJ 不存在: {m['obj']}")
            return models, notes
        notes.append("DSH_MODELS 没解析出可用模型，回退到分槽位环境变量")

    models = []
    for i in range(1, MAX_SLOTS + 1):
        if i == 1:
            prefixes = ["DSH_MODEL1_", "DSH_MODEL_", "DSH_POKE_", "DSH_POKE1_"]
        else:
            prefixes = [f"DSH_MODEL{i}_", f"DSH_POKE{i}_"]
        m = _slot(env, prefixes, f"model{i}")
        if not m:
            continue
        used = next(p for p in prefixes if (env.get(p + "OBJ") or "").strip())
        if used.startswith("DSH_POKE"):
            notes.append(
                f"模型 {i} 用的是 v0.1.x 的旧环境变量前缀 —— 已统一为 DSH_MODEL，"
                f"旧前缀下个主版本移除")
        if not os.path.isfile(m["obj"]):
            notes.append(f"模型 {m['name']} 的 OBJ 不存在: {m['obj']}")
        models.append(m)
    return models, notes
