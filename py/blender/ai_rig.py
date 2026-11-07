"""原创 AI 人偶 v0.1：可被 CharacterSpec 驱动的方块角色。

不复制 BPS/BSS 的网格、骨骼、权重或代码；只参考公开的“root/pelvis/IK 脚目标/
FK-IK 切换”工作流。自身是程序化生成的方块人，分段关节 + IK 手脚，先验证
提示词 -> spec -> 动作 这条链路。

用法：
    blender -b --factory-startup -P py/blender/ai_rig.py -- --build out.blend
    blender -b out.blend -P py/blender/ai_rig.py -- --spec spec.json --out render.png
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import bmesh
import bpy
from mathutils import Matrix, Vector

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from char_control import validate_spec  # noqa: E402

# 角色尺寸（Minecraft 格）
HIP_Z = 0.75
ANKLE_Z = 0.08
SHOULDER_Z = 1.50
HEAD_Z = 1.75

# 颜色：先做“黏土/方块”版，M2 再换 64x64 皮肤 UV。
C_SKIN = (0.80, 0.62, 0.48, 1.0)
C_HAIR = (0.22, 0.13, 0.08, 1.0)
C_SHIRT = (0.10, 0.55, 0.55, 1.0)
C_PANTS = (0.12, 0.20, 0.55, 1.0)
C_SHOE = (0.18, 0.18, 0.20, 1.0)
C_EYE_W = (0.92, 0.92, 0.90, 1.0)
C_EYE_B = (0.10, 0.16, 0.50, 1.0)
C_MOUTH = (0.35, 0.16, 0.14, 1.0)
C_GROUND = (0.42, 0.52, 0.32, 1.0)


def _mat(name: str, rgba: tuple[float, float, float, float],
         rough: float = 0.75) -> bpy.types.Material:
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes
                 if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf:
        bsdf.inputs['Base Color'].default_value = rgba
        bsdf.inputs['Roughness'].default_value = rough
    mat.diffuse_color = rgba
    return mat


def _box(name: str, center: tuple[float, float, float],
         size: tuple[float, float, float],
         mat: bpy.types.Material) -> bpy.types.Object:
    cx, cy, cz = center
    sx, sy, sz = (s * 0.5 for s in size)
    verts = [
        (cx - sx, cy - sy, cz - sz), (cx + sx, cy - sy, cz - sz),
        (cx + sx, cy + sy, cz - sz), (cx - sx, cy + sy, cz - sz),
        (cx - sx, cy - sy, cz + sz), (cx + sx, cy - sy, cz + sz),
        (cx + sx, cy + sy, cz + sz), (cx - sx, cy + sy, cz + sz),
    ]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    ob = bpy.data.objects.new(name, mesh)
    ob.data.materials.append(mat)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _weight(ob: bpy.types.Object, bone: str) -> None:
    vg = ob.vertex_groups.new(name=bone)
    vg.add(list(range(len(ob.data.vertices))), 1.0, 'REPLACE')


def _add_face(parts: dict[str, bpy.types.Object], mat_white, mat_blue,
              mat_mouth, mat_hair) -> None:
    # 脸在 -Y 面。眼睛/眉毛/嘴都是独立小方块，先不做表情骨。
    y = -0.255
    for sx in (-0.13, 0.13):
        parts[f'eye_{sx:+.2f}'] = _box(
            f'eye_{sx:+.2f}', (sx, y, 1.90), (0.10, 0.015, 0.08), mat_white)
        parts[f'pupil_{sx:+.2f}'] = _box(
            f'pupil_{sx:+.2f}', (sx, y - 0.006, 1.90), (0.05, 0.012, 0.06), mat_blue)
        parts[f'brow_{sx:+.2f}'] = _box(
            f'brow_{sx:+.2f}', (sx, y - 0.004, 1.985), (0.12, 0.014, 0.025), mat_hair)
    parts['mouth'] = _box('mouth', (0.0, y - 0.004, 1.79),
                          (0.16, 0.014, 0.018), mat_mouth)


def build(path: str | Path, res: str = "720x1280") -> bpy.types.Object:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.view_settings.view_transform = 'AgX'
    scene.render.resolution_x, scene.render.resolution_y = (int(x) for x in res.split('x'))
    scene.render.image_settings.file_format = 'PNG'
    scene.world = bpy.data.worlds.new('World')
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs[0].default_value = (0.55, 0.66, 0.78, 1.0)
        bg.inputs[1].default_value = 0.8

    mat_skin = _mat('Skin', C_SKIN)
    mat_hair = _mat('Hair', C_HAIR, 0.85)
    mat_shirt = _mat('Shirt', C_SHIRT)
    mat_pants = _mat('Pants', C_PANTS)
    mat_shoe = _mat('Shoe', C_SHOE, 0.9)
    mat_white = _mat('EyeWhite', C_EYE_W, 0.4)
    mat_blue = _mat('EyeBlue', C_EYE_B, 0.35)
    mat_mouth = _mat('Mouth', C_MOUTH, 0.8)

    parts: dict[str, bpy.types.Object] = {}
    parts['head'] = _box('head', (0, 0, HEAD_Z), (0.50, 0.50, 0.50), mat_skin)
    parts['hair_top'] = _box('hair_top', (0, 0.02, HEAD_Z + 0.27),
                             (0.52, 0.48, 0.08), mat_hair)
    parts['hair_back'] = _box('hair_back', (0, 0.24, HEAD_Z + 0.02),
                              (0.52, 0.06, 0.46), mat_hair)
    _add_face(parts, mat_white, mat_blue, mat_mouth, mat_hair)

    parts['neck'] = _box('neck', (0, 0, 1.60), (0.16, 0.16, 0.10), mat_skin)
    parts['chest'] = _box('chest', (0, 0, 1.33), (0.50, 0.26, 0.40), mat_shirt)
    parts['waist'] = _box('waist', (0, 0, 1.06), (0.46, 0.24, 0.18), mat_shirt)
    parts['pelvis'] = _box('pelvis', (0, 0, 0.94), (0.46, 0.24, 0.14), mat_pants)

    for side, x in (('L', 0.34), ('R', -0.34)):
        parts[f'upper_arm.{side}'] = _box(
            f'upper_arm.{side}', (x, 0, 1.34), (0.18, 0.18, 0.30), mat_shirt)
        parts[f'forearm.{side}'] = _box(
            f'forearm.{side}', (x, 0, 1.04), (0.18, 0.18, 0.30), mat_skin)
        parts[f'hand.{side}'] = _box(
            f'hand.{side}', (x, 0, 0.86), (0.18, 0.18, 0.10), mat_skin)
        parts[f'elbow.{side}'] = _box(
            f'elbow.{side}', (x, 0, 1.19), (0.19, 0.19, 0.10), mat_shirt)

    for side, x in (('L', 0.15), ('R', -0.15)):
        parts[f'thigh.{side}'] = _box(
            f'thigh.{side}', (x, 0, 0.72), (0.20, 0.20, 0.44), mat_pants)
        parts[f'shin.{side}'] = _box(
            f'shin.{side}', (x, 0, 0.30), (0.20, 0.20, 0.38), mat_pants)
        parts[f'knee.{side}'] = _box(
            f'knee.{side}', (x, 0, 0.50), (0.21, 0.21, 0.08), mat_pants)
        parts[f'foot.{side}'] = _box(
            f'foot.{side}', (x, -0.06, 0.05), (0.20, 0.30, 0.10), mat_shoe)
        parts[f'toe.{side}'] = _box(
            f'toe.{side}', (x, -0.22, 0.05), (0.20, 0.10, 0.10), mat_shoe)

    # 骨架
    arm_data = bpy.data.armatures.new('AI_Rig')
    arm = bpy.data.objects.new('AI_Rig', arm_data)
    bpy.context.scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones

    def bone(name: str, head: tuple[float, float, float],
             tail: tuple[float, float, float],
             parent: str | None = None) -> bpy.types.EditBone:
        b = eb.new(name)
        b.head = head
        b.tail = tail
        b.use_connect = False
        if parent:
            b.parent = eb[parent]
        return b

    bone('root', (0, 0, 0), (0, 0, 0.12))
    bone('pelvis', (0, 0, HIP_Z), (0, 0, HIP_Z + 0.10), 'root')
    bone('spine', (0, 0, 1.06), (0, 0, 1.26), 'pelvis')
    bone('chest', (0, 0, 1.26), (0, 0, 1.50), 'spine')
    bone('neck', (0, 0, 1.58), (0, 0, 1.66), 'chest')
    bone('head', (0, 0, 1.66), (0, 0, 2.12), 'neck')

    for side, x in (('L', 0.15), ('R', -0.15)):
        bone(f'clavicle.{side}', (0, 0, 1.46), (x * 1.8, 0, 1.48), 'chest')
        bone(f'upper_arm.{side}', (x * 1.8, 0, 1.48),
             (x * 1.8, 0, 1.19), f'clavicle.{side}')
        bone(f'forearm.{side}', (x * 1.8, 0, 1.19),
             (x * 1.8, 0, 0.91), f'upper_arm.{side}')
        bone(f'hand.{side}', (x * 1.8, 0, 0.91),
             (x * 1.8, 0, 0.81), f'forearm.{side}')

        bone(f'thigh.{side}', (x, 0, HIP_Z), (x, 0, 0.50), 'pelvis')
        bone(f'shin.{side}', (x, 0, 0.50), (x, 0, ANKLE_Z), f'thigh.{side}')
        bone(f'foot.{side}', (x, 0, ANKLE_Z), (x, -0.26, ANKLE_Z),
             f'shin.{side}')
        bone(f'toe.{side}', (x, -0.26, ANKLE_Z), (x, -0.36, ANKLE_Z),
             f'foot.{side}')

    # IK 目标：不参与变形，只作为约束目标。
    for side, x in (('L', 0.15), ('R', -0.15)):
        bone(f'foot_ik.{side}', (x, -0.06, ANKLE_Z), (x, -0.26, ANKLE_Z), 'root')
        bone(f'knee_pole.{side}', (x, -0.60, 0.55), (x, -0.60, 0.75), 'root')
        bone(f'hand_ik.{side}', (x * 1.8, 0, 0.86),
             (x * 1.8, 0, 0.76), 'root')
        bone(f'elbow_pole.{side}', (x * 2.8, 0.35, 1.10),
             (x * 2.8, 0.35, 1.30), 'root')

    bpy.ops.object.mode_set(mode='OBJECT')

    # 权重：每个部件绑到对应骨；关节块 0.5/0.5。
    for key, ob in parts.items():
        if key.startswith('hair') or key in ('head',) or key.startswith(('eye', 'pupil', 'brow')) or key == 'mouth':
            _weight(ob, 'head')
        elif key == 'neck':
            _weight(ob, 'neck')
        elif key == 'chest':
            _weight(ob, 'chest')
        elif key == 'waist':
            _weight(ob, 'spine')
        elif key == 'pelvis':
            _weight(ob, 'pelvis')
        elif key.startswith('toe.'):
            _weight(ob, f'toe.{key.split(".")[1]}')
        elif key.startswith('elbow.'):
            _weight(ob, f'forearm.{key.split(".")[1]}')
        elif key.startswith('elbow_joint.'):
            _weight(ob, f'forearm.{key.split(".")[1]}')
        elif key.startswith('knee_joint.'):
            _weight(ob, f'shin.{key.split(".")[1]}')
        elif key.startswith('hip_sleeve.'):
            _weight(ob, f'thigh.{key.split(".")[1]}')
        elif key.startswith('knee_sleeve.'):
            _weight(ob, f'shin.{key.split(".")[1]}')
        elif key.startswith(('upper_arm.', 'forearm.', 'hand.',
                             'thigh.', 'shin.', 'foot.')):
            _weight(ob, key)

        ob.parent = arm
        mod = ob.modifiers.new('Armature', 'ARMATURE')
        mod.object = arm

    bpy.ops.object.mode_set(mode='OBJECT')

    # 地面
    _box('Ground', (0, 0, -0.01), (30, 30, 0.02), _mat('Ground', C_GROUND, 0.95))

    # 保存
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    return arm


def _set_ik_target(arm: bpy.types.Object, name: str, target: list[float]) -> None:
    pb = arm.pose.bones[name]
    # IK 目标骨父级是 root（原点），直接给 armature space 平移。
    pb.matrix = Matrix.Translation(Vector(target)) @ pb.bone.matrix_local.to_3x3().to_4x4()



def _aim_bone(arm: bpy.types.Object, name: str, head, tail) -> None:
    """把骨骼摆到 head->tail 的方向；用世界空间矩阵，绕开 Blender IK 的父子链问题。"""
    pb = arm.pose.bones[name]
    rest_dir = (pb.bone.tail_local - pb.bone.head_local)
    if rest_dir.length < 1e-8:
        return
    rest_dir.normalize()
    d = Vector(tail) - Vector(head)
    if d.length < 1e-8:
        return
    d.normalize()
    q = rest_dir.rotation_difference(d)
    rot = q.to_matrix().to_4x4()
    pb.matrix = (Matrix.Translation(Vector(head)) @ rot
                 @ pb.bone.matrix_local.to_3x3().to_4x4())


def _two_bone_knee(hip, foot, l1: float, l2: float, pole):
    """解析两骨 IK：返回膝/肘位置。"""
    hv = Vector(hip)
    fv = Vector(foot)
    dvec = fv - hv
    dist = max(abs(l1 - l2) + 1e-4, min(l1 + l2 - 1e-4, dvec.length))
    if dvec.length < 1e-8:
        dvec = Vector((0, -1, 0))
    u = dvec.normalized()
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = math.sqrt(max(0.0, l1 * l1 - a * a))
    p = Vector(pole) - hv
    p = p - u * p.dot(u)
    if p.length < 1e-6:
        p = Vector((0, -1, 0))
    p.normalize()
    return hv + u * a + p * h

def apply_spec(arm: bpy.types.Object, spec: dict[str, Any]) -> None:
    errors = validate_spec(spec)
    if errors:
        raise ValueError('; '.join(errors))
    bpy.context.view_layer.update()

    for pb in arm.pose.bones:
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = (0, 0, 0)
        pb.location = (0, 0, 0)
        pb.scale = (1, 1, 1)

    root = arm.pose.bones['root']
    root.rotation_mode = 'XYZ'
    # root 骨朝 +Z，局部 Y 才是世界 Z。
    root.rotation_euler[1] = math.radians(spec['root']['yaw'])
    root.location = Vector(spec['root']['pos'])

    pelvis = arm.pose.bones['pelvis']
    # pelvis 骨朝 +Z：局部 Y = 世界 Z，局部 X = 世界 X，局部 Z = 世界 -Y。
    pelvis.location = (0.0, float(spec['pelvis']['height']) - HIP_Z, 0.0)
    pelvis.rotation_euler = (
        math.radians(spec['pelvis']['pitch']),
        math.radians(spec['pelvis']['yaw']),
        -math.radians(spec['pelvis']['roll']),
    )

    for pb_name, values in (('spine', spec['spine']), ('head', spec['head'])):
        pb = arm.pose.bones[pb_name]
        pb.rotation_euler = (
            math.radians(values['pitch']),
            math.radians(values['yaw']),
            -math.radians(values['roll']),
        )

    for side in ('L', 'R'):
        key = 'left' if side == 'L' else 'right'
        leg = spec['legs'][key]
        foot = Vector(leg['foot'])
        foot.z = max(ANKLE_Z, foot.z + ANKLE_Z)

        thigh = arm.pose.bones[f'thigh.{side}']
        shin = arm.pose.bones[f'shin.{side}']
        hip = thigh.matrix.translation.copy()
        if leg.get('straight'):
            _aim_bone(arm, f'thigh.{side}', hip, foot)
            bpy.context.view_layer.update()
        else:
            knee = _two_bone_knee(hip, foot, thigh.bone.length,
                                  shin.bone.length, leg['knee_hint'])
            _aim_bone(arm, f'thigh.{side}', hip, knee)
            bpy.context.view_layer.update()
            _aim_bone(arm, f'shin.{side}', knee, foot)
            bpy.context.view_layer.update()
        _aim_bone(arm, f'foot.{side}', foot, foot + Vector((0, -0.25, 0)))
        bpy.context.view_layer.update()

        arm_spec = spec['arms'][key]
        wrist = Vector(arm_spec['hand'])
        upper = arm.pose.bones[f'upper_arm.{side}']
        fore = arm.pose.bones[f'forearm.{side}']
        shoulder = upper.matrix.translation.copy()
        elbow = _two_bone_knee(shoulder, wrist, upper.bone.length,
                               fore.bone.length, arm_spec['elbow_hint'])
        _aim_bone(arm, f'upper_arm.{side}', shoulder, elbow)
        bpy.context.view_layer.update()
        _aim_bone(arm, f'forearm.{side}', elbow, wrist)
        bpy.context.view_layer.update()
        _aim_bone(arm, f'hand.{side}', wrist, wrist + Vector((0, 0, -0.10)))
        bpy.context.view_layer.update()



def _joint_balls(arm):
    """关节球放在摆好姿势后的真实关节位置上，不参与蒙皮。"""
    scene = bpy.context.scene
    for ob in list(bpy.data.objects):
        if ob.name.startswith('_joint_'):
            bpy.data.objects.remove(ob, do_unlink=True)
    colors = {
        'knee': (0.045, 0.060, 0.180, 1.0),
        'elbow': (0.330, 0.190, 0.110, 1.0),
        'hip': (0.045, 0.060, 0.180, 1.0),
        'shoulder': (0.030, 0.260, 0.260, 1.0),
    }
    joints = (
        ('knee', 'shin'), ('elbow', 'forearm'),
        ('hip', 'thigh'), ('shoulder', 'upper_arm'),
    )
    for side in ('L', 'R'):
        for kind, stem in joints:
            pose = arm.pose.bones[f'{stem}.{side}']
            pos = arm.matrix_world @ pose.head
            if kind == 'knee':
                pos = pos + Vector((0.0, -0.105, 0.035))
                radius = 0.225
            elif kind == 'hip':
                radius = 0.20
            elif kind == 'shoulder':
                radius = 0.17
            else:
                radius = 0.16
            bpy.ops.mesh.primitive_uv_sphere_add(
                segments=12, ring_count=8, radius=radius, location=pos)
            ob = bpy.context.object
            ob.name = f'_joint_{kind}.{side}'
            ob.parent = arm
            ob.matrix_parent_inverse = arm.matrix_world.inverted()
            for poly in ob.data.polygons:
                poly.use_smooth = True
            ob.data.materials.append(_mat(f'_joint_{kind}_mat', colors[kind], 0.82))
    bpy.context.view_layer.update()


def setup_camera_light(scene: bpy.types.Scene, spec: dict[str, Any]) -> None:
    cam_data = bpy.data.cameras.new('Camera')
    cam = bpy.data.objects.new('Camera', cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    cam.data.lens = float(spec['camera']['lens'])
    c = spec['camera']
    framing = c.get('framing', '9:16')
    if framing == '9:16':
        scene.render.resolution_x, scene.render.resolution_y = 720, 1280
    elif framing == '1:1':
        scene.render.resolution_x, scene.render.resolution_y = 900, 900
    elif framing == '4:5':
        scene.render.resolution_x, scene.render.resolution_y = 864, 1080
    else:
        scene.render.resolution_x, scene.render.resolution_y = 1280, 720

    az = math.radians(float(c['azimuth']))
    dist = float(c['distance'])
    height = float(c['height'])
    target = Vector((0, 0, float(c['aim_height'])))
    if framing == '16:9':
        # 横屏时纵向视野变窄，自动退远并抬高目标，避免裁头。
        dist = max(dist, 11.0)
        height = max(height, 1.15)
        target.z = max(target.z, 0.85)
        cam.data.lens = min(cam.data.lens, 55.0)
    cam.location = Vector((math.sin(az) * dist,
                           -math.cos(az) * dist,
                           height))
    direction = target - cam.location
    cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()

    sun_data = bpy.data.lights.new('Key', 'SUN')
    sun = bpy.data.objects.new('Key', sun_data)
    scene.collection.objects.link(sun)
    sun.data.energy = 3.0
    sun.data.angle = math.radians(8)
    sun.rotation_euler = (math.radians(55), 0, math.radians(35))

    fill_data = bpy.data.lights.new('Fill', 'AREA')
    fill = bpy.data.objects.new('Fill', fill_data)
    scene.collection.objects.link(fill)
    fill.data.energy = 120.0
    fill.data.size = 4.0
    fill.location = (2.5, -3.5, 2.0)
    fill.rotation_euler = (math.radians(65), 0, math.radians(25))

    scene.render.film_transparent = False
    scene.eevee.taa_render_samples = 32
    scene.render.filepath = ''


def render_spec(rig_path: str, spec_path: str, out: str,
                samples: int = 32) -> None:
    bpy.ops.wm.open_mainfile(filepath=rig_path)
    scene = bpy.context.scene
    spec = json.loads(Path(spec_path).read_text(encoding='utf-8'))
    arm = bpy.data.objects.get('AI_Rig')
    if not arm:
        raise RuntimeError('AI_Rig not found')
    apply_spec(arm, spec)
    setup_camera_light(scene, spec)
    scene.eevee.taa_render_samples = samples
    scene.render.image_settings.file_format = 'PNG'
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    scene.render.filepath = out
    bpy.ops.render.render(write_still=True)
    print(f'wrote {out}')


# ---------- Steve/Alex 外观：64x64 皮肤 + 正确比例 ----------

STEVE_HIP_Z = 0.75
STEVE_ANKLE_Z = 0.08
STEVE_SHOULDER_Z = 1.50
STEVE_HEAD_Z = 1.75

# 皮肤图里各部位的像素矩形（x0,y0,x1,y1，左上角原点）。
# 面顺序：0 底、1 顶、2 前(-Y)、3 右(+X)、4 后(+Y)、5 左(-X)。
_H = {
    'bottom': (16, 0, 24, 8), 'top': (8, 0, 16, 8),
    'front': (8, 8, 16, 16), 'left': (16, 8, 24, 16),
    'back': (24, 8, 32, 16), 'right': (0, 8, 8, 16),
}
_BODY_TOP = {
    'bottom': (28, 16, 36, 20), 'top': (20, 16, 28, 20),
    'front': (20, 20, 28, 26), 'left': (28, 20, 32, 26),
    'back': (32, 20, 40, 26), 'right': (16, 20, 20, 26),
}
_BODY_BOTTOM = {
    'bottom': (28, 16, 36, 20), 'top': (20, 16, 28, 20),
    'front': (20, 26, 28, 32), 'left': (28, 26, 32, 32),
    'back': (32, 26, 40, 32), 'right': (16, 26, 20, 32),
}


_HEAD_OUTER = {
    'bottom': (48, 0, 56, 8), 'top': (40, 0, 48, 8),
    'front': (40, 8, 48, 16), 'right': (32, 8, 40, 16),
    'back': (56, 8, 64, 16), 'left': (48, 8, 56, 16),
}
_BODY_TOP_OUTER = {
    'bottom': (28, 32, 36, 36), 'top': (20, 32, 28, 36),
    'front': (20, 36, 28, 44), 'right': (28, 36, 32, 44),
    'back': (32, 36, 40, 44), 'left': (16, 36, 20, 44),
}
_BODY_BOTTOM_OUTER = {
    'bottom': (28, 48, 36, 52), 'top': (20, 48, 28, 52),
    'front': (20, 52, 28, 60), 'right': (28, 52, 32, 60),
    'back': (32, 52, 40, 60), 'left': (16, 52, 20, 60),
}


def _outer_rects(prefix):
    table = {'head': _HEAD_OUTER, 'chest': _BODY_TOP_OUTER,
             'waist': _BODY_BOTTOM_OUTER}[prefix]
    return [table[k] for k in ('bottom', 'top', 'front', 'right', 'back', 'left')]


def _skin_rects(prefix):
    """按部位返回 6 面的皮肤像素矩形。"""
    if prefix == 'head':
        return [_H[k] for k in ('bottom', 'top', 'front', 'right', 'back', 'left')]
    if prefix == 'chest':
        return [_BODY_TOP[k] for k in ('bottom', 'top', 'front', 'right', 'back', 'left')]
    if prefix == 'waist':
        return [_BODY_BOTTOM[k] for k in ('bottom', 'top', 'front', 'right', 'back', 'left')]

    # 四肢：上半段 / 下半段。
    limb, half = prefix.split(':')
    up = (half == 'up')
    if limb == 'arm.R':  # 角色右臂，皮肤 x=40..56
        y0 = 20 if up else 26
        return [
            (48, 16, 52, 20), (44, 16, 48, 20),
            (44, y0, 48, y0 + 6), (48, y0, 52, y0 + 6),
            (52, y0, 56, y0 + 6), (40, y0, 44, y0 + 6),
        ]
    if limb == 'arm.L':  # 角色左臂，皮肤 x=32..48, y=48..64
        y0 = 52 if up else 58
        return [
            (40, 48, 44, 52), (36, 48, 40, 52),
            (36, y0, 40, y0 + 6), (40, y0, 44, y0 + 6),
            (44, y0, 48, y0 + 6), (32, y0, 36, y0 + 6),
        ]
    if limb == 'leg.R':  # 角色右腿，皮肤 x=0..16
        y0 = 20 if up else 26
        # 大腿底面朝膝，不能采样鞋底灰块；用裤腿蓝。
        bottom = (4, 16, 8, 20) if up else (8, 16, 12, 20)
        return [
            bottom, (4, 16, 8, 20),
            (4, y0, 8, y0 + 6), (8, y0, 12, y0 + 6),
            (12, y0, 16, y0 + 6), (0, y0, 4, y0 + 6),
        ]
    if limb == 'leg.L':  # 角色左腿，皮肤 x=16..32, y=48..64
        y0 = 52 if up else 58
        bottom = (20, 48, 24, 52) if up else (24, 48, 28, 52)
        return [
            bottom, (20, 48, 24, 52),
            (20, y0, 24, y0 + 6), (24, y0, 28, y0 + 6),
            (28, y0, 32, y0 + 6), (16, y0, 20, y0 + 6),
        ]
    raise KeyError(prefix)


def _uv_rect(rect, w=64, h=64):
    x0, y0, x1, y1 = rect
    # 皮肤坐标左上角；Blender UV 左下角。
    return (x0 / w, 1.0 - y1 / h, x1 / w, 1.0 - y0 / h)


def _box_uv(name, center, size, mat, face_rects):
    cx, cy, cz = center
    sx, sy, sz = (s * 0.5 for s in size)
    verts = [
        (cx - sx, cy - sy, cz - sz), (cx + sx, cy - sy, cz - sz),
        (cx + sx, cy + sy, cz - sz), (cx - sx, cy + sy, cz - sz),
        (cx - sx, cy - sy, cz + sz), (cx + sx, cy - sy, cz + sz),
        (cx + sx, cy + sy, cz + sz), (cx - sx, cy + sy, cz + sz),
    ]
    faces = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    ob = bpy.data.objects.new(name, mesh)
    ob.data.materials.append(mat)
    bpy.context.scene.collection.objects.link(ob)
    uv = mesh.uv_layers.new(name='UVMap')
    for poly, rect in zip(mesh.polygons, face_rects):
        u0, v0, u1, v1 = _uv_rect(rect)
        # 每个四边形 4 个 loop：左下、左上、右上、右下。
        for li, (u, v) in zip(poly.loop_indices,
                              ((u0, v0), (u0, v1), (u1, v1), (u1, v0))):
            uv.data[li].uv = (u, v)
    return ob


def _skin_material(path):
    p = Path(path).expanduser()
    if not p.exists():
        mat = _mat('Skin', C_SKIN, 0.8)
        return mat
    img = bpy.data.images.load(str(p), check_existing=True)
    try:
        img.pack()
    except Exception:
        pass
    mat = bpy.data.materials.new('SteveSkin')
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    tex.interpolation = 'Closest'
    if bsdf:
        nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
        bsdf.inputs['Roughness'].default_value = 0.82
    return mat


def _skin_material_3d(path):
    """3D 皮肤外层材质：带 alpha，透明像素不显示。"""
    p = Path(path).expanduser()
    if not p.exists():
        return _mat('Skin3D', C_SKIN, 0.8)
    img = bpy.data.images.load(str(p), check_existing=True)
    try:
        img.pack()
    except Exception:
        pass
    mat = bpy.data.materials.new('SteveSkin3D')
    mat.use_nodes = True
    nt = mat.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    tex = nt.nodes.new('ShaderNodeTexImage')
    tex.image = img
    tex.interpolation = 'Closest'
    if bsdf:
        nt.links.new(tex.outputs['Color'], bsdf.inputs['Base Color'])
        nt.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
        bsdf.inputs['Roughness'].default_value = 0.82
    # Blender 4/5 兼容：4 用 blend_method，5 用 surface_render_method。
    for attr, val in (('blend_method', 'BLEND'),
                      ('surface_render_method', 'DITHERED')):
        try:
            setattr(mat, attr, val)
        except Exception:
            pass
    mat.use_backface_culling = True
    return mat


def _image_of(mat):
    if not mat or not mat.use_nodes:
        return None
    for n in mat.node_tree.nodes:
        if n.type == 'TEX_IMAGE' and n.image:
            return n.image
    return None


def _avg_rect(img, rect):
    if not img:
        return (0.62, 0.45, 0.33)
    w, h = img.size
    px = list(img.pixels)
    x0, y0, x1, y1 = rect
    cols = []
    for y in range(max(0, y0), min(h, y1)):
        yy = h - 1 - y
        for x in range(max(0, x0), min(w, x1)):
            i = (yy * w + x) * 4
            cols.append((px[i], px[i + 1], px[i + 2]))
    if not cols:
        return (0.62, 0.45, 0.33)
    return tuple(sum(c[k] for c in cols) / len(cols) for k in range(3))


def _joint_sphere(name, center, radius, color):
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=12, ring_count=8, radius=radius, location=center)
    ob = bpy.context.object
    ob.name = name
    for poly in ob.data.polygons:
        poly.use_smooth = True
    col = tuple(color) + (1.0,) if len(color) == 3 else tuple(color)
    ob.data.materials.append(_mat(name + '_mat', col, 0.82))
    return ob



def _open_end(ob, which):
    """删掉关节端面，让关节球从开口里露出来，避免弯折时看到方形截面。"""
    bm = bmesh.new()
    bm.from_mesh(ob.data)
    zs = [v.co.z for v in bm.verts]
    if not zs:
        bm.free(); return
    zt = min(zs) if which == 'bottom' else max(zs)
    for f in list(bm.faces):
        if abs(f.calc_center_median().z - zt) < 1e-5:
            bmesh.ops.delete(bm, geom=[f], context='FACES')
    bm.to_mesh(ob.data)
    bm.free()


def _weight_limb(ob, upper, lower, joint_z):
    for vg in list(ob.vertex_groups):
        ob.vertex_groups.remove(vg)
    gu = ob.vertex_groups.new(name=upper)
    gl = ob.vertex_groups.new(name=lower)
    for v in ob.data.vertices:
        z = v.co.z
        if z >= joint_z + 0.02:
            gu.add([v.index], 1.0, 'REPLACE')
        elif z <= joint_z - 0.02:
            gl.add([v.index], 1.0, 'REPLACE')
        else:
            t = (z - (joint_z - 0.02)) / 0.04
            gu.add([v.index], 1.0 - t, 'REPLACE')
            gl.add([v.index], t, 'REPLACE')


def _make_limb(name, upper_center, upper_size, lower_center, lower_size,
               mat, up_rects, dn_rects, joint_z, upper_bone, lower_bone,
               end_center=None, end_size=None, end_rects=None, weld_end=False,
               end_solid=None):
    up = _box_uv(name + '_up', upper_center, upper_size, mat, up_rects)
    dn = _box_uv(name + '_dn', lower_center, lower_size, mat, dn_rects)
    _open_end(up, 'bottom')
    _open_end(dn, 'top')
    end = None
    if end_center:
        if end_solid:
            end = _box(name + '_end', end_center, end_size,
                       _mat(name + '_end_mat', end_solid, 0.85))
        else:
            end = _box_uv(name + '_end', end_center, end_size, mat,
                          end_rects or dn_rects)
        if weld_end:
            _open_end(dn, 'bottom')
            _open_end(end, 'top')
    bpy.ops.object.select_all(action='DESELECT')
    up.select_set(True); dn.select_set(True)
    if end:
        end.select_set(True)
    bpy.context.view_layer.objects.active = up
    bpy.ops.object.join()
    ob = up
    ob.name = name
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    for off in (-0.05, 0.0, 0.05):
        geom = list(bm.verts) + list(bm.edges) + list(bm.faces)
        bmesh.ops.bisect_plane(
            bm, geom=geom, dist=1e-6,
            plane_co=(0, 0, joint_z + off), plane_no=(0, 0, 1),
            clear_inner=False, clear_outer=False)
    bm.to_mesh(ob.data); bm.free()
    _weight_limb(ob, upper_bone, lower_bone, joint_z)
    return ob


def build_steve(path, res='720x1280', skin=None, skin3d=None):
    """Build a Steve/Alex-proportioned character with a 64x64 skin atlas."""
    skin = skin or os.environ.get('DSH_AI_SKIN') or os.path.expanduser(
        '~/.dsh/blender-mc/skins/default.png')
    if skin3d is None:
        skin3d = os.environ.get('DSH_AI_SKIN3D') == '1'
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = 'BLENDER_EEVEE'
    scene.view_settings.view_transform = 'AgX'
    scene.render.resolution_x, scene.render.resolution_y = (int(x) for x in res.split('x'))
    scene.render.image_settings.file_format = 'PNG'
    scene.world = bpy.data.worlds.new('World')
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get('Background')
    if bg:
        bg.inputs[0].default_value = (0.55, 0.66, 0.78, 1.0)
        bg.inputs[1].default_value = 0.8

    mat = _skin_material(skin)
    parts = {}

    # 头 / 胸 / 腰 / 骨盆
    parts['head'] = _box_uv('head', (0, 0, STEVE_HEAD_Z), (0.50, 0.50, 0.50),
                            mat, _skin_rects('head'))
    parts['neck'] = _box_uv('neck', (0, 0, 1.52), (0.25, 0.25, 0.10),
                            mat, _skin_rects('head'))
    parts['chest'] = _box_uv('chest', (0, 0, 1.3125), (0.50, 0.25, 0.375),
                             mat, _skin_rects('chest'))
    parts['waist'] = _box_uv('waist', (0, 0, 0.9375), (0.50, 0.25, 0.375),
                             mat, _skin_rects('waist'))

    # 3D 皮肤外层（帽子/头发、外套、裤腿的 overlay 层）。
    # 只在 overlay 有像素时可见；材质带 alpha，透明像素不脏画面。
    if skin3d:
        mat3d = _skin_material_3d(skin)
        for key, center, size in (
                ('head_outer', (0, 0, STEVE_HEAD_Z), (0.54, 0.54, 0.54)),
                ('chest_outer', (0, 0, 1.3125), (0.54, 0.29, 0.415)),
                ('waist_outer', (0, 0, 0.9375), (0.52, 0.27, 0.395))):
            parts[key] = _box_uv(
                key, center, size, mat3d,
                _outer_rects('head' if key == 'head_outer'
                             else ('chest' if key == 'chest_outer' else 'waist')))

    for side, x in (('L', 0.375), ('R', -0.375)):
        parts[f'arm.{side}'] = _make_limb(
            f'arm.{side}',
            (x, 0, 1.2925), (0.25, 0.25, 0.375),
            (x, 0, 0.9175), (0.25, 0.25, 0.375),
            mat, _skin_rects(f'arm.{side}:up'), _skin_rects(f'arm.{side}:down'),
            1.105, f'upper_arm.{side}', f'forearm.{side}',
            end_center=(x, 0, 0.680), end_size=(0.25, 0.25, 0.10),
            weld_end=True)

    for side, x in (('L', 0.125), ('R', -0.125)):
        parts[f'leg.{side}'] = _make_limb(
            f'leg.{side}',
            (x, 0, 0.5625), (0.25, 0.25, 0.375),
            (x, 0, 0.2275), (0.25, 0.25, 0.295),
            mat, _skin_rects(f'leg.{side}:up'), _skin_rects(f'leg.{side}:down'),
            0.375, f'thigh.{side}', f'shin.{side}',
            end_center=(x, -0.02, 0.090), end_size=(0.25, 0.30, 0.18),
            weld_end=False, end_solid=(0.20, 0.20, 0.22, 1.0))

    # 骨架
    arm_data = bpy.data.armatures.new('AI_Rig')
    arm = bpy.data.objects.new('AI_Rig', arm_data)
    scene.collection.objects.link(arm)
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode='EDIT')
    eb = arm.data.edit_bones

    def bone(name, head, tail, parent=None):
        b = eb.new(name)
        b.head = head
        b.tail = tail
        b.use_connect = False
        if parent:
            b.parent = eb[parent]
        return b

    bone('root', (0, 0, 0), (0, 0, 0.12))
    bone('pelvis', (0, 0, STEVE_HIP_Z), (0, 0, STEVE_HIP_Z + 0.10), 'root')
    bone('spine', (0, 0, 0.78), (0, 0, 1.00), 'pelvis')
    bone('chest', (0, 0, 1.00), (0, 0, 1.50), 'spine')
    bone('neck', (0, 0, 1.50), (0, 0, 1.58), 'chest')
    bone('head', (0, 0, 1.58), (0, 0, 2.08), 'neck')

    for side, x in (('L', 0.125), ('R', -0.125)):
        bone(f'clavicle.{side}', (0, 0, 1.46), (x * 3, 0, 1.48), 'chest')
        bone(f'upper_arm.{side}', (x * 3, 0, 1.48),
             (x * 3, 0, 1.105), f'clavicle.{side}')
        bone(f'forearm.{side}', (x * 3, 0, 1.105),
             (x * 3, 0, 0.73), f'upper_arm.{side}')
        bone(f'hand.{side}', (x * 3, 0, 0.73),
             (x * 3, 0, 0.63), f'forearm.{side}')

        bone(f'thigh.{side}', (x, 0, STEVE_HIP_Z), (x, 0, 0.375), 'pelvis')
        bone(f'shin.{side}', (x, 0, 0.375), (x, 0, STEVE_ANKLE_Z),
             f'thigh.{side}')
        bone(f'foot.{side}', (x, 0, STEVE_ANKLE_Z), (x, -0.26, STEVE_ANKLE_Z),
             f'shin.{side}')
        bone(f'toe.{side}', (x, -0.26, STEVE_ANKLE_Z),
             (x, -0.36, STEVE_ANKLE_Z), f'foot.{side}')

    for side, x in (('L', 0.125), ('R', -0.125)):
        bone(f'foot_ik.{side}', (x, -0.06, STEVE_ANKLE_Z),
             (x, -0.26, STEVE_ANKLE_Z), 'root')
        bone(f'knee_pole.{side}', (x, -0.60, 0.50), (x, -0.60, 0.70), 'root')
        bone(f'hand_ik.{side}', (x * 3, 0, 0.69),
             (x * 3, 0, 0.59), 'root')
        bone(f'elbow_pole.{side}', (x * 4, 0.35, 1.10),
             (x * 4, 0.35, 1.30), 'root')

    bpy.ops.object.mode_set(mode='OBJECT')

    for key, ob in parts.items():
        if key.startswith(('arm.', 'leg.')):
            pass
        elif key == 'head_outer':
            _weight(ob, 'head')
        elif key == 'chest_outer':
            _weight(ob, 'chest')
        elif key == 'waist_outer':
            _weight(ob, 'spine')
        elif key in ('head',):
            _weight(ob, 'head')
        elif key == 'neck':
            _weight(ob, 'neck')
        elif key == 'chest':
            _weight(ob, 'chest')
        elif key in ('waist', 'pelvis'):
            _weight(ob, 'spine' if key == 'waist' else 'pelvis')
        elif key.startswith('elbow.'):
            _weight(ob, f'forearm.{key.split(".")[1]}')
        elif key.startswith('elbow_joint.'):
            _weight(ob, f'forearm.{key.split(".")[1]}')
        elif key.startswith('knee_joint.'):
            _weight(ob, f'shin.{key.split(".")[1]}')
        elif key.startswith('hip_sleeve.'):
            _weight(ob, f'thigh.{key.split(".")[1]}')
        elif key.startswith('knee_sleeve.'):
            _weight(ob, f'shin.{key.split(".")[1]}')
        elif key.startswith(('upper_arm.', 'forearm.', 'hand.',
                             'thigh.', 'shin.', 'foot.')):
            _weight(ob, key)
        ob.parent = arm
        mod = ob.modifiers.new('Armature', 'ARMATURE')
        mod.object = arm

    _box('Ground', (0, 0, -0.01), (30, 30, 0.02),
         _mat('Ground', C_GROUND, 0.95))

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(path))
    return arm


def main() -> int:
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    ap = argparse.ArgumentParser()
    ap.add_argument('--build', help='输出 .blend')
    ap.add_argument('--res', default='720x1280')
    ap.add_argument('--rig', help='现有 .blend')
    ap.add_argument('--spec', help='CharacterSpec JSON')
    ap.add_argument('--out', help='渲染 PNG')
    ap.add_argument('--samples', type=int, default=32)
    ap.add_argument('--skin', help='64x64 皮肤 PNG；默认 ~/.dsh/blender-mc/skins/default.png')
    ap.add_argument('--skin3d', action='store_true',
                    help='叠加 3D 皮肤外层（帽子/头发、外套、裤腿 overlay）')
    args = ap.parse_args(argv)
    if args.build:
        build_steve(args.build, args.res, args.skin, skin3d=args.skin3d)
        print(f'built {args.build}')
        return 0
    if args.rig and args.spec and args.out:
        render_spec(args.rig, args.spec, args.out, args.samples)
        return 0
    ap.error('需要 --build，或 --rig/--spec/--out')
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
