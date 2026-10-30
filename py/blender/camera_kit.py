# -*- coding: utf-8 -*-
"""camera_kit —— 相机能力扩展：抖动 / 环绕 / 镜头切换 / 天空延时

课程（T27 / T33 / T38 / T50）里讲了、但这个插件原来没有的四件事，
统一成 ``def xxx(scene, cam, ctx, spec)`` 的签名，注册表是 :data:`CAM_OPS`::

    import sys, os
    sys.path.insert(0, r'...\\dsh-blender-mc\\py\\blender')
    import camera_kit as ck

    ck.CAM_OPS['shake'](bpy.context.scene, bpy.context.scene.camera, ctx,
                        {'strength': 0.02, 'scale': 8})
    ck.apply_cam_op(bpy.context.scene, cam, ctx, 'orbit', {'sweep': 360})

``ctx`` 是 anim_kit.scene_ctx() 的那个 dict（``sc`` / ``arm`` / ``base`` /
``total`` / ``fps``），本模块只用到 ``base`` 和 ``total``，**缺了也能跑**
（退化成 scene.frame_end）。``spec`` 是参数 dict，None 视为 {}

容错约定
--------
* 相机对象为空 → 打印 ``[camera] 跳过 xxx（相机对象为空）`` 并返回，不抛异常；
* 属性/修改器在本版本不存在 → 打印跳过原因，继续；
* 唯一的硬失败是「op 名字写错」——那是调用方的 bug，静默反而难查。

Blender 5.2.3 LTS 实测要点
--------------------------
* ``Action`` 没有 ``.fcurves``（4.4 起移除），要走
  ``action.layers[i].strips[j].channelbags[k].fcurves``，见 :func:`iter_fcurves`。
* ``TimelineMarker`` 有 ``camera`` 属性，可以直接把机位绑到 marker 上，
  动画渲染到该帧就自动换机位，不用动 ``scene.camera``。
* ``FModifierNoise`` 的可用参数：strength / scale / phase / depth /
  blend_type / use_restricted_range（5.2 另有 use_legacy_noise）。
"""

import math
import random

import bpy
from mathutils import Matrix, Vector

__all__ = ['CAM_OPS', 'apply_cam_op', 'iter_fcurves',
           'shake', 'orbit', 'cuts', 'sky_timelapse']

_MISSING = object()


# ═══════════════════════════════════════════════════════════════
# 0. 日志 / 通用小工具
# ═══════════════════════════════════════════════════════════════
_SKIPPED = []


def _log(msg):
    print('[camera] %s' % msg)


def _skip(what, why='这个版本没有'):
    _SKIPPED.append(str(what))
    print('[camera] 跳过 %s（%s）' % (what, why))


def _read(obj, path, default=None):
    cur = obj
    for part in str(path).split('.'):
        cur = getattr(cur, part, _MISSING)
        if cur is _MISSING:
            return default
    return cur


def _scene(scene):
    return scene if scene is not None else bpy.context.scene


def _frames(scene, ctx, spec):
    """起始/结束帧：spec > ctx['total'] > scene.frame_end。"""
    ctx = ctx or {}
    f0 = int(spec.get('frame_start', ctx.get('start', scene.frame_start) or 1))
    f1 = int(spec.get('frame_end', ctx.get('total') or scene.frame_end))
    if f1 <= f0:
        f1 = f0 + max(1, int(scene.frame_end - scene.frame_start))
    return f0, f1


# ═══════════════════════════════════════════════════════════════
# 1. F-Curve 跨版本访问（5.2 没有 action.fcurves）
# ═══════════════════════════════════════════════════════════════
def iter_fcurves(action):
    """遍历 Action 里的 fcurve，跨版本兼容（与 blender/_compat.py 同一套逻辑）。"""
    if action is None:
        return
    fcs = getattr(action, 'fcurves', None)
    if fcs is not None:                       # 4.3 及更早
        for fc in fcs:
            yield fc
        return
    for layer in getattr(action, 'layers', []):          # 4.4+
        for strip in getattr(layer, 'strips', []):
            for cb in getattr(strip, 'channelbags', []):
                for fc in getattr(cb, 'fcurves', []):
                    yield fc


def _fcurve_containers(action):
    """返回能 ``remove(fcurve)`` 的容器集合。"""
    if action is None:
        return
    fcs = getattr(action, 'fcurves', None)
    if fcs is not None:
        yield fcs
        return
    for layer in getattr(action, 'layers', []):
        for strip in getattr(layer, 'strips', []):
            for cb in getattr(strip, 'channelbags', []):
                yield cb.fcurves


def _fcurves_of(ob, paths=None):
    """取对象的 fcurve；paths 给了就只留这些 data_path。"""
    ad = getattr(ob, 'animation_data', None)
    if ad is None:
        return []
    out = []
    for fc in iter_fcurves(getattr(ad, 'action', None)):
        if paths is None or fc.data_path in paths:
            out.append(fc)
    return out


def _drop_fcurves(ob, paths):
    """删掉对象在某些 data_path 上的所有 fcurve（环绕前清掉旧机位动画用）。"""
    ad = getattr(ob, 'animation_data', None)
    if ad is None:
        return 0
    n = 0
    for cont in _fcurve_containers(getattr(ad, 'action', None)):
        for fc in list(cont):
            if fc.data_path in paths:
                try:
                    cont.remove(fc)
                    n += 1
                except Exception:
                    pass
    return n


def _ensure_anim(ob, paths, frames):
    """保证 ob 在 paths 上有 fcurve：没有就在两端插静止关键帧把曲线建出来。"""
    if getattr(ob, 'animation_data', None) is None:
        try:
            ob.animation_data_create()
        except Exception as exc:
            _skip('%s.animation_data' % getattr(ob, 'name', '?'), str(exc)[:50])
            return 0
    made = 0
    for path in paths:
        if _fcurves_of(ob, (path,)):
            continue
        for f in frames:
            try:
                ob.keyframe_insert(path, frame=int(f))
                made += 1
            except Exception as exc:
                _skip('关键帧 %s' % path, str(exc).splitlines()[0][:50])
    return made


# ═══════════════════════════════════════════════════════════════
# 2. T27 —— 相机抖动
# ═══════════════════════════════════════════════════════════════
def shake(scene, cam, ctx, spec):
    """相机抖动：给相机 location / rotation_euler 的 F-Curve 挂 Noise 修改器。

    spec
    ----
    strength   旋转分量幅度（弧度，F-Curve 单位）；默认 0.03 ≈ 1.7°
    loc_k      位置分量 = strength × loc_k（米）；默认 0.5（手持感别抖出画面）
    scale      噪波尺度（帧）：**越小越抖**；默认 12
    depth      噪波细节层数 0-10；默认 2
    phase      相位；不传则每条曲线随机，避免三条轴完全同步
    tracks     参与抖动的通道，默认 ('location', 'rotation_euler')
    key_step   仅用于两端建立曲线，无用
    """
    scene = _scene(scene)
    spec = dict(spec or {})
    if cam is None:
        _skip('shake（相机抖动）', '相机对象为空')
        return {'op': 'shake', 'applied': False}

    strength = float(spec.get('strength', 0.03))
    loc_k = float(spec.get('loc_k', 0.5))
    scale = float(spec.get('scale', 12.0))
    depth = int(spec.get('depth', 2))
    tracks = tuple(spec.get('tracks') or ('location', 'rotation_euler'))
    f0, f1 = _frames(scene, ctx, spec)

    _ensure_anim(cam, tracks, (f0, f1))

    per_path = {'location': strength * loc_k, 'rotation_euler': strength}
    made = 0
    for fc in _fcurves_of(cam, tracks):
        path = fc.data_path.split('[')[0]
        amp = per_path.get(path, strength)
        try:
            mod = fc.modifiers.new('NOISE')
        except Exception as exc:
            _skip('Noise 修改器（%s）' % fc.data_path, str(exc).splitlines()[0][:50])
            continue
        try:
            mod.strength = amp
            mod.scale = scale
            mod.depth = depth
            mod.phase = (float(spec['phase']) if spec.get('phase') is not None
                         else random.uniform(0.0, 1000.0))
            if spec.get('blend_type'):
                mod.blend_type = spec['blend_type']
            if spec.get('frame_start') is not None or spec.get('frame_end') is not None:
                mod.use_restricted_range = True
                mod.frame_start = float(spec.get('frame_start', f0))
                mod.frame_end = float(spec.get('frame_end', f1))
        except Exception as exc:
            _skip('Noise 参数（%s）' % fc.data_path, str(exc).splitlines()[0][:50])
            continue
        made += 1

    if not made:
        _skip('shake（相机抖动）', '相机上没有可挂 Noise 的 F-Curve')
        return {'op': 'shake', 'applied': False}

    _log('抖动：%d 条曲线 / strength %.3f（位置 ×%.2f）/ scale %.1f / depth %d / %d-%d 帧'
         % (made, strength, loc_k, scale, depth, f0, f1))
    return {'op': 'shake', 'applied': True, 'curves': made,
            'strength': strength, 'loc_k': loc_k, 'scale': scale,
            'depth': depth, 'frames': [f0, f1]}


# ═══════════════════════════════════════════════════════════════
# 3. T38 —— 360° 环绕
# ═══════════════════════════════════════════════════════════════
def _resolve_target(scene, ctx, spec):
    """机位瞄准的地面点（不加上 aim_h）。"""
    target = spec.get('target')
    if target is not None:
        if isinstance(target, str):
            ob = bpy.data.objects.get(target)
            if ob is not None:
                return ob.matrix_world.translation.copy()
            _skip('target=%r' % target, '场景里没有这个物体')
        elif isinstance(target, (list, tuple)) and len(target) >= 3:
            return Vector((float(target[0]), float(target[1]), float(target[2])))
        else:
            try:
                return Vector(target)
            except Exception:
                _skip('target=%r' % (target,), '不是坐标')

    ctx = ctx or {}
    base = ctx.get('base')
    if base is not None:
        return Vector(base)

    arm = ctx.get('arm')
    if arm is None:
        arm = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)
    if arm is not None:
        return arm.matrix_world.translation.copy()

    cam = getattr(scene, 'camera', None)
    if cam is not None:
        return cam.matrix_world.translation.copy() + Vector((0.0, -6.0, 0.0))
    _skip('机位中心', '场景里既没有骨架也没有摄影机，按原点算')
    return Vector((0.0, 0.0, 0.0))


def orbit(scene, cam, ctx, spec):
    """360° 环绕：新建空物体当父级，相机挂上去绕角色转一圈。

    spec
    ----
    rot_z     起始角（度）；默认 0
    sweep     扫过角度（度）；默认 360
    radius    环绕半径（米）；默认 = 相机当前水平距离（保持原来的构图）
    height    相对环绕圆心的相机高度（米）；默认 = 相机当前高度 − 圆心高度
    aim_h     圆心高度（角色胸口，米）；默认 1.0
    pivot     圆心坐标 [x,y,z]；给了就覆盖 target/aim_h 的推算
    aim       是否给相机加 TRACK_TO 一直看圆心；默认 True
    clear_keys 是否清掉相机原有的 location/rotation 动画；默认 True
    key_step  无关（空物体只打首尾两个关键帧，线性匀速）
    """
    scene = _scene(scene)
    spec = dict(spec or {})
    if cam is None:
        _skip('orbit（360° 环绕）', '相机对象为空')
        return {'op': 'orbit', 'applied': False}

    f0, f1 = _frames(scene, ctx, spec)
    rot0 = float(spec.get('rot_z', 0.0))
    rot1 = rot0 + float(spec.get('sweep', 360.0))

    # 圆心
    pivot_pos = spec.get('pivot')
    if pivot_pos is not None:
        try:
            pivot_pos = Vector((float(pivot_pos[0]), float(pivot_pos[1]),
                                float(pivot_pos[2])))
        except Exception:
            _skip('pivot=%r' % (pivot_pos,), '不是坐标，改用 target 推算')
            pivot_pos = None
    if pivot_pos is None:
        pivot_pos = _resolve_target(scene, ctx, spec)
        pivot_pos = pivot_pos + Vector((0.0, 0.0, float(spec.get('aim_h', 1.0))))

    # 半径 / 高度：不传就沿用当前构图
    cam_pos = cam.matrix_world.translation.copy()
    dxy = Vector((cam_pos.x - pivot_pos.x, cam_pos.y - pivot_pos.y, 0.0))
    cur_r = dxy.length
    cur_h = cam_pos.z - pivot_pos.z
    radius = float(spec.get('radius', cur_r if cur_r > 1e-4 else 8.0))
    height = float(spec.get('height', cur_h))

    empty_name = str(spec.get('pivot_name') or 'CAM_ORBIT_PIVOT')
    pivot = bpy.data.objects.get(empty_name)
    if pivot is None:
        pivot = bpy.data.objects.new(empty_name, None)
        scene.collection.objects.link(pivot)
    pivot.empty_display_type = 'CIRCLE'
    pivot.empty_display_size = max(0.5, radius * 0.35)
    pivot.location = pivot_pos
    pivot.rotation_mode = 'XYZ'

    if spec.get('clear_keys', True):
        n_clr = _drop_fcurves(cam, ('location', 'rotation_euler'))
        if n_clr:
            _log('清掉相机原有 %d 条机位曲线，保证绕的是干净的一圈' % n_clr)

    cam.parent = pivot
    try:
        cam.matrix_parent_inverse = Matrix.Identity(4)
    except Exception:
        pass
    cam.location = Vector((radius, 0.0, height))
    cam.rotation_euler = (0.0, 0.0, 0.0)

    if spec.get('aim', True):
        con = next((c for c in cam.constraints if c.type == 'TRACK_TO'
                    and getattr(c, 'target', None) is pivot), None)
        if con is None:
            try:
                con = cam.constraints.new('TRACK_TO')
                con.target = pivot
                con.track_axis = 'TRACK_NEGATIVE_Z'
                con.up_axis = 'UP_Y'
            except Exception as exc:
                _skip('TRACK_TO 约束', str(exc).splitlines()[0][:50])

    # 空物体打首尾两个关键帧并设 LINEAR → 匀速转圈
    _drop_fcurves(pivot, ('rotation_euler',))     # 重跑时先清掉上一轮的转速曲线
    try:
        pivot.rotation_euler.z = math.radians(rot0)
        pivot.keyframe_insert('rotation_euler', index=2, frame=f0)
        pivot.rotation_euler.z = math.radians(rot1)
        pivot.keyframe_insert('rotation_euler', index=2, frame=f1)
        for fc in _fcurves_of(pivot, ('rotation_euler',)):
            for kp in fc.keyframe_points:
                kp.interpolation = 'LINEAR'
    except Exception as exc:
        _skip('环绕关键帧', str(exc).splitlines()[0][:60])
        return {'op': 'orbit', 'applied': False}

    _log('环绕：圆心 (%.2f, %.2f, %.2f) / 半径 %.2f / 高 %.2f / %.0f°→%.0f° / %d-%d 帧'
         % (pivot_pos.x, pivot_pos.y, pivot_pos.z, radius, height,
            rot0, rot1, f0, f1))
    return {'op': 'orbit', 'applied': True, 'pivot': pivot.name,
            'pivot_loc': [round(v, 3) for v in pivot_pos],
            'radius': round(radius, 3), 'height': round(height, 3),
            'rot_z': [rot0, rot1], 'frames': [f0, f1],
            'aim': bool(spec.get('aim', True))}


# ═══════════════════════════════════════════════════════════════
# 4. T33 —— 镜头切换
# ═══════════════════════════════════════════════════════════════
def cuts(scene, cam, ctx, spec):
    """镜头切换：时间线插 marker（绑定机位），并生成 cam_cut_1..n。

    spec
    ----
    shots    [{'frame': 1, 'az': 250, 'dist': 8.0, 'lens': 50,
               'height': 0.5}, ...]   —— az 是 MC 角度（0=+X，90=+Y）
    aim_h    瞄准高度（角色胸口，米）；默认 1.0
    height   所有机位的默认高度（相对目标点，米）；默认 0.0
    prefix   机位名前缀；默认 'cam_cut_'
    copy_data 新机位是否复制主相机的数据块（DOF 等）；默认 True
    bind_scene_camera 是否把 scene.camera 换成第一台，默认 False
    """
    scene = _scene(scene)
    spec = dict(spec or {})
    shots = spec.get('shots') or []
    if cam is None:
        _skip('cuts（镜头切换）', '相机对象为空')
        return {'op': 'cuts', 'applied': False}
    if not shots:
        _skip('cuts（镜头切换）', 'spec["shots"] 是空的')
        return {'op': 'cuts', 'applied': False, 'cameras': []}

    prefix = str(spec.get('prefix') or 'cam_cut_')
    aim_h = float(spec.get('aim_h', 1.0))
    def_h = float(spec.get('height', 0.0))
    target = _resolve_target(scene, ctx, spec)
    aim = target + Vector((0.0, 0.0, aim_h))

    # 幂等：先清掉上一轮生成的 marker / 机位
    for mk in list(scene.timeline_markers):
        if str(mk.name).startswith(prefix) or str(mk.name).startswith('cut_'):
            try:
                scene.timeline_markers.remove(mk)
            except Exception:
                pass

    ordered = sorted(shots, key=lambda s: float(s.get('frame', 1)))
    made, marks = [], []
    for idx, shot in enumerate(ordered, 1):
        name = '%s%d' % (prefix, idx)
        old = bpy.data.objects.get(name)
        if old is not None:                     # 重跑时先删旧机位
            try:
                bpy.data.objects.remove(old, do_unlink=True)
            except Exception:
                pass
        try:
            if spec.get('copy_data', True) and cam.data is not None:
                camdata = cam.data.copy()
                camdata.name = name
            else:
                camdata = bpy.data.cameras.new(name)
            ob = bpy.data.objects.new(name, camdata)
            scene.collection.objects.link(ob)
        except Exception as exc:
            _skip('机位 %s' % name, str(exc).splitlines()[0][:50])
            continue

        az = math.radians(float(shot.get('az', 0.0)))
        dist = float(shot.get('dist', spec.get('dist', 8.0)))
        h = float(shot.get('height', def_h))
        pos = aim + Vector((math.cos(az) * dist, math.sin(az) * dist, h))
        ob.location = pos
        ob.rotation_euler = (aim - pos).to_track_quat('-Z', 'Y').to_euler()
        lens = shot.get('lens')
        if lens is not None:
            try:
                ob.data.lens = float(lens)
            except Exception:
                _skip('%s.lens' % name, str(lens)[:40])

        frame = int(shot.get('frame', 1))
        mk = scene.timeline_markers.new(name, frame=frame)
        bound = False
        if hasattr(mk, 'camera'):                # 5.2 有，直接绑机位
            try:
                mk.camera = ob
                bound = True
            except Exception as exc:
                _skip('marker.camera（%s）' % name, str(exc).splitlines()[0][:50])
        made.append(ob.name)
        marks.append({'marker': mk.name, 'frame': frame, 'camera': ob.name,
                      'bound': bound, 'az': round(math.degrees(az), 2),
                      'dist': round(dist, 3),
                      'lens': _read(ob.data, 'lens')})

    if not made:
        return {'op': 'cuts', 'applied': False, 'cameras': []}

    if spec.get('bind_scene_camera'):
        try:
            scene.camera = bpy.data.objects[made[0]]
        except Exception:
            pass

    _log('镜头切换：%d 个机位（%s），marker 帧 %s'
         % (len(made), '、'.join(made),
            '、'.join(str(m['frame']) for m in marks)))
    return {'op': 'cuts', 'applied': True, 'cameras': made,
            'markers': marks, 'target': [round(v, 3) for v in target]}


# ═══════════════════════════════════════════════════════════════
# 5. T50 —— 天空延时
# ═══════════════════════════════════════════════════════════════
def sun_elevation_deg(hour):
    """小时 → 太阳高度角（度）：6 点 0°、12 点 90°、18 点 0°、24 点 −90°。"""
    h = float(hour) % 24.0
    return 90.0 * math.sin(math.pi * (h - 6.0) / 12.0)


def sky_timelapse(scene, cam, ctx, spec):
    """天空延时：World 背景强度 + 太阳高度 / 太阳灯角度，随时段打关键帧。

    spec
    ----
    hours    [{'frame': 1, 'hour': 6.5}, {'frame': 120, 'hour': 18}]（也接受 [帧, 小时] 对）
    day_strength   白天背景强度；默认 1.0
    night_strength 夜晚背景强度；默认 0.02
    sun_energy     白天太阳灯强度（W/m²）；默认沿用灯当前值
    sun_energy_night 夜晚太阳灯强度；默认 sun_energy × 0.03
    sun_azimuth    太阳方位角（弧度）；默认沿用灯当前 Z 旋转
    linear         关键帧是否线性插值；默认 False（贝塞尔更顺）
    """
    scene = _scene(scene)
    spec = dict(spec or {})
    if cam is None:
        # 这个 op 只动 World 和太阳灯，相机为空不影响，但还是提示一声
        print('[camera] 相机对象为空，sky_timelapse 只操作 World / 太阳灯，继续')

    raw = spec.get('hours') or []
    if not raw:
        _skip('sky_timelapse（天空延时）', 'spec["hours"] 是空的')
        return {'op': 'sky_timelapse', 'applied': False}

    keys = []
    for item in raw:
        if isinstance(item, dict):
            keys.append((int(item.get('frame', 1)), float(item.get('hour', 12.0))))
        elif isinstance(item, (list, tuple)) and len(item) >= 2:
            keys.append((int(item[0]), float(item[1])))
        else:
            _skip('hours 里的 %r' % (item,), '既不是 {frame,hour} 也不是 [帧, 小时]')
    keys.sort(key=lambda kv: kv[0])
    if not keys:
        return {'op': 'sky_timelapse', 'applied': False}

    day_s = float(spec.get('day_strength', 1.0))
    night_s = float(spec.get('night_strength', 0.02))

    # —— World 背景强度
    bg = None
    world = getattr(scene, 'world', None)
    if world is None:
        _skip('World 背景强度', '场景里没有 World')
    else:
        nt = getattr(world, 'node_tree', None)
        if nt is not None:
            bg = next((n for n in nt.nodes if n.type == 'BACKGROUND'), None)
        if bg is None:
            _skip('World 背景强度', 'World 里没有 Background 节点')
    sky_node = None
    if world is not None and getattr(world, 'node_tree', None) is not None:
        sky_node = next((n for n in world.node_tree.nodes if n.type == 'TEX_SKY'), None)

    # —— 太阳灯
    sun = spec.get('sun')
    if isinstance(sun, str):
        sun = bpy.data.objects.get(sun)
    if sun is None or getattr(sun, 'type', None) != 'LIGHT':
        sun = next((o for o in bpy.data.objects
                    if o.type == 'LIGHT' and o.data.type == 'SUN'), None)
    if sun is None:
        _skip('太阳灯关键帧', '场景里没有 SUN 灯（只做 World 强度与天空太阳高度）')

    sun_e_day = float(spec.get('sun_energy', _read(sun, 'data.energy', 6.0) or 6.0)) \
        if sun is not None else None
    sun_e_night = float(spec.get('sun_energy_night',
                                 (sun_e_day * 0.03) if sun_e_day else 0.18))
    az = spec.get('sun_azimuth')
    if az is None:
        az = _read(sun, 'rotation_euler.z', 0.0) if sun is not None else 0.0

    n_bg = n_sky = n_sun = 0
    elevs = []
    for frame, hour in keys:
        elev = sun_elevation_deg(hour)
        elevs.append(round(elev, 1))
        day = max(0.0, math.sin(math.radians(elev)))       # 0=夜 1=正午
        strength = night_s + (day_s - night_s) * day

        if bg is not None:
            try:
                bg.inputs['Strength'].default_value = float(strength)
                bg.inputs['Strength'].keyframe_insert('default_value', frame=frame)
                n_bg += 1
            except Exception as exc:
                _skip('背景强度关键帧（帧 %d）' % frame, str(exc).splitlines()[0][:50])

        if sky_node is not None:
            try:
                sky_node.sun_elevation = math.radians(max(-10.0, min(89.0, elev)))
                sky_node.keyframe_insert('sun_elevation', frame=frame)
                n_sky += 1
            except Exception as exc:
                _skip('太阳高度关键帧（帧 %d）' % frame, str(exc).splitlines()[0][:50])

        if sun is not None:
            ok_rot = ok_nrg = False
            try:
                # SUN 灯默认朝 −Z 打：rot_x = 90° + 高度角 → 光束确实按高度角来
                sun.rotation_euler = (math.radians(90.0 + max(-90.0, min(90.0, elev))),
                                      0.0, float(az))
                sun.keyframe_insert('rotation_euler', frame=frame)
                ok_rot = True
            except Exception as exc:
                _skip('太阳灯角度关键帧（帧 %d）' % frame, str(exc).splitlines()[0][:50])
            try:
                # 5.x 不允许 object.keyframe_insert('data.energy')（跨 ID 块），
                # 强度必须打在灯数据块自己身上
                sun.data.energy = max(0.01, sun_e_night + (sun_e_day - sun_e_night) * day)
                sun.data.keyframe_insert('energy', frame=frame)
                ok_nrg = True
            except Exception as exc:
                _skip('太阳灯强度关键帧（帧 %d）' % frame, str(exc).splitlines()[0][:50])
            if ok_rot and ok_nrg:
                n_sun += 1

    if not (n_bg or n_sky or n_sun):
        _skip('sky_timelapse（天空延时）', 'World / 天空 / 太阳灯一样都没打上关键帧')
        return {'op': 'sky_timelapse', 'applied': False}

    if spec.get('linear'):
        holders = []
        if sun is not None:
            holders.append(sun)                      # rotation_euler
            if getattr(sun, 'data', None) is not None:
                holders.append(sun.data)             # energy（灯数据块自己的 action）
        if world is not None and getattr(world, 'node_tree', None) is not None:
            holders.append(world.node_tree)          # Background 强度 / 天空太阳高度
        n = 0
        for holder in holders:
            for fc in _fcurves_of(holder):
                for kp in fc.keyframe_points:
                    kp.interpolation = 'LINEAR'
                n += 1
        if n:
            _log('关键帧插值 → LINEAR（%d 条曲线）' % n)

    _log('天空延时：%d 个时段 / 背景强度 %d 键 / 太阳高度 %d 键 / 太阳灯 %d 键 / 高度角 %s°'
         % (len(keys), n_bg, n_sky, n_sun, elevs))
    return {'op': 'sky_timelapse', 'applied': True, 'keys': len(keys),
            'hours': [h for _f, h in keys], 'frames': [f for f, _h in keys],
            'elevation_deg': elevs,
            'bg_strength_keys': n_bg, 'sun_elevation_keys': n_sky,
            'sun_keys': n_sun,
            'day_strength': day_s, 'night_strength': night_s}


# ═══════════════════════════════════════════════════════════════
# 6. 注册表
# ═══════════════════════════════════════════════════════════════
CAM_OPS = {
    'shake': shake,             # T27 相机抖动
    'orbit': orbit,             # T38 360° 环绕
    'cuts': cuts,               # T33 镜头切换
    'sky_timelapse': sky_timelapse,   # T50 天空延时
}

# 别名，配 recipe 里的写法省事
CAM_ALIASES = {
    'noise': 'shake', 'jitter': 'shake', '抖动': 'shake',
    'turntable': 'orbit', '环绕': 'orbit',
    'cut': 'cuts', '切换': 'cuts',
    'sky': 'sky_timelapse', 'timelapse': 'sky_timelapse', '延时': 'sky_timelapse',
}


def apply_cam_op(scene, cam, ctx, name, spec=None):
    """按名字跑一个相机能力。返回 op 的结果 dict（applied 字段表示是否生效）。"""
    key = str(name or '').strip().lower()
    key = CAM_ALIASES.get(key, key)
    fn = CAM_OPS.get(key)
    if fn is None:
        raise ValueError('未知相机能力 %r；可选：%s'
                         % (name, ' / '.join(sorted(CAM_OPS))))
    _SKIPPED[:] = []
    res = fn(_scene(scene), cam, ctx or {}, dict(spec or {}))
    res.setdefault('skipped', list(_SKIPPED))
    return res


def apply_cam_ops(scene, cam, ctx, ops):
    """批量跑：``[{'op': 'shake', ...}, {'op': 'orbit', ...}]``。"""
    out = []
    for item in ops or []:
        item = dict(item)
        name = item.pop('op', None) or item.pop('name', None)
        if name is None:
            _skip('相机能力（没写 op）')
            continue
        try:
            out.append(apply_cam_op(scene, cam, ctx, name, item))
        except ValueError as exc:
            _skip('相机能力 %r' % name, str(exc)[:60])
    return out
