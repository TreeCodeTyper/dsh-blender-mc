# -*- coding: utf-8 -*-
"""Blender 4.x / 5.x 合成器 Glare 兼容层。

为什么要有它
-----------
Blender 5.x 把合成器整体换掉了：

* 合成树挂在 ``scene.compositing_node_group``（``scene.node_tree`` 已不存在）；
* 输出节点是 ``NodeGroupOutput``（没有 ``CompositorNodeComposite``）；
* ``CompositorNodeGlare`` 变成 socket 化节点——Type/Quality 是菜单 socket，
  Threshold/Strength/Size 是输入 socket，不再有 ``glare_type``/``quality``/
  ``threshold``/``size``/``mix`` 这些节点属性；
* 老代码「先删 GLARE，再找 COMPOSITE 重连」在 5.x 上会：删掉链路中间的
  Glare → ``next(... COMPOSITE)`` 抛 StopIteration → 被 ``except`` 吞掉 →
  合成器断了输入，整帧输出纯灰，而工具仍然报“完成”。

这个模块只做一件事：把 Glare 装回去，装不上就**保证画面还能正常出**
（旁路成无辉光 / 关合成器直出），绝不留下断链的合成树。
"""

import bpy  # noqa: E402

# 4.x 的枚举 → 5.x 菜单 socket 的显示名
_MENU_TYPES = {
    'FOG_GLOW': 'Fog Glow',
    'BLOOM': 'Bloom',
    'STREAKS': 'Streaks',
    'SIMPLE_STAR': 'Simple Star',
    'GHOSTS': 'Ghosts',
    'SUN_BEAMS': 'Sun Beams',
}


def _set_socket(node, name, value):
    """给输入 socket 赋值；名字不存在/类型不对就静默跳过。"""
    sock = node.inputs.get(name)
    if sock is None:
        return False
    try:
        sock.default_value = value
        return True
    except Exception:
        return False


def _find_output(nt):
    for n in nt.nodes:
        if n.type in ('COMPOSITE', 'GROUP_OUTPUT'):
            return n
    for n in nt.nodes:
        if getattr(n, 'is_active_output', False):
            return n
    return None


def _find_tree(sc):
    for attr in ('node_tree', 'compositing_node_group'):
        cand = getattr(sc, attr, None)
        if cand is not None and hasattr(cand, 'nodes'):
            return cand
    return None


def _bypass_glare(nt):
    """删掉旧 Glare 并把上游直接接到下游，返回 (源 socket, 下游 socket 列表)。"""
    src, targets = None, []
    for n in list(nt.nodes):
        if n.type != 'GLARE':
            continue
        img_in = n.inputs.get('Image')
        if img_in is not None and img_in.links:
            src = img_in.links[0].from_socket
        for link in n.outputs['Image'].links:
            targets.append(link.to_socket)
        nt.nodes.remove(n)
    for t in targets:
        if src is not None:
            nt.links.new(src, t)
    return src, targets


def _degrade(sc, nt, log, reason):
    """装不上 Glare 时的兜底：先保证输出有图，再不行就关合成器。"""
    log('    !! 辉光节点不可用: %s' % reason)
    try:
        comp = _find_output(nt)
        rl = next((n for n in nt.nodes if n.type == 'R_LAYERS'), None)
        if comp is not None and rl is not None and not comp.inputs['Image'].links:
            nt.links.new(rl.outputs['Image'], comp.inputs['Image'])
            log('    已把 Render Layers 直连输出（降级为无辉光）')
            return False
    except Exception:
        pass
    try:
        sc.render.use_compositing = False
        log('    已关闭合成器（降级为直出）')
    except Exception:
        pass
    return False


def install_glare(sc, spec, log=print):
    """按配方 spec 在 5.x / 4.x 合成器上装一个 Glare。

    spec 键与旧配方一致：``type`` / ``threshold`` / ``size`` / ``mix``。
    返回 True 表示装上了；False 表示已降级（画面仍可正常渲染）。
    """
    nt = None
    try:
        nt = _find_tree(sc)
        if nt is None:
            return _degrade(sc, bpy.data.node_groups.new('Empty', 'CompositorNodeTree'),
                            log, '这个版本没有可用的合成器节点树')
        # 先把旧 Glare 旁路掉：它的上游直接接下游，链路不会断
        src, targets = _bypass_glare(nt)
        comp = _find_output(nt)
        if comp is None:
            return _degrade(sc, nt, log, '合成器没有输出节点（COMPOSITE/GROUP_OUTPUT）')
        if src is None:
            rl = next((n for n in nt.nodes if n.type == 'R_LAYERS'), None)
            if rl is None:
                return _degrade(sc, nt, log, '合成器里没有 Render Layers')
            src = rl.outputs['Image']

        gl = nt.nodes.new('CompositorNodeGlare')
        kind = str(spec.get('type', 'FOG_GLOW')).upper()
        if hasattr(gl, 'glare_type'):           # —— 4.x：属性式 API ——
            gl.glare_type = kind
            gl.quality = 'HIGH'
            gl.threshold = float(spec.get('threshold', 1.0))
            gl.size = int(spec.get('size', 7))
            gl.mix = float(spec.get('mix', -0.35))
        else:                                    # —— 5.x：socket 式 API ——
            _set_socket(gl, 'Type', _MENU_TYPES.get(kind, 'Bloom'))
            _set_socket(gl, 'Quality', 'High')
            _set_socket(gl, 'Threshold', float(spec.get('threshold', 1.0)))
            # 4.x 的 size 是 6~9 档，5.x 是 0~1 的比例
            _set_socket(gl, 'Size', max(0.0, min(1.0, float(spec.get('size', 7)) / 10.0)))
            mix = float(spec.get('mix', -0.35))
            _set_socket(gl, 'Strength', max(0.0, min(1.0, 1.0 + mix)))

        nt.links.new(src, gl.inputs['Image'])
        if targets:
            for t in targets:
                nt.links.new(gl.outputs['Image'], t)
        else:
            nt.links.new(gl.outputs['Image'], comp.inputs['Image'])
        return True
    except Exception as e:
        if nt is not None:
            return _degrade(sc, nt, log, '%s: %s' % (type(e).__name__, e))
        return _degrade(sc, None, log, '%s: %s' % (type(e).__name__, e))
