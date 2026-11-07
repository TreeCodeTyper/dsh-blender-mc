"""BPS 角色面部表情库（v0.5.6）

这一版换了做法：不再「隐藏 3D 五官、只挪嘴骨」，而是直接用 BPS 面板上的
面部控制骨，并把 3D 眼/眉/眼皮放出来：

  · 眉毛  Eyebrows / Eyebrow.R / Eyebrow.L   —— 抬、压、内外倾斜
  · 眼皮  Top Eyelids / Bottom Eyelids      —— 眯眼、瞪眼、半边眼
  · 眨眼  Blink                             —— 整只眼睛闭上
  · 眼珠  Eyes                              —— 视线上下左右
  · 嘴角  Left Lip / Right Lip              —— 微笑、撇嘴
  · 张口  Mouth                             —— 张嘴、惊讶

3D 眼睛是按 BPS 默认皮肤做的，换皮后会压在皮肤贴图的眼睛上。这里在
apply_face 里做三件事，让 3D 五官和任意皮肤共存：

  1. 把皮肤脸部的「画上去的五官」用肤色盖掉（保留刘海），避免双眼睛；
  2. 从皮肤里采样眼睛颜色和眉毛颜色，给 3D 瞳孔/眉毛上色；
  3. 只在这个表情启用时放出 3D 五官；face=0 仍然走旧的皮肤眼路线。

注意：py/blender/*.py 跑在 Blender 自带 Python，不能 import cv2。
"""

import math
import os

import bpy
import mathutils

# BPS 面板控制骨：表情只动这些，不动身体、不转 Head。
_PANEL_CONTROLS = (
    'Eyebrows', 'Eyebrow.R', 'Eyebrow.L',
    'Top Eyelids', 'Bottom Eyelids', 'Blink', 'Eyes',
    'Left Lip', 'Right Lip', 'Mouth',
)

# 旧版直接位移的嘴骨，重置时一并归零，保证重复调用幂等。
_DIRECT_LIPS = (
    'Top Lip.M', 'Bottom Lip.M', 'Top Lip.R', 'Bottom Lip.L',
    'Top Lip S.R', 'Top Lip S.L',
    'Bottom Lip.R', 'Bottom Lip.L', 'Bottom Lip S.R', 'Bottom Lip S.L',
)

# 放出 3D 五官；build_cover.py 早期默认把 mesh eye / pupil 隐藏了。
_SHOW_3D = (
    'BPS v4 - mesh eye.l', 'BPS v4 - mesh eye.r',
    'BPS v4 - mesh pupils.l', 'BPS v4 - mesh pupil.r',
    'BPS v4 - mesh eyebrow.l', 'BPS v4 - mesh eyebrow.r',
    'BPS v4 - mesh eyelids',
)

# 预设：trans 是世界空间平移，rot 是绕 Y 轴（正对镜头看）的角度，scale 是
# 绕骨骼 head 的缩放。坐标单位与骨骼一致；1 个皮肤像素≈0.0625。
FACE_PRESETS = {
    'neutral': {
        'label': '平静',
    },
    'happy': {
        'label': '愉快',
        'trans': {
            'Eyebrows': (0.0, 0.0, 0.030),
            'Left Lip': (0.0, 0.0, 0.032),
            'Right Lip': (0.0, 0.0, 0.032),
        },
        'scale': {'Top Eyelids': (1.0, 1.0, 0.85)},
    },
    'angry': {
        'label': '生气',
        'trans': {
            'Eyebrows': (0.0, 0.0, -0.015),
            'Top Eyelids': (0.0, 0.0, -0.012),
            'Left Lip': (0.0, 0.0, -0.032),
            'Right Lip': (0.0, 0.0, -0.032),
        },
        'rot': (
            ('Eyebrow.R', 'Y', 18.0),
            ('Eyebrow.L', 'Y', -18.0),
        ),
    },
    'sad': {
        'label': '难过',
        'trans': {
            'Eyebrows': (0.0, 0.0, 0.006),
            'Top Eyelids': (0.0, 0.0, -0.018),
            'Left Lip': (0.0, 0.0, -0.032),
            'Right Lip': (0.0, 0.0, -0.032),
            'Eyes': (0.0, 0.0, -0.008),
        },
        'scale': {'Top Eyelids': (1.0, 1.0, 0.82)},
        'rot': (
            ('Eyebrow.R', 'Y', -16.0),
            ('Eyebrow.L', 'Y', 16.0),
        ),
    },
    'surprised': {
        'label': '惊讶',
        'trans': {
            'Eyebrows': (0.0, 0.0, 0.055),
            'Top Eyelids': (0.0, 0.0, 0.008),
            'Bottom Eyelids': (0.0, 0.0, -0.008),
            'Mouth': (0.0, 0.0, -0.038),
            'Left Lip': (-0.030, 0.0, 0.0),
            'Right Lip': (0.030, 0.0, 0.0),
        },
    },
    'curious': {
        'label': '好奇 / 坏笑',
        'trans': {
            'Eyebrow.L': (0.0, 0.0, 0.045),
            'Left Lip': (0.0, 0.0, 0.036),
            'Right Lip': (0.0, 0.0, -0.008),
        },
        'scale': {'Top Eyelids.L': (1.0, 1.0, 0.88)},
    },
    'blink': {
        'label': '眨眼',
        'trans': {'Blink': (0.0, 0.0, -0.050)},
    },
}

_ALIASES = {
    'smile': 'happy', 'smiling': 'happy', 'joy': 'happy', '愉快': 'happy', '笑': 'happy',
    'mad': 'angry', 'annoyed': 'angry', '生气': 'angry', '怒': 'angry',
    'sorrow': 'sad', 'unhappy': 'sad', '难过': 'sad', '伤心': 'sad',
    'surprise': 'surprised', 'shock': 'surprised', '惊讶': 'surprised',
    'smirk': 'curious', '好奇': 'curious', '坏笑': 'curious',
    'calm': 'neutral', 'none': 'neutral', '平静': 'neutral', '无': 'neutral',
    '眨眼': 'blink', '闭眼': 'blink',
}


def resolve_face(raw, pose=None):
    """把 DSH_FACE 的值解析成预设名；返回 None 表示不做表情。"""
    name = (raw or 'auto').strip().lower()
    if name in ('0', 'off', 'false', 'no', 'disabled'):
        return None
    if name in ('1', 'on', 'true', 'yes'):
        return 'happy'
    if name == 'auto':
        return 'happy' if (pose or '').lower() == 'snapshot' else None
    name = _ALIASES.get(name, name)
    if name in FACE_PRESETS:
        return name
    return None


def _translate_world(pb, delta, scale=1.0):
    m = pb.matrix.copy()
    m.translation.x += delta[0] * scale
    m.translation.y += delta[1] * scale
    m.translation.z += delta[2] * scale
    pb.matrix = m


def _rotate_world(pb, axis, deg, scale=1.0):
    if abs(deg) < 1e-9:
        return
    pivot = pb.matrix.translation.copy()
    rot = mathutils.Matrix.Rotation(math.radians(deg * scale), 4, axis)
    m = (mathutils.Matrix.Translation(pivot) @ rot
         @ mathutils.Matrix.Translation(-pivot) @ pb.matrix)
    pb.matrix = m


def _scale_world(pb, s, scale=1.0):
    sx = 1.0 + (s[0] - 1.0) * scale
    sy = 1.0 + (s[1] - 1.0) * scale
    sz = 1.0 + (s[2] - 1.0) * scale
    pivot = pb.matrix.translation.copy()
    m = (mathutils.Matrix.Translation(pivot)
         @ mathutils.Matrix.Diagonal((sx, sy, sz, 1.0))
         @ mathutils.Matrix.Translation(-pivot) @ pb.matrix)
    pb.matrix = m


def _reset_face_controls(arm):
    for name in _PANEL_CONTROLS + _DIRECT_LIPS:
        pb = arm.pose.bones.get(name)
        if pb is not None:
            pb.matrix_basis.identity()


def _set_bsdf_color(mat, color):
    if not mat or not mat.use_nodes:
        return
    for node in mat.node_tree.nodes:
        if node.type == 'BSDF_PRINCIPLED':
            node.inputs['Base Color'].default_value = (*color, 1.0)
        elif node.type == 'GROUP' and node.node_tree:
            for sub in node.node_tree.nodes:
                if sub.type == 'BSDF_PRINCIPLED':
                    sub.inputs['Base Color'].default_value = (*color, 1.0)
    try:
        mat.diffuse_color = (*color, 1.0)
    except Exception:
        pass


def _face_image():
    """找到 Skin 材质上挂的用户皮肤图像节点。"""
    for mat in bpy.data.materials:
        if not mat.use_nodes or mat.name.split('.')[0] != 'Skin':
            continue
        for node in mat.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image is not None:
                return node
    return None


def _sample_skin(img):
    """返回 (眼睛颜色, 眉毛颜色, 肤色)。坐标按 64x64 标准皮肤布局缩放。"""
    w, h = img.size
    if w < 64 or h < 32:
        return None
    s = max(1, w // 64)
    px = list(img.pixels)

    def rgb(x, y):
        x = min(max(x, 0), w - 1)
        y = min(max(y, 0), h - 1)
        i = ((h - 1 - y) * w + x) * 4
        return (px[i], px[i + 1], px[i + 2])

    eye = None
    best = -1.0
    for y in range(12 * s, 14 * s):
        for x in range(8 * s, 16 * s):
            r, g, b = rgb(x, y)
            sat = max(r, g, b) - min(r, g, b)
            # 瞳孔是深色蓝/绿；米色皮肤和棕色眼线要被排除。
            cool = g > r * 1.05 or b > r * 1.05
            if cool and max(r, g, b) < 0.65 and sat > best:
                best = sat
                eye = (r, g, b)
    # 找不到冷色瞳孔（例如纯色/无眼皮肤）时返回 None，调用方保留 BPS 默认紫瞳；
    # 不要拿肤色/棕色阴影去染，否则会得到一双米色眼睛。

    brow = None
    lum = 9.0
    for y in range(9 * s, 12 * s):
        for x in range(8 * s, 16 * s):
            r, g, b = rgb(x, y)
            v = 0.2126 * r + 0.7152 * g + 0.0722 * b
            if v < lum:
                lum = v
                brow = (r, g, b)

    # 肤色取两颊：太暗/太亮就退回脸部中间亮度。
    cheek = rgb(8 * s, 12 * s)
    if max(cheek) < 0.2 or min(cheek) > 0.95:
        vals = [rgb(x, y) for y in range(12 * s, 16 * s) for x in range(8 * s, 16 * s)]
        vals.sort(key=lambda c: 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2])
        mid = vals[int(len(vals) * 0.45):int(len(vals) * 0.8)] or vals
        cheek = tuple(sum(c[i] for c in mid) / len(mid) for i in range(3))
    return eye, brow, cheek


def _prepare_skin_face():
    """盖掉皮肤上画的眼睛/眉毛/嘴，保留刘海；同时给 3D 瞳孔/眉毛上色。

    只改脸部 8x8 像素区域，身体/头发 UV 不受影响。返回日志 dict。
    """
    node = _face_image()
    info = {'blanked': False, 'eye': None, 'brow': None}
    if node is None or node.image is None:
        return info
    img = node.image
    w, h = img.size
    if w < 64 or h < 32:
        return info
    s = max(1, w // 64)
    eye, brow, skin = _sample_skin(img)
    if not skin:
        return info
    info['eye'] = eye
    info['brow'] = brow

    # 眼睛/眉毛的 3D 材质；没有可靠瞳孔色时保留 BPS 默认紫瞳。
    if eye:
        for name in ('pupil top.r', 'pupil bottom.r', 'pupil top.l', 'pupil bottom.l'):
            _set_bsdf_color(bpy.data.materials.get(name), eye)
        for name in ('Pupils',):
            mat = bpy.data.materials.get(name)
            if mat and mat.use_nodes:
                for n in mat.node_tree.nodes:
                    if n.type == 'GROUP' and n.node_tree and 'Pupils' in n.node_tree.name:
                        for inp in n.inputs:
                            if inp.name in ('Right 1', 'Right 2', 'Left 1', 'Left 2'):
                                f = 0.75 if inp.name.endswith('1') else 1.25
                                inp.default_value = (min(1.0, eye[0] * f),
                                                     min(1.0, eye[1] * f),
                                                     min(1.0, eye[2] * f), 1.0)
    if brow:
        _set_bsdf_color(bpy.data.materials.get('eyebrow.r'), brow)

    # 复制一张图再改，避免污染其他材质共享的同一张用户皮肤。
    newimg = img.copy()
    newimg.name = '用户皮肤_净脸'
    px = list(newimg.pixels)
    for y in range(8 * s + 2 * s, 8 * s + 8 * s):
        for x in range(8 * s, 16 * s):
            i = ((h - 1 - y) * w + x) * 4
            px[i], px[i + 1], px[i + 2], px[i + 3] = skin[0], skin[1], skin[2], 1.0
    newimg.pixels = px
    try:
        newimg.pack()
    except Exception:
        pass
    node.image = newimg
    info['blanked'] = True
    return info


def apply_face(arm, name, *, scale=1.0, log=None):
    """把预设表情应用到 BPS 骨架上。返回 dict，供调用方打日志/测试断言。"""
    info = {'name': name, 'moved': [], 'shown': 0, 'blanked': False, 'eye': None}
    if arm is None:
        return info
    preset = FACE_PRESETS.get(name)
    if preset is None:
        if log:
            log(f'    ⚠️ 未知表情 {name!r}，跳过（可用: {", ".join(FACE_PRESETS)}）')
        return info

    _reset_face_controls(arm)

    # 放出 3D 五官，并把皮肤上的画版五官盖掉。
    for obj_name in _SHOW_3D:
        ob = bpy.data.objects.get(obj_name)
        if ob is not None and ob.hide_render:
            ob.hide_render = False
            info['shown'] += 1
    skin_info = _prepare_skin_face()
    info['blanked'] = skin_info['blanked']
    info['eye'] = skin_info['eye']

    for bone, delta in preset.get('trans', {}).items():
        pb = arm.pose.bones.get(bone)
        if pb is None:
            continue
        _translate_world(pb, delta, scale)
        info['moved'].append(bone)
    for bone, axis, deg in preset.get('rot', ()):
        pb = arm.pose.bones.get(bone)
        if pb is None:
            continue
        _rotate_world(pb, axis, deg, scale)
        info['moved'].append(bone)
    for bone, s in preset.get('scale', {}).items():
        pb = arm.pose.bones.get(bone)
        if pb is None:
            continue
        _scale_world(pb, s, scale)
        info['moved'].append(bone)
    bpy.context.view_layer.update()

    if log:
        extra = '，净脸+取样眼' if info['blanked'] else ''
        log(f'    表情 {preset["label"]}（{name}）：{len(info["moved"])} 个面部控制 ×{scale:.2f}，'
            f'放 3D 五官 {info["shown"]} 个{extra}')
    return info


def apply_face_from_env(arm, pose=None, log=None):
    """读 DSH_FACE / DSH_POSE / DSH_SMILE_SCALE。"""
    pose = pose if pose is not None else os.environ.get('DSH_POSE')
    name = resolve_face(os.environ.get('DSH_FACE'), pose)
    if name is None:
        return None
    try:
        scale = float(os.environ.get('DSH_SMILE_SCALE') or 1.0)
    except ValueError:
        scale = 1.0
    return apply_face(arm, name, scale=scale, log=log)
