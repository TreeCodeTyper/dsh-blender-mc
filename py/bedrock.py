# -*- coding: utf-8 -*-
"""Bedrock 几何（.geo.json）→ OBJ

不绑定任何具体作品或模组：输入是资源包里的模型定义 + 一张贴图，输出是 Blender 能直接导入的 OBJ。
插件本身不带模型/贴图，转换对象全部由使用者提供。

设计要点
--------
1. Bedrock 模型单位是"像素"，16 像素 = 1 方块 → 统一除以 16
2. 骨骼是层级结构：world = parent_world @ T(pivot) @ R @ T(-pivot)
3. cube 的 origin 是【模型空间】的 min 角，size 是尺寸，inflate 是外扩
4. UV 支持两种：
   - box UV：uv 是 [u, v]，按 Minecraft 标准展开布局算六个面
   - per-face UV：uv 是 {north: {uv, uv_size}, ...}
5. 缠绕方向（winding）不追求正确 —— Blender 里会 normals_make_consistent 重算，
   但每个面的 UV 四角顺序必须与顶点顺序【一一对应】，否则贴图会乱

用法::

    from bedrock import convert
    convert("mob.geo.json", "mob.png", "out/mob.obj")

命令行::

    python py/bedrock.py <geo.json> <texture.png> <out.obj> [--anim anim.json]
"""
import json
import math
import os
import shutil
import sys

# 像素 → 方块
PX = 16.0

# 零厚度 cube 是合法的（翅膀/刀刃/鳍/贴图特效都这么做）。
# 直接跳过会缺零件，钳到一个极小厚度渲成薄片更接近原貌。
MIN_THICK = 0.12

FACE_NAMES = ("north", "south", "east", "west", "up", "down")


def _molang(expr, t=0.0):
    """极简 Molang 求值：把 q.anim_time 代成 t，支持 math.xxx。
    失败就返回 0.0（静态姿势下 t=0 通常就是 0）。"""
    if isinstance(expr, (int, float)):
        return float(expr)
    if not isinstance(expr, str):
        return 0.0
    s = expr
    for k in ("q.anim_time", "query.anim_time", "q.life_time",
              "q.time_alive", "query.life_time"):
        s = s.replace(k, str(t))
    s = s.replace("math.", "")
    ns = {k: getattr(math, k) for k in dir(math) if not k.startswith("_")}
    ns.update({"abs": abs, "min": min, "max": max, "round": round})
    try:
        return float(eval(s, {"__builtins__": {}}, ns))   # noqa: S307
    except Exception:
        return 0.0


def _num3(v, t=0.0):
    """把 [x,y,z] / {"0.0":[x,y,z]} / Molang 列表统一成 3 个 float"""
    if v is None:
        return None
    if isinstance(v, dict):
        if not v:
            return None
        v = v.get("0.0")
        if v is None:
            v = list(v.values())[0]
    if not isinstance(v, (list, tuple)):
        return None
    out = [_molang(x, t) for x in v[:3]]
    while len(out) < 3:
        out.append(0.0)
    return out


def _rot_matrix(rx, ry, rz):
    """Bedrock 骨骼旋转：角度制，顺序 Z→Y→X（Blockbench 约定即 XYZ 欧拉）"""
    if not rx and not ry and not rz:
        return None
    rx, ry, rz = math.radians(rx), math.radians(ry), math.radians(rz)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    # R = Rx @ Ry @ Rz
    return [
        [cy * cz, -cy * sz, sy],
        [cx * sz + sx * sy * cz, cx * cz - sx * sy * sz, -sx * cy],
        [sx * sz - cx * sy * cz, sx * cz + cx * sy * sz, cx * cy],
    ]


def _matmul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _apply(m, v):
    return [sum(m[i][k] * v[k] for k in range(3)) for i in range(3)]


def _add(a, b):
    return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]


def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _box_uv_faces(u, v, w, h, d):
    """Minecraft 标准 box UV 展开。返回 {面: (u0,v0,u1,v1)}，单位像素，V 向下。"""
    return {
        "down":  (u + d,          v,       u + d + w,       v + d),
        "up":    (u + d + w,      v,       u + d + w + w,   v + d),
        "east":  (u,              v + d,   u + d,           v + d + h),
        "north": (u + d,          v + d,   u + d + w,       v + d + h),
        "west":  (u + d + w,      v + d,   u + d + w + d,   v + d + h),
        "south": (u + d + w + d,  v + d,   u + d + w + d + w, v + d + h),
    }


def _norm_perface_uv(uvobj):
    """per-face UV（像素）→ {面: (u0,v0,u1,v1)}"""
    out = {}
    for face, data in uvobj.items():
        if face not in FACE_NAMES:
            continue
        if isinstance(data, dict):
            uu = data.get("uv", [0, 0])
            us = data.get("uv_size", [0, 0])
            out[face] = (uu[0], uu[1], uu[0] + us[0], uu[1] + us[1])
    return out


# 每个面的四个顶点（用 min/max 角表示），顺序 = UV 的 TL,TR,BR,BL
# 约定：侧面以 +Y 为上；up/down 面以 -Z 为上（贴图视角）
FACE_VERTS = {
    # 面名: [(角标组合), ...]  角标: 0=x0 1=x1 | 2=y0 3=y1 | 4=z0 5=z1
    "north": [(1, 3, 4), (0, 3, 4), (0, 2, 4), (1, 2, 4)],   # -Z 面
    "south": [(0, 3, 5), (1, 3, 5), (1, 2, 5), (0, 2, 5)],   # +Z 面
    "east":  [(1, 3, 5), (1, 3, 4), (1, 2, 4), (1, 2, 5)],   # +X 面
    "west":  [(0, 3, 4), (0, 3, 5), (0, 2, 5), (0, 2, 4)],   # -X 面
    "up":    [(0, 3, 4), (1, 3, 4), (1, 3, 5), (0, 3, 5)],   # +Y 面
    "down":  [(0, 2, 5), (1, 2, 5), (1, 2, 4), (0, 2, 4)],   # -Y 面
}


def read_identifier(geo_path):
    """读 geometry.description.identifier（没有就返回空串），用来猜模型名/贴图名。"""
    try:
        with open(geo_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        geos = data.get("minecraft:geometry") or []
        if geos:
            return str(geos[0].get("description", {}).get("identifier") or "")
    except Exception:
        pass
    return ""


def convert(geo_path, tex_path, out_obj, anim_path=None, anim_name=None,
            material="model", debug=True):
    """把一份 Bedrock 几何转成 OBJ（+ MTL + 贴图副本）。

    tex_path 可以为 None —— 那样只导出几何，材质是纯白。
    返回统计信息 dict（verts / faces / bbox / identifier / texture）。
    """
    with open(geo_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    # ---- 载入动画姿势（可选）----
    #   geo.json 存的是绑定姿势（常常是展开的），游戏里靠动画摆成正常站姿。
    #   所以可以叠加一段动画的 bone rotation。
    anim_rot = {}
    anim_pos = {}
    if anim_path:
        with open(anim_path, "r", encoding="utf-8") as f:
            ad = json.load(f)
        anims = ad.get("animations", {})
        key = None
        if anim_name and anim_name in anims:
            key = anim_name
        elif anim_name:
            for k in anims:
                if k.endswith("." + anim_name) or anim_name in k:
                    key = k
                    break
        if key is None and anims:
            key = list(anims.keys())[0]
        if key:
            entry = anims[key]
            for bn, bv in (entry.get("bones") or {}).items():
                r = _num3(bv.get("rotation"))
                if r is not None:
                    anim_rot[bn] = r
                p = _num3(bv.get("position"))
                if p is not None:
                    anim_pos[bn] = p
            if debug:
                print(f"[anim] {key} → rotation {len(anim_rot)} 骨骼, "
                      f"position {len(anim_pos)} 骨骼")

    geos = data.get("minecraft:geometry")
    if not geos:
        raise SystemExit("没有 minecraft:geometry")
    geo = geos[0]
    desc = geo.get("description", {})
    tw = desc.get("texture_width", 64)
    th = desc.get("texture_height", 64)
    bones = geo.get("bones", [])

    if debug:
        print(f"[geo] identifier={desc.get('identifier')}  "
              f"tex={tw}x{th}  bones={len(bones)}")

    # ---- 骨骼层级 → 世界矩阵 ----
    bone_by_name = {b.get("name"): b for b in bones}

    def bone_world(name, seen=None):
        """递归算骨骼世界矩阵（返回 (R, T) 表示 p' = R@p + T）

        骨骼旋转的来源优先级：
          动画里的 rotation（有就覆盖）> geo.json 里骨骼自带的 rotation
        这是 GeckoLib 的行为：动画的骨骼旋转覆盖模型默认旋转。
        """
        seen = seen or set()
        if name in seen or name not in bone_by_name:
            return ([1, 0, 0], [0, 1, 0], [0, 0, 1]), [0, 0, 0]
        seen.add(name)
        b = bone_by_name[name]
        pivot = list(b.get("pivot", [0, 0, 0]))
        if name in anim_pos:
            pivot = [pivot[i] + anim_pos[name][i] for i in range(3)]
        deg = anim_rot.get(name)
        if deg is None:
            deg = b.get("rotation") or [0, 0, 0]
        rot = _rot_matrix(*deg)
        identity = (rot is None)
        R = rot if rot else [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
        T = [0, 0, 0] if identity else _sub(pivot, _apply(R, pivot))
        parent = b.get("parent")
        if parent:
            pR, pT = bone_world(parent, seen)
            R = _matmul(pR, R)
            T = _add(_apply(pR, T), pT)
        return R, T

    verts = []      # [(x,y,z)] 已 /16
    uvs = []        # [(u,v)] Blender 坐标（V 已翻转）
    faces = []      # [([v...], [uv...])]
    cube_total = 0
    cube_done = 0

    for b in bones:
        cubes = b.get("cubes") or []
        cube_total += len(cubes)
        if not cubes:
            continue
        bR, bT = bone_world(b.get("name"))

        for c in cubes:
            origin = list(c.get("origin", [0, 0, 0]))
            size = list(c.get("size", [1, 1, 1]))
            inflate = float(c.get("inflate", 0.0) or 0.0)
            o = [origin[i] - inflate for i in range(3)]
            s = [abs(size[i]) + inflate * 2 for i in range(3)]
            for _i in range(3):
                if s[_i] < MIN_THICK:
                    s[_i] = MIN_THICK

            # cube 自身的 pivot / rotation
            cR = _rot_matrix(*(c.get("rotation") or [0, 0, 0]))
            cp = c.get("pivot", [0, 0, 0])
            cT = _sub(cp, _apply(cR, cp)) if cR else [0, 0, 0]

            def xform(p):
                q = _apply(cR, p) if cR else p
                q = _add(q, cT)
                q = _apply(bR, q)
                q = _add(q, bT)
                return [q[0] / PX, q[1] / PX, q[2] / PX]

            x0, y0, z0 = o
            x1, y1, z1 = o[0] + s[0], o[1] + s[1], o[2] + s[2]
            corners = {0: x0, 1: x1, 2: y0, 3: y1, 4: z0, 5: z1}
            cw = {}
            for key in (0, 1):
                for ky in (2, 3):
                    for kz in (4, 5):
                        lp = [corners[key], corners[ky], corners[kz]]
                        cw[(key, ky, kz)] = xform(lp)

            uvspec = c.get("uv")
            if isinstance(uvspec, dict):
                faceuv = _norm_perface_uv(uvspec)
            elif isinstance(uvspec, (list, tuple)) and len(uvspec) >= 2:
                faceuv = _box_uv_faces(uvspec[0], uvspec[1], s[0], s[1], s[2])
            else:
                continue

            mirror = bool(c.get("mirror"))
            for face, corners4 in FACE_VERTS.items():
                if face not in faceuv:
                    continue
                u0, v0, u1, v1 = faceuv[face]
                # 像素 → 0..1，V 翻转（Bedrock V 向下，Blender V 向上）
                ntu = lambda uu: uu / float(tw)              # noqa: E731
                ntv = lambda vv: 1.0 - vv / float(th)        # noqa: E731
                if mirror:
                    cu = [ntu(u1), ntu(u0), ntu(u0), ntu(u1)]
                else:
                    cu = [ntu(u0), ntu(u1), ntu(u1), ntu(u0)]
                cv = [ntv(v0), ntv(v0), ntv(v1), ntv(v1)]

                vi = []
                for corner in corners4:
                    vi.append(len(verts) + 1)   # OBJ 1-based
                    verts.append(cw[corner])
                ui = []
                for k in range(4):
                    uvs.append((cu[k], cv[k]))
                    ui.append(len(uvs))
                faces.append((vi, ui))
            cube_done += 1

    # ---- 写 OBJ ----
    mat = material or "model"
    mtl_name = os.path.basename(out_obj).rsplit(".", 1)[0] + ".mtl"
    tex_name = os.path.basename(tex_path) if tex_path else ""
    with open(out_obj, "w", encoding="utf-8") as f:
        f.write(f"# Bedrock -> OBJ  (source: {os.path.basename(geo_path)})\n")
        f.write(f"mtllib {mtl_name}\n")
        f.write(f"usemtl {mat}\n")
        for v in verts:
            f.write(f"v {v[0]:.5f} {v[1]:.5f} {v[2]:.5f}\n")
        for u in uvs:
            f.write(f"vt {u[0]:.6f} {u[1]:.6f}\n")
        for vi, ui in faces:
            f.write("f " + " ".join(f"{vi[k]}/{ui[k]}" for k in range(4)) + "\n")

    mtl_path = os.path.join(os.path.dirname(out_obj), mtl_name)
    with open(mtl_path, "w", encoding="utf-8") as f:
        f.write(f"newmtl {mat}\nKa 1 1 1\nKd 1 1 1\nKs 0 0 0\nd 1\nillum 1\n")
        if tex_name:
            f.write(f"map_Kd {tex_name}\n")

    if tex_path:
        dst_tex = os.path.join(os.path.dirname(out_obj), tex_name)
        if os.path.abspath(tex_path) != os.path.abspath(dst_tex):
            shutil.copy2(tex_path, dst_tex)

    # ---- 统计（用于验证几何） ----
    xs = [v[0] for v in verts]
    ys = [v[1] for v in verts]
    zs = [v[2] for v in verts]
    if debug:
        print(f"[out] {out_obj}")
        print(f"[out] 顶点 {len(verts)}  UV {len(uvs)}  面 {len(faces)}  "
              f"cube {cube_done}/{cube_total}")
        if verts:
            print(f"[out] 包围盒 X[{min(xs):.2f},{max(xs):.2f}] "
                  f"Y[{min(ys):.2f},{max(ys):.2f}] "
                  f"Z[{min(zs):.2f},{max(zs):.2f}]  （单位：方块）")
            print(f"[out] 尺寸 {max(xs)-min(xs):.2f} x "
                  f"{max(ys)-min(ys):.2f} x {max(zs)-min(zs):.2f} 方块")
        print(f"[out] 贴图 {tex_name or '(无)'}  材质 {mtl_name}")
    return {
        "verts": len(verts),
        "faces": len(faces),
        "cubes": cube_done,
        "cubes_total": cube_total,
        "bbox": ((min(xs), max(xs), min(ys), max(ys), min(zs), max(zs))
                 if verts else None),
        "identifier": desc.get("identifier") or "",
        "texture": tex_name,
    }


def _main():
    import argparse
    ap = argparse.ArgumentParser(description="Bedrock .geo.json → OBJ")
    ap.add_argument("geo")
    ap.add_argument("tex", nargs="?", default=None, help="贴图 png（可省略）")
    ap.add_argument("out")
    ap.add_argument("--anim", default=None, help="animation.json 路径")
    ap.add_argument("--anim-name", default=None, help="要套用的动画名（缺省取第一条）")
    a = ap.parse_args()
    convert(a.geo, a.tex, a.out, anim_path=a.anim, anim_name=a.anim_name)
    return 0


if __name__ == "__main__":
    sys.exit(_main())
