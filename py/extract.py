# -*- coding: utf-8 -*-
"""批量模型提取：Bedrock 几何 → OBJ（交给 Blender 摆放）

目录里有什么就转什么 —— 不认模组名、不认作品名、不联网下载。
素材由使用者自备，转换结果也只落在使用者指定的目录里。

    python py/cli.py extract <资源目录> --out <输出目录> [--filter 关键词]
                              [--anim animation.json] [--list]

产物结构::

    <out>/
      models.json                 ← 清单：每个模型的来源、面数、包围盒
      <name>/<name>.obj
      <name>/<name>.mtl
      <name>/<贴图>.png
      <name>/model.json           ← 单个模型的元信息
"""
import json
import os
import re
import time

import bedrock

GEO_SUFFIXES = (".geo.json",)
MODEL_META = "model.json"
MANIFEST = "models.json"


# ───────────────────────────── 找文件 ─────────────────────────────
def is_geo(path):
    low = path.lower()
    return any(low.endswith(s) for s in GEO_SUFFIXES)


def find_geos(root):
    """递归找 .geo.json，按路径排序（结果稳定，方便复现）。"""
    found = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for fn in filenames:
            if is_geo(fn):
                found.append(os.path.join(dirpath, fn))
    found.sort()
    return found


def _identifier_tail(identifier):
    """geometry.namespace.name → name；namespace:name → name"""
    if not identifier:
        return ""
    s = identifier.split("geometry.")[-1]
    s = s.split(":")[-1]
    return s.strip()


def guess_texture(geo_path, identifier="", root=None):
    """给一份几何猜贴图：同名 → 资源目录里同名 → 同目录唯一 png。"""
    geo_dir = os.path.dirname(geo_path)
    base = os.path.basename(geo_path)
    for suf in GEO_SUFFIXES:
        if base.lower().endswith(suf):
            stem = base[: -len(suf)]
            break
    else:
        stem = os.path.splitext(base)[0]

    names = []
    for n in (stem, _identifier_tail(identifier)):
        if n and n not in names:
            names.append(n)

    # ① 几何文件旁边
    for n in names:
        for cand in (n + ".png", n + "_texture.png", n + "_tex.png"):
            p = os.path.join(geo_dir, cand)
            if os.path.isfile(p):
                return p

    # ② 资源目录里同名（大小写不敏感）
    if root:
        want = {n.lower() for n in names}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames.sort()
            for fn in sorted(filenames):
                if not fn.lower().endswith(".png"):
                    continue
                if os.path.splitext(fn)[0].lower() in want:
                    return os.path.join(dirpath, fn)

    # ③ 同目录只有一张 png 就是它
    try:
        pngs = [f for f in os.listdir(geo_dir) if f.lower().endswith(".png")]
    except OSError:
        pngs = []
    if len(pngs) == 1:
        return os.path.join(geo_dir, pngs[0])
    return None


def sanitize(name, fallback="model"):
    s = (name or "").strip()
    s = re.sub(r"\.geo$", "", s, flags=re.I)
    s = re.sub(r"[^\w\-.]+", "_", s, flags=re.U)
    s = s.strip("._-")
    return s or fallback


# ───────────────────────────── 转一个 ─────────────────────────────
def extract_one(geo_path, out_root, tex=None, anim=None, anim_name=None,
                name=None, identifier=None):
    ident = identifier if identifier is not None else bedrock.read_identifier(geo_path)
    nm = sanitize(name or _identifier_tail(ident) or
                  os.path.basename(geo_path).replace(".geo.json", ""))
    dst = os.path.join(out_root, nm)
    if os.path.exists(dst):
        i = 2
        while os.path.exists(f"{dst}_{i}"):
            i += 1
        nm = f"{nm}_{i}"
        dst = os.path.join(out_root, nm)
    os.makedirs(dst, exist_ok=True)

    if tex is None:
        tex = guess_texture(geo_path, ident)
    obj = os.path.join(dst, nm + ".obj")
    stats = bedrock.convert(geo_path, tex, obj, anim_path=anim,
                            anim_name=anim_name, material="model", debug=False)
    meta = {
        "name": nm,
        "source": os.path.abspath(geo_path),
        "identifier": ident,
        "texture": os.path.basename(tex) if tex else None,
        "texture_source": os.path.abspath(tex) if tex else None,
        "obj": os.path.basename(obj),
        "verts": stats["verts"],
        "faces": stats["faces"],
        "cubes": f'{stats["cubes"]}/{stats["cubes_total"]}',
        "bbox": stats["bbox"],
    }
    with open(os.path.join(dst, MODEL_META), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


# ───────────────────────────── 批量 ─────────────────────────────
def extract(root, out, keyword=None, anim=None, anim_name=None,
            list_only=False, progress=print):
    """把一个资源目录里的 Bedrock 几何全部转成 OBJ，返回清单 dict。"""
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise SystemExit(f"资源目录不存在: {root}")
    geos = find_geos(root)
    if keyword:
        kw = keyword.lower()
        geos = [g for g in geos if kw in os.path.relpath(g, root).lower()]
    if not geos:
        progress(f"没找到 Bedrock 几何（*{' / *'.join(GEO_SUFFIXES)}）"
                 f"{'（筛选: ' + keyword + '）' if keyword else ''}")
        return {"root": root, "out": os.path.abspath(out), "models": [],
                "errors": [], "count": 0}

    progress(f"找到 {len(geos)} 份几何"
             f"{'（筛选: ' + keyword + '）' if keyword else ''}")

    if list_only:
        rows = []
        for g in geos:
            ident = bedrock.read_identifier(g)
            tex = guess_texture(g, ident, root)
            rows.append({
                "source": os.path.relpath(g, root).replace(os.sep, "/"),
                "name": sanitize(_identifier_tail(ident) or
                                 os.path.basename(g).replace(".geo.json", "")),
                "identifier": ident,
                "texture": (os.path.relpath(tex, root).replace(os.sep, "/")
                            if tex else None),
            })
            progress(f"  {rows[-1]['name']:24s} "
                     f"{rows[-1]['source']}"
                     f"{'' if tex else '   (没配到贴图)'}")
        return {"root": root, "out": os.path.abspath(out), "list": rows,
                "count": len(rows), "dry_run": True}

    os.makedirs(out, exist_ok=True)
    models, errors = [], []
    for i, g in enumerate(geos, 1):
        try:
            m = extract_one(g, out, anim=anim, anim_name=anim_name)
            models.append(m)
            progress(f"  [{i}/{len(geos)}] {m['name']}  "
                     f"{m['verts']} 顶点 / {m['faces']} 面  "
                     f"贴图{'✓' if m['texture'] else '✗'}")
        except Exception as e:                       # 单个失败不影响整批
            rel = os.path.relpath(g, root)
            errors.append({"source": rel, "error": f"{type(e).__name__}: {e}"})
            progress(f"  [{i}/{len(geos)}] ✗ {rel} —— {type(e).__name__}: {e}")

    manifest = {
        "root": root,
        "out": os.path.abspath(out),
        "keyword": keyword,
        "anim": os.path.abspath(anim) if anim else None,
        "extracted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(models),
        "failed": len(errors),
        "models": models,
        "errors": errors,
        "note": "模型与贴图由使用者提供；插件不附带、不下载任何第三方素材。"
                "分发提取结果前请自行确认素材授权。",
    }
    with open(os.path.join(out, MANIFEST), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    progress(f"\n完成 {len(models)} 个"
             f"{'，失败 ' + str(len(errors)) if errors else ''} → {out}")
    progress(f"清单: {os.path.join(out, MANIFEST)}")
    return manifest
