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
import json
import math
import os
import sys

import addon_utils
import bpy
import mathutils

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _compat import (set_engine, set_sky_type, set_view_transform,   # noqa: E402
                     eval_bbox, is_driven_body)

# ============================================================
# ★ 资产定位（v0.5.0）
#   ⚠️ 老版本这里写死开发者路径 `PACK = r"E:\...\pack"`，
#      换台机器 / 换个工作区就变成「⚠️ 没装 BPS 角色骨架」「⚠️ 没装物品库」。
#      而且 pipeline.resolve_assets() 明明算出了 rig/item 路径，
#      却从没塞进环境变量 —— 两头都没人负责，这是用户抱怨的根因之一。
#   现在的优先级：显式环境变量 > vendor 目录 > 明确报错（不再静默硬编码）。
# ============================================================
def _env_path(name):
    v = os.environ.get(name, '').strip().strip('"').strip("'")
    return v or None


def _first_blend(path):
    """把「.blend 文件」或「含 .blend 的目录」统一解析成 .blend 文件路径。

    ⚠️ vendor/item/ 下面是一个【目录】，resolve_assets() 早先直接把目录当 item
       返回，于是 bpy.data.libraries.load(目录) 必然失败 —— 角色只能空手。
    """
    if not path:
        return None
    if os.path.isfile(path):
        return path if path.lower().endswith('.blend') else None
    if os.path.isdir(path):
        cands = []
        for root, _dirs, files in os.walk(path):
            for f in files:
                if f.lower().endswith('.blend'):
                    cands.append(os.path.join(root, f))
        if cands:
            cands.sort(key=lambda x: (0 if 'item' in os.path.basename(x).lower() else 1, x))
            return cands[0]
    return None


def _dir_with_blend(parent, want_item):
    if not parent or not os.path.isdir(parent):
        return None
    return parent


def _vendor_dir():
    for c in (_env_path('DSH_MC_VENDOR'),
              os.path.join(os.path.expanduser('~'), '.dsh', 'blender-mc', 'vendor'),
              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           'vendor')):
        if c and os.path.isdir(c):
            return c
    return None


VENDOR = _vendor_dir()
BPS = (_first_blend(_env_path('DSH_BPS_RIG'))
       or (_first_blend(os.path.join(VENDOR, 'rig')) if VENDOR else None))
ITEM = (_first_blend(_env_path('DSH_ITEM_RIG'))
        or (_first_blend(os.path.join(VENDOR, 'item')) if VENDOR else None))
# 贴图库（「原版方块」物品和身后的树都要用它）
TEXDIR = _env_path('DSH_MC_TEXTURES') or (os.path.join(VENDOR, 'textures') if VENDOR else '')

# ★ 用户皮肤：设了 DSH_SKIN 就把它贴到角色身上，否则保持原版 Steve
SKIN = os.environ.get("DSH_SKIN", "").strip()
# ★ 输出分辨率：竖版封面用 1080x1920 之类
RES_X = int(os.environ.get("DSH_RES_X", "1280"))
RES_Y = int(os.environ.get("DSH_RES_Y", "720"))
SAMPLES = int(os.environ.get("DSH_SAMPLES", "256"))   # EEVEE 采样数

# ★ 光照/氛围（工作区副本新增）——做"黄昏逆光"这类氛围片用
#   数值口径见 _scratch/render-opt/优化方案.md §6「参数总表」（AgX+Punchy 下标定）
SUN_ELEV = float(os.environ.get("DSH_SUN_ELEV", "14"))      # 太阳仰角（度）14=上午斜射；8=黄昏贴地会把阴影压没
# ⚠️ 太阳方位【固定世界 340°】而不是跟 CAM_AZ 绑定（优化方案 P0-2）：
#   跟机位绑会让"同一个存档换个机位就换了个时段"；解耦后绕场才会自然出现
#   顺光/侧光/逆光的叙事变化。仍可用 DSH_SUN_ROT 覆盖。
SUN_ROT = float(os.environ.get("DSH_SUN_ROT", "340"))       # 太阳方位（度）
SUN_INT = float(os.environ.get("DSH_SUN_INT", "6.0"))       # 太阳灯强度
SUN_COLOR = (1.0, 0.70, 0.42)                               # 黄昏暖色
SUN_ANGLE = float(os.environ.get("DSH_SUN_ANGLE", "1.6"))   # 太阳盘角（度）
KEY_SCALE = float(os.environ.get("DSH_KEY_SCALE", "1.0"))   # 三点布光整体倍率
# ★ Rim 单独倍率（优化方案 P0-4 第 6 条）：逆光封面里轮廓光常要拉到 Key 的 3 倍。
RIM_SCALE = float(os.environ.get("DSH_RIM_SCALE", str(KEY_SCALE)))
CAM_H = float(os.environ.get("DSH_CAM_H", "1.55"))          # 相机离地（格）略高于人眼，能看见地表层次而不是一堵土墙
AIM_H = float(os.environ.get("DSH_AIM_H", "1.00"))          # 看向高度（格）
AIM_SIDE = float(os.environ.get("DSH_AIM_SIDE", "0.58"))    # 看向点横移（留白量）
EXPOSURE = float(os.environ.get("DSH_EXPOSURE", "0.05"))    # 曝光补偿（AgX 下 +0.05）
# ★ look：AgX 下用 'AgX - Punchy' 把被压掉的饱和度找回来；失败降级 'None'
LOOK = os.environ.get("DSH_LOOK", "AgX - Punchy").strip() or "None"
LENS_OVR = float(os.environ.get("DSH_LENS", "0"))           # 焦距覆盖（0=用默认）
# ★ 雾：默认开；任何一步异常整体回退成"无雾"，绝不让渲染失败
FOG_ON = os.environ.get("DSH_MC_FOG", "1").lower() not in ("0", "off", "false", "no")

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OBJ = argv[0]
OUT = argv[1]
YAW = float(argv[2]) if len(argv) > 2 else 25.0       # 角色朝向（度）
LENS = float(argv[3]) if len(argv) > 3 else 105.0
VT = argv[4] if len(argv) > 4 else 'AgX'              # 视图转换
SKY = float(argv[5]) if len(argv) > 5 else 0.12        # 天空强度（0.20 会削顶成白板）
CAM_AZ = float(argv[6]) if len(argv) > 6 else 210.0    # 视线方位角（MC 角度）
DIST = float(argv[7]) if len(argv) > 7 else 15.0        # 相机距离（105mm 配 13.5 格 ≈ 画高 72%）
# 自动改方位后角色转了多少度 —— BYO 自带模型也要跟着转，否则和角色不一体。
_YAW_DELTA = 0.0
# ★ 落地时让脚【略微陷入】地面，而不是精确贴面。
#   实测：精确贴面时间隙 0.019（数值上正确），但 MC 方块边缘锐利，
#   这点缝在画面里会读成"悬浮"。往下沉 0.06 让鞋底真正埋进草方块顶面。
SINK = float(argv[8]) if len(argv) > 8 else 0.06
# ★ 景深光圈：方案写 f/2.2，但那是"看起来浅"的经验值。
#   算过账：50mm、主体 8.2m、f/2.2 时，无限远的弥散圆只有
#       f²/(N·s) = 2500/(2.2×8200) ≈ 0.14mm ≈ 36mm 画幅的 0.39%
#   → 1280 宽只有 ~5px，背景草地（30~150m）几乎不糊，审图永远 0 分。
#   要真的拉开层次得开到 f/1.2~1.4（≈8px），这里默认 1.4，可用 DSH_FSTOP 覆盖。
FSTOP = float(os.environ.get("DSH_FSTOP", "1.4"))
# ▲ 视线方位自动选择：scan 只保证「周围是平地」，不管朝哪看。
#   朝山崖看 = 满屏土墙、一丝天空都没有（实测旧图天空占比 0%）。
AUTO_AZ = os.environ.get("DSH_MC_AUTO_AZ", "1") not in ("0", "false", "False", "no")

# ============================================================
# ★ 时段 DSH_HOUR（v0.5.0）
#   用户原话：「搞成早晨或者午间啥的」「把这个改成白天」——
#   以前只能填 DSH_SUN_ELEV 仰角，没人知道「早上 8 点」该填几度。
#   现在直接给 0-24 的小时数（或 morning/noon/dusk/night），
#   太阳仰角 / 色温 / 强度 / 天空强度 全按时刻推。
#   ⚠️ 显式设了旧变量 DSH_SUN_ELEV / DSH_SUN_INT / DSH_SUN_COLOR 就尊重旧变量。
# ============================================================
_HOUR_PRESETS = {'morning': 8.0, 'noon': 12.0, 'afternoon': 15.0,
                 'dusk': 18.0, 'evening': 18.5, 'night': 22.0, 'midnight': 0.0}
_HOUR_ENV = os.environ.get('DSH_HOUR', '').strip()
HOUR = None
if _HOUR_ENV:
    try:
        HOUR = float(_HOUR_PRESETS.get(_HOUR_ENV.lower(), _HOUR_ENV)) % 24.0
    except Exception:
        HOUR = None

SUN_DAY = True
CLOUD_BY_HOUR = None
if HOUR is not None:
    # 6 点日出、18 点日落；仰角按正弦走，正午封顶 62°（再高变顶光，脸全黑）
    _s = math.sin(math.pi * (HOUR - 6.0) / 12.0)      # 6→0, 12→1, 18→0, 0/24→-1
    _elev = 62.0 * _s
    SUN_DAY = _elev > 2.0
    if not os.environ.get('DSH_SUN_ELEV'):
        SUN_ELEV = max(-8.0, _elev)
    if not os.environ.get('DSH_SUN_INT'):
        SUN_INT = (6.0 * min(1.0, max(0.0, _elev / 30.0 + 0.25)) if SUN_DAY else 1.2)
    if not os.environ.get('DSH_SUN_COLOR'):
        _warm = max(0.0, min(1.0, (30.0 - _elev) / 30.0)) if SUN_DAY else 1.0
        SUN_COLOR = (1.0, 0.70 + 0.25 * (1.0 - _warm), 0.42 + 0.45 * (1.0 - _warm))
        if not SUN_DAY:
            SUN_COLOR = (0.55, 0.68, 1.0)      # 夜里当月光用，别拿暖橙打冷景
    # 天空强度：白天足、黄昏稍暗、夜里压低但【绝不为 0】（0 = 背景黑屏）
    _sky_k = 1.0 if 7.0 <= HOUR <= 17.0 else (0.55 if SUN_DAY else 0.20)
    SKY = max(0.03, min(1.0, SKY * _sky_k))

# ★ 贴图过滤 / 背景虚化 / 云 / 雾（v0.5.0）
#   「背景咋是糊的，看着后面是一堆二维码一样的」——
#   世界贴图的过滤方式如果是 Linear，1px 宽的方块边缘会被插值抹成糊块，
#   放大到长焦里就成了一片噪点（像二维码）。默认改成最邻近（Closest）。
TEX_FILTER = ('CLOSEST' if os.environ.get('DSH_TEX_FILTER', 'nearest')
              .lower().startswith(('near', 'clos')) else 'LINEAR')
BG_BLUR = float(os.environ.get('DSH_BG_BLUR', '0') or 0)
_CLOUDS_ENV = os.environ.get('DSH_CLOUDS', '').strip()
CLOUDS_ON = os.environ.get('DSH_MC_CLOUDS', '1') != '0'
if _CLOUDS_ENV:
    CLOUDS_ON = _CLOUDS_ENV.lower() not in ('0', 'off', 'false', 'no')
# 雾浓度倍率：DSH_FOG 优先；老变量 DSH_MC_FOG=0 依旧表示整体关雾
_FOG_MULT_ENV = os.environ.get('DSH_FOG', '').strip()
if _FOG_MULT_ENV:
    try:
        FOG_MULT = max(0.0, float(_FOG_MULT_ENV))
    except Exception:
        FOG_MULT = 1.0
    FOG_ON = FOG_MULT > 0
else:
    FOG_MULT = 1.0

os.makedirs(OUT, exist_ok=True)
LOG = []


def p(s=''):
    LOG.append(str(s))
    print(s)


def finish(code=0):
    open(os.path.join(OUT, '_封面日志.txt'), 'w', encoding='utf-8').write('\n'.join(LOG))
    sys.exit(code)


def try_set(obj, attr, val, tag=''):
    """设置一个属性，失败时【逐项记进日志】，不再用 `except: pass` 静默吞。

    ⚠️ 坑 68：原来天空参数是 `for attr,val in (...): try: setattr(...) except: pass`，
    Blender 5.2 把 `dust_density` 改名成 `aerosol_density` 后，那一行被吞掉，
    等于「写了参数其实没设」，查了半天才发现。凡是版本敏感的属性都走这里。
    """
    try:
        setattr(obj, attr, val)
        return True
    except Exception as e:
        p(f"    ⚠️ 设置 {tag or attr} = {val!r} 失败: {type(e).__name__}: {str(e)[:120]}")
        return False


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
    # 列是 M 作用在 e1/e2 上的像：M@e1 = (a-b)/√2, M@e2 = (a+b)/√2。
    # 这样写 det(M) = +1（右手系）。早先写成 (a+b)/(a-b) 的顺序 det ≡ -1，
    # 等于把剑做了一次镜像 → 法线内翻、刃口没有高光。
    col0 = (a - b) * s
    col1 = (a + b) * s
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
        # 1) 材质参数：保留一点镜面（spec=0 会让全场景零高光，剑刃完全没有反光）
        for key, val in (('Roughness', 0.62), ('Specular IOR Level', 0.45),
                         ('Specular', 0.35)):
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

# ★ 贴图过滤（v0.5.0）——「背景咋是糊的，看着后面是一堆二维码一样的」
#   Linear 会把 1px 的方块边缘插值抹成糊块，长焦放大后就是一片噪点。
if TEX_FILTER == 'CLOSEST':
    _n_fix = 0
    for _m in bpy.data.materials:
        if not (_m.use_nodes and _m.node_tree):
            continue
        for _nd in _m.node_tree.nodes:
            if _nd.type == 'TEX_IMAGE' and _nd.image is not None:
                try:
                    _nd.interpolation = 'Closest'
                    _n_fix += 1
                except Exception:
                    pass
    p(f"    贴图过滤 = 最邻近（Closest），写了 {_n_fix} 个采样节点")
else:
    p("    贴图过滤 = 线性（DSH_TEX_FILTER=linear）")

# ★ 带 alpha 的贴图必须把 alpha 接到 Principled（v0.5.0）
#   —— 这是「背景一团黑」的真正根因：
#   树叶/灌木/花草/仙人掌一类的贴图是【带透明】的 PNG，材质里却只接了 Color，
#   Alpha 留在默认 1.0 → EEVEE 把整面四边形当成【不透明的黑块】渲染，
#   于是画面里出现大片方块状黑斑（用户原话：「背景一团黑」「一坨是啥」）。
_n_alpha = 0
for _m in bpy.data.materials:
    if not (_m.use_nodes and _m.node_tree):
        continue
    _bsdf = next((x for x in _m.node_tree.nodes
                  if x.type == 'BSDF_PRINCIPLED'), None)
    _tex = next((x for x in _m.node_tree.nodes
                 if x.type == 'TEX_IMAGE' and x.image), None)
    if not (_bsdf and _tex):
        continue
    _asock = _bsdf.inputs.get('Alpha')
    if _asock is None or _asock.is_linked:
        continue
    try:
        _has_alpha = (_tex.image.depth == 32) or (_tex.image.channels == 4)
    except Exception:
        _has_alpha = False
    if not _has_alpha:
        continue
    try:
        _m.node_tree.links.new(_tex.outputs['Alpha'], _asock)
    except Exception:
        continue
    for _attr, _val in (('surface_render_method', 'DITHERED'),
                        ('blend_method', 'HASHED'),
                        ('shadow_method', 'HASHED'),
                        ('use_backface_culling', False),
                        ('show_transparent_back', True)):
        try:
            setattr(_m, _attr, _val)
        except Exception:
            pass
    _n_alpha += 1
if _n_alpha:
    p(f"    ★ 透明贴图 {_n_alpha} 个材质已接 Alpha（修「背景一团黑」）")

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
# ★ 单条射线太脆：中心 xy 正好落在方块边界/花丛十字面片的交线上时会「没命中」，
#   于是整张图退化成 GROUND = 世界最低点（相机掉到地形底下，画面一片空）。
#   改成 5×5 打 25 条，取出现最多的那个高度（众数），树顶这类零星高度自动被压掉。
_hits = []
for _dx in (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0):
    for _dy in (-2.0, -1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5, 2.0):
        _h, _loc, _n, _i, _o, _m = bpy.context.scene.ray_cast(
            dg0, mathutils.Vector((WX + _dx, WY + _dy, probe_top)),
            mathutils.Vector((0.0, 0.0, -1.0)), distance=(probe_top - wmn.z) + 20.0)
        if _h:
            _hits.append(round(_loc.z, 2))
if _hits:
    from collections import Counter as _C
    GROUND = _C(_hits).most_common(1)[0][0]
else:
    GROUND = wmn.z
p(f"    世界 xy 中心 ({WX:.1f}, {WY:.1f})")
p(f"    射线向下探测地面: 命中 {len(_hits)}/81 条 → 地面 z={GROUND:.2f}")

# ============================================================
# ---- 视线方位自动选择：先看"朝哪看有天空"（sky-first）----
#   scan 的 openness 只保证「周围 15 格是平地」，完全不管视线，
#   于是可能架好相机才发现正对一堵 26 格高的土崖 —— 上半幅没有天空
#   （实测：旧图天空占比 0%，审图直接报"没有渐变/没有高光斑"）。
#   做法：拿刚导入的世界网格顶点建 2 格精度高度图，对每个候选方位
#   沿视线采样，算「前方地形相对镜头仰角的最大值」；再给"太阳在正前方"
#   加分（低角度太阳+暖霞是黄昏封面的关键，也让审图的天空项有东西可测）。
#   任何一步失败 → 保持用户给的方位，绝不让选角度把渲染搞挂。
# ============================================================
def _eye_azimuth_pick():
    try:
        import numpy as _np
        # wmesh 是世界网格【列表】（这时角色还没加进来），取顶点最多的那块当地形
        _ob = max(wmesh, key=lambda o: len(o.data.vertices))
        _me = _ob.data
        _n = len(_me.vertices)
        _co = _np.empty(_n * 3, dtype=_np.float32)
        _me.vertices.foreach_get('co', _co)
        _co = _co.reshape(_n, 3).astype(_np.float64)
        _M = _np.array(_ob.matrix_world)
        _pts = _co @ _M[:3, :3].T + _M[:3, 3]
        _cell = 2.0
        _x0, _y0 = float(_pts[:, 0].min()), float(_pts[:, 1].min())
        _W = int((float(_pts[:, 0].max()) - _x0) // _cell) + 1
        _H = int((float(_pts[:, 1].max()) - _y0) // _cell) + 1
        _hg = _np.full((_H, _W), -1e9, dtype=_np.float64)
        _ii = ((_pts[:, 0] - _x0) // _cell).astype(_np.int64)
        _jj = ((_pts[:, 1] - _y0) // _cell).astype(_np.int64)
        _np.maximum.at(_hg, (_jj, _ii), _pts[:, 2])
        _eye_z = GROUND + CAM_H
        _r = _np.arange(4.0, 172.0, 2.0)

        def _score(az):
            _phi = math.radians(az)
            _fx, _fy = math.cos(_phi), -math.sin(_phi)
            # 从【相机位置】起算视线（DIST 变了，前景遮挡也跟着变）
            _px = (WX - _fx * DIST) + _fx * _r
            _py = (WY - _fy * DIST) + _fy * _r
            _ci = ((_px - _x0) // _cell).astype(_np.int64)
            _cj = ((_py - _y0) // _cell).astype(_np.int64)
            _ok = (_ci >= 0) & (_ci < _W) & (_cj >= 0) & (_cj < _H)
            if not _ok.any():
                return -1e9, (90.0, 9.0, 0.0)
            _rr = _r[_ok]
            _hh = _hg[_cj[_ok], _ci[_ok]]
            _m = _hh > -1e8
            _rr, _hh = _rr[_m], _hh[_m]
            if _rr.size == 0:
                return -1e9, (90.0, 9.0, 0.0)
            _elev = _np.degrees(_np.arctan2(_hh - _eye_z, _rr))
            _hmax = float(_elev.max())
            # ★ 天空带里的地形占比：仰角 > 1.5° 且 120 格内的视线比例。
            #   远处树冠/浮岛会被读成"浮空方块"糊在天空里（lead 核对 verify2/3 时提的）。
            _sky_hit = float(((_elev > 1.5) & (_rr < 120.0)).mean()) if _rr.size else 1.0
            # ★ 背后空旷度：主体身后 8~150 格里，第一块「高过胸口」的地形有多远。
            #   身后 2~5 格就贴一堵土墙 = 背景又乱又平（用户原话"背景杂乱"），
            #   所以这里奖励"人物背后是天空/远景"，而不是只奖励"画面里有天空"。
            _clear = 150.0
            _bpx = WX + _fx * _r
            _bpy = WY + _fy * _r
            _bci = ((_bpx - _x0) // _cell).astype(_np.int64)
            _bcj = ((_bpy - _y0) // _cell).astype(_np.int64)
            _bok = (_bci >= 0) & (_bci < _W) & (_bcj >= 0) & (_bcj < _H)
            if _bok.any():
                _brr = _r[_bok]
                _bhh = _hg[_bcj[_bok], _bci[_bok]]
                _bm = (_bhh > -1e8) & (_bhh > GROUND + 1.2)
                if _bm.any():
                    _clear = float(_brr[_bm].min())
            _daz = abs((az - SUN_ROT + 180.0) % 360.0 - 180.0)
            sc = 100.0 - 7.0 * max(0.0, _hmax)
            sc -= 55.0 * max(0.0, _sky_hit - 0.06) / 0.94
            # 背后 <18 格就被挡 = 扣分；>60 格给满 10 分（只是加分项，不是主导项）
            sc += 10.0 * min(1.0, max(0.0, (_clear - 18.0) / 42.0))
            # ★ 太阳在正前方 = 逆光：天空会过曝成一块白板、人物变剪影
            #   （审图实测会直接报「天空过曝成一块白板 hot_ratio=0.88」）。
            #   所以不再给「正对太阳」加分，改成【侧逆光】最好、正对给惩罚。
            if _daz < 30.0:
                sc -= 40.0
            sc += 12.0 * max(0.0, 1.0 - abs(_daz - 55.0) / 45.0)
            if _hmax > 10.0:
                sc -= 60.0
            return sc, (_hmax, _sky_hit, _clear)

        return [(a, _score(a)[0], _score(a)[1]) for a in range(0, 360, 5)]
    except Exception as e:
        p(f"    ⚠️ 视线评估失败({type(e).__name__}: {str(e)[:120]}) → 保持原方位")
        return None


_az_scored = _eye_azimuth_pick() if AUTO_AZ else None
if _az_scored:
    # ★ 只在【当前机位 ±40°】里挑：全局最优经常落到水边/悬崖/暗面，
    #   直接把构图毁掉（实测 260° 方向是大片水面 + 逆光剪影，审图直接 FAIL）。
    #   scan 选出来的位置本来就踩在平草地上，别为了"背景更空"把它扔了。
    def _ang(t):
        return abs((t[0] - CAM_AZ + 180.0) % 360.0 - 180.0)

    # 硬约束：视线不能被地形堵住（土崖仰角 > 8° 就是糊脸，相机也会埋进方块里）
    _open = [t for t in _az_scored if t[2][0] <= 8.0]
    # ★ 不按"离原方位多远"限制：scan 给的原方位可能正对土崖（实测 30° 仰角 42.8°），
    #   而干净背景在 130° 之外。真正要拦的是「迎着太阳」和「背后贴墙」，
    #   这两件已经在 _score 里扣过了（迎太阳 −40、背后 <18 格不给加分）。
    _pool = _open or _az_scored
    _best_az, _best_sc, _best_elev = max(_pool, key=lambda t: t[1])
    _cur = min(_az_scored, key=lambda t: abs(t[0] - CAM_AZ))
    p(f"    视线评估: 当前方位 {CAM_AZ:.0f}° → 得分 {_cur[1]:.0f}"
      f"（地形仰角 {_cur[2][0]:.1f}°，天空带地形 {_cur[2][1]*100:.0f}%，背后空旷 {_cur[2][2]:.0f} 格）")
    p(f"    最佳方位 {_best_az:.0f}° → 得分 {_best_sc:.0f}"
      f"（地形仰角 {_best_elev[0]:.1f}°，天空带地形 {_best_elev[1]*100:.0f}%，背后空旷 {_best_elev[2]:.0f} 格，"
      f"太阳在 {SUN_ROT:.0f}°）")
    _top = sorted(_az_scored, key=lambda t: -t[1])[:5]
    p("    候选: " + "  ".join(
        f"{a:.0f}°→{sc:.0f}(仰角{e[0]:.1f}/天空{e[1]*100:.0f}%/后{e[2]:.0f})"
        for a, sc, e in _top))
    if _best_sc > _cur[1] + 12 and abs(_best_az - CAM_AZ) >= 5:
        _old_az = CAM_AZ
        _d_az = _best_az - _old_az
        CAM_AZ = float(_best_az)
        # ⚠️ 这里【不再】顺手改 YAW：方位换了多少度就反向补多少度，这条补偿只有
        #   在「初始 yaw 本来就是正面」时才成立。verify6/verify7 实测证明它不是
        #   不变量（az 200/yaw 25 是正面，az 345/yaw 173 却是背面，两者相对角一样）。
        #   真正可靠的做法是等角色进场后用几何量测出朝向，见 [4.9] 的朝向搜索。
        p(f"    ★ 自动改视线方位 {_old_az:.0f}° → {CAM_AZ:.0f}°"
          f"（角色朝向稍后按几何量测重新定）")


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

# ★ [4.9] 朝向搜索：用几何量测把 yaw 定下来，别再靠「相对角」公式推算。
#
#   为什么不能算：verify6（az 0 / yaw 25）是侧脸，verify7（az 345 / yaw 173）是
#   背面，两组的 (cam_az − yaw) 都在 170° 附近 —— 说明「相对角恒定 ⇒ 视角恒定」
#   这个假设根本不成立（角色静止朝向、相机方位、太阳方位三套换算不统一）。
#   而这里这个几何量测是准的：verify7 它报「面部→相机夹角 112° → 背面」，
#   和成图完全一致。所以直接拿它当目标函数，搜出一个正对镜头的 yaw。
#
#   判据：后脑方向 (head − face) 与「主体 → 相机」方向的夹角 ang，
#   ang 小 = 正对镜头。目标取 25°~40°（正中会显得证件照，留点 3/4 侧脸）。
def _facing_probe(yaw_deg):
    """把角色转到 yaw_deg，量出「面部→相机夹角」。返回 (ang, tag) 或 None。"""
    arm.rotation_euler = (0, 0, math.radians(yaw_deg))
    bpy.context.view_layer.update()
    fb = eval_bbox([_face_obj])
    hb = eval_bbox([_head_obj])
    if not (fb[0] and hb[0]):
        return None
    fx = (fb[0].x + fb[1].x) / 2.0
    fy = (fb[0].y + fb[1].y) / 2.0
    hx = (hb[0].x + hb[1].x) / 2.0
    hy = (hb[0].y + hb[1].y) / 2.0
    vx, vy = hx - fx, hy - fy               # 面部 → 后脑
    cdx = -math.cos(math.radians(CAM_AZ))   # 主体 → 相机
    cdy = math.sin(math.radians(CAM_AZ))
    vl = math.hypot(vx, vy) or 1e-6
    dot = (vx * cdx + vy * cdy) / vl
    ang = math.degrees(math.acos(max(-1.0, min(1.0, -dot))))
    tag = ("正面" if ang < 60 else "侧脸" if ang < 100 else "★背面★")
    return ang, tag


if arm:
    _yaw_orig = YAW
    _face_obj = next((o for o in char if 'mesh face' in o.name.lower()), None)
    _head_obj = next((o for o in char if o.name.lower().endswith('mesh head')), None)
    if _face_obj and _head_obj:
        _probe = _facing_probe(YAW)
        if _probe is None:
            p("    !! 面部/头部包围盒取不到，跳过朝向搜索")
        else:
            p(f"    朝向搜索前：yaw={YAW:.0f}° 夹角 {_probe[0]:.0f}° → {_probe[1]}")
            # 全周扫描（每 3°）直接挑「夹角最接近 45°」的那个 —— 45° 是标准的
            # 3/4 侧脸，脸看得见、举起的右手也不会挡在脸前面（正中 15° 会挡）。
            _cands = [(a, _facing_probe(a)) for a in range(0, 360, 3)]
            _cands = [(a, r) for a, r in _cands if r is not None]
            if _cands:
                YAW = min(_cands, key=lambda t: abs(t[1][0] - 45.0))[0]
                _after = _facing_probe(YAW)
                p(f"    ★ 朝向搜索：yaw {YAW:.0f}°  夹角 {_after[0]:.0f}° → "
                  f"{_after[1]}（相机方位 {CAM_AZ:.0f}°，太阳 {SUN_ROT:.0f}°，"
                  f"扫了 {len(_cands)} 个候选）")
    else:
        p("    !! 找不到 face/head 网格，朝向保持 yaw=%.0f°" % YAW)
    _YAW_DELTA = YAW - _yaw_orig   # 自带模型跟着角色一起对镜头
    arm.rotation_euler = (0, 0, math.radians(YAW))
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
# 肩膀往外侧张开 + 抬手到肩高（Z 42 → 62）。肘部原来 -85° 会把小臂折到胸前，
# 手正好挡在脸下方，剑看起来像从胸口插出来 —— 改成 -52°。
SHOULDER_ROT = _rot3(os.environ.get('DSH_ARM_SHOULDER'), (-40.0, 0.0, 42.0))
ELBOW_ROT = _rot3(os.environ.get('DSH_ARM_ELBOW'), (-85.0, 0.0, 0.0))
WRIST_ROT = _rot3(os.environ.get('DSH_ARM_WRIST'), (0.0, 0.0, 6.0))


# ★ 姿势预设（动作效果）：光调手臂角度做不出「帅气」，
#   得有躯干拧转 + 头转 + 另一只手的配平，剪影才立得住。
#   DSH_POSE=hero|guard|legacy 切换，默认 hero。
POSES = {
    # 动作姿（默认）：剑斜举过肩、躯干朝镜头拧 18°、头再转回来看着镜头，
    # 左臂往身后甩做配平，重心压在前腿 —— 目标是【剪影张开】，不是站桩。
    'action': {
        'Torso': (5.0, 0.0, -18.0),
        'Head': (-4.0, 0.0, 15.0),
        'Top Arm.R': (-88.0, 0.0, 32.0),
        'Bottom Arm.R': (-16.0, 0.0, 0.0),
        'Hand.R': (0.0, 0.0, 4.0),
        'Top Arm.L': (16.0, 0.0, -34.0),
        'Bottom Arm.L': (-24.0, 0.0, 0.0),
        'Hand.L': (0.0, 0.0, -10.0),
        'Top Leg.R': (6.0, 0.0, 0.0),
        'Bottom Leg.R': (-5.0, 0.0, 0.0),
        'Top Leg.L': (-6.0, 0.0, 0.0),
        'Bottom Leg.L': (4.0, 0.0, 0.0),
    },
    # 举剑英雄姿：躯干朝镜头拧 12°、头再补 8° 看向镜头，
    # 右臂抬到肩高外张 34°（剑在头外侧，不糊脸），左臂往后收做配平。
    'hero': {
        'Torso': (-4.0, 0.0, -12.0),
        'Head': (2.0, 0.0, 8.0),
        'Top Arm.R': (-14.0, 0.0, 26.0),
        'Bottom Arm.R': (-28.0, 0.0, 0.0),
        'Hand.R': (0.0, 0.0, 6.0),
        'Top Arm.L': (-14.0, 0.0, -20.0),
        'Bottom Arm.L': (-38.0, 0.0, 0.0),
        'Hand.L': (0.0, 0.0, -8.0),
    },
    # 举过头顶：拳头升到头顶以上，剑尖再往上 —— 剪影最清楚，脸完全不被挡。
    'raise': {
        'Torso': (-3.0, 0.0, -10.0),
        'Head': (3.0, 0.0, 10.0),
        'Top Arm.R': (-96.0, 0.0, 26.0),
        'Bottom Arm.R': (-12.0, 0.0, 0.0),
        'Hand.R': (0.0, 0.0, 0.0),
        'Top Arm.L': (-12.0, 0.0, -22.0),
        'Bottom Arm.L': (-30.0, 0.0, 0.0),
        'Hand.L': (0.0, 0.0, -8.0),
    },
    # 横持警戒姿：剑横在身前，左手压住剑脊（双手感），躯干略前倾。
    'guard': {
        'Torso': (6.0, 0.0, -8.0),
        'Head': (-2.0, 0.0, 6.0),
        'Top Arm.R': (-48.0, 0.0, 18.0),
        'Bottom Arm.R': (-70.0, 0.0, 0.0),
        'Hand.R': (0.0, 0.0, 4.0),
        'Top Arm.L': (-42.0, 0.0, -26.0),
        'Bottom Arm.L': (-64.0, 0.0, 0.0),
        'Hand.L': (0.0, 0.0, -6.0),
    },
    # 旧姿势（0.2.x 的站桩抬手），保留用于对比/回退。
    'legacy': {
        'Top Arm.R': (-40.0, 0.0, 42.0),
        'Bottom Arm.R': (-85.0, 0.0, 0.0),
        'Hand.R': (0.0, 0.0, 6.0),
    },
}


def pose_right_arm():
    """按预设摆角色（FK）。主要动右臂，同时带躯干/头/左臂。"""
    if not arm:
        return False
    if not ARM_POSE:
        p("    (DSH_ARM_POSE=0，保持默认站姿)")
        return False
    name = (os.environ.get('DSH_POSE') or 'action').lower()
    spec = POSES.get(name) or POSES['action']
    done = []
    for bone, rot in spec.items():
        pb = arm.pose.bones.get(bone)
        if pb is None:
            continue
        pb.rotation_mode = 'XYZ'
        pb.rotation_euler = tuple(math.radians(v) for v in rot)
        done.append(f"{bone}{rot}")
    bpy.context.view_layer.update()
    p(f"    姿势 {name}：{' / '.join(done)}（FK）")
    return bool(done)


p("\n[4.8] 摆右臂（FK，举剑）")
pose_right_arm()
bpy.context.view_layer.update()

# ============================================================
# [5] 手持物品（v0.5.0）
#   用户原话：「左手拿工作台，右手拿铁砧」「我记得不是能直接调物品吗？？」
#   老版本只有一段写死的「挂钻石剑」，别的物品全靠手写 Blender 脚本 ——
#   上一场就是这么烂尾的。现在给名字就行：
#     · 物品库（Minecraft_Item_Rig.blend，376 件）里有 → 按剑的握法挂上去
#     · 库里没有（anvil / crafting_table / furnace …）→ 用原版方块贴图建方块
#   握法沿用坑 51/55/71/72/75/76 的结论：先用 FK 摆手臂，
#   抓握点取【形变后的拳头网格中心】，再沿「刃轴」把柄沉进拳心。
# ============================================================
p("\n[5] 手持物品（左手 / 右手）")

def _norm_item_spec(v):
    """允许直接写 MC 物品 id：minecraft:diamond_sword → diamond sword（再交给模糊匹配）。"""
    x = (v or '').strip()
    for pre in ('minecraft:', 'mc:'):
        if x.lower().startswith(pre):
            x = x[len(pre):]
            break
    return x.replace('_', ' ').strip()


ITEM_R_SPEC = _norm_item_spec(os.environ.get('DSH_ITEM_R', 'Sword: Diamond'))
ITEM_L_SPEC = _norm_item_spec(os.environ.get('DSH_ITEM_L', ''))
TARGET_LEN = float(os.environ.get('DSH_ITEM_LEN', '1.35'))        # 物品库成品长度（格）
BLOCK_SCALE = float(os.environ.get('DSH_BLOCK_SCALE', '0.42'))    # 手持方块边长（格）


def _norm(s):
    return ''.join(ch for ch in str(s).lower() if ch.isalnum())


# ---- 贴图库：决定「原版方块」长什么样 ----
def _mcprep_tex_dirs():
    """MCprep 自带资源包里的方块贴图（vendor/textures 没搬全时的兜底）。

    ⚠️ 之前只认 VENDOR/textures，谁忘了跑 setup_assets 搬贴图，
    `_TEX_DIRS` 就是空 → 树林整段跳过、方块物品只能画成纯色，而且只打一行警告。
    这里额外扫一遍 Blender 插件目录下 MCprep 的 resourcepacks。
    """
    out = []
    roots = []
    try:
        import bpy
        for sp in bpy.utils.script_paths():
            roots.append(os.path.join(sp, 'addons'))
            roots.append(os.path.join(sp, 'addons_core'))
    except Exception:
        pass
    appdata = os.environ.get('APPDATA')
    if appdata:
        bf = os.path.join(appdata, 'Blender Foundation', 'Blender')
        if os.path.isdir(bf):
            for ver in sorted(os.listdir(bf), reverse=True):
                roots.append(os.path.join(bf, ver, 'scripts', 'addons'))
    for r in roots:
        if not os.path.isdir(r):
            continue
        try:
            packs = os.listdir(r)
        except OSError:
            continue
        for name in packs:
            if 'mcprep' not in name.lower():
                continue
            rp = os.path.join(r, name, 'MCprep_resources', 'resourcepacks')
            if not os.path.isdir(rp):
                continue
            for pack in os.listdir(rp):
                d = os.path.join(rp, pack, 'assets', 'minecraft', 'textures', 'block')
                if os.path.isdir(d) and d not in out:
                    out.append(d)
    return out


def _tex_dirs():
    cands = []
    for c in (TEXDIR,
              os.path.join(TEXDIR, 'block') if TEXDIR else '',
              os.path.join(TEXDIR, 'blocks') if TEXDIR else '',
              os.path.join(VENDOR, 'textures', 'block') if VENDOR else '',
              os.path.join(VENDOR, 'textures') if VENDOR else '',
              *_mcprep_tex_dirs()):
        if c and os.path.isdir(c) and c not in cands:
            cands.append(c)
    return cands


_TEX_DIRS = _tex_dirs()


def _tex_path(*names):
    for n in names:
        if not n:
            continue
        for d in _TEX_DIRS:
            f = os.path.join(d, n + '.png')
            if os.path.isfile(f):
                return f
    return None


# 每个面找贴图的候选顺序（MC 原版命名：top / side / front / bottom）
_FACE_CANDS = {
    'top': ('{b}_top', 'top_{b}', '{b}'),
    'bottom': ('{b}_bottom', 'bottom_{b}', '{b}_top', '{b}'),
    'side': ('{b}_side', '{b}'),
    'front': ('{b}_front', '{b}_side', '{b}'),
    'back': ('{b}_back', '{b}_side', '{b}'),
}


def _block_id(spec):
    s = str(spec).split(':')[-1].strip().lower()
    s = s.replace('minecraft:', '').replace(' ', '_').replace('-', '_')
    return s


def _block_tex(bid, face):
    return _tex_path(*[c.format(b=bid) for c in _FACE_CANDS.get(face, ('{b}',))])


def _mat_from_image(img_path, name, tint=None):
    key = name + ('' if tint is None else '_tint')
    m = bpy.data.materials.get(key)
    if m is not None:
        return m
    m = bpy.data.materials.new(key)
    m.use_nodes = True
    nt = m.node_tree
    bsdf = next((n for n in nt.nodes if n.type == 'BSDF_PRINCIPLED'), None)
    if bsdf is not None:
        for k, v in (('Roughness', 0.62), ('Specular IOR Level', 0.45), ('Specular', 0.35)):
            s = bsdf.inputs.get(k)
            if s is not None:
                try:
                    s.default_value = v
                except Exception:
                    pass
    if img_path:
        tex = nt.nodes.new('ShaderNodeTexImage')
        try:
            tex.image = bpy.data.images.load(img_path, check_existing=True)
        except Exception:
            tex.image = None
        try:
            tex.interpolation = 'Closest' if TEX_FILTER == 'CLOSEST' else 'Linear'
        except Exception:
            pass
        out = tex.outputs['Color']
        if tint is not None:
            try:
                mix, c1, c2, mout = _mix_rgb(nt)
                c2.default_value = (tint[0], tint[1], tint[2], 1.0)
                nt.links.new(tex.outputs['Color'], c1)
                out = mout
            except Exception:
                out = tex.outputs['Color']
        if bsdf is not None:
            try:
                nt.links.new(out, bsdf.inputs['Base Color'])
            except Exception:
                pass
        if tint is not None:
            for attr, val in (('blend_method', 'CLIP'),
                              ('surface_render_method', 'DITHERED')):
                try:
                    setattr(m, attr, val)
                    break
                except Exception:
                    continue
    return m


# ---- 方块几何：把若干 1×1×1 方块合成【一个】网格（逐面 UV，面数可控） ----
_CUBE_V = ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0),
           (0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))
_CUBE_F = (('+z', (4, 7, 6, 5)), ('-z', (0, 3, 2, 1)),
           ('-y', (0, 1, 5, 4)), ('+y', (3, 7, 6, 2)),
           ('-x', (0, 4, 7, 3)), ('+x', (1, 2, 6, 5)))
_UVQ = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))


def _build_boxes(name, blocks, mats):
    """blocks: [(x, y, z, matkey_fn)] → 一个 mesh 对象（不链接场景）。"""
    verts, faces, uvs, poly_mats = [], [], [], []
    order = []
    for (bx, by, bz, keyfn) in blocks:
        for face_name, quads in _CUBE_F:
            key = keyfn(face_name)
            if key not in order:
                order.append(key)
            base = len(verts)
            for vi in quads:
                vx, vy, vz = _CUBE_V[vi]
                verts.append((bx + vx, by + vy, bz + vz))
            faces.append((base, base + 1, base + 2, base + 3))
            poly_mats.append(order.index(key))
            uvs.extend(_UVQ)
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.validate()
    me.update()
    uvl = me.uv_layers.new(name='UVMap')
    for i, uv in enumerate(uvs):
        if i < len(uvl.data):
            uvl.data[i].uv = uv
    ob = bpy.data.objects.new(name, me)
    for key in order:
        me.materials.append(mats[key])
    for poly, fi in zip(me.polygons, poly_mats):
        poly.material_index = fi
    return ob


# ---- 物品库查找（模糊匹配：忽略大小写 / 空格 / 下划线 / 冒号）----
def _rig_item_names():
    if not ITEM or not os.path.isfile(ITEM):
        return []
    try:
        with bpy.data.libraries.load(ITEM, link=False) as (src, _dst):
            return list(src.objects)
    except Exception as e:
        p(f"    ⚠️ 物品库打不开（{ITEM}）: {type(e).__name__}: {str(e)[:120]}")
        return []


_ITEM_NAMES = _rig_item_names()


def _pick_rig_item(spec):
    s = _norm(spec)
    if not s or not _ITEM_NAMES:
        return None
    exact = [n for n in _ITEM_NAMES if _norm(n) == s]
    if exact:
        return sorted(exact, key=len)[0]
    part = [n for n in _ITEM_NAMES if s in _norm(n)]
    if part:
        return sorted(part, key=len)[0]
    words = [_norm(w) for w in str(spec).replace(':', ' ').replace('_', ' ').split()]
    words = [w for w in words if w]
    if words:
        hits = [n for n in _ITEM_NAMES if all(w in _norm(n) for w in words)]
        if hits:
            return sorted(hits, key=len)[0]
    return None


def _load_rig_object(name):
    before = set(bpy.data.objects.keys())
    try:
        with bpy.data.libraries.load(ITEM, link=False) as (src, dst):
            dst.objects = [name] if name in src.objects else []
        bpy.context.view_layer.update()
    except Exception as e:
        p(f"    ⚠️ 加载 {name} 失败: {type(e).__name__}: {str(e)[:120]}")
        return None, []
    new = [bpy.data.objects[n] for n in bpy.data.objects.keys() if n not in before]
    for o in new:
        if not o.users_collection:
            try:
                sc.objects.link(o)
            except Exception:
                pass
    return (new[0] if new else None), new


def _hand_ok(hand):
    return arm is not None and any(b.name == hand for b in arm.data.bones)


def _fist_center(mid, radius=0.30, min_n=8):
    """取「形变之后、真实拳头网格」里离手骨最近的顶点群中心。

    ⚠️ 坑 75：BPS 的手骨偏腕侧，直接拿骨中点当抓握点，
       物品看起来是「插在手腕/小臂上」。
    """
    dg = bpy.context.evaluated_depsgraph_get()
    best = None
    best_n = 0
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or ob.parent is None or ob.parent.name != arm.name:
            continue
        nm = ob.name.lower()
        if ('hand' not in nm) and ('arm' not in nm):
            continue
        try:
            ev = ob.evaluated_get(dg)
            me = ev.to_mesh()
        except Exception:
            continue
        acc = mathutils.Vector((0.0, 0.0, 0.0))
        n = 0
        for v in me.vertices:
            w = ev.matrix_world @ v.co
            if (w - mid).length <= radius:
                acc += w
                n += 1
        ev.to_mesh_clear()
        if n >= min_n and n > best_n:
            best = acc / float(n)
            best_n = n
    return best


def _grip_point(hand):
    if not _hand_ok(hand):
        return None, None
    pb = arm.pose.bones[hand]
    head_w = arm.matrix_world @ pb.head
    tail_w = arm.matrix_world @ pb.tail
    bone_mid = (head_w + tail_w) / 2.0
    fist = _fist_center(bone_mid)
    return (fist if fist is not None else bone_mid), head_w


def _head_center():
    try:
        hb = eval_bbox([_head_obj]) if _head_obj else None
        if hb and hb[0] is not None:
            return (hb[0] + hb[1]) / 2.0
    except Exception:
        pass
    return None


def _side_vectors(hand_pos):
    """视图系：to_cam（指向相机）/ lat（头心→拳头，即身体外侧）。"""
    az = math.radians(CAM_AZ)
    to_cam = mathutils.Vector((-math.cos(az), math.sin(az), 0.0)).normalized()
    hc = _head_center()
    if hc is not None:
        lat = mathutils.Vector((hand_pos.x - hc.x, hand_pos.y - hc.y, 0.0))
    else:
        lat = mathutils.Vector((hand_pos.x - WX, hand_pos.y - WY, 0.0))
    if lat.length < 1e-4:
        lat = mathutils.Vector((to_cam.y, -to_cam.x, 0.0))
    return to_cam, lat.normalized()


def _attach_rig_item(obj, hand, label):
    """把物品库对象挂到 hand 上（剑/斧/镐同一套握法）。"""
    mxs = [v.co.x for v in obj.data.vertices]
    mys = [v.co.y for v in obj.data.vertices]
    mzs = [v.co.z for v in obj.data.vertices]
    if not mxs:
        return None
    x0, x1, y0, y1 = min(mxs), max(mxs), min(mys), max(mys)
    diag = math.hypot(x1 - x0, y1 - y0) or 2.83
    K = TARGET_LEN / diag
    # 柄尾在 bbox 的 (-,-) 角，刃沿对角 (1,1,0) —— 这是这套 rig 的约定
    pommel = mathutils.Vector((x0, y0, (min(mzs) + max(mzs)) / 2.0))
    blade_dir = mathutils.Vector((x1 - x0, y1 - y0, 0.0)).normalized()
    _sink_env = os.environ.get('DSH_SWORD_SINK')
    sink_local = float(_sink_env) if _sink_env else (0.275 / K)
    anchor = pommel + blade_dir * sink_local

    hand_pos, _head_w = _grip_point(hand)
    if hand_pos is None:
        return None
    to_cam, lat = _side_vectors(hand_pos)
    hc = _head_center()

    def _seg_head_dist(d, length=1.35):
        h = hc if hc is not None else mathutils.Vector(
            (hand_pos.x, hand_pos.y, hand_pos.z + 0.5))
        ah = h - hand_pos
        t = max(0.0, min(length, ah.dot(d)))
        return (ah - d * t).length

    up = mathutils.Vector((0.0, 0.0, 1.0))
    best = None
    for sg in (1.0, -1.0):
        d = (up + lat * (0.95 * sg) - to_cam * 0.06).normalized()
        dd = _seg_head_dist(d)
        if best is None or dd > best[0]:
            best = (dd, d, sg)
    _dist_head, blade_w, sg_used = best
    normal_w = (-to_cam).normalized()
    R = basis_from(blade_w, normal_w).to_4x4()
    M = (mathutils.Matrix.Translation(hand_pos) @ R
         @ mathutils.Matrix.Scale(K, 4)
         @ mathutils.Matrix.Translation(-anchor))
    obj.parent = arm
    obj.parent_type = 'BONE'
    obj.parent_bone = hand
    obj.matrix_world = M
    bpy.context.view_layer.update()
    tip = M @ mathutils.Vector((x1, y1, (min(mzs) + max(mzs)) / 2.0))
    p(f"    {hand} ← 物品库「{label}」  长 {diag * K:.2f} 格  "
      f"抓握 {tuple(round(v, 2) for v in hand_pos)}  离头球 {_dist_head:.2f} 格  "
      f"刃{'上' if tip.z > hand_pos.z else '下'}")
    return obj


def _attach_block_item(spec, hand):
    """物品库里没有的 → 用原版方块贴图现建方块拿在手里。"""
    bid = _block_id(spec)
    texs = {f: _block_tex(bid, f) for f in ('top', 'bottom', 'side', 'front', 'back')}
    if not any(texs.values()):
        p(f"    !! {hand} 「{spec}」：物品库里没有，贴图库里也没有 {bid}_*.png")
        p(f"       （贴图库: {_TEX_DIRS or '没找到'}）")
        return None
    mats = {}
    for f, path in texs.items():
        mats[f] = _mat_from_image(path, f"block_{bid}_{f}")
    hand_pos, _hw = _grip_point(hand)
    if hand_pos is None:
        return None
    to_cam, lat = _side_vectors(hand_pos)
    up = mathutils.Vector((0.0, 0.0, 1.0))
    # 往镜头侧（to_cam）、外侧（+lat）和下方推出去一点：
    # 原来贴在拳心正中，方块会跟胸口/肩膀糊在一起（用户：「挡在胸前/肩侧」）。
    _out = 0.55 * BLOCK_SCALE
    pos = (hand_pos
           + to_cam * _out
           + lat * (0.32 * BLOCK_SCALE)
           + up * (0.02 * BLOCK_SCALE))
    theta = math.atan2(-to_cam.x, to_cam.y)
    R = (mathutils.Matrix.Rotation(theta, 4, 'Z')
         @ mathutils.Matrix.Rotation(math.radians(-14.0), 4, 'X'))
    # ★ 面名 → 材质 key。_CUBE_F 用 '+z'/'-y' 这种轴别名，mats 用 top/side/front，
    #   之前直接把轴名当 key 传进去 → KeyError: '+z'，两只手都挂不上（v0.4.6 的 bug）。
    _FACE_ALIAS = {'+z': 'top', '-z': 'bottom', '-y': 'front', '+y': 'back',
                   '+x': 'side', '-x': 'side'}
    blocks = [(-0.5, -0.5, -0.5, lambda f: _FACE_ALIAS.get(f, 'side'))]
    ob = _build_boxes(f"手持_{bid}", blocks, mats)
    sc.objects.link(ob)
    ob.parent = arm
    ob.parent_type = 'BONE'
    ob.parent_bone = hand
    ob.matrix_world = (mathutils.Matrix.Translation(pos) @ R
                       @ mathutils.Matrix.Scale(BLOCK_SCALE, 4))
    bpy.context.view_layer.update()
    p(f"    {hand} ← 原版方块「{bid}」贴图 "
      f"{sum(1 for v in texs.values() if v)}/5 面  边长 {BLOCK_SCALE} 格  "
      f"位置 {tuple(round(v, 2) for v in pos)}")
    return ob


def hold_item_in_hand(spec, hand):
    spec = (spec or '').strip()
    if not spec:
        p(f"    {hand}: 空手")
        return None
    if not _hand_ok(hand):
        p(f"    !! {hand}: 骨架里没有这根手骨（骨架没装上？BPS={BPS}）")
        return None
    name = _pick_rig_item(spec)
    if name:
        obj, _all = _load_rig_object(name)
        if obj is not None:
            got = _attach_rig_item(obj, hand, name)
            if got is not None:
                return got
    return _attach_block_item(spec, hand)


# 手里要拿【方块】时，手臂换成「自然下垂 + 微微前伸」的捧物姿势：
#   默认的 hero 姿势是抬臂举剑（手在胸口高度），方块挂在拳心就会糊在胸/肩上
#   （用户原话：「铁砧挡在肩侧、工作台挡在胸前」）。
if arm and (ITEM_R_SPEC or ITEM_L_SPEC):
    _pb = arm.pose.bones
    _poses = []
    for _spec, _side in ((ITEM_R_SPEC, 'R'), (ITEM_L_SPEC, 'L')):
        _spec = (_spec or '').strip()
        if not _spec:
            continue
        if _pick_rig_item(_spec):
            continue                      # 物品库里的剑/斧等，保持举着的姿势
        _sign = 1.0 if _side == 'R' else -1.0
        _poses.append((f'Top Arm.{_side}', (-6.0, 0.0, 8.0 * _sign)))
        _poses.append((f'Bottom Arm.{_side}', (-16.0, 0.0, 0.0)))
        _poses.append((f'Hand.{_side}', (0.0, 0.0, 0.0)))
    for _bone, _rot in _poses:
        _b = _pb.get(_bone)
        if _b is not None:
            _b.rotation_mode = 'XYZ'
            _b.rotation_euler = tuple(math.radians(v) for v in _rot)
    if _poses:
        bpy.context.view_layer.update()
        p("    手里拿方块 → 手臂换成自然下垂的捧物姿势")

HELD_OBJS = []      # 右手/左手挂上的物品对象（视线检测时要跟角色一起临时隐藏）
for _hand, _spec in (('Hand.R', ITEM_R_SPEC), ('Hand.L', ITEM_L_SPEC)):
    try:
        _ho = hold_item_in_hand(_spec, _hand)
        if _ho is not None:
            HELD_OBJS.append(_ho)
    except Exception as _e:
        p(f"    ⚠️ {_hand} 挂物品失败({type(_e).__name__}: {str(_e)[:140]}) → 该手保持空")

# ============================================================
# [5.2] 盔甲（DSH_ARMOR）—— 真穿在骨骼上，不是拿在手里
#   物品库里有两套盔甲：
#     ① 可穿戴的 3D 件：'Helmet:<材质>' / 'Chestplate:<材质>' / 'Chestplate:<材质>-Arm.L|R'
#        （Helmet 0.57×0.57×0.50、Chestplate 0.53×0.27×0.73，就是 1 格 = 1 单位的方块盔甲）
#     ② 扁平图标：'Diamond-Helmet(item)' 这种厚 0.04 的，那是手持物品图，穿上身会像贴纸 → 不用
#   挂接方式跟手持物品一致：parent_type='BONE'，然后写 matrix_world。
# ============================================================
ARMOR_SPEC = (os.environ.get('DSH_ARMOR') or '').strip()
try:
    ARMOR_YAW = float(os.environ.get('DSH_ARMOR_YAW') or 0.0)
except ValueError:
    ARMOR_YAW = 0.0


def _part_box(kw):
    """身体某部件求值后的包围盒 → (骨架空间中心, 尺寸)。

    必须在【骨架空间】量：直接取世界包围盒会被角色整体旋转/摆位污染
    （头转了 25° 世界 x 尺寸就从 1.0 缩到 0.69），量出来再算缩放就全乱。
    """
    dg = bpy.context.evaluated_depsgraph_get()
    inv = arm.matrix_world.inverted()
    mn = mx = None
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or not ob.data or not len(ob.data.vertices):
            continue
        n = ob.name.lower()
        if kw not in n or 'boneshape' in n or 'slim' in n:
            continue
        if not is_body(ob, arm):
            continue
        try:
            ev = ob.evaluated_get(dg)
            me = ev.to_mesh()
        except Exception:
            continue
        mw = inv @ ev.matrix_world
        for v in me.vertices:
            w = mw @ v.co
            if mn is None:
                mn = [w.x, w.y, w.z]
                mx = [w.x, w.y, w.z]
            else:
                mn = [min(mn[0], w.x), min(mn[1], w.y), min(mn[2], w.z)]
                mx = [max(mx[0], w.x), max(mx[1], w.y), max(mx[2], w.z)]
        ev.to_mesh_clear()
    if mn is None:
        return None, None
    c = mathutils.Vector(((mn[0] + mx[0]) / 2.0, (mn[1] + mx[1]) / 2.0,
                          (mn[2] + mx[2]) / 2.0))
    sz = mathutils.Vector((mx[0] - mn[0], mx[1] - mn[1], mx[2] - mn[2]))
    return c, sz


def _ensure_image(name):
    """按名字把物品库里的 image 数据块拉进来（贴图不会随对象一起自动加载）。"""
    if not name:
        return None
    if name in bpy.data.images:
        return bpy.data.images[name]
    try:
        with bpy.data.libraries.load(ITEM, link=False) as (src, dst):
            if name in src.images:
                dst.images = [name]
    except Exception:
        return None
    return bpy.data.images.get(name)


ARMOR_RGB = {'leather': (0.52, 0.33, 0.19), 'chain': (0.62, 0.63, 0.66),
             'iron': (0.82, 0.83, 0.85), 'gold': (0.93, 0.76, 0.26),
             'diamond': (0.36, 0.85, 0.86), 'netherite': (0.30, 0.25, 0.27)}

# 借钻石模型/贴图时用来「染成该材质的颜色」：
#   netherite 的贴图在物品库里是 0×0 空壳、chain 一件可穿戴模型都没有，
#   0.4.9 起这两类改成「用钻石的模型 + 乘色」，至少形状和色调对得上。
ARMOR_TINT = {'leather': (0.62, 0.40, 0.24), 'chain': (0.72, 0.73, 0.76),
              'netherite': (0.36, 0.31, 0.34)}
ARMOR_COLOR = {}          # 由 _parse_armor_spec 填：材质 → 用户指定的 RGB（0-1）


def _srgb_to_linear(v):
    v = max(0.0, min(1.0, v))
    return v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4


def _hex_to_rgb(txt):
    """'#8B4513' / '8b4513' / '#84b' → 0-1 的 sRGB 分量。

    染色是「贴图 × 颜色」，在 sRGB 空间乘才跟 MC 里看到的一致；
    纯色材质交给 Blender 前再转线性（见 _solid_armor_mat）。
    """
    h = (txt or '').strip().lstrip('#')
    if len(h) == 3:
        h = ''.join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return (int(h[0:2], 16) / 255.0, int(h[2:4], 16) / 255.0,
                int(h[4:6], 16) / 255.0)
    except ValueError:
        return None


def _armor_rgb(mat):
    """这件盔甲该染成什么色：用户给的单件色 > 用户给的全局色 > 材质默认色。"""
    m = (mat or '').lower()
    return (ARMOR_COLOR.get(m) or ARMOR_COLOR.get('_default')
            or ARMOR_TINT.get(m) or ARMOR_RGB.get(m))


def _tint_mats(obj, rgb):
    """MC 的皮革盔甲贴图是灰模（游戏里靠染色），直接贴上去是灰的 → 乘一个颜色。

    物品库里的盔甲材质是 Diffuse + Glossy 混的老式结构（不是 Principled），
    所以这里不认 BSDF 类型，只找「Color 输入被贴图直接驱动的那个节点」。
    """
    if not rgb:
        return 0
    n = 0
    for slot in obj.data.materials:
        if slot is None or not slot.use_nodes:
            continue
        nt = slot.node_tree
        for nd in list(nt.nodes):
            for sock in nd.inputs:
                if sock.name not in ('Color', 'Base Color'):
                    continue
                if not sock.is_linked:
                    continue
                src = sock.links[0].from_socket
                if src.node.type != 'TEX_IMAGE':
                    continue
                mix = nt.nodes.new('ShaderNodeMixRGB')
                mix.blend_type = 'MULTIPLY'
                mix.inputs['Fac'].default_value = 1.0
                mix.inputs['Color2'].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
                nt.links.new(src, mix.inputs['Color1'])
                nt.links.new(mix.outputs['Color'], sock)
                n += 1
    return n


def _rebuild_armor_mat(obj, rgb, desat=False):
    """把盔甲件的材质重建成「贴图 → (去饱和) → (乘色) → Principled」。

    物品库里的盔甲材质是 Diffuse + Glossy + Mix Shader 的老结构：染色只改到 Diffuse，
    腿部那种面朝光的件会被 Glossy 的白高光盖住，于是「皮裤染了还是灰白的」。
    统一重建成单个 Principled，颜色/粗糙度可控，各材质看起来也一致。
    """
    n = 0
    for slot in obj.data.materials:
        if slot is None or not slot.use_nodes:
            continue
        nt = slot.node_tree
        img = None
        for nd in nt.nodes:
            if nd.type == 'TEX_IMAGE' and nd.image is not None and nd.image.size[0] > 0:
                img = nd.image
                break
        if img is None:
            continue
        nt.nodes.clear()
        out = nt.nodes.new('ShaderNodeOutputMaterial')
        out.location = (520, 0)
        bsdf = nt.nodes.new('ShaderNodeBsdfPrincipled')
        bsdf.location = (200, 0)
        tex = nt.nodes.new('ShaderNodeTexImage')
        tex.image = img
        tex.interpolation = 'Closest'
        tex.location = (-520, 60)
        src = tex.outputs['Color']
        if desat:
            hs = nt.nodes.new('ShaderNodeHueSaturation')
            hs.inputs['Saturation'].default_value = 0.0
            hs.location = (-300, 60)
            nt.links.new(src, hs.inputs['Color'])
            src = hs.outputs['Color']
        if rgb:
            mix = nt.nodes.new('ShaderNodeMixRGB')
            mix.blend_type = 'MULTIPLY'
            mix.inputs['Fac'].default_value = 1.0
            mix.inputs['Color2'].default_value = (rgb[0], rgb[1], rgb[2], 1.0)
            mix.location = (-80, 60)
            nt.links.new(src, mix.inputs['Color1'])
            src = mix.outputs['Color']
        nt.links.new(src, bsdf.inputs['Base Color'])
        try:
            if img.depth == 32 or img.channels == 4:
                nt.links.new(tex.outputs['Alpha'], bsdf.inputs['Alpha'])
                slot.surface_render_method = 'DITHERED'
        except Exception:
            pass
        try:
            bsdf.inputs['Roughness'].default_value = 0.55
            bsdf.inputs['Metallic'].default_value = 0.10
        except Exception:
            pass
        nt.links.new(bsdf.outputs['BSDF'], out.inputs['Surface'])
        n += 1
    return n


def _desat_mats(obj, amount=1.0):
    """把贴图先抽成灰度（借钻石贴图给 chain/netherite 时用）。

    单纯乘色只能压暗、去不掉青色调，所以先插一个 Hue/Saturation 把 Saturation 归零。
    """
    n = 0
    for slot in obj.data.materials:
        if slot is None or not slot.use_nodes:
            continue
        nt = slot.node_tree
        for nd in list(nt.nodes):
            for sock in nd.inputs:
                if sock.name not in ('Color', 'Base Color') or not sock.is_linked:
                    continue
                src = sock.links[0].from_socket
                if src.node.type != 'TEX_IMAGE':
                    continue
                hs = nt.nodes.new('ShaderNodeHueSaturation')
                hs.inputs['Saturation'].default_value = 0.0
                hs.inputs['Value'].default_value = 1.0
                hs.location = (nd.location.x + 40, nd.location.y - 160)
                nt.links.new(src, hs.inputs['Color'])
                nt.links.new(hs.outputs['Color'], sock)
                n += 1
    return n


def _solid_armor_mat(mat_name, rgb, name='armor_solid'):
    """给「贴图丢了」的盔甲件兜一个纯色材质（不然 Blender 会画成洋红）。"""
    m = bpy.data.materials.get(name)
    if m is None:
        m = bpy.data.materials.new(name)
        m.use_nodes = True
        bsdf = m.node_tree.nodes.get('Principled BSDF')
        if bsdf is not None:
            bsdf.inputs['Base Color'].default_value = (
                _srgb_to_linear(rgb[0]), _srgb_to_linear(rgb[1]),
                _srgb_to_linear(rgb[2]), 1.0)
            try:
                bsdf.inputs['Roughness'].default_value = 0.45
                bsdf.inputs['Metallic'].default_value = 0.6
            except Exception:
                pass
    return m


def _patch_broken_mats(obj, rgb, tag, fallback_img=None):
    """处理「贴图缺失」的盔甲材质。

    优先借 fallback_img（钻石的 armor layer）顶上再乘 rgb —— 形状纹理都保住，
    只是换个颜色；借不到才退化成纯色（否则 Blender 会画成洋红）。
    """
    fixed = swapped = 0
    for i, slot in enumerate(obj.data.materials):
        if slot is None:
            continue
        broken = False
        if slot.use_nodes:
            for nd in slot.node_tree.nodes:
                if nd.type == 'TEX_IMAGE':
                    if nd.image is None or nd.image.size[0] == 0:
                        broken = True
        if not broken:
            continue
        _img = _ensure_image(fallback_img) if fallback_img else None
        if _img is not None and slot.use_nodes:
            for nd in slot.node_tree.nodes:
                if nd.type == 'TEX_IMAGE' and (nd.image is None or nd.image.size[0] == 0):
                    nd.image = _img
            swapped += 1
            continue
        if rgb:
            obj.data.materials[i] = _solid_armor_mat(slot.name, rgb,
                                                     'armor_solid_%s_%d' % (tag, i))
            fixed += 1
    return {'solid': fixed, 'swapped': swapped}


def _wear_piece(obj_name, bone, part_kw, fit_w, grow=1.02, dz=0.0, dz_frac=0.0,
                img_swap=None, fallback_img=None, desat=False):
    """把物品库里的一件 3D 盔甲穿到骨头上。

    fit_w = 身体该部件的【宽度】（BPS 是 2 倍 MC 比例：头 1.0、躯干 1.0、手臂 0.5），
    缩放按「模型自身宽度 → 部件宽度」算，再乘 grow 留一点外壳厚度。
    """
    if arm is None:
        return None
    _real = obj_name
    if _real not in _ITEM_NAMES:
        for _cand in (obj_name + '.001', obj_name + '.002'):
            if _cand in _ITEM_NAMES:
                _real = _cand
                break
    if _real not in _ITEM_NAMES:
        _real = _pick_rig_item(obj_name) or obj_name
    if _real not in _ITEM_NAMES:
        return None
    obj_name = _real
    if not any(b.name == bone for b in arm.data.bones):
        p(f"    ⚠️ 骨架里没有 {bone} 骨 → {obj_name} 穿不上")
        return None
    obj, _new = _load_rig_object(obj_name)
    if obj is None:
        return None
    # 换材质贴图：同形状的腿/靴只差一张 armor layer 图（diamond/iron/gold/leather 各一组）
    if img_swap:
        _im = _ensure_image(img_swap)
    else:
        _im = None
    if _im is not None:
        for _slot in obj.data.materials:
            if not _slot or not _slot.use_nodes:
                continue
            for _nx in _slot.node_tree.nodes:
                if _nx.type == 'TEX_IMAGE' and _nx.image is not None:
                    _nx.image = _im
    _rgb = _armor_rgb(MAT_HINT)
    _fix = _patch_broken_mats(obj, _rgb, MAT_HINT or 'x', fallback_img)
    if _fix['solid']:
        p(f"    （「{obj_name}」有 {_fix['solid']} 个材质的贴图丢了 → 换成纯色）")
    if _fix['swapped']:
        p(f"    （「{obj_name}」贴图在库里是空壳 → 借钻石贴图 + 乘色）")
    if not _fix['solid']:
        _tint = _rgb if (desat or (MAT_HINT or '').lower() in
                         ('leather', 'chain', 'netherite', 'iron', 'gold')) else None
        _nr = _rebuild_armor_mat(obj, _tint, desat)
        if _nr:
            p(f"    （{MAT_HINT}：重建 {_nr} 个材质{'（去饱和 + 乘色）' if (desat and _tint) else ('（乘色）' if _tint else '')}）")
    vs = [v.co for v in obj.data.vertices]
    if not vs:
        return None
    lmn = [min(v.x for v in vs), min(v.y for v in vs), min(v.z for v in vs)]
    lmx = [max(v.x for v in vs), max(v.y for v in vs), max(v.z for v in vs)]
    lc = mathutils.Vector(((lmn[0] + lmx[0]) / 2.0, (lmn[1] + lmx[1]) / 2.0,
                           (lmn[2] + lmx[2]) / 2.0))
    lw = lmx[0] - lmn[0]
    s = (fit_w / lw) * grow if lw > 1e-6 else 1.0
    ctr, bsz = _part_box(part_kw)
    if ctr is None:
        pb = arm.pose.bones[bone]
        ctr = (pb.head + pb.tail) / 2.0
        bsz = None
    if dz_frac and bsz is not None:
        dz = dz + dz_frac * bsz.z
    obj.parent = arm
    obj.parent_type = 'BONE'
    obj.parent_bone = bone
    # 姿态里这块骨头转了，盔甲也得跟着转（只跟 yaw：整块盔甲跟着骨骼上下颠倒更难看）
    # 骨骼姿态的整体朝向（骨架空间 → 只取 3×3 旋转）
    _rot = mathutils.Matrix.Identity(3)
    try:
        _rot = arm.pose.bones[bone].matrix.to_3x3().normalized()
    except Exception:
        pass
    # 模型 +Z 朝上 / 骨骼 +Y 沿骨：套 model→bone 的轴换算再乘骨骼朝向
    _axis = mathutils.Matrix.Rotation(math.radians(-90.0), 3, 'X')
    _b3 = (_rot @ _axis).to_4x4()
    # 模型朝向微调（物品库里的盔甲不一定是严格朝 -Y，差几度到 90 度都可能）
    if ARMOR_YAW:
        _b3 = _b3 @ mathutils.Matrix.Rotation(math.radians(ARMOR_YAW), 4, 'Z')
    obj.matrix_world = (arm.matrix_world
                        @ mathutils.Matrix.Translation(ctr + mathutils.Vector((0, 0, dz)))
                        @ _b3
                        @ mathutils.Matrix.Scale(s, 4)
                        @ mathutils.Matrix.Translation(-lc))
    bpy.context.view_layer.update()
    p(f"    ★ 盔甲「{obj_name}」→ {bone} 骨  贴合 {part_kw}"
      f"（宽 {fit_w:.2f} 格）缩放 {s:.2f}  位置 {tuple(round(v, 2) for v in ctr)}")
    return obj


# armor layer 贴图（腿/靴的形状跟材质无关，只有这张图不同）
_ARMOR_LAYER2 = {'diamond': 'diamond_2.002', 'iron': 'iron_2.002',
                 'leather': 'leather_2.002', 'gold': 'Armour:Gold2.002',
                 'netherite': 'netherite_layer_2.png'}
_ARMOR_LAYER1 = {'diamond': 'diamond_1.002', 'iron': 'iron_1.002',
                 'leather': 'leather.002', 'gold': 'Armour:Gold.002',
                 'netherite': 'netherite_layer_1.png'}
_ARMOR_KIND_WORDS = (('helmet', 'helmet'), ('helm', 'helmet'), ('帽子', 'helmet'),
                     ('chestplate', 'chest'), ('chest', 'chest'), ('胸甲', 'chest'),
                     ('leggings', 'legs'), ('legs', 'legs'), ('pants', 'legs'),
                     ('护腿', 'legs'), ('裤子', 'legs'),
                     ('boots', 'boots'), ('boot', 'boots'), ('靴子', 'boots'), ('鞋', 'boots'))
_ARMOR_MATS = ('netherite', 'diamond', 'leather', 'chain', 'iron', 'gold',
               '下界合金', '钻石', '皮革', '锁链', '铁', '金')
_MAT_ALIAS = {'下界合金': 'netherite', '钻石': 'diamond', '皮革': 'leather',
              '锁链': 'chain', '铁': 'iron', '金': 'gold'}


def _parse_armor_spec(spec):
    """把 DSH_ARMOR 解析成 [(部位, 材质)]。

    三种写法：
      1) 材质名        diamond / 钻石
      2) 部位=材质     helmet=Netherite,chest=Gold,legs=Iron,boots=Gold
      3) MC 物品 id    minecraft:diamond_helmet,diamond_chestplate,
                       diamond_leggings,diamond_boots（可以直接贴游戏里的物品 id）
    写法 1、2 里没点名的部位按「整套」补齐（1 是五件套，2 只补它提到的）。
    """
    raw = (spec or '').strip()
    if not raw or raw.lower() in ('none', 'off', '0'):
        return []
    plan = []
    seen = set()
    tokens = [t for t in raw.replace(';', ',').replace('/', ',').split(',') if t.strip()]
    _id_mode = False
    for tok in tokens:
        t = tok.strip()
        _hex = None
        if '#' in t:
            t, _hx = t.split('#', 1)
            _hex = _hex_to_rgb(_hx)
            t = t.strip()
        tl = t.lower()
        if _hex and not tl:
            ARMOR_COLOR['_default'] = _hex        # 只给了 #8B4513 → 整身都染这个色
            continue
        if '=' in t:
            k, v = t.split('=', 1)
            k = k.strip().lower()
            kind = None
            for word, kd in _ARMOR_KIND_WORDS:
                if word in k:
                    kind = kd
                    break
            mat = _strip_ns(v)
            mat = _MAT_ALIAS.get(mat, mat)
            if kind and mat:
                if _hex:
                    ARMOR_COLOR[mat] = _hex
                plan.append((kind, mat))
                seen.add(kind)
            continue
        for pre in ('minecraft:', 'mc:'):
            if tl.startswith(pre):
                tl = tl[len(pre):]
                _id_mode = True
        tl = tl.replace(' ', '_')
        mat = None
        for m in _ARMOR_MATS:
            if tl.startswith(m) or tl.endswith(m):
                mat = _MAT_ALIAS.get(m, m)
                break
        if mat is None:
            # 只给了材质名
            _m = _MAT_ALIAS.get(tl, tl)
            if _hex:
                ARMOR_COLOR[_m] = _hex
            plan.append(('all', _m))
            continue
        kind = None
        for word, kd in _ARMOR_KIND_WORDS:
            if tl.endswith(word) or ('_' + word) in tl:
                kind = kd
                break
        if kind is None:
            if _hex:
                ARMOR_COLOR[mat] = _hex
            plan.append(('all', mat))
            continue
        if _hex:
            ARMOR_COLOR[mat] = _hex
        if kind not in seen:
            plan.append((kind, mat))
            seen.add(kind)
    # 'all' → 展开成整套
    out = []
    for kind, mat in plan:
        if kind == 'all':
            for kd in ('helmet', 'chest', 'legs', 'boots'):
                if (kd, mat) not in out:
                    out.append((kd, mat))
        else:
            out.append((kind, mat))
    if _id_mode and not out:
        return []
    return out


def _strip_ns(v):
    x = (v or '').strip().lower()
    for pre in ('minecraft:', 'mc:'):
        if x.startswith(pre):
            x = x[len(pre):]
    return _MAT_ALIAS.get(x, x)


def _has(name):
    if name in _ITEM_NAMES:
        return name
    for suf in ('.001', '.002'):
        if name + suf in _ITEM_NAMES:
            return name + suf
    return None


def _wear_kind(kind, mat):
    """穿一个部位，返回挂上的对象列表。

    0.4.9：物品库里缺某个材质的模型/贴图时不再直接跳过，而是借钻石的形状 + 染色
    （chain 没有可穿戴模型、netherite 的贴图是空壳，都靠这条路）。
    """
    Mat = mat[:1].upper() + mat[1:]
    _des = mat in ('chain', 'netherite')
    got = []
    if kind == 'helmet':
        name = _has('Helmet:' + Mat) or _has('Helmet:Diamond')
        o = _wear_piece(name, 'Head', 'mesh head', 1.00, grow=1.00, dz=0.14, desat=_des)
        if o is not None:
            got.append(o)
    elif kind == 'chest':
        name = _has('Chestplate:' + Mat) or _has('Chestplate:Diamond')
        o = _wear_piece(name, 'Torso', 'mesh torso', 1.00, grow=1.02, desat=_des)
        if o is not None:
            got.append(o)
        for side, bone in (('L', 'Top Arm.L'), ('R', 'Top Arm.R')):
            an = _has('Chestplate:%s-Arm.%s' % (Mat, side)) or _has('Chestplate:Diamond-Arm.%s' % side)
            a = _wear_piece(an, bone, 'mesh arm.%s' % side.lower(),
                            0.50, grow=1.06, dz=0.42, desat=_des)
            if a is not None:
                got.append(a)
    elif kind == 'legs':
        img = _ARMOR_LAYER2.get(mat) or _ARMOR_LAYER2['diamond']
        for side, bone in (('L', 'Top Leg.L'), ('R', 'Top Leg.R')):
            name = None
            for cand in ('Leg.%s.%s' % (Mat, side), '%slegunder%s' % (Mat, side)):
                name = _has(cand)
                if name:
                    break
            name = name or 'Leg.Diamond.%s' % side
            o = _wear_piece(name, bone, 'mesh leg.%s' % side.lower(), 0.50,
                            grow=1.04, dz_frac=0.13, img_swap=img,
                            fallback_img=_ARMOR_LAYER2['diamond'], desat=_des)
            if o is not None:
                got.append(o)
    elif kind == 'boots':
        img = _ARMOR_LAYER1.get(mat) or _ARMOR_LAYER1['diamond']
        for side, bone in (('L', 'Bottom Leg.L'), ('R', 'Bottom Leg.R')):
            o = _wear_piece('Boot.Diamond.%s' % side, bone,
                            'mesh leg.%s' % side.lower(), 0.50, grow=1.06,
                            dz_frac=-0.24, img_swap=img,
                            fallback_img=_ARMOR_LAYER1['diamond'], desat=_des)
            if o is not None:
                got.append(o)
    return got


MAT_HINT = ''
if ARMOR_SPEC and ARMOR_SPEC.lower() not in ('none', 'off', '0'):
    p("\n[5.2] 盔甲（DSH_ARMOR）")
    _gcol = _hex_to_rgb(os.environ.get('DSH_ARMOR_COLOR', ''))
    if _gcol:
        ARMOR_COLOR['_default'] = _gcol
        p(f"    全局染色 DSH_ARMOR_COLOR = {os.environ.get('DSH_ARMOR_COLOR')}")
    _plan = _parse_armor_spec(ARMOR_SPEC)
    ARMOR_OBJS = []
    for _kind, _mat in _plan:
        MAT_HINT = _mat
        ARMOR_OBJS.extend(_wear_kind(_kind, _mat))
    if not ARMOR_OBJS:
        p(f"    ⚠️ 「{ARMOR_SPEC}」没穿上任何一件：检查材质名或物品 id"
          f"（可用材质 netherite / diamond / iron / gold / leather）")
    else:
        p(f"    共穿上 {len(ARMOR_OBJS)} 件")
    HELD_OBJS.extend(ARMOR_OBJS)   # 视线检测时跟角色一起临时隐藏


# ============================================================
# [5.5] 身后树林（DSH_TREES）
#   用户原话：「在后面加几颗树」「背景看不出来有森林的样子」「还是一坨也不像树」
#   上一场的做法是拿树叶贴图堆一堆方块，看着像一坨二维码 —— 因为不是树的形状。
#   这里按 MC 原版橡树/白桦/云杉的形状种：树干一柱 + 分层树冠（5×5 去角 → 3×3 → 顶）。
#   位置：人物【背后】（相机视线的远方那半边）±60°，距离/高度由 DSH_TREE_DIST / DSH_TREE_H 给。
# ============================================================
p("\n[5.5] 身后树林（DSH_TREES）")
import random as _random   # noqa: E402

TREES_N = 0
try:
    TREES_N = max(0, int(float(os.environ.get('DSH_TREES', '0') or 0)))
except Exception:
    TREES_N = 0


def _range2(s, dflt):
    try:
        parts = [float(x) for x in str(s).replace('，', ',').split(',') if x.strip() != '']
        if len(parts) >= 2:
            return (min(parts[0], parts[1]), max(parts[0], parts[1]))
    except Exception:
        pass
    return dflt


TREE_DIST = _range2(os.environ.get('DSH_TREE_DIST'), (8.0, 45.0))
DEBUG_TREES = bool(os.environ.get('DSH_DEBUG_TREES'))
TREE_H = _range2(os.environ.get('DSH_TREE_H'), (4.0, 7.0))

_SPECIES = {
    'oak': ('oak_log', 'oak_leaves'),
    'birch': ('birch_log', 'birch_leaves'),
    'spruce': ('spruce_log', 'spruce_leaves'),
}


def _ray_world(origin, direction, distance):
    """只对【世界网格】打射线。

    ★ 不能用 bpy.context.scene.ray_cast：场景里还有 5000×5000 的云层平面
      （z=137）和雾体积，往上的射线全被云挡住，法线朝下 → 全被判成"坡壁"，
      实测 120 条射线里 105 条都栽在这上面，一棵树都种不下来。
    """
    try:
        bpy.context.view_layer.update()      # matrix_world 不更新的话射线全打空
        best = None
        # wmesh 是【网格对象列表】（OBJ 导入可能拆成多个），逐个打、取最近
        for _ob in wmesh:
            inv = _ob.matrix_world.inverted()
            o = inv @ origin
            d = (inv.to_3x3() @ direction).normalized()
            hit, loc, nrm, _idx = _ob.ray_cast(o, d, distance=distance)
            if not hit:
                continue
            mw = _ob.matrix_world
            wloc = mw @ loc
            dist = (wloc - origin).length
            if best is None or dist < best[0]:
                best = (dist, wloc, (mw.to_3x3() @ nrm).normalized())
        if best is None:
            return (False, None, None)
        return (True, best[1], best[2])
    except Exception:
        return (False, None, None)


def _ground_at(x, y, fallback):
    """在 (x,y) 竖直向下打射线找地面；坡太陡 / 落差太大就不要（免得树插在山壁上）。"""
    try:
        top = fallback + 40.0
        hit, loc, nrm = _ray_world(mathutils.Vector((x, y, top)),
                                   mathutils.Vector((0.0, 0.0, -1.0)), 160.0)
        if hit and nrm.z > 0.45 and abs(loc.z - fallback) < 20.0:
            return float(loc.z)
    except Exception:
        pass
    return None


def _tree_blocks(cx, cy, base, h, species):
    """返回 [(kind, x, y, z)]；kind 为 'log' / 'leaf'。"""
    logs = [(cx, cy, base + i) for i in range(h)]
    leaves = []
    if species == 'spruce':
        # 云杉：圆锥，越往上越窄
        for dy in range(1, h + 2):
            r = max(1, (h + 1 - dy) // 2 + 1)
            yy = base + h - 1 + dy
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if abs(dx) == r and abs(dz) == r:
                        continue
                    leaves.append((cx + dx, cy + dz, yy))
        leaves.append((cx, cy, base + h + 2))
    else:
        # 橡树 / 白桦：底部 5×5 去四角，顶部 3×3，再压一层顶盖
        for dy in range(-3, 2):
            r = 2 if dy < 0 else 1
            yy = base + h + dy
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if r == 2 and abs(dx) == 2 and abs(dz) == 2:
                        continue
                    if r == 1 and dy == 1 and abs(dx) == 1 and abs(dz) == 1:
                        continue
                    leaves.append((cx + dx, cy + dz, yy))
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                leaves.append((cx + dx, cy + dz, base + h + 2))
    leaves = sorted(set(leaves))
    return ([('log',) + b for b in logs] + [('leaf',) + b for b in leaves])


def _plant_tree(cx, cy, base, h, sp):
    logb, leafb = _SPECIES[sp]
    log_top = _tex_path(logb + '_top')
    log_side = _tex_path(logb) or log_top
    leaf_tex = _tex_path(leafb)
    if not (log_side or log_top) or not leaf_tex:
        p(f"    ⚠️ 贴图库缺 {logb}_*.png / {leafb}.png → 这棵树跳过")
        return None
    mats = {
        'log_top': _mat_from_image(log_top or log_side, f"tree_{logb}_top"),
        'log_side': _mat_from_image(log_side, f"tree_{logb}_side"),
        'leaf': _mat_from_image(leaf_tex, f"tree_{leafb}", tint=_LEAF_TINT),
    }

    def kf(kind):
        def f(face):
            if kind == 'log':
                return 'log_top' if face in ('+z', '-z') else 'log_side'
            return 'leaf'
        return f

    blocks = [(float(x), float(y), float(z), kf(kind))
              for (kind, x, y, z) in _tree_blocks(cx, cy, base, h, sp)]
    ob = _build_boxes(f"tree_{sp}_{int(cx)}_{int(cy)}", blocks, mats)
    sc.objects.link(ob)
    return ob


def _cam_pos():
    """和 [8] 相机同一套算法 —— 树林要先知道机位，才能只种【看得见】的树。"""
    _a = math.radians(CAM_AZ)
    _f = mathutils.Vector((math.cos(_a), -math.sin(_a), 0.0)).normalized()
    return (mathutils.Vector((WX, WY, GROUND)) - _f * DIST
            + mathutils.Vector((0.0, 0.0, CAM_H)))


def _visible_from_cam(point, cam_to=None):
    """从机位朝 point 打射线，中途没被地形挡住才算可见。

    ★ 用户原话「背景看不出来有森林的样子」：树是种了，但全落在土坡背后，
      画面里一棵都看不到。所以种之前先做遮挡测试。
    """
    try:
        cam_p = cam_to if cam_to is not None else _cam_pos()
        d = (point - cam_p)
        dist = d.length
        if dist < 1.0:
            return True
        d = d.normalized()
        hit, _loc, _nrm = _ray_world(cam_p, d, max(0.5, dist - 1.2))
        return not hit
    except Exception:
        return True


if TREES_N > 0:
    p(f"    要种 {TREES_N} 棵；距离 {TREE_DIST[0]:.0f}-{TREE_DIST[1]:.0f} 格  "
      f"树干高 {TREE_H[0]:.0f}-{TREE_H[1]:.0f}")
    if not _TEX_DIRS:
        p("    !! 找不到方块贴图目录（DSH_MC_TEXTURES）→ 树林跳过")
    else:
        _tan_h = 18.0 / (LENS_OVR if LENS_OVR > 0 else LENS)
        _tan_v = _tan_h * (float(RES_Y) / float(RES_X))
        _upv = mathutils.Vector((0.0, 0.0, 1.0))
        _char = mathutils.Vector((WX, WY, GROUND))

        def _basis(az_deg):
            a = math.radians(az_deg)
            f = mathutils.Vector((math.cos(a), -math.sin(a), 0.0)).normalized()
            r = mathutils.Vector((f.y, -f.x, 0.0)).normalized()
            cp = (mathutils.Vector((WX, WY, GROUND)) - f * DIST
                  + mathutils.Vector((0.0, 0.0, CAM_H)))
            return f, r, cp

        def _probe(az_deg, want, seed):
            """按【画面里的像素位置】撒点，返回能种树的地面点。

            ★ 用户原话「背景看不出来有森林的样子」：105mm 长焦水平视角只有 ~19°，
              绕人物撒点等于把树种到画外。所以从机位往画面里的 (u,v) 打射线，
              落到能站树的平地才要。
            """
            f, r, cp = _basis(az_deg)
            rd = _random.Random(seed)
            got = []
            _cnt = {'ray': 0, 'miss': 0, 'slope': 0, 'behind': 0, 'near': 0,
                    'ground': 0, 'hidden': 0}
            for _ in range(want * 30):
                if len(got) >= want:
                    break
                _cnt['ray'] += 1
                side = rd.choice([-1.0, 1.0])
                # 0.10 会让树干正好插在人物后脑勺上（实测：树「种成功了」但把主体挡花）。
                # 留出 ±0.30 的主体空档，树只种在画面两侧。
                u = side * rd.uniform(0.30, 0.95)
                v = rd.uniform(-0.55, 0.92)
                dirv = (f + r * (u * _tan_h) + _upv * (v * _tan_v)).normalized()
                hit, loc, nrm = _ray_world(cp, dirv, 260.0)
                if not hit:
                    _cnt['miss'] += 1
                    continue
                if nrm.z < 0.20:
                    # 打到台地的【立面】（土坡台阶的侧面）。
                    # 这种地形从低机位看全是立面，光按"必须是顶面"筛会一棵都种不下
                    # （实测 120 条射线 105 条栽在这里）。改成：往面里退半格，
                    # 把树长到这一列的方块顶面上 —— MC 里树本来就长在台地边上。
                    px = loc.x + nrm.x * 0.6
                    py = loc.y + nrm.y * 0.6
                    cx0 = math.floor(px) + 0.5
                    cy0 = math.floor(py) + 0.5
                    gz0 = _ground_at(cx0, cy0, GROUND)
                    if gz0 is None:
                        _cnt['slope'] += 1
                        continue
                    tx, ty, gz = float(cx0), float(cy0), float(gz0)
                else:
                    tx, ty = float(loc.x), float(loc.y)
                    gz = _ground_at(tx, ty, GROUND)
                    if gz is None:
                        _cnt['ground'] += 1
                        continue
                behind = (mathutils.Vector((tx, ty, gz)) - _char).dot(f)
                if behind < 2.5 or behind > max(TREE_DIST[1], 60.0):
                    _cnt['behind'] += 1
                    continue
                # u 是「画面归一化横坐标」，不是世界距离：105mm 水平视角只有 ~19°，
                # u=0.30 在 10 格处横向才 1 米 —— 树干会贴着人物脸。按实际横向偏移卡。
                _lat = abs(u) * _tan_h * max(behind, 1.0)
                if _lat < 4.5:
                    _cnt['near'] += 1
                    continue
                if any(math.hypot(tx - a[0], ty - a[1]) < 2.4 for a in got):
                    _cnt['near'] += 1
                    continue
                # 树干底部或树冠任一能被看见就算"在画面里"
                if not (_visible_from_cam(mathutils.Vector((tx, ty, gz + 1.0)), cp)
                        or _visible_from_cam(mathutils.Vector((tx, ty, gz + 6.0)), cp)):
                    _cnt['hidden'] += 1
                    continue
                got.append((tx, ty, gz, behind, u, v))
            _PROBE_TRIES.append((round(az_deg, 1), len(got), dict(_cnt)))
            return got

        # ★ 树林不允许改机位。以前这里会为了「多找几棵能种的树」把 CAM_AZ 转 ±72°，
        #   实测直接把镜头转到土崖上（1360 万像素全是墙，主体缩成一角、审图 FAIL 38）。
        #   现在机位固定：能种几棵种几棵，不够只告警。
        _PROBE_TRIES = []
        _spots = _probe(CAM_AZ, TREES_N, 2603 + TREES_N)
        rnd = _random.Random(2603 + TREES_N)
        _fwd, _rgt, cam_p = _basis(CAM_AZ)
        p(f"    机位参考 {tuple(round(v, 1) for v in cam_p)}（只种挡不住的树）")
        planted = []
        for (tx, ty, gz, behind, u, v) in _spots:
            sp = rnd.choice(['oak', 'oak', 'birch', 'spruce'])
            h = int(round(rnd.uniform(*TREE_H)))
            ob = _plant_tree(tx, ty, gz, h, sp)
            if ob is None:
                continue
            planted.append((tx, ty))
            p(f"    树 {len(planted)}: {sp} 高 {h}  位置 ({tx:.1f}, {ty:.1f})  "
              f"地面 z={gz:.2f}  在人物后方 {behind:.1f} 格  画面 (u={u:+.2f}, v={v:+.2f})")
        if len(planted) < TREES_N:
            p(f"    ⚠️ 只种下 {len(planted)}/{TREES_N} 棵"
              f"（这个机位背后能站树的画面位置不够）")
            if DEBUG_TREES:
                for _azd, _n, _c in _PROBE_TRIES:
                    p(f"      方位 {_azd}° → {_n} 棵  射线 {_c['ray']}："
                      f"打空 {_c['miss']} / 坡壁 {_c['slope']} / 距离 {_c['behind']} / "
                      f"太挤 {_c['near']} / 无地面 {_c['ground']} / 被挡 {_c['hidden']}")
else:
    p("    (DSH_TREES=0，不种树)")

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
    mo.rotation_euler = (0, 0, math.radians(cfg["yaw"] + _YAW_DELTA))
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
        # ★ 标定过的换算（别改成 ±90，会差 95°）：实测 sun_rotation=R 时
        #   日轮出现在"相机 MC 方位角 R+270"的方向（R=0→270, R=90→0，两点验证）。
        #   要让日轮落在 MC 方位 SUN_ROT，就得 sun_rotation = SUN_ROT - 270。
        #   ⚠️ 太阳【灯】的朝向公式完全不同（见下面 rotation_euler），
        #   两边必须各自按实测换算，否则日轮和投影方向会差出 100° 以上。
        sky.sun_rotation = math.radians(SUN_ROT - 270.0)
        sky.sun_disc = True
        # ★ 5.2 的属性名（优化方案 P0-3）：`dust_density` 早就不存在了，
        #   正确名字是 `aerosol_density`。逐项 try + 逐项记日志，
        #   避免下次改名又被静默吞掉。
        for attr, val in (("altitude", 40.0), ("air_density", 1.25),
                          ("aerosol_density", 0.45), ("ozone_density", 1.0),
                          ("ground_albedo", 0.30), ("sun_intensity", 3.0),
                          ("sun_size", math.radians(1.8))):
            try_set(sky, attr, val, f"sky.{attr}")
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
# ★ 夜晚（v0.5.0）——「背景不能黑屏」
#   物理天空在太阳落到地平线以下时给的是近乎全黑，
#   用户看到的就是「人物后面一块黑」。夜里改成深蓝夜色，
#   并把天空强度抬到不至于让背景死黑的下限。
if HOUR is not None and SUN_ELEV < -1.0:
    for _lk in list(bg.inputs['Color'].links):
        nt.links.remove(_lk)
    bg.inputs['Color'].default_value = (0.035, 0.055, 0.115, 1)
    SKY = max(SKY, 0.35)
    kind = (kind or '平涂蓝') + "→夜色"
    p(f"    ★ 时段 {HOUR:.1f} 点 = 夜晚 → 改深蓝夜色（不接物理天空，避免背景全黑）")
if HOUR is not None:
    p(f"    ★ 时段 DSH_HOUR={HOUR:.1f} → 太阳仰角 {SUN_ELEV:+.1f}°  "
      f"灯强度 {SUN_INT:.2f}  色 {tuple(round(c,2) for c in SUN_COLOR)}")
# ⚠️ 坑 53/57：视图转换与天空强度必须配套 ——
#   「标准」没有高光压缩，物理天空（辐射度量）哪怕强度 0.12 也会削顶成白，
#   整个动态范围被天空占满，三点布光完全显不出来（= "背景死白 + 没有光源"）。
#   → 最终渲染用 AgX（默认），天空强度可以放到 0.09；
#     但只要 VT 是 Standard 就必须钳到 ≤0.05，写成硬约束。
if str(VT).strip().lower() == 'standard' and SKY > 0.05:
    p(f"    ⚠️ 视图转换 = Standard → 天空强度 {SKY} 钳到 0.05（否则削顶死白）")
    SKY = 0.05
bg.inputs['Strength'].default_value = SKY
nt.links.new(bg.outputs['Background'], out.inputs['Surface'])
sd = bpy.data.lights.new("SunKey", type='SUN')
sd.energy = SUN_INT
sd.angle = math.radians(SUN_ANGLE)
sd.color = SUN_COLOR
so = bpy.data.objects.new("SunKey", sd)
bpy.context.scene.collection.objects.link(so)
# ★ 太阳灯朝向：让光线从 MC 方位 SUN_ROT 打过来。
#   推导：rotation_euler=(90-elev, 0, rz) 时，光的行进方向是
#   (-sin rx·sin rz, sin rx·cos rz, -cos rx)；要它等于 -(cos φ, -sin φ)，
#   解出 rz = 90 - φ。原来写成 φ+90，实测日轮在 340° 方向、
#   灯却在 20° 方向（差 140°），人物是"侧逆光却投正影"，必须一起改。
so.rotation_euler = (math.radians(90 - SUN_ELEV), 0, math.radians(90 - SUN_ROT))
p(f"    {kind or '平涂蓝'}  太阳仰角 {SUN_ELEV:.1f}° 方位 {SUN_ROT:.0f}°  "
  f"灯强度 {SUN_INT} 色 {SUN_COLOR} 盘角 {SUN_ANGLE}°  天空强度 {SKY}")
p(f"    视图转换 = {VT}（给高光留压缩余量，布光才显形）")

# ---- 云层（MC 的天上是有云的，只有纯渐变天空会显得"世界是假的"）----
#   MC 的云在 y≈128（地表 64 上方 64 格）。做成一块巨大的半透明噪声平面：
#   1) 用 3D Noise 的 Fac 经 ColorRamp 切成软边团块，喂给 Principled 的 Alpha；
#   2) 云面不投影（visible_shadow=False），否则会在主角身上压出一大片假阴影；
#   3) 尺寸给 900 格，保证 105mm 长焦下地平线附近也有云，而不是只有头顶几团。
p("\n[6.5] 云层（MC 风格噪声云）")
cloud_ok = False
try:
    if CLOUDS_ON:
        # ⚠️ 云面必须铺得极远：105mm 竖直视野约 11°，450 格远的地方才升到 43 格高，
        #    所以 900 格/72 格高的云整块都在画面外（实测 A/B 两张图完全一样）。
        #    改成 5000 格 + 地表 +62，远处云才会压在地平线附近。
        bpy.ops.mesh.primitive_plane_add(size=5000.0,
                                         location=(WX, WY, GROUND + 62.0))
        cl = bpy.context.active_object
        cl.name = "CloudLayer"
        cm = bpy.data.materials.new("CloudMat")
        cm.use_nodes = True
        c_nt = cm.node_tree
        c_bsdf = c_nt.nodes.get("Principled BSDF")
        c_bsdf.inputs['Base Color'].default_value = (1.0, 0.985, 0.96, 1.0)
        c_bsdf.inputs['Roughness'].default_value = 1.0
        c_bsdf.inputs['Specular IOR Level'].default_value = 0.0 if 'Specular IOR Level' in c_bsdf.inputs else 0.0
        c_noise = c_nt.nodes.new("ShaderNodeTexNoise")
        c_noise.inputs['Scale'].default_value = 0.006
        c_noise.inputs['Detail'].default_value = 8.0
        c_noise.inputs['Roughness'].default_value = 0.62
        c_ramp = c_nt.nodes.new("ShaderNodeValToRGB")
        c_ramp.color_ramp.interpolation = 'EASE'
        c_ramp.color_ramp.elements[0].position = 0.46
        c_ramp.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
        c_ramp.color_ramp.elements[1].position = 0.58
        c_ramp.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
        c_nt.links.new(c_noise.outputs['Fac'], c_ramp.inputs['Fac'])
        c_nt.links.new(c_ramp.outputs['Color'], c_bsdf.inputs['Alpha'])
        # EEVEE Next(4.2+/5.x) 用 surface_render_method，老版本用 blend_method
        blended = False
        for attr, val in (('surface_render_method', 'BLENDED'),
                          ('blend_method', 'BLEND')):
            try:
                setattr(cm, attr, val)
                blended = True
                break
            except Exception:
                continue
        if not blended:
            p("    ⚠️ 这个版本设不了透明混合 → 云层跳过（不影响出图）")
            bpy.data.objects.remove(cl, do_unlink=True)
            raise RuntimeError('no blend mode')
        cm.use_backface_culling = False
        cl.data.materials.append(cm)
        try_set(cl, 'visible_shadow', False, 'cloud.visible_shadow')
        cloud_ok = True
        p(f"    云层 y={GROUND + 62.0:.0f}（地表 +62）尺寸 5000 格 噪声 scale 0.006")
    else:
        p("    (DSH_CLOUDS=0 / DSH_MC_CLOUDS=0，无云)")
except Exception as e:
    p(f"    ⚠️ 云层构建失败({type(e).__name__}: {str(e)[:120]}) → 继续，无云")

# ---- 相机基向量（灯和相机共用同一套，先算出来）----
# 相机相对【世界位置】摆放。视线方位角用 MC 角度给（和地表扫描同一套坐标），
# 免得我自己换算错：MC(x,z) → Blender(x,-z)，所以方向 (mx,mz) → (mx,-mz)。
_az = math.radians(CAM_AZ)
_mc = (math.cos(_az), math.sin(_az))
fw = mathutils.Vector((_mc[0], -_mc[1], 0.0)).normalized()   # 视线水平方向
_right = mathutils.Vector((fw.y, -fw.x, 0.0)).normalized()   # 视线右方
up = mathutils.Vector((0.0, 0.0, 1.0))

# 角色中心 C（灯都绕它摆）。身高用【求值后】包围盒，含举臂。
_cmn, _cmx = eval_bbox(body)
CH_H = float(_cmx.z - _cmn.z) if (_cmn is not None and _cmx is not None) else 1.8
C = mathutils.Vector((WX, WY, GROUND + CH_H * 0.52))
p(f"    角色中心 C = {tuple(round(v, 2) for v in C)}（身高 {CH_H:.2f}）")

# ---- T48 三点布光（★ 相机相对，优化方案 P0-4）----
#   原来的三盏灯是【世界轴绝对坐标】+ 写死欧拉角：CAM_AZ 一变，Key 变侧逆光、
#   Rim 变正面光，同一套灯在不同机位下含义完全不同（az290 实测到的）。
#   现在全部用 fw/_right/up 的相机基向量摆位，朝向直接朝角色中心。
p("\n[7] T48 三点布光（相机相对）")
for o in list(bpy.data.objects):
    if o.type == 'LIGHT' and o.name.split('.')[0] in ('Key', 'Fill', 'Rim'):
        bpy.data.objects.remove(o, do_unlink=True)


def area(name, loc, energy, size, color, vol, target, scale=1.0):
    ld = bpy.data.lights.new(name, type='AREA')
    ld.energy = energy * scale
    ld.shape = 'SQUARE'
    ld.size = size
    ld.color = color
    for attr, val in (('use_shadow_jitter', True), ('shadow_jitter_overblur', 12.0),
                      ('shadow_filter_radius', 2.0), ('volume_factor', vol)):
        try_set(ld, attr, val, f'{name}.{attr}')
    ob = bpy.data.objects.new(name, ld)
    bpy.context.scene.collection.objects.link(ob)
    ob.location = loc
    ob.rotation_euler = (target - loc).to_track_quat('-Z', 'Y').to_euler()
    return ob


_KEY = C - fw * 1.15 + _right * 2.6 + up * 2.7
_FILL = C - fw * 3.2 - _right * 2.6 + up * 1.2
# ★ 逆光封面靠轮廓光把人物从天空里"抠"出来：往镜头侧收、贴肩线打。
_RIM = C + fw * 1.70 - _right * 1.20 + up * 2.90
area("Key", _KEY, 320.0, 1.7, (1.00, 0.86, 0.70), 0.10, C + up * 0.35, KEY_SCALE)
area("Fill", _FILL, 45.0, 2.6, (0.62, 0.76, 1.00), 0.00, C, KEY_SCALE)
area("Rim", _RIM, 1500.0, 0.8, (0.78, 0.86, 1.00), 0.00, C + up * 0.55, RIM_SCALE)
p(f"    Key {320*KEY_SCALE:.0f}W s1.7 / Fill {45*KEY_SCALE:.0f}W s2.6 / "
  f"Rim {1500*RIM_SCALE:.0f}W s0.8（KEY_SCALE {KEY_SCALE} / RIM_SCALE {RIM_SCALE}）光比≈7:1")
p(f"    Key 位置 {tuple(round(v, 2) for v in _KEY)}  "
  f"Rim 位置 {tuple(round(v, 2) for v in _RIM)}")

# ---- 相机 ----
p("\n[8] 相机")
cam_d = bpy.data.cameras.new("Cam")
cam_d.lens = (LENS_OVR if LENS_OVR > 0 else LENS)
cam = bpy.data.objects.new("Cam", cam_d)
bpy.context.scene.collection.objects.link(cam)
bpy.context.scene.camera = cam
# 相机相对【世界位置】摆放。fw/_right 已在三点布光前算好（同一套坐标）。
cam.location = (mathutils.Vector((WX, WY, GROUND))
                - fw * DIST
                + up * CAM_H)
# 看向点往视线右方偏 → 角色落在画面左侧，右侧留白给文字
look = (mathutils.Vector((WX, WY, GROUND + AIM_H)) + _right * AIM_SIDE)
cam.rotation_euler = (look - cam.location).to_track_quat('-Z', 'Y').to_euler()
p(f"    {LENS:.0f}mm  视线方位 {CAM_AZ:.0f}°(MC)  距离 {DIST}")
p(f"    机位高 {CAM_H}  看向高 {AIM_H}  侧移 {AIM_SIDE}"
  f"（角色在左、右侧留白给文字）")
p(f"    位置 {tuple(round(v,2) for v in cam.location)}  "
  f"看向 {tuple(round(v,2) for v in look)}")

# ★ 景深（优化方案 P1-2）：f/2.2，对焦【头部网格】。
#   实测坑：BPS 的网格对象原点不一定在头上（可能还挂在骨架原点），
#   直接 focus_object 会让焦点落在空气里。所以先试 focus_object，
#   若对象原点离头部包围盒中心太远，就退回 focus_distance（几何算出来）。
_head = next((o for o in body
              if 'head' in o.name.lower() and 'eyebrow' not in o.name.lower()), None)
_hb = eval_bbox([_head]) if _head else (None, None)
if _hb[0] is not None:
    _head_c = (_hb[0] + _hb[1]) / 2.0
else:
    _head_c = C + up * (CH_H * 0.28)
cam_d.dof.use_dof = True
# ★ 背景虚化强度（v0.5.0）：DSH_BG_BLUR>0 时把光圈再开大（f 值除以倍率），
#   给「人物很实、背后树林化开」这种壁纸感；0 = 维持原来的 f/1.4。
if BG_BLUR > 0:
    _fstop_eff = max(0.4, FSTOP / max(0.25, BG_BLUR))
    p(f"    背景虚化倍率 {BG_BLUR:g} → f/{_fstop_eff}（原 f/{FSTOP}）")
else:
    _fstop_eff = FSTOP
cam_d.dof.aperture_fstop = _fstop_eff
try:
    cam_d.dof.aperture_blades = 6
except Exception:
    pass
_dof_src = 'focus_distance'
if _head is not None:
    try:
        cam_d.dof.focus_object = _head
        if (_head.matrix_world.translation - _head_c).length < 1.5:
            _dof_src = f'focus_object={_head.name}'
        else:
            cam_d.dof.focus_object = None
    except Exception as e:
        p(f"    ⚠️ DOF 焦点对象设置失败: {type(e).__name__}: {str(e)[:100]}")
if cam_d.dof.focus_object is None:
    cam_d.dof.focus_distance = max(0.5, (cam.location - _head_c).length)
p(f"    DOF f/{cam_d.dof.aperture_fstop}  对焦 {_dof_src}  "
  f"距离 {cam_d.dof.focus_distance:.2f}  头心 "
  f"{tuple(round(v,2) for v in _head_c)}")

# 视线检测（坑 41：先隐藏角色与剑）
bpy.context.view_layer.update()
stash = []
for o in list(char) + list(HELD_OBJS):
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

# ============================================================
# ---- 体积雾（优化方案 P1-1）----
#   ⚠️ 世界体积（world volume）在 5.2 里【必黑】，实测过，不要用。
#   这里做的是【有限体积盒】：一个包住角色周围空间的立方体，
#   挂只含 Volume 输出的材质。任何一步异常 → 整体回退成"无雾"，
#   绝不让雾把渲染搞挂（渲染失败比没雾严重得多）。
# ============================================================
p("\n[8.5] 体积雾（有限体积盒）")
fog_ok = False
fog_box = None
if not FOG_ON:
    p("    (DSH_MC_FOG=0，无雾)")
else:
    try:
        _fzh = 75.0
        _fzc = C + fw * 2.0
        bpy.ops.mesh.primitive_cube_add(
            size=2.0, location=(_fzc.x, _fzc.y, GROUND - 1.0 + _fzh))
        fog_box = bpy.context.active_object
        fog_box.name = "FogVolume"
        fog_box.scale = (_fzh, _fzh, _fzh)
        _fmt = bpy.data.materials.new("FogVol")
        _fmt.use_nodes = True
        _fnt = _fmt.node_tree
        for n in list(_fnt.nodes):
            _fnt.nodes.remove(n)
        _fo = _fnt.nodes.new("ShaderNodeOutputMaterial")
        _fv = _fnt.nodes.new("ShaderNodeVolumePrincipled")
        _fv.inputs['Color'].default_value = (0.80, 0.88, 1.00, 1.0)
        _fv.inputs['Density'].default_value = 0.0012 * max(0.0, FOG_MULT)
        _fv.inputs['Anisotropy'].default_value = 0.45
        _fnt.links.new(_fv.outputs['Volume'], _fo.inputs['Volume'])
        fog_box.data.materials.append(_fmt)
        try_set(fog_box, 'visible_shadow', False, 'FogVolume.visible_shadow')
        try_set(fog_box, 'hide_select', True, 'FogVolume.hide_select')
        fog_ok = True
        p(f"    体积盒 {_fzh*2:.0f}×{_fzh*2:.0f}×{_fzh*2:.0f} "
          f"密度 {0.0012 * max(0.0, FOG_MULT):.4f}（倍率 {FOG_MULT:g}）"
          f" 各向异性 0.45 色 (0.80,0.88,1.00)")
    except Exception as e:
        p(f"    ⚠️ 建雾失败({type(e).__name__}: {str(e)[:140]}) → 回退无雾")
        if fog_box is not None:
            try:
                bpy.data.objects.remove(fog_box, do_unlink=True)
            except Exception:
                pass
            fog_box = None
        fog_ok = False

# ============================================================
# ---- 渲染设置 ----
# ============================================================
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

# ★ P0-1 色彩管理：视图转换 / look / 曝光
vt = set_view_transform(scn, prefer=VT)
if str(vt) != str(VT):
    p(f"    ⚠️ 视图转换 {VT!r} 不可用 → 回落 {vt!r}")
scn.view_settings.exposure = EXPOSURE
look_applied = None
for _lk in (LOOK, 'AgX - Punchy', 'None'):
    try:
        scn.view_settings.look = _lk
        look_applied = _lk
        break
    except Exception as e:
        p(f"    ⚠️ look {_lk!r} 不可用: {type(e).__name__}: {str(e)[:80]}")
if look_applied is None:
    look_applied = 'None'
# 若最终落到 Standard，天空强度再钳一次（前面那次是按入参 VT 判的）
if str(vt).strip().lower() == 'standard':
    try:
        _cur = float(bg.inputs['Strength'].default_value)
        if _cur > 0.05:
            bg.inputs['Strength'].default_value = 0.05
            p(f"    ⚠️ 实际视图转换=Standard → 天空强度 {_cur:.3f} 钳到 0.05")
    except Exception:
        pass

# ★ P1-3 接触阴影 / AO：EEVEE 的 ray tracing + fast GI
_ee = scn.eevee
for attr, val in (('use_raytracing', True), ('use_fast_gi', True),
                  ('use_bokeh_jittered', True)):
    try_set(_ee, attr, val, f'eevee.{attr}')
_ro = getattr(_ee, 'ray_tracing_options', None)
if _ro is not None:
    for attr, val in (('resolution_scale', '1'), ('use_denoise', True),
                      ('trace_max_roughness', 0.5)):
        try_set(_ro, attr, val, f'ray_tracing.{attr}')
else:
    p("    ⚠️ 没有 ray_tracing_options（版本差异），跳过接触阴影细分参数")
_fg = getattr(_ee, 'fast_gi_options', None)
if _fg is not None:
    for attr, val in (('method', 'GLOBAL_ILLUMINATION'), ('distance', 3.0),
                      ('quality', 0.6), ('thickness', 0.3),
                      ('ray_count', 8), ('step_count', 8)):
        try_set(_fg, attr, val, f'fast_gi.{attr}')
else:
    # ★ 5.2 把 AO/GI 参数摊平成 eevee.fast_gi_* —— 没有 fast_gi_options 这个容器。
    #   原来只设 use_fast_gi=True，但 fast_gi_distance 默认 0 = 【关闭】，
    #   于是接触阴影从来没出现过（脚像贴纸的直接原因）。
    for attr, val in (('fast_gi_method', 'GLOBAL_ILLUMINATION'),
                      ('fast_gi_distance', 3.0), ('fast_gi_quality', 0.6),
                      ('fast_gi_ray_count', 8), ('fast_gi_step_count', 8),
                      ('fast_gi_thickness_near', 0.3), ('fast_gi_resolution', '1')):
        try_set(_ee, attr, val, f'eevee.{attr}')
    p(f"    AO/接触阴影: fast_gi_distance={getattr(_ee, 'fast_gi_distance', '?')} "
      f"ray={getattr(_ee, 'fast_gi_ray_count', '?')} "
      f"res={getattr(_ee, 'fast_gi_resolution', '?')}")

# ★ P1-1 体积参数（tile_size 是字符串枚举：'2','4','8','16'）
if fog_ok:
    for attr, val in (('volumetric_start', 0.4), ('volumetric_end', 200.0),
                      ('volumetric_samples', 96),
                      ('volumetric_sample_distribution', 0.85),
                      ('volumetric_light_clamp', 0.0),
                      ('use_volumetric_shadows', True),
                      ('volumetric_tile_size', '4')):
        try_set(_ee, attr, val, f'eevee.{attr}')

scn.render.filepath = os.path.join(OUT, "封面_世界")
scn.render.image_settings.file_format = 'PNG'

# ★ P0-2 后期合成（Bloom + 分级 + 暗角）：封面链路以前一个后期节点都没有，
#   EEVEE 5.x 又删了 use_bloom，所以直出就是「均匀发灰」的截图感。
#   post_grade.py 自己降级，失败只打日志，不影响出图。
try:
    from post_grade import setup_post_grade
    setup_post_grade(scn, log=p)
except Exception as _e:
    p(f"    ⚠️ 后期不可用({type(_e).__name__}: {str(_e)[:90]}) → 直出")

p(f"    {scn.render.engine} / {SAMPLES} 采样 / 视图转换={vt} / look={look_applied} / "
  f"曝光{EXPOSURE:+.2f} / {RES_X}x{RES_Y}")


# ============================================================
# ---- P0-0 审图框：把角色身体包围盒投影成像素框 ----
#   pipeline 会把这个框传给 vision_check，让它"在框里判是什么"，
#   而不是按颜色满画面找主体（大草原背景绿/天空蓝，颜色判据不可靠）。
# ============================================================
def project_subject_box():
    """返回 (box, raw, notes)。box 为 [x0,y0,x1,y1] 像素（左上原点）。"""
    try:
        from bpy_extras.object_utils import world_to_camera_view
    except Exception as e:
        return None, None, [f"world_to_camera_view 不可用: {e}"]
    bmin, bmax = eval_bbox(body)
    if bmin is None:
        return None, None, ["身体包围盒取不到"]
    notes = []
    us = []
    vs = []
    for cx in (bmin.x, bmax.x):
        for cy in (bmin.y, bmax.y):
            for cz in (bmin.z, bmax.z):
                co = world_to_camera_view(scn, cam, mathutils.Vector((cx, cy, cz)))
                if co.z <= 0:
                    notes.append("有角点在相机后方（z<=0）")
                us.append(co.x)
                vs.append(co.y)
    u0, u1 = min(us), max(us)
    v0, v1 = min(vs), max(vs)
    raw = [round(u0 * RES_X, 1), round((1.0 - v1) * RES_Y, 1),
           round(u1 * RES_X, 1), round((1.0 - v0) * RES_Y, 1)]
    x0, y0, x1, y1 = raw
    # 轴对齐盒含空气（举臂/侧身时更明显）→ 向内收 2%/边 补偿
    ix = (x1 - x0) * 0.02
    iy = (y1 - y0) * 0.02
    box = [x0 + ix, y0 + iy, x1 - ix, y1 - iy]
    # 夹到画面内
    box = [min(max(0.0, box[0]), RES_X), min(max(0.0, box[1]), RES_Y),
           min(max(0.0, box[2]), RES_X), min(max(0.0, box[3]), RES_Y)]
    box = [int(round(v)) for v in box]
    if box[2] - box[0] < 4 or box[3] - box[1] < 4:
        return None, raw, notes + [f"投影框太小 {box}"]
    return box, raw, notes


_subject_box = None
_subject_raw = None
_subject_notes = []
if not body:
    _subject_notes.append("没有身体网格（空镜）→ 不投影主体框")
else:
    _subject_box, _subject_raw, _subject_notes = project_subject_box()
if _subject_notes:
    for _n in _subject_notes:
        p(f"    ⚠️ 审图框: {_n}")
p(f"    主体框 {_subject_box}  （原始投影 {_subject_raw}，画面 {RES_X}x{RES_Y}）")
try:
    with open(os.path.join(OUT, '_subject_box.json'), 'w', encoding='utf-8') as f:
        json.dump({'subject_box': _subject_box, 'raw': _subject_raw,
                   'size': [RES_X, RES_Y],
                   'source': 'world_to_camera_view(body eval_bbox)',
                   'notes': _subject_notes}, f, ensure_ascii=False, indent=2)
except Exception as e:
    p(f"    ⚠️ 写 _subject_box.json 失败: {e}")

blend = os.path.join(OUT, "封面_世界.blend")
bpy.ops.wm.save_as_mainfile(filepath=blend)
p(f"\n工程: {blend}")
finish(0)
