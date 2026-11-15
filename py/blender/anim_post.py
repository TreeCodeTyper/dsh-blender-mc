# -*- coding: utf-8 -*-
"""动画后处理：把「Blender 合成器」搬去 ffmpeg —— 每帧省 60% 的渲染时间。

## 为什么要这么干

0.6.2 实测（RTX 5060 / Blender 5.3 / EEVEE Next，960x540 / 16 采样）：

    baseline（带合成器）        1.01–1.05 s/帧
    关掉合成器                  0.41 s/帧      ← 合成器占 60%
    其中 Glare(Bloom)           0.37 s/帧
    其中 其余（调色 + 180px 暗角模糊）0.26 s/帧

合成器是**每帧**都要跑一遍的，帧数一多就变成主要成本：240 帧差 2.5 分钟。
而它做的事（溢光、提饱和、暗角）全是纯 2D 后期 —— ffmpeg 一次过一遍比
Blender 每帧做快两个数量级，观感差别肉眼几乎看不出。

## 怎么用

Blender 侧：`anim_kit.setup_render()` 调 `collect()` 把当前合成树的参数
读出来写进 `<out>/_post.json`，然后把 `render.use_compositing` 关掉。
ffmpeg 侧：`pipeline.animate()` 渲完帧后调 `ffmpeg_args()` 拼命令出 MP4。

要恢复「Blender 内合成」：环境变量 `DSH_ANIM_COMPOSITE=1`。
"""
import glob
import json
import math
import os
import time

POST_FILE = '_post.json'

# 合成树读不到时的兜底参数 —— 与 py/blender/post_grade.py 的默认值一致
DEFAULTS = {
    'glare': {'on': True, 'threshold': 0.62, 'strength': 0.30, 'size': 0.55},
    'grade': {'saturation': 1.16, 'value': 1.02},
    'vignette': {'on': True, 'amount': 0.34},
    'res': [960, 540],
}


# ═══════════════════════════════════════════════════════════════
# Blender 侧：读合成树
# ═══════════════════════════════════════════════════════════════
def _sock(node, *names):
    """从 socket 化的节点里按名字取默认值（Blender 5.x 合成器）。"""
    for n in names:
        try:
            for s in node.inputs:
                if s.name == n or getattr(s, 'identifier', '') == n:
                    v = getattr(s, 'default_value', None)
                    if v is not None:
                        return v
        except Exception:
            continue
    return None


def _sock_const(node):
    """返回未连线的那一路浮点输入值。

    MATH 节点的前一路通常被遮罩连接（Value 保留着默认 0.5），
    真正的常量在另一路（如 0.34）；直接按名字取 Value 会读错。
    """
    try:
        for s in node.inputs:
            if getattr(s, 'links', None):
                continue
            v = getattr(s, 'default_value', None)
            if isinstance(v, float):
                return v
    except Exception:
        pass
    return None


def _tree(sc):
    """Blender 5.x: scene.compositing_node_group；4.x: scene.node_tree。"""
    for attr in ('compositing_node_group', 'node_tree'):
        try:
            t = getattr(sc, attr, None)
            if t is not None:
                return t
        except Exception:
            continue
    return None


def collect(sc, log=print):
    """把合成器里「溢光 / 调色 / 暗角」的实际参数读出来。"""
    post = json.loads(json.dumps(DEFAULTS))  # 深拷贝
    try:
        post['res'] = [int(sc.render.resolution_x), int(sc.render.resolution_y)]
    except Exception:
        pass
    tree = _tree(sc)
    if tree is None:
        log('    !! 找不到合成树 → 后处理用默认参数')
        return post
    nodes = list(getattr(tree, 'nodes', []) or [])

    # —— Glare（溢光 / Bloom）———————————————————————————
    glare = [n for n in nodes if n.type == 'GLARE']
    if glare:
        g = glare[0]
        typ = _sock(g, 'Type')
        thr = _sock(g, 'Threshold')
        strength = _sock(g, 'Strength')
        size = _sock(g, 'Size')
        if typ is not None:
            typ = str(getattr(typ, 'name', None) or typ)
        post['glare'] = {
            'on': True,
            'type': typ or 'Bloom',
            'threshold': float(thr) if thr is not None else DEFAULTS['glare']['threshold'],
            'strength': float(strength) if strength is not None else DEFAULTS['glare']['strength'],
            'size': float(size) if size is not None else DEFAULTS['glare']['size'],
        }
    else:
        post['glare'] = {'on': False}

    # —— Hue/Saturation ———————————————————————————————
    hs = [n for n in nodes if n.type in ('HUE_SAT', 'HUE_SATURATION_VAL')]
    if hs:
        sat = _sock(hs[0], 'Saturation')
        val = _sock(hs[0], 'Value')
        post['grade'] = {
            'saturation': float(sat) if sat is not None else 1.0,
            'value': float(val) if val is not None else 1.0,
        }

    # —— 暗角（EllipseMask → Math → Blur → Mix）———————
    mix_vig = [n for n in nodes if n.type in ('MIX_RGB', 'MIX')]
    if mix_vig:
        # Mix 的 factor 恒为 1.0，强度在 MATH(MULTIPLY ×0.34) 里。
        # ★ 只认「MULTIPLY → BLUR(径≥64px)」那一条（晶圈遮罩的结构），
        #   场景里灰度/调色也有 MATH MULTIPLY，盲猜会读成 0.5。
        amount = None
        for n in nodes:
            if n.type != 'MATH':
                continue
            try:
                if str(getattr(n, 'operation', '')).upper() != 'MULTIPLY':
                    continue
            except Exception:
                continue
            nxt = None
            try:
                for out in n.outputs:
                    for lk in out.links:
                        nxt = lk.to_node
                        break
                    if nxt is not None:
                        break
            except Exception:
                nxt = None
            if nxt is None or nxt.type != 'BLUR':
                continue
            try:
                if int(_sock(nxt, 'Size', 'Size_X') or 0) < 64:
                    continue
            except Exception:
                pass
            v = _sock_const(n)
            if v is not None and 0.0 < float(v) < 1.0:
                amount = float(v)
                break
        env = os.environ.get('DSH_POST_VIGNETTE')
        if env:
            try:
                amount = float(env)
            except ValueError:
                pass
        post['vignette'] = {'on': True,
                            'amount': amount if amount is not None else 0.34}
    return post


def describe(post):
    g = post.get('glare') or {}
    gr = post.get('grade') or {}
    bits = []
    if g.get('on'):
        bits.append('溢光 %s 阈值 %.2f ×%.2f' % (g.get('type', 'Bloom'),
                                              g.get('threshold', 0.62), g.get('strength', 0.3)))
    if gr.get('saturation', 1.0) != 1.0:
        bits.append('饱和 %.2f' % gr.get('saturation', 1.0))
    if (post.get('vignette') or {}).get('on'):
        bits.append('暗角 %.2f' % (post.get('vignette') or {}).get('amount', 0.34))
    return '、'.join(bits) or '无'


# ═══════════════════════════════════════════════════════════════
# 读写（Blender 侧写，pipeline 侧读）
# ═══════════════════════════════════════════════════════════════
def write(out_dir, post):
    path = os.path.join(out_dir, POST_FILE)
    txt = json.dumps(post, ensure_ascii=False, indent=2)
    with open(path, 'w', encoding='utf-8') as f:
        f.write(txt)
    return path


def load(out_dir):
    path = os.path.join(out_dir, POST_FILE)
    if not os.path.exists(path):
        return dict(DEFAULTS)
    try:
        with open(path, encoding='utf-8') as f:
            post = json.load(f)
        out = json.loads(json.dumps(DEFAULTS))
        for k, v in (post or {}).items():
            if isinstance(v, dict) and isinstance(out.get(k), dict):
                out[k].update(v)
            else:
                out[k] = v
        return out
    except Exception:
        return dict(DEFAULTS)


# ═══════════════════════════════════════════════════════════════
# ffmpeg 侧：拼滤镜
# ═══════════════════════════════════════════════════════════════
def _env_f(*names, default=None):
    for n in names:
        v = os.environ.get(n)
        if v not in (None, ''):
            try:
                return float(v)
            except ValueError:
                continue
    return default


def filter_chain(post, w=None, h=None):
    """把 _post.json 翻成一条 ffmpeg -filter_complex 链（不带输入输出）。

    顺序与 Blender 合成器一致：溢光 → 调色 → 暗角。
    逐项都是拿同一帧和合成器对过平均色 / 角落亮度定下来的（见模块文档）。
    """
    g = post.get('glare') or {}
    gr = post.get('grade') or {}
    vg = post.get('vignette') or {}
    w = int(w or (post.get('res') or [960, 540])[0])
    h = int(h or (post.get('res') or [960, 540])[1])

    parts = []
    src = '[0:v]'
    if g.get('on') and float(g.get('strength', 0)) > 0:
        thr = _env_f('DSH_POST_THRESHOLD', default=float(g.get('threshold', 0.62)))
        strength = _env_f('DSH_POST_STRENGTH', default=float(g.get('strength', 0.30)))
        # Glare 的 Size 是「相对画面」的模糊半径：0.55 在 720p 上约 22px
        sigma = _env_f('DSH_POST_SIGMA', default=float(g.get('size', 0.55)) * h * 0.055)
        sigma = max(2.0, min(90.0, sigma))
        # 阈值以上的亮部才参与溢光；再按 strength 叠加（addition，不是 screen）
        parts.append(
            '%ssplit=2[base][hl];'
            '[hl]format=gbrp,colorlevels=rimin=%.3f:gimin=%.3f:bimin=%.3f,'
            'gblur=sigma=%.2f[bloom];'
            '[base][bloom]blend=all_mode=addition:all_opacity=%.3f[lit]'
            % (src, thr, thr, thr, sigma, min(1.0, strength)))
        src = '[lit]'

    chain = []
    sat = _env_f('DSH_POST_SAT', default=float(gr.get('saturation', 1.0)))
    val = float(gr.get('value', 1.0) or 1.0)
    if abs(sat - 1.0) > 0.001 or abs(val - 1.0) > 0.001:
        chain.append('eq=saturation=%.3f:brightness=%.4f' % (sat, (val - 1.0) * 0.5))
    if vg.get('on'):
        amount = _env_f('DSH_POST_VIGNETTE', default=float(vg.get('amount', 0.34)))
        # 角度是反直觉的：越小越弱。实测我们的合成器 = 0.200（角落暗 7%）
        angle = _env_f('DSH_POST_VIGNETTE_ANGLE',
                       default=max(0.05, min(0.9, amount * 0.59)))
        chain.append('vignette=angle=%.3f:mode=forward' % angle)
    if chain:
        parts.append('%s%s[out]' % (src, ','.join(chain)))
    else:
        parts.append('%scopy[out]' % src)
    return ';'.join(parts)


def ffmpeg_args(ffmpeg, frames_dir, fps, out_mp4, post=None, total=None, crf=18):
    """拼出「帧序列 → MP4」的 ffmpeg 命令。

    帧文件是 frame_0001.png / frame_0002.png …（1 起始）。
    total 给定时会带上 -frames:v，避免目录里多出来的帧也编进去。
    """
    post = post or dict(DEFAULTS)
    pat = os.path.join(frames_dir, 'frame_%04d.png')
    w = (post.get('res') or [960, 540])[0]
    h = (post.get('res') or [960, 540])[1]
    args = [ffmpeg, '-y', '-loglevel', 'error', '-stats',
            '-framerate', str(int(fps or 24)), '-start_number', '1',
            '-i', pat,
            '-filter_complex', filter_chain(post, w, h),
            '-map', '[out]',
            '-c:v', 'libx264', '-preset', 'medium', '-crf', str(crf),
            '-pix_fmt', 'yuv420p', '-movflags', '+faststart']
    if total:
        args += ['-frames:v', str(int(total))]
    args.append(out_mp4)
    return args


def count_frames(frames_dir, prefix='frame_'):
    return len(glob.glob(os.path.join(frames_dir, prefix + '*.png')))


# ═══════════════════════════════════════════════════════════════
# Blender 侧：一步到位（读参数 → 关合成器 → 同进程渲完整段）
# 老脚本 animate_aura.py / animate_killaura.py 直接调这两个。
# ═══════════════════════════════════════════════════════════════
def setup_ffmpeg_post(sc, out_dir, log=print):
    """把合成器参数导出到 _post.json，并关掉 Blender 内的合成。"""
    try:
        post = collect(sc, log=log)
        write(out_dir, post)
        if os.environ.get('DSH_ANIM_COMPOSITE', '0') == '1':
            log('    合成器: 保留（DSH_ANIM_COMPOSITE=1，每帧多约 0.6s）')
            return post
        if getattr(sc.render, 'use_compositing', False):
            sc.render.use_compositing = False
        log('    合成器: 移到 ffmpeg 后处理（%s）→ 每帧约省 60%%' % describe(post))
        return post
    except Exception as e:
        log('    !! 后处理参数导出失败（合成器保持原样）: %s' % e)
        return None


def render_all(sc, out_dir, total, every=24, log=print):
    """在【同一个 Blender 进程】里把整段帧渲完（省掉第二次冷启 12–16s）。"""
    import bpy
    keep = sc.render.filepath
    total = int(total)
    sc.frame_start, sc.frame_end = 1, total
    t0 = time.time()
    t_first = 0.0
    for f in range(1, total + 1):
        sc.frame_set(f)
        sc.render.filepath = os.path.join(out_dir, 'frame_%04d' % f)
        bpy.ops.render.render(write_still=True)
        if f == 1:
            t_first = time.time() - t0
        if f % max(1, int(every)) == 0 or f == total:
            el = time.time() - t0
            log('    帧 %d/%d  %.1fs（首帧 %.1fs，之后 %.2fs/帧）'
                % (f, total, el, t_first, (el - t_first) / max(1, f - 1)))
    sc.render.filepath = keep
    el = time.time() - t0
    log('    帧序列完成: %d 帧 / %.1fs（%.2f 帧/秒）' % (total, el, total / max(0.01, el)))
    log('INPROC_DONE frames=%d seconds=%.1f' % (total, el))
    return el
