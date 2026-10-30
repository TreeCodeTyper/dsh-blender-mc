"""封面后期合成：Bloom + 分级 + 暗角。

为什么需要它
-----------
封面链路以前【完全没有后期】：
  * `build_cover.py` 全文 grep `composit|glare` = 0 命中；
  * EEVEE 5.x 又删掉了 `scene.eevee.use_bloom`；
  * 于是渲染直出 = 「均匀、发灰、像游戏截图」。
而动画链路 `fx_kit.py` 里的 Glare 只服务动画，封面从来没接上。

5.2 的 API 与 4.x 完全不同（实测 Blender 5.2.3 LTS）：
  * 合成树挂在 `scene.compositing_node_group`（`scene.node_tree` 已不存在）；
  * 没有 `CompositorNodeComposite`，输出节点是 `NodeGroupOutput`；
  * 没有 `CompositorNodeMixRGB` / `CompositorNodeMath`，
    混合与四则运算要用 `ShaderNodeMix` / `ShaderNodeMath`；
  * `CompositorNodeGlare` 是 socket 化节点：Type/Quality 是菜单 socket，
    Threshold/Strength/Size 都是输入 socket（不是节点属性）。

用法
----
    from post_grade import setup_post_grade
    setup_post_grade(scn)        # 失败会自己降级，不会抛给调用方

环境变量
--------
    DSH_POST=0        整体关掉后期
    DSH_GLARE=0.62    Bloom 阈值（必须低于画面最亮值，否则等于关闭）
    DSH_GRADE_SAT=1.16  饱和度增益
"""

import os

import bpy  # noqa: E402  （只在 Blender 内可用）

POST_ON = os.environ.get("DSH_POST", "1").strip().lower() not in (
    "0", "off", "false", "no", "")
GLARE_THRESHOLD = float(os.environ.get("DSH_GLARE", "0.62"))
GRADE_SAT = float(os.environ.get("DSH_GRADE_SAT", "1.16"))


def _setin(node, name, val):
    """给 socket 赋值；名字不存在/类型不对就静默跳过（版本差异兜底）。"""
    sock = node.inputs.get(name)
    if sock is None:
        return False
    try:
        sock.default_value = val
        return True
    except Exception:
        return False


def _new_mix(nt):
    """5.2 的 RGBA 混合器：ShaderNodeMix + data_type='RGBA'。

    它的同名 socket 有 4 组（Float/Vector/Color/Rotation），
    所以必须按索引取：0=Factor, 6=A(color), 7=B(color)，输出 2=Result(color)。
    """
    mix = nt.nodes.new("ShaderNodeMix")
    mix.data_type = 'RGBA'
    return mix, mix.inputs[0], mix.inputs[6], mix.inputs[7], mix.outputs[2]


def setup_post_grade(scn, log=None, threshold=None, saturation=None):
    """给 scn 接上 Bloom + 饱和度 + 暗角。返回是否成功。"""
    def say(msg):
        if log:
            log(msg)
        else:
            print("[POST] " + msg)

    if not POST_ON:
        say("(DSH_POST=0，无后期)")
        return False

    thr = GLARE_THRESHOLD if threshold is None else threshold
    sat = GRADE_SAT if saturation is None else saturation

    try:
        ng = bpy.data.node_groups.new("CoverGrade", "CompositorNodeTree")
    except Exception as e:
        say(f"⚠️ 建合成树失败({type(e).__name__}: {str(e)[:90]}) → 无后期")
        return False

    try:
        scn.compositing_node_group = ng
    except Exception as e:
        say(f"⚠️ 挂不上 compositing_node_group({type(e).__name__}: {str(e)[:90]}) → 无后期")
        return False

    try:
        ng.interface.new_socket(name="Image", in_out='OUTPUT',
                                socket_type='NodeSocketColor')
    except Exception:
        pass

    ok = []
    rl = ng.nodes.new("CompositorNodeRLayers")
    gout = ng.nodes.new("NodeGroupOutput")
    last_out = rl.outputs['Image']

    # ---- 1. Bloom：把剑刃高光/日轮/轮廓光晕开 ----
    try:
        glare = ng.nodes.new("CompositorNodeGlare")
        _setin(glare, 'Type', 'Bloom')
        _setin(glare, 'Quality', 'Medium')
        _setin(glare, 'Threshold', thr)
        _setin(glare, 'Strength', 0.30)
        _setin(glare, 'Size', 0.55)
        _setin(glare, 'Smoothness', 0.3)
        ng.links.new(last_out, glare.inputs['Image'])
        last_out = glare.outputs['Image']
        ok.append(f"bloom(thr={thr:.2f})")
    except Exception as e:
        say(f"⚠️ Glare 跳过({type(e).__name__}: {str(e)[:70]})")

    # ---- 2. 分级：饱和度 + 一点点提亮（MC 方块色才不发灰） ----
    try:
        hs = ng.nodes.new("CompositorNodeHueSat")
        _setin(hs, 'Saturation', sat)
        _setin(hs, 'Value', 1.02)
        ng.links.new(last_out, hs.inputs['Image'])
        last_out = hs.outputs['Image']
        ok.append(f"sat={sat:.2f}")
    except Exception as e:
        say(f"⚠️ HueSat 跳过({type(e).__name__}: {str(e)[:70]})")

    # ---- 3. 暗角：椭圆遮罩 → (1-mask)*0.34 当 MULTIPLY 的 Factor ----
    try:
        ell = ng.nodes.new("CompositorNodeEllipseMask")
        _setin(ell, 'Mask', 1.0)
        _setin(ell, 'Value', 0.0)
        _setin(ell, 'Position', (0.5, 0.52))
        _setin(ell, 'Size', (0.66, 0.82))

        inv = ng.nodes.new("ShaderNodeMath")
        inv.operation = 'SUBTRACT'
        inv.inputs[0].default_value = 1.0
        ng.links.new(ell.outputs['Mask'], inv.inputs[1])

        amt = ng.nodes.new("ShaderNodeMath")
        amt.operation = 'MULTIPLY'
        amt.inputs[1].default_value = 0.34
        ng.links.new(inv.outputs[0], amt.inputs[0])

        blur = ng.nodes.new("CompositorNodeBlur")
        _setin(blur, 'Size', (180.0, 180.0))
        ng.links.new(amt.outputs[0], blur.inputs['Image'])

        mix, fac, ca, cb, res = _new_mix(ng)
        mix.blend_type = 'MULTIPLY'
        cb.default_value = (0.0, 0.0, 0.0, 1.0)
        ng.links.new(blur.outputs['Image'], fac)
        ng.links.new(last_out, ca)
        last_out = res
        ok.append("vignette")
    except Exception as e:
        say(f"⚠️ 暗角跳过({type(e).__name__}: {str(e)[:70]})")

    try:
        ng.links.new(last_out, gout.inputs['Image'])
    except Exception as e:
        say(f"⚠️ 连到输出失败({type(e).__name__}: {str(e)[:90]}) → 无后期")
        return False

    say("后期: " + (" + ".join(ok) if ok else "无"))
    return bool(ok)
