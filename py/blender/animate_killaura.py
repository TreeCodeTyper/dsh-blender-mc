# -*- coding: utf-8 -*-
"""杀戮光环动画 —— 「不用动手，进圈就死」

上一版做成了「一个人拿剑挥砍 + 身上挂个发光环」，那只是**装饰**，没有讲出
「杀戮光环」这件事本身。这一版按这个词的定义来编排：

  ① 地面上画出一圈**杀戮半径**（发光法阵）—— 这是「光环」在游戏里真正的含义：
     一圈判定范围，不是装饰。
  ② 角色**站着不动**（剑垂在手里，最多转个身）—— 强调「不是他砍的」。
  ③ 6 只僵尸从圈外走进来，**跨进圈内的那一帧**被一道白光横斩 → 倒地 → 消失。
  ④ 每死一只，法阵脉冲一次（范围本身在「工作」）。
  ⑤ 镜头缓慢环绕 + 推进，画面里始终能同时看到「圈」和「圈里的人」。

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
p("杀戮光环动画 2.0 —— 「进圈就死」")
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
    """建一个方块，原点保持在自身中心 —— 合并后方便当整体移动。"""
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


def key_obj(ob, path, keys, is_rot=False, is_scale=False):
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


# ===================== ① 杀戮半径 =====================
p("\n[1] 杀戮半径（地面法阵）")
R_KILL = 3.2                       # 判定半径（太小时内圈贴着人，闪光会糊在主角身上）
GLOW = (0.20, 0.95, 1.0)

bpy.ops.mesh.primitive_torus_add(major_radius=R_KILL, minor_radius=0.055,
                                 major_segments=96, minor_segments=8,
                                 location=(AX, AY, AZ + 0.05))
ring = bpy.context.active_object
ring.name = "KillRadius"
ring.scale = (1, 1, 0.30)
ring.data.materials.append(mat("KillRadiusMat", GLOW, emission=2.4))

# 内圈：让「范围」更像一个场，而不是一根线圈
bpy.ops.mesh.primitive_torus_add(major_radius=R_KILL * 0.72, minor_radius=0.035,
                                 major_segments=72, minor_segments=6,
                                 location=(AX, AY, AZ + 0.04))
ring_in = bpy.context.active_object
ring_in.name = "KillRadiusInner"
ring_in.scale = (1, 1, 0.30)
ring_in.data.materials.append(
    mat("KillRadiusInnerMat", (0.75, 0.35, 1.0), emission=1.5))

# 范围脉冲：每死一只僵尸打一次
PULSE = []


def add_pulse(frame):
    PULSE.append((max(1, frame - 1), 1.0))
    PULSE.append((max(1, frame), 1.055))
    PULSE.append((min(TOTAL, frame + 2), 1.02))
    PULSE.append((min(TOTAL, frame + 12), 1.0))


# ===================== ② 僵尸 =====================
p("\n[2] 僵尸（6 只，从圈外走进来）")
M_SKIN = mat("MobSkin", (0.29, 0.44, 0.24))
M_SHIRT = mat("MobShirt", (0.11, 0.34, 0.32))
M_PANTS = mat("MobPants", (0.15, 0.17, 0.40))
M_EYE = mat("MobEye", (0.02, 0.02, 0.02))


def make_zombie(idx):
    """在角色脚下建一只僵尸，返回它的「根」（空物体，原点在脚底）。

    ⚠️ 两个坑（都踩过）：
      1) 几何体要按【世界坐标】建，否则只有 location 在动、模型留在原点；
      2) 不要用 join 合并 —— 手里这套从 pydata 建的网格 join 之后材质槽会错，
         身体/手臂直接消失。改成每个部件 parent 到空物体、保留各自材质。
    """
    root = bpy.data.objects.new(f"Zombie{idx}", None)
    root.location = (AX, AY, AZ)
    sc.collection.objects.link(root)
    inv = mathutils.Matrix.Translation(-mathutils.Vector(root.location))

    def part(name, size, loc, m):
        o = box(f"z{idx}{name}", size, loc, m)
        o.parent = root
        o.matrix_parent_inverse = inv
        return o

    for sx in (-1, 1):
        part(f"leg{sx}", (0.25, 0.25, 0.75),
             (AX + sx * 0.125, AY, AZ + 0.375), M_PANTS)
    part("body", (0.50, 0.25, 0.75), (AX, AY, AZ + 1.125), M_SHIRT)
    for sx in (-1, 1):
        part(f"arm{sx}", (0.25, 0.25, 0.75),
             (AX + sx * 0.375, AY, AZ + 1.125), M_SKIN)
    part("head", (0.50, 0.50, 0.50), (AX, AY, AZ + 1.75), M_SKIN)
    for sx in (-1, 1):
        part(f"eye{sx}", (0.10, 0.03, 0.10),
             (AX + sx * 0.11, AY - 0.255, AZ + 1.82), M_EYE)
    return root


# 从「相机方向」散开，保证都看得见
cam_d = mathutils.Vector((0.0, 0.0))
if CAM:
    cam_d = mathutils.Vector((CAM.matrix_world.translation.x - AX,
                              CAM.matrix_world.translation.y - AY))
if cam_d.length < 0.01:
    cam_d = mathutils.Vector((0.0, -1.0))
cam_ang = math.atan2(cam_d.y, cam_d.x)
OFFSETS = [-68, -45, 45, 68, 150, 210]   # 避开正前方 ±30°（会糊在主角身上）

R_SPAWN = 5.2
R_ARRIVE = R_KILL - 0.30          # 停在圈内一点点，才像"跨进来就被斩"
kill_frames = []
zombies = []
for i, off in enumerate(OFFSETS):
    a = math.radians(cam_ang + off)
    z = make_zombie(i)
    x0, y0 = AX + math.cos(a) * R_SPAWN, AY + math.sin(a) * R_SPAWN
    x1, y1 = AX + math.cos(a) * R_ARRIVE, AY + math.sin(a) * R_ARRIVE
    t0 = 12 + i * 20
    t_hit = t0 + 52
    kill_frames.append(t_hit)
    # 朝向：一半朝圆心、一半朝镜头。纯朝圆心会让侧面的僵尸只剩 0.25 格厚，
    # 纯朝镜头又不像在进攻；混一半最像「扑上来的僵尸」。
    _zc = mathutils.Vector((x1, y1))
    _to_c = (mathutils.Vector((AX, AY)) - _zc).normalized()
    _to_cam = (mathutils.Vector((CAM.location.x, CAM.location.y)) - _zc).normalized()
    _fd = (_to_c * 0.55 + _to_cam * 0.45).normalized()
    _rz = math.degrees(math.atan2(_fd.y, _fd.x)) + 90.0   # 局部 -Y 是脸
    z.rotation_euler = (0, 0, math.radians(_rz))
    key_obj(z, 'location', [
        (1, (x0, y0, AZ)),
        (t0, (x0, y0, AZ)),
        (t_hit, (x1, y1, AZ)),
        (t_hit + 6, (x1 * 0.995 + AX * 0.005, y1 * 0.995 + AY * 0.005, AZ)),
        (t_hit + 26, (x1, y1, AZ - 0.55)),
    ])
    # 走路的上下颠簸
    bob = []
    for f in range(t0, t_hit + 1, 8):
        bob.append((f, (x0 + (x1 - x0) * (f - t0) / max(1, t_hit - t0),
                        y0 + (y1 - y0) * (f - t0) / max(1, t_hit - t0),
                        AZ + 0.05 * abs(math.sin((f - t0) * 0.5)))))
    key_obj(z, 'location', bob)
    # 倒地：绕 X 前扑，然后缩没（Z 轴保持 _rz，别写回数学角度 a，否则朝向被关键帧覆盖）
    key_obj(z, 'rot', [
        (1, (0, 0, _rz)),
        (t_hit, (0, 0, _rz)),
        (t_hit + 7, (78, 0, _rz)),
        (TOTAL, (78, 0, _rz)),
    ], is_rot=True)
    key_obj(z, 'scale', [
        (1, (1, 1, 1)),
        (t_hit + 20, (1, 1, 1)),
        (t_hit + 30, (0.01, 0.01, 0.01)),
    ], is_scale=True)
    add_pulse(t_hit + 2)
    # 斩杀闪光：一个会爆开的光球。
    # ⚠️ 一开始用的是一条薄板当"刀光"——它法线朝哪边就看运气，侧面时等于隐形。
    #    球体任何角度都看得见，配运动模糊就是"被斩中的一瞬"。
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.42, segments=16, ring_count=8,
                                         location=(x1, y1, AZ + 1.0))
    fl = bpy.context.active_object
    fl.name = f"KillFlash{i}"
    fl.data.materials.append(mat(f"KillFlashMat{i}", (0.90, 1.0, 1.0), emission=8.0))
    key_obj(fl, 'scale', [
        (1, (0.01, 0.01, 0.01)),
        (t_hit - 1, (0.01, 0.01, 0.01)),
        (t_hit, (0.6, 0.6, 0.6)),
        (t_hit + 2, (1.1, 1.1, 1.1)),
        (t_hit + 4, (1.3, 1.3, 1.3)),
        (t_hit + 6, (0.01, 0.01, 0.01)),
    ], is_scale=True)
    zombies.append(z)
    p(f"    僵尸{i}: 角度 {math.degrees(a):.0f}°  出场 f{t0}  进圈被斩 f{t_hit}")

key_obj(ring, 'scale', [(1, (1, 1, 0.30)), (TOTAL, (1, 1, 0.30))], is_scale=True)
# 脉冲叠加在 1.0 基线上
ring_scale = [(1, (1, 1, 0.30))]
for f, s in PULSE:
    ring_scale.append((f, (s, s, 0.30 * (1 + (s - 1) * 0.5))))
key_obj(ring, 'scale', ring_scale, is_scale=True)
key_obj(ring_in, 'scale', [(1, (1, 1, 0.30))]
        + [(f, (s * 0.98, s * 0.98, 0.30)) for f, s in PULSE], is_scale=True)

# ===================== ③ 角色：站着别动 =====================
p("\n[3] 角色：全程不动手（只轻微转身 / 呼吸）")
pose = arm.pose.bones


def kbone(name, path, keys):
    b = pose.get(name)
    if b is None:
        return False
    for f, val in keys:
        sc.frame_set(int(f))
        setattr(b, path, val if path == 'location' else
                (val if isinstance(val, tuple) else val))
        b.keyframe_insert(path, frame=int(f))
    return True


# 躯干极轻微起伏 —— 只有「活着」，没有「出手」
if 'Torso' in pose:
    for f in range(1, TOTAL + 1, 24):
        sc.frame_set(f)
        t = f / TOTAL
        pose['Torso'].rotation_euler = (math.radians(1.2 * math.sin(t * 6.28)),
                                        0, math.radians(1.5 * math.sin(t * 3.14)))
        pose['Torso'].keyframe_insert('rotation_euler', frame=f)

# 角色整体慢慢转一点，像在「环视」
key_obj(arm, 'rot', [
    (1, (arm.rotation_euler.x, arm.rotation_euler.y,
         math.degrees(arm.rotation_euler.z))),
    (TOTAL, (arm.rotation_euler.x, arm.rotation_euler.y,
             math.degrees(arm.rotation_euler.z) + 26)),
], is_rot=True)

# ===================== ④ 渲染设置 / 运镜 =====================
p("\n[4] 渲染设置 + 运镜")
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
try:
    sc.render.use_motion_blur = True
    sc.render.motion_blur_shutter = 0.5
    sc.render.motion_blur_position = 'CENTER'
except Exception as _e:
    p(f"    !! 运动模糊不可用: {_e}")

# 环绕 26° + 推进 1.4：画面一直在动，但始终把「圈」框住
# ⚠️ 只挪相机位置会让构图整个跑掉（相机朝向不变，等于把镜头"推开"再看别处）。
#    做法：先用当前朝向算一个「注视点」空物体，给相机加 Track To 把它钉住。
if CAM:
    c0 = mathutils.Vector(CAM.matrix_world.translation)
    fwd = CAM.matrix_world.to_quaternion() @ mathutils.Vector((0.0, 0.0, -1.0))
    aim_dist = (mathutils.Vector((AX, AY, AZ + 1.0)) - c0).length
    aim = c0 + fwd * aim_dist
    tgt = bpy.data.objects.new("CamAim", None)
    tgt.location = aim
    sc.collection.objects.link(tgt)
    con = CAM.constraints.new('TRACK_TO')
    con.target = tgt
    con.track_axis = 'TRACK_NEGATIVE_Z'
    con.up_axis = 'UP_Y'
    rel = mathutils.Vector((c0.x - AX, c0.y - AY, 0.0))
    rad = rel.length
    a0 = math.atan2(rel.y, rel.x)
    h0 = c0.z
    keys = []
    for f in (1, TOTAL // 2, TOTAL):
        t = (f - 1) / max(1, TOTAL - 1)
        a = a0 + math.radians(26) * t
        r = rad - 1.4 * t
        keys.append((f, (AX + math.cos(a) * r, AY + math.sin(a) * r,
                         h0 + 0.35 * t)))
    key_obj(CAM, 'location', keys)
    p(f"    运镜：环绕 26° / 推进 1.4 格 / 升高 0.35（半径 {rad:.1f}，注视点已锁定）")

sc.render.filepath = os.path.join(OUT, "frame_")
p(f"    {sc.render.engine} / 48 采样 / 960x540 / {TOTAL} 帧 @ {FPS}fps / {vt}")

# 插值收拾
try:
    from _compat import set_all_interpolation
    for ob in [ring, ring_in, arm] + zombies:
        ad = ob.animation_data
        if ad and ad.action:
            set_all_interpolation(ad.action, 'LINEAR')
except Exception as _e:
    p(f"    !! 插值兼容层: {_e}")

blend = os.path.join(OUT, "杀戮光环.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
p(f"\n工程: {blend}")

# ===================== ⑤ 渲染 =====================
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
