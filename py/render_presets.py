# -*- coding: utf-8 -*-
"""render_presets —— 渲染预设（缩略图 / 视频直出 / Cycles 高品质 / 草稿）

只依赖标准库 + ``bpy``，可以这样直接用::

    import sys
    sys.path.insert(0, r'...\\dsh-blender-mc\\py')
    import render_presets as rp

    print(rp.apply_preset(bpy.context.scene, 'thumbnail'))
    print(rp.apply_preset(bpy.context.scene, 'bilibili', {'samples': 64}))

设计原则
--------
**每一条配置都容错**：属性在本版本不存在 / 枚举名对不上，就打印一行
``[render] 跳过 xxx（这个版本没有）`` 然后继续，绝不抛异常中断渲染。
唯一会抛异常的是「预设名写错」和「scene 传 None」——那是调用方的 bug，
静默吞掉只会让人对着没生效的画面发呆。

``apply_preset`` 返回的是**实际生效的参数**（写完再读回来），所以可以直接
拿返回值做日志、写进报告、或者断言。

版本坑（Blender 5.2.3 LTS 实测）
--------------------------------
* ``bpy.types.RenderSettings.bl_rna.properties['engine'].enum_items`` 只列
  **内核自带**引擎，实测只回 ``['BLENDER_EEVEE']`` —— Cycles 是 addon 注册的，
  静态枚举里看不到，但真的能赋值。所以 :func:`probe_engines` 会故意赋一个
  非法值，从报错 ``enum "x" not found in ('BLENDER_EEVEE', 'BLENDER_WORKBENCH',
  'CYCLES')`` 里把真实列表套出来。**不要硬编码引擎名**。
* 4.2~4.5 的 EEVEE 标识符是 ``BLENDER_EEVEE_NEXT``，5.2 又改回 ``BLENDER_EEVEE``。
* EEVEE Next（4.2+）**没有** ``use_bloom`` / ``use_gtao`` / ``use_vector_blur``，
  运动模糊在 ``scene.render.use_motion_blur`` + ``scene.eevee.motion_blur_steps``。
* ``view_settings.look`` 的「无」是 ``'None'``，不是 ``'NONE'``。
"""

import math
import re

import bpy

__all__ = ['PRESETS', 'ALIASES', 'apply_preset', 'probe_engines',
           'three_point_lights', 'KNOWN_KEYS']

_MISSING = object()

# ═══════════════════════════════════════════════════════════════
# 0. 日志 / 容错写属性
# ═══════════════════════════════════════════════════════════════
_SKIPPED = []


def _log(msg):
    print('[render] %s' % msg)


def _skip(what, why='这个版本没有'):
    """记录并打印一条「跳过」，never raise。"""
    _SKIPPED.append(str(what))
    print('[render] 跳过 %s（%s）' % (what, why))


def _read(obj, path, default=None):
    """按点号路径读属性，任何一环缺失都返回 default。"""
    cur = obj
    for part in str(path).split('.'):
        cur = getattr(cur, part, _MISSING)
        if cur is _MISSING:
            return default
    return cur


def _write(obj, path, value, label=None):
    """按点号路径写属性；属性不存在或类型/枚举不接受就跳过。返回 bool。"""
    parts = str(path).split('.')
    target = obj
    for part in parts[:-1]:
        target = getattr(target, part, None)
        if target is None:
            break
    leaf = parts[-1]
    name = label or path
    if target is None or not hasattr(target, leaf):
        _skip(name)
        return False
    try:
        setattr(target, leaf, value)
    except Exception as exc:                      # 枚举不认 / 只读 / 越界
        _skip(name, str(exc).splitlines()[0][:70])
        return False
    return True


def _write_read(obj, path, value, default=None):
    """写 + 读回实际值，一次搞定。"""
    if not _write(obj, path, value):
        return default
    got = _read(obj, path, default)
    return default if got is _MISSING else got


# ═══════════════════════════════════════════════════════════════
# 1. 引擎探测
# ═══════════════════════════════════════════════════════════════
_ENGINE_CACHE = None
_PROBE_SENTINEL = '__dsh_probe_engine__'
_ENGINE_FALLBACK = ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE', 'CYCLES', 'BLENDER_WORKBENCH')


def _parse_enum_error(msg):
    """从 ``enum "x" not found in ('A', 'B')`` 里抠出合法枚举列表。"""
    m = re.search(r'not found in \(([^)]*)\)', str(msg or ''))
    if not m:
        return []
    return [t.strip().strip('\'"') for t in m.group(1).split(',') if t.strip()]


def probe_engines(scene=None):
    """探测本版本真正接受的渲染引擎标识符，返回 ``(静态枚举, 真实可用)``。

    ⚠️ 只用 ``bl_rna...enum_items`` 会漏掉 Cycles（它是 addon 注册的），
    所以这里再故意赋一个非法值，从报错信息里把真实列表套出来。
    """
    global _ENGINE_CACHE
    if _ENGINE_CACHE is not None:
        return _ENGINE_CACHE

    static = []
    try:
        prop = bpy.types.RenderSettings.bl_rna.properties['engine']
        static = [k for k in prop.enum_items.keys()]
    except Exception:
        pass

    real = []
    if scene is not None:
        cur = _read(scene, 'render.engine')
        try:
            scene.render.engine = _PROBE_SENTINEL       # 一定失败，只为套列表
        except Exception as exc:
            real = _parse_enum_error(exc)
        if cur:
            try:
                scene.render.engine = cur
            except Exception:
                pass

    if not real:                                        # 兜底：静态 + 已知别名
        real = list(static)
        for cand in _ENGINE_FALLBACK:
            if cand not in real:
                real.append(cand)

    _ENGINE_CACHE = (static, real)
    return _ENGINE_CACHE


def _pick_engine_id(real, want):
    """在真实枚举列表里挑一个匹配 want 的标识符。"""
    want = str(want or '').upper()
    if want.startswith('CYCLE'):
        prefer = ('CYCLES',)
        fuzzy = 'CYCLES'
    elif want.startswith('WORK'):
        prefer = ('BLENDER_WORKBENCH',)
        fuzzy = 'WORKBENCH'
    else:
        # 4.2~4.5 = BLENDER_EEVEE_NEXT，5.2 = BLENDER_EEVEE，两个都试
        prefer = ('BLENDER_EEVEE_NEXT', 'BLENDER_EEVEE')
        fuzzy = 'EEVEE'
    for cand in prefer:
        if cand in real:
            return cand
    for cand in real:
        if fuzzy in str(cand).upper():
            return cand
    return None


def _engine_family(engine_id):
    e = str(engine_id or '').upper()
    if 'EEVEE' in e:
        return 'EEVEE'
    if 'CYCLES' in e:
        return 'CYCLES'
    if 'WORKBENCH' in e:
        return 'WORKBENCH'
    return e or None


def _set_engine(scene, want):
    """切引擎；切不动就保持原样并提示。返回实际生效的引擎标识符。"""
    if not want:
        return _read(scene, 'render.engine')
    _static, real = probe_engines(scene)
    target = _pick_engine_id(real, want)
    if target is None:
        _skip('engine=%s' % want, '本版本引擎列表只有 %s' % (list(real),))
        return _read(scene, 'render.engine')
    try:
        scene.render.engine = target
        return _read(scene, 'render.engine', target)
    except Exception as exc:
        # 枚举又变了：从这次报错里再学一遍
        for cand in _parse_enum_error(exc):
            if _pick_engine_id([cand], want) == cand:
                try:
                    scene.render.engine = cand
                    return _read(scene, 'render.engine', cand)
                except Exception:
                    continue
        _skip('engine=%s' % want, str(exc).splitlines()[0][:70])
        return _read(scene, 'render.engine')


# ═══════════════════════════════════════════════════════════════
# 2. 单项设置（每个都能单独调用）
# ═══════════════════════════════════════════════════════════════
VT_CANDIDATES = ('AgX', 'Filmic', 'Standard', 'Filmic Log', 'Raw')
VT_LOOKS = {'AgX': 'AgX - Punchy', 'Filmic': 'Filmic', 'Standard': 'None'}


def _set_res(scene, res):
    """'1280x720' / '1280×720' → resolution_x/y，返回实际生效的字串。"""
    if not res:
        return None
    txt = str(res).lower().replace('×', 'x').replace(' ', '')
    try:
        w, h = [int(float(v)) for v in txt.split('x')[:2]]
    except Exception:
        _skip('分辨率 %r' % (res,), '格式不对，要 1280x720')
        return None
    _write(scene, 'render.resolution_x', w, 'render.resolution_x')
    _write(scene, 'render.resolution_y', h, 'render.resolution_y')
    _write(scene, 'render.resolution_percentage', 100, 'render.resolution_percentage')
    return '%dx%d' % (_read(scene, 'render.resolution_x', w),
                      _read(scene, 'render.resolution_y', h))


def _set_samples(scene, samples, family):
    """采样数落到哪个属性上看引擎：EEVEE→taa_render_samples，Cycles→samples。"""
    try:
        n = int(round(float(samples)))
    except Exception:
        _skip('samples=%r' % (samples,), '不是数字')
        return None
    if family == 'CYCLES':
        _write(scene, 'cycles.samples', n, 'cycles.samples')
        got = _read(scene, 'cycles.samples', n)
    elif family == 'WORKBENCH':
        _skip('samples（Workbench 没有采样数）')
        return None
    else:
        _write(scene, 'eevee.taa_render_samples', n, 'eevee.taa_render_samples')
        # 视口预览采样别比渲染采样还低，否则调光时看到的和出图不一样
        view = _read(scene, 'eevee.taa_samples', _MISSING)
        if view is not _MISSING and int(view) > n:
            _write(scene, 'eevee.taa_samples', n, 'eevee.taa_samples')
        got = _read(scene, 'eevee.taa_render_samples', n)
    try:
        return int(got)
    except Exception:
        return got


def _set_view(scene, vt=None, look=None):
    """色彩管理：view_transform 逐候选降级，look 对不上就退回 'None'。"""
    got_vt = _read(scene, 'view_settings.view_transform')
    if vt:
        for cand in (vt,) + tuple(v for v in VT_CANDIDATES if v != vt):
            try:
                scene.view_settings.view_transform = cand
                got_vt = _read(scene, 'view_settings.view_transform', cand)
                break
            except Exception:
                continue
        else:
            _skip('view_transform=%r' % (vt,), '内置转换名不同')
    if look:
        if isinstance(look, str) and look.lower() in ('auto', 'default'):
            look = VT_LOOKS.get(got_vt, 'None')
        ok = False
        for cand in (look, 'None', 'NONE'):          # 5.2 的「无」是 'None'
            try:
                scene.view_settings.look = cand
                ok = True
                break
            except Exception:
                continue
        if not ok:
            _skip('look=%r' % (look,), '这个版本没有这个 look')
    return got_vt, _read(scene, 'view_settings.look')


def _set_motion_blur(scene, shutter):
    """shutter>0 开运动模糊并设快门角；0 = 关。返回实际快门值（关=0.0）。"""
    on = float(shutter or 0) > 0.0
    _write(scene, 'render.use_motion_blur', on, 'render.use_motion_blur')
    if on:
        _write(scene, 'render.motion_blur_shutter', float(shutter),
               'render.motion_blur_shutter')
        _write(scene, 'render.motion_blur_position', 'CENTER',
               'render.motion_blur_position')
        _write(scene, 'eevee.motion_blur_steps', 2, 'eevee.motion_blur_steps')
    if not _read(scene, 'render.use_motion_blur', False):
        return 0.0
    try:
        return round(float(_read(scene, 'render.motion_blur_shutter', 0.0)), 4)
    except Exception:
        return 0.0


def _set_vector_blur(scene, on):
    """EEVEE 矢量模糊。真正有这个属性的版本直接写；5.x 没有就说明清楚。"""
    for path in ('eevee.use_vector_blur', 'render.use_vector_blur'):
        if _read(scene, path, _MISSING) is not _MISSING:
            if _write(scene, path, bool(on), path):
                return bool(on)
            return None
    if on:
        # 5.x 的 EEVEE Next 没有独立开关，矢量模糊就是运动模糊
        if _write(scene, 'render.use_motion_blur', True, 'render.use_motion_blur'):
            _log('本版本无独立矢量模糊开关，已用 render.use_motion_blur 顶替')
            return True
        return None
    _skip('eevee.use_vector_blur', '5.x 的矢量模糊＝render.use_motion_blur，由 motion_blur 控')
    return False


def _set_simplify(scene, on, threshold=None):
    """简化 + 渲染细分上限。"""
    got = _write(scene, 'render.use_simplify', bool(on), 'render.use_simplify')
    if threshold is not None and got:
        _write(scene, 'render.simplify_subdivision_render', int(threshold),
               'render.simplify_subdivision_render')
    return bool(_read(scene, 'render.use_simplify', bool(on)))


def _set_gpu(scene, on):
    """Cycles 设备：GPU 需先把偏好里的计算后端切成 OPTIX/CUDA/…，再逐个开设备。

    ⚠️ 这里改的是**用户偏好**（不只这个 .blend），所以预设默认不碰；
       只有显式传 gpu=True/False 才动。
    """
    if _read(scene, 'cycles', None) is None:
        _skip('cycles.device', '这个版本没装 Cycles')
        return None
    if not on:
        _write(scene, 'cycles.device', 'CPU', 'cycles.device')
        return _read(scene, 'cycles.device', 'CPU')

    prefs = None
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
    except Exception as exc:
        _skip('cycles 偏好设置', '拿不到 addons["cycles"].preferences: %s'
              % str(exc).splitlines()[0][:50])

    if prefs is not None:
        # 有 N 卡且装了 OptiX 优先 OptiX，其次 CUDA，再往后是 A 卡 / I 卡
        has_optix = False
        try:
            has_optix = any(str(getattr(d, 'type', '')).upper() == 'OPTIX'
                            for d in prefs.devices)
        except Exception:
            pass
        order = (('OPTIX', 'CUDA', 'HIP', 'ONEAPI', 'METAL') if has_optix
                 else ('CUDA', 'OPTIX', 'HIP', 'ONEAPI', 'METAL'))
        chosen = None
        for backend in order:
            try:
                prefs.compute_device_type = backend
                chosen = backend
                break
            except Exception:
                continue
        try:
            prefs.get_devices()
        except Exception:
            pass
        if chosen:
            gpus = 0
            for dev in prefs.devices:
                kind = str(getattr(dev, 'type', '')).upper()
                use = kind != 'CPU'          # 纯 GPU 渲染：CPU 设备不开
                try:
                    dev.use = use
                except Exception:
                    pass
                gpus += 1 if use else 0
            if gpus:
                _log('Cycles 后端 %s，GPU 设备 %d 个' % (chosen, gpus))
            else:
                _skip('cycles GPU 设备', '没找到可用显卡，落回 CPU')
    _write(scene, 'cycles.device', 'GPU', 'cycles.device')
    if _read(scene, 'cycles.device') != 'GPU':
        _write(scene, 'cycles.device', 'CPU', 'cycles.device')
    return _read(scene, 'cycles.device')


def _set_lens(scene, lens):
    """镜头焦距（mm）。场景没摄影机就跳过。"""
    cam = _read(scene, 'camera')
    if cam is None or _read(cam, 'data') is None:
        _skip('lens=%.1fmm' % float(lens), '场景里没有摄影机')
        return None
    ok = _write(cam.data, 'lens', float(lens), '%s.data.lens' % cam.name)
    return round(float(_read(cam.data, 'lens', 0.0)), 2) if ok else None


def _set_dof(scene, on):
    """景深开关。"""
    cam = _read(scene, 'camera')
    if cam is None or _read(cam, 'data') is None:
        _skip('dof=%r' % (on,), '场景里没有摄影机')
        return None
    _write(cam.data, 'dof.use_dof', bool(on), 'camera.data.dof.use_dof')
    return bool(_read(cam.data, 'dof.use_dof', bool(on)))


def _set_film_transparent(scene, on):
    got = _write(scene, 'render.film_transparent', bool(on), 'render.film_transparent')
    if got and on:
        # 透明背景必须 RGBA，否则 PNG 存成 RGB 又变回黑底
        _write(scene, 'render.image_settings.color_mode', 'RGBA',
               'render.image_settings.color_mode')
    return bool(_read(scene, 'render.film_transparent', bool(on)))


# ═══════════════════════════════════════════════════════════════
# 3. 三点光
# ═══════════════════════════════════════════════════════════════
_ROLE_WORDS = {'key': ('key',), 'fill': ('fill',),
               'rim': ('rim', 'back', '轮廓', '背光')}


def _light_role(name):
    """从灯名里拆出 key / fill / rim 角色。

    按 ``[._\\- 空格]`` 切词再整词/前缀匹配，所以 ``SunKey`` 拆成 ``['sunkey']``
    不会被误判成 key 灯；``Key.001`` 拆成 ``['key','001']`` 能正确命中。
    """
    tokens = [t for t in re.split(r'[._\-\s]+', str(name).strip().lower()) if t]
    for role, words in _ROLE_WORDS.items():
        for tok in tokens:
            for w in words:
                if tok == w or tok.startswith(w):
                    return role
    return None


def three_point_lights(scene, cfg=None):
    """按灯名匹配 key/fill/rim 并改能量；rim 默认转到正后方（Rx −90°）。

    ``cfg``::

        {'key': 100.0, 'fill': 60.0, 'rim': None, 'rim_rx': -90.0,
         'size': {'key': 4.0}, 'color': {'fill': [0.88, 0.93, 1.0]}}

    只认名字里能拆出角色的灯；一个都没匹配上也不会抛异常。
    """
    cfg = dict(cfg or {})
    out = {'key': None, 'fill': None, 'rim': None, 'rim_rx': None, 'found': []}
    rx = float(cfg.get('rim_rx', -90.0))

    for ob in list(bpy.data.objects):
        if getattr(ob, 'type', None) != 'LIGHT':
            continue
        role = _light_role(ob.name)
        if role is None:
            continue
        out['found'].append(ob.name)

        energy = cfg.get(role)
        if energy is not None:
            if _write(ob.data, 'energy', float(energy), '%s.data.energy' % ob.name):
                out[role] = round(float(energy), 2)

        size = (cfg.get('size') or {}).get(role)
        if size is not None:
            path = ('data.size' if hasattr(ob.data, 'size')
                    else 'data.shadow_soft_size')
            _write(ob, path, float(size), '%s.%s' % (ob.name, path))

        col = (cfg.get('color') or {}).get(role)
        if col:
            try:
                _write(ob.data, 'color', tuple(col), '%s.data.color' % ob.name)
            except Exception:
                pass

        if role == 'rim' and cfg.get('rim_rx') is not None:
            # 默认灯是朝 −Z 打的；Rx −90° 把光束转到 −Y（正后方往相机方向补轮廓）
            if _write(ob, 'rotation_euler', (math.radians(rx), 0.0, 0.0),
                      '%s.rotation_euler(rx=%g°)' % (ob.name, rx)):
                out['rim_rx'] = rx

    for role in ('key', 'fill', 'rim'):
        if cfg.get(role) is not None and out[role] is None:
            _skip('%s 灯' % role, '工程里没有名字含 %s 的灯' % role)
    if out['found']:
        _log('三点光：%s（key=%s / fill=%s / rim=%s）'
             % ('+'.join(out['found']), out['key'], out['fill'], out['rim']))
    return out


# ═══════════════════════════════════════════════════════════════
# 4. 预设内容
# ═══════════════════════════════════════════════════════════════
# 键名是「规范键」，apply_preset 的 spec 覆盖用的也是同一套键：
#   engine / res / samples / exposure / motion_blur / vector_blur /
#   film_transparent / view_transform / look / lens / dof / gpu /
#   simplify / simplify_threshold / noise_threshold / denoise / adaptive / lights
PRESETS = {
    # 缩略图：一次一张，要快、要干净，直接出 PNG 带 alpha 贴封面
    'thumbnail': {
        'engine': 'EEVEE',
        'res': '1280x720',
        'samples': 64,
        'exposure': 0.0,
        'view_transform': 'AgX',
        'look': 'AgX - Punchy',
        'motion_blur': 0.0,          # 静帧不要动态模糊
        'vector_blur': False,
        'film_transparent': True,    # 透明背景，方便贴到封面/缩略图版式里
        'lens': 80.0,                # 长焦压缩透视，人物比例更像海报
        'lights': {'key': 100.0, 'fill': 60.0, 'rim_rx': -90.0},
    },

    # 视频直出：B 站投稿规格，和 presets/walk.json、ride.json 的 render 段对齐
    'bilibili': {
        'engine': 'EEVEE',
        'res': '1920x1080',
        'samples': 32,
        'exposure': 0.0,
        'motion_blur': 0.35,
        'film_transparent': False,
        'view_transform': 'AgX',
        'look': 'AgX - Punchy',
    },

    # Cycles 高品质静帧：128 采样 + 降噪 + 自适应，噪点阈值 0.01
    'cycles_hq': {
        'engine': 'CYCLES',
        'res': '1920x1080',
        'samples': 128,
        'denoise': True,
        'adaptive': True,
        'noise_threshold': 0.01,
        'exposure': 0.0,
        'motion_blur': 0.0,
        'film_transparent': False,
        'view_transform': 'AgX',
        'look': 'AgX - Punchy',
        # gpu 不设：切 GPU 改的是用户偏好，预设不该擅自改，要就显式传 gpu=True
    },

    # 草稿：调机位/试动作时用，8 采样、半分辨率、关动态模糊、关景深
    'quick': {
        'engine': 'EEVEE',
        'res': '960x540',
        'samples': 8,
        'motion_blur': 0.0,
        'vector_blur': False,
        'dof': False,
        'exposure': 0.0,
        'view_transform': 'AgX',     # 和成片同一条色彩管线，草稿才对得上色
        'film_transparent': False,
    },
}

ALIASES = {
    'thumb': 'thumbnail', 'thumbnail': 'thumbnail', '缩略图': 'thumbnail',
    'bili': 'bilibili', 'b站': 'bilibili', 'video': 'bilibili',
    'cycles': 'cycles_hq', 'hq': 'cycles_hq', 'cycles-hq': 'cycles_hq',
    'draft': 'quick', 'fast': 'quick', 'preview': 'quick', '草稿': 'quick',
}

KNOWN_KEYS = ('engine', 'res', 'samples', 'exposure', 'motion_blur',
              'vector_blur', 'film_transparent', 'view_transform', 'look',
              'lens', 'dof', 'gpu', 'simplify', 'simplify_threshold',
              'noise_threshold', 'denoise', 'adaptive', 'lights')


def _canon(name):
    key = str(name or '').strip().lower()
    return ALIASES.get(key, key if key in PRESETS else None)


# ═══════════════════════════════════════════════════════════════
# 5. 应用
# ═══════════════════════════════════════════════════════════════
def apply_preset(scene, name, spec=None):
    """把预设应用到 scene，返回**实际生效**的参数 dict。

    :param scene: ``bpy.context.scene``
    :param name:  预设名或别名（thumbnail / bilibili / cycles_hq / quick）
    :param spec:  覆盖项，None 值表示「这一项不动」
    :return:      生效参数 dict（含 ``skipped``：本次跳过的配置项清单）
    """
    if scene is None:
        raise ValueError('apply_preset(scene=None)：需要传 bpy.context.scene')
    key = _canon(name)
    if key is None:
        raise ValueError('未知渲染预设 %r；可选：%s'
                         % (name, ' / '.join(sorted(PRESETS))))

    merged = dict(PRESETS[key])
    if spec:
        if not isinstance(spec, dict):
            raise TypeError('spec 要是 dict，收到 %r' % type(spec).__name__)
        for k, v in spec.items():
            if k not in KNOWN_KEYS:
                _log('未知配置项 %r（已忽略）' % (k,))
                continue
            if v is None:                        # None = 这一项不动
                merged.pop(k, None)
            else:
                merged[k] = v

    _SKIPPED[:] = []
    eff = {'preset': key}

    # ── 1) 引擎（必须先定：采样数落在哪个属性上取决于它）
    if merged.get('engine'):
        eng = _set_engine(scene, merged['engine'])
    else:
        eng = _read(scene, 'render.engine')
    family = _engine_family(eng)
    eff['engine'] = eng
    eff['engine_family'] = family

    # ── 2) 分辨率
    if merged.get('res'):
        eff['res'] = _set_res(scene, merged['res'])
    else:
        eff['res'] = '%dx%d' % (_read(scene, 'render.resolution_x', 0),
                                _read(scene, 'render.resolution_y', 0))
    eff['resolution'] = [_read(scene, 'render.resolution_x'),
                         _read(scene, 'render.resolution_y')]

    # ── 3) 采样
    if merged.get('samples') is not None:
        eff['samples'] = _set_samples(scene, merged['samples'], family)
    else:
        eff['samples'] = (_read(scene, 'cycles.samples', None) if family == 'CYCLES'
                          else _read(scene, 'eevee.taa_render_samples', None))

    # ── 4) 曝光
    if merged.get('exposure') is not None:
        _write(scene, 'view_settings.exposure', float(merged['exposure']),
               'view_settings.exposure')
    eff['exposure'] = _read(scene, 'view_settings.exposure')

    # ── 5) 色彩管理
    eff['view_transform'], eff['look'] = _set_view(
        scene, merged.get('view_transform'), merged.get('look'))

    # ── 6) 运动模糊
    if merged.get('motion_blur') is not None:
        eff['motion_blur'] = _set_motion_blur(scene, merged['motion_blur'])
    else:
        eff['motion_blur'] = (_read(scene, 'render.motion_blur_shutter', None)
                              if _read(scene, 'render.use_motion_blur', False) else 0.0)

    # ── 7) 矢量模糊
    if merged.get('vector_blur') is not None:
        eff['vector_blur'] = _set_vector_blur(scene, bool(merged['vector_blur']))
    else:
        eff['vector_blur'] = None

    # ── 8) 透明背景
    if merged.get('film_transparent') is not None:
        eff['film_transparent'] = _set_film_transparent(
            scene, bool(merged['film_transparent']))
    else:
        eff['film_transparent'] = _read(scene, 'render.film_transparent')

    # ── 9) 摄影机（焦距 / 景深）
    eff['lens'] = (_set_lens(scene, merged['lens'])
                   if merged.get('lens') is not None
                   else _read(_read(scene, 'camera', None), 'data.lens', None))
    eff['dof'] = (_set_dof(scene, bool(merged['dof']))
                  if merged.get('dof') is not None else None)

    # ── 10) Cycles 设备
    eff['gpu'] = (_set_gpu(scene, bool(merged['gpu']))
                  if merged.get('gpu') is not None else None)

    # ── 11) Cycles 降噪 / 自适应
    if merged.get('denoise') is not None:
        _write(scene, 'cycles.use_denoising', bool(merged['denoise']),
               'cycles.use_denoising')
        eff['denoise'] = _read(scene, 'cycles.use_denoising')
    else:
        eff['denoise'] = _read(scene, 'cycles.use_denoising')
    if merged.get('adaptive') is not None:
        _write(scene, 'cycles.use_adaptive_sampling', bool(merged['adaptive']),
               'cycles.use_adaptive_sampling')
    if merged.get('noise_threshold') is not None:
        # ⚠️ 这个属性在部分版本没有：写不进去就只打一行提示，不中断
        _write(scene, 'cycles.adaptive_threshold',
               float(merged['noise_threshold']), 'cycles.adaptive_threshold')
    raw = _read(scene, 'cycles.adaptive_threshold')
    try:
        eff['noise_threshold'] = round(float(raw), 4)   # 0.0099999997 → 0.01
    except Exception:
        eff['noise_threshold'] = raw
    eff['adaptive'] = _read(scene, 'cycles.use_adaptive_sampling')

    # ── 12) 简化
    if merged.get('simplify') is not None:
        eff['simplify'] = _set_simplify(scene, bool(merged['simplify']),
                                       merged.get('simplify_threshold'))
    else:
        eff['simplify'] = _read(scene, 'render.use_simplify')
    eff['simplify_threshold'] = _read(scene, 'render.simplify_subdivision_render')

    # ── 13) 三点光
    eff['lights'] = (three_point_lights(scene, merged['lights'])
                     if merged.get('lights') else None)

    eff['skipped'] = list(_SKIPPED)
    _log('%s → %s / %s / %s 采样 / 镜头 %smm / VT %s(%s)%s'
         % (key, eff.get('engine'), eff.get('res'), eff.get('samples'),
            eff.get('lens'), eff.get('view_transform'), eff.get('look'),
            ('，跳过 %d 项' % len(_SKIPPED)) if _SKIPPED else ''))
    return eff
