"""3D 皮肤外层：把皮肤 outer layer 的不透明像素挤出成有厚度的块。

BPS 的 `mesh layer *` 是 64×64 皮肤布局下的外层网格，每个面正好对应
一个皮肤像素。这里按像素把不透明像素沿法线挤出，得到 Minecraft
"3D Skin Layers" 那种头发/帽子/衣服的厚度。

头部只挤头发/帽子，正面下半（眼睛/嘴）保留平面的皮肤脸；
身体、手臂、腿的外层也一起挤，衣服和袖子会有体积。

原创实现，只使用 BPS 自带网格与用户皮肤贴图。
"""

import bpy

_LAYER_PREFIX = 'BPS v4 - mesh layer'


def _skin_image():
    for m in bpy.data.materials:
        if m.name.split('.')[0] != 'Skin' or not m.use_nodes:
            continue
        for n in m.node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image:
                return n.image
    return None


def _skin_material():
    for m in bpy.data.materials:
        if m.name.split('.')[0] == 'Skin' and m.use_nodes:
            for n in m.node_tree.nodes:
                if n.type == 'TEX_IMAGE' and n.image:
                    return m
    return None


def _layers():
    """所有可用的 BPS 外层网格；跳过 slim 备用件和隐藏对象。"""
    for ob in bpy.data.objects:
        if ob.type != 'MESH' or not ob.name.startswith(_LAYER_PREFIX):
            continue
        if 'slim' in ob.name.lower() or ob.hide_render:
            continue
        me = ob.data
        if not me.polygons or not me.uv_layers.active:
            continue
        if not any(m and m.name.split('.')[0] == 'Skin' for m in me.materials):
            continue
        yield ob


def _pixel(img, u, v):
    w, h = img.size[:]
    x = min(w - 1, max(0, int(u * w)))
    y = min(h - 1, max(0, int((1.0 - v) * h)))
    i = (y * w + x) * 4
    px = img.pixels
    return px[i], px[i + 1], px[i + 2], px[i + 3]


def _box(verts, faces, uvs, base, normal, thickness, uv_face, cuv):
    """一个像素的薄盒：顶面 + 底面 + 四侧。UV 侧面用该像素中心色。"""
    nv = len(base)
    b0 = len(verts)
    top = [v + normal * thickness for v in base]
    verts.extend(base + top)
    faces.append(tuple(range(b0, b0 + nv))[::-1])
    faces.append(tuple(range(b0 + nv, b0 + 2 * nv)))
    uvs.append(list(reversed(uv_face)))
    uvs.append(list(uv_face))
    for k in range(nv):
        k2 = (k + 1) % nv
        faces.append((b0 + k, b0 + k2, b0 + nv + k2, b0 + nv + k))
        uvs.append([cuv] * 4)


def _face_group(ob, poly):
    """一个像素面主要跟随哪根骨（取四角权重和最大的组）。"""
    scores = {}
    for vi in poly.vertices:
        v = ob.data.vertices[vi]
        for g in v.groups:
            name = ob.vertex_groups[g.group].name
            scores[name] = scores.get(name, 0.0) + g.weight
    return max(scores, key=scores.get) if scores else None


def _build_layer(ob, img, thickness, skip_front):
    me = ob.data
    uv = me.uv_layers.active.data
    head = 'mesh layer head' in ob.name
    head_z = ob.location.z
    verts, faces, uvdata, face_groups = [], [], [], []
    opaque = 0

    for poly in me.polygons:
        lis = list(poly.loop_indices)
        if len(lis) != 4:
            continue
        uvs = [uv[li].uv.copy() for li in lis]
        cu = sum(u.x for u in uvs) / 4.0
        cv = sum(u.y for u in uvs) / 4.0
        _, _, _, alpha = _pixel(img, cu, cv)
        if alpha < 0.5:
            continue
        base = [me.vertices[me.loops[li].vertex_index].co.copy() for li in lis]
        cz = sum(v.z for v in base) / 4.0
        # 头部正面下半部分（眼睛/嘴）不挤，避免脸变成马赛克；保留刘海。
        if head and skip_front and poly.normal.y < -0.5 and cz < head_z + 0.12:
            continue
        opaque += 1
        b0 = len(verts)
        _box(verts, faces, uvdata, base, poly.normal.normalized(),
             thickness, [(u.x, u.y) for u in uvs], (cu, cv))
        face_groups.append((_face_group(ob, poly), b0, len(verts)))

    if not opaque:
        return 0, 0, 0

    tag = ob.name[len(_LAYER_PREFIX):].strip(' -') or 'layer'
    new = bpy.data.meshes.new(f'BPS3D {tag}')
    new.from_pydata([tuple(v) for v in verts], [], faces)
    new.update()
    old_mat = me.materials[0] if me.materials else None
    if old_mat:
        new.materials.append(old_mat)
    uvl = new.uv_layers.new(name='UVMap')
    for pi, poly in enumerate(new.polygons):
        src = uvdata[pi]
        for k, li in enumerate(poly.loop_indices):
            uvl.data[li].uv = src[k]

    # 保留原来的骨骼权重名：每个像素盒整块归到它主要跟随的骨。
    # 必须在 ob.data = new 之后写权重，否则权重会写进旧 mesh 并随替换丢失。
    names = sorted({g for g, _, _ in face_groups if g})
    old = ob.data
    ob.data = new
    try:
        bpy.data.meshes.remove(old)
    except Exception:
        pass
    ob.vertex_groups.clear()
    vgs = {n: ob.vertex_groups.new(name=n) for n in names}
    for g, a, b in face_groups:
        if g in vgs:
            vgs[g].add(list(range(a, b)), 1.0, 'REPLACE')
    return opaque, len(new.vertices), len(new.polygons)


def _solidify_body(ob, thickness):
    """身体外层不逐像素挤（会露出破洞），改成 Solidify 加一层衣料厚度。"""
    if ob.hide_render:
        return False
    for m in ob.modifiers:
        if m.type == 'SOLIDIFY' and m.name == 'Skin3D Solidify':
            return False
    m = ob.modifiers.new('Skin3D Solidify', 'SOLIDIFY')
    m.thickness = thickness
    m.offset = 1.0
    m.use_rim = True
    return True


def apply_skin3d(arm, thickness=0.02, skip_front=True, log=None):
    """头部逐像素挤出头发/帽子；身体外层加 Solidify 衣料厚度。"""
    p = log or (lambda *a, **k: None)
    img = _skin_image()
    if not img:
        p('    3D 皮肤：找不到 Skin 贴图，跳过')
        return {'ok': False, 'pixels': 0, 'verts': 0, 'faces': 0, 'layers': []}

    total_px = total_v = total_f = 0
    done = []
    body = 0
    for ob in list(_layers()):
        if 'head' in ob.name:
            try:
                px, nv, nf = _build_layer(ob, img, thickness, skip_front)
            except Exception as e:
                p(f'    3D 皮肤：{ob.name} 失败（{type(e).__name__}: {e}）')
                continue
            if px:
                total_px += px
                total_v += nv
                total_f += nf
                done.append(f'head:{px}')
        else:
            try:
                if _solidify_body(ob, thickness):
                    body += 1
            except Exception as e:
                p(f'    3D 皮肤：{ob.name} Solidify 失败（{type(e).__name__}: {e}）')

    if not total_px and not body:
        p('    3D 皮肤：没有可处理的头部/身体外层')
        return {'ok': False, 'pixels': 0, 'verts': 0, 'faces': 0, 'layers': []}

    p(f'    3D 皮肤：头部挤出 {total_px} 像素，{total_v} 顶点 / {total_f} 面，'
      f'身体 Solidify {body} 层，厚度 {thickness}')
    return {'ok': True, 'pixels': total_px, 'verts': total_v,
            'faces': total_f, 'layers': done + ([f'body:{body}'] if body else [])}
