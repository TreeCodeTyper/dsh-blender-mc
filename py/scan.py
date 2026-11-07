# -*- coding: utf-8 -*-
"""scan —— 在 MC 存档里找"适合拍摄"的位置（0.6.0 算法版）

0.6.0 把原来的暴力方格扫描换成 2.5D 地形算法：

① 站得下    积分图（Summed-Area Table）O(1) 算任意窗口的
            有效格数 / 高度方差 / 可站格数，替代 O(F·r²) 的逐格检查
② 看得远    DDA / Amanatides-Woo 体素光线步进，按射线是否被高于
            地面的方块挡住来算 openness；不再用固定锥的稀疏采样
③ 有层次    同一组射线顺带统计 relief（视野里"有东西"的比例）
④ 不重复    Poisson-disk 式最小间距去重，替代 x//2r 的硬网格
⑤ 多目标    Pareto 非支配排序 + 原来的构图惩罚分，避免只追求"最空"

接口保持不变：surface_map / find_open_ground / openness / relief /
best_shots / rough_shots / pick_from_save。
"""
import math

import numpy as np

from anvil import World

# 地表方块里算作"可站立地面"的
STANDABLE = ('grass_block', 'dirt', 'coarse_dirt', 'podzol', 'mycelium',
             'sand', 'red_sand', 'gravel', 'stone', 'andesite', 'diorite',
             'granite', 'deepslate', 'tuff', 'calcite', 'snow_block',
             'moss_block', 'rooted_dirt', 'mud', 'clay', 'terracotta',
             'smooth_basalt', 'blackstone', 'basalt', 'netherrack',
             'end_stone', 'sandstone', 'planks', 'log', 'cobblestone',
             'bricks', 'concrete', 'polished')

# ★ 这些不算「地表」：树冠 / 植物 / 水面 / 薄雪。
DECOR = ('_leaves', 'water', 'lava', 'seagrass', 'kelp', 'vine', 'carpet',
         'torch', 'flower', 'sapling', 'bush', 'crop', 'wheat', 'bamboo',
         'sugar_cane', 'snow_layer', 'short_grass', 'tall_grass', 'fern',
         'dead_bush', 'mushroom', 'lily_pad', 'cobweb', 'roots', 'azalea')

WATERISH = ('water', 'lava', 'ice', '_ice')


def _is_decor(name):
    if name in ('short_grass', 'tall_grass', 'fern', 'dead_bush', 'sugar_cane'):
        return True
    return any(name.endswith(s) for s in DECOR)


class TopMap(dict):
    """地表字典，额外带 top_any（含树冠/水）与 wet（水面）两个视图。

    外部仍当普通 dict 用；相机侧检查用它区分“水面”与“树冠/屋檐”。
    """


def surface_map(world, x0, x1, z0, z1, y0=50, y1=200, progress=None):
    """取每列的最高方块。返回 {(x,z): (y, 方块名)}

    水/树冠/屋檐的处理保持 0.5.x 的实测规则：
    - 水面高于地表的列整列丢掉（不能站水里）；
    - 头顶 3 格内有方块的列丢掉（树冠/屋檐下）。
    """
    top = TopMap()
    wet = {}
    top_any = {}
    n = 0
    for (x, y, z, name) in world.iter_blocks(x0=x0, x1=x1, z0=z0, z1=z1,
                                             y0=y0, y1=y1):
        k = (x, z)
        if name and not name.endswith('air'):
            if y > top_any.get(k, -9999):
                top_any[k] = y
        if _is_decor(name):
            if name.endswith(WATERISH):
                if y > wet.get(k, -9999):
                    wet[k] = y
            continue
        if k not in top or y > top[k][0]:
            top[k] = (y, name)
        n += 1
        if progress and n % 200000 == 0:
            progress(f'      已读 {n} 个方块，{len(top)} 列')
    dropped = 0
    for k, wy in wet.items():
        t = top.get(k)
        if t is not None and wy >= t[0]:
            del top[k]
            dropped += 1
    shaded = 0
    for k, gy in list(top.items()):
        hi = top_any.get(k)
        if hi is not None and hi > gy[0] + 3:
            del top[k]
            shaded += 1
    if progress and shaded:
        progress(f'      剔除树冠/屋檐下 {shaded} 列（头顶 3 格内有方块）')
    if progress and dropped:
        progress(f'      剔除水下 {dropped} 列（水面高于地表，站不住）')
    top.any = top_any
    top.wet = wet
    return top


# ---------------------------------------------------------------------------
# 2.5D 数组：高度 / 有效列 / 可站列
# ---------------------------------------------------------------------------

def _grid(top, bounds, want_grass=True):
    """把稀疏的 {(x,z):(y,name)} 压成矩形数组。

    返回 (x0, z0, height, valid, standable)
    - height 缺失列是 NaN
    - valid=该列有地表
    - standable=该列是草地（或任意可站立地面）
    """
    if bounds:
        x0, x1, z0, z1 = bounds
    else:
        if not top:
            return 0, 0, np.zeros((0, 0), np.float32), np.zeros((0, 0), bool), np.zeros((0, 0), bool)
        xs = [k[0] for k in top]
        zs = [k[1] for k in top]
        x0, x1, z0, z1 = min(xs), max(xs), min(zs), max(zs)
    w = x1 - x0 + 1
    h = z1 - z0 + 1
    if w <= 0 or h <= 0:
        return x0, z0, np.zeros((0, 0), np.float32), np.zeros((0, 0), bool), np.zeros((0, 0), bool)
    height = np.full((h, w), np.nan, np.float32)
    valid = np.zeros((h, w), bool)
    stand = np.zeros((h, w), bool)
    items = list(top.items())
    if items:
        xs = np.fromiter((k[0] - x0 for k, _ in items), dtype=np.int32, count=len(items))
        zs = np.fromiter((k[1] - z0 for k, _ in items), dtype=np.int32, count=len(items))
        ys = np.fromiter((v[0] for _, v in items), dtype=np.float32, count=len(items))
        names = [v[1] for _, v in items]
        m = (xs >= 0) & (xs < w) & (zs >= 0) & (zs < h)
        if m.any():
            ix, iz = xs[m], zs[m]
            height[iz, ix] = ys[m]
            valid[iz, ix] = True
            if want_grass:
                sm = np.fromiter((str(n).endswith('grass_block') for n in names), dtype=bool, count=len(names))[m]
            else:
                sm = np.fromiter((any(str(n).endswith(s) for s in STANDABLE) for n in names), dtype=bool, count=len(names))[m]
            stand[iz, ix] = sm
    return x0, z0, height, valid, stand


def _integral(a):
    """二维积分图，返回 (H+1,W+1)，左上角补 0。"""
    return np.pad(np.cumsum(np.cumsum(a, axis=0), axis=1), ((1, 0), (1, 0)))


def _window_sum(I, r):
    """用积分图算每个像素 (2r+1)² 窗口和；边界只算实际重叠部分。"""
    h, w = I.shape[0] - 1, I.shape[1] - 1
    if h <= 0 or w <= 0:
        return np.zeros((max(h, 0), max(w, 0)), np.float64)
    ys = np.arange(h)
    xs = np.arange(w)
    y0 = np.clip(ys - r, 0, h)
    y1 = np.clip(ys + r + 1, 0, h)
    x0 = np.clip(xs - r, 0, w)
    x1 = np.clip(xs + r + 1, 0, w)
    return (I[y1[:, None], x1[None, :]]
            - I[y0[:, None], x1[None, :]]
            - I[y1[:, None], x0[None, :]]
            + I[y0[:, None], x0[None, :]])


def _flat_mask(height, valid, stand, radius):
    """积分图平坦度：窗口内有效格数=满窗、可站格数=满窗、高度方差≈0。"""
    r = max(0, int(radius))
    n = (2 * r + 1) ** 2
    vf = valid.astype(np.float64)
    sf = stand.astype(np.float64)
    cnt = _window_sum(_integral(vf), r)
    scnt = _window_sum(_integral(sf), r)
    h0 = np.nan_to_num(height, nan=0.0).astype(np.float64)
    s = _window_sum(_integral(h0 * vf), r)
    s2 = _window_sum(_integral(h0 * h0 * vf), r)
    var = s2 - (s * s) / np.maximum(cnt, 1.0)
    return (cnt >= n - 0.5) & (scnt >= n - 0.5) & (var < 1e-2)


def _edt_1d(f):
    """Felzenszwalb 一维平方距离变换；f 是 float64 一维数组。"""
    n = f.shape[0]
    if n == 0:
        return f.copy()
    d = np.empty(n, np.float64)
    v = np.zeros(n, np.int32)
    z = np.zeros(n + 1, np.float64)
    k = 0
    v[0] = 0
    z[0] = -np.inf
    z[1] = np.inf
    for q in range(1, n):
        s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        while s <= z[k]:
            k -= 1
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2.0 * q - 2.0 * v[k])
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = np.inf
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d[q] = (q - v[k]) ** 2 + f[v[k]]
    return d


def _distance_transform(allowed):
    """到最近不可站格的欧氏距离（格）；allowed=True 为可站。"""
    if allowed.size == 0:
        return np.zeros_like(allowed, np.float64)
    big = 1e9
    f = np.where(allowed, big, 0.0).astype(np.float64)
    # 先按行，再按列做一维 EDT
    for i in range(f.shape[0]):
        f[i, :] = _edt_1d(f[i, :])
    for j in range(f.shape[1]):
        f[:, j] = _edt_1d(f[:, j])
    return np.sqrt(np.maximum(f, 0.0))


def _slope_grid(height, valid):
    """Sobel 梯度 → 坡度角（度）；缺列记 90°。"""
    h = np.nan_to_num(height, nan=0.0).astype(np.float64)
    gy, gx = np.gradient(h)
    slope = np.degrees(np.arctan(np.hypot(gx, gy)))
    slope[~valid] = 90.0
    return slope


def _ransac_slope(height, valid, bounds, cx, cz, radius=15,
                  iters=24, thresh=0.35):
    """窗口内 RANSAC 平面拟合，返回稳健坡度角（度）。

    只取窗口内有效点；三点采样拟合 h=ax+bz+c，内点阈值 0.35 格。
    比单点 Sobel 稳：草方块台阶/单块石头不会把整块平地判成陡坡。
    """
    if bounds is None:
        return 0.0
    bx0, bx1, bz0, bz1 = bounds
    h, w = height.shape
    r = max(2, int(radius))
    step = max(1, r // 6)
    pts = []
    for dz in range(-r, r + 1, step):
        for dx in range(-r, r + 1, step):
            x, z = cx + dx, cz + dz
            ax, az = x - bx0, z - bz0
            if ax < 0 or ax >= w or az < 0 or az >= h:
                continue
            if valid[az, ax]:
                pts.append((float(x), float(z), float(height[az, ax])))
    if len(pts) < 6:
        return 90.0
    p = np.asarray(pts, np.float64)
    rng = np.random.default_rng(1234)
    best_in = None
    best_n = -1
    for _ in range(iters):
        idx = rng.choice(len(p), 3, replace=False)
        A = np.column_stack([p[idx, 0], p[idx, 1], np.ones(3)])
        try:
            coef, *_ = np.linalg.lstsq(A, p[idx, 2], rcond=None)
        except np.linalg.LinAlgError:
            continue
        pred = coef[0] * p[:, 0] + coef[1] * p[:, 1] + coef[2]
        inl = np.abs(p[:, 2] - pred) < thresh
        n = int(inl.sum())
        if n > best_n:
            best_n = n
            best_in = inl
    if best_in is None or best_n < 3:
        return 90.0
    A = np.column_stack([p[best_in, 0], p[best_in, 1], np.ones(int(best_in.sum()))])
    coef, *_ = np.linalg.lstsq(A, p[best_in, 2], rcond=None)
    return float(np.degrees(np.arctan(np.hypot(coef[0], coef[1]))))


# ---------------------------------------------------------------------------
# 射线：DDA / Amanatides-Woo
# ---------------------------------------------------------------------------

def _ray_cells(cx, cz, az_deg, dist):
    """Amanatides-Woo 体素遍历，返回沿射线的格子 (x,z) 序列。

    从浮点起点开始，按 x/z 哪个方向先穿过网格线选择步进。
    范围外会 yield 出来，调用方按"没数据=被挡"处理。
    """
    a = math.radians(az_deg)
    dx, dz = math.cos(a), math.sin(a)
    x, z = float(cx), float(cz)
    ix, iz = math.floor(x), math.floor(z)
    step_x = 1 if dx > 0 else -1
    step_z = 1 if dz > 0 else -1
    tdx = abs(1.0 / dx) if abs(dx) > 1e-9 else 1e30
    tdz = abs(1.0 / dz) if abs(dz) > 1e-9 else 1e30
    tmx = ((ix + 1 - x) / dx) if dx > 0 else ((x - ix) / -dx) if dx < 0 else 1e30
    tmz = ((iz + 1 - z) / dz) if dz > 0 else ((z - iz) / -dz) if dz < 0 else 1e30
    yield ix, iz
    t = 0.0
    while t < dist:
        if tmx < tmz:
            ix += step_x
            t = tmx
            tmx += tdx
        else:
            iz += step_z
            t = tmz
            tmz += tdz
        if t > dist:
            break
        yield ix, iz


def _ray_open_stats(height, valid, bounds, cx, cz, az_deg, ground_y, dist, min_height):
    """单条射线的 (通视格数, 总格数)。

    范围外/缺列 = 被挡（坑 58）；但按"格子比例"而不是"整条射线全有或全无"，
    否则一条射线中途碰到一棵树/一个缺列就会把整条方向判死 —— 0.5.x 的锥形
    采样正是靠多格平均才没有被单棵树一票否决。
    """
    bx0, bx1, bz0, bz1 = bounds if bounds else (None, None, None, None)
    h, w = height.shape
    clear = total = 0
    for ix, iz in _ray_cells(cx, cz, az_deg, dist):
        total += 1
        if bx0 is not None:
            if ix < bx0 or ix > bx1 or iz < bz0 or iz > bz1:
                continue
        ax, az = ix - bx0, iz - bz0
        if ax < 0 or ax >= w or az < 0 or az >= h:
            continue
        if not valid[az, ax]:
            continue
        if height[az, ax] > ground_y + min_height:
            continue
        clear += 1
    return clear, total


def _ray_horizon(height, valid, bounds, cx, cz, az_deg, ground_y, dist):
    """单条射线的最大仰角（度）；被树/山挡住时变大。"""
    bx0, bx1, bz0, bz1 = bounds if bounds else (None, None, None, None)
    h, w = height.shape
    eye = ground_y + 1.62
    max_ang = 0.0
    for ix, iz in _ray_cells(cx, cz, az_deg, dist):
        if bx0 is not None:
            if ix < bx0 or ix > bx1 or iz < bz0 or iz > bz1:
                continue
        ax, az = ix - bx0, iz - bz0
        if ax < 0 or ax >= w or az < 0 or az >= h or not valid[az, ax]:
            continue
        dh = float(height[az, ax]) - eye
        if dh <= 0:
            continue
        d = math.hypot(ix - cx, iz - cz)
        if d < 1e-6:
            continue
        ang = math.degrees(math.atan2(dh, d))
        if ang > max_ang:
            max_ang = ang
    return max_ang


def _horizon_grid(height, valid, bounds, cx, cz, az_deg, ground_y,
                  dist=42, half_deg=17):
    """9 条射线的平均 horizon score：1=完全无遮挡，0=仰角≥45°。"""
    if height.size == 0:
        return 0.0
    rays = 9
    total = 0.0
    for i in range(rays):
        da = -half_deg + (2.0 * half_deg) * i / max(1, rays - 1)
        total += _ray_horizon(height, valid, bounds, cx, cz, az_deg + da,
                              ground_y, dist)
    ang = total / float(rays)
    return max(0.0, 1.0 - ang / 45.0)


def _ray_relief(height, valid, bounds, cx, cz, az_deg, ground_y, dist, min_height):
    """单条射线上的 (有东西格数, 有效格数)。范围外不计入分母。"""
    bx0, bx1, bz0, bz1 = bounds if bounds else (None, None, None, None)
    h, w = height.shape
    hit = total = 0
    for ix, iz in _ray_cells(cx, cz, az_deg, dist):
        if bx0 is not None:
            if ix < bx0 or ix > bx1 or iz < bz0 or iz > bz1:
                continue
        ax, az = ix - bx0, iz - bz0
        if ax < 0 or ax >= w or az < 0 or az >= h:
            continue
        total += 1
        if valid[az, ax] and height[az, ax] > ground_y + min_height:
            hit += 1
    return hit, total


# ---------------------------------------------------------------------------
# 公开接口
# ---------------------------------------------------------------------------

def find_open_ground(top, radius=15, bounds=None, want_grass=True):
    """找 radius×radius 全是同高度可站立方块的格子。

    0.6.0：积分图一次算完整张图，返回所有满足窗口的格子。
    返回 [(x, z, y, 可站格数)]，按 y、x、z 排序。
    """
    x0, z0, height, valid, stand = _grid(top, bounds, want_grass=want_grass)
    if height.size == 0:
        return []
    flat = _flat_mask(height, valid, stand, radius)
    iz, ix = np.where(flat)
    if len(ix) == 0:
        return []
    n = (2 * max(0, int(radius)) + 1) ** 2
    out = [(int(x0 + x), int(z0 + z), int(round(float(height[z, x]))), n)
           for z, x in zip(iz, ix)]
    out.sort(key=lambda t: (t[2], t[0], t[1]))
    return out


def _poisson_pick(cands, min_dist, limit=40):
    """Poisson-disk 式去重：贪心保留彼此至少 min_dist 的候选。"""
    if not cands:
        return []
    md2 = float(min_dist) ** 2
    # 同面积时优先靠扫描中心的点，避免总贴边
    xs = [c[0] for c in cands]
    zs = [c[1] for c in cands]
    cx = (min(xs) + max(xs)) / 2.0
    cz = (min(zs) + max(zs)) / 2.0
    cands = sorted(cands, key=lambda c: ((c[0] - cx) ** 2 + (c[1] - cz) ** 2, -c[3], c[0], c[1]))
    picked = []
    for c in cands:
        x, z = c[0], c[1]
        if all((x - p[0]) ** 2 + (z - p[1]) ** 2 >= md2 for p in picked):
            picked.append(c)
            if len(picked) >= limit:
                break
    return picked


def _openness_grid(height, valid, bounds, cx, cz, az_deg, ground_y,
                   dist=42, half_deg=17, min_height=2):
    if height.size == 0:
        return 0.0
    rays = 9
    clear = total = 0
    for i in range(rays):
        da = -half_deg + (2.0 * half_deg) * i / max(1, rays - 1)
        c, t = _ray_open_stats(height, valid, bounds, cx, cz, az_deg + da,
                               ground_y, dist, min_height)
        clear += c
        total += t
    return clear / float(max(1, total))


def _relief_grid(height, valid, bounds, cx, cz, az_deg, ground_y,
                 dist=64, half_deg=32, min_height=3):
    if height.size == 0:
        return 0.0
    rays = 9
    hit = total = 0
    for i in range(rays):
        da = -half_deg + (2.0 * half_deg) * i / max(1, rays - 1)
        hh, tt = _ray_relief(height, valid, bounds, cx, cz, az_deg + da,
                             ground_y, dist, min_height)
        hit += hh
        total += tt
    return hit / float(max(1, total))



def _occlusion_top(top):
    """把树冠/屋檐并入高度图，避免 openness 把树墙当成空地。"""
    anyh = getattr(top, 'any', None)
    if not anyh:
        return top
    wet = getattr(top, 'wet', None) or set()
    d = dict(top)
    for k, ah in anyh.items():
        if k in wet:
            continue
        cur = d.get(k)
        if cur is None or ah > cur[0]:
            d[k] = (ah, 'canopy')
    return d

def openness(top, cx, cz, az_deg, ground_y, dist=42, half_deg=17, bounds=None,
             min_height=2):
    """从 (cx,cz) 朝 az 看，9 条 DDA 射线的通视比例。0~1，越大越开阔。"""
    if bounds is None:
        xs = [k[0] for k in top]
        zs = [k[1] for k in top]
        bounds = (min(xs), max(xs), min(zs), max(zs))
    x0, z0, height, valid, _ = _grid(top, bounds, want_grass=False)
    return _openness_grid(height, valid, bounds, cx, cz, az_deg, ground_y,
                          dist=dist, half_deg=half_deg, min_height=min_height)


def relief(top, cx, cz, az_deg, ground_y, dist=64, half_deg=32, bounds=None,
           min_height=3):
    """视野锥里「有东西」的比例（树/建筑/山）。0~1。"""
    if bounds is None:
        xs = [k[0] for k in top]
        zs = [k[1] for k in top]
        bounds = (min(xs), max(xs), min(zs), max(zs))
    x0, z0, height, valid, _ = _grid(top, bounds, want_grass=False)
    return _relief_grid(height, valid, bounds, cx, cz, az_deg, ground_y,
                        dist=dist, half_deg=half_deg, min_height=min_height)


def _camera_side_ok(top, bounds, x, z, az_deg, ground_y, dist=15):
    """机位侧清空：相机所在格不能是树/水/山，且相机到主体不被地形挡住。

    openness 是从主体往视线方向看；但 mc_shoot 的相机在主体【反方向】dist 格，
    如果那一侧是山坡/树冠，画面里就会出现一块贴脸的大草方块（实测 0.6.0
    首轮在 (-32,31) az=120 就是这样）。这里用 15 格默认相机距离做一个低成本
    的前置过滤，避免把“主体看起来开阔、相机却埋在山里”的机位推出去。
    """
    if bounds is None:
        return True
    bx0, bx1, bz0, bz1 = bounds
    a = math.radians(az_deg)
    camx = int(round(x - math.cos(a) * dist))
    camz = int(round(z - math.sin(a) * dist))
    if not (bx0 <= camx <= bx1 and bz0 <= camz <= bz1):
        return False
    anyh = getattr(top, 'any', None) or {}
    wet = getattr(top, 'wet', None) or {}
    v = top.get((camx, camz))
    # 相机可以悬在水面（wet）上；但不能埋进树冠/屋檐（any 高且不是水）
    ah = anyh.get((camx, camz))
    if ah is not None and ah > ground_y + 2.5 and (camx, camz) not in wet:
        return False
    if v is not None and v[0] > ground_y + 2.5:
        return False
    # 相机 → 主体：中间不能有高于两者脚底的树冠/地形
    max_y = max(ground_y, v[0]) if v is not None else ground_y
    for r in range(2, max(3, dist - 1)):
        t = r / float(max(1, dist))
        px = int(round(x * (1.0 - t) + camx * t))
        pz = int(round(z * (1.0 - t) + camz * t))
        if not (bx0 <= px <= bx1 and bz0 <= pz <= bz1):
            return False
        pv = top.get((px, pz))
        pah = anyh.get((px, pz))
        if pah is not None and pah > max_y + 2.0 and (px, pz) not in wet:
            return False
        if pv is None:
            continue
        if pv[0] > max_y + 2.0:
            return False
    return True


def _shot_score(openness_v, relief_v):
    """构图惩罚分：开阔度 0.55~0.98、背景 3%~25% 最舒服。"""
    o = openness_v
    if o > 0.98:
        pen_o = (o - 0.98) * 250.0
    elif o < 0.60:
        pen_o = (0.60 - o) * 110.0
    else:
        pen_o = 0.0
    r = relief_v
    if r < 0.03:
        pen_r = (0.03 - r) * 900.0
    elif r > 0.25:
        pen_r = (r - 0.25) * 260.0
    else:
        pen_r = 0.0
    return 100.0 - pen_o - pen_r


def _pareto_rank(points, keys):
    """非支配排序；points 是 list，keys(p) 返回要最大化的 tuple。"""
    n = len(points)
    if n == 0:
        return []
    vals = [keys(p) for p in points]
    rank = [0] * n
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            vi, vj = vals[i], vals[j]
            if all(b >= a for a, b in zip(vi, vj)) and any(b > a for a, b in zip(vi, vj)):
                rank[i] += 1
    return rank


def _nsga2_rank(points, keys):
    """快速非支配排序 + 拥挤距离；返回 (ranks, crowding)。

    ranks: 0 是最优前沿；crowding: 同前沿内越大越不拥挤。
    """
    n = len(points)
    if n == 0:
        return [], []
    vals = [keys(p) for p in points]
    S = [[] for _ in range(n)]
    n_dom = [0] * n
    fronts = [[]]
    for p in range(n):
        for q in range(n):
            if p == q:
                continue
            vp, vq = vals[p], vals[q]
            if all(b >= a for a, b in zip(vp, vq)) and any(b > a for a, b in zip(vp, vq)):
                S[p].append(q)
            elif all(a >= b for a, b in zip(vp, vq)) and any(a > b for a, b in zip(vp, vq)):
                n_dom[p] += 1
        if n_dom[p] == 0:
            fronts[0].append(p)
    i = 0
    while fronts[i]:
        nxt = []
        for p in fronts[i]:
            for q in S[p]:
                n_dom[q] -= 1
                if n_dom[q] == 0:
                    nxt.append(q)
        i += 1
        fronts.append(nxt)
    fronts = [f for f in fronts if f]
    ranks = [0] * n
    crowd = [0.0] * n
    for rk, front in enumerate(fronts):
        for p in front:
            ranks[p] = rk
        m = len(vals[0]) if vals else 0
        for k in range(m):
            front_sorted = sorted(front, key=lambda p: vals[p][k])
            lo = vals[front_sorted[0]][k]
            hi = vals[front_sorted[-1]][k]
            crowd[front_sorted[0]] = float('inf')
            crowd[front_sorted[-1]] = float('inf')
            if hi - lo <= 1e-12:
                continue
            for j in range(1, len(front_sorted) - 1):
                p = front_sorted[j]
                crowd[p] += (vals[front_sorted[j + 1]][k] - vals[front_sorted[j - 1]][k]) / (hi - lo)
    return ranks, crowd


def _nms_shots(shots, min_dist, limit):
    """显式 NMS：按排序保留彼此至少 min_dist 的机位，抑制同片广场。"""
    if not shots:
        return []
    md2 = float(min_dist) ** 2
    kept = []
    for s in shots:
        if all((s['x'] - k['x']) ** 2 + (s['z'] - k['z']) ** 2 >= md2 for k in kept):
            kept.append(s)
            if len(kept) >= limit:
                break
    return kept



def _canopy_wall_ratio(top, x, z, az_deg, ground_y, dist=12):
    """视线前方 2..dist 格被树冠/树叶挡住的比例。"""
    anyh = getattr(top, 'any', None)
    if not anyh:
        return 0.0
    wet = getattr(top, 'wet', None) or set()
    blocked = total = 0
    for ix, iz in _ray_cells(x, z, az_deg, dist):
        if (ix, iz) == (x, z):
            continue
        total += 1
        ah = anyh.get((ix, iz))
        if ah is not None and ah > ground_y + 2 and (ix, iz) not in wet:
            blocked += 1
    return blocked / float(total) if total else 0.0


def best_shots(top, bounds=None, radius=15, want_grass=True,
               az_step=30, top_n=12, min_open=0.80, max_slope=6.0):
    """积分图找平地 → Poisson 去重 → DDA 视线 → Pareto 排序。"""
    flats = find_open_ground(top, radius=radius, bounds=bounds,
                             want_grass=want_grass)
    if not flats:
        return []
    picked = _poisson_pick(flats, max(8.0, radius * 1.5), limit=40)
    if bounds is None:
        xs = [k[0] for k in top]
        zs = [k[1] for k in top]
        bounds = (min(xs), max(xs), min(zs), max(zs))
    _x0, _z0, height, valid, stand = _grid(top, bounds, want_grass=False)
    allowed = valid & stand
    dist = _distance_transform(allowed)
    slope_sob = _slope_grid(height, valid)

    shots = []
    for x, z, y, cnt in picked:
        ax, az = x - bounds[0], z - bounds[2]
        if not (0 <= ax < height.shape[1] and 0 <= az < height.shape[0]):
            continue
        ins = max(0.0, float(dist[az, ax]) - 1.0)
        # 单点 Sobel + 窗口 RANSAC 双校验；陡坡直接淘汰
        slope = max(float(slope_sob[az, ax]),
                    _ransac_slope(height, valid, bounds, x, z, radius=radius))
        if slope > max_slope:
            continue
        for angle in range(0, 360, az_step):
            if not _camera_side_ok(top, bounds, x, z, angle, y):
                continue
            o = _openness_grid(height, valid, bounds, x, z, angle, y)
            if o < min_open:
                continue
            wall = max(_canopy_wall_ratio(top, x, z, angle, y),
                          _canopy_wall_ratio(top, x, z, angle + 180, y))
            if wall > 0.45:
                continue
            rl = _relief_grid(height, valid, bounds, x, z, angle, y)
            hs = _horizon_grid(height, valid, bounds, x, z, angle, y)
            shots.append({'x': x, 'z': z, 'y': y, 'az': angle,
                          'openness': round(o, 3), 'relief': round(rl, 3),
                          'horizon': round(hs, 3), 'inscribed': round(ins, 2),
                          'slope': round(slope, 2), 'wall': round(wall, 3),
                          'score': round(_shot_score(o, rl), 2), 'flat': cnt})
    if not shots:
        return []
    # 多目标：开阔度、层次、内接面积、地平线；用"理想值"质量函数，
    # 避免 Pareto 奖励纯空地。
    def _keys(s):
        oq = 1.0 - abs(s['openness'] - 0.85) / 0.85
        rq = 1.0 - abs(s['relief'] - 0.12) / 0.12
        iq = min(1.0, s['inscribed'] / float(max(1, radius)))
        hq = s['horizon']
        return (oq, rq, iq, hq)
    ranks, crowd = _nsga2_rank(shots, _keys)
    for s, rk, cr in zip(shots, ranks, crowd):
        s['pareto'] = int(rk)
        s['crowding'] = round(float(cr), 4) if cr != float('inf') else 9999.0
    shots.sort(key=lambda s: (s['pareto'], -s['crowding'], -s['score'],
                              -s['openness'], -s['inscribed']))
    # 显式 NMS：同片广场只留一个，并保留多个方位角
    return _nms_shots(shots, max(8.0, radius * 1.5), top_n)


def rough_shots(top, bounds=None, az_step=30, top_n=6, progress=None):
    """兜底：只要求「这一列能站人」，按 5×5 同高度格数排序。"""
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
            if not _camera_side_ok(top, bounds, x, z, az, y):
                continue
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
        for r, min_open in ((max(6, radius // 2), 0.55), (6, 0.50), (4, 0.35),
                            (3, 0.25), (2, 0.20)):
            shots = best_shots(top, bounds=bounds, radius=r, want_grass=True,
                               az_step=az_step, top_n=top_n, min_open=min_open)
            if shots:
                if progress:
                    progress(f'    没有 {radius}×{radius} 平整草地，放宽到 {r}×{r} 后找到 {len(shots)} 个机位')
                break
    if not shots:
        for r, min_open in ((max(6, radius // 2), 0.55), (4, 0.35), (2, 0.20)):
            shots = best_shots(top, bounds=bounds, radius=r, want_grass=False,
                               az_step=az_step, top_n=top_n, min_open=min_open)
            if shots:
                if progress:
                    progress(f'    连草地都没有，改用「任意可站立地面」{r}×{r} → {len(shots)} 个机位')
                break
    if not shots:
        if progress:
            progress('    仍然没有成片平地，改用「能站人」兜底挑选')
        shots = rough_shots(top, bounds=bounds, az_step=az_step, top_n=top_n,
                            progress=progress)
    return w, top, shots


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='在 MC 存档里找适合拍摄的位置（0.6.0 算法版）')
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
