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
             'end_stone', 'sandstone')


def surface_map(world, x0, x1, z0, z1, y0=50, y1=200, progress=None):
    """取每列的最高方块。返回 {(x,z): (y, 方块名)}"""
    top = {}
    n = 0
    for (x, y, z, name) in world.iter_blocks(x0=x0, x1=x1, z0=z0, z1=z1, y0=y0, y1=y1):
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


def pick_from_save(save_dir, x0, x1, z0, z1, y0=50, y1=200,
                   radius=15, az_step=30, top_n=12, progress=None):
    """一站式：读存档 → 地表 → 推荐机位"""
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
