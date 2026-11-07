# -*- coding: utf-8 -*-
"""把 mc_camera_path 的点列写进一个 Blender 工程，覆盖工程自带相机。

用法（由 film.py 调用，不直接面向用户）：

    blender -b <blend> --python apply_camera_path.py -- <path.json> <out.blend> <total>

path.json 可以是：
  {"points": [[x,z,y], ...], "look_at": [x,y,z]}   # look_at 可选
  [[x,z,y], ...]
点列来自 py/camera_path.py，坐标顺序是 (x, z, y)，正好对应 Blender 的
(x, y, z) 三个分量，所以直接当 location 用。
"""
import json
import math
import os
import sys

import bpy
from mathutils import Vector


def _args():
    argv = sys.argv
    if '--' in argv:
        argv = argv[argv.index('--') + 1:]
    if len(argv) < 2:
        raise SystemExit('usage: apply_camera_path.py -- <path.json> <out.blend> [total]')
    path = argv[0]
    out = argv[1]
    total = int(argv[2]) if len(argv) > 2 else 240
    return path, out, total


def _load(path):
    with open(path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if isinstance(data, dict):
        pts = data.get('points') or []
        look = data.get('look_at')
    else:
        pts = data
        look = None
    pts = [tuple(float(v) for v in p) for p in pts if len(p) >= 3]
    if len(pts) < 2:
        raise SystemExit('camera path needs at least 2 points')
    return pts, (tuple(float(v) for v in look) if look else None)


def _subject():
    """找角色骨架（优先 BPS 的 *.arma）。"""
    best = None
    for ob in bpy.data.objects:
        if ob.type == 'ARMATURE' and not ob.hide_render:
            if best is None or 'arma' in ob.name.lower():
                best = ob
    return best


def _subject_target(arm):
    """对准头部；没有 Head 骨就退到骨架原点上方 1.5 格。"""
    pb = arm.pose.bones.get('Head')
    if pb is not None:
        return arm.matrix_world @ pb.head + Vector((0.0, 0.0, 0.15))
    return arm.matrix_world.translation + Vector((0.0, 0.0, 1.5))


def main():
    path, out, total = _args()
    pts, look = _load(path)
    scene = bpy.context.scene
    cam = scene.camera or bpy.data.objects.get('Camera')
    if cam is None:
        raise SystemExit('blend 里没有相机')

    arm = _subject()
    n = len(pts)
    frames = [1 + round(i * (total - 1) / (n - 1)) for i in range(n)]
    for i, (x, z, y) in enumerate(pts):
        f = frames[i]
        scene.frame_set(f)
        cam.location = (x, z, y)
        if look:
            target = Vector(look)
        elif arm is not None:
            # 路径只负责“相机在哪”，看向哪里由角色决定：不会再把镜头甩向天空。
            target = _subject_target(arm)
        else:
            nx, nz, ny = pts[min(i + 1, n - 1)]
            if i + 1 >= n:
                nx, nz, ny = pts[max(0, i - 1)]
            target = Vector((nx, nz, ny + 1.2))
        direction = target - cam.location
        if direction.length > 1e-6:
            cam.rotation_euler = direction.to_track_quat('-Z', 'Y').to_euler()
        cam.keyframe_insert('location', frame=f)
        cam.keyframe_insert('rotation_euler', frame=f)

    scene.frame_start = 1
    scene.frame_end = max(scene.frame_end, total)
    bpy.ops.wm.save_as_mainfile(filepath=out)
    print('camera path applied:', n, 'points ->', out)


main()
