# -*- coding: utf-8 -*-
"""anim_kit —— 数据驱动的动画内核。

为什么要有它
------------
原来每加一种动画就要复制一份 animate_xxx.py（各 300~580 行），里面 70% 是
一模一样的脚手架：找骨架、打关键帧、建自发光材质、搭相机跟拍、配渲染/Glare、
写日志。改一处配色要改三份，加一种动作要再抄一份。

现在：**代码只写一次，动画写成 JSON**。

    animate_recipe.py（薄壳，20 行）
        └── anim_kit.build(recipe)
                ├── 动作  MOTIONS[recipe['motion']['type']]
                ├── 武器  WEAPONS[recipe['weapon']['type']]
                ├── 特效  FX[item['type']]           ← recipe['fx'] 是一个列表
                ├── 相机  build_camera(recipe['camera'])
                ├── 灯光  follow_lights(recipe['lights'])
                └── 渲染  setup_render(recipe['render'])

什么时候仍然要写代码（老实说）
------------------------------
- 全新的**运动形状**（不是"沿轴飞"而是"绕柱子螺旋"）→ 在 MOTIONS 里加一个函数
- 全新的**特效种类**（不是环/波/拖尾/风线）→ 在 FX 里加一个函数
- 全新的**武器姿态** → 在 WEAPONS 里加一个

但只要这三类够用，新增一支片子 = 写一个 JSON，**零代码**。这就是复用。

配方格式见 py/blender/presets/yujian.json；键名即上文，数值单位：
角度用度、距离用格（=1 方块）、时间用帧。
"""
import json
import math
import os
import random
import sys

import bpy
import mathutils as mu

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

LOG = []


def p(s=''):
    LOG.append(str(s))
    print(s)


# ═══════════════════════════════════════════════════════════════
# 0. 小工具
# ═══════════════════════════════════════════════════════════════
def smoothstep(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def lerp(a, b, t):
    return a + (b - a) * t


def track(points, f):
    """(帧, 值) 折线 → smoothstep 插值。点必须按帧升序。"""
    if not points:
        return 0.0
    if f <= points[0][0]:
        return points[0][1]
    if f >= points[-1][0]:
        return points[-1][1]
    for i in range(len(points) - 1):
        f0, v0 = points[i]
        f1, v1 = points[i + 1]
        if f0 <= f <= f1:
            return lerp(v0, v1, smoothstep((f - f0) / max(1e-6, (f1 - f0))))
    return points[-1][1]


def offset_track(points, add):
    return [(f, v + add) for f, v in points]


def scale_track(points, k):
    return [(f, v * k) for f, v in points]


def unit_vec(a):
    return mu.Vector(a).normalized()


def vec(a, default=(0.0, 0.0, 0.0)):
    if a is None:
        return mu.Vector(default)
    return mu.Vector([float(x) for x in a])


def color(c, default=(1.0, 1.0, 1.0)):
    if c is None:
        return default
    if isinstance(c, str):
        named = {'cyan': (0.10, 0.75, 1.0), 'white': (1.0, 1.0, 1.0),
                 'gold': (1.0, 0.78, 0.25), 'violet': (0.55, 0.35, 1.0),
                 'red': (1.0, 0.25, 0.2), 'green': (0.3, 1.0, 0.45)}
        return named.get(c.lower(), default)
    return tuple(float(x) for x in c)


# ═══════════════════════════════════════════════════════════════
# 1. 场景上下文
# ═══════════════════════════════════════════════════════════════
def scene_ctx(total, fps):
    """抓住骨架、基准位置与朝向。封面工程里只有一个 ARMATURE。"""
    sc = bpy.context.scene
    sc.render.fps = fps
    sc.frame_start = 1
    sc.frame_end = total
    arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    if arm is None:
        raise RuntimeError('工程里没有骨架（ARMATURE）—— 这不是 mc_shoot 出的封面工程？')
    if arm.parent is not None:
        mw = arm.matrix_world.copy()
        arm.parent = None
        arm.matrix_world = mw
    base = arm.matrix_world.translation.copy()
    return {'sc': sc, 'arm': arm, 'base': base,
            'base_rz': arm.rotation_euler.z, 'total': total, 'fps': fps}


# ═══════════════════════════════════════════════════════════════
# 2. 材质 / 几何
# ═══════════════════════════════════════════════════════════════
def emis_mat(name, col, strength=2.2, rough=0.4):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes.get("Principled BSDF")
    b.inputs["Base Color"].default_value = (*col, 1)
    b.inputs["Roughness"].default_value = rough
    try:
        b.inputs["Emission Color"].default_value = (*col, 1)
        b.inputs["Emission Strength"].default_value = strength
    except Exception:
        pass
    return m


def box(name, size, loc, material, parent=None):
    sx, sy, sz = size
    me = bpy.data.meshes.new(name)
    vs = [(x * sx / 2, y * sy / 2, z * sz / 2)
          for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    me.from_pydata(vs, [], [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
                            (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)])
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    ob.data.materials.append(material)
    bpy.context.scene.collection.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def torus(name, loc, major, minor, material, seg=(96, 10)):
    bpy.ops.mesh.primitive_torus_add(location=loc, major_radius=major,
                                     minor_radius=minor,
                                     major_segments=int(seg[0]),
                                     minor_segments=int(seg[1]))
    ob = bpy.context.active_object
    ob.name = name
    ob.data.materials.append(material)
    return ob


def ico(name, loc, radius, material, subdivisions=2):
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=int(subdivisions),
                                          radius=radius, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    ob.data.materials.append(material)
    return ob


# ═══════════════════════════════════════════════════════════════
# 3. 关键帧
# ═══════════════════════════════════════════════════════════════
def key(ob, path, keys, degrees=False, data_block=False):
    """keys: [(帧, 值)]。rotation_euler 的值按【度】给。"""
    target = ob if data_block else ob
    sc = bpy.context.scene
    for f, v in keys:
        f = int(f)
        sc.frame_set(f)
        if path == 'scale':
            target.scale = v
        elif path == 'rotation_euler':
            target.rotation_euler = [math.radians(x) for x in v] if degrees else v
        else:
            setattr(target, path, v)
        target.keyframe_insert(path, frame=f)


def key_data(data_block, path, keys):
    sc = bpy.context.scene
    for f, v in keys:
        f = int(f)
        sc.frame_set(f)
        setattr(data_block, path, v)
        data_block.keyframe_insert(path, frame=f)


# ═══════════════════════════════════════════════════════════════
# 4. 动作：把"运动形状"做成可插拔的函数
# ═══════════════════════════════════════════════════════════════
def _terms(f, terms):
    """terms: [[起点帧, 时长, 权重], ...] → Σ 权重·smoothstep((f-起点)/时长)"""
    s = 0.0
    for start, dur, w in terms:
        s += w * smoothstep((f - start) / float(max(1e-6, dur)))
    return s


def motion_fly(ctx, m):
    """沿一条水平方向飞（可带转身/前倾/侧倾）。

    m 的键：az 方位角、lift 升空、travel 前移、progress [起,止]、
            turn {amount,start,end}、tilt/bank {scale, terms}、
            hop {amount,start,dur}、pre {amount,start,dur,fade}
    """
    az = math.radians(float(m.get('az', 20)))
    FV = mu.Vector((math.cos(az), math.sin(az), 0.0))   # 前进方向（水平）
    UP = mu.Vector((0.0, 0.0, 1.0))
    base = ctx['base']
    lift = float(m.get('lift', 12))
    travel = float(m.get('travel', 20))
    pr = m.get('progress', [58, 196])
    hop = m.get('hop') or {}
    pre = m.get('pre') or {}
    turn = m.get('turn') or {}
    tilt_cfg = m.get('tilt') or {'scale': 0.0, 'terms': []}
    bank_cfg = m.get('bank') or {'scale': 0.0, 'terms': []}

    def char_world(f):
        u = smoothstep((f - pr[0]) / float(max(1e-6, pr[1] - pr[0])))
        hop_v = float(hop.get('amount', 0.0)) * smoothstep(
            (f - hop.get('start', 46)) / float(max(1e-6, hop.get('dur', 10))))
        # pre 是一个"先下蹲再回正"的脉冲：升起后按 fade 淡出
        pre_v = (float(pre.get('amount', 0.0))
                 * smoothstep((f - pre.get('start', 40)) / float(max(1e-6, pre.get('dur', 18))))
                 * (1.0 - smoothstep((f - pr[0]) / float(max(1e-6, pre.get('fade', 24))))))
        return base + FV * (travel * u) + UP * (lift * u + pre_v + hop_v)

    def yaw(f):
        amt = math.radians(float(turn.get('amount', 0)))
        f0, f1 = turn.get('start', 46), turn.get('end', 66)
        return ctx['base_rz'] + amt * smoothstep((f - f0) / float(max(1e-6, f1 - f0)))

    def tilt(f):
        return float(tilt_cfg.get('scale', 0)) * _terms(f, tilt_cfg.get('terms') or [])

    def bank(f):
        return float(bank_cfg.get('scale', 0)) * _terms(f, bank_cfg.get('terms') or [])

    return {'char_world': char_world, 'yaw': yaw, 'tilt': tilt, 'bank': bank,
            'FV': FV, 'UP': UP, 'axis': UP.cross(FV).normalized()}


MOTIONS = {'fly': motion_fly}


# ═══════════════════════════════════════════════════════════════
# 5. 武器
# ═══════════════════════════════════════════════════════════════
def weapon_ridden_sword(ctx, w, mo):
    """把手里那把剑复制一把、放大、横在脚下当飞行器；原剑溶进光里。"""
    arm, sc = ctx['arm'], ctx['sc']
    src = next((o for o in bpy.data.objects
                if o.type == 'MESH' and o.name.startswith(w.get('source', 'Sword'))), None)
    if src is None:
        raise RuntimeError('工程里没找到 %s* 手持剑 —— 御剑类预设需要它'
                           % w.get('source', 'Sword'))
    p('    手持剑 %s   parent_bone=%s' % (src.name, src.parent_bone))

    fs = src.copy()
    fs.data = src.data.copy()
    fs.name = 'FlyingSword'
    sc.collection.objects.link(fs)

    bb = [mu.Vector(c) for c in src.bound_box]
    MID = mu.Vector((
        (min(v.x for v in bb) + max(v.x for v in bb)) / 2,
        (min(v.y for v in bb) + max(v.y for v in bb)) / 2,
        (min(v.z for v in bb) + max(v.z for v in bb)) / 2))

    local_fwd = str(w.get('local_fwd', '-y')).lower()
    a = unit_vec((0.0, -1.0, 0.0) if local_fwd == '-y' else (0.0, 1.0, 0.0))
    c = mu.Vector((0.0, 0.0, 1.0))
    b = c.cross(a).normalized()
    R_sw = (mu.Matrix(((a.x, b.x, c.x), (a.y, b.y, c.y), (a.z, b.z, c.z)))
            @ mu.Matrix.Rotation(math.radians(float(w.get('roll', -45.0))), 3, 'Z'))

    sw_scale = float(w.get('scale', 2.4))
    # 刀身是薄片：均匀放大会变成"一根线"，只沿刀面法线（网格本地 Z）加厚才有体积
    thick = float(w.get('thick', 2.6))
    desired = vec(w.get('desired', [0.0, 0.30, 0.02]))
    local = (mu.Matrix.Translation(desired) @ R_sw.to_4x4()
             @ mu.Matrix.Scale(sw_scale, 4)
             @ mu.Matrix.Diagonal((1.0, 1.0, thick, 1.0))
             @ mu.Matrix.Translation(-MID))
    # ⚠️ 坑 R31：parent 到「已被打关键帧」的骨架时，matrix_parent_inverse 必须是
    #    Identity + matrix_local，否则剑会被推到几十格以外（v1 里整把剑消失）。
    fs.parent = arm
    fs.parent_type = 'OBJECT'
    fs.matrix_parent_inverse = mu.Matrix.Identity(4)
    fs.matrix_local = local

    glow = w.get('glow') or {}
    if glow:
        gcol = color(glow.get('color', [0.45, 0.92, 1.0]))
        gstr = float(glow.get('strength', 2.6))
        for i, slot in enumerate(fs.material_slots):
            if slot.material is None:
                continue
            m2 = slot.material.copy()
            m2.name = 'FlyingSwordMat%d' % i
            m2.use_nodes = True
            bs = next((n for n in m2.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
            if bs is not None:
                try:
                    bs.inputs['Emission Color'].default_value = (*gcol, 1.0)
                    bs.inputs['Emission Strength'].default_value = gstr
                except Exception:
                    pass
            slot.material = m2

    edge = w.get('edge') or {}
    if edge:
        ecol = color(edge.get('color', [0.30, 0.85, 1.0]))
        mat_edge = emis_mat('BladeEdge', ecol, float(edge.get('strength', 7.0)), 0.2)
        eo = box('BladeEdge', edge.get('size', [1.50, 0.050, 0.014]),
                 (0, 0, 0), mat_edge, parent=fs)
        off = vec(edge.get('offset', [0, 0, 0.05]))
        eo.location = (MID.x + off.x, MID.y + off.y, MID.z + off.z)
        eo.rotation_euler = (0, 0, math.radians(float(edge.get('rot', 45.0))))

    key(fs, 'scale', [(f, (s,) * 3) for f, s in w.get('spawn_scale',
                                                      [[1, 0.01], [54, 1.0], [240, 1.0]])])
    zkeys = w.get('spawn_z', [[1, -2.8], [54, 0.02], [240, 0.02]])
    key(fs, 'location', [(f, (desired.x, desired.y, z)) for f, z in zkeys])
    if w.get('hand_scale'):
        key(src, 'scale', [(f, (s,) * 3) for f, s in w['hand_scale']])

    flash = w.get('flash') or {}
    if flash:
        fcol = color(flash.get('color', [0.55, 0.9, 1.0]))
        fo = box('MergeFlash', flash.get('size', [0.4, 0.4, 0.4]),
                 ctx['base'] + vec(flash.get('at', [0, 0, 1.0])),
                 emis_mat('MergeFlash', fcol, float(flash.get('strength', 16.0))))
        key(fo, 'scale', [(f, (s,) * 3) for f, s in flash.get(
            'keys', [[1, 0.01], [48, 1.7], [56, 0.01]])])

    p('    剑长 ≈%.1f 格   缩放 %.2f   加厚 ×%.1f'
      % (max(src.dimensions) * sw_scale, sw_scale, thick))
    return fs


WEAPONS = {'ridden_sword': weapon_ridden_sword}


# ═══════════════════════════════════════════════════════════════
# 6. 特效（配方里 fx 是一个列表，每项按 type 分发）
# ═══════════════════════════════════════════════════════════════
def fx_rune_ring(ctx, it, mo):
    base = ctx['base']
    col = color(it.get('color', [0.04, 0.55, 1.0]))
    mat = emis_mat(it.get('name', 'RuneMat'), col, float(it.get('strength', 1.1)))
    z = float(it.get('z', 0.05))
    ob = torus(it.get('name', 'RuneRing'), (base.x, base.y, base.z + z),
               float(it.get('major', 1.3)), float(it.get('minor', 0.045)), mat,
               it.get('segments', [96, 10]))
    key(ob, 'scale', [(f, (sx, sy, sz)) for f, sx, sy, sz in it.get(
        'scale', [[1, 0.2, 0.2, 0.10], [58, 1.3, 1.3, 0.10], [64, 0.01, 0.01, 0.01]])])
    key(ob, 'rotation_euler', [(f, (0, 0, deg)) for f, deg in it.get('spin', [[1, 0], [64, 210]])],
        degrees=True)
    return ob


def fx_shockwave(ctx, it, mo):
    """地面气浪：在指定帧从脚下炸开一圈。scale 键用【相对帧】(0 就是该帧)。"""
    col = color(it.get('color', 'cyan'))
    for i, f in enumerate(it.get('frames', [58, 96, 134])):
        mat = emis_mat('ShockMat%d' % i, col, float(it.get('strength', 4.0)))
        ob = torus('ShockWave%d' % i, (0, 0, 0), float(it.get('major', 1.0)),
                   float(it.get('minor', 0.04)), mat, it.get('segments', [72, 8]))
        w0 = mo['char_world'](f)
        z = float(it.get('z', 0.06))
        key(ob, 'location', [(1, (w0.x, w0.y, w0.z + z)),
                             (ctx['total'], (w0.x, w0.y, w0.z + z))])
        keys = [(1, (0.01,) * 3)]
        for rel, s in it.get('scale', [[0, 0.22], [20, 2.4], [26, 0.01]]):
            ff = max(1, int(f + rel))
            keys.append((ff, (s, s, float(it.get('flat', 0.45)) if rel else s)))
        keys.append((ctx['total'], (0.01,) * 3))
        keys.sort(key=lambda k: k[0])
        key(ob, 'scale', keys)


def fx_trail(ctx, it, mo):
    """剑尾光带：沿飞行轨迹延迟采样，越靠后越细。"""
    total = ctx['total']
    n = int(it.get('n', 18))
    delay = float(it.get('delay', 0.45))
    start = int(it.get('start', 66))
    col = color(it.get('color', [0.25, 0.75, 1.0]))
    mat = emis_mat('TailGlow', col, float(it.get('strength', 0.55)), 0.3)
    r = float(it.get('radius', 0.032))
    rmin = float(it.get('min', 0.006))
    off = it.get('offset', [-0.22, -0.05])
    FV, UP = mo['FV'], mo['UP']
    for i in range(n):
        gg = ico('Trail%02d' % i, (ctx['base'].x, ctx['base'].y, ctx['base'].z),
                 1.0, mat, it.get('subdivisions', 2))
        s = r * (1.0 - i / float(n)) + rmin
        key(gg, 'scale', [(1, (0.01,) * 3), (start, (0.01,) * 3),
                          (start + 6, (s, s, s)), (total, (s, s, s))])
        for f in range(start - 2, total + 1, 2):
            bpy.context.scene.frame_set(f)
            gg.location = (mo['char_world'](max(1, f - i * delay))
                           + FV * float(off[0]) + UP * float(off[1]))
            gg.keyframe_insert('location', frame=f)


def fx_streaks(ctx, it, mo):
    """高速风线：细长盒子沿飞行方向拉长下落。"""
    total = ctx['total']
    random.seed(int(it.get('seed', 11)))
    col = color(it.get('color', [0.82, 0.95, 1.0]))
    mat = emis_mat('WindStreak', col, float(it.get('strength', 0.45)), 0.2)
    mid = mo['char_world'](int(it.get('reference', 130)))
    d = it.get('dir', [0.42, -1.0])
    STREAK = (mo['FV'] * float(d[0]) + mo['UP'] * float(d[1])).normalized()
    rot = STREAK.to_track_quat('Z', 'Y').to_euler()
    rad_r = it.get('rad', [1.6, 8.5])
    zoff_r = it.get('zoff', [-6.0, 24.0])
    len_r = it.get('len', [3.0, 7.5])
    st_r = it.get('start', [52, 18])
    travel = float(it.get('travel', 65.0))
    for i in range(int(it.get('n', 24))):
        ang = random.uniform(0, math.tau)
        rad = random.uniform(rad_r[0], rad_r[1])
        zoff = random.uniform(zoff_r[0], zoff_r[1])
        w = box('Wind%02d' % i, (0.03, 0.03, random.uniform(len_r[0], len_r[1])),
                (mid.x, mid.y, mid.z), mat)
        w.rotation_euler = rot
        pos0 = mu.Vector((mid.x + math.cos(ang) * rad,
                          mid.y + math.sin(ang) * rad, mid.z + zoff))
        f0 = float(st_r[0]) + random.uniform(0, float(st_r[1]))
        key(w, 'location', [(1, pos0), (f0, pos0),
                            (f0 + 150, pos0 + STREAK * travel),
                            (total, pos0 + STREAK * travel)])
        key(w, 'scale', [(1, (0.01,) * 3), (f0, (0.01,) * 3),
                         (f0 + 8, (1.0,) * 3), (total, (1.0,) * 3)])


def fx_particles(ctx, it, mo):
    """通用粒子团：围绕角色做螺旋/喷发（给新片子留的口子，JSON 可配）。"""
    total = ctx['total']
    random.seed(int(it.get('seed', 7)))
    col = color(it.get('color', 'gold'))
    mat = emis_mat('Particle', col, float(it.get('strength', 3.0)))
    n = int(it.get('n', 20))
    rad = it.get('radius', [0.6, 2.4])
    spin = float(it.get('spin', 540.0))
    rise = float(it.get('rise', 6.0))
    start = int(it.get('start', 1))
    for i in range(n):
        ang0 = random.uniform(0, math.tau)
        r = random.uniform(rad[0], rad[1])
        gg = ico('Particle%02d' % i, (ctx['base'].x, ctx['base'].y, ctx['base'].z),
                 float(it.get('size', 0.06)), mat, it.get('subdivisions', 1))
        key(gg, 'scale', [(1, (0.01,) * 3), (start, (0.01,) * 3),
                          (start + 4, (1, 1, 1)), (total, (1, 1, 1))])
        for f in range(start, total + 1, 2):
            t = (f - start) / float(max(1, total - start))
            a = ang0 + math.radians(spin) * t
            c = mo['char_world'](f)
            gg.location = c + mu.Vector((math.cos(a) * r, math.sin(a) * r,
                                         rise * t + random.uniform(-0.2, 0.2)))
            gg.keyframe_insert('location', frame=f)


FX = {'rune_ring': fx_rune_ring, 'shockwave': fx_shockwave,
      'trail': fx_trail, 'streaks': fx_streaks, 'particles': fx_particles}


# ═══════════════════════════════════════════════════════════════
# 7. 相机 / 灯光
# ═══════════════════════════════════════════════════════════════
def build_camera(ctx, cam, mo, override=None):
    """轨道跟拍：方位角/距离/焦距/抬头比例 四条轨道，每 key_step 帧烘焙一次。"""
    sc = ctx['sc']
    CAM = sc.camera
    if CAM is None:
        raise RuntimeError('场景里没有摄影机')
    ov = override or {}
    az = offset_track(cam['az'], ov.get('az_add', 0.0))
    dist = scale_track(cam['dist'], ov.get('d_scale', 1.0))
    lens = scale_track(cam['lens'], ov.get('lens_scale', 1.0))
    rise = [(f, max(0.0, min(1.0, v + ov.get('rise_add', 0.0))))
            for f, v in cam['rise']]
    z_base = float(cam.get('z_base', 2.30)) + ov.get('z_add', 0.0)
    aim_h = float(cam.get('aim_h', 1.00))
    step = int(cam.get('key_step', 2))
    dof_f = float(cam.get('dof', 2.4) or 0.0)
    az0, az1 = cam['az'][0][1], cam['az'][-1][1]

    aim_empty = bpy.data.objects.new('AimTarget', None)
    aim_empty.empty_display_size = 0.3
    sc.collection.objects.link(aim_empty)

    UP = mo['UP']
    base_z = ctx['base'].z
    for f in range(1, ctx['total'] + 1, step):
        c = mo['char_world'](f)
        a = math.radians(track(az, f))
        d = track(dist, f)
        zc = base_z + z_base + (c.z - base_z) * track(rise, f)
        pos = mu.Vector((c.x + math.cos(a) * d, c.y + math.sin(a) * d, zc))
        aim = c + UP * aim_h
        sc.frame_set(f)
        CAM.location = pos
        CAM.rotation_euler = (aim - pos).to_track_quat('-Z', 'Y').to_euler()
        CAM.data.lens = track(lens, f)
        CAM.keyframe_insert('location', frame=f)
        CAM.keyframe_insert('rotation_euler', frame=f)
        CAM.data.keyframe_insert('lens', frame=f)
        aim_empty.location = aim
        aim_empty.keyframe_insert('location', frame=f)
        if dof_f > 0:
            try:
                CAM.data.dof.use_dof = True
                CAM.data.dof.aperture_fstop = dof_f
                CAM.data.dof.focus_distance = (aim - pos).length
                CAM.data.dof.keyframe_insert('focus_distance', frame=f)
            except Exception:
                pass
    p('    视线 %.0f°→%.0f°、距离 %.1f→%.1f、焦距 %.0f→%.0fmm'
      % (az0, az1, cam['dist'][0][1], cam['dist'][-1][1],
         cam['lens'][0][1], cam['lens'][-1][1]))
    return {'cam': CAM, 'aim': aim_empty}


def follow_lights(ctx, spec, mo, cam_info):
    """三点光/点光跟着角色走并 TRACK_TO 胸口；另加一盏相机侧补光。"""
    sc = ctx['sc']
    base = ctx['base']
    UP = mo['UP']
    aim_empty = cam_info['aim']
    fill_spec = spec.get('fill') or {}
    fill_ob = None
    if fill_spec and float(fill_spec.get('energy', 0)) > 0:
        li = bpy.data.lights.new('FlightFill', type='AREA')
        li.energy = float(fill_spec.get('energy', 900.0))
        li.size = float(fill_spec.get('size', 6.0))
        try:
            li.color = color(fill_spec.get('color', [0.86, 0.94, 1.0]))
        except Exception:
            pass
        fill_ob = bpy.data.objects.new('FlightFill', li)
        sc.collection.objects.link(fill_ob)

    step = int(spec.get('key_step', 4))
    n_lit = 0
    for ob in list(bpy.data.objects):
        if ob.type != 'LIGHT' or ob.data.type not in ('AREA', 'POINT', 'SPOT'):
            continue
        if ob.name == 'FlightFill':
            continue
        off = ob.matrix_world.translation - base
        for f in range(1, ctx['total'] + 1, step):
            sc.frame_set(f)
            ob.location = mo['char_world'](f) + off
            ob.keyframe_insert('location', frame=f)
        con = ob.constraints.new('TRACK_TO')
        con.target = aim_empty
        con.track_axis = 'TRACK_NEGATIVE_Z'
        con.up_axis = 'UP_Y'
        n_lit += 1

    if fill_ob is not None:
        forward = float(fill_spec.get('forward', 2.2))
        up_k = float(fill_spec.get('up', 1.2))
        CAM = cam_info['cam']
        for f in range(1, ctx['total'] + 1, step):
            sc.frame_set(f)
            c = mo['char_world'](f)
            pos = CAM.location.copy()
            aim = c + UP * 1.0
            fpos = pos + (aim - pos).normalized() * forward + UP * up_k
            fill_ob.location = fpos
            fill_ob.rotation_euler = (aim - fpos).to_track_quat('-Z', 'Y').to_euler()
            fill_ob.keyframe_insert('location', frame=f)
            fill_ob.keyframe_insert('rotation_euler', frame=f)
    p('    %d 盏灯跟随角色并对准胸口%s'
      % (n_lit, '，另有相机侧补光' if fill_ob else ''))


# ═══════════════════════════════════════════════════════════════
# 8. 渲染设置
# ═══════════════════════════════════════════════════════════════
def setup_render(ctx, r, out_dir):
    sc = ctx['sc']
    res = str(r.get('res', '960x540')).replace('×', 'x')
    samples = int(r.get('samples', 32))
    for cand in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
        try:
            sc.render.engine = cand
            break
        except Exception:
            continue
    try:
        w, h = [int(v) for v in res.lower().split('x')]
    except Exception:
        w, h = 960, 540
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.resolution_percentage = 100
    sc.render.image_settings.file_format = 'PNG'
    sc.render.film_transparent = False
    try:
        sc.eevee.taa_render_samples = samples
    except Exception:
        pass

    vt = None
    try:
        from _compat import set_view_transform
        vt = set_view_transform(sc, prefer=r.get('view_transform', 'AgX'))
    except Exception as e:
        p('    !! 色彩管理兼容层不可用: %s' % e)
    if not vt:
        for v in ('AgX', 'Filmic', 'Standard'):
            try:
                sc.view_settings.view_transform = v
                vt = v
                break
            except Exception:
                continue
    try:
        if r.get('look'):
            sc.view_settings.look = r['look']
    except Exception:
        pass
    if r.get('motion_blur'):
        try:
            sc.render.use_motion_blur = True
            sc.render.motion_blur_shutter = float(r['motion_blur'])
            sc.render.motion_blur_position = 'CENTER'
        except Exception as e:
            p('    !! 运动模糊不可用: %s' % e)
    if r.get('exposure') is not None:
        sc.view_settings.exposure = float(r['exposure'])

    # 天空亮度关键帧（第一项给 null = 保持工程原值）
    sky = r.get('sky') or []
    if sky:
        try:
            bg = next(n for n in sc.world.node_tree.nodes if n.type == 'BACKGROUND')
            for f, v in sky:
                if v is None:
                    v = bg.inputs['Strength'].default_value
                bg.inputs['Strength'].default_value = float(v)
                bg.inputs['Strength'].keyframe_insert('default_value', frame=int(f))
        except Exception as e:
            p('    !! 天空亮度关键帧失败: %s' % e)

    # EEVEE 没有 bloom，用合成器 Glare(Fog Glow) 做溢光
    gl_spec = r.get('glare') or {}
    if gl_spec:
        try:
            nt = None
            for attr in ('node_tree', 'compositing_node_group'):
                cand = getattr(sc, attr, None)
                if cand is not None and hasattr(cand, 'nodes'):
                    nt = cand
                    break
            if nt is None:
                raise RuntimeError('这个版本没有可用的合成器节点树')
            for n in list(nt.nodes):
                if n.type == 'GLARE':
                    nt.nodes.remove(n)
            rl = next(n for n in nt.nodes if n.type == 'R_LAYERS')
            comp = next(n for n in nt.nodes if n.type == 'COMPOSITE')
            gl = nt.nodes.new('CompositorNodeGlare')
            gl.glare_type = gl_spec.get('type', 'FOG_GLOW')
            gl.quality = 'HIGH'
            gl.threshold = float(gl_spec.get('threshold', 1.0))
            gl.size = int(gl_spec.get('size', 7))
            gl.mix = float(gl_spec.get('mix', -0.35))
            nt.links.new(rl.outputs['Image'], gl.inputs['Image'])
            nt.links.new(gl.outputs['Image'], comp.inputs['Image'])
            p('    合成器 Glare: %s / threshold %.1f / size %d'
              % (gl_spec.get('type', 'FOG_GLOW'), gl.threshold, gl.size))
        except Exception as e:
            p('    !! 辉光节点不可用: %s' % e)

    if r.get('hide_fog'):
        hidden = 0
        for ob in bpy.data.objects:
            if ob.name.startswith(str(r.get('hide_fog_prefix', 'FogVolume'))):
                ob.hide_render = True
                ob.hide_viewport = True
                hidden += 1
        if hidden:
            p('    已关闭体积雾盒 %d 个（升空后会把天空糊成灰）' % hidden)

    sc.render.filepath = os.path.join(out_dir, r.get('frame_prefix', 'frame_'))
    p('    %s / %d 采样 / %s / %d 帧 @ %dfps / %s'
      % (sc.render.engine, samples, res, ctx['total'], ctx['fps'], vt))
    return {'engine': sc.render.engine, 'res': res, 'samples': samples, 'vt': vt}


# ═══════════════════════════════════════════════════════════════
# 9. 主流程
# ═══════════════════════════════════════════════════════════════
def apply_env(recipe):
    """环境变量覆盖（film.py 的逐镜头机位、以及临时调参都靠它）。"""
    cam = recipe.setdefault('camera', {})
    ov = recipe.setdefault('_cam_override', {})
    pairs = (('DSH_YJ_CAM_AZ_ADD', 'az_add', 0.0),
             ('DSH_YJ_CAM_D_SCALE', 'd_scale', 1.0),
             ('DSH_YJ_CAM_LENS_SCALE', 'lens_scale', 1.0),
             ('DSH_YJ_CAM_RISE_ADD', 'rise_add', 0.0),
             ('DSH_YJ_CAM_Z_ADD', 'z_add', 0.0))
    for env, key, dflt in pairs:
        v = os.environ.get(env)
        if v not in (None, ''):
            try:
                ov[key] = float(v)
            except ValueError:
                p('    !! %s=%r 不是数字，忽略' % (env, v))
    m = recipe.setdefault('motion', {})
    for env, key in (('DSH_YJ_LIFT', 'lift'), ('DSH_YJ_TRAVEL', 'travel'),
                     ('DSH_YJ_AZ', 'az')):
        v = os.environ.get(env)
        if v not in (None, ''):
            try:
                m[key] = float(v)
            except ValueError:
                pass
    for env, obj, key in (('DSH_YJ_LEAN', m.get('tilt') or {}, 'scale'),
                          ('DSH_YJ_BANK', m.get('bank') or {}, 'scale')):
        v = os.environ.get(env)
        if v not in (None, '') and isinstance(obj, dict) and obj:
            try:
                obj[key] = float(v)
            except ValueError:
                pass
    w = recipe.setdefault('weapon', {})
    for env, key in (('DSH_YJ_SWORD', 'scale'), ('DSH_YJ_THICK', 'thick')):
        v = os.environ.get(env)
        if v not in (None, ''):
            try:
                w[key] = float(v)
            except ValueError:
                pass
    if os.environ.get('DSH_YJ_LOCAL'):
        w['local_fwd'] = os.environ['DSH_YJ_LOCAL']
    rr = recipe.setdefault('render', {})
    if os.environ.get('DSH_YJ_RES'):
        rr['res'] = os.environ['DSH_YJ_RES']
    if os.environ.get('DSH_YJ_SAMPLES'):
        try:
            rr['samples'] = int(os.environ['DSH_YJ_SAMPLES'])
        except ValueError:
            pass
    if os.environ.get('DSH_YJ_FOG') == '1':
        rr['hide_fog'] = False
    return recipe


def load_recipe(src):
    """src 可以是 JSON 路径，也可以直接是 dict。"""
    if isinstance(src, dict):
        return dict(src)
    with open(src, 'r', encoding='utf-8') as f:
        r = json.load(f)
    r['_path'] = os.path.abspath(src)
    return r


def build(recipe, out_dir, total=None, fps=None):
    """按配方搭一整套动画，返回 ctx。"""
    recipe = apply_env(dict(recipe))
    total = int(total if total is not None else recipe.get('duration', 240))
    fps = int(fps or recipe.get('fps', 24))
    ctx = scene_ctx(total, fps)
    mo_spec = recipe.get('motion') or {'type': 'fly'}
    mo = MOTIONS[mo_spec.get('type', 'fly')](ctx, mo_spec)
    FV, UP = mo['FV'], mo['UP']

    p('=' * 70)
    p(recipe.get('title', '动画'))
    p('=' * 70)
    p('骨架 (%.2f, %.2f, %.2f)   基准朝向 %.1f°'
      % (ctx['base'].x, ctx['base'].y, ctx['base'].z, math.degrees(ctx['base_rz'])))
    p('%d 帧 @ %dfps = %.1f 秒' % (total, fps, total / fps))

    # ① 角色位移与朝向
    p('\n[1] 角色：位移 / 转身 / 倾斜')
    arm = ctx['arm']
    arm.rotation_mode = 'QUATERNION'
    step = int(mo_spec.get('key_step', 2))
    for f in range(1, total + 1, step):
        ctx['sc'].frame_set(f)
        arm.location = mo['char_world'](f)
        arm.keyframe_insert('location', frame=f)
        R = (mu.Matrix.Rotation(math.radians(mo['bank'](f)), 4, FV)
             @ mu.Matrix.Rotation(math.radians(mo['tilt'](f)), 4, mo['axis'])
             @ mu.Matrix.Rotation(mo['yaw'](f), 4, 'Z'))
        arm.rotation_quaternion = R.to_quaternion()
        arm.keyframe_insert('rotation_quaternion', frame=f)
    p('    升空 %.0f 格 / 前移 %.0f 格 / 转身 %.0f° / 侧倾 %.0f°'
      % (float(mo_spec.get('lift', 0)), float(mo_spec.get('travel', 0)),
         float((mo_spec.get('turn') or {}).get('amount', 0)),
         float((mo_spec.get('bank') or {}).get('scale', 0))))

    # ② 姿态
    poses = recipe.get('poses') or {}
    if poses:
        p('\n[2] 姿态：%d 根骨骼' % len(poses))
        PB = arm.pose.bones
        n = 0
        for name, seq in poses.items():
            pb = PB.get(name)
            if not pb:
                p('    !! 没骨骼 %s' % name)
                continue
            pb.rotation_mode = 'XYZ'
            for f, xyz in seq:
                ctx['sc'].frame_set(int(f))
                pb.rotation_euler = [math.radians(v) for v in xyz]
                pb.keyframe_insert('rotation_euler', frame=int(f))
            n += 1
        p('    %d 根打了关键帧' % n)

    # ③ 武器
    w = recipe.get('weapon') or {}
    fsobj = None
    if w:
        p('\n[3] 武器：%s' % w.get('type', 'ridden_sword'))
        fsobj = WEAPONS[w.get('type', 'ridden_sword')](ctx, w, mo)

    # ④ 特效
    fx = recipe.get('fx') or []
    if fx:
        p('\n[4] 特效：%d 组' % len(fx))
        for it in fx:
            kind = it.get('type')
            fn = FX.get(kind)
            if fn is None:
                p('    !! 不认识的特效类型: %s（可用 %s）' % (kind, '/'.join(FX)))
                continue
            fn(ctx, it, mo)
            p('    - %s ✔' % kind)

    # ⑤ 相机
    p('\n[5] 摄影机')
    cam_info = build_camera(ctx, recipe.get('camera') or {
        'az': [[1, 200], [240, 200]], 'dist': [[1, 8], [240, 8]],
        'lens': [[1, 50], [240, 50]], 'rise': [[1, 0.6], [240, 0.6]]},
        mo, recipe.get('_cam_override'))

    # ⑥ 灯光
    p('\n[6] 布光')
    follow_lights(ctx, recipe.get('lights') or {}, mo, cam_info)

    # ⑦ 渲染设置
    p('\n[7] 渲染设置')
    setup_render(ctx, recipe.get('render') or {}, out_dir)

    blend_name = recipe.get('blend_name') or '动画.blend'
    blend = os.path.join(out_dir, blend_name)
    bpy.ops.wm.save_as_mainfile(filepath=blend)
    p('\n工程: %s' % blend)
    return {'ctx': ctx, 'blend': blend, 'mo': mo, 'recipe': recipe,
            'total': total, 'fps': fps, 'out': out_dir}


def render_preview(res, frames):
    sc = res['ctx']['sc']
    p('\n[8] 预览渲染 %d 帧: %s' % (len(frames), frames))
    for f in frames:
        sc.frame_set(f)
        sc.render.filepath = os.path.join(res['out'], 'preview_%04d' % f)
        bpy.ops.render.render(write_still=True)
        p('    帧 %d 完成' % f)
    sc.render.filepath = os.path.join(res['out'], 'frame_')


def write_log(out_dir):
    try:
        with open(os.path.join(out_dir, '_动画日志.txt'), 'w', encoding='utf-8') as f:
            f.write('\n'.join(LOG))
    except OSError:
        pass
