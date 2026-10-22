# -*- coding: utf-8 -*-
"""scan —— 在 MC 存档里找"适合拍摄"的位置

用户只提供地图；"哪里拍好看"由这里决定。

三个判据（都是实测踩坑换来的）
------------------------------
① 站得下    要求 N×N 格【同高度草地】，否则角色会踩在台阶/坑里
② 看得远    视线方向上不能有高于地面的方块挡着
③ 边界安全  ★ 扫描范围外的格子必须当作【有东西】，不能当空旷

⚠️ 坑 58：第一版把"扫描范围外"当成空旷，于是判出一批"开阔度 1.00"的
   假阳性位置 —— 选过去一看，正好在村落广场中间，四周全是房子。
   凡是"没数据"的地方，一律按最坏情况处理。
"""
import math

from anvil import World

# 地表方块里算作"可站立地面"的
STANDABLE = ('grass_block', 'dirt', 'coarse_dirt', 'podzol', 'mycelium',
             'sand', 'red_sand', 'gravel', 'stone', 'andesite', 'diorite',
             'granite', 'deepslate', 'tuff', 'calcite', 'snow_block',
             'moss_block', 'rooted_dirt', 'mud', 'clay', 'terracotta',
             'smooth_basalt', 'blackstone', 'basalt', 'netherrack',
             'end_stone', 'sandstone', 'planks', 'log', 'cobblestone',
             'bricks', 'concrete', 'polished')

# ★ 这些不算「地表」：树冠 / 植物 / 水面 / 薄雪。它们盖在真正能站的地面上面，
#   如果把它们算进「最高方块」，整片森林的地表都会被判成 oak_leaves，
#   于是「15×15 同高度草地」一个都找不到 —— 整合包地图（树多、装饰多）上必现。
DECOR = ('_leaves', 'water', 'lava', 'seagrass', 'kelp', 'vine', 'carpet',
         'torch', 'flower', 'sapling', 'bush', 'crop', 'wheat', 'bamboo',
         'sugar_cane', 'snow_layer', 'short_grass', 'tall_grass', 'fern',
         'dead_bush', 'mushroom', 'lily_pad', 'cobweb', 'roots', 'azalea')


def _is_decor(name):
    if name in ('short_grass', 'tall_grass', 'fern', 'dead_bush', 'sugar_cane'):
        return True
    return any(name.endswith(s) for s in DECOR)


def surface_map(world, x0, x1, z0, z1, y0=50, y1=200, progress=None):
    """取每列的最高方块。返回 {(x,z): (y, 方块名)}"""
    top = {}
    n = 0
    for (x, y, z, name) in world.iter_blocks(x0=x0, x1=x1, z0=z0, z1=z1, y0=y0, y1=y1):
        if _is_decor(name):
            continue
        k = (x, z)
        if k not in top or y > top[k][0]:
            top[k] = (y, name)
        n += 1
        if progress and n % 200000 == 0:
            progress(f'      已读 {n} 个方块，{len(top)} 列')
    return top


def find_open_ground(top, radius=15, bounds=None, want_grass=True):
    """找 radius×radius 全是同高度【可站立方块】的位置。

    ★ bounds=(x0,x1,z0,z1) 时，只返回【整个方块都在范围内】的候选 ——
      贴着扫描边界的候选一律丢弃，因为边界外的数据是缺的。
    返回 [(x, z, y, 可站格数)]，按可站格数降序
    """
    if bounds:
        bx0, bx1, bz0, bz1 = bounds
    grass = {}
    for (x, z), (y, name) in top.items():
        if want_grass:
            if name.endswith('grass_block'):
                grass[(x, z)] = (y, name)
        else:
            if any(name.endswith(s) for s in STANDABLE):
                grass[(x, z)] = (y, name)

    out = []
    for (x, z), (y, _) in grass.items():
        if bounds:
            if x - radius < bx0 or x + radius > bx1 or z - radius < bz0 or z + radius > bz1:
                continue
        ok = True
        for dx in range(-radius, radius + 1):
            for dz in range(-radius, radius + 1):
                v = grass.get((x + dx, z + dz))
                if v is None or v[0] != y:
                    ok = False
                    break
            if not ok:
                break
        if ok:
            out.append((x, z, y, (radius * 2 + 1) ** 2))
    return out


def openness(top, cx, cz, az_deg, ground_y, dist=42, half_deg=17, bounds=None,
             min_height=2):
    """从 (cx,cz) 朝 az 看，视野锥内被挡的比例。返回 0~1，越大越开阔。

    ★ 范围外/没数据 → 一律算【被挡】（坑 58）
    """
    blocked = total = 0
    for r in range(3, dist):
        for da in range(-half_deg, half_deg + 1, 3):
            a = math.radians(az_deg + da)
            x = int(round(cx + r * math.cos(a)))
            z = int(round(cz + r * math.sin(a)))
            total += 1
            if bounds:
                bx0, bx1, bz0, bz1 = bounds
                if x < bx0 or x > bx1 or z < bz0 or z > bz1:
                    blocked += 1          # ★ 没数据 = 当有东西
                    continue
            v = top.get((x, z))
            if v is None:
                blocked += 1              # ★ 缺列 = 当有东西
            elif v[0] > ground_y + min_height:
                blocked += 1
    return 1.0 - blocked / max(1, total)


def best_shots(top, bounds=None, radius=15, want_grass=True,
               az_step=30, top_n=12, min_open=0.80):
    """综合 ①②③ 给出推荐机位。

    返回 [{x,z,y,az,openness,flat}]，按 (开阔度, 平坦格数) 降序
    """
    flats = find_open_ground(top, radius=radius, bounds=bounds, want_grass=want_grass)
    if not flats:
        return []
    # 按区域聚团，每团只留代表，避免返回一堆相邻点
    flats.sort(key=lambda t: -t[3])
    picked, seen = [], set()
    for x, z, y, cnt in flats:
        key = (x // (radius * 2), z // (radius * 2))
        if key in seen:
            continue
        seen.add(key)
        picked.append((x, z, y, cnt))
        if len(picked) >= 40:
            break

    shots = []
    for x, z, y, cnt in picked:
        for az in range(0, 360, az_step):
            o = openness(top, x, z, az, y, bounds=bounds)
            if o >= min_open:
                shots.append({'x': x, 'z': z, 'y': y, 'az': az,
                              'openness': round(o, 3), 'flat': cnt})
    shots.sort(key=lambda s: (-s['openness'], -s['flat']))
    # 同一位置只留最好的一个方位角
    out, seen_pos = [], set()
    for s in shots:
        k = (s['x'], s['z'])
        if k in seen_pos:
            continue
        seen_pos.add(k)
        out.append(s)
        if len(out) >= top_n:
            break
    return out


def rough_shots(top, bounds=None, az_step=30, top_n=6, progress=None):
    """最后兜底：只要求「这一列能站人」，按周围 5×5 同高度格数排序。

    ★ 为什么需要它：整合包地图（山地、城堡、密集装饰）上常常一整张图都
      找不到 15×15 的平整草地，于是 mc_scan 返回 0 个机位、mc_shoot 直接失败。
      用户上传的是「他想拍的地图」，不是「适合拍的平地」—— 必须给个能拍的落点。
    """
    cols = {}
    for (x, z), (y, name) in top.items():
        if any(name.endswith(s) for s in STANDABLE):
            cols[(x, z)] = y
    if not cols:
        return []
    bx0, bx1, bz0, bz1 = bounds if bounds else (None, None, None, None)
    scored = []
    for (x, z), y in cols.items():
        if bounds and not (bx0 <= x <= bx1 and bz0 <= z <= bz1):
            continue
        cnt = 0
        for dx in range(-2, 3):
            for dz in range(-2, 3):
                v = cols.get((x + dx, z + dz))
                if v is not None and abs(v - y) <= 1:
                    cnt += 1
        if cnt >= 9:
            scored.append((cnt, x, z, y))
    scored.sort(reverse=True)
    if progress:
        progress(f'    兜底候选 {len(scored)} 列（要求 5×5 内至少 9 格同高度）')
    out = []
    for cnt, x, z, y in scored[:max(1, top_n) * 6]:
        for az in range(0, 360, az_step):
            o = openness(top, x, z, az, y, bounds=bounds)
            if o >= 0.30:
                out.append({'x': x, 'z': z, 'y': y, 'az': az,
                            'openness': round(o, 3), 'flat': cnt})
        if len(out) >= top_n:
            break
    out.sort(key=lambda s: (-s['openness'], -s['flat']))
    return out[:top_n]


def pick_from_save(save_dir, x0, x1, z0, z1, y0=50, y1=200,
                   radius=15, az_step=30, top_n=12, progress=None):
    """一站式：读存档 → 地表 → 推荐机位（找不到就逐级放宽，最后兜底）"""
    w = World(save_dir)
    if progress:
        progress(f'    读存档 {save_dir}')
        progress(f'    范围 x[{x0},{x1}] z[{z0},{z1}] y[{y0},{y1}]')
    top = surface_map(w, x0, x1, z0, z1, y0=y0, y1=y1, progress=progress)
    if progress:
        progress(f'    地表 {len(top)} 列')
    bounds = (x0, x1, z0, z1)
    shots = best_shots(top, bounds=bounds, radius=radius, az_step=az_step,
                       top_n=top_n)
    if not shots:
        for r, min_open in ((max(6, radius // 2), 0.55), (4, 0.35), (2, 0.20)):
            shots = best_shots(top, bounds=bounds, radius=r, want_grass=False,
                               az_step=az_step, top_n=top_n, min_open=min_open)
            if shots:
                if progress:
                    progress(f'    没有 {radius}×{radius} 平整草地，放宽到 {r}×{r} 后找到 {len(shots)} 个机位')
                break
    if not shots:
        if progress:
            progress('    仍然没有成片平地，改用「能站人」兜底挑选')
        shots = rough_shots(top, bounds=bounds, az_step=az_step, top_n=top_n,
                            progress=progress)
    return w, top, shots


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='在 MC 存档里找适合拍摄的位置')
    ap.add_argument('save')
    ap.add_argument('--area', required=True, help='x0,x1,z0,z1')
    ap.add_argument('--y', default='50,200')
    ap.add_argument('--radius', type=int, default=15)
    ap.add_argument('--top', type=int, default=12)
    a = ap.parse_args()
    x0, x1, z0, z1 = (int(v) for v in a.area.split(','))
    y0, y1 = (int(v) for v in a.y.split(','))
    _, _, shots = pick_from_save(a.save, x0, x1, z0, z1, y0=y0, y1=y1,
                                 radius=a.radius, top_n=a.top,
                                 progress=lambda s: print(s))
    print(f'\n推荐机位（开阔度 / 坐标 / 地面Y / 视线方位角 / 平坦格数）:')
    for s in shots:
        print("  开阔度%.2f  (%5d,%5d) Y=%d  az=%3d°  平坦%4d"
              % (s['openness'], s['x'], s['z'], s['y'], s['az'], s['flat']))
