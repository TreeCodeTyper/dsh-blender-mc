# -*- coding: utf-8 -*-
"""封面_世界 —— 在真实存档的草原上渲染 Steve 持钻石剑

用到的课程结论（B站 Woodplank 课）：
  T55   BPS V4.0.9（非 BETA1）+ 以【集合】方式追加
  T11   mcprep.prep_materials 管材质
  实测  MCprep 自己做群系染色（所以导出器不再烘染色）
  T30   视图转换用「标准」
  T48   60-80mm、角色偏左留白、三点布光(100/50-70/100)

钻石剑挂载（坑 51）：
  剑的【网格本地】包围盒 X[-0.7,1.3] Y[-0.7,1.3] Z[-0.02,0.1]，
  刀刃是【本地对角方向】——柄在 (-0.7,-0.7)，尖在 (1.3,1.3)。
  而且对象自带 rotation_euler=(1.5771,0,0)（绕X 90°）。
  之前直接写 o.matrix_world = ... 会把这个自带旋转【覆盖掉】，
  剑就躺平贴在身侧了 → 看起来"没拿上"。
  正确做法：用刀刃方向 + 面法线构造正交基，自己算完整矩阵。

用法: blender -b --factory-startup --python 封面_世界.py -- <世界.obj> <输出目录> [相机方位角]
"""
import math
import os
import sys

import addon_utils
import bpy
import mathutils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _compat import (set_engine, set_sky_type, set_view_transform,   # noqa: E402
                     eval_bbox, is_driven_body)

PACK = r"E:\游戏与自媒体\AI自媒体工作区\pack"
BPS = os.path.join(PACK, "BPS_Rig_v4", "BPS Character Rig V4.0.9.blend")
ITEM = os.path.join(PACK, "Cycles_Minecraft_Rig_BSS_Edit_V6_5",
                    "Cycles_Minecraft_Rig BSS Edit V6.5", "Minecraft_Item_Rig.blend")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OBJ = argv[0]
OUT = argv[1]
YAW = float(argv[2]) if len(argv) > 2 else 25.0       # 角色朝向（度）
LENS = float(argv[3]) if len(argv) > 3 else 52.0
VT = argv[4] if len(argv) > 4 else 'Standard'         # 视图转换
SKY = float(argv[5]) if len(argv) > 5 else 0.03        # 天空强度
CAM_AZ = float(argv[6]) if len(argv) > 6 else 210.0    # 视线方位角（MC 角度）
DIST = float(argv[7]) if len(argv) > 7 else 12.2        # 相机距离
# ★ 落地时让脚【略微陷入】地面，而不是精确贴面。
#   实测：精确贴面时间隙 0.019（数值上正确），但 MC 方块边缘锐利，
#   这点缝在画面里会读成"悬浮"。往下沉 0.06 让鞋底真正埋进草方块顶面。
SINK = float(argv[8]) if len(argv) > 8 else 0.06
os.makedirs(OUT, exist_ok=True)
LOG = []


def p(s=''):
    LOG.append(str(s))
    print(s)


def finish(code=0):
    open(os.path.join(OUT, '_封面日志.txt'), 'w', encoding='utf-8').write('\n'.join(LOG))
    sys.exit(code)


def bbox(objs):
    pts = []
    for o in objs:
        if o.type == 'MESH' and o.data and o.data.vertices:
            pts += [o.matrix_world @ mathutils.Vector(c) for c in o.bound_box]
    if not pts:
        return None, None
    return (mathutils.Vector((min(v.x for v in pts), min(v.y for v in pts), min(v.z for v in pts))),
            mathutils.Vector((max(v.x for v in pts), max(v.y for v in pts), max(v.z for v in pts))))


def eval_bbox(objs):
    """★ 真实世界包围盒 —— 用 depsgraph 求值【之后】的网格。

    ⚠️ 坑 59：`o.bound_box` **不算修改器形变**。
    BPS 角色是靠 Armature 修改器（顶点组蒙皮）驱动的，bound_box 给的是
    【未变形的静止姿态】包围盒 —— 拿它算落地，角色就会悬在半空
    （实测腿底落在 z=65.75，而地面是 65，整整浮了 0.75 格）。
    必须走 evaluated_get(dg).to_mesh() 取形变后的顶点。
    """
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
        mw = oe.matrix_world
        for v in me.vertices:
            pts.append(mw @ v.co)
        oe.to_mesh_clear()
    if not pts:
        return None, None
    return (mathutils.Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts))),
            mathutils.Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts))))


def basis_from(blade, normal):
    """构造 3x3 旋转矩阵 M，使得
         M @ normalize(1,1,0) = blade      （刀刃朝 blade）
         M @ (0,0,1)          = normal     （剑面朝 normal）
    剑的网格本地：刀刃沿对角 (1,1,0)，薄轴是 Z。
    """
    a = blade.normalized()
    c = normal - a * normal.dot(a)
    if c.length < 1e-6:
        c = mathutils.Vector((0, 0, 1))
    c.normalize()
    b = c.cross(a).normalized()
    s = 1.0 / math.sqrt(2.0)
    col0 = (a + b) * s
    col1 = (a - b) * s
    return mathutils.Matrix(((col0.x, col1.x, c.x),
                             (col0.y, col1.y, c.y),
                             (col0.z, col1.z, c.z)))


p("=" * 72)
p("封面_世界 —— 真实存档草原 + Steve 持钻石剑")
p("=" * 72)

bpy.ops.wm.read_factory_settings(use_empty=True)      # 坑 47：先重置
addon_utils.enable('MCprep_addon', default_set=True, persistent=True)
p("\n[1] MCprep 已启用")

# ---- 导入世界（Blender 原生 obj 导入器）----
p("[2] Blender 原生导入世界 OBJ")
if hasattr(bpy.ops.wm, "obj_import"):
    bpy.ops.wm.obj_import(filepath=OBJ)
else:
    bpy.ops.import_scene.obj(filepath=OBJ)
wmesh = [o for o in bpy.data.objects if o.type == 'MESH']
p(f"    {len(wmesh)} 网格 / {sum(len(o.data.polygons) for o in wmesh if o.data)} 面 / "
  f"{len(bpy.data.materials)} 材质")

p("\n[3] mcprep.prep_materials（草会变绿）")
bpy.ops.object.select_all(action='DESELECT')
for o in wmesh:
    o.select_set(True)
bpy.context.view_layer.objects.active = wmesh[0]
try:
    r = bpy.ops.mcprep.prep_materials(autoFindMissingTextures=True,
                                      usePrincipledShader=True, useEmission=True)
    p(f"    返回 {r}")
except Exception as e:
    p(f"    FAIL {type(e).__name__}: {str(e)[:140]}")
for m in bpy.data.materials:
    if 'grass' in m.name:
        for nd in m.node_tree.nodes:
            if nd.type == 'MIX':
                for inp in nd.inputs:
                    dv = getattr(inp, 'default_value', None)
                    try:
                        if dv is not None and len(dv) == 4 and not inp.is_linked \
                                and (abs(dv[0] - 1) > 0.02 or abs(dv[1] - 1) > 0.02):
                            p(f"    ★ {m.name} 染色 = ({dv[0]:.3f},{dv[1]:.3f},{dv[2]:.3f})")
                    except Exception:
                        pass

wmn, wmx = bbox(wmesh)
p(f"    世界范围 {tuple(round(v,1) for v in (wmx-wmn))}")
# ⚠️ 坑 52：两个错一起犯过 ——
#   ① 不要拿【世界包围盒顶面】当地面（那是最高的树顶 z=83，草原地面在 z=65）；
#   ② 世界【不在原点】！MC(-1798,-1145) 经轴转换后在 Blender(-1798,+1145)，
#      在 (0,0) 竖直打射线会完全打空。
# 正确做法：在世界网格的 xy 中心竖直向下打射线，用 Blender 自己的 ray_cast。
WX = (wmn.x + wmx.x) / 2.0
WY = (wmn.y + wmx.y) / 2.0
bpy.context.view_layer.update()
dg0 = bpy.context.evaluated_depsgraph_get()
probe_top = wmx.z + 10.0
hit, hloc, hnrm, hidx, hobj, hmat = bpy.context.scene.ray_cast(
    dg0, mathutils.Vector((WX, WY, probe_top)),
    mathutils.Vector((0.0, 0.0, -1.0)), distance=(probe_top - wmn.z) + 20.0)
GROUND = hloc.z if hit else wmn.z
p(f"    世界 xy 中心 ({WX:.1f}, {WY:.1f})")
p(f"    射线向下探测地面: {'命中 ' + str(hobj.name) if hit else '没命中'} "
  f"→ 地面 z={GROUND:.2f}")

# ---- 角色 ----
p("\n[4] 追加 BPS 角色 V4.0.9（集合方式）")
before = set(bpy.data.objects.keys())
with bpy.data.libraries.load(BPS, link=False) as (src, dst):
    dst.collections = list(src.collections)
bpy.context.view_layer.update()
sc = bpy.context.scene.collection
linked = {c.name for c in sc.children}
for c in bpy.data.collections:
    if c.name not in linked:
        sc.children.link(c)
bpy.context.view_layer.update()
char = [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]
for o in char:
    if not o.users_collection:
        try:
            sc.objects.link(o)
        except Exception:
            pass
arm = next((o for o in char if o.type == 'ARMATURE'), None)
p(f"    {len(char)} 对象；骨架 {arm.name if arm else None}"
  f"（{len(arm.data.bones) if arm else 0} 骨）")


def is_body(o, arm=None):
    """判断是不是"真正的身体网格"（被骨架驱动的那种）。

    ⚠️ 坑 55（踩了三次才找对判据）：
      BPS V4 带【两套手臂】：'mesh arm.r'（经典）和 'mesh arm slim.r'
      （Alex 细手臂）。后者实测【丢在世界原点没被摆位】，会把身体包围盒
      撑到跨 68 格，落地计算全错，角色一直落不了地。

      错误判据 ①：按名字排除 —— slim 手臂名字里没有特别标记，排不掉。
      错误判据 ②：离骨架距离 —— 时序错了（调用时角色还在原点，谁都不算远）。
      错误判据 ③：`o.parent is arm` —— BPS 的网格【不是父子挂接】，
                 而是靠 Armature 修改器（顶点组蒙皮）驱动，parent 是 None。
                 结果 body 为空 → bbox 返回 None → 崩。

      正确判据：**有指向该骨架的 Armature 修改器**。
    """
    n = o.name.lower()
    if not (o.type == 'MESH' and o.data and len(o.data.vertices) > 0):
        return False
    if any(k in n for k in ('boneshape', 'materials', 'xray', 'helper')):
        return False
    if arm is not None:
        if not any(md.type == 'ARMATURE' and md.object is arm
                   for md in o.modifiers):
            return False
    # ★ 坑 60：还要排除【隐藏的备用部件】。
    #   BPS V4 带一堆变体网格（Alex 细手臂 'arm slim'、牙齿 'teeth' 等），
    #   它们同样挂了 Armature 修改器，但【没被摆位】——躺在世界原点，
    #   而且 'arm slim' 的 z 范围是 [-0.75, 0.75]，比脚底还低 0.75。
    #   它们设置了 hide_render（渲染里根本不出现），
    #   但求值包围盒会把它们算进去 → 落地时把角色抬高 0.75 格 → 【悬浮】。
    if o.hide_render or o.hide_viewport:
        return False
    return True


body = [o for o in char if is_body(o, arm)]
if not body:                      # 兜底：判据没命中就别让流程崩
    p("    !! 没有任何网格带 Armature 修改器，退回按名字过滤")
    body = [o for o in char if is_body(o, None)]
skipped = [f"{o.name.replace('BPS v4 - ','')}"
           f"{'[隐藏]' if (o.hide_render or o.hide_viewport) else ''}"
           for o in char
           if o.type == 'MESH' and o.data and len(o.data.vertices) > 0
           and o not in body]
p(f"    身体网格 {len(body)} 个（带 Armature 修改器且未隐藏）；"
  f"其余 {len(skipped)} 个: {skipped[:8]}")
cc = mathutils.Vector((WX, WY, GROUND))     # 放到世界 xy 中心的地面上
bpy.context.view_layer.update()

# ⚠️ 坑 59：落地要用【求值后】的包围盒，并且【只移动骨架】。
#   ① o.bound_box 不算 Armature 修改器的形变 → 拿到的是静止姿态包围盒，
#      拿它算落地，角色会浮在半空（实测腿底 65.75 而地面 65，浮 0.75 格）。
#   ② 网格是靠修改器跟随骨架的，单独改网格 location 会破坏蒙皮绑定
#      → 只动 arm.location，网格自动跟。
#   ③ ★ 只算【一次】，不要迭代：后台模式下 view_layer.update() 不足以保证
#      depsgraph 重新求值 —— 迭代时每次读到的都是同一份旧数据，
#      偏移量会滚雪球（实测角色被推到 x=-3527）。
#      角色导入时就在原点，第一次算出的偏移就是正确的。
mn, mx = eval_bbox(body)
if mn is not None:
    dz = (cc.z - SINK) - mn.z          # ★ 下沉 SINK，让脚埋进地面
    dx = cc.x - (mn.x + mx.x) / 2.0
    dy = cc.y - (mn.y + mx.y) / 2.0
    p(f"    求值包围盒 min={tuple(round(v,3) for v in mn)} "
      f"max={tuple(round(v,3) for v in mx)}")
    p(f"    身高 {mx.z-mn.z:.2f}  脚底 z={mn.z:.3f}  地面 {GROUND:.2f}")
    delta = mathutils.Vector((dx, dy, dz))
    if arm is not None:
        arm.location += delta
    else:
        ids = {id(o) for o in char}
        for o in char:
            if o.parent is None or id(o.parent) not in ids:
                o.location += delta
    bpy.context.view_layer.update()
    p(f"    ★ 落地位移 ({dx:+.3f}, {dy:+.3f}, {dz:+.3f})  "
      f"→ 脚底应落在 {mn.z + dz:.3f}（地面 {GROUND:.2f}）")
    p(f"      骨架世界位置 → "
      f"{tuple(round(v,2) for v in arm.matrix_world.translation)}"
      if arm else "")
else:
    p("    !! 求值包围盒取不到，跳过落地")

if arm:
    arm.rotation_euler = (0, 0, math.radians(YAW))
    bpy.context.view_layer.update()
    # 报告朝向：用【求值后】的面部/头部中心比较 y（和落地同理）
    face = next((o for o in char if 'mesh face' in o.name.lower()), None)
    head = next((o for o in char if o.name.lower().endswith('mesh head')), None)
    if face and head:
        fb = eval_bbox([face])
        hb = eval_bbox([head])
        if fb[0] and hb[0]:
            fy = (fb[0].y + fb[1].y) / 2
            hy = (hb[0].y + hb[1].y) / 2
            p(f"    朝向 yaw={YAW}°  面部中心 y={fy:.2f} 头部中心 y={hy:.2f} "
              f"→ 面朝 {'-Y（相机在 -Y，即【面向镜头】）' if fy < hy else '+Y（背对相机）'}")
bpy.context.view_layer.update()

# ---- 钻石剑：用基向量构造矩阵 ----
p("\n[5] 钻石剑挂 Hand.R（用刀刃方向构造旋转矩阵）")
sw = []
if arm and any(b.name == 'Hand.R' for b in arm.data.bones):
    before = set(bpy.data.objects.keys())
    with bpy.data.libraries.load(ITEM, link=False) as (src, dst):
        dst.objects = [n for n in src.objects if n in ('Sword: Diamond',)]
        dst.collections = []
    bpy.context.view_layer.update()
    sw = [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]
    for o in sw:
        if not o.users_collection:
            sc.objects.link(o)

    o = sw[0]
    # 网格本地包围盒（注意：不是 matrix_world，对象自带 90° 旋转要绕开）
    mxs = [v.co.x for v in o.data.vertices]
    mys = [v.co.y for v in o.data.vertices]
    mzs = [v.co.z for v in o.data.vertices]
    p(f"    网格本地包围盒 X[{min(mxs):.2f},{max(mxs):.2f}] "
      f"Y[{min(mys):.2f},{max(mys):.2f}] Z[{min(mzs):.2f},{max(mzs):.2f}]")
    p(f"    对象自带: rot={tuple(round(v,3) for v in o.rotation_euler)} "
      f"scale={tuple(round(v,3) for v in o.scale)}  ← 会被 matrix_world 覆盖")

    # 刀刃本地方向 = 对角 (1,1,0)；柄在 (-0.7,-0.7) 一侧
    handle_local = mathutils.Vector((-0.55, -0.55, 0.04))
    K = 0.5                                   # 网格对角 2.83 → 成品约 1.4

    # 目标世界朝向：刀刃朝【上 + 明显偏外 + 略朝镜头】
    # ⚠️ 坑 56（两次）：① 太偏 -Y 会从正面盖住脸；
    #   ② 偏 +X 不够时，剑尖仍停在头部范围内。
    #   手在身体侧面只有 0.34 格空间，而剑长 1.4 格 —— 必须给足侧向分量，
    #   剑尖才能让开头部（头 x 范围约 -1862.83~-1862.17）。
    blade_w = mathutils.Vector((1.25, -0.42, 1.0))
    normal_w = mathutils.Vector((0.25, -0.96, 0.0))
    R = basis_from(blade_w, normal_w).to_4x4()

    # ★ 坑 55：不要用 pb.tail 当抓握点。
    #   BPS V4 的 Hand.R 骨骼：head z=66.50 → tail z=66.17（还在手臂网格【下面】0.33）。
    #   手臂网格 z[66.50, 67.25] 才是整条手臂，【手 = 网格底部中心 z=66.50】。
    #   用 tail 会把剑柄放到大腿高度，看起来就是"没握在手上"。
    arm_mesh = None
    for x in char:
        n = x.name.lower()
        if x.type == 'MESH' and 'mesh arm' in n and 'slim' not in n \
                and 'layer' not in n and n.rstrip().endswith('.r'):
            arm_mesh = x
            break
    if arm_mesh is None:
        for x in char:
            n = x.name.lower()
            if x.type == 'MESH' and 'mesh arm' in n and 'slim' not in n and 'layer' not in n:
                arm_mesh = x
                break
    if arm_mesh is not None:
        amn, amx = bbox([arm_mesh])
        grip = mathutils.Vector(((amn.x + amx.x) / 2.0,
                                 (amn.y + amx.y) / 2.0,
                                 amn.z))          # 网格底部 = 拳头
        p(f"    右臂网格 {arm_mesh.name}")
        p(f"      z[{amn.z:.2f},{amx.z:.2f}] x[{amn.x:.2f},{amx.x:.2f}] "
          f"y[{amn.y:.2f},{amx.y:.2f}]")
        p(f"    抓握点（拳头=网格底部中心） {tuple(round(v,2) for v in grip)}")
    else:
        pb = arm.pose.bones['Hand.R']
        grip = (arm.matrix_world @ mathutils.Matrix.Translation(pb.tail)).translation
        p(f"    (没找到手臂网格，退回骨骼尾端) {tuple(round(v,2) for v in grip)}")
    hand_pos = grip

    M = (mathutils.Matrix.Translation(hand_pos)
         @ R
         @ mathutils.Matrix.Scale(K, 4)
         @ mathutils.Matrix.Translation(-handle_local))
    for x in sw:
        if x.parent is None:
            x.parent = arm
            x.parent_type = 'BONE'
            x.parent_bone = 'Hand.R'
        x.matrix_world = M
    bpy.context.view_layer.update()

    smn, smx = bbox(sw)
    tip = M @ mathutils.Vector((1.3, 1.3, 0.04))
    p(f"    剑世界包围盒 {tuple(round(v,2) for v in (smx-smn))}")
    p(f"    柄在 {tuple(round(v,2) for v in hand_pos)}  尖在 "
      f"{tuple(round(v,2) for v in tip)}  → 剑刃朝"
      f"{'上' if tip.z > hand_pos.z else '下'}")
else:
    p("    !! 没有 Hand.R")

# ---- 天空 ----
p("\n[6] Blender 物理天空")
world = bpy.context.scene.world or bpy.data.worlds.new("MCWorld")
bpy.context.scene.world = world
world.use_nodes = True
nt = world.node_tree
for n in list(nt.nodes):
    nt.nodes.remove(n)
out = nt.nodes.new("ShaderNodeOutputWorld")
bg = nt.nodes.new("ShaderNodeBackground")
kind = None
for k in ('MULTIPLE_SCATTERING', 'NISHITA', 'HOSEK_WILKIE', 'PREETHAM'):
    try:
        sky = nt.nodes.new("ShaderNodeTexSky")
        sky.sky_type = k
        sky.sun_elevation = math.radians(45)
        sky.sun_rotation = math.radians(215) - math.pi / 2
        sky.sun_disc = True
        for attr, val in (("altitude", 120.0), ("air_density", 1.0),
                          ("dust_density", 0.5), ("ozone_density", 1.0)):
            try:
                setattr(sky, attr, val)
            except Exception:
                pass
        nt.links.new(sky.outputs['Color'], bg.inputs['Color'])
        kind = k
        break
    except Exception:
        try:
            nt.nodes.remove(sky)
        except Exception:
            pass
if kind is None:
    bg.inputs['Color'].default_value = (0.35, 0.58, 0.95, 1)
bg.inputs['Strength'].default_value = SKY
nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
sd = bpy.data.lights.new("SunKey", type='SUN')
sd.energy = 3.5
sd.angle = math.radians(2.5)
so = bpy.data.objects.new("SunKey", sd)
bpy.context.scene.collection.objects.link(so)
so.rotation_euler = (math.radians(90 - 45), 0, math.radians(215 + 90))
p(f"    {kind or '平涂蓝'}  太阳仰角 45° 方位 215°  强度 3.5  天空强度 {SKY}")
# ⚠️ 坑 53/57：视图转换与天空强度必须配套 ——
#   「标准」没有高光压缩，物理天空（辐射度量）哪怕强度 0.12 也会削顶成白，
#   整个动态范围被天空占满，三点布光完全显不出来（= "背景死白 + 没有光源"）。
#   T30 说的"切标准"是【调色时】为了看见颜色变化，不是最终渲染该用的。
#   → 最终渲染用 Filmic / AgX，天空强度可以放到 0.4~0.8。
p(f"    视图转换 = {VT}（给高光留压缩余量，布光才显形）")

# ---- T48 三点布光 ----
p("\n[7] T48 三点布光")
for o in list(bpy.data.objects):
    if o.type == 'LIGHT' and o.name.split('.')[0] in ('Key', 'Fill', 'Rim'):
        bpy.data.objects.remove(o, do_unlink=True)


def area(name, loc, rot, energy, size):
    ld = bpy.data.lights.new(name, type='AREA')
    ld.energy = energy
    ld.shape = 'SQUARE'
    ld.size = size
    ob = bpy.data.objects.new(name, ld)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = rot
    return ob


area("Key", (WX + 3.5, WY - 3.8, GROUND + 5.0),
     (math.radians(50), 0, math.radians(42)), 320.0, 3.0)
area("Fill", (WX - 4.2, WY - 2.6, GROUND + 2.8),
     (math.radians(74), 0, math.radians(-58)), 140.0, 5.5)
area("Rim", (WX - 0.5, WY + 3.8, GROUND + 3.6),
     (math.radians(80), 0, math.radians(188)), 420.0, 3.0)
p("    Key 320 / Fill 140 / Rim 420（T48 的 100:50:110 比例，"
  "整体抬高以在有压缩的视图转换下生效）")

# ---- 相机 ----
p("\n[8] 相机")
cam_d = bpy.data.cameras.new("Cam")
cam_d.lens = LENS
cam = bpy.data.objects.new("Cam", cam_d)
bpy.context.scene.collection.objects.link(cam)
bpy.context.scene.camera = cam
# 相机相对【世界位置】摆放。视线方位角用 MC 角度给（和地表扫描同一套坐标），
# 免得我自己换算错：MC(x,z) → Blender(x,-z)，所以方向 (mx,mz) → (mx,-mz)。
_az = math.radians(CAM_AZ)
_mc = (math.cos(_az), math.sin(_az))
fw = mathutils.Vector((_mc[0], -_mc[1], 0.0)).normalized()   # 视线水平方向
_right = mathutils.Vector((fw.y, -fw.x, 0.0)).normalized()   # 视线右方
cam.location = (mathutils.Vector((WX, WY, GROUND))
                - fw * DIST
                + mathutils.Vector((0.0, 0.0, 2.45)))
# 看向点往视线右方偏 → 角色落在画面左侧，右侧留白给文字
look = (mathutils.Vector((WX, WY, GROUND + 1.05)) + _right * 0.9)
cam.rotation_euler = (look - cam.location).to_track_quat('-Z', 'Y').to_euler()
p(f"    {LENS:.0f}mm  视线方位 {CAM_AZ:.0f}°(MC)  距离 {DIST}")
p(f"    位置 {tuple(round(v,2) for v in cam.location)}  "
  f"看向 {tuple(round(v,2) for v in look)}")

# 视线检测（坑 41：先隐藏角色与剑）
bpy.context.view_layer.update()
stash = []
for o in list(char) + list(sw):
    try:
        stash.append((o, o.hide_viewport))
        o.hide_viewport = True
    except Exception:
        pass
bpy.context.view_layer.update()
dg = bpy.context.evaluated_depsgraph_get()
d = (cam.location - look).normalized()
hit, loc, nrm, idx, hobj, hm = bpy.context.scene.ray_cast(
    dg, look, d, distance=(cam.location - look).length + 0.5)
for o, hv in stash:
    try:
        o.hide_viewport = hv
    except Exception:
        pass
p(f"    射线检测: {'命中 ' + str(hobj.name) if hit else '视线通畅'}")

# ---- 渲染设置 ----
p("\n[9] 渲染设置")
scn = bpy.context.scene
for cand in ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE'):
    try:
        scn.render.engine = cand
        break
    except Exception:
        continue
try:
    scn.eevee.taa_render_samples = 256
except Exception:
    pass
scn.render.resolution_x, scn.render.resolution_y = 1280, 720
scn.render.film_transparent = False
vt = None
for v in (VT, 'Filmic', 'AgX', 'Standard'):
    try:
        scn.view_settings.view_transform = v
        vt = v
        break
    except Exception:
        continue
scn.view_settings.exposure = 0.0
scn.view_settings.look = 'None'
scn.render.filepath = os.path.join(OUT, "封面_世界")
scn.render.image_settings.file_format = 'PNG'
p(f"    {scn.render.engine} / 256 采样 / 视图转换={vt} / 1280x720")

blend = os.path.join(OUT, "封面_世界.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
p(f"\n工程: {blend}")
finish(0)
