# -*- coding: utf-8 -*-
"""_compat —— Blender 版本差异的兼容层

把反复踩到的版本坑集中到一处，所有脚本共用。
每一条都注明是哪个版本的改动、报什么错。

用法::

    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _compat import iter_fcurves, set_engine, set_sky_type, set_view_transform
"""


# ─────────────────────────────────────────────────────────────
# Action 的 fcurve 遍历
# ─────────────────────────────────────────────────────────────
def iter_fcurves(action):
    """遍历一个 Action 里的所有 fcurve，跨版本兼容。

    ⚠️ Blender 4.4 起 `action.fcurves` 被移除，改为：
         action.layers[i].strips[j].channelbags[k].fcurves
    直接写 `for fc in action.fcurves` 会报：
         AttributeError: 'Action' object has no attribute 'fcurves'
    （旧版用 slots 的还有 `action.slots`，这里一并兜住。）

    产出: fcurve 对象
    """
    if action is None:
        return
    # 老版本
    fcs = getattr(action, 'fcurves', None)
    if fcs is not None:
        for fc in fcs:
            yield fc
        return
    # 4.4+：layers → strips → channelbags
    for layer in getattr(action, 'layers', []):
        for strip in getattr(layer, 'strips', []):
            for cb in getattr(strip, 'channelbags', []):
                for fc in getattr(cb, 'fcurves', []):
                    yield fc


def action_fcurve_count(action):
    """方便打日志"""
    return sum(1 for _ in iter_fcurves(action))


def set_all_interpolation(action, mode='LINEAR'):
    """把某个 Action 的所有关键帧插值设成统一模式"""
    n = 0
    for fc in iter_fcurves(action):
        for kp in fc.keyframe_points:
            kp.interpolation = mode
            n += 1
    return n


# ─────────────────────────────────────────────────────────────
# 渲染引擎
# ─────────────────────────────────────────────────────────────
ENGINE_CANDIDATES = ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'CYCLES')


def set_engine(scene, prefer='EEVEE'):
    """设置渲染引擎，跨版本。

    ⚠️ 4.2 起 EEVEE 的标识符从 'BLENDER_EEVEE' 变成 'BLENDER_EEVEE_NEXT'，
    5.x 又可能改回。所以要逐个试。
    """
    order = (('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE') if prefer.upper() == 'EEVEE'
             else ('CYCLES',))
    for cand in order:
        try:
            scene.render.engine = cand
            return cand
        except Exception:
            continue
    return scene.render.engine


# ─────────────────────────────────────────────────────────────
# 物理天空类型
# ─────────────────────────────────────────────────────────────
SKY_CANDIDATES = ('MULTIPLE_SCATTERING', 'NISHITA', 'HOSEK_WILKIE', 'PREETHAM')


def set_sky_type(sky_node, prefer='MULTIPLE_SCATTERING'):
    """设置 Sky Texture 的模型，跨版本。

    ⚠️ Blender 5.2 把 'NISHITA' 改名成 'MULTIPLE_SCATTERING'，
    报错: enum "NISHITA" not found in ('SINGLE_SCATTERING',
          'MULTIPLE_SCATTERING', 'PREETHAM', 'HOSEK_WILKIE')
    """
    order = (prefer,) + tuple(k for k in SKY_CANDIDATES if k != prefer)
    for k in order:
        try:
            sky_node.sky_type = k
            return k
        except Exception:
            continue
    return None


# ─────────────────────────────────────────────────────────────
# 色彩管理 / 视图转换
# ─────────────────────────────────────────────────────────────
VT_CANDIDATES = ('Standard', 'Filmic', 'AgX', 'Filmic Log', 'Raw')


def set_view_transform(scene, prefer='Standard'):
    """设置视图转换，跨版本（不同版本内置的转换名不同）"""
    order = (prefer,) + tuple(v for v in VT_CANDIDATES if v != prefer)
    for v in order:
        try:
            scene.view_settings.view_transform = v
            return v
        except Exception:
            continue
    return scene.view_settings.view_transform


# ─────────────────────────────────────────────────────────────
# 图像插值（像素风）
# ─────────────────────────────────────────────────────────────
def set_closest_interpolation(mat):
    """把材质里的图像纹理节点设成最近邻（像素风）。

    ⚠️ Blender 5.x 的 `Image` 没有 `interpolation` 属性（设在纹理节点上），
    写 `img.interpolation = 'Closest'` 会报：
        AttributeError: 'Image' object has no attribute 'interpolation'
    """
    if not getattr(mat, 'use_nodes', False):
        return 0
    n = 0
    for node in mat.node_tree.nodes:
        if node.type == 'TEX_IMAGE':
            try:
                node.interpolation = 'Closest'
                n += 1
            except Exception:
                pass
    return n


# ─────────────────────────────────────────────────────────────
# 求值后的包围盒
# ─────────────────────────────────────────────────────────────
def eval_bbox(objs, depsgraph=None):
    """【真实】世界包围盒 —— 走 depsgraph 求值后的网格。

    ⚠️ `o.bound_box` **不算修改器形变**。被 Armature 修改器驱动的网格
    拿到的是【静止姿态】包围盒，拿它做落地计算角色会浮在半空。

    返回 (min_vec, max_vec)，取不到时返回 (None, None)
    """
    import bpy
    import mathutils
    dg = depsgraph or bpy.context.evaluated_depsgraph_get()
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
        mw = oe.matrix_world
        pts += [mw @ v.co for v in me.vertices]
        oe.to_mesh_clear()
    if not pts:
        return None, None
    return (mathutils.Vector((min(q.x for q in pts), min(q.y for q in pts), min(q.z for q in pts))),
            mathutils.Vector((max(q.x for q in pts), max(q.y for q in pts), max(q.z for q in pts))))


# ─────────────────────────────────────────────────────────────
# 判断"真正的身体网格"（被骨架驱动的那种）
# ─────────────────────────────────────────────────────────────
def is_driven_body(o, armature):
    """网格是否由该骨架驱动、且是可见的身体部件。

    ⚠️ 三条判据是层层试出来的（见实测报告坑 55）：
      · 按名字排除 → BPS 的变体网格（arm slim / teeth）名字里没标记
      · 按离骨架距离 → 时序错，角色还在原点时谁都不算远
      · 按 o.parent is arm → BPS 网格不是父子挂接，是修改器驱动
      · 按"有 Armature 修改器" → 变体网格也挂修改器，仍会混进来
      ✅ 最终：有指向该骨架的 Armature 修改器 **且未隐藏**
         （变体部件在 BPS 里是 hide_render=True 的）
    """
    if o.type != 'MESH' or not o.data or len(o.data.vertices) == 0:
        return False
    n = o.name.lower()
    if any(k in n for k in ('boneshape', 'materials', 'xray', 'helper')):
        return False
    if armature is not None:
        if not any(md.type == 'ARMATURE' and md.object is armature
                   for md in o.modifiers):
            return False
    if o.hide_render or o.hide_viewport:
        return False
    return True
