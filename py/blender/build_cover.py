# -*- coding: utf-8 -*-
# 运行方式（由 pipeline.step_render 调用，输出目录必须是绝对路径）：
#   blender -b --factory-startup --python build_cover.py -- <世界.obj> <输出目录> \
#        <yaw> <lens> <view_transform> <sky> <cam_az> <dist> <sink>
# 可选环境变量：DSH_RES_X/Y、DSH_SAMPLES、DSH_SKIN、DSH_SUN_*、DSH_ARM_*、DSH_SWORD_SINK、DSH_MODEL1_*
"""封面_世界 —— 在真实存档的草原上渲染 Steve 持钻石剑

用到的课程结论（B站 Woodplank 课）：
  T55   BPS V4.0.9（非 BETA1）+ 以【集合】方式追加
  T11   mcprep.prep_materials 管材质
  实测  MCprep 自己做群系染色（所以导出器不再烘染色）；
        MCprep 挂掉时这里有一套内置兜底（贴图 + 群系乘色 + 调平材质）
  T30   视图转换用「标准」
  T48   60-80mm、角色偏左留白、三点布光(100/50-70/100)

钻石剑挂载（坑 51 / 55 / 71 / 72）：
  剑的【网格本地】包围盒 X[-0.7,1.3] Y[-0.7,1.3] Z[-0.02,0.1]，
  刀刃是【本地对角方向】——柄在 (-0.7,-0.7)，尖在 (1.3,1.3)；
  对象自带 rotation_euler=(1.5771,0,0)（绕 X 90°），直接写 matrix_world 会覆盖它。
  挂法三步：① 用 FK 摆右臂（Top/Bottom Arm.R + Hand.R）；
            ② 抓握点取摆完姿势后的 Hand.R 骨中点（不能用网格 bbox）；
            ③ 剑沿刃轴下沉 DSH_SWORD_SINK，让手掌包住一段柄 —— 握持不是"托着"。
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
BPS = os.environ.get("DSH_BPS_RIG") or os.path.join(
    PACK, "BPS_Rig_v4", "BPS Character Rig V4.0.9.blend")
ITEM = os.environ.get("DSH_ITEM_RIG") or os.path.join(
    PACK, "Cycles_Minecraft_Rig_BSS_Edit_V6_5",
    "Cycles_Minecraft_Rig BSS Edit V6.5", "Minecraft_Item_Rig.blend")

# ★ 用户皮肤：设了 DSH_SKIN 就把它贴到角色身上，否则保持原版 Steve
SKIN = os.environ.get("DSH_SKIN", "").strip()
# ★ 输出分辨率：竖版封面用 1080x1920 之类
RES_X = int(os.environ.get("DSH_RES_X", "1280"))
RES_Y = int(os.environ.get("DSH_RES_Y", "720"))
SAMPLES = int(os.environ.get("DSH_SAMPLES", "256"))   # EEVEE 采样数

# ★ 光照/氛围（工作区副本新增）——做"黄昏逆光"这类氛围片用
SUN_ELEV = float(os.environ.get("DSH_SUN_ELEV", "45"))      # 太阳仰角（度）8=黄昏贴地
SUN_ROT = float(os.environ.get("DSH_SUN_ROT", "215"))       # 太阳方位（度）
SUN_INT = float(os.environ.get("DSH_SUN_INT", "3.5"))       # 太阳强度
KEY_SCALE = float(os.environ.get("DSH_KEY_SCALE", "1.0"))   # 三点布光整体倍率
CAM_H = float(os.environ.get("DSH_CAM_H", "2.45"))          # 相机离地（格）
AIM_H = float(os.environ.get("DSH_AIM_H", "1.05"))          # 看向高度（格）
AIM_SIDE = float(os.environ.get("DSH_AIM_SIDE", "0.9"))     # 看向点横移（留白量）
EXPOSURE = float(os.environ.get("DSH_EXPOSURE", "0.0"))     # 曝光补偿
LENS_OVR = float(os.environ.get("DSH_LENS", "0"))           # 焦距覆盖（0=用默认）

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
prep_ok = False
try:
    r = bpy.ops.mcprep.prep_materials(autoFindMissingTextures=True,
                                      usePrincipledShader=True, useEmission=True)
    prep_ok = 'FINISHED' in str(r) or 'RUNNING_MODAL' in str(r)
    p(f"    返回 {r}")
except Exception as e:
    p(f"    FAIL {type(e).__name__}: {str(e)[:140]}")


# ---- 材质兜底：MCprep 没跑通时，自己做最基础的群系染色 ----
#   ⚠️ 为什么需要：MC 的 grass_block_top / 树叶贴图本身是【灰的】，
#   颜色在游戏里由群系 tint 决定。MCprep 挂了（实测 addon 注册时写
#   tracker.json 抛 PermissionError → 它的 operator 一个都没注册上），
#   草地就会渲染成一片灰白 —— 这正是"世界发白"的根因。
#   这里补一个最小的乘色，够出封面用；MCprep 正常时不会触发。
BIOME_TINT = {
    'grass_block_top': (0.36, 0.72, 0.16),      # 平原草地（实测对齐：偏纯绿）
    'grass_block_side': None,                    # 侧面带泥土，不整体染
    'water_still': (0.20, 0.42, 0.85),
    'water_flow': (0.20, 0.42, 0.85),
}
_LEAF_TINT = (0.28, 0.62, 0.16)


def _tint_for(name):
    base = name.split('.')[0].lower()
    if base in BIOME_TINT:
        return BIOME_TINT[base]
    if 'leaves' in base or 'vine' in base or 'fern' in base:
        return _LEAF_TINT
    if base in ('grass', 'short_grass', 'tall_grass', 'grass_block'):
        return BIOME_TINT['grass_block_top']
    return None


def _mix_rgb(nt):
    """建一个 Multiply 混色节点（兼容 4.x/5.x 的两种 Mix 节点）。"""
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
    """MCprep 不可用时的最小替代：贴图接上 + 群系染色 + 调平材质。

    对齐 MCprep 出来的观感（实测它的成品材质是 Roughness 0.7 / Specular 0）：
    不调这两项的话，草地会反出一层白光，看着"发白、没细节"。
    """
    tinted = 0
    tuned = 0
    for m in bpy.data.materials:
        if not (m.use_nodes and m.node_tree):
            continue
        nt = m.node_tree
        tex = next((x for x in nt.nodes if x.type == 'TEX_IMAGE' and x.image), None)
        bsdf = next((x for x in nt.nodes if x.type == 'BSDF_PRINCIPLED'), None)
        if not tex or not bsdf:
            continue
        # 1) 材质参数：去掉高光，粗糙度对齐 MCprep
        for key, val in (('Roughness', 0.72), ('Specular IOR Level', 0.0),
                         ('Specular', 0.0)):
            sock = bsdf.inputs.get(key)
            if sock is not None and not sock.is_linked:
                try:
                    sock.default_value = val
                    tuned += 1
                except Exception:
                    pass
        try:
            tex.interpolation = 'Closest'
        except Exception:
            pass
        # 2) 灰度贴图乘群系色
        tint = _tint_for(m.name)
        if not tint:
            continue
        if any(x.type in ('MIX', 'MIX_RGB') for x in nt.nodes):
            continue                        # 已经染过
        in_sock = bsdf.inputs.get('Base Color')
        if not in_sock:
            continue
        mix, c1, c2, out = _mix_rgb(nt)
        c2.default_value = (tint[0], tint[1], tint[2], 1.0)
        for lk in list(in_sock.links):
            nt.links.remove(lk)
        nt.links.new(tex.outputs['Color'], c1)
        nt.links.new(out, in_sock)
        if tex.image.depth == 32 or 'leaves' in m.name.lower():
            try:
                m.blend_method = 'CLIP'
            except Exception:
                pass
        tinted += 1
    return tinted, tuned


if not prep_ok:
    p("    ⚠️ MCprep 没跑通 → 用内置兜底：贴图 + 群系染色 + 调平材质")
    _tinted, _tuned = fallback_prep_materials()
    p(f"    染色 {_tinted} 个材质，调平 {_tuned} 处参数")
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

# ============================================================
# ★ 换皮肤（工作区副本新增）
#   原理：BPS 的 Skin 材质只是一张 64x64 的 Steve.png，
#         而所有网格的 UV 就是【标准 MC 皮肤布局】——
#         所以把图像数据换成用户皮肤即可，UV 一个都不用动。
# ============================================================
if SKIN:
    p(f"\n[4.5] 换皮肤 → {SKIN}")
    if not os.path.exists(SKIN):
        p(f"    !! 皮肤文件不存在，保持 Steve：{SKIN}")
    else:
        try:
            tgt = None
            for m in bpy.data.materials:
                if m.name.split('.')[0] != "Skin" or not m.use_nodes:
                    continue
                for n in m.node_tree.nodes:
                    if n.type == 'TEX_IMAGE' and n.image:
                        tgt = n
                        break
                if tgt:
                    break
            if not tgt:
                p("    !! 没找到挂图像的 Skin 材质节点，保持 Steve")
            else:
                old_name = tgt.image.name
                old_size = tuple(tgt.image.size)
                tmp = bpy.data.images.load(SKIN)
                w, h = tmp.size
                px = list(tmp.pixels)
                newimg = bpy.data.images.new("用户皮肤", width=w, height=h,
                                             alpha=True)
                newimg.pixels = px
                newimg.pack()
                tgt.image = newimg
                try:
                    bpy.data.images.remove(tmp)
                except Exception:
                    pass
                if old_name in bpy.data.images:
                    try:
                        bpy.data.images.remove(bpy.data.images[old_name])
                    except Exception:
                        pass
                p(f"    皮肤已替换: {old_name} {old_size} → 用户皮肤 {w}x{h}")
        except Exception as e:
            p(f"    !! 换皮肤失败({type(e).__name__}: {e})，保持 Steve")

# ============================================================
# ★ 隐藏替身眼睛（工作区副本新增）
#   理由：用户皮肤的脸（眼睛/眉毛）已经画在头部贴图上。
#   BPS 自带 mesh eye.l/.r、mesh eyebrow.l/.r、mesh pupils.l/.r，
#   它们是按【Steve 的眼位】摆放的独立几何 —— 不关掉会出现两套眼睛。
#   只关'eye'与'pupil'，眉毛(eyebrow)保留（它们是可动附件，位置正常）。
# ============================================================
if SKIN:
    hid = []
    for o in bpy.data.objects:
        if o.type != 'MESH':
            continue
        nm = o.name.replace('BPS v4 - ', '').lower()
        if nm.startswith('mesh eye') or nm.startswith('mesh pupil'):
            o.hide_render = True
            hid.append(o.name.replace('BPS v4 - ', ''))
    body = [o for o in body if not o.hide_render]
    p(f"    已隐藏替身眼部 {len(hid)} 个: {hid}")

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

# ============================================================
# ★ 右臂握剑姿势（0.2.0 新增）
#
#   参考文献（本工作区实测 + 课程笔记）：
#     ① 课程笔记 4.4：BPS V4 的手臂**默认是 FK**（IK 约束影响 = 0），
#        摆姿势直接转 Top Arm.R / Bottom Arm.R / Hand.R 三根 pose bone；
#        别去碰 Arm IK.R —— 那是切到 IK 才启用的控制器。
#     ② 课程笔记 T11（Dynamic Parent）："剑握在手里 = 把剑绑到角色手上"，
#        这里用 bone parent 到 Hand.R 等价实现（不用装插件）。
#
#   ⚠️ 坑 71：之前只做了②，没做① —— 手臂垂在胯边，1.4 格长的剑
#   只能横在胸前，看起来就是"没握住"。摆完手臂再挂剑才对。
# ============================================================
def _rot3(s, default):
    if not s:
        return default
    try:
        v = tuple(float(x) for x in s.split(','))
        return v if len(v) == 3 else default
    except Exception:
        return default


ARM_POSE = os.environ.get('DSH_ARM_POSE', '1').lower() not in ('0', 'off', 'false')
SHOULDER_ROT = _rot3(os.environ.get('DSH_ARM_SHOULDER'), (-40.0, 0.0, 42.0))
ELBOW_ROT = _rot3(os.environ.get('DSH_ARM_ELBOW'), (-85.0, 0.0, 0.0))
WRIST_ROT = _rot3(os.environ.get('DSH_ARM_WRIST'), (0.0, 0.0, 6.0))


def pose_right_arm():
    """把右臂摆成举剑姿势（FK，三根 pose bone）。"""
    if not arm:
        return False
    names = ('Top Arm.R', 'Bottom Arm.R', 'Hand.R')
    if not all(n in arm.pose.bones for n in names):
        p("    !! 找不到 Top/Bottom Arm.R 或 Hand.R，跳过摆姿势")
        return False
    if not ARM_POSE:
        p("    (DSH_ARM_POSE=0，保持默认站姿)")
        return False
    for name, rot in zip(names, (SHOULDER_ROT, ELBOW_ROT, WRIST_ROT)):
        pb = arm.pose.bones[name]
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = tuple(math.radians(v) for v in rot)
    bpy.context.view_layer.update()
    p(f"    右臂：肩{SHOULDER_ROT} 肘{ELBOW_ROT} 腕{WRIST_ROT}（FK）")
    return True


p("\n[4.8] 摆右臂（FK，举剑）")
pose_right_arm()
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

    # 剑的本地几何：柄尾在 (-0.7,-0.7)，刀刃沿对角 (1,1,0) 到 (1.3,1.3)
    POMMEL = mathutils.Vector((-0.70, -0.70, 0.04))
    BLADE_DIR = mathutils.Vector((1.0, 1.0, 0.0)).normalized()
    K = 0.5                                   # 网格对角 2.83 → 成品约 1.4

    # ★ 坑 72：握持 ≠ 托着。
    #   把【柄尾】对准掌心，整把剑都在拳头上方 → 看着像用手托着。
    #   正确做法是沿刃方向把剑往下沉 SINK，让拳头包住一段柄：
    #   柄尾从拳下露出、刀刃从拳上伸出，手和剑真正"咬合"。
    #   0.55 本地单位 ≈ 0.27 方块，正好是 MC 手臂方块的一半高度。
    SINK = float(os.environ.get('DSH_SWORD_SINK', '0.55'))
    anchor = POMMEL + BLADE_DIR * SINK

    # 目标世界朝向：刀刃朝【上 + 略偏外 + 略朝镜头】
    # ⚠️ 坑 56（两次）：① 太偏 -Y 会从正面盖住脸；
    #   ② 偏 +X 不够时，剑尖仍停在头部范围内。
    # 现在手臂已经抬起来（[4.8]），手在肩高、离头 0.4 格左右，
    # 只要保持"竖直 + 一点点外倾"就不会糊脸。
    blade_w = mathutils.Vector((0.42, -0.22, 1.0))
    normal_w = mathutils.Vector((0.12, -0.99, 0.0))
    R = basis_from(blade_w, normal_w).to_4x4()

    # ★ 坑 55 + 坑 71 的修正：
    #   抓握点必须用【摆好姿势之后】的 Hand.R 骨（pose bone），
    #   不能用网格 bbox —— bbox 不含骨架形变，手抬起来它还停在原位。
    #   取手骨中点（拳头几何中心），再让柄沿刃向沉 SINK 进去。
    pb = arm.pose.bones['Hand.R']
    head_w = arm.matrix_world @ pb.head
    tail_w = arm.matrix_world @ pb.tail
    grip = (head_w + tail_w) / 2.0
    p(f"    Hand.R 头 {tuple(round(v,2) for v in head_w)} "
      f"尾 {tuple(round(v,2) for v in tail_w)}")
    p(f"    抓握点（手骨中点） {tuple(round(v,2) for v in grip)}  "
      f"剑柄下沉 {SINK}（本地）≈ {SINK * K:.2f} 方块")
    hand_pos = grip

    M = (mathutils.Matrix.Translation(hand_pos)
         @ R
         @ mathutils.Matrix.Scale(K, 4)
         @ mathutils.Matrix.Translation(-anchor))
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

# ============================================================
# ★ 自带模型（BYO models）—— 插件不附带任何模型 / 贴图
#
#   模型由使用者自备。推荐流程：
#     python py/cli.py extract <资源目录> --out <输出目录>
#   把 Bedrock 几何（.geo.json + 贴图）批量转成 OBJ，再指进来：
#     ① DSH_MODELS=/path/models.json     数量不限，字段见 py/model_spec.py
#     ② DSH_MODEL1_OBJ / DSH_MODEL2_OBJ / DSH_MODEL3_OBJ   三个槽位
#   旧前缀（v0.1.x）仍然兼容，日志里会提示迁移，下个主版本移除。
# ============================================================
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from model_spec import collect as collect_models    # noqa: E402


def import_model(idx, cfg):
    """把一个 OBJ 模型放进场景：先摆朝向和缩放，最后按包围盒落地。"""
    obj_path = cfg["obj"]
    name = cfg["name"]
    zoff = cfg["z"]
    if not obj_path or not os.path.exists(obj_path):
        p(f"    [模型 {idx}] !! 找不到 {obj_path or '(空)'}")
        return None
    p(f"    [模型 {idx}] {name} 加载中…")
    op = (getattr(bpy.ops.wm, "obj_import", None)
          or getattr(bpy.ops.import_scene, "obj", None))
    op(filepath=obj_path)
    got = [o for o in bpy.context.selected_objects if o.type == 'MESH']
    if not got:
        p(f"    [模型 {idx}] !! OBJ 导入后没有网格")
        return None
    bpy.ops.object.select_all(action='DESELECT')
    for o in got:
        o.select_set(True)
    bpy.context.view_layer.objects.active = got[0]
    if len(got) > 1:
        bpy.ops.object.join()
    mo = bpy.context.view_layer.objects.active

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    try:
        bpy.ops.mesh.normals_make_consistent(inside=False)
    except Exception:
        pass
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.shade_flat()

    # ★ 关键：OBJ 导入器会带一个 Y-up→Z-up 的旋转。
    #   必须先把它烘焙进网格，否则下面设 rotation_euler 会把它覆盖掉 →
    #   模型躺倒、落地高度全错（实测底 z 比地面低 1.65 格）。
    bpy.ops.object.select_all(action='DESELECT')
    mo.select_set(True)
    bpy.context.view_layer.objects.active = mo
    try:
        bpy.ops.object.transform_apply(location=False, rotation=True, scale=True)
    except Exception as e:
        p(f"    !! transform_apply 失败: {e}")

    tex = cfg.get("tex") or ""
    if tex and os.path.exists(tex):
        img = bpy.data.images.load(tex)
        mat = bpy.data.materials.new(f"Model{idx}")
        mat.use_nodes = True
        nt_ = mat.node_tree
        for n in list(nt_.nodes):
            nt_.nodes.remove(n)
        o_ = nt_.nodes.new("ShaderNodeOutputMaterial")
        b_ = nt_.nodes.new("ShaderNodeBsdfPrincipled")
        t_ = nt_.nodes.new("ShaderNodeTexImage")
        t_.image = img
        t_.interpolation = 'Closest'
        nt_.links.new(t_.outputs['Color'], b_.inputs['Base Color'])
        b_.inputs['Roughness'].default_value = 0.78
        try:
            nt_.links.new(t_.outputs['Alpha'], b_.inputs['Alpha'])
            mat.blend_method = 'CLIP'
        except Exception:
            pass
        nt_.links.new(b_.outputs['BSDF'], o_.inputs['Surface'])
        mo.data.materials.clear()
        mo.data.materials.append(mat)

    # ★ 顺序很重要：先设【朝向】和【缩放】，最后才按包围盒算落地位移。
    #   反过来做的话，转完朝向包围盒会变，落地就算错了。
    mo.rotation_euler = (0, 0, math.radians(cfg["yaw"]))
    mo.scale = (cfg["scale"], cfg["scale"], cfg["scale"])
    bpy.context.view_layer.update()
    bb = [mo.matrix_world @ mathutils.Vector(c) for c in mo.bound_box]
    bx = [v.x for v in bb]
    by = [v.y for v in bb]
    bz = [v.z for v in bb]
    p(f"    [模型 {idx}] {name} 尺寸 {(max(bx)-min(bx)):.2f} x "
      f"{(max(by)-min(by)):.2f} x {(max(bz)-min(bz)):.2f} 方块")

    # ⚠️ fw/_right 在后面的相机段才定义，所以这里自己按 CAM_AZ 算一份
    _az_ = math.radians(CAM_AZ)
    _fwd = mathutils.Vector((math.cos(_az_), -math.sin(_az_), 0.0)).normalized()
    _rgt = mathutils.Vector((_fwd.y, -_fwd.x, 0.0)).normalized()

    tgt = (mathutils.Vector((WX, WY, GROUND + zoff))
           + _fwd * cfg["back"] + _rgt * cfg["side"])
    dx_ = tgt.x - (min(bx) + max(bx)) / 2
    dy_ = tgt.y - (min(by) + max(by)) / 2
    dz_ = tgt.z - min(bz)
    mo.location = (mo.location.x + dx_, mo.location.y + dy_,
                   mo.location.z + dz_)
    bpy.context.view_layer.update()
    bb2 = [mo.matrix_world @ mathutils.Vector(c) for c in mo.bound_box]
    bz2 = [v.z for v in bb2]
    ok_ground = abs(min(bz2) - (GROUND + zoff)) < 0.05
    p(f"    [模型 {idx}] {name} 落位 ({tgt.x:.1f},{tgt.y:.1f}) "
      f"底z={min(bz2):.2f}(目标{GROUND + zoff:.2f})"
      f"{'✓' if ok_ground else '✗'} 高{max(bz2)-min(bz2):.2f}格 "
      f"缩放{cfg['scale']} 背后{cfg['back']} 侧偏{cfg['side']}")
    return mo


bpy.context.view_layer.update()
_models, _model_notes = collect_models()
for _n in _model_notes:
    p(f"    ⚠️  {_n}")
if _models:
    p(f"\n[5.5] 自带模型 {len(_models)} 个")
for _i, _cfg in enumerate(_models, 1):
    import_model(_i, _cfg)

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
        sky.sun_elevation = math.radians(SUN_ELEV)
        sky.sun_rotation = math.radians(SUN_ROT) - math.pi / 2
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
sd.energy = SUN_INT
sd.angle = math.radians(2.5)
so = bpy.data.objects.new("SunKey", sd)
bpy.context.scene.collection.objects.link(so)
so.rotation_euler = (math.radians(90 - SUN_ELEV), 0, math.radians(SUN_ROT + 90))
p(f"    {kind or '平涂蓝'}  太阳仰角 {SUN_ELEV:.0f}° 方位 {SUN_ROT:.0f}°  "
  f"强度 {SUN_INT}  天空强度 {SKY}")
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
     (math.radians(50), 0, math.radians(42)), 320.0 * KEY_SCALE, 3.0)
area("Fill", (WX - 4.2, WY - 2.6, GROUND + 2.8),
     (math.radians(74), 0, math.radians(-58)), 140.0 * KEY_SCALE, 5.5)
area("Rim", (WX - 0.5, WY + 3.8, GROUND + 3.6),
     (math.radians(80), 0, math.radians(188)), 420.0 * KEY_SCALE, 3.0)
p(f"    Key {320*KEY_SCALE:.0f} / Fill {140*KEY_SCALE:.0f} / "
  f"Rim {420*KEY_SCALE:.0f}（倍率 {KEY_SCALE}，黄昏逆光时调小）")

# ---- 相机 ----
p("\n[8] 相机")
cam_d = bpy.data.cameras.new("Cam")
cam_d.lens = (LENS_OVR if LENS_OVR > 0 else LENS)
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
                + mathutils.Vector((0.0, 0.0, CAM_H)))
# 看向点往视线右方偏 → 角色落在画面左侧，右侧留白给文字
look = (mathutils.Vector((WX, WY, GROUND + AIM_H)) + _right * AIM_SIDE)
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
    scn.eevee.taa_render_samples = SAMPLES
except Exception:
    pass
scn.render.resolution_x, scn.render.resolution_y = RES_X, RES_Y
scn.render.film_transparent = False
vt = None
for v in (VT, 'Filmic', 'AgX', 'Standard'):
    try:
        scn.view_settings.view_transform = v
        vt = v
        break
    except Exception:
        continue
scn.view_settings.exposure = EXPOSURE
scn.view_settings.look = 'None'
scn.render.filepath = os.path.join(OUT, "封面_世界")
scn.render.image_settings.file_format = 'PNG'
p(f"    {scn.render.engine} / {SAMPLES} 采样 / 视图转换={vt} / {RES_X}x{RES_Y}")

blend = os.path.join(OUT, "封面_世界.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
p(f"\n工程: {blend}")
finish(0)
