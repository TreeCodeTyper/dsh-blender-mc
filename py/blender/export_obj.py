# -*- coding: utf-8 -*-
"""mc_导出OBJ —— 把 Minecraft 存档转成标准 Wavefront OBJ + MTL

分工说明（这一版的核心）：
  本工具【只负责取出来】，产出业界标准格式：
      世界.obj / 世界.mtl / tex/*.png
  之后交给 **Blender 自己的 obj 导入器** + MCprep 的材质工具处理。
  本工具不做任何 Blender 相关的事（不建节点树、不算机位、不管光照）。

染色在这里【烘进贴图】：MC 的草/树叶贴图是灰度的，绿色来自运行时群系染色。
烘好后 Blender 侧就是普通 PNG，一条节点连线都不用加。

用法:
    python mc_导出OBJ.py <存档目录> <输出目录> [选项]
      --radius N     以角色为中心的水平半径（默认 40）
      --ymin/--ymax  高度范围（默认 -16 / 80）
      --horizon N    远处地形 LOD 半径（默认 0=关）
      --center x,z   指定中心（缺省用玩家位置）
      --no-cull      不做隐藏面剔除
"""
import argparse
import math
import os
import shutil
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 插件里模块叫 anvil.py（原来是 mc_anvil.py），并且它在上一级 py/ 目录
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from anvil import World, read_player                  # noqa: E402              # noqa: E402

TEX_SRC = r"E:\游戏与自媒体\AI自媒体工作区\03_素材库\方块贴图\minecraft\block"

# 群系染色（MC 官方 tint 表，平原/森林系）
TINT = {
    'grass_block': (0.569, 0.741, 0.349), 'grass_block_top': (0.569, 0.741, 0.349),
    'grass_block_side': (0.569, 0.741, 0.349),
    'short_grass': (0.569, 0.741, 0.349), 'tall_grass': (0.569, 0.741, 0.349),
    'fern': (0.569, 0.741, 0.349), 'large_fern': (0.569, 0.741, 0.349),
    'oak_leaves': (0.467, 0.671, 0.184), 'jungle_leaves': (0.184, 0.792, 0.153),
    'acacia_leaves': (0.573, 0.737, 0.114), 'dark_oak_leaves': (0.341, 0.596, 0.161),
    'mangrove_leaves': (0.541, 0.714, 0.353), 'birch_leaves': (0.502, 0.655, 0.333),
    'spruce_leaves': (0.380, 0.608, 0.451),
    'vine': (0.467, 0.671, 0.184), 'sugar_cane': (0.569, 0.741, 0.349),
    'lily_pad': (0.126, 0.502, 0.153), 'pink_petals': (0.569, 0.741, 0.349),
    'water': (0.247, 0.463, 0.894), 'water_still': (0.247, 0.463, 0.894),
    'bubble_column': (0.247, 0.463, 0.894),
}

SPECIAL = {
    'grass_block': 'grass_block_side', 'lava': 'lava_still', 'water': 'water_still',
    'mycelium': 'mycelium_side', 'podzol': 'podzol_side', 'dirt_path': 'dirt_path_top',
    'magma_block': 'magma', 'fire': 'fire_0', 'soul_fire': 'soul_fire_0',
    'redstone_wire': 'redstone_dust_line0', 'cocoa': 'cocoa_stage2',
}
SHAPE_SUFFIX = ('_stairs', '_slab', '_wall', '_fence', '_fence_gate', '_door',
                '_trapdoor', '_button', '_pressure_plate', '_sign', '_wall_sign',
                '_hanging_sign', '_carpet', '_pane', '_bars', '_rod', '_chain',
                '_lantern', '_torch', '_candle', '_bed', '_banner', '_skull', '_head')
TOPBLOCKS = ('grass_block', 'mycelium', 'podzol', 'dirt_path', 'farmland',
             'sandstone', 'log', 'stem', 'hyphae', 'basalt', 'deepslate',
             'quartz_block', 'bone_block', 'hay_block', 'barrel', 'furnace',
             'smoker', 'crafting_table', 'bee_nest', 'beehive', 'sculk',
             'melon', 'pumpkin', 'chiseled_bookshelf')
TINY_SKIP = ('torch', '_sign', 'banner', 'button', 'lever', '_rail', 'tripwire',
             'redstone_wire', 'redstone_dust', '_flower', 'sapling', 'potted_',
             'short_grass', 'tall_grass', '_fern', 'dead_bush', 'warped_roots',
             'crimson_roots', 'hanging_roots', 'nether_sprouts', 'wheat',
             'carrots', 'potatoes', 'beetroots', 'sweet_berry_bush', 'kelp',
             'seagrass', 'vine', 'lily_pad', 'string', 'cave_vines', '_door',
             '_trapdoor', '_fence', '_pane', '_bars', 'chain', 'candle',
             'lantern', 'cocoa', 'nether_wart', 'pink_petals', 'spore_blossom',
             'dripleaf', 'glow_lichen', 'sculk_vein', 'frogspawn')
THIN_EXACT = ('snow', 'snow_layer')


def _exists(n):
    return os.path.isfile(os.path.join(TEX_SRC, n + '.png'))


def resolve_texture(name):
    n = name.split(':', 1)[-1]
    if n in SPECIAL and _exists(SPECIAL[n]):
        return SPECIAL[n]
    cands = [n, n + '_side', n + '_top', n + '_still', n + '_front']
    for pre in ('_carpet', '_bed', '_banner', '_wall_banner', '_wool'):
        if n.endswith(pre):
            b = n[:-len(pre)]
            cands += [b + '_wool', b, b + '_concrete', b + '_terracotta']
            break
    if n.endswith('_wood'):
        cands += [n[:-5] + '_log', n[:-5] + '_log_side']
    if n.endswith('_hyphae'):
        cands += [n[:-7] + '_stem', n[:-7] + '_stem_side']
    for suf in SHAPE_SUFFIX:
        if n.endswith(suf):
            b = n[:-len(suf)]
            cands += [b, b + 's', b + '_planks', b + '_side', b + '_top',
                      b + 's_side', b + '_block']
            if b.endswith('s'):
                cands += [b[:-1], b[:-1] + '_planks']
            break
    cands.append(n[:-1] if n.endswith('s') else n + 's')
    for c in cands:
        if c and _exists(c):
            return c
    return None


def resolve_textures(name):
    n = name.split(':', 1)[-1]
    side = resolve_texture(name)
    top = bottom = side
    for k in TOPBLOCKS:
        if k in n:
            if _exists(n + '_top'):
                top = n + '_top'
            if _exists(n + '_bottom'):
                bottom = n + '_bottom'
            break
    return {'top': top, 'side': side, 'bottom': bottom}


def block_extent(name, props):
    n = name.split(':', 1)[-1]
    if n in ('air', 'cave_air', 'void_air'):
        return None
    if n in THIN_EXACT:
        return (0.0, 0.125)
    for k in TINY_SKIP:
        if k in n:
            return None
    if 'pressure_plate' in n or 'carpet' in n or 'tripwire_hook' in n:
        return (0.0, 0.0625)
    if n.endswith('_slab'):
        t = (props or {}).get('type', 'bottom')
        if t == 'top':
            return (0.5, 1.0)
        if t == 'double':
            return (0.0, 1.0)
        return (0.0, 0.5)
    return (0.0, 1.0)


def tint_of(block_name):
    n = block_name.split(':', 1)[-1]
    if n in TINT:
        return TINT[n]
    if n.endswith('_leaves'):
        return (0.380, 0.608, 0.451)
    return None


# 六个方向 + 该面四个角（0/1 局部坐标）
FACES = (
    ((1, 0, 0), ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1)), 'side'),
    ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)), 'side'),
    ((0, 1, 0), ((0, 1, 1), (1, 1, 1), (1, 1, 0), (0, 1, 0)), 'top'),
    ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)), 'bottom'),
    ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)), 'side'),
    ((0, 0, -1), ((1, 0, 0), (0, 0, 0), (0, 1, 0), (1, 1, 0)), 'side'),
)


def bake_textures(needed, outdir):
    """把用到的贴图复制/染色到 tex/。染色在这里一次烘好，
    Blender 侧就是普通 PNG —— 一条节点连线都不用加。"""
    from PIL import Image
    texdir = os.path.join(outdir, 'tex')
    os.makedirs(texdir, exist_ok=True)
    cache = {}
    for texname, tint in needed:
        key = texname + ('__t' if tint else '')
        if key in cache:
            continue
        src = os.path.join(TEX_SRC, texname + '.png')
        dst = os.path.join(texdir, key + '.png')
        if not os.path.isfile(src):
            cache[key] = None
            continue
        if tint:
            im = Image.open(src).convert('RGBA')
            px = im.load()
            w, h = im.size
            for x in range(w):
                for y in range(h):
                    r, g, b, a = px[x, y]
                    px[x, y] = (int(r * tint[0]), int(g * tint[1]),
                                int(b * tint[2]), a)
            im.save(dst)
        else:
            shutil.copyfile(src, dst)
        cache[key] = key + '.png'
    return cache


_ALPHA_CACHE = {}


def tex_has_alpha(texname):
    """这张贴图里有没有真镂空（alpha < 250 的像素）。
    方块贴图都是 16×16，读一遍很便宜，结果缓存。"""
    if texname in _ALPHA_CACHE:
        return _ALPHA_CACHE[texname]
    ok = False
    try:
        from PIL import Image
        src = os.path.join(TEX_SRC, texname + '.png')
        if os.path.isfile(src):
            im = Image.open(src)
            if 'A' in im.getbands():
                ok = im.convert('RGBA').split()[3].getextrema()[0] < 250
    except Exception:
        ok = False
    _ALPHA_CACHE[texname] = ok
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('save')
    ap.add_argument('outdir')
    ap.add_argument('--radius', type=int, default=40)
    ap.add_argument('--ymin', type=int, default=-16)
    ap.add_argument('--ymax', type=int, default=80)
    ap.add_argument('--horizon', type=int, default=0)
    ap.add_argument('--center', default=None)
    ap.add_argument('--name', default='世界')
    ap.add_argument('--no-cull', action='store_true')
    ap.add_argument('--bake-tint', action='store_true',
                    help='把群系染色烘进贴图。默认关——MCprep 会自己做，'
                         '烘了反而让它按材质名认不出方块')
    a = ap.parse_args()

    os.makedirs(a.outdir, exist_ok=True)
    t0 = time.time()

    # ---- 中心：默认玩家登录位置 ----
    p = read_player(a.save)
    if a.center:
        cx, cz = (float(v) for v in a.center.split(','))
        cy = p['pos'][1] if p else 64
    elif p:
        cx, cy, cz = p['pos']
    else:
        cx, cy, cz = 0, 64, 0
    print(f"中心 = ({cx:.0f}, {cy:.0f}, {cz:.0f})"
          + (f"（玩家 {p['file'][:8]}… 登录位置）" if p else ""))

    w = World(a.save)
    r = a.radius
    blocks = {}
    for (x, y, z, name, props) in w.iter_blocks(x0=int(cx) - r, x1=int(cx) + r,
                                                z0=int(cz) - r, z1=int(cz) + r,
                                                y0=a.ymin, y1=a.ymax, skip_air=True,
                                                with_props=True):
        blocks[(x, y, z)] = (name, props)
    print(f"近景 {len(blocks)} 方块，{time.time()-t0:.1f} 秒")

    if a.horizon and a.horizon > r:
        t1 = time.time()
        top = {}
        for (x, y, z, name, props) in w.iter_blocks(x0=int(cx) - a.horizon,
                                                    x1=int(cx) + a.horizon,
                                                    z0=int(cz) - a.horizon,
                                                    z1=int(cz) + a.horizon,
                                                    y0=a.ymin, y1=min(a.ymax, 90),
                                                    skip_air=True, with_props=True):
            k = (x, z)
            if k not in top or y > top[k][0]:
                top[k] = (y, name, props)
        add = 0
        for (x, z), (y, name, props) in top.items():
            if (x, y, z) not in blocks:
                blocks[(x, y, z)] = (name, props)
                add += 1
        print(f"远景 LOD ±{a.horizon} 补入 {add} 个地表方块，{time.time()-t1:.1f} 秒")

    valid = {k: v for k, v in blocks.items() if block_extent(v[0], v[1]) is not None}

    # ---- 材质表：按 (贴图, 染色) 去重 ----
    # 多个方块共用同一张贴图很常见（acacia_planks / acacia_slab / acacia_stairs …），
    # 若按"方块×面"建槽会生出 acacia_planks.2 … .9 这种名字，
    # 既冗余又可能让 MCprep 匹配不上。所以按 (贴图, 染色) 归一。
    names = sorted({v[0] for v in valid.values()})
    slots = []
    slot_i = {}
    by_key = {}
    needed = []
    for n in names:
        texs = resolve_textures(n)
        for k in ('top', 'side', 'bottom'):
            t = texs.get(k) or texs.get('side')
            if not t:
                continue
            # ★ 不再烘染色：实测 MCprep 的 prep_materials 会【自己做群系染色】
            #   （grass_block_top 被自动乘上 (0.227,0.615,0.089)）。
            #   自己烘反而把材质名变成 grass_block_top__t，MCprep 就认不出来了。
            #   保留 --bake-tint 作为退路（不用 MCprep 时才需要）。
            tint = tint_of(n) if (a.bake_tint and k != 'bottom') else None
            key = (t, tint)
            if key not in by_key:
                by_key[key] = len(slots)
                slots.append((n, k, t, tint))
                needed.append((t, tint))
            slot_i[(n, k)] = by_key[key]
    print(f"方块 {len(names)} 种 → 材质 {len(slots)} 个"
          f"（按 贴图×染色 去重，顶/侧/底分开）"
          f"{'  [已烘染色]' if a.bake_tint else '  [不烘染色，交给 MCprep]'}")

    texmap = bake_textures(needed, a.outdir)
    have = sum(1 for v in texmap.values() if v)
    print(f"贴图就绪 {have}/{len(texmap)} 张")

    # ---- 材质名 = MC 贴图名（关键！）----
    # ⚠️ 坑 42：原先材质叫 m0/m1/m2，结果 MCprep 的 prep_materials【认不出来】——
    # 它按 MC 的贴图/方块命名匹配。jmc2obj 兼容性 🟢 正是因为它按贴图名命名。
    # 去重后每个 (贴图, 染色) 只出现一次，所以名字天然唯一。
    mat_names = [tex + ('__t' if tint else '') for (_b, _k, tex, tint) in slots]

    # ---- 写 OBJ ----
    # ★ 植物类方块（花/草/蘑菇/树苗…）不能再当【立方体】导：贴图是带镂空的
    #   16×16 sprite，做成六面盒子后 Blender 里 Alpha 镂空一开，就会看到
    #   同一朵花贴在 6 个面上（正面、侧面、顶面各一份）→ 用户说的「花错位」，
    #   而且它作为"实心方块"还会把脚下草地的顶面剔掉 →「地上是透明的」。
    #   这里改成 MC 原版的十字面片（两个对角面），并且不让它参与遮挡剔除。
    PLANT_KEYS = (
        'flower', 'dandelion', 'poppy', 'cornflower', 'tulip', 'allium',
        'orchid', 'azure_bluet', 'oxeye_daisy', 'lily_of_the_valley',
        'wither_rose', 'sunflower', 'lilac', 'rose_bush', 'peony',
        'sapling', 'seedling', 'short_grass', 'tall_grass', 'fern',
        'dead_bush', 'bush', 'mushroom', 'sugar_cane', 'wheat', 'carrots',
        'potatoes', 'beetroot', 'sweet_berry', 'roots', 'sprouts',
        'torchflower', 'pink_petals', 'seagrass', 'kelp', 'vine', 'azalea',
        'nether_wart', 'crimson_fungus', 'warped_fungus', 'glow_lichen',
    )
    PLANT_SKIP = ('grass_block', 'cactus', 'mushroom_block', 'mushroom_stem',
                  'leaves', 'lily_pad')

    def is_plant(bname):
        n = bname.split(':')[-1]
        for sk in PLANT_SKIP:
            if sk in n:
                return False
        if n.endswith('_block') or n.endswith('_slab') or n.endswith('_stairs'):
            return False
        for k in PLANT_KEYS:
            if k in n:
                return True
        # 兜底：贴图本身有镂空 → 一定是 sprite 类
        t = resolve_textures(bname).get('side')
        return bool(t) and tex_has_alpha(t)

    plants = {k for k, v in valid.items() if is_plant(v[0])}
    occl = {k: v for k, v in valid.items() if k not in plants}

    objp = os.path.join(a.outdir, a.name + '.obj')
    mtlp = os.path.join(a.outdir, a.name + '.mtl')
    verts, vcache = [], {}

    def vid(p):
        i = vcache.get(p)
        if i is None:
            i = len(verts)
            vcache[p] = i
            verts.append(p)
        return i

    # 十字面片：两块对角平面，跟 MC 的 cross 模型一致
    CROSS = (
        ((0, 0, 0), (1, 0, 1), (1, 1, 1), (0, 1, 0)),
        ((1, 0, 0), (0, 0, 1), (0, 1, 1), (1, 1, 0)),
    )

    facelines = []
    cur_slot = None
    nface = 0
    culled = 0
    nplant = 0
    cull = not a.no_cull
    for (X, Y, Z), (bn, props) in valid.items():
        lo, hi = block_extent(bn, props)
        if (X, Y, Z) in plants:
            si = slot_i.get((bn, 'side'))
            if si is None:
                continue
            if si != cur_slot:
                facelines.append('usemtl ' + mat_names[si])
                cur_slot = si
            for quad in CROSS:
                idx = [vid((X + cx, Y + cy * hi, Z + cz)) for (cx, cy, cz) in quad]
                facelines.append('f ' + ' '.join(
                    '%d/%d' % (i + 1, k + 1) for k, i in enumerate(idx)))
                nface += 1
            nplant += 1
            continue
        for (dx, dy, dz), corners, kind in FACES:
            if cull and (X + dx, Y + dy, Z + dz) in occl:
                culled += 1
                continue
            si = slot_i.get((bn, kind))
            if si is None:
                continue
            if si != cur_slot:
                facelines.append('usemtl ' + mat_names[si])
                cur_slot = si
            idx = []
            for (cx2, cy2, cz2) in corners:
                # 角点约定 (x, y, z)，高度是【第 2 个分量 cy2】。
                # 早先误用 cz2 当高度：Y 与 Z 被绑死 → 面出现重复顶点 → 退化。
                # 完整方块碰巧仍是合法立方体（错切），半砖则完全错。
                yy = lo if cy2 == 0 else hi
                idx.append(vid((X + cx2, Y + yy, Z + cz2)))
            facelines.append('f ' + ' '.join(
                '%d/%d' % (i + 1, k + 1) for k, i in enumerate(idx)))
            nface += 1

    with open(objp, 'w', encoding='utf-8') as f:
        f.write('# 由 mc_导出OBJ 从 Minecraft 存档生成\n')
        f.write(f'mtllib {a.name}.mtl\no {a.name}\n')
        for (x, y, z) in verts:
            f.write('v %.4f %.4f %.4f\n' % (x, y, z))
        # 所有面都是贴图整面 0~1，四组 UV 全局共用
        for (u, v) in ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)):
            f.write('vt %.4f %.4f\n' % (u, v))
        f.write('\n'.join(facelines) + '\n')

    with open(mtlp, 'w', encoding='utf-8') as f:
        f.write('# 由 mc_导出OBJ 生成\n')
        for i, (bn, kind, tex, tint) in enumerate(slots):
            key = tex + ('__t' if tint else '')
            png = texmap.get(key)
            f.write(f'newmtl {mat_names[i]}\n')
            f.write(f'# {bn} / {kind}\n')
            if png:
                f.write(f'map_Kd tex/{png}\n')
            f.write('Kd 1.000 1.000 1.000\nKa 0.000 0.000 0.000\nKs 0.000 0.000 0.000\n')
            f.write('d 1.0\nillum 1\n\n')

    print(f"OBJ: {len(verts)} 顶点 / {nface} 面（剔除 {culled}；其中 {nplant} 个植物方块按十字面片导）")
    print(f"  {objp}")
    print(f"  {mtlp}")
    print(f"  {os.path.join(a.outdir, 'tex')}\\  （{have} 张贴图）")
    print(f"总耗时 {time.time()-t0:.1f} 秒")


if __name__ == '__main__':
    main()
