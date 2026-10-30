"""fx_kit —— Minecraft 风格特效模块（Blender 5.2 LTS）

独立模块，不依赖 anim_kit / pipeline，只 import bpy / math / mathutils / random
（标准库 os、time 仅用于自测入口）。

接入方式
--------
    from fx_kit import FX_REGISTRY
    ctx = {
        'arm':   arm_ob,                      # 角色骨架（可 None）
        'total': 240,                         # 镜头总帧数
        'fps':   24,
        'base':  Vector((0, 0, 0)),           # 角色脚底位置
        'FV':    Vector((0, -1, 0)),          # 角色前向
        'UP':    Vector((0, 0, 1)),           # 上方向
    }
    made = FX_REGISTRY['embers'](scene, ctx, {'n': 90})   # 返回新建对象列表

约定
----
* 每个特效签名统一 ``fx_xxx(scene, ctx, spec=None)``，自己建几何/材质、打关键帧，
  返回新建对象列表（含灯与空物体）。
* 所有对象、材质名以 ``fx_<效果>_`` 开头，``cleanup_fx()`` 可一键隐藏或删除。
* spec 缺省值见各函数 docstring 与 ``FX_DEFAULTS``；文档见 docs/特效模块.md。
* Blender 5.2 的 ``Action`` 没有 ``.fcurves``，统一走 ``_fcurves()`` 兼容层
  （layers -> strips -> channelbags）。
* 自发光一律用 Principled BSDF 的 ``Emission Color`` + ``Emission Strength``。
* 自测：``FX_SELFTEST=1 FX_OUT=<目录> blender -b --python fx_kit.py``
"""

import math
import os
import random
import time

import bpy
from mathutils import Quaternion, Vector

FX_VERSION = '1.1'
FX_PREFIX = 'fx_'


# ---------------------------------------------------------------- 小工具

def p(*args):
    """自测用的 print（只接受 0~1 个参数，需要格式化请自己先拼字符串）。"""
    if args:
        print(str(args[0]))
    else:
        print('')


def _pfx(effect, suffix):
    return '%s%s_%s' % (FX_PREFIX, effect, suffix)


def _v3(v, default=(0.0, 0.0, 0.0)):
    if v is None:
        return Vector(default)
    if isinstance(v, Vector):
        return v.copy()
    if len(v) >= 3:
        return Vector((float(v[0]), float(v[1]), float(v[2])))
    return Vector(default)


def _unit(v, default=(0.0, 0.0, 1.0)):
    vec = _v3(v, default)
    if vec.length < 1e-8:
        return Vector(default)
    return vec.normalized()


def lerp(a, b, t):
    return a + (b - a) * t


def smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def ramp(spec, key, default, lo=0.0):
    """从 spec 取 (小, 大) 范围；单值也能用。"""
    v = spec.get(key, default)
    if isinstance(v, (int, float)):
        return (float(v), float(v))
    try:
        return (float(v[0]), float(v[1]))
    except Exception:
        return (float(default[0]), float(default[1]))


def color(v, default=(1.0, 1.0, 1.0)):
    if v is None:
        return Vector(default)
    if isinstance(v, str):
        return _NAMED.get(v.lower(), Vector(default)).copy()
    if len(v) == 4:
        return Vector((float(v[0]), float(v[1]), float(v[2])))
    if len(v) == 3:
        return Vector((float(v[0]), float(v[1]), float(v[2])))
    return Vector(default)


_NAMED = {
    'cyan': (0.35, 0.95, 1.00),
    'ice': (0.55, 0.85, 1.00),
    'amber': (1.00, 0.34, 0.03),
    'fire': (1.00, 0.55, 0.12),
    'white': (1.00, 1.00, 1.00),
    'gold': (1.00, 0.78, 0.28),
    'violet': (0.58, 0.32, 1.00),
    'purple': (0.70, 0.35, 1.00),
    'green': (0.35, 1.00, 0.55),
    'water': (0.72, 0.90, 1.00),
}


def _ctx(scene, ctx):
    """把调用方给的 ctx 补齐成内部统一的字典。"""
    ctx = dict(ctx or {})
    c = {}
    c['sc'] = scene
    c['arm'] = ctx.get('arm')
    try:
        c['total'] = max(1, int(ctx.get('total') or 24))
    except Exception:
        c['total'] = 24
    try:
        c['fps'] = float(ctx.get('fps') or 24.0)
    except Exception:
        c['fps'] = 24.0
    c['base'] = _v3(ctx.get('base'))
    c['FV'] = _unit(ctx.get('FV'), (0.0, -1.0, 0.0))
    c['UP'] = _unit(ctx.get('UP'), (0.0, 0.0, 1.0))
    right = c['UP'].cross(c['FV'])
    if right.length < 1e-6:
        right = Vector((1.0, 0.0, 0.0))
    c['RIGHT'] = right.normalized()
    return c


# ---------------------------------------------------------------- 材质

def _bsdf(mat):
    """返回 Principled BSDF 节点（没有就建一个）。"""
    if mat is None:
        return None
    if not getattr(mat, 'use_nodes', False):
        mat.use_nodes = True
    nt = mat.node_tree
    node = nt.nodes.get('Principled BSDF')
    if node is None:
        for n in nt.nodes:
            if n.type == 'BSDF_PRINCIPLED':
                node = n
                break
    return node


def _put(node, name, value):
    """按名字给 socket 赋值，不存在就跳过（版本差异兜底）。"""
    if node is None:
        return False
    sock = node.inputs.get(name)
    if sock is None:
        return False
    try:
        sock.default_value = value
        return True
    except Exception:
        return False


def emis_mat(name, col, strength=3.0, rough=0.35, alpha=1.0, metallic=0.0, base=None):
    """自发光材质。

    col      自发光颜色（Base Color 默认同色，可用 base 单独压暗）
    strength Emission Strength
    alpha    1 = 不透明；<1 走 BLEND/BLENDED（EEVEE 下有效）
    base     Base Color；纯光物体务必给个很暗的底，否则被环境光照成奶白
    """
    col = _v3(col, (1.0, 1.0, 1.0))
    base_col = col if base is None else _v3(base, (0.0, 0.0, 0.0))
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    node = _bsdf(mat)
    _put(node, 'Base Color', (base_col.x, base_col.y, base_col.z, 1.0))
    _put(node, 'Roughness', float(rough))
    _put(node, 'Metallic', float(metallic))
    _put(node, 'Emission Color', (col.x, col.y, col.z, 1.0))
    _put(node, 'Emission Strength', float(strength))
    _put(node, 'Alpha', float(alpha))
    if alpha < 1.0:
        try:
            mat.blend_method = 'BLEND'
        except Exception:
            pass
        try:
            mat.surface_render_method = 'BLENDED'
        except Exception:
            pass
    try:
        mat.use_backface_culling = False
    except Exception:
        pass
    mat.diffuse_color = (base_col.x, base_col.y, base_col.z, float(alpha))
    return mat


def _key_emis(mat, keys):
    """给材质的 Emission Strength 打关键帧。keys = [(frame, value), ...]"""
    node = _bsdf(mat)
    if node is None:
        return
    sock = node.inputs.get('Emission Strength')
    if sock is None:
        return
    sc = bpy.context.scene
    for f, v in keys:
        sc.frame_set(int(round(f)))
        sock.default_value = float(v)
        try:
            sock.keyframe_insert('default_value', frame=f)
        except Exception:
            pass


# ---------------------------------------------------------------- 几何

def _object_mode():
    try:
        if bpy.context.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass


def _link(ob):
    try:
        bpy.context.scene.collection.objects.link(ob)
    except Exception:
        bpy.context.collection.objects.link(ob)
    return ob


def box(name, size, loc, mat=None, parent=None):
    """手写 8 顶点盒子（和 anim_kit 同一套做法，不依赖 ops）。"""
    sx, sy, sz = float(size[0]), float(size[1]), float(size[2])
    hx, hy, hz = sx * 0.5, sy * 0.5, sz * 0.5
    verts = [(-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
             (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
    faces = [(0, 1, 2, 3), (7, 6, 5, 4), (0, 3, 7, 4),
             (1, 5, 6, 2), (0, 4, 5, 1), (3, 2, 6, 7)]
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    if mat is not None:
        ob.data.materials.append(mat)
    _link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def cyl(name, radius, height, loc, mat=None, verts=24):
    _object_mode()
    bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=float(radius),
                                        depth=float(height), location=loc)
    ob = bpy.context.view_layer.objects.active or bpy.context.object
    ob.name = name
    if mat is not None:
        ob.data.materials.append(mat)
    return ob


def torus(name, major, minor, loc, mat=None, segs=48, rsegs=12, rot=(0.0, 0.0, 0.0)):
    _object_mode()
    bpy.ops.mesh.primitive_torus_add(major_radius=float(major),
                                     minor_radius=float(minor),
                                     major_segments=int(segs),
                                     minor_segments=int(rsegs),
                                     location=loc, rotation=rot)
    ob = bpy.context.view_layer.objects.active or bpy.context.object
    ob.name = name
    if mat is not None:
        ob.data.materials.append(mat)
    return ob


def ico(name, radius, loc, mat=None, subdiv=1):
    _object_mode()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=int(subdiv),
                                          radius=float(radius), location=loc)
    ob = bpy.context.view_layer.objects.active or bpy.context.object
    ob.name = name
    if mat is not None:
        ob.data.materials.append(mat)
    return ob


# ---------------------------------------------------------------- 关键帧

def _fcurves(action):
    """Blender 5.2 兼容：Action 没有 .fcurves，要遍历 layers/strips/channelbags。

    4.3 及更早可以直接 action.fcurves；5.x 的通道在
    ``action.layers[*].strips[*].channelbags[*].fcurves``。
    """
    if action is None:
        return []
    try:
        legacy = list(action.fcurves)
        if legacy:
            return legacy
    except Exception:
        pass
    out = []
    layers = getattr(action, 'layers', None)
    if not layers:
        return out
    for layer in layers:
        strips = getattr(layer, 'strips', None) or []
        for strip in strips:
            bags = getattr(strip, 'channelbags', None) or []
            for bag in bags:
                try:
                    out.extend(list(bag.fcurves))
                except Exception:
                    pass
    return out


def _set_interp(ob, mode='BEZIER', paths=None):
    """把某对象的 fcurve 插值方式统一改掉（paths 给名字后缀过滤）。"""
    ad = getattr(ob, 'animation_data', None)
    if ad is None or ad.action is None:
        return
    for fc in _fcurves(ad.action):
        if paths:
            if not any(fc.data_path.endswith(sfx) or sfx in fc.data_path for sfx in paths):
                continue
        for kp in fc.keyframe_points:
            kp.interpolation = mode
        try:
            fc.update()
        except Exception:
            pass


def key(ob, path, keys, degrees=False):
    """给 ob 的某个属性打关键帧。keys = [(frame, value), ...]"""
    sc = bpy.context.scene
    for f, v in keys:
        sc.frame_set(int(round(f)))
        if degrees and isinstance(v, (tuple, list)) and len(v) == 3:
            v = (math.radians(v[0]), math.radians(v[1]), math.radians(v[2]))
        if path.startswith('location') and not isinstance(v, Vector):
            v = Vector(v)
        setattr(ob, path, v)
        try:
            ob.keyframe_insert(path, frame=f)
        except Exception:
            pass


def key_quat(ob, path, keys):
    ob.rotation_mode = 'QUATERNION'
    sc = bpy.context.scene
    for f, q in keys:
        sc.frame_set(int(round(f)))
        setattr(ob, path, q)
        try:
            ob.keyframe_insert(path, frame=f)
        except Exception:
            pass


def _key_light(ob, keys):
    """给灯打 energy 关键帧（灯的能量在 ob.data 上）。"""
    data = getattr(ob, 'data', None)
    if data is None:
        return
    sc = bpy.context.scene
    for f, v in keys:
        sc.frame_set(int(round(f)))
        data.energy = float(v)
        try:
            data.keyframe_insert('energy', frame=f)
        except Exception:
            pass


def _key_alpha(mat, keys):
    node = _bsdf(mat)
    if node is None:
        return
    sock = node.inputs.get('Alpha')
    if sock is None:
        return
    sc = bpy.context.scene
    for f, v in keys:
        sc.frame_set(int(round(f)))
        sock.default_value = float(v)
        try:
            sock.keyframe_insert('default_value', frame=f)
        except Exception:
            pass


# ================================================================ 1. wind

def fx_wind(scene, ctx, spec=None):
    """风特效（T10）：一圈细长薄片贴地扫过，随角色前进方向飘。

    spec
        n        30           薄片数量
        radius   [1.0,2.6]    出生半径范围（格）
        z        [0.05,0.45]  出生高度范围（格）
        length   [0.8,1.6]    薄片长度（格）
        life     [12,22]      单片存活帧数
        travel   6.5          沿风向飘的距离（格）
        rise     0.35         沿途上扬高度（格）
        strength 0.85         自发光强度（白色风线别太亮）
        color    [0.80,0.88,1.0] 颜色
    """
    spec = dict(spec or {})
    c = _ctx(scene, ctx)
    base, FV, RIGHT, UP, total = c['base'], c['FV'], c['RIGHT'], c['UP'], c['total']

    n = max(1, int(spec.get('n', 30)))
    rad = ramp(spec, 'radius', (1.0, 2.6))
    zr = ramp(spec, 'z', (0.05, 0.45))
    lgr = ramp(spec, 'length', (0.8, 1.6))
    lfr = ramp(spec, 'life', (12.0, 22.0))
    travel = float(spec.get('travel', 6.5))
    rise = float(spec.get('rise', 0.35))
    strength = float(spec.get('strength', 0.85))
    col = color(spec.get('color'), (0.80, 0.88, 1.0))

    mat = emis_mat(_pfx('wind', 'mat'), col, strength, rough=0.55, alpha=0.86, base=col)

    made = []
    for i in range(n):
        f0 = random.uniform(0.0, total * 0.45)
        life = random.uniform(lfr[0], lfr[1])
        ang = random.uniform(0.0, math.tau)
        r = random.uniform(rad[0], rad[1])
        origin = (base
                  + RIGHT * (math.cos(ang) * r)
                  + FV * (math.sin(ang) * r)
                  + UP * random.uniform(zr[0], zr[1]))
        dirv = (FV * random.uniform(0.85, 1.0)
                + RIGHT * random.uniform(-0.22, 0.22)).normalized()
        L = random.uniform(lgr[0], lgr[1])
        ob = box(_pfx('wind', 'blade%02d' % i), (L, 0.018, 0.011), origin, mat)
        ob.rotation_euler = (0.0, 0.0,
                             math.atan2(dirv.y, dirv.x) + math.radians(random.uniform(-9.0, 9.0)))
        end = origin + dirv * travel + UP * rise
        key(ob, 'location', [(f0, origin), (f0 + life, end)])
        key(ob, 'scale', [(f0 - 1.0, (0.001, 0.001, 0.001)),
                          (f0 + 1.0, (1.0, 1.0, 1.0)),
                          (f0 + life - 2.0, (1.0, 1.0, 1.0)),
                          (f0 + life, (0.001, 0.001, 0.001))])
        _set_interp(ob, 'LINEAR')
        made.append(ob)
    return made


# ================================================================ 2. glow_eyes

def _eye_positions(c, spec):
    """返回两眼位置：优先骨架 eye.* 骨骼，其次 spec['at']，最后按身体比例兜底。"""
    base, FV, RIGHT, UP = c['base'], c['FV'], c['RIGHT'], c['UP']
    spread = float(spec.get('spread', 0.105))
    height = float(spec.get('height', 1.60))
    depth = float(spec.get('depth', 0.20))

    at = spec.get('at')
    if at is not None and len(at) >= 2:
        return [_v3(at[0]), _v3(at[1])], 'spec.at'

    arm = c.get('arm')
    if arm is not None:
        left = right = None
        mw = arm.matrix_world
        pose = getattr(arm, 'pose', None)
        bones = getattr(pose, 'bones', None) if pose is not None else None
        if bones:
            for b in bones:
                nm = (b.name or '').lower()
                if 'eye' not in nm:
                    continue
                pos = mw @ b.head
                if nm.endswith(('.l', '_l', 'le', 'left')):
                    left = pos
                elif nm.endswith(('.r', '_r', 're', 'right')):
                    right = pos
                elif left is None:
                    left = pos
                else:
                    right = pos
        if left is not None and right is not None:
            if left.x > right.x:
                left, right = right, left
            push = float(spec.get('push', 0.020))
            return [left + FV * push, right + FV * push], 'bones.eye'

    center = base + UP * height + FV * depth
    return [center - RIGHT * spread, center + RIGHT * spread], 'fallback'


def fx_glow_eyes(scene, ctx, spec=None):
    """眼睛发光（T13）：两个自发光小方块 + 一个点光源，随呼吸微亮。

    spec
        color    cyan        发光颜色（也可 RGB）
        strength 2.6         Emission Strength（AgX 下再高就糊成白色）
        min_ratio 0.55       呼吸最低亮度比例
        light    30.0        点光源功率（W）
        light_radius 0.12    点光源半径（柔和程度）
        pulses   3           呼吸次数（整段时长里的明暗周期）
        size     [0.085,0.030,0.075]  眼块 [宽,厚,高]
        spread   0.105       两眼中心间距的一半（无骨骼时用）
        push     0.020       沿前向再外推一点（有骨骼时防止眼块埋进脸里）
        height   1.60        眼睛高度（无骨骼时用）
        depth    0.20        眼睛前伸距离（无骨骼时用）
        at       None        直接给两个相对 base 的坐标 [[x,y,z],[x,y,z]]
    """
    spec = dict(spec or {})
    c = _ctx(scene, ctx)
    base, FV, total = c['base'], c['FV'], c['total']

    col = color(spec.get('color'), _NAMED['cyan'])
    strength = float(spec.get('strength', 2.6))
    min_ratio = float(spec.get('min_ratio', 0.55))
    light_w = float(spec.get('light', 30.0))
    light_r = float(spec.get('light_radius', 0.12))
    pulses = max(1, int(spec.get('pulses', 3)))
    size = spec.get('size', [0.085, 0.030, 0.075])
    size = (float(size[0]), float(size[1]), float(size[2]))

    eyes, source = _eye_positions(c, spec)
    p('  眼睛位置来自 %s  (%s %s)' % (source,
                                      tuple(round(v, 2) for v in eyes[0]),
                                      tuple(round(v, 2) for v in eyes[1])))

    mat = emis_mat(_pfx('glow_eyes', 'mat'), col, strength, rough=0.25,
                   base=(0.02, 0.05, 0.06))
    yaw = math.atan2(-FV.x, FV.y)

    made = []
    for i, pos in enumerate(eyes):
        ob = box(_pfx('glow_eyes', 'eye%s' % ('L' if i == 0 else 'R')), size, pos, mat)
        ob.rotation_euler = (0.0, 0.0, yaw)
        made.append(ob)

    mid = (eyes[0] + eyes[1]) * 0.5 + FV * 0.30
    lamp_data = bpy.data.lights.new(_pfx('glow_eyes', 'lamp'), 'POINT')
    lamp_data.energy = light_w
    lamp_data.color = (col.x, col.y, col.z)
    try:
        lamp_data.shadow_soft_size = light_r
    except Exception:
        pass
    lamp = bpy.data.objects.new(_pfx('glow_eyes', 'lamp'), lamp_data)
    lamp.location = mid
    _link(lamp)
    made.append(lamp)

    # 呼吸：material Emission Strength + 灯功率 同步
    frames = max(8, int(round(total)))
    ems_keys = []
    lit_keys = []
    for k in range(pulses + 1):
        f0 = 1.0 + (frames - 1.0) * k / float(pulses)
        phase = 0.5 - 0.5 * math.cos(math.tau * k / float(pulses))
        ratio = min_ratio + (1.0 - min_ratio) * phase
        ems_keys.append((f0, strength * ratio))
        lit_keys.append((f0, light_w * ratio))
    _key_emis(mat, ems_keys)
    _key_light(lamp, lit_keys)
    return made


# ================================================================ 3. embers

def fx_embers(scene, ctx, spec=None):
    """余烬 / 火星（T24）：几十个自发光小方块从地面向上飘，带随机相位与淡出。

    spec
        n        80           火星数量
        color    amber        主色
        color_hot [1.0,0.58,0.10] 亮芯色（越亮越黄）
        radius   [0.18,0.95]  出生半径范围（格）
        size     [0.016,0.036] 单颗边长（格，火星要小）
        rise     [0.8,2.0]    上升高度（格）
        life     [total*0.5, total*0.9] 单颗存活帧数
        strength 3.0          峰值 Emission Strength（AgX 下再高就变白点）
        sway     [0.04,0.14]  左右摆动幅度（格）
        drift_fwd 0.15        随角色前向飘的距离（格）
        z        [0.02,0.25]  出生高度范围（格）
    """
    spec = dict(spec or {})
    c = _ctx(scene, ctx)
    base, FV, RIGHT, UP, total = c['base'], c['FV'], c['RIGHT'], c['UP'], c['total']

    n = max(1, int(spec.get('n', 80)))
    col0 = color(spec.get('color'), _NAMED['amber'])
    col1 = color(spec.get('color_hot'), (1.0, 0.58, 0.10))
    rad = ramp(spec, 'radius', (0.18, 0.95))
    sz = ramp(spec, 'size', (0.016, 0.036))
    rise = ramp(spec, 'rise', (0.8, 2.0))
    life = ramp(spec, 'life', (max(8.0, total * 0.5), max(12.0, total * 0.9)))
    sway = ramp(spec, 'sway', (0.04, 0.14))
    zr = ramp(spec, 'z', (0.02, 0.25))
    peak = float(spec.get('strength', 3.0))
    drift = float(spec.get('drift_fwd', 0.15))

    made = []
    for i in range(n):
        t_mix = random.random() ** 1.6
        col = col0.lerp(col1, t_mix)
        # 每颗一颗材质，才能各自淡出
        mat = emis_mat(_pfx('embers', 'mat%02d' % i), col, peak, rough=0.6,
                       base=(0.035, 0.014, 0.004))
        ang = random.uniform(0.0, math.tau)
        r = random.uniform(rad[0], rad[1])
        origin = (base
                  + RIGHT * (math.cos(ang) * r)
                  + FV * (math.sin(ang) * r)
                  + UP * random.uniform(zr[0], zr[1]))
        h_rise = random.uniform(rise[0], rise[1])
        life_f = random.uniform(life[0], life[1])
        f0 = random.uniform(-life_f * 0.35, max(0.0, total - life_f * 0.5))
        s = random.uniform(sz[0], sz[1])
        ob = box(_pfx('embers', 'e%03d' % i), (s, s, s * random.uniform(0.8, 1.4)),
                 origin, mat)
        phase = random.uniform(0.0, math.tau)
        amp = random.uniform(sway[0], sway[1])
        sway_dir = (RIGHT * random.uniform(-1.0, 1.0)
                    + FV * random.uniform(-0.6, 0.6))
        if sway_dir.length < 1e-6:
            sway_dir = RIGHT.copy()
        sway_dir.normalize()

        locs = []
        f = f0
        while f <= f0 + life_f:
            t = max(0.0, min(1.0, (f - f0) / life_f))
            off = (UP * (h_rise * t)
                   + sway_dir * (amp * math.sin(math.tau * 1.5 * t + phase))
                   + FV * (drift * t))
            locs.append((f, origin + off))
            f += 2.0
        if not locs or locs[-1][0] < f0 + life_f:
            t = 1.0
            off = (UP * h_rise + sway_dir * (amp * math.sin(math.tau * 1.5 + phase))
                   + FV * drift)
            locs.append((f0 + life_f, origin + off))
        key(ob, 'location', locs)
        key(ob, 'scale', [(f0 - 0.5, (0.001, 0.001, 0.001)),
                          (f0 + 1.5, (1.0, 1.0, 1.0)),
                          (f0 + life_f * 0.75, (0.85, 0.85, 0.85)),
                          (f0 + life_f, (0.001, 0.001, 0.001))])
        _key_emis(mat, [(f0 - 0.5, 0.0),
                        (f0 + 2.0, peak),
                        (f0 + life_f * 0.65, peak * 0.75),
                        (f0 + life_f, 0.0)])
        _set_interp(ob, 'LINEAR')
        made.append(ob)
    return made


# ================================================================ 4. teleport

def fx_teleport(scene, ctx, spec=None):
    """传送（T32）：竖直能量柱快速收缩 + 环形冲击波。

    结构：雾壳圆柱（很淡）+ 细亮芯柱 + 一圈竖直能量条，整体按 z 压扁并沉到地面，
    随后两圈 torus 冲击波向外扩散。

    spec
        height     2.8       柱高（格）
        radius     0.30      能量条分布半径（0.25~0.35 合适）
        bars       10        竖直能量条数量（0 = 只要芯柱和雾壳）
        barsize    0.035     能量条截面边长（格）
        color      ice       主色
        core_color [0.75,0.97,1.0] 芯柱颜色
        alpha      0.10      雾壳不透明度（越大越像桶，别超过 0.2）
        core_alpha 0.90      芯柱不透明度
        in         0.08      出现时刻（占 total 比例）
        collapse   0.50      收缩时刻（占 total 比例）
        ring_major 0.62      冲击波起始半径（格）
        ring_grow  3.4       冲击波最终半径倍率
        flash      700.0     中心闪光功率（W）
    """
    spec = dict(spec or {})
    c = _ctx(scene, ctx)
    base, UP, total = c['base'], c['UP'], c['total']

    h = float(spec.get('height', 2.8))
    r = float(spec.get('radius', 0.30))
    nb = max(0, int(spec.get('bars', 10)))
    bs = float(spec.get('barsize', 0.035))
    col = color(spec.get('color'), _NAMED['ice'])
    col_core = color(spec.get('core_color'), (0.75, 0.97, 1.0))
    a_shell = float(spec.get('alpha', 0.10))
    a_core = float(spec.get('core_alpha', 0.90))
    f_in = max(1.0, round(total * float(spec.get('in', 0.08))))
    f_col = max(f_in + 2.0, round(total * float(spec.get('collapse', 0.50))))
    vanish = f_col + 4.0
    ring_major = float(spec.get('ring_major', 0.62))
    ring_grow = float(spec.get('ring_grow', 3.4))
    flash_w = float(spec.get('flash', 700.0))

    mat_shell = emis_mat(_pfx('teleport', 'mat_shell'), col, 0.5, rough=0.30,
                         alpha=a_shell, base=(0.01, 0.03, 0.05))
    mat_core = emis_mat(_pfx('teleport', 'mat_core'), col_core, 4.0, rough=0.12,
                        alpha=a_core, base=(0.02, 0.04, 0.05))
    mat_bar = emis_mat(_pfx('teleport', 'mat_bar'), col, 1.8, rough=0.25,
                       alpha=0.85, base=(0.01, 0.05, 0.07))
    mat_ring = emis_mat(_pfx('teleport', 'mat_ring'), col_core, 8.0, rough=0.20,
                        base=(0.02, 0.05, 0.06))

    def _squash(ob, grow=1.0, flat=0.02):
        """出现 -> 保持 -> 压扁 + 沉到地面 -> 消失。"""
        key(ob, 'scale', [(f_in - 1.0, (0.001, 0.001, 0.001)),
                          (f_in + 1.0, (grow, grow, grow)),
                          (f_col - 1.0, (grow, grow, grow)),
                          (f_col + 2.0, (grow * 1.2, grow * 1.2, flat * grow)),
                          (vanish, (0.001, 0.001, 0.001))])
        key(ob, 'location', [(f_col - 1.0, ob.location.copy()),
                             (f_col + 2.0, (ob.location.x, ob.location.y,
                                            base.z + 0.03))])

    made = []
    shell = cyl(_pfx('teleport', 'shell'), r, h, base + UP * (h * 0.5), mat_shell)
    _squash(shell)
    _key_emis(mat_shell, [(f_in - 1.0, 0.0), (f_in + 1.0, 0.5),
                          (f_col - 1.0, 0.7), (f_col + 1.0, 3.0), (vanish, 0.0)])
    made.append(shell)

    core = cyl(_pfx('teleport', 'core'), r * 0.22, h * 1.02,
               base + UP * (h * 0.51), mat_core, verts=20)
    _squash(core, flat=0.012)
    _key_emis(mat_core, [(f_in - 1.0, 0.0), (f_in + 1.0, 3.5),
                         (f_col - 1.0, 4.5), (f_col + 1.0, 10.0), (vanish, 0.0)])
    made.append(core)

    for i in range(nb):
        a = math.tau * (i + 0.5) / float(nb)
        bh = h * random.uniform(0.45, 0.88)
        off = (Vector((math.cos(a), math.sin(a), 0.0)) * (r * 1.05))
        ob = box(_pfx('teleport', 'bar%02d' % i), (bs, bs, bh),
                 base + off + UP * (bh * 0.5), mat_bar)
        d = f_in + 0.5 * (i % 4)
        key(ob, 'scale', [(d - 1.0, (0.001, 0.001, 0.001)),
                          (d + 1.0, (1.0, 1.0, 1.0)),
                          (f_col + 1.0, (1.0, 1.0, 1.0)),
                          (f_col + 3.0, (0.4, 0.4, 0.02)),
                          (vanish, (0.001, 0.001, 0.001))])
        made.append(ob)

    # 两圈冲击波
    for k, (off_f, emis) in enumerate(((0.5, 8.0), (2.0, 6.0))):
        ring = torus(_pfx('teleport', 'ring%d' % k), ring_major, 0.035,
                     base + UP * 0.035, mat_ring, segs=64, rsegs=8)
        f0 = f_col + off_f
        key(ring, 'scale', [(f0 - 0.5, (0.001, 0.001, 0.001)),
                            (f0 + 1.0, (0.35, 0.35, 1.0)),
                            (f0 + 9.0, (ring_grow, ring_grow, 1.0)),
                            (f0 + 11.0, (ring_grow * 1.15, ring_grow * 1.15, 0.4)),
                            (f0 + 13.0, (0.001, 0.001, 0.001))])
        made.append(ring)

    lamp_data = bpy.data.lights.new(_pfx('teleport', 'flash'), 'POINT')
    lamp_data.energy = 0.0
    lamp_data.color = (col.x, col.y, col.z)
    lamp = bpy.data.objects.new(_pfx('teleport', 'flash'), lamp_data)
    lamp.location = base + UP * (h * 0.5)
    _link(lamp)
    _key_light(lamp, [(f_in - 1.0, 0.0), (f_in + 1.0, flash_w * 0.4),
                      (f_col, flash_w), (f_col + 3.0, flash_w * 0.15),
                      (vanish, 0.0)])
    made.append(lamp)

    # 环的亮度也要淡出（材质是共用的，用 alpha 关键帧近似）
    _key_emis(mat_ring, [(f_col - 0.5, 0.0), (f_col + 0.5, 8.0),
                         (f_col + 8.0, 3.0), (f_col + 14.0, 0.0)])
    p('  传送柱: 出现 %d 帧 -> 收缩 %d 帧  柱 %.1f×%.2f 格  能量条 %d'
      % (f_in, f_col - f_in, h, r, nb))
    return made


# ================================================================ 5. spawn_in

def fx_spawn_in(scene, ctx, spec=None):
    """物品/方块出现（T17）：目标从 scale 0 弹到 1，带 overshoot 与闪光。

    spec
        object     None      目标物体名（不给则用 'target'，再不给就造占位方块）
        dist       1.35      占位方块离角色的前向距离（格）
        size       0.55      占位方块边长（格）
        color      gold      占位方块颜色
        glow       1.4       占位方块自发光强度
        pop        0.32      弹出时刻（占 total 比例）
        overshoot  1.15      过冲倍率
        bounce     0.96      回弹倍率
        flash      260.0     闪光功率（W）
        with_flash True      是否加白闪方块 + 闪光灯
    """
    spec = dict(spec or {})
    c = _ctx(scene, ctx)
    base, FV, UP, total = c['base'], c['FV'], c['UP'], c['total']

    name = spec.get('object') or spec.get('target')
    dist = float(spec.get('dist', 1.35))
    size = float(spec.get('size', 0.55))
    col = color(spec.get('color'), _NAMED['gold'])
    glow = float(spec.get('glow', 1.4))
    pop = max(2.0, round(total * float(spec.get('pop', 0.32))))
    over = float(spec.get('overshoot', 1.15))
    bounce = float(spec.get('bounce', 0.96))
    flash_w = float(spec.get('flash', 260.0))
    with_flash = bool(spec.get('with_flash', True))

    tgt = None
    if name:
        tgt = bpy.data.objects.get(str(name))
        if tgt is None and hasattr(name, 'name'):
            tgt = name
    placeholder = tgt is None
    if placeholder:
        mat = emis_mat(_pfx('spawn_in', 'mat'), col, glow, rough=0.30,
                       metallic=0.25, base=col * 0.5)
        tgt = box(_pfx('spawn_in', 'target'), (size, size, size),
                  base + FV * dist + UP * (size * 0.5), mat)
        tgt.name = _pfx('spawn_in', 'target')
    p('  spawn_in: 目标 %s%s  第 %d 帧弹出  过冲 %.2f'
      % (tgt.name, '（占位）' if placeholder else '', pop, over))

    made = [tgt]
    key(tgt, 'scale', [(pop - 3.0, (0.001, 0.001, 0.001)),
                       (pop + 2.0, (over, over, over)),
                       (pop + 5.0, (bounce, bounce, bounce)),
                       (pop + 8.0, (1.0, 1.0, 1.0))])
    _set_interp(tgt, 'BEZIER')

    center = tgt.location.copy()
    if placeholder:
        center = base + FV * dist + UP * (size * 0.5)

    if with_flash:
        fmat = emis_mat(_pfx('spawn_in', 'mat_flash'), (1.0, 0.98, 0.90), 6.0,
                        rough=0.4, base=(0.05, 0.05, 0.05))
        fl = box(_pfx('spawn_in', 'flash'), (size * 1.3, size * 1.3, size * 1.3),
                 center, fmat)
        key(fl, 'scale', [(pop - 2.0, (0.2, 0.2, 0.2)),
                          (pop + 1.0, (1.25, 1.25, 1.25)),
                          (pop + 4.0, (0.001, 0.001, 0.001))])
        _key_emis(fmat, [(pop - 2.0, 0.0), (pop + 1.0, 6.0), (pop + 4.0, 0.0)])
        made.append(fl)

        lamp_data = bpy.data.lights.new(_pfx('spawn_in', 'lamp'), 'POINT')
        lamp_data.energy = 0.0
        lamp_data.color = (1.0, 0.85, 0.55)
        lamp = bpy.data.objects.new(_pfx('spawn_in', 'lamp'), lamp_data)
        lamp.location = center + FV * 0.55 + UP * (size * 0.55)
        _link(lamp)
        _key_light(lamp, [(pop - 2.0, 0.0), (pop + 1.0, flash_w),
                          (pop + 5.0, flash_w * 0.2), (pop + 9.0, 0.0)])
        made.append(lamp)

    if placeholder:
        # 占位方块自己也在弹出前不可见
        _key_emis(tgt.data.materials[0], [(pop - 3.0, 0.0),
                                          (pop + 2.0, glow * 1.6),
                                          (pop + 8.0, glow)])
    return made


# ================================================================ 6. splash

def fx_splash(scene, ctx, spec=None):
    """水花（T34）：几个方块从落点向外抛物飞散，地面留一圈涟漪。

    spec
        n        14          水花方块数量
        color    water       颜色
        strength 1.6         自发光强度（水花偏亮一点好看）
        size     [0.09,0.16] 单块边长（格）
        vz       [3.2,5.2]   初始竖直速度（格/秒）
        vh       [1.5,3.4]   初始水平速度（格/秒）
        gravity  22.0        重力（格/秒²，MC 手感偏大）
        start    0.18        起跳时刻（占 total 比例）
        ring     True        是否画地面涟漪环
        ring_radius [0.6,2.2] 涟漪环最终半径（格）
        origin   None        落点（相对 base 的偏移）；None = 角色脚前 0.2 格
    """
    spec = dict(spec or {})
    c = _ctx(scene, ctx)
    base, FV, RIGHT, UP, total = c['base'], c['FV'], c['RIGHT'], c['UP'], c['total']
    fps = c['fps']

    n = max(1, int(spec.get('n', 14)))
    col = color(spec.get('color'), _NAMED['water'])
    strength = float(spec.get('strength', 1.6))
    sizr = ramp(spec, 'size', (0.09, 0.16))
    vzr = ramp(spec, 'vz', (3.2, 5.2))
    vhr = ramp(spec, 'vh', (1.5, 3.4))
    g = float(spec.get('gravity', 22.0))
    f_start = max(1.0, round(total * float(spec.get('start', 0.18))))

    org = spec.get('origin')
    origin = base + (FV * 0.2 if org is None else _v3(org))

    mat = emis_mat(_pfx('splash', 'mat'), col, strength, rough=0.15,
                   alpha=0.92, base=col * 0.7)

    made = []
    for i in range(n):
        ang = random.uniform(0.0, math.tau)
        horiz = (RIGHT * math.cos(ang) + FV * math.sin(ang))
        vh = random.uniform(vhr[0], vhr[1])
        vz = random.uniform(vzr[0], vzr[1])
        s = random.uniform(sizr[0], sizr[1])
        flight = 2.0 * vz / g
        frames = max(4, int(round(flight * fps)))
        ob = box(_pfx('splash', 'drop%02d' % i), (s, s, s), origin, mat)
        locs = []
        for f in range(frames + 1):
            t = f / fps
            off = horiz * (vh * t) + UP * (vz * t - 0.5 * g * t * t)
            locs.append((f_start + f, origin + off + UP * (s * 0.5)))
        key(ob, 'location', locs)
        key(ob, 'rotation_euler', [(f_start, (0.0, 0.0, 0.0)),
                                   (f_start + frames,
                                    (math.radians(random.uniform(120, 360)),
                                     math.radians(random.uniform(120, 360)),
                                     math.radians(random.uniform(120, 360))))])
        key(ob, 'scale', [(f_start - 1.0, (0.001, 0.001, 0.001)),
                          (f_start, (1.0, 1.0, 1.0)),
                          (f_start + frames, (0.6, 0.6, 0.6)),
                          (f_start + frames + 1.0, (0.001, 0.001, 0.001))])
        _set_interp(ob, 'LINEAR')
        made.append(ob)

    if spec.get('ring', True):
        rr = ramp(spec, 'ring_radius', (0.6, 2.2))
        rmat = emis_mat(_pfx('splash', 'mat_ring'), col, 2.0, rough=0.25,
                        alpha=0.55, base=(0.01, 0.03, 0.05))
        ring = torus(_pfx('splash', 'ring'), 0.42, 0.022, origin + UP * 0.02,
                     rmat, segs=64, rsegs=8)
        grow = rr[1]
        key(ring, 'scale', [(f_start - 1.0, (0.001, 0.001, 0.001)),
                            (f_start + 1.0, (0.4, 0.4, 1.0)),
                            (f_start + 9.0, (grow * 1.5, grow * 1.5, 1.0)),
                            (f_start + 12.0, (grow * 1.9, grow * 1.9, 0.6)),
                            (f_start + 14.0, (0.001, 0.001, 0.001))])
        _key_alpha(rmat, [(f_start, 0.55), (f_start + 9.0, 0.30),
                          (f_start + 14.0, 0.0)])
        made.append(ring)

    p('  splash: %d 块  起跳 %.0f 帧  重力 %.1f 格/秒²' % (n, f_start, g))
    return made


# ================================================================ 7. fog_gate

def fx_fog_gate(scene, ctx, spec=None):
    """雾门 / 传送门（T41）：半透明自发光薄板 + 两圈旋转符文环。

    spec
        w           2.6      门宽（格）
        h           3.2      门高（格）
        thickness   0.03     门板厚度（格）
        dist        2.0      门离角色的前向距离（格）
        side        1.35     门中心相对角色中轴的右向偏移（格）
        color       violet   雾面颜色
        alpha       0.13     雾面不透明度（0.1~0.2，别做成实心板）
        strength    0.7      雾面自发光（高了就变亮板子）
        rune_strength 7.0    符文环自发光
        frame_strength 6.0   门框自发光
        runes       8        内环符文数量
        spin        1.0      外环转一圈所需的整段时长倍率（1 = 整段一圈）
        grow        0.25     门从地面长出的时刻（占 total 比例）
        light       45.0     门内点光源功率（W）
    """
    spec = dict(spec or {})
    c = _ctx(scene, ctx)
    base, FV, RIGHT, UP, total = c['base'], c['FV'], c['RIGHT'], c['UP'], c['total']

    w = float(spec.get('w', 2.6))
    h = float(spec.get('h', 3.2))
    th = float(spec.get('thickness', 0.03))
    dist = float(spec.get('dist', 2.0))
    side = float(spec.get('side', 1.35))
    col = color(spec.get('color'), _NAMED['violet'])
    alpha = float(spec.get('alpha', 0.13))
    strength = float(spec.get('strength', 0.7))
    rune_strength = float(spec.get('rune_strength', 7.0))
    frame_strength = float(spec.get('frame_strength', 6.0))
    nrunes = max(0, int(spec.get('runes', 8)))
    spin = float(spec.get('spin', 1.0))
    f_grow = max(1.0, round(total * float(spec.get('grow', 0.25))))
    light_w = float(spec.get('light', 45.0))

    center = base + FV * dist + RIGHT * side
    at = center + UP * (h * 0.5)

    pm = emis_mat(_pfx('fog_gate', 'mat_panel'), col, strength, rough=0.55,
                  alpha=alpha, base=(0.02, 0.012, 0.05))
    fm = emis_mat(_pfx('fog_gate', 'mat_frame'), col, frame_strength, rough=0.30,
                  alpha=0.95, base=(0.03, 0.02, 0.06))
    rm = emis_mat(_pfx('fog_gate', 'mat_rune'), (0.92, 0.88, 1.0), rune_strength,
                  rough=0.25, base=(0.03, 0.02, 0.06))

    made = []

    panel = box(_pfx('fog_gate', 'panel'), (w, th, h), at, pm)
    panel.rotation_euler = (0.0, 0.0, math.atan2(FV.y, FV.x) + math.pi * 0.5)
    # 从地面长出来：z 缩放 + 位置联动（原点在门中心，所以同时抬 location）
    key(panel, 'scale', [(f_grow - 2.0, (1.0, 1.0, 0.001)),
                         (f_grow + 4.0, (1.0, 1.0, 1.0))])
    key(panel, 'location', [(f_grow - 2.0, center + UP * (h * 0.002)),
                            (f_grow + 4.0, at)])
    made.append(panel)

    # 门框：四根细条挂在门板上一起长
    for i, (bw, bh, off) in enumerate((
            (w + 0.10, 0.05, Vector((0.0, 0.0, h * 0.5))),
            (w + 0.10, 0.05, Vector((0.0, 0.0, -h * 0.5))),
            (0.05, h + 0.10, Vector((-w * 0.5, 0.0, 0.0))),
            (0.05, h + 0.10, Vector((w * 0.5, 0.0, 0.0))))):
        bar = box(_pfx('fog_gate', 'frame%d' % i), (bw, th * 1.6, bh),
                  (0.0, 0.0, 0.0), fm, parent=panel)
        bar.location = off
        made.append(bar)

    # 两圈符文环（绕门的法线旋转）
    base_q = FV.to_track_quat('Z', 'Y')
    ring_objs = []
    for k, (rmaj, rmin, turns) in enumerate(((w * 0.30, 0.022, 1.0),
                                             (w * 0.21, 0.016, -1.6))):
        ring = torus(_pfx('fog_gate', 'runering%d' % k), rmaj, rmin, at, rm,
                     segs=64, rsegs=8)
        ring.rotation_mode = 'QUATERNION'
        ring.rotation_quaternion = base_q
        keys = []
        steps = 8
        for s in range(steps + 1):
            f = f_grow + (total - f_grow) * s / float(steps)
            ang = math.tau * turns * s / float(steps) / max(1.0, spin)
            keys.append((f, Quaternion(FV, ang) @ base_q))
        key_quat(ring, 'rotation_quaternion', keys)
        key(ring, 'scale', [(f_grow - 1.0, (0.001, 0.001, 0.001)),
                            (f_grow + 3.0, (1.0, 1.0, 1.0))])
        ring_objs.append(ring)
        made.append(ring)

    # 内圈符文块
    inner = ring_objs[1]
    for i in range(nrunes):
        a = math.tau * i / float(nrunes)
        rr = w * 0.21
        rune = box(_pfx('fog_gate', 'rune%02d' % i), (0.035, 0.035, 0.085),
                   (0.0, 0.0, 0.0), rm, parent=inner)
        rune.location = (math.cos(a) * rr, math.sin(a) * rr, 0.0)
        rune.rotation_euler = (0.0, 0.0, a + math.pi * 0.25)
        made.append(rune)

    lamp_data = bpy.data.lights.new(_pfx('fog_gate', 'lamp'), 'POINT')
    lamp_data.energy = 0.0
    lamp_data.color = (col.x, col.y, col.z)
    lamp = bpy.data.objects.new(_pfx('fog_gate', 'lamp'), lamp_data)
    lamp.location = at + FV * 0.35
    _link(lamp)
    _key_light(lamp, [(f_grow - 1.0, 0.0), (f_grow + 4.0, light_w),
                      (max(f_grow + 5.0, total), light_w * 0.8)])
    made.append(lamp)

    p('  fog_gate: %.1f×%.1f 格  距角色 %.1f / 右 %.1f  符文 %d 个'
      % (w, h, dist, side, nrunes))
    return made


# ================================================================ 注册表

FX_REGISTRY = {
    'wind': fx_wind,
    'glow_eyes': fx_glow_eyes,
    'embers': fx_embers,
    'teleport': fx_teleport,
    'spawn_in': fx_spawn_in,
    'splash': fx_splash,
    'fog_gate': fx_fog_gate,
}

# 与上面各函数 docstring 缺省值保持一致的“配方级”默认值（自测与文档共用）。
FX_DEFAULTS = {
    'wind': {'n': 30, 'strength': 0.85, 'travel': 6.5},
    'glow_eyes': {'strength': 2.6, 'light': 30.0, 'pulses': 3},
    'embers': {'n': 80, 'strength': 3.0},
    'teleport': {'height': 2.8, 'radius': 0.30, 'bars': 10},
    'spawn_in': {'overshoot': 1.15, 'bounce': 0.96, 'glow': 1.4, 'dist': 1.35},
    'splash': {'n': 14, 'gravity': 22.0},
    'fog_gate': {'w': 2.6, 'h': 3.2, 'side': 1.35, 'alpha': 0.13},
}


def cleanup_fx(hide=False):
    """清理所有 fx_ 前缀对象（hide=True 只隐藏，不删）。返回处理数量。"""
    n = 0
    for ob in list(bpy.data.objects):
        if not ob.name.startswith(FX_PREFIX):
            continue
        n += 1
        if hide:
            ob.hide_render = True
            ob.hide_viewport = True
            continue
        data = ob.data
        try:
            bpy.data.objects.remove(ob, do_unlink=True)
        except Exception:
            continue
        try:
            if isinstance(data, bpy.types.Mesh) and data.users == 0:
                bpy.data.meshes.remove(data)
        except Exception:
            pass
    if not hide:
        for mat in list(bpy.data.materials):
            if mat.name.startswith(FX_PREFIX) and mat.users == 0:
                try:
                    bpy.data.materials.remove(mat)
                except Exception:
                    pass
    return n


# ================================================================ 自测

_CAMS = {
    'wind': ((-4.6, -6.4, 2.9), (0.0, -0.6, 0.90), 50.0),
    'glow_eyes': ((-1.25, -2.25, 1.90), (0.0, 0.0, 1.60), 50.0),
    'embers': ((-3.4, -5.0, 2.2), (0.0, 0.0, 1.05), 50.0),
    'teleport': ((-5.0, -7.0, 3.0), (0.0, 0.0, 1.25), 50.0),
    'spawn_in': ((-3.9, -5.4, 2.30), (0.0, -0.85, 0.80), 45.0),
    'splash': ((-4.6, -6.2, 2.30), (0.0, 0.0, 0.70), 45.0),
    'fog_gate': ((-6.4, -8.4, 3.5), (0.6, -1.2, 1.45), 45.0),
}

# 单帧验收看不出打击感的特效，额外渲一帧
_EXTRA_FRAMES = {'teleport': (17, '双环冲击波')}

_TEST_ORDER = ('wind', 'glow_eyes', 'embers', 'teleport', 'spawn_in', 'splash', 'fog_gate')


def _aim(ob, target):
    d = Vector(target) - ob.location
    if d.length < 1e-6:
        return
    ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()


def _cam_for(sc, name):
    loc, tgt, lens = _CAMS.get(name, ((-4.0, -6.0, 2.6), (0.0, 0.0, 1.1), 50.0))
    cd = bpy.data.cameras.get(_pfx('selftest', 'cam')) or bpy.data.cameras.new('fx_selftest_cam')
    cam = bpy.data.objects.get('fx_selftest_cam')
    if cam is None:
        cam = bpy.data.objects.new('fx_selftest_cam', cd)
        _link(cam)
    cam.data.lens = lens
    cam.location = Vector(loc)
    _aim(cam, tgt)
    sc.camera = cam
    return cam


_ENUM_KNOWN = {
    'type': ('Fog Glow', 'Bloom', 'Ghosts', 'Simple Star', 'Sun Beams', 'Streaks', 'Kernel'),
    'quality': ('High', 'Medium', 'Low'),
    'kernel_data_type': ('1D', '2D'),
}


def _set_enum_socket(node, sock_name, want):
    """5.2 的 Glare Type 是 NodeSocketMenu：enum_items 为空，只认显示名。"""
    sock = node.inputs.get(sock_name)
    if sock is None:
        return None
    wl = want.lower().replace('_', ' ').replace('-', ' ')
    cands = []
    for k in _ENUM_KNOWN.get(sock_name.lower(), ()):
        if k.lower().replace('_', ' ').replace('-', ' ') == wl:
            cands.append(k)
    cands.extend([want, want.title(), want.replace('_', ' ').title(), want.upper()])
    for k in cands:
        try:
            sock.default_value = k
            return k
        except Exception:
            continue
    return None


def _setup_glare(sc, opt=None):
    opt = dict(opt or {'threshold': 1.0, 'strength': 1.0, 'size': 0.65, 'smoothness': 0.30})
    try:
        ng = bpy.data.node_groups.new('FXCompositor', 'CompositorNodeTree')
        ng.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
        rl = ng.nodes.new('CompositorNodeRLayers')
        gl = ng.nodes.new('CompositorNodeGlare')
        out = ng.nodes.new('NodeGroupOutput')
        ng.links.new(rl.outputs['Image'], gl.inputs['Image'])
        ng.links.new(gl.outputs['Image'], out.inputs[0])
        got = _set_enum_socket(gl, 'Type', 'Fog Glow')
        for k, v in (('Threshold', opt['threshold']), ('Strength', opt['strength']),
                     ('Size', opt['size']), ('Smoothness', opt['smoothness'])):
            sock = gl.inputs.get(k)
            if sock is not None:
                try:
                    sock.default_value = v
                except Exception:
                    pass
        sc.compositing_node_group = ng
        sc.render.use_compositing = True
        p('  合成器 Glare: %s' % (got or '直连（无 Fog Glow）'))
        return got
    except Exception as exc:
        p('  合成器 Glare 失败（忽略）: %s' % exc)
        return None


def _setup_render(sc, w=800, h=450):
    for eng in ('BLENDER_EEVEE', 'BLENDER_EEVEE_NEXT', 'CYCLES'):
        try:
            sc.render.engine = eng
            break
        except Exception:
            continue
    sc.render.resolution_x = w
    sc.render.resolution_y = h
    sc.render.resolution_percentage = 100
    sc.render.film_transparent = False
    sc.render.image_settings.file_format = 'PNG'
    try:
        sc.eevee.taa_render_samples = 24
    except Exception:
        pass
    try:
        sc.view_settings.view_transform = 'AgX'
        sc.view_settings.look = 'AgX - Punchy'
    except Exception:
        pass
    try:
        sc.view_settings.exposure = 0.0
    except Exception:
        pass
    _setup_glare(sc)
    p('  渲染: %s / %dx%d / AgX=%s' % (sc.render.engine, w, h,
                                       getattr(sc.view_settings, 'view_transform', '?')))


def _build_stage(sc):
    """搭一个最小的验收舞台：地面 + 假角色 + 骨架 + 三点光。"""
    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    except Exception:
        pass
    sc = bpy.context.scene
    sc.frame_start = 1
    sc.frame_end = 24

    # world
    world = bpy.data.worlds.new('FXTestWorld')
    sc.world = world
    try:
        world.use_nodes = True
        bg = world.node_tree.nodes.get('Background')
        if bg is not None:
            bg.inputs[0].default_value = (0.010, 0.016, 0.030, 1.0)
            bg.inputs[1].default_value = 1.0
    except Exception:
        pass

    # 地面
    gmat = bpy.data.materials.new('FXTestGround')
    gmat.use_nodes = True
    _put(_bsdf(gmat), 'Base Color', (0.155, 0.165, 0.185, 1.0))
    _put(_bsdf(gmat), 'Roughness', 0.75)
    _object_mode()
    bpy.ops.mesh.primitive_plane_add(size=40.0, location=(0.0, 0.0, 0.0))
    ground = bpy.context.view_layer.objects.active or bpy.context.object
    ground.name = 'FXTestGround'
    ground.data.materials.append(gmat)

    # 假角色
    cmat = bpy.data.materials.new('FXTestCharMat')
    cmat.use_nodes = True
    _put(_bsdf(cmat), 'Base Color', (0.13, 0.18, 0.22, 1.0))
    _put(_bsdf(cmat), 'Roughness', 0.65)
    parts = (('Body', (0.55, 0.28, 0.78), (0.0, 0.0, 1.02)),
             ('Head', (0.50, 0.50, 0.50), (0.0, 0.0, 1.72)),
             ('LegL', (0.20, 0.22, 0.63), (-0.135, 0.0, 0.31)),
             ('LegR', (0.20, 0.22, 0.63), (0.135, 0.0, 0.31)),
             ('ArmL', (0.18, 0.22, 0.66), (-0.365, 0.0, 1.02)),
             ('ArmR', (0.18, 0.22, 0.66), (0.365, 0.0, 1.02)))
    for nm, size, loc in parts:
        box('FXTest' + nm, size, loc, cmat)

    # 骨架（自带 eye.* 骨骼，验证 glow_eyes 的取位分支）
    arm_data = bpy.data.armatures.new('FXTestArmData')
    arm = bpy.data.objects.new('FXTestArm', arm_data)
    _link(arm)
    bpy.context.view_layer.objects.active = arm
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        head = arm_data.edit_bones.new('head')
        head.head = (0.0, 0.0, 1.47)
        head.tail = (0.0, 0.0, 1.97)
        for side, x in (('L', -0.10), ('R', 0.10)):
            eb = arm_data.edit_bones.new('eye.' + side)
            eb.head = (x, -0.245, 1.63)
            eb.tail = (x, -0.345, 1.63)
            eb.parent = head
        bpy.ops.object.mode_set(mode='OBJECT')
        p('  测试骨架: %s' % ', '.join(b.name for b in arm_data.bones))
    except Exception as exc:
        p('  骨架搭建失败（忽略）: %s' % exc)
        arm = None
    _object_mode()

    # 灯
    sun_d = bpy.data.lights.new('FXTestSun', 'SUN')
    sun_d.energy = 3.0
    sun = bpy.data.objects.new('FXTestSun', sun_d)
    sun.rotation_euler = (math.radians(54), 0.0, math.radians(-38))
    _link(sun)

    fill_d = bpy.data.lights.new('FXTestFill', 'AREA')
    fill_d.energy = 300.0
    fill_d.size = 4.0
    fill = bpy.data.objects.new('FXTestFill', fill_d)
    fill.location = (-3.2, -4.6, 3.4)
    _link(fill)
    d = Vector((0.0, 0.0, 1.1)) - fill.location
    fill.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()

    rim_d = bpy.data.lights.new('FXTestRim', 'AREA')
    rim_d.energy = 300.0
    rim_d.size = 3.0
    rim = bpy.data.objects.new('FXTestRim', rim_d)
    rim.location = (2.0, 3.4, 3.2)
    _link(rim)
    d = Vector((0.0, 0.0, 1.2)) - rim.location
    rim.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()

    ctx = {'arm': arm, 'total': 24, 'fps': 24.0,
           'base': Vector((0.0, 0.0, 0.0)),
           'FV': Vector((0.0, -1.0, 0.0)),
           'UP': Vector((0.0, 0.0, 1.0))}
    return sc, ctx


def _image_stats(path):
    try:
        img = bpy.data.images.load(path)
    except Exception:
        return None
    try:
        px = list(img.pixels)
    finally:
        pass
    n = len(px) // 4
    if n <= 0:
        try:
            bpy.data.images.remove(img)
        except Exception:
            pass
        return None
    step = 4
    tot = 0.0
    mx = 0.0
    lit = 0
    cnt = 0
    for i in range(0, n, step):
        r, g, b = px[i * 4], px[i * 4 + 1], px[i * 4 + 2]
        v = (r + g + b) / 3.0
        tot += v
        cnt += 1
        if v > mx:
            mx = v
        if v > 0.06:
            lit += 1
    try:
        bpy.data.images.remove(img)
    except Exception:
        pass
    if cnt == 0:
        return None
    return {'mean': tot / cnt, 'max': mx, 'lit': lit / float(cnt)}


def _render_frame(sc, path, frame):
    sc.frame_set(int(frame))
    bpy.context.view_layer.update()
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    return os.path.exists(path)


def _selftest():
    out_dir = os.environ.get('FX_OUT') or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                       'fx_selftest')
    try:
        os.makedirs(out_dir, exist_ok=True)
    except Exception:
        pass

    p('=' * 70)
    p('fx_kit %s 自测  FX_OUT=%s' % (FX_VERSION, out_dir))
    p('Blender %s' % bpy.app.version_string)

    sc, ctx = _build_stage(bpy.context.scene)
    _setup_render(sc)

    rows = []
    fails = []
    for idx, name in enumerate(_TEST_ORDER, 1):
        cleanup_fx()
        _cam_for(sc, name)
        spec = dict(FX_DEFAULTS.get(name) or {})
        t0 = time.time()
        try:
            made = FX_REGISTRY[name](sc, ctx, spec)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            fails.append('%s: %s' % (name, exc))
            p('  [%d/7] %-10s FAIL  %s' % (idx, name, exc))
            continue
        dt = time.time() - t0
        img = os.path.join(out_dir, 'fx_%02d_%s.png' % (idx, name))
        _render_frame(sc, img, 12)
        stats = _image_stats(img)
        if stats is None:
            stats = {'mean': 0.0, 'max': 0.0, 'lit': 0.0}
        rows.append((name, len(made), stats, dt, os.path.basename(img)))
        p('  [%d/7] %-10s ok  %3d obj  %5.1fs  mean=%.4f max=%.3f lit=%.3f'
          % (idx, name, len(made), dt, stats['mean'], stats['max'], stats['lit']))

        extra = _EXTRA_FRAMES.get(name)
        if extra:
            f_extra, note = extra
            img2 = os.path.join(out_dir, 'fx_%02d_%s_f%d.png' % (idx, name, f_extra))
            _render_frame(sc, img2, f_extra)
            st2 = _image_stats(img2) or {'mean': 0.0, 'max': 0.0, 'lit': 0.0}
            p('        额外帧 %d（%s）mean=%.4f max=%.3f lit=%.3f'
              % (f_extra, note, st2['mean'], st2['max'], st2['lit']))

    p('-' * 70)
    p('%-10s %6s %8s %8s %8s %8s  %s' % ('效果', '对象', 'mean', 'max', 'lit%', '耗时s', '图片'))
    for name, nobj, st, dt, fn in rows:
        p('%-10s %6d %8.4f %8.3f %8.1f %8.1f  %s'
          % (name, nobj, st['mean'], st['max'], st['lit'] * 100.0, dt, fn))
    p('-' * 70)
    if fails:
        p('SELFTEST FAIL: %d/%d 出错 -> %s' % (len(fails), len(_TEST_ORDER), '; '.join(fails)))
    else:
        p('SELFTEST OK: 7/7 特效无异常，每张都在第 12 帧出图')
    p('输出目录: %s' % out_dir)
    return 0 if not fails else 1


if os.environ.get('FX_SELFTEST') == '1':
    _selftest()
