# -*- coding: utf-8 -*-
"""import_map.py —— 把导出的 Minecraft 地形 OBJ 装成可编辑的 Blender 背景地图。

用法（由 py/pipeline.py 调用，不直接面向用户）::

    blender -b --factory-startup --python import_map.py -- \
        <world.obj> <out_dir> <name> <WxH> <samples> <preview 1|0>

产物::

    <out_dir>/<name>.blend      MAP / LIGHTS / CAMERA 三个集合
    <out_dir>/预览.png          preview=1 时
    <out_dir>/导入信息.json     对象/面/材质统计

设计说明
--------
这是本项目的原创实现：存档解析、区域计算、导出、缓存、场景组织都在
py/anvil.py / py/blender/export_obj.py / py/pipeline.py 里完成。这里的
「OBJ 世界导入 → 准备材质 → 归入 MAP 集合」工作流参考了 MCprep、Mineways、
MiBlend、MCBlend 等公开工具的通用做法，但不包含、也不依赖它们的代码。
MCprep 只作为可选的材质后端：装了就用它准备材质，没装就走下面的内置兜底。
"""

import json
import math
import os
import re
import sys

import addon_utils
import bpy
import mathutils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _compat import (set_engine, set_view_transform,  # noqa: E402
                     set_closest_interpolation, eval_bbox)


def log(msg):
    print(msg, flush=True)


def _parse_res(text, default=(1280, 720)):
    m = re.match(r'^\s*(\d+)\s*[x×]\s*(\d+)\s*$', str(text or ''))
    if not m:
        return default
    w, h = int(m.group(1)), int(m.group(2))
    return (w, h) if w > 0 and h > 0 else default


def _safe_name(name):
    name = os.path.basename(str(name or '背景_世界')).strip()
    name = re.sub(r'[\\/:*?"<>|]+', '_', name)
    return name or '背景_世界'


# ─────────────────────────────────────────────────────────────
# 材质：MCprep 优先，失败时内置兜底
# ─────────────────────────────────────────────────────────────
_BIOME_TINT = {
    'grass_block_top': (0.36, 0.72, 0.16),
    'water_still': (0.20, 0.42, 0.85),
    'water_flow': (0.20, 0.42, 0.85),
}
_LEAF_TINT = (0.28, 0.62, 0.16)


def _tint_for(name):
    base = str(name or '').split('.')[0].lower()
    if base in _BIOME_TINT:
        return _BIOME_TINT[base]
    if 'leaves' in base or 'vine' in base or 'fern' in base:
        return _LEAF_TINT
    if base in ('grass', 'short_grass', 'tall_grass', 'grass_block'):
        return _BIOME_TINT['grass_block_top']
    return None


def _mix_rgb(nt):
    try:
        n = nt.nodes.new('ShaderNodeMixRGB')
        n.blend_type = 'MULTIPLY'
        n.inputs['Fac'].default_value = 1.0
        return n, n.inputs[1], n.inputs[2], n.outputs[0]
    except Exception:
        n = nt.nodes.new('ShaderNodeMix')
        n.data_type = 'RGBA'
        n.blend_type = 'MULTIPLY'
        n.inputs[0].default_value = 1.0
        return n, n.inputs[6], n.inputs[7], n.outputs[2]


def fallback_prep_materials():
    """最基础的材质准备：接贴图 + 群系乘色 + 调平粗糙度。"""
    tinted = tuned = 0
    for m in bpy.data.materials:
        if not (m.use_nodes and m.node_tree):
            continue
        nt = m.node_tree
        tex = next((x for x in nt.nodes if x.type == 'TEX_IMAGE' and x.image), None)
        bsdf = next((x for x in nt.nodes if x.type == 'BSDF_PRINCIPLED'), None)
        if not tex or not bsdf:
            continue
        for key, val in (('Roughness', 0.68), ('Specular IOR Level', 0.35),
                         ('Specular', 0.30)):
            sock = bsdf.inputs.get(key)
            if sock is not None and not sock.is_linked:
                try:
                    sock.default_value = val
                    tuned += 1
                except Exception:
                    pass
        try:
            set_closest_interpolation(m)
        except Exception:
            pass
        try:
            if tex.image and (tex.image.depth == 32 or 'leaves' in m.name.lower()
                              or 'water' in m.name.lower()):
                m.blend_method = 'CLIP'
        except Exception:
            pass
        tint = _tint_for(m.name)
        if not tint:
            continue
        if any(x.type in ('MIX', 'MIX_RGB') for x in nt.nodes):
            continue
        in_sock = bsdf.inputs.get('Base Color')
        if not in_sock:
            continue
        mix, c1, c2, out = _mix_rgb(nt)
        c2.default_value = (tint[0], tint[1], tint[2], 1.0)
        for lk in list(in_sock.links):
            nt.links.remove(lk)
        nt.links.new(tex.outputs['Color'], c1)
        nt.links.new(out, in_sock)
        tinted += 1
    return tinted, tuned


# ─────────────────────────────────────────────────────────────
# 场景
# ─────────────────────────────────────────────────────────────
def enable_mcprep():
    try:
        addon_utils.enable('MCprep_addon', default_set=True, persistent=True)
        return True
    except Exception as e:
        log(f'    MCprep 不可用（{type(e).__name__}: {str(e)[:100]}）')
        return False


def prep_materials(objs):
    mcprep = enable_mcprep()
    prep_ok = False
    if mcprep and objs:
        bpy.ops.object.select_all(action='DESELECT')
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        try:
            r = bpy.ops.mcprep.prep_materials(autoFindMissingTextures=True,
                                              usePrincipledShader=True,
                                              useEmission=True)
            prep_ok = 'FINISHED' in str(r) or 'RUNNING_MODAL' in str(r)
            log(f'    MCprep prep_materials: {r}')
        except Exception as e:
            log(f'    MCprep prep_materials 失败: {type(e).__name__}: {str(e)[:140]}')
    if not prep_ok:
        tinted, tuned = fallback_prep_materials()
        log(f'    内置材质兜底：染色 {tinted} 个，调平 {tuned} 处')
    return prep_ok


def _move_to_collection(obj, coll):
    for c in list(obj.users_collection):
        try:
            c.objects.unlink(obj)
        except Exception:
            pass
    coll.objects.link(obj)


def _make_sky(scene):
    """干净的渐变天空。

    这里刻意不用 Nishita：背景地图通常从高处俯拍，物理天空在画面里
    容易变成一大片深蓝/黑，反而抢地形。用世界背景色 + 轻微渐变最稳，
    换 Blender 版本也不会变。
    """
    world = bpy.data.worlds.new('MCWorld')
    scene.world = world
    world.use_nodes = True
    nt = world.node_tree
    for n in list(nt.nodes):
        nt.nodes.remove(n)
    out = nt.nodes.new('ShaderNodeOutputWorld')
    bg = nt.nodes.new('ShaderNodeBackground')
    bg.inputs['Color'].default_value = (0.62, 0.78, 0.96, 1.0)
    bg.inputs['Strength'].default_value = 0.9
    nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
    log('    天空: 纯色渐变（0.62, 0.78, 0.96）')


def _add_ground(coll, center, size, z):
    """地图边缘的兜底地面：避免相机拍到 OBJ 方形边界外的虚空。"""
    mesh = bpy.data.meshes.new('Ground')
    half = max(size * 4.0, 64.0)
    verts = [(-half, -half, 0), (half, -half, 0),
             (half, half, 0), (-half, half, 0)]
    mesh.from_pydata(verts, [], [(0, 1, 2, 3)])
    mesh.update()
    ob = bpy.data.objects.new('Ground', mesh)
    ob.location = (center.x, center.y, z)
    mat = bpy.data.materials.new('GroundMat')
    mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes
                 if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf:
        bsdf.inputs['Base Color'].default_value = (0.36, 0.56, 0.28, 1.0)
        bsdf.inputs['Roughness'].default_value = 0.92
    ob.data.materials.append(mat)
    coll.objects.link(ob)
    return ob


def _add_sun(coll):
    data = bpy.data.lights.new('Sun', 'SUN')
    data.energy = 4.0
    data.angle = math.radians(2.5)
    data.color = (1.0, 0.95, 0.86)
    sun = bpy.data.objects.new('Sun', data)
    sun.rotation_euler = (math.radians(52), 0.0, math.radians(32))
    coll.objects.link(sun)
    return sun


def _add_fill(coll, center, size):
    data = bpy.data.lights.new('Fill', 'AREA')
    data.energy = max(300.0, size * size * 0.8)
    data.shape = 'DISK'
    data.size = max(8.0, size * 0.9)
    data.color = (0.72, 0.82, 1.0)
    ob = bpy.data.objects.new('Fill', data)
    ob.location = center + mathutils.Vector((size * 0.9, -size * 0.9, size * 0.7))
    d = center - ob.location
    ob.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    coll.objects.link(ob)
    return ob


def _add_camera(coll, scene, center, size):
    data = bpy.data.cameras.new('Camera')
    data.lens = 55
    data.clip_start = 0.1
    data.clip_end = max(2000.0, size * 30)
    cam = bpy.data.objects.new('Camera', data)
    # 收紧到地图内部：只露出地形，不把 OBJ 的方形边界和虚空拍进来。
    cam.location = center + mathutils.Vector((size * 0.40, -size * 1.05, size * 0.95))
    aim = center + mathutils.Vector((0, 0, size * 0.02))
    cam.rotation_euler = (aim - cam.location).to_track_quat('-Z', 'Y').to_euler()
    coll.objects.link(cam)
    scene.camera = cam
    return cam


def main():
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if len(argv) < 5:
        raise SystemExit('用法: import_map.py -- <obj> <out_dir> <name> <WxH> <samples> <preview>')
    obj_path, out_dir, name, res, samples = argv[:5]
    preview = (argv[5] if len(argv) > 5 else '1') not in ('0', 'false', 'False', '')
    name = _safe_name(name)
    width, height = _parse_res(res)
    os.makedirs(out_dir, exist_ok=True)

    log('=' * 64)
    log(f'背景地图导入: {obj_path}')
    log('=' * 64)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = set_engine(scene, 'EEVEE')
    try:
        scene.eevee.taa_render_samples = max(8, int(samples))
    except Exception:
        pass
    scene.render.resolution_x = width
    scene.render.resolution_y = height
    scene.render.resolution_percentage = 100
    scene.render.film_transparent = False
    scene.render.image_settings.file_format = 'PNG'
    set_view_transform(scene, 'AgX')

    # ① 导入 OBJ
    if hasattr(bpy.ops.wm, 'obj_import'):
        bpy.ops.wm.obj_import(filepath=obj_path)
    else:
        bpy.ops.import_scene.obj(filepath=obj_path)
    meshes = [o for o in bpy.data.objects if o.type == 'MESH']
    if not meshes:
        raise SystemExit('OBJ 导入后没有网格')
    faces = sum(len(o.data.polygons) for o in meshes if o.data)
    log(f'    导入 {len(meshes)} 网格 / {faces} 面 / {len(bpy.data.materials)} 材质')

    # ② 集合：MAP / LIGHTS / CAMERA
    map_coll = bpy.data.collections.new('MAP')
    light_coll = bpy.data.collections.new('LIGHTS')
    cam_coll = bpy.data.collections.new('CAMERA')
    for c in (map_coll, light_coll, cam_coll):
        scene.collection.children.link(c)
    for o in meshes:
        _move_to_collection(o, map_coll)

    # ③ 材质
    prep_ok = prep_materials(meshes)

    # ④ 天空 / 灯光 / 相机
    _make_sky(scene)
    mn, mx = eval_bbox(meshes, bpy.context.evaluated_depsgraph_get())
    if mn is None:
        mn = mathutils.Vector((-64, -64, 0))
        mx = mathutils.Vector((64, 64, 64))
    center = (mn + mx) * 0.5
    size = max((mx - mn).x, (mx - mn).y, (mx - mn).z, 8.0)
    _add_ground(map_coll, center, size, mn.z - max(1.0, size * 0.01))
    _add_sun(light_coll)
    _add_fill(light_coll, center, size)
    _add_camera(cam_coll, scene, center, size)
    log(f'    范围 {tuple(round(v, 2) for v in mn)} .. '
        f'{tuple(round(v, 2) for v in mx)} / 尺寸 {size:.1f}')

    # ⑤ 预览
    preview_path = os.path.join(out_dir, '预览.png')
    if preview:
        try:
            scene.render.filepath = preview_path
            bpy.ops.render.render(write_still=True)
            log(f'    预览: {preview_path}')
        except Exception as e:
            log(f'    预览失败（工程仍保存）: {type(e).__name__}: {str(e)[:140]}')
            preview_path = None

    # ⑥ 保存
    blend_path = os.path.join(out_dir, name + '.blend')
    bpy.ops.wm.save_as_mainfile(filepath=blend_path)
    log(f'    工程: {blend_path}')

    info = {
        'obj': os.path.abspath(obj_path),
        'blend': os.path.abspath(blend_path),
        'preview': os.path.abspath(preview_path) if preview_path else None,
        'objects': len(meshes),
        'faces': faces,
        'materials': len(bpy.data.materials),
        'mcprep': bool(prep_ok),
        'collections': ['MAP', 'LIGHTS', 'CAMERA'],
        'bounds': {'min': list(mn), 'max': list(mx)},
        'finished_at': __import__('time').strftime('%Y-%m-%d %H:%M:%S'),
    }
    with open(os.path.join(out_dir, '导入信息.json'), 'w', encoding='utf-8') as f:
        json.dump(info, f, ensure_ascii=False, indent=2)
    log('完成')


main()
