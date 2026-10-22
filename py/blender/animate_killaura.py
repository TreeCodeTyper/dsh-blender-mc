# -*- coding: utf-8 -*-
"""杀戮光环动画 —— 「双剑 · 陀螺 · 升空」

之前两版都不对：第一版是「挥剑 + 挂个发光环」（只是装饰），
第二版做成了「站桩、僵尸走进来被斩」（把主角变成了被动的法阵）。

真正的「杀戮光环」画面感是主角自己的：
  ① 站定、摆一个帅姿势；
  ② **双手各拔出一把剑**（双持）；
  ③ 整个人像陀螺一样原地高速旋转 —— 剑就是那圈「环」；
  ④ 边转边**升空**，脚下的法阵张开；
  ⑤ 围上来的僵尸被旋转的剑风甩飞、缩没。

用法:
  blender -b <封面.blend> --python animate_killaura.py -- <输出目录> [总帧数] [fps] [预览帧]
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
TOTAL = int(argv[1]) if len(argv) > 1 else 240
FPS = int(argv[2]) if len(argv) > 2 else 24
PREVIEW = argv[3] if len(argv) > 3 else ''

L = []


def p(s=''):
    L.append(str(s))
    print(s)


sc = bpy.context.scene
arm = next(o for o in bpy.data.objects if o.type == 'ARMATURE')
sc.render.fps = FPS
sc.frame_start = 1
sc.frame_end = TOTAL
AX, AY, AZ = (arm.matrix_world.translation.x,
              arm.matrix_world.translation.y,
              arm.matrix_world.translation.z)
CAM = sc.camera

p("=" * 70)
p("杀戮光环动画 3.0 —— 「双剑 · 陀螺 · 升空」")
p("=" * 70)
p(f"角色 ({AX:.2f}, {AY:.2f}, {AZ:.2f})   {TOTAL} 帧 @ {FPS}fps = {TOTAL/FPS:.1f} 秒")


# ===================== 工具 =====================
def mat(name, col, emission=0.0, rough=0.9):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*col, 1)
    try:
        b.inputs["Roughness"].default_value = rough
    except Exception:
        pass
    if emission > 0:
        b.inputs["Emission Color"].default_value = (*col, 1)
        b.inputs["Emission Strength"].default_value = emission
    return m


def box(name, size, loc, material, parent=None):
    sx, sy, sz = size
    me = bpy.data.meshes.new(name)
    vs = [(x * sx / 2, y * sy / 2, z * sz / 2)
          for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]
    fs = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1),
          (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    me.from_pydata(vs, [], fs)
    me.update()
    ob = bpy.data.objects.new(name, me)
    ob.location = loc
    ob.data.materials.append(material)
    sc.collection.objects.link(ob)
    if parent is not None:
        ob.parent = parent
    return ob


def sphere(name, r, loc, material):
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, segments=16, ring_count=8, location=loc)
    ob = bpy.context.active_object
    ob.name = name
    ob.data.materials.append(material)
    return ob


def key_obj(ob, keys, is_rot=False, is_scale=False):
    for f, val in keys:
        sc.frame_set(int(f))
        if is_rot:
            ob.rotation_euler = [math.radians(v) for v in val]
            ob.keyframe_insert('rotation_euler', frame=int(f))
        elif is_scale:
            ob.scale = val
            ob.keyframe_insert('scale', frame=int(f))
        else:
            ob.location = val
            ob.keyframe_insert('location', frame=int(f))


def key_pose(pb, f, xyz):
    sc.frame_set(int(f))
    pb.rotation_euler = [math.radians(v) for v in xyz]
    pb.keyframe_insert('rotation_euler', frame=int(f))


PB = arm.pose.bones
if arm.animation_data:
    arm.animation_data_clear()          # 丢掉封面工程里残留的姿势关键帧

# ===================== 时间轴 =====================
T_POSE = 10          # 起势
T_READY = 26         # 双剑出鞘
T_SPIN0 = 56         # 开始旋转
T_LIFT0 = 74         # 开始升空
T_SPIN_END = 210     # 停止旋转
T_TOP = 210          # 升到最高
T_END = TOTAL

H_SPIN = 5.0         # 旋转圈数
H_LIFT = 3.4         # 升空高度（格）

p("\n[1] 双剑")
src = next(o for o in bpy.data.objects if o.type == 'MESH' and o.name.startswith('Sword'))
p(f"    原始剑: {src.name}  parent_bone={src.parent_bone}")

left = src.copy()
left.data = src.data.copy()
left.name = 'Sword: Diamond L'
sc.collection.objects.link(left)
left.parent = arm
left.parent_type = 'BONE'
left.parent_bone = 'Hand.L'
left.matrix_parent_inverse = mathutils.Matrix.Identity(4)
S = mathutils.Matrix.Diagonal((-1.0, 1.0, 1.0, 1.0))
left.matrix_local = S @ src.matrix_local @ S
p("    左手剑已复制并对 X 轴镜像")

# 拔剑前藏起来
for sw in (src, left):
    key_obj(sw, [(1, (0.01, 0.01, 0.01)),
                 (T_READY - 4, (0.01, 0.01, 0.01)),
                 (T_READY, (1.0, 1.0, 1.0)),
                 (T_END, (1.0, 1.0, 1.0))], is_scale=True)

# 拔剑瞬间的两点白光
FL = mat('DrawFlash', (0.92, 1.0, 1.0), emission=9.0)
for i, sx in enumerate((-0.62, 0.62)):
    fl = sphere(f'DrawFlash{i}', 0.22, (AX + sx, AY - 0.1, AZ + 1.2), FL)
    key_obj(fl, [(1, (0.01,) * 3), (T_READY - 2, (0.01,) * 3),
                 (T_READY, (1.6,) * 3), (T_READY + 5, (0.01,) * 3)], is_scale=True)

# ===================== ② 姿势 =====================
p("\n[2] 姿势：双臂抬起、双剑向外上方")
# 右臂是封面工程调好的举剑姿势，左臂按 X/Y 取反镜像
POSE = {
    'Top Arm.R': [(-40, 0, 42), (-6, 0, 62), (-6, 0, 62), (-20, 0, 58), (-20, 0, 58)],
    'Bottom Arm.R': [(-85, 0, 0), (-38, 0, 0), (-38, 0, 0), (-52, 0, 0), (-52, 0, 0)],
    'Top Arm.L': [(-40, 0, -42), (-6, 0, -62), (-6, 0, -62), (-20, 0, -58), (-20, 0, -58)],
    'Bottom Arm.L': [(-85, 0, 0), (-38, 0, 0), (-38, 0, 0), (-52, 0, 0), (-52, 0, 0)],
}
KEYS = [1, T_POSE + 8, T_READY + 6, T_SPIN0 + 20, T_END]
for name, vals in POSE.items():
    pb = PB.get(name)
    if not pb:
        p(f"    !! 没有骨骼 {name}")
        continue
    for f, xyz in zip(KEYS, vals):
        key_pose(pb, f, xyz)
    p(f"    {name}: {vals[0]} → {vals[-1]}")

# 手腕：拔剑后把小臂轴线修正一点，让剑更直立
for name, z in (('Hand.R', 6), ('Hand.L', -6)):
    pb = PB.get(name)
    if pb:
        key_pose(pb, 1, (0, 0, z))
        key_pose(pb, T_END, (0, 0, z))

# ===================== ③ 旋转 + 升空 =====================
p("\n[3] 陀螺旋转 + 升空")
BASE_RZ = math.degrees(arm.rotation_euler.z)   # 封面工程里角色已经摆好 3/4 朝向，别把它清零
p(f"    基准朝向 {BASE_RZ:.1f}°")
key_obj(arm, [(1, (0, 0, BASE_RZ)),
              (T_SPIN0 - 1, (0, 0, BASE_RZ)),
              (T_SPIN0, (0, 0, BASE_RZ - 12)),
              (T_SPIN0 + 24, (0, 0, BASE_RZ - 360 * 0.9)),
              (T_SPIN0 + 60, (0, 0, BASE_RZ - 360 * 2.4)),
              (T_SPIN0 + 100, (0, 0, BASE_RZ - 360 * 3.9)),
              (T_SPIN_END, (0, 0, BASE_RZ - 360 * H_SPIN)),
              (T_END, (0, 0, BASE_RZ - 360 * H_SPIN - 8))], is_rot=True)

key_obj(arm, [(1, (AX, AY, AZ)),
              (T_LIFT0 - 1, (AX, AY, AZ - 0.12)),
              (T_LIFT0, (AX, AY, AZ - 0.10)),
              (T_LIFT0 + 30, (AX, AY, AZ + H_LIFT * 0.42)),
              (T_LIFT0 + 70, (AX, AY, AZ + H_LIFT * 0.86)),
              (T_TOP, (AX, AY, AZ + H_LIFT)),
              (T_END, (AX, AY, AZ + H_LIFT + 0.25))])
p(f"    旋转 {H_SPIN} 圈，升空 {H_LIFT} 格")

# ===================== ④ 地面法阵 =====================
p("\n[4] 地面法阵")
GLOW = (0.20, 0.95, 1.0)
MAG = (1.0, 0.30, 0.85)
R0 = 1.15
mr = mat('KillRing', GLOW, emission=3.0)
ring = bpy.data.objects.new('KillRing', None)
bpy.ops.mesh.primitive_torus_add(
    location=(AX, AY, AZ + 0.04), major_radius=R0, minor_radius=0.055,
    major_segments=96, minor_segments=10)
ring = bpy.context.active_object
ring.name = 'KillRing'
ring.data.materials.append(mr)
ring.scale = (1, 1, 0.26)

mr2 = mat('KillRing2', MAG, emission=2.0)
bpy.ops.mesh.primitive_torus_add(
    location=(AX, AY, AZ + 0.03), major_radius=R0 * 0.72, minor_radius=0.035,
    major_segments=80, minor_segments=8)
ring2 = bpy.context.active_object
ring2.name = 'KillRing2'
ring2.data.materials.append(mr2)
ring2.scale = (1, 1, 0.26)

# 法阵随升空扩散（地面上的圈在「放大」），并在最后爆开
for ob, base in ((ring, 1.0), (ring2, 1.0)):
    key_obj(ob, [(1, (base * 0.4, base * 0.4, 0.26)),
                 (T_POSE, (base * 0.8, base * 0.8, 0.26)),
                 (T_READY, (base, base, 0.26)),
                 (T_SPIN0 + 30, (base * 1.55, base * 1.55, 0.26)),
                 (T_LIFT0 + 60, (base * 2.3, base * 2.3, 0.26)),
                 (T_END, (base * 2.9, base * 2.9, 0.26))], is_scale=True)
    key_obj(ob, [(1, (AX, AY, AZ + 0.04)), (T_END, (AX, AY, AZ + 0.04))])

# ===================== ⑤ 僵尸 =====================
# 这一版只拍主角：105mm 下 6 格外的僵尸会撑满前景，先不放。
p("\n[5] 僵尸：本版不放（只拍主角双剑旋转升空）")


# ===================== ⑥ 相机 =====================
p("\n[6] 摄影机：跟着升空抬高、轻微环绕")
c0 = CAM.location.copy()
fwd = CAM.matrix_world.to_quaternion() @ mathutils.Vector((0, 0, -1))
aim_dist = ((mathutils.Vector((AX, AY, AZ + 1.0)) - c0).length)
aim0 = c0 + fwd * aim_dist
aim = bpy.data.objects.new('CamAim', None)
# 注意：aim 必须正对角色的正上方轴线，不能用 aim0（封面构图本来就偏了一点，
# 人一升空就会跑到画面左侧）。
aim.location = (AX, AY, AZ + 1.15)
aim0 = aim.location.copy()
sc.collection.objects.link(aim)
con = CAM.constraints.new('TRACK_TO')
con.target = aim
con.track_axis = 'TRACK_NEGATIVE_Z'
con.up_axis = 'UP_Y'

key_obj(aim, [(1, (aim0.x, aim0.y, aim0.z)),
              (T_LIFT0 + 30, (aim0.x, aim0.y, aim0.z + H_LIFT * 0.50)),
              (T_TOP, (aim0.x, aim0.y, aim0.z + H_LIFT * 0.97)),
              (T_END, (aim0.x, aim0.y, aim0.z + H_LIFT * 1.0))])

# 相机绕人转 22°、后退 2.2、抬高 1.6
rel = c0 - mathutils.Vector((AX, AY, AZ))
ang0 = math.atan2(rel.y, rel.x)
dist0 = math.hypot(rel.x, rel.y)
cam_keys = []
for f, ang_off, dscale, hz in ((1, 0, 1.0, 0.0),
                               (T_LIFT0 + 40, -12, 1.25, 0.55),
                               (T_TOP, -22, 1.42, 1.55),
                               (T_END, -22, 1.42, 1.60)):
    a = ang0 + math.radians(ang_off)
    d = dist0 * dscale
    cam_keys.append((f, (AX + math.cos(a) * d, AY + math.sin(a) * d, c0.z + hz)))
key_obj(CAM, cam_keys)
p(f"    相机距离 {dist0:.1f} → {dist0*1.42:.1f}（升空时退远，保证全身在画面里）")

# ===================== 渲染输出 =====================
sc.render.image_settings.file_format = 'PNG'
sc.render.filepath = os.path.join(OUT, 'frame_')
sc.view_settings.exposure = 0.05
for look in ('AgX - Punchy', 'Punchy', 'None'):
    try:
        sc.view_settings.look = look
        break
    except Exception:
        continue
try:
    sc.render.engine = 'BLENDER_EEVEE_NEXT'
except Exception:
    pass
try:
    sc.eevee.taa_render_samples = 48
except Exception:
    pass
try:
    sc.render.use_motion_blur = True
    sc.render.motion_blur_shutter = 0.5
except Exception:
    pass

bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT, '杀戮光环.blend'))
p(f"\n工程已保存: {os.path.join(OUT, '杀戮光环.blend')}")

if PREVIEW:
    frames = [int(x) for x in PREVIEW.split(',') if x.strip().isdigit()]
    for f in frames:
        sc.frame_set(f)
        sc.render.filepath = os.path.join(OUT, f'preview_{f:04d}.png')
        bpy.ops.render.render(write_still=True)
        p(f"    预览 {f} → preview_{f:04d}.png")
    sc.render.filepath = os.path.join(OUT, 'frame_')

open(os.path.join(OUT, '_动画日志.txt'), 'w', encoding='utf-8').write('\n'.join(L))
