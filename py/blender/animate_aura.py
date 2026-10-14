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

# 主环
bpy.ops.mesh.primitive_torus_add(
    major_radius=RING_R, minor_radius=0.045,
    major_segments=48, minor_segments=8,
    location=(AX, AY, CHEST))
ring = bpy.context.active_object
ring.name = "AuraRing"
ring.data.materials.append(emis_mat("AuraRingMat", CYAN, 2.6))
ring.parent = aura_root
ring.matrix_parent_inverse = aura_root.matrix_world.inverted()

# 6 片刃片（细长立方体，绕环均布，斜插）
blades = []
for i in range(6):
    a = i * math.tau / 6
    bpy.ops.mesh.primitive_cube_add(size=1.0,
                                    location=(AX + math.cos(a) * RING_R,
                                              AY + math.sin(a) * RING_R,
                                              CHEST))
    b = bpy.context.active_object
    b.name = f"AuraBlade{i}"
    b.scale = (0.05, 0.22, 0.58)
    b.rotation_euler = (math.radians(-22), 0, a)
    b.data.materials.append(emis_mat(f"AuraBladeMat{i}", CYAN, 1.8))
    b.parent = aura_root
    b.matrix_parent_inverse = aura_root.matrix_world.inverted()
    blades.append(b)

# 第二层反向环（品红，略小，反向转）
bpy.ops.mesh.primitive_torus_add(
    major_radius=RING_R * 0.78, minor_radius=0.035,
    major_segments=40, minor_segments=8,
    location=(AX, AY, CHEST - 0.55))
ring2 = bpy.context.active_object
ring2.name = "AuraRing2"
ring2.data.materials.append(emis_mat("AuraRing2Mat", MAGENTA, 2.2))
ring2.parent = aura_root
ring2.matrix_parent_inverse = aura_root.matrix_world.inverted()
p(f"    主环 r={RING_R} @ z={CHEST:.2f}；6 片刃片；内层品红环 r={RING_R*0.78:.2f}")

# ============ ② 斩击弧光 ============
p("\n[2] 斩击弧光")
bpy.ops.mesh.primitive_torus_add(
    major_radius=1.55, minor_radius=0.10,
    major_segments=40, minor_segments=6,
    location=(AX, AY, CHEST + 0.15))
slash = bpy.context.active_object
slash.name = "SlashArc"
slash.scale = (1.0, 1.0, 0.35)
sm = emis_mat("SlashMat", (0.75, 1.0, 1.0), 5.0)
slash.data.materials.append(sm)
slash.parent = aura_root
slash.matrix_parent_inverse = aura_root.matrix_world.inverted()
# 用缩放做"闪现"：平时缩到 0
slash.scale = (0.01, 0.01, 0.01)

# ============ ③ 关键帧 ============
p("\n[3] 打关键帧")
pose = arm.pose.bones


def key_bone(name, frames):
    """frames: [(frame, (rx,ry,rz) 度, (sx,sy,sz) 可选)]"""
    pb = pose.get(name)
    if pb is None:
        p(f"    !! 没有骨骼 {name}")
        return
    pb.rotation_mode = 'XYZ'
    for item in frames:
        f, rot = item[0], item[1]
        pb.rotation_euler = tuple(math.radians(v) for v in rot)
        pb.keyframe_insert('rotation_euler', frame=f)
    p(f"    {name}: {len(frames)} 个关键帧")


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

# --- 斩击弧光：只在挥砍瞬间闪现 ---
key_obj(slash, 'scale', [
    (1, (0.01,) * 3),
    (52, (0.01,) * 3),
    (58, (1.15, 1.15, 1.0)),      # 第一刀
    (66, (0.01,) * 3),
    (144, (0.01,) * 3),
    (150, (1.15, 1.15, 1.0)),     # 第二刀
    (160, (0.01,) * 3),
    (TOTAL, (0.01,) * 3),
], is_scale=True)
key_obj(slash, 'rot', [
    (1, (0, 0, 0)), (TOTAL, (0, 0, 720)),
], is_rot=True)

# 插值：挥砍用 BEZIER 默认够用，光环设成线性更均匀
# ⚠️ 坑 R17：Blender 4.4+ 的 Action 没有 .fcurves，必须走
#    layers → strips → channelbags。用共用的兼容层。
from _compat import set_all_interpolation      # noqa: E402
for obj in (aura_root, ring2, slash):
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
for v in ('Standard', 'Filmic', 'AgX'):
    try:
        sc.view_settings.view_transform = v
        break
    except Exception:
        continue
sc.render.filepath = os.path.join(OUT, "frame_")
p(f"    {sc.render.engine} / 48 采样 / 960x540 / {TOTAL} 帧 @ {FPS}fps")

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
