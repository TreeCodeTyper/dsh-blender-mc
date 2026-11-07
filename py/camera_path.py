# -*- coding: utf-8 -*-
"""camera_path —— 相机路径：A* / Theta* / Catmull-Rom（0.6.0）

输入是 scan.surface_map 的地表字典；输出带高度的路径点。
- A*：8 邻域，代价=步长*(1+坡度*2)
- Theta*：若父节点到当前节点有视线，直接连父节点，减少锯齿
- Catmull-Rom：对稀疏路径做平滑重采样
"""
import heapq
import json
import math

import numpy as np

try:
    from scan import _grid, _ray_cells
except Exception:  # 作为包导入时
    from .scan import _grid, _ray_cells


def _line_clear(height, valid, x0, z0, x1, z1, max_step=0.6):
    """Theta* 视线：采样直线，检查是否经过无效/陡增格。"""
    h, w = height.shape
    dist = math.hypot(x1 - x0, z1 - z0)
    n = max(2, int(dist / max_step))
    last = None
    for i in range(n + 1):
        t = i / float(n)
        x = int(round(x0 + (x1 - x0) * t))
        z = int(round(z0 + (z1 - z0) * t))
        if not (0 <= x < w and 0 <= z < h) or not valid[z, x]:
            return False
        y = float(height[z, x])
        if last is not None and abs(y - last) > 2.5:
            return False
        last = y
    return True


def astar(height, valid, start, goal):
    """8 邻域 A*，返回 [(x,z)]；不可达返回 []。"""
    h, w = height.shape
    sx, sz = int(start[0]), int(start[1])
    gx, gz = int(goal[0]), int(goal[1])
    if not (0 <= sx < w and 0 <= sz < h and valid[sz, sx]):
        return []
    if not (0 <= gx < w and 0 <= gz < h and valid[gz, gx]):
        return []
    nbr = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]
    openq = [(0.0, sx, sz)]
    came = {}
    gscore = {(sx, sz): 0.0}
    closed = set()
    while openq:
        _, x, z = heapq.heappop(openq)
        if (x, z) in closed:
            continue
        if (x, z) == (gx, gz):
            path = []
            cur = (x, z)
            while cur in came:
                path.append(cur)
                cur = came[cur]
            path.append((sx, sz))
            return path[::-1]
        closed.add((x, z))
        for dx, dz in nbr:
            nx, nz = x + dx, z + dz
            if not (0 <= nx < w and 0 <= nz < h) or not valid[nz, nx]:
                continue
            step = math.hypot(dx, dz)
            slope = abs(float(height[nz, nx]) - float(height[z, x])) / step
            cost = step * (1.0 + 2.0 * slope)
            ng = gscore[(x, z)] + cost
            if ng < gscore.get((nx, nz), 1e30):
                gscore[(nx, nz)] = ng
                came[(nx, nz)] = (x, z)
                f = ng + math.hypot(gx - nx, gz - nz)
                heapq.heappush(openq, (f, nx, nz))
    return []


def theta_smooth(path, height, valid):
    """Theta* 后处理：能直连就直连，去掉栅格锯齿。"""
    if len(path) < 3:
        return path
    out = [path[0]]
    i = 0
    while i < len(path) - 1:
        j = len(path) - 1
        while j > i + 1:
            if _line_clear(height, valid, path[i][0], path[i][1],
                           path[j][0], path[j][1]):
                break
            j -= 1
        out.append(path[j])
        i = j
    return out


def catmull_rom(points, samples_per_seg=8):
    """Catmull-Rom 平滑重采样；points 是 [(x,z)]。"""
    if len(points) < 2:
        return list(points)
    p = [points[0]] + list(points) + [points[-1]]
    out = []
    for i in range(1, len(p) - 2):
        p0, p1, p2, p3 = p[i - 1], p[i], p[i + 1], p[i + 2]
        for s in range(samples_per_seg):
            t = s / float(samples_per_seg)
            t2, t3 = t * t, t * t * t
            x = 0.5 * ((2 * p1[0]) + (-p0[0] + p2[0]) * t +
                       (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2 +
                       (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3)
            z = 0.5 * ((2 * p1[1]) + (-p0[1] + p2[1]) * t +
                       (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2 +
                       (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3)
            out.append((x, z))
    out.append(points[-1])
    return out


def plan_camera_path(top, start, goal, bounds=None, samples_per_seg=8,
                     height_offset=2.5):
    """一站式：A* → Theta* → Catmull-Rom，返回 [(x,z,y)]。"""
    if bounds is None:
        xs = [k[0] for k in top]
        zs = [k[1] for k in top]
        bounds = (min(xs), max(xs), min(zs), max(zs))
    x0, z0, height, valid, _ = _grid(top, bounds, want_grass=False)
    sx, sz = int(start[0]), int(start[1])
    gx, gz = int(goal[0]), int(goal[1])
    path = astar(height, valid, (sx - x0, sz - z0), (gx - x0, gz - z0))
    if not path:
        return []
    # ★ 平滑必须在局部坐标做：height/valid 的索引是 [z-z0, x-x0]
    smooth = theta_smooth(path, height, valid)
    fine = catmull_rom(smooth, samples_per_seg)
    out = []
    for lx, lz in fine:
        ax, az = int(round(lx)), int(round(lz))
        if 0 <= ax < height.shape[1] and 0 <= az < height.shape[0]:
            if valid[az, ax]:
                out.append((round(float(lx + x0), 2), round(float(lz + z0), 2),
                            round(float(height[az, ax]) + height_offset, 2)))
    return out


if __name__ == '__main__':
    import argparse
    from anvil import World
    from scan import surface_map

    ap = argparse.ArgumentParser(description='A*/Theta*/Catmull-Rom 相机路径')
    ap.add_argument('save')
    ap.add_argument('--area', required=True, help='x0,x1,z0,z1')
    ap.add_argument('--start', required=True, help='x,z')
    ap.add_argument('--goal', required=True, help='x,z')
    ap.add_argument('--out', default='')
    a = ap.parse_args()
    x0, x1, z0, z1 = (int(v) for v in a.area.split(','))
    st = tuple(int(v) for v in a.start.split(','))
    gl = tuple(int(v) for v in a.goal.split(','))
    w = World(a.save)
    top = surface_map(w, x0, x1, z0, z1, y0=50, y1=200)
    path = plan_camera_path(top, st, gl, bounds=(x0, x1, z0, z1))
    data = {'start': list(st), 'goal': list(gl), 'points': path}
    print(json.dumps(data, ensure_ascii=False, indent=2))
    if a.out:
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print('wrote', a.out)
