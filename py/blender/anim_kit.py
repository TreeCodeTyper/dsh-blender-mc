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
def _action_fcurves(act):
    """取 Action 的 fcurve。Blender 4.4+ 改成 layers/strips/channelbags，没有 act.fcurves。"""
    try:
        return list(act.fcurves)
    except Exception:
        pass
    out = []
    for layer in getattr(act, 'layers', []):
        for strip in getattr(layer, 'strips', []):
            for cb in getattr(strip, 'channelbags', []):
                out.extend(cb.fcurves)
    return out


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


def motion_leap(ctx, m):
    """起飞 → 平飞 → 降落：高度先升到 height 再回到地面，水平位移匀速。

    fly 的位移是"一次缓入缓出"，起落对称且终点还在高处，做不了完整起降；
    leap 把高度和位移拆成两条独立曲线：
      height(f) = height · (smoothstep(升) − smoothstep(降))   → 起、落各一段
      along(f)  = dist · (f−1)/(total−1)                       → 巡航匀速
    m 的键：az、height、dist、rise [起,止]、fall [起,止]、bob 巡航期的上下浮动、
            tilt/bank {scale, terms}（用两条权重 +1/−1 的 terms 就能做"抬起来再放平"）。
    """
    az = math.radians(float(m.get('az', 20)))
    FV = mu.Vector((math.cos(az), math.sin(az), 0.0))
    UP = mu.Vector((0.0, 0.0, 1.0))
    base = ctx['base']
    height = float(m.get('height', 4.0))
    dist = float(m.get('dist', 60.0))
    rise = m.get('rise', [1, 52])
    fall = m.get('fall', [188, 240])
    bob = float(m.get('bob', 0.0))
    tilt_cfg = m.get('tilt') or {'scale': 0.0, 'terms': []}
    bank_cfg = m.get('bank') or {'scale': 0.0, 'terms': []}
    span = float(max(1e-6, ctx['total'] - 1))

    def height_at(f):
        a = smoothstep((f - rise[0]) / float(max(1e-6, rise[1] - rise[0])))
        b = smoothstep((f - fall[0]) / float(max(1e-6, fall[1] - fall[0])))
        return height * (a - b)

    def char_world(f):
        u = min(1.0, max(0.0, (f - 1) / span))
        return base + FV * (dist * u) + UP * (height_at(f) + bob * math.sin(u * math.pi * 5.0))

    def yaw(f):
        return ctx['base_rz']

    def tilt(f):
        return float(tilt_cfg.get('scale', 0)) * _terms(f, tilt_cfg.get('terms') or [])

    def bank(f):
        return float(bank_cfg.get('scale', 0)) * _terms(f, bank_cfg.get('terms') or [])

    return {'char_world': char_world, 'yaw': yaw, 'tilt': tilt, 'bank': bank,
            'FV': FV, 'UP': UP, 'axis': UP.cross(FV).normalized(),
            'speed': dist / span, 'dist': dist}


def motion_ground(ctx, m):
    """贴着地面匀速前进 —— 走路、跑步、任何"一直往前走"的循环都用它。

    和 fly 的区别：fly 是缓入缓出的位移曲线（起跳→滑行→停），
    地面的循环动作必须**匀速**，否则脚一定打滑。

    m 的键：
        az      前进方位角（0 = +X）
        speed   速度，世界单位/帧。脚不打滑靠它：
                speed = 一个循环里脚相对身体向后走的距离 ÷ 循环帧数
        start   从第几帧开始走（之前原地踏步）
        yaw     相对骨架基准朝向的偏转（度）
        bob     {amount, cycle} 身体上下起伏：amount 格、cycle 帧一个来回
    """
    az = math.radians(float(m.get('az', 0.0)))
    FV = mu.Vector((math.cos(az), math.sin(az), 0.0))
    UP = mu.Vector((0.0, 0.0, 1.0))
    base = ctx['base']
    speed = float(m.get('speed', 0.0))
    start = float(m.get('start', 1))
    bob = m.get('bob') or {}
    bob_a = float(bob.get('amount', 0.0))
    bob_c = max(1.0, float(bob.get('cycle', 16)))
    yaw0 = ctx['base_rz'] + math.radians(float(m.get('yaw', 0.0)))

    # 提示词出片会保留真实地图；棚拍配方原本是固定 z 前进，
    # 这里按帧把 root 贴到地图表面，否则角色会悬空滑行。
    snap = os.environ.get('DSH_ANIM_TERRAIN_SNAP') == '1'
    terrain = []
    dg = None
    if snap:
        dg = bpy.context.evaluated_depsgraph_get()
        for ob in bpy.data.objects:
            if ob.type != 'MESH' or ob.hide_render:
                continue
            try:
                if max(ob.dimensions) > 50.0:
                    terrain.append(ob)
            except Exception:
                pass

    def terrain_z(x, y):
        """取 (x,y) 下方最高的地形面；找不到返回 None。"""
        best = None
        for ob in terrain:
            try:
                inv = ob.matrix_world.inverted()
                origin = inv @ mu.Vector((x, y, 500.0))
                hit, loc, _n, _i = ob.ray_cast(origin, (0.0, 0.0, -1.0),
                                               distance=1000.0, depsgraph=dg)
                if hit:
                    z = (ob.matrix_world @ loc).z
                    if best is None or z > best:
                        best = z
            except Exception:
                pass
        return best

    def dist(f):
        return speed * max(0.0, f - start)

    def char_world(f):
        d = dist(f)
        b = bob_a * math.sin(2.0 * math.pi * (f - start) / bob_c) if bob_a else 0.0
        p = base + FV * d + UP * b
        if snap:
            z = terrain_z(p.x, p.y)
            # 只接受与角色原点同一高度层的地形；避免射线打到地底/远景大平面
            # 把整条相机轨道拽到地下。
            if z is not None and abs(z - base.z) < 8.0:
                p.z = z + 0.02
        return p

    return {'char_world': char_world, 'yaw': lambda f: yaw0,
            'tilt': lambda f: 0.0, 'bank': lambda f: 0.0,
            'FV': FV, 'UP': UP, 'axis': UP.cross(FV).normalized(),
            'speed': speed, 'dist': dist}


def motion_drop(ctx, m):
    """从高处落下来砸地 —— 「坠落 / 落地」这类片子的运动形状。

    fly 只能往上飞、leap 是起跳再落回原处，两者都做不出「一开始就在半空」。
    drop 把高度写成一条重力曲线：

        h(f) = height · (1 − u^accel)        u = (f − start) / (land − start)
        落地后：h = bounce · 衰减的正弦（不写 bounce 就一直贴地）

    m 的键：
        height  起始离地高度（格）
        start   从第几帧开始下落（之前定在半空）
        land    落地帧；land >= total 表示这支片子不落地（纯坠落）
        accel   重力指数，1 = 匀加速，2 = 越落越快（更像真重力）
        bounce  {amount, frames, cycle} 落地回弹
    """
    UP = mu.Vector((0.0, 0.0, 1.0))
    az = math.radians(float(m.get('az', 0.0)))
    FV = mu.Vector((math.cos(az), math.sin(az), 0.0))
    base = ctx['base']
    height = float(m.get('height', 8.0))
    start = float(m.get('start', 1))
    land = float(m.get('land', ctx['total']))
    accel = max(1.0, float(m.get('accel', 2.0)))
    bo = m.get('bounce') or {}
    bo_a = float(bo.get('amount', 0.0))
    bo_f = max(1.0, float(bo.get('frames', 14)))
    bo_c = max(2.0, float(bo.get('cycle', 10)))
    tilt_cfg = m.get('tilt') or {'scale': 0.0, 'terms': []}
    bank_cfg = m.get('bank') or {'scale': 0.0, 'terms': []}

    def h_at(f):
        if f <= start:
            return height
        if f < land:
            u = (f - start) / float(max(1e-6, land - start))
            return height * (1.0 - min(1.0, u) ** accel)
        if not bo_a:
            return 0.0
        # 落地后按 frames 的时长衰减回弹
        d = float(f) - land
        if d > bo_f:
            return 0.0
        return bo_a * math.sin(d / bo_c * math.pi) * max(0.0, 1.0 - d / bo_f)

    def char_world(f):
        return base + UP * h_at(f)

    def tilt(f):
        return float(tilt_cfg.get('scale', 0)) * _terms(f, tilt_cfg.get('terms') or [])

    def bank(f):
        return float(bank_cfg.get('scale', 0)) * _terms(f, bank_cfg.get('terms') or [])

    return {'char_world': char_world, 'yaw': lambda f: ctx['base_rz'],
            'tilt': tilt, 'bank': bank,
            'FV': FV, 'UP': UP, 'axis': UP.cross(FV).normalized(), 'speed': 0.0}


MOTIONS = {'fly': motion_fly, 'ground': motion_ground,
           'leap': motion_leap, 'drop': motion_drop}


# ── 坐骑 ───────────────────────────────────────────────────────────────
# 课程 T52 讲的「生物生成器」：MCprep 自带一堆绑好的生物（panda、horse、cat…）。
# 但在 -b 无界面模式下 bpy.ops.mcprep 是空的（ops 只在 GUI 注册），
# 所以这里直接 append MCprep 的 rig .blend，规则和 py/mobs.py 一致：
# 一套角色 = 骨架 + 它的对象子级 + 蒙皮到它的网格。
MOUNT_RIG_GROUPS = ('custom', 'friendly', 'hostile', 'passive', 'player')


def _mcprep_rig_dirs():
    import glob as _glob
    base = bpy.utils.user_resource('SCRIPTS', path='addons')
    pat = os.path.join(base, 'MCprep_addon', 'MCprep_resources', 'rigs', '*')
    dirs = sorted(_glob.glob(pat), reverse=True)
    return [d for d in dirs if os.path.isdir(d)]


def _find_mount_rig(query):
    """按名字片段找 MCprep 的 rig 文件（"panda" / "Fox Panda" / "Horse" 都行）。"""
    q = (query or '').lower()
    hits = []
    for d in _mcprep_rig_dirs():
        for fn in sorted(os.listdir(d)):
            if fn.endswith('.blend') and q in fn.lower():
                hits.append(os.path.join(d, fn))
    if not hits:
        raise RuntimeError('MCprep rig 库里找不到坐骑：%s' % query)
    return hits[0]


def load_mount_rig(spec, ctx):
    """append 一个坐骑 rig，只留一套角色，归一化到"脚落 z=0、水平居中"。

    返回 (armature_object, 背高)。背高用来算骑手该坐多高。
    """
    path = spec.get('path') or _find_mount_rig(spec.get('rig') or spec.get('name') or '')
    stem = os.path.splitext(os.path.basename(path))[0]
    kw = spec.get('keyword') or stem.split(' - ')[0]
    p('\\n[0] 坐骑：%s（%s）' % (path, kw))

    with bpy.data.libraries.load(path, link=False) as (df, dt):
        allnames = [n for n in df.objects]
        # 先只加载名字里带关键词的对象（Fox Panda 里同时有 fox.* 和 panda.*）
        focus = [n for n in allnames if kw.lower() in n.lower()]
        want = focus if focus else allnames
        dt.objects = want
    loaded = [o for o in dt.objects if o is not None]
    p('    载入 %d 个数据块，其中对象 %d（关键词命中 %d / 全部 %d）'
      % (len(want), len(loaded), len(focus), len(allnames)))

    arms = [o for o in loaded if o.type == 'ARMATURE']
    words = [w for w in kw.replace('-', ' ').split() if w]
    best = None
    for cand in arms:
        low = cand.name.lower()
        for w in words:
            if w.lower() in low and (best is None or len(w) > best[0]):
                best = (len(w), cand)
    tgt = best[1] if best else (arms[0] if arms else None)

    keep = []
    if tgt is not None:
        keep.append(tgt)
        for ob in loaded:
            if ob is tgt or ob.type in ('CAMERA', 'LIGHT', 'FONT'):
                continue
            if ob.parent is tgt:
                keep.append(ob)
            elif any(m.type == 'ARMATURE' and m.object is tgt for m in ob.modifiers):
                keep.append(ob)
    else:
        keep = [o for o in loaded if o.type == 'MESH']

    col = ctx['sc'].collection
    for ob in keep:
        if ob.name not in col.objects:
            try:
                col.objects.link(ob)
            except RuntimeError:
                pass
        # rig 文件里不少部件是隐藏的（表情/变体），不放开的话熊猫只剩半个
        ob.hide_render = False
        ob.hide_viewport = False
    # 多余的对象（另一套角色、相机、灯光…）不删，整体挪进一个隐藏集合 ——
    # 删父级会连带清掉子级引用，隐藏最稳。
    extra = [o for o in loaded if o not in keep]
    if extra:
        hid = bpy.data.collections.new('_mount_extra')
        col.children.link(hid)
        for ob in extra:
            for c in list(ob.users_collection):
                try:
                    c.objects.unlink(ob)
                except RuntimeError:
                    pass
            try:
                hid.objects.link(ob)
            except RuntimeError:
                pass
        hid.hide_render = True
        hid.hide_viewport = True

    # 归一化：水平居中 + 脚落 z=0
    lo = mu.Vector((1e9, 1e9, 1e9))
    hi = mu.Vector((-1e9, -1e9, -1e9))
    for ob in keep:
        if ob.type != 'MESH':
            continue
        for c in ob.bound_box:
            w = ob.matrix_world @ mu.Vector(c)
            lo = mu.Vector((min(lo.x, w.x), min(lo.y, w.y), min(lo.z, w.z)))
            hi = mu.Vector((max(hi.x, w.x), max(hi.y, w.y), max(hi.z, w.z)))
    back_z = (hi.z - lo.z) if tgt is not None else 0.0
    # rig 里可能带着摆姿势的动画数据，一跑 frame_set 熊猫就趴下了 —— 清掉
    for ob in keep:
        try:
            if ob.animation_data is not None:
                ob.animation_data_clear()
        except Exception:
            pass
    if tgt is not None and tgt.data is not None:
        try:
            if tgt.data.animation_data is not None:
                tgt.data.animation_data_clear()
        except Exception:
            pass
    # 归一化偏移不直接写进 location（后面每帧要按骑手位置重设它），
    # 而是返回给调用方，让调用方把"居中量 + 世界位置"一起算。
    fit = mu.Vector((-(lo.x + hi.x) * 0.5, -(lo.y + hi.y) * 0.5, -lo.z))
    p('    背高 %.2f  物体 %d 个' % (back_z, len(keep)))
    return tgt, back_z, fit



# ═══════════════════════════════════════════════════════════════
# 5. 循环动作：只写半个循环，剩下的交给镜像 + 复制
# ═══════════════════════════════════════════════════════════════
def mirror_bone(name):
    """Blender「粘贴翻转姿势」的骨骼名规则：左右互换。"""
    for a, b in (('.L', '.R'), ('_L', '_R'), ('Left', 'Right'), ('left', 'right')):
        if name.endswith(a):
            return name[:-len(a)] + b
        if name.endswith(b):
            return name[:-len(b)] + a
    return name


def mirror_xyz(v):
    """翻转姿势的欧拉角规则：X 不动，Y/Z 取负。"""
    return [v[0], -v[1], -v[2]]


def expand_cycle(poses, cyc):
    """把「半个循环的关键姿势」展开成完整循环。

    输入 poses 写的是 0 ≤ f < length/2 的姿势（例如走路：接触帧 0、过渡帧 8），
    这里负责：
      ① 镜像到后半循环（骨骼 .L/.R 互换、Y/Z 取负）—— 等价于 Ctrl+Shift+V
      ② 复制 repeats 次铺满全片 —— 等价于 Shift+D
      ③ 闭环：末帧补上第 0 帧的值（漏了这步，最后一帧会烂成插值垃圾）
    cyc 的键：length 循环帧数 / repeats 复制几遍 / mirror 是否镜像（默认 true）
    """
    length = int(cyc.get('length', 32))
    repeats = max(1, int(cyc.get('repeats', 1)))
    mirror = bool(cyc.get('mirror', True))
    half = length // 2
    frames = {name: {} for name in poses}
    for name, seq in poses.items():
        for f, xyz in seq:
            f = float(f)
            if f >= length:
                continue
            frames.setdefault(name, {})[f] = [float(v) for v in xyz]

    if mirror:
        for name, by_f in list(frames.items()):
            tgt = mirror_bone(name)
            frames.setdefault(tgt, {})
            for f, xyz in list(by_f.items()):
                if f >= half:
                    continue
                frames[tgt].setdefault(f + half, mirror_xyz(xyz))

    # 闭环：每根骨在 length 处必须有键，值取它自己的 f=0
    for name, by_f in frames.items():
        if length not in by_f and 0.0 in by_f:
            by_f[length] = list(by_f[0.0])

    out = {}
    for name, by_f in frames.items():
        keys = []
        for k in range(repeats):
            off = k * length
            for f in sorted(by_f):
                if k > 0 and f == 0.0:
                    continue          # 上一遍的末帧已经是这个值，别打双键
                keys.append((f + off, list(by_f[f])))
        if not keys:
            continue
        out[name] = keys
    return out


def fcurves_of(action):
    """Blender 4.4+ 的分层 Action 没有 .fcurves，得自己钻进去。"""
    fc = getattr(action, 'fcurves', None)
    if fc is not None:
        return list(fc)
    out = []
    for layer in getattr(action, 'layers', None) or []:
        for strip in getattr(layer, 'strips', None) or []:
            for bag in getattr(strip, 'channelbags', None) or []:
                out.extend(list(getattr(bag, 'fcurves', None) or []))
    return out


def mark_linear(arm, frames, only_rot=True):
    """把指定帧上的关键帧改成线性插值 —— 接触帧必须这样，脚才不滑。

    T14 原话：只给「接触帧」的那几个关键帧设 linear，过渡帧不要动。
    """
    ad = arm.animation_data
    if not ad or not ad.action:
        return 0
    want = set(round(float(f), 3) for f in frames)
    n = 0
    for fc in fcurves_of(ad.action):
        if only_rot and 'rotation_euler' not in fc.data_path:
            continue
        for kp in fc.keyframe_points:
            if round(float(kp.co[0]), 3) in want:
                kp.interpolation = 'LINEAR'
                n += 1
    return n


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
    # 封面工程的相机常带 TRACK_TO 约束和对准角色的固定朝向：
    # 角色一飞走，画面就会整体歪掉。约束会盖掉 euler，必须先摘掉。
    for c in list(CAM.constraints):
        CAM.constraints.remove(c)
    CAM.delta_location = (0.0, 0.0, 0.0)
    CAM.delta_rotation_euler = (0.0, 0.0, 0.0)
    ov = dict(override or {})
    # 通用相机覆盖：run/walk/dash 等配方没有 DSH_YJ_* 这条通道，
    # 提示词里的低机位/推近/环绕要对它们也生效。
    def _fenv(key):
        try:
            return float(os.environ.get(key) or 0.0)
        except ValueError:
            return 0.0
    ov['az_add'] = float(ov.get('az_add', 0.0)) + _fenv('DSH_ANIM_CAM_AZ_ADD')
    ov['z_add'] = float(ov.get('z_add', 0.0)) + _fenv('DSH_ANIM_CAM_Z_ADD')
    ov['rise_add'] = float(ov.get('rise_add', 0.0)) + _fenv('DSH_ANIM_CAM_RISE_ADD')
    ov['d_scale'] = float(ov.get('d_scale', 1.0)) * (1.0 + _fenv('DSH_ANIM_CAM_D_SCALE_ADD'))
    ov['lens_scale'] = float(ov.get('lens_scale', 1.0)) * (1.0 + _fenv('DSH_ANIM_CAM_LENS_SCALE_ADD'))
    az = offset_track(cam['az'], ov.get('az_add', 0.0))
    # ★ R08 朝向守卫：本 rig 的 camera.az 与「看得见正脸」不是差 180° 而是差 266°
    #   ——这个数是从渲染实测扫出来的（cast：cam 196° 侧后方、cam 286° 正脸），
    #   不是推导出来的；几何量（眼骨质心−头中心）在本 rig 上给出相反的结论，
    #   所以别用几何式替代它。只对「情绪族」配方生效，跟拍/位移类本来就在身后。
    try:
        _spec = ctx.get('spec') or {}
        _motion = _spec.get('motion') or {}
        _char_az = _motion.get('az')
        _name = _spec.get('name') or ''
        # dl01–dl03 是「对白」：机位本来就该在侧前方跟拍对话，不算背对，
        # 所以不进白名单（否则每次渲对白都刷一条无意义警告）。
        _FACING = ('cast', 'block', 'taunt', 'idle_shift', 'land', 'fall')
        if _char_az is not None and _name in _FACING:
            _d = (float(az[0][1]) - float(_char_az)) % 360.0
            _off = abs(((_d - 266.0 + 180.0) % 360.0) - 180.0)   # 离 266° 多远
            if _off > 45.0:
                print('⚠️  朝向守卫：camera.az %.0f° 与 motion.az %.0f° 相差 %.0f°，'
                      '离实测正脸位（char+266°）偏 %.0f° —— 大概率看不到脸。配方：%s'
                      % (float(az[0][1]), float(_char_az), _d, _off, _name or '?'))
    except Exception:
        pass
    dist = scale_track(cam['dist'], ov.get('d_scale', 1.0))
    lens = scale_track(cam['lens'], ov.get('lens_scale', 1.0))
    rise = [(f, max(0.0, min(1.0, v + ov.get('rise_add', 0.0))))
            for f, v in cam['rise']]
    z_base = float(cam.get('z_base', 2.30)) + ov.get('z_add', 0.0)
    aim_h = float(cam.get('aim_h', 1.00))
    # ★ 运镜 DSL（P1-3）：camera.moves 是一组「在基础轨道上叠加的运动」，
    #   每项 {type, start, dur, amount, ease}，按 smoothstep 从 0 涨到 amount。
    #   type：push_in / pull_out / orbit / crane_up / crane_down / rise /
    #         aim / dutch（画面滚转）/ handheld（微抖）。
    #   以前只能改 az/dist/lens 的起止值，也就是「一条斜线」；有了 moves 才写得出
    #   「推进 + 慢慢绕半圈 + 手持微抖」这种镜头语言，而不用另写 Python。
    moves = cam.get('moves') or []

    def _ease(u, kind):
        u = min(1.0, max(0.0, u))
        if kind == 'linear':
            return u
        if kind == 'in':
            return u * u
        if kind == 'out':
            return 1.0 - (1.0 - u) ** 2
        if kind == 'hold':
            return 1.0 if u > 0 else 0.0
        return u * u * (3.0 - 2.0 * u)      # 默认 smoothstep（缓入缓出）

    def move_sum(f):
        d_mul, az_add, lens_mul = 1.0, 0.0, 1.0
        z_add, rise_add, aim_add, roll = 0.0, 0.0, 0.0, 0.0
        for mv in moves:
            f0 = float(mv.get('start', 1))
            dur = float(mv.get('dur', 0))
            amt = float(mv.get('amount', 0.0))
            u = _ease(1.0 if dur <= 0 and f >= f0 else (f - f0) / max(1e-6, dur),
                      str(mv.get('ease', 'inout')))
            t = str(mv.get('type', ''))
            if t == 'push_in':
                d_mul *= (1.0 - amt * u)
            elif t in ('pull_out', 'dolly'):
                d_mul *= (1.0 + amt * u)
            elif t == 'orbit':
                az_add += amt * u
            elif t == 'crane_up':
                z_add += amt * u
            elif t == 'crane_down':
                z_add -= amt * u
            elif t == 'rise':
                rise_add += amt * u
            elif t == 'aim':
                aim_add += amt * u
            elif t == 'dutch':
                roll += amt * u
            elif t == 'handheld':
                roll += amt * math.sin(f * 0.61) * 0.6
                z_add += amt * 0.35 * math.sin(f * 0.93)
                az_add += amt * 0.5 * math.sin(f * 0.37)
        return d_mul, az_add, lens_mul, z_add, rise_add, aim_add, roll

    step = int(cam.get('key_step', 2))
    dof_f = float(cam.get('dof', 2.4) or 0.0)
    az0, az1 = cam['az'][0][1], cam['az'][-1][1]

    aim_empty = bpy.data.objects.new('AimTarget', None)
    aim_empty.empty_display_size = 0.3
    sc.collection.objects.link(aim_empty)

    UP = mo['UP']
    # 贴地动作的 z 会随地形变化，相机的基准高度必须跟着角色，而不是封面原点。
    base_z = mo['char_world'](1).z
    for f in range(1, ctx['total'] + 1, step):
        c = mo['char_world'](f)
        _d_mul, _az_add, _lens_mul, _z_add, _rise_add, _aim_add, _roll = move_sum(f)
        a = math.radians(track(az, f) + _az_add)
        d = max(0.6, track(dist, f) * _d_mul)
        _rise = min(1.0, max(0.0, track(rise, f) + _rise_add))
        zc = base_z + z_base + _z_add + (c.z - base_z) * _rise
        pos = mu.Vector((c.x + math.cos(a) * d, c.y + math.sin(a) * d, zc))
        aim = c + UP * (aim_h + _aim_add)
        sc.frame_set(f)
        CAM.location = pos
        # 封面工程里相机可能是 QUATERNION 模式，写 euler 会被忽略、画面保持原朝向（歪的）
        CAM.rotation_mode = 'XYZ'
        _q = (aim - pos).to_track_quat('-Z', 'Y')
        if _roll:
            _q = _q @ mu.Quaternion((0.0, 0.0, 1.0), math.radians(_roll))
        CAM.rotation_euler = _q.to_euler()
        CAM.data.lens = track(lens, f) * _lens_mul
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
    if moves:
        p('    运镜 %d 段：%s' % (len(moves), '、'.join(
            '%s @%d+%d帧 %+.2f' % (m.get('type'), m.get('start', 1), m.get('dur', 0),
                                  float(m.get('amount', 0))) for m in moves)))
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
    natural = os.environ.get('DSH_ANIM_NATURAL_LIGHT') == '1'
    fill_spec = spec.get('fill') or {}
    fill_ob = None
    if natural:
        # 保留真实地图时，封面工程那套近距三点光会在雪地/草地上打出一块
        # 摄影棚光斑，和 Minecraft 的天光叠在一起很假。只留太阳和天空，
        # 让角色真的站在世界里，而不是站在灯箱里。
        for ob in list(bpy.data.objects):
            if ob.type == 'LIGHT' and ob.data.type in ('AREA', 'POINT', 'SPOT'):
                ob.hide_render = True
        p('    自然光模式：关闭近距人像灯，只保留太阳/天空')
    elif fill_spec and float(fill_spec.get('energy', 0)) > 0:
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
def clean_stage(ctx, spec):
    """清理封面工程自带的整片地形，给贴地动作换一块干净地面。

    封面工程是为"飞天"做的：地形 246 格宽、雾盒 150 格 —— 角色一贴地就全被土挡死。
    spec 的键：
        hide_world  隐藏所有尺寸超过 50 格的网格（默认 true）
        hide        额外要隐藏的名字片段（小写子串）
        hide_held   隐藏挂在骨头上的非蒙皮件＝盔甲和手里的剑（默认 true）
        ground      建一块棋盘地面：{size, tile, a, b, z}
    """
    sc = ctx['sc']
    base = ctx['base']
    # 提示词出片默认保留封面工程的 Minecraft 地形：
    # run/walk/dash 等配方原本是“棚拍”，会隐藏 world 并铺棋盘地面。
    if os.environ.get('DSH_ANIM_KEEP_WORLD') == '1':
        spec = dict(spec)
        spec['hide_world'] = False
        spec['ground'] = None
        spec['hide_held'] = False
    if os.environ.get('DSH_ANIM_HIDE_WORLD') == '1':
        spec = dict(spec)
        spec['hide_world'] = True
    # ★ B06 修复：缺省值从 True 改成 False。
    #   以前规格里没写 hide_world 就默默把整张地形删掉，渲出来是
    #   灰底/黑底（fall 那支背景全黑就是这么来的）。现在默认保留地形，
    #   要棚拍必须在配方里显式写 "hide_world": true。
    _hide_world = spec.get('hide_world', False)
    frags = [str(s).lower() for s in (spec.get('hide') or [])]
    hidden = 0
    for ob in list(bpy.data.objects):
        if ob.type != 'MESH':
            continue
        name = ob.name.lower()
        if 'boneshape' in name or any(f in name for f in frags):
            ob.hide_render = True
            hidden += 1
            continue
        if _hide_world:
            try:
                if max(ob.dimensions) > 50.0:
                    ob.hide_render = True
                    hidden += 1
            except Exception:
                pass

    # 挂在骨头上的物件（剑 / 盔甲）：parent_type == 'BONE' 且没有 Armature 修改器
    if spec.get('hide_held', True):
        for ob in list(bpy.data.objects):
            if ob.type != 'MESH' or ob.hide_render:
                continue
            if getattr(ob, 'parent_type', None) != 'BONE':
                continue
            if any(m.type == 'ARMATURE' for m in ob.modifiers):
                continue
            ob.hide_render = True
            hidden += 1

    g = spec.get('ground')
    if g:
        size = float(g.get('size', 120.0))
        tile = max(0.25, float(g.get('tile', 1.0)))
        za = float(g.get('z', 0.0))
        mat = bpy.data.materials.new('StageGround')
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = nt.nodes.get('Principled BSDF')
        try:
            tc = nt.nodes.new('ShaderNodeTexCoord')
            ck = nt.nodes.new('ShaderNodeTexChecker')
            ck.inputs['Scale'].default_value = max(1.0, size / tile)
            ck.inputs['Color1'].default_value = tuple(g.get('a', [0.36, 0.38, 0.34])) + (1,)
            ck.inputs['Color2'].default_value = tuple(g.get('b', [0.29, 0.31, 0.28])) + (1,)
            nt.links.new(tc.outputs['Generated'], ck.inputs['Vector'])
            nt.links.new(ck.outputs['Color'], bsdf.inputs['Base Color'])
            bsdf.inputs['Roughness'].default_value = 0.85
        except Exception:
            bsdf.inputs['Base Color'].default_value = (0.32, 0.34, 0.31, 1)
        mesh = bpy.data.meshes.new('StageGround')
        mesh.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)],
                         [], [(0, 1, 2, 3)])
        mesh.update()
        ob = bpy.data.objects.new('StageGround', mesh)
        sc.collection.objects.link(ob)
        ob.location = mu.Vector((base.x, base.y, base.z - 0.002 + za))
        ob.scale = mu.Vector((size * 0.5, size * 0.5, 1.0))
        ob.data.materials.append(mat)
    p('    地形：%s；隐藏 %d 个网格%s'
      % ('隐藏（棚拍档）' if _hide_world else '保留（要棚拍在配方里写 hide_world:true）',
         hidden, '、铺了棋盘地面' if g else ''))


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

    # EEVEE 没有 bloom，用合成器 Glare(Fog Glow) 做溢光。
    # Blender 5.x 的合成器是 socket 化的（输出叫 GROUP_OUTPUT、Glare 的属性
    # 也挪进了 socket），版本差异与“装不上就旁路”全在 glare_compat 里，
    # 确保任何情况下都不会留下断链的合成树（那会输出整帧纯灰）。
    gl_spec = r.get('glare') or {}
    if gl_spec:
        from glare_compat import install_glare
        if install_glare(sc, gl_spec, log=p):
            p('    合成器 Glare: %s / threshold %.1f / size %d'
              % (gl_spec.get('type', 'FOG_GLOW'),
                 float(gl_spec.get('threshold', 1.0)), int(gl_spec.get('size', 7))))

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
# 8b. 时间 / 雾（film 逐镜头覆盖）
# ═══════════════════════════════════════════════════════════════
def _sun_for_hour(hour):
    """把 0..24 的小时数换算成 (仰角°, 颜色 RGB, 灯光能量)。

    ★ 与 build_cover.py 的 DSH_HOUR 公式保持一致（同样的 62°·sin），否则
      「建场景时是 18 点、动画里又按另一套公式摆一次」，两套值会互相打架。

    ★ R05 修复：旧版 `day = elev > 2.0`，而 elev = 62·sin(π(h-6)/12) 在
      h=6 与 h=18 恰好是 0 → 黎明和黄昏都落进「夜里」（月光 1.2W / 冷蓝），
      实测两镜成片亮度 V 0.686 vs 0.694、色相 79.5 vs 78.5，肉眼同色。
      现在改成【连续日光因子】k：仰角 -6°→+8° 之间平滑过渡，不再有硬阈值。
    """
    import math as _m
    h = float(hour) % 24.0
    elev = 62.0 * _m.sin(_m.pi * (h - 6.0) / 12.0)
    k = max(0.0, min(1.0, (elev + 6.0) / 14.0))   # 0=纯夜 1=正午
    night_col = (0.55, 0.68, 1.0)
    if k <= 0.0:
        return max(-8.0, elev), night_col, 1.2
    day_e = 6.0 * min(1.0, max(0.0, elev / 30.0 + 0.25))
    warm = max(0.0, min(1.0, (30.0 - elev) / 30.0))
    day_col = (1.0, 0.70 + 0.25 * (1.0 - warm), 0.42 + 0.45 * (1.0 - warm))
    col = tuple(night_col[i] + (day_col[i] - night_col[i]) * k for i in range(3))
    # ★ R05 第二半：6 点与 18 点仰角都是 0°，光靠仰角永远分不开。
    #   现实里区分黎明/黄昏靠的是色温（晨光偏冷粉、夕照偏橙红），
    #   所以按「离 12 点多远 + 是否过了 12 点」补一档色偏。
    _low = max(0.0, 1.0 - elev / 22.0)           # 越低越强
    if _low > 0.0:
        if h < 12.0:
            _tint = (0.98, 0.86, 0.92)            # 晨：冷粉
        else:
            _tint = (1.00, 0.66, 0.44)            # 昏：橙红
        _w = 0.55 * _low
        col = tuple(col[i] * (1.0 - _w) + _tint[i] * _w for i in range(3))
    energy = max(0.02, 1.2 + (day_e - 1.2) * k)
    return max(-8.0, elev), col, energy


def apply_time_and_fog(p=print):
    """★ B02 修复：让 DSH_HOUR / DSH_FOG 在【逐帧渲染】这条路上真的生效。

    以前 film.py 把 hour/fog 塞进 env，但只有 build_cover.py 建场景时
    读一次；而 anim/film 是在已经烘好的 .blend 上渲帧，世界和灯早定死了，
    于是「每镜不同时间/雾」静默无效。这里在渲染前重新摆一次太阳和雾。
    """
    hour_s = os.environ.get('DSH_HOUR')
    fog_s = os.environ.get('DSH_FOG')
    import math as _m
    if hour_s not in (None, ''):
        try:
            hour = float(hour_s)
        except ValueError:
            p('    !! DSH_HOUR 不是数字: %r（忽略）' % hour_s)
            hour = None
        if hour is not None:
            # 工程本来就是按这个时刻建的 → 不用重复摆灯（build_cover 已摆好）
            try:
                _built = float(bpy.context.scene.get('dsh_hour', -1.0))
            except Exception:
                _built = -1.0
            if _built >= 0 and abs(_built - hour) < 0.01:
                p('    🕒 时间 %g 点（与封面工程一致，沿用已烘好的灯）' % hour)
                hour = None
        if hour is not None:
            elev, col, energy = _sun_for_hour(hour)
            touched = 0
            for ob in bpy.data.objects:
                if ob.type != 'LIGHT':
                    continue
                ld = ob.data
                if ld.type != 'SUN':
                    continue
                e = ob.rotation_euler
                e.x = _m.radians(90.0 - elev)
                ob.rotation_euler = e
                ld.color = col
                ld.energy = max(0.02, energy)
                touched += 1
            p('    🕒 时间 %g 点 → 太阳仰角 %.1f° 色 (%.2f,%.2f,%.2f) 能量 %.2fW（%d 盏）'
              % (hour, elev, col[0], col[1], col[2], energy, touched))
            # ★ R05 第三半：只改太阳改不动画面 —— 场景亮度主要来自天光，
            #   实测 6 点与 18 点成片 RGB 几乎逐通道相同（134.9/152.0/135.3
            #   vs 138.4/154.9/138.7）。所以这里连世界背景一起调：
            #   黄昏压暗 + 暖偏，清晨稍亮 + 冷粉，正午不动。
            try:
                _w = bpy.context.scene.world
                _bg = None
                if _w and _w.use_nodes:
                    for _n in _w.node_tree.nodes:
                        if _n.type == 'BACKGROUND':
                            _bg = _n
                            break
                if _bg is not None:
                    _base = bpy.context.scene.get('dsh_sky_base')
                    if _base is None:
                        _base = float(_bg.inputs['Strength'].default_value)
                        bpy.context.scene['dsh_sky_base'] = _base
                    _base = float(_base)
                    if 7.0 <= hour <= 17.0:
                        _f, _tint, _tw = 1.0, (1.0, 1.0, 1.0), 0.0
                    elif hour < 12.0:
                        _f, _tint, _tw = 0.85, (0.92, 0.95, 1.05), 0.30
                    else:
                        _f, _tint, _tw = 0.55, (1.15, 0.88, 0.70), 0.40
                    _bg.inputs['Strength'].default_value = max(0.02, _base * _f)
                    _bg.inputs['Color'].default_value = (
                        (1.0 - _tw) + _tint[0] * _tw,
                        (1.0 - _tw) + _tint[1] * _tw,
                        (1.0 - _tw) + _tint[2] * _tw, 1.0)
                    p('    🌅 天光 ×%.2f 色偏 (%.2f,%.2f,%.2f)'
                      % (_f, _tint[0], _tint[1], _tint[2]))
            except Exception as _e:
                p('    !! 天光调整失败(%s)' % type(_e).__name__)
    if fog_s not in (None, ''):
        try:
            fog = float(fog_s)
        except ValueError:
            p('    !! DSH_FOG 不是数字: %r（忽略）' % fog_s)
            fog = None
        if fog is not None:
            n = 0
            for ob in bpy.data.objects:
                if not ob.name.startswith('FogVolume'):
                    continue
                ob.hide_render = False
                ob.hide_viewport = False
                for ms in getattr(ob.data, 'materials', []) or []:
                    nt = getattr(ms, 'node_tree', None)
                    if not nt:
                        continue
                    for nd in nt.nodes:
                        if nd.type == 'PRINCIPLED_VOLUME':
                            nd.inputs['Density'].default_value = \
                                0.0012 * max(0.0, fog)
                            n += 1
            p('    🌫 雾倍率 %g → 密度 %.5f（%d 个节点）'
              % (fog, 0.0012 * max(0.0, fog), n))


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
    # 通用配方（run/walk/dash/ride/superman/conduct）的渲染覆盖；
    # 让 mc_film 的 res/samples 对所有 preset 都生效，而不是只有 yujian。
    if os.environ.get('DSH_ANIM_RES'):
        rr['res'] = os.environ['DSH_ANIM_RES']
    if os.environ.get('DSH_ANIM_SAMPLES'):
        try:
            rr['samples'] = int(os.environ['DSH_ANIM_SAMPLES'])
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
    requested = int(total if total is not None else recipe.get('duration', 240))
    recipe_total = int(recipe.get('duration') or 0)
    cyc0 = recipe.get('cycle') or {}
    cyclic = bool(cyc0 and (recipe.get('poses') or {}))
    fit = 1.0
    if cyclic:
        # 循环动作只需要写半个循环：把 repeats 补到请求时长，别截断。
        total = max(requested, int(cyc0.get('length') or 20))
    elif recipe_total > 0 and requested != recipe_total:
        # 非循环配方：先按原时长搭，最后整体重定时到请求时长。
        # 直接按 requested 搭只会播开头（yujian 请求 2 秒 = 只看到起手）。
        total = recipe_total
        fit = (requested - 1) / float(max(1, total - 1))
    build_total = total
    fps = int(fps or recipe.get('fps', 24))
    ctx = scene_ctx(total, fps)
    # 配方本身挂进 ctx：朝向守卫（R08）要对比 motion.az 与 camera.az，
    # 下面 build_camera 只拿到 camera 段，看不到角色朝哪走。
    ctx['spec'] = recipe
    mo_spec = recipe.get('motion') or {'type': 'fly'}

    # 坐骑：先把坐骑摆好，再按它的背高把骑手整体抬起来 ——
    # base 一改，下面的位移曲线自然带着骑手一起升，熊猫自己不动。
    mount_spec = recipe.get('mount') or {}
    mount_arm, mount_seat = None, 0.0
    _mount_loc0 = mu.Vector((0.0, 0.0, 0.0))
    _mount_base0 = mu.Vector((0.0, 0.0, 0.0))
    if mount_spec:
        mount_arm, back_z, _mount_d = load_mount_rig(mount_spec, ctx)
        # 加载时的居中偏移，后面每帧要跟世界位置一起加
        _mount_loc0 = _mount_d.copy()
        mount_seat = back_z - 1.18 + float(mount_spec.get('seat_dz', 0.0))
        ctx['base'] = ctx['base'] + mu.Vector((0.0, 0.0, mount_seat))
        _mount_base0 = ctx['base'].copy()
        # 把坐骑转到跟骑手一致：rig 里是朝 -Y，角色被 base_rz 转过
        try:
            mount_arm.rotation_mode = 'XYZ'
            mount_arm.rotation_euler.z += ctx['base_rz'] + math.radians(
                float(mount_spec.get('yaw', 0.0)))
        except Exception as _e:
            p('    !! 坐骑转向失败：%s' % _e)
        p('    骑手抬高 %.2f（背高 %.2f - 站立臀高 1.18）' % (mount_seat, back_z))

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
        # 坐骑不挂父级（挂上去会继承骑手的转身/侧倾，熊猫会被转成躺倒），
        # 改成逐帧跟随同一个世界位移，姿态保持 rig 原样。
        if mount_arm is not None:
            # 只动 location：脚底贴着骑手基准往下 mount_seat；
            # seat_y 沿角色前后方向算（正 = 往前坐），不能直接加到世界 Y 上，
            # 否则角色一转，骑手就偏到熊猫侧面去了。
            _fwd = mu.Vector((0.0, -1.0, 0.0))
            _fwd.rotate(mu.Euler((0.0, 0.0, ctx['base_rz'])))
            mount_arm.location = (_mount_loc0 + mu.Vector(
                (arm.location.x, arm.location.y, arm.location.z - mount_seat))
                - _fwd * float(mount_spec.get('seat_y', 0.0)))
    p('    升空 %.0f 格 / 前移 %.0f 格 / 转身 %.0f° / 侧倾 %.0f°'
      % (float(mo_spec.get('lift', 0)), float(mo_spec.get('travel', 0)),
         float((mo_spec.get('turn') or {}).get('amount', 0)),
         float((mo_spec.get('bank') or {}).get('scale', 0))))

    # ①b 坐骑的跟随已经在 ① 的逐帧循环里写了（不挂父级，避免继承侧倾）
    if mount_arm is not None:
        p('    坐骑跟随已烘焙（相对骑手 y=%.2f z=%.2f）'
          % (float(mount_spec.get('seat_y', 0.0)), -mount_seat))

    # ② 姿态（给了 cycle 段就只写半个循环，剩下的自动镜像/复制/闭环）
    poses = recipe.get('poses') or {}
    cyc = recipe.get('cycle') or {}
    if cyc and poses:
        cyc_len = int(cyc.get('length', 32))
        cyc_rep = max(1, int(cyc.get('repeats', 1)))
        # 请求时长 > 配方原时长时，循环自动补遍数（run 20 帧 × N 遍）。
        need = max(1, int(math.ceil(total / float(max(1, cyc_len)))))
        if need > cyc_rep:
            cyc = dict(cyc)
            cyc['repeats'] = need
            cyc_rep = need
            p('    循环补到 %d 帧（%d 遍）以填满 %d 帧'
              % (cyc_len * cyc_rep, cyc_rep, total))
        poses = expand_cycle(poses, cyc)
        p('\n[2a] 循环：半循环 %d 帧 × %d 遍 = %d 帧、%d 根骨骼'
          % (cyc_len // 2, cyc_rep, cyc_len * cyc_rep, len(poses)))
        if cyc_len * cyc_rep > total:
            p('    !! 循环共 %d 帧 > 总帧数 %d，尾巴会被截掉'
              % (cyc_len * cyc_rep, total))
    if poses:
        p('\n[2b] 姿态：%d 根骨骼' % len(poses))
        PB = arm.pose.bones
        n = 0
        for key_name, seq in poses.items():
            is_loc = key_name.endswith('@loc')
            name = key_name[:-4] if is_loc else key_name
            pb = PB.get(name)
            if not pb:
                p('    !! 没骨骼 %s' % name)
                continue
            if not is_loc:
                pb.rotation_mode = 'XYZ'
                # BPS Rig：Top Leg / Top Arm 上挂着 "FK <-> IK" 开关，
                # 值是 1（IK）时 FK 旋转**完全无效** —— 只打关键帧不会报错，
                # 但渲染出来角色像滑冰。用 poses 驱动就必须先关回 FK。
                try:
                    if 'FK <-> IK' in pb.keys():
                        pb['FK <-> IK'] = 0.0
                except Exception:
                    pass
            for f, v in seq:
                ctx['sc'].frame_set(int(f))
                if is_loc:
                    pb.location = [float(x) for x in v]
                    pb.keyframe_insert('location', frame=int(f))
                else:
                    pb.rotation_euler = [math.radians(x) for x in v]
                    pb.keyframe_insert('rotation_euler', frame=int(f))
            n += 1
        p('    %d 根打了关键帧' % n)
        # 接触帧设线性：不设的话 Blender 的默认缓入缓出会把脚拖回来（脚滑）
        if cyc and cyc.get('linear', 'contact'):
            cyc_len = int(cyc.get('length', 32))
            cyc_rep = max(1, int(cyc.get('repeats', 1)))
            spec = cyc.get('linear', 'contact')
            base_f = [0, cyc_len // 2] if spec == 'contact' else [int(x) for x in spec]
            frames = [float(b + k * cyc_len)
                      for k in range(cyc_rep) for b in base_f]
            p('    接触帧改线性插值：%d 个关键帧' % mark_linear(arm, frames))

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
            if fn is not None:
                fn(ctx, it, mo)
                p('    - %s ✔' % kind)
                continue
            # 内置五种之外的，交给 fx_kit（wind/glow_eyes/embers/teleport/
            # spawn_in/splash/fog_gate），参数原样透传
            try:
                from fx_kit import FX_REGISTRY
                fk = FX_REGISTRY.get(kind)
                if fk is None and kind in ('glow', 'eyes'):
                    fk = FX_REGISTRY.get('glow_eyes')
                if fk is None:
                    p('    !! 不认识的特效类型: %s（内置 %s + fx_kit %s）'
                      % (kind, '/'.join(FX), '/'.join(FX_REGISTRY)))
                    continue
                fk(ctx['sc'], ctx, {k: v for k, v in it.items() if k != 'type'})
                p('    - %s ✔（fx_kit）' % kind)
            except Exception as _e:
                p('    !! 特效 %s 失败：%s' % (kind, _e))

    # ⑤ 相机
    p('\n[5] 摄影机')
    cam_info = build_camera(ctx, recipe.get('camera') or {
        'az': [[1, 200], [240, 200]], 'dist': [[1, 8], [240, 8]],
        'lens': [[1, 50], [240, 50]], 'rise': [[1, 0.6], [240, 0.6]]},
        mo, recipe.get('_cam_override'))

    # ⑥ 灯光
    p('\n[6] 布光')
    follow_lights(ctx, recipe.get('lights') or {}, mo, cam_info)

    # ⑥b 布景（贴地动作要清地形 + 铺地，飞天的片子别开）
    stage_spec = recipe.get('stage')
    if stage_spec:
        p('\n[6b] 布景')
        clean_stage(ctx, stage_spec)
        for _ob in bpy.data.objects:
            if _ob.name.lower().startswith(('panda', 'fox')):
                p('    坐骑对象 %s 父=%s 世界=(%.1f,%.1f,%.1f) 尺寸=%s hide=%s'
                  % (_ob.name, _ob.parent.name if _ob.parent else '-',
                     _ob.matrix_world.translation.x, _ob.matrix_world.translation.y,
                     _ob.matrix_world.translation.z,
                     tuple(round(v, 2) for v in _ob.dimensions), _ob.hide_render))

    # ⑥c 时间 / 雾（film 逐镜头覆盖靠这两个 env；B02 修复）
    apply_time_and_fog(p)

    # ⑦ 渲染设置
    p('\n[7] 渲染设置')
    render_spec = recipe.get('render') or {}
    setup_render(ctx, render_spec, out_dir)
    # 预设（thumbnail / bilibili / cycles_hq / quick）在最上面覆盖一遍
    if render_spec.get('preset'):
        try:
            import sys as _sys
            _py = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            if _py not in _sys.path:
                _sys.path.insert(0, _py)
            import render_presets
            got = render_presets.apply_preset(ctx['sc'], render_spec['preset'],
                                             render_spec.get('preset_spec') or {})
            p('    预设 %s → %s / %s / %s 采样'
              % (render_spec['preset'], got.get('engine'),
                 got.get('res'), got.get('samples')))
        except Exception as _e:
            p('    !! 渲染预设 %r 失败：%s' % (render_spec.get('preset'), _e))

    # ⑥c 相机能力（抖动 / 环绕 / 镜头切换 / 天空延时）
    cam_spec = recipe.get('camera') or {}
    cam_ops = cam_spec.get('ops') or []
    if cam_ops:
        try:
            from camera_kit import apply_cam_ops
            cam_ob = ctx['sc'].camera
            done = apply_cam_ops(ctx['sc'], cam_ob, ctx, cam_ops)
            names = [d.get('op', str(d)) if isinstance(d, dict) else str(d)
                     for d in (done or [])]
            p('    相机能力：%s' % ', '.join(names))
        except Exception as _e:
            p('    !! 相机能力失败：%s' % _e)

    # ⑧ 非循环配方重定时：动作 / 相机 / 灯光的时间轴一起缩放
    if fit != 1.0 and build_total > 1:
        for act in bpy.data.actions:
            for fc in _action_fcurves(act):
                for kp in fc.keyframe_points:
                    kp.co.x = 1.0 + (kp.co.x - 1.0) * fit
                    kp.handle_left.x = 1.0 + (kp.handle_left.x - 1.0) * fit
                    kp.handle_right.x = 1.0 + (kp.handle_right.x - 1.0) * fit
                fc.update()
        ctx['total'] = requested
        ctx['sc'].frame_end = requested
        p('\n[8] 重定时：%d 帧 → %d 帧（%.2f 秒）'
          % (build_total, requested, requested / float(fps)))
        total = requested

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
