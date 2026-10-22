# -*- coding: utf-8 -*-
"""杀戮光环动画 —— 10 秒带动作的 MC 战斗镜头

在封面工程的基础上加：
  ① 杀戮光环：胸口高度的发光圆环 + 6 片旋转刃片（自转 + 脉动）
  ② 剑挥砍  ：给 BPS 的手臂骨骼打关键帧（FK 模式）
  ③ 身体    ：躯干扭转 + 起伏
  ④ 斩击弧光：挥砍瞬间闪现的发光弧

用法:
  blender -b <封面.blend> --python 杀戮光环.py -- <输出目录> [总帧数] [fps] [预览帧列表]
"""
import math
import os
import sys

import bpy
import mathutils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = argv[0]
os.makedirs(OUT, exist_ok=True)
TOTAL = int(argv[1]) if len(argv) > 1 else 240     # 240 帧 @24fps = 10 秒
FPS = int(argv[2]) if len(argv) > 2 else 24
PREVIEW = argv[3] if len(argv) > 3 else ''         # 如 "1,60,72,90,150" 只渲这些帧
L = []


def p(s=''):
    L.append(str(s))
    print(s)


def eval_bbox(objs):
    dg = bpy.context.evaluated_depsgraph_get()
    pts = []
    for o in objs:
        if o.type != 'MESH':
            continue
        oe = o.evaluated_get(dg)
        try:
            me = oe.to_mesh()
        except Exception:
            continue
        if me is None:
            continue
        pts += [oe.matrix_world @ v.co for v in me.vertices]
        oe.to_mesh_clear()
    if not pts:
        return None, None
    return (mathutils.Vector((min(q.x for q in pts), min(q.y for q in pts), min(q.z for q in pts))),
            mathutils.Vector((max(q.x for q in pts), max(q.y for q in pts), max(q.z for q in pts))))


p("=" * 70)
p("杀戮光环动画")
p("=" * 70)

arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
sc = bpy.context.scene
sc.render.fps = FPS
sc.frame_start = 1
sc.frame_end = TOTAL
AX, AY, AZ = (arm.matrix_world.translation.x,
              arm.matrix_world.translation.y,
              arm.matrix_world.translation.z)
p(f"\n骨架 ({AX:.2f}, {AY:.2f}, {AZ:.2f})   动画 {TOTAL} 帧 @ {FPS}fps "
  f"= {TOTAL/FPS:.1f} 秒")

# ============ ① 杀戮光环 ============
p("\n[1] 建杀戮光环")
CHEST = AZ + 1.05        # 胸口高度（骨架原点在脚底附近）
RING_R = 1.15


def emis_mat(name, col, strength=8.0):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*col, 1)
    b.inputs["Emission Color"].default_value = (*col, 1)
    b.inputs["Emission Strength"].default_value = strength
    try:
        b.inputs["Roughness"].default_value = 0.4
    except Exception:
        pass
    return m


CYAN = (0.15, 0.95, 1.0)
MAGENTA = (1.0, 0.25, 0.85)

aura_root = bpy.data.objects.new("AuraRoot", None)
sc.collection.objects.link(aura_root)
aura_root.location = (AX, AY, CHEST)

# 主环（头顶光环）
# ⚠️ 0.3.0：原来环心在 CHEST、半径 1.15 —— 正对镜头时环是【一根横穿胸口的白条】，
#   挡人物、还因为 emission 太高直接过曝成白板。改成肩上方的小环（贴头像光环），
#   压扁一点、亮度降到 1.8，让轮廓留在画面上部。
RING_Z = CHEST + 0.95
RING_R2 = 0.70
bpy.ops.mesh.primitive_torus_add(
    major_radius=RING_R2, minor_radius=0.04,
    major_segments=48, minor_segments=8,
    location=(AX, AY, RING_Z))
ring = bpy.context.active_object
ring.name = "AuraRing"
ring.scale = (1.0, 1.0, 0.55)
ring.data.materials.append(emis_mat("AuraRingMat", CYAN, 1.8))
ring.parent = aura_root
ring.matrix_parent_inverse = aura_root.matrix_world.inverted()

# 8 片刃片（细长立方体，绕环均布，斜插）
# ⚠️ 0.3.0：原来是 6 片 0.05×0.22×0.58 的大板子，渲出来像"纸板风车"。
#   改成 8 片更细更短的碎片，亮度 2.2（不再过曝成白板）。
BLADE_N = 8
BLADES = []
for i in range(BLADE_N):
    a = i * math.tau / BLADE_N
    bpy.ops.mesh.primitive_cube_add(size=1.0,
                                    location=(AX + math.cos(a) * RING_R2,
                                              AY + math.sin(a) * RING_R2,
                                              RING_Z))
    b = bpy.context.active_object
    b.name = f"AuraBlade{i}"
    b.scale = (0.03, 0.07, 0.30)
    b.rotation_euler = (math.radians(-28), 0, a)
    b.data.materials.append(emis_mat(f"AuraBladeMat{i}", CYAN, 2.2))
    b.parent = aura_root
    b.matrix_parent_inverse = aura_root.matrix_world.inverted()
    BLADES.append(b)

# 地面魔法阵（品红）
# ⚠️ 0.3.0：原来是 CHEST-0.55，正好切在大腿上（渲出来像呼啦圈穿腿）。
#   改成脚底贴地的薄环。
GROUND = AZ + 0.06
bpy.ops.mesh.primitive_torus_add(
    major_radius=RING_R * 1.25, minor_radius=0.035,
    major_segments=64, minor_segments=8,
    location=(AX, AY, GROUND))
ring2 = bpy.context.active_object
ring2.name = "AuraRing2"
ring2.scale = (1.0, 1.0, 0.35)
ring2.data.materials.append(emis_mat("AuraRing2Mat", MAGENTA, 2.8))
ring2.parent = aura_root
ring2.matrix_parent_inverse = aura_root.matrix_world.inverted()
p(f"    主环 r={RING_R} @ z={CHEST:.2f}；{BLADE_N} 片刃片；"
  f"地面魔法阵 r={RING_R*1.25:.2f} @ z={GROUND:.2f}")

# ============ ② 斩击弧光（0.3.0 起停用）============
# ⚠️ 0.3.0：原来是一个 r=1.55 的水平圆环，靠缩放做"闪现"。
#   正对镜头时它被看成【一根横贯画面、过曝成纯白的长条】，一次挥砍闪三帧，
#   比"没有特效"还糟。与其留着一个必然穿帮的环，不如去掉，
#   让运动模糊 + 光环自己承担节奏。
p("\n[2] 斩击弧光：已停用（旧的水平环在正面机位会糊成白条）")
slash = None

# ============ ③ 关键帧 ============
p("\n[3] 打关键帧")
pose = arm.pose.bones


def key_bone(name, frames):
    """frames: [(frame, (rx,ry,rz) 度, (sx,sy,sz) 可选)]

    ★ 0.2.0：关键帧是【相对封面工程的静态姿势】的增量。
      封面里右臂已经被摆成举剑姿势（build_cover 的 [4.8]），
      如果这里写绝对值，第一帧就会把手臂打回垂手，剑又横在胸前。
    """
    pb = pose.get(name)
    if pb is None:
        p(f"    !! 没有骨骼 {name}")
        return
    pb.rotation_mode = 'XYZ'
    base = tuple(pb.rotation_euler)      # 当前静态姿势（没有摆过就是 0）
    for item in frames:
        f, rot = item[0], item[1]
        pb.rotation_euler = tuple(base[i] + math.radians(rot[i]) for i in range(3))
        pb.keyframe_insert('rotation_euler', frame=f)
    p(f"    {name}: {len(frames)} 个关键帧（基准 "
      f"{tuple(round(math.degrees(v), 1) for v in base)}°）")


def key_obj(obj, path, frames, is_rot=False, is_scale=False):
    for f, v in frames:
        if is_rot:
            obj.rotation_euler = tuple(math.radians(x) for x in v)
            obj.keyframe_insert('rotation_euler', frame=f)
        elif is_scale:
            obj.scale = v
            obj.keyframe_insert('scale', frame=f)
        else:
            obj.location = v
            obj.keyframe_insert('location', frame=f)


# --- 手臂挥砍：右臂（握剑手）---
# BPS 默认手臂是 FK。绕 X 一般是前后摆，绕 Z 是内外张。
key_bone('Top Arm.R', [
    (1,    (0, 0, 0)),
    (36,   (-95, 0, -25)),      # 蓄力：抬手后拉
    (52,   (-95, 0, -25)),      # 停顿
    (60,   (55, 0, 20)),        # 挥下（快）
    (68,   (60, 0, 22)),        # 收
    (92,   (10, 0, 0)),         # 回中
    (128,  (-70, 0, -40)),      # 第二刀蓄力（反手）
    (150,  (65, 0, 28)),        # 第二刀挥出
    (170,  (10, 0, 0)),
    (200,  (0, 0, 0)),
    (TOTAL, (0, 0, 0)),
])
key_bone('Bottom Arm.R', [
    (1,    (0, 0, 0)),
    (36,   (-45, 0, 0)),
    (52,   (-45, 0, 0)),
    (60,   (15, 0, 0)),
    (68,   (18, 0, 0)),
    (92,   (0, 0, 0)),
    (128,  (-35, 0, 0)),
    (150,  (12, 0, 0)),
    (170,  (0, 0, 0)),
    (TOTAL, (0, 0, 0)),
])
# --- 左臂：配合摆动 ---
key_bone('Top Arm.L', [
    (1, (0, 0, 0)), (60, (25, 0, 15)), (92, (0, 0, 0)),
    (150, (25, 0, 15)), (170, (0, 0, 0)), (TOTAL, (0, 0, 0)),
])
# --- 躯干扭转 ---
key_bone('Torso', [
    (1, (0, 0, 0)), (36, (0, -18, 0)), (60, (0, 22, 0)),
    (92, (0, 0, 0)), (128, (0, 20, 0)), (150, (0, -24, 0)),
    (170, (0, 0, 0)), (TOTAL, (0, 0, 0)),
])

# --- 光环自转（全程连续）---
key_obj(aura_root, 'rot', [
    (1, (0, 0, 0)), (TOTAL, (0, 0, 360 * 3)),   # 10 秒转 3 圈
], is_rot=True)
key_obj(ring2, 'rot', [
    (1, (0, 0, 0)), (TOTAL, (0, 0, -360 * 5)),  # 内环反向转 5 圈
], is_rot=True)
# 光环脉动（呼吸感）
pulse = []
for i in range(0, TOTAL + 1, 20):
    t = i / TOTAL
    s = 1.0 + 0.12 * math.sin(t * math.pi * 2 * 5)
    pulse.append((max(1, i), (s, s, s)))
key_obj(aura_root, 'scale', pulse, is_scale=True)

# --- 斩击弧光已停用（slash is None），挥砍节奏交给运动模糊 + 光环 ---

# 插值：挥砍用 BEZIER 默认够用，光环设成线性更均匀
# ⚠️ 坑 R17：Blender 4.4+ 的 Action 没有 .fcurves，必须走
#    layers → strips → channelbags。用共用的兼容层。
from _compat import set_all_interpolation      # noqa: E402
for obj in (aura_root, ring2):
    ad = obj.animation_data
    if ad and ad.action:
        n = set_all_interpolation(ad.action, 'LINEAR')
        p(f"    {obj.name}: {n} 个关键帧设为线性")

p(f"    共 {len(bpy.data.actions)} 个动作")

# ============ ④ 渲染设置 ============
p("\n[4] 渲染设置")
for cand in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
    try:
        sc.render.engine = cand
        break
    except Exception:
        continue
try:
    sc.eevee.taa_render_samples = 48
except Exception:
    pass
sc.render.resolution_x, sc.render.resolution_y = 960, 540
sc.render.image_settings.file_format = 'PNG'
sc.render.film_transparent = False
# ⚠️ 坑 R21：原来这里写 `for v in ('Standard','Filmic','AgX')` —— Standard 永远成功，
#   于是动画一律走 Standard，和 AgX 渲出来的封面完全是两个颜色（灰、平、死白）。
try:
    from _compat import set_view_transform
    vt = set_view_transform(sc, prefer='AgX')
except Exception as _e:
    vt = None
    p(f"    !! 色彩管理兼容层不可用: {_e}")
if not vt:
    for v in ('AgX', 'Filmic', 'Standard'):
        try:
            sc.view_settings.view_transform = v
            break
        except Exception:
            continue
try:
    sc.view_settings.look = 'AgX - Punchy'
except Exception:
    pass
# 运动模糊：挥砍只有 4~6 帧，没有模糊的话看起来像"瞬移"
try:
    sc.render.use_motion_blur = True
    sc.render.motion_blur_shutter = 0.45
    sc.render.motion_blur_position = 'CENTER'
except Exception as _e:
    p(f"    !! 运动模糊不可用: {_e}")
# 辉光：EEVEE 没有 bloom，用合成器 Glare（Fog Glow）给能量环/刃片做溢光
# ⚠️ Blender 5.x 把合成器节点树挪到了 scene.compositing_node_group，
#    4.x 及更早是 scene.node_tree —— 两个都试，都没有就跳过（不影响出图）。
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
    gl.glare_type = 'FOG_GLOW'
    gl.quality = 'HIGH'
    gl.threshold = 1.0
    gl.size = 7
    gl.mix = -0.35
    nt.links.new(rl.outputs['Image'], gl.inputs['Image'])
    nt.links.new(gl.outputs['Image'], comp.inputs['Image'])
    p("    合成器 Glare: Fog Glow / threshold 1.0 / size 7")
except Exception as _e:
    p(f"    !! 辉光节点不可用: {_e}")

# 运镜：10 秒缓慢推进 + 轻微升高（原来相机完全静止，视频像一张会动的静帧）
cam = sc.camera
if cam:
    c0 = tuple(cam.location)
    d = mathutils.Vector((AX - c0[0], AY - c0[1], 0.0))
    if d.length > 0.01:
        d.normalize()
        push = d * 2.6          # 推进 2.6 格
        key_obj(cam, 'location', [
            (1, c0),
            (max(2, TOTAL // 2), (c0[0] + push.x * 0.45, c0[1] + push.y * 0.45, c0[2] + 0.10)),
            (TOTAL, (c0[0] + push.x, c0[1] + push.y, c0[2] + 0.22)),
        ])
        p(f"    运镜：推进 {push.length:.1f} 格 + 升高 0.22")

sc.render.filepath = os.path.join(OUT, "frame_")
p(f"    {sc.render.engine} / 48 采样 / 960x540 / {TOTAL} 帧 @ {FPS}fps / {vt}")

blend = os.path.join(OUT, "杀戮光环.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
p(f"\n工程: {blend}")

# ============ ⑤ 渲染 ============
if PREVIEW:
    frames = [int(x) for x in PREVIEW.split(',') if x.strip()]
    p(f"\n[5] 预览渲染 {len(frames)} 帧: {frames}")
    for f in frames:
        sc.frame_set(f)
        sc.render.filepath = os.path.join(OUT, f"preview_{f:04d}")
        bpy.ops.render.render(write_still=True)
        p(f"    帧 {f} 完成")
else:
    p("\n[5] 用 blender -b 渲染动画序列（不在本脚本内做）")
    p(f"    blender -b \"{blend}\" -a")

open(os.path.join(OUT, '_动画日志.txt'), 'w', encoding='utf-8').write('\n'.join(L))
