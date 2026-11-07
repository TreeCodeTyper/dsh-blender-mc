# -*- coding: utf-8 -*-
"""把一句中文提示词编译成 mc_film 分镜表 / mc_anim 预设。

不是"再写死一支动画"：presets/ 下面所有 JSON 配方（run / walk / dash /
ride / superman / conduct / yujian ...）都可以被提示词选中；镜头数量、
景别、画幅、时长、相机参数也都从提示词里取。

用法：
    from prompt_film import compile_prompt, compile_anim
    plan = compile_prompt("10秒仙侠御剑，背面跟拍，黄昏，9:16", blend="封面.blend")

规则是确定性的、可解释的；不调用大模型。以后要更聪明，只需要替换
pick_preset / _camera_env，不用改 film.py。
"""
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
PRESETS = os.path.join(HERE, 'blender', 'presets')

# 关键词顺序 = 优先级；先匹配"战斗/施法"，再匹配"跑/飞"。
RULES = [
    ('killaura', ('杀', '战斗', '僵尸', '围攻', '砍', '进圈', '光环', 'aura')),
    ('conduct', ('施法', '结印', '法术', '法阵', '念咒', '符', '召唤')),
    ('dash', ('冲刺', '突进', '加速', '闪现', '疾跑')),
    ('ride', ('骑', '坐骑', '马', '策马')),
    ('superman', ('超人', '腾空', '冲天', '起飞')),
    ('run', ('跑', '奔跑', '追', '逃', '追逐')),
    ('walk', ('走', '散步', '漫步')),
    ('yujian', ('御剑', '仙侠', '飞剑', '剑', '飞天', '飞行', '踏剑')),
]
DEFAULT_PRESET = 'yujian'


def _recipe(preset):
    p = os.path.join(PRESETS, '%s.json' % preset)
    if not os.path.isfile(p):
        return None
    try:
        with open(p, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def pick_preset(prompt):
    text = str(prompt or '').lower()
    for preset, keys in RULES:
        if any(k in text for k in keys):
            return preset
    return DEFAULT_PRESET


def _seconds(prompt, default):
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:秒|s\b)', str(prompt or ''), re.I)
    if not m:
        return default
    try:
        return max(1.0, float(m.group(1)))
    except ValueError:
        return default


def _ratio(prompt, recipe):
    text = str(prompt or '')
    if re.search(r'9\s*[:：]\s*16|竖屏|竖版|portrait', text, re.I):
        return '720x1280'
    if re.search(r'16\s*[:：]\s*9|横屏|横版|landscape', text, re.I):
        return '1280x720'
    r = (recipe or {}).get('render') or {}
    return str(r.get('res') or '1280x720').replace('×', 'x')


def _camera_env(prompt, preset=None):
    text = str(prompt or '')
    env = {}
    # yujian 的旧通道；其它配方走通用的 DSH_ANIM_CAM_*。
    yj = preset in (None, 'yujian')
    if yj:
        if any(k in text for k in ('背面跟拍', '跟拍', '背后', '后追')):
            env['DSH_YJ_CAM_D_SCALE'] = '1.8'
            env['DSH_YJ_CAM_RISE_ADD'] = '8'
        if any(k in text for k in ('仰拍', '低机位', '低角度')):
            env['DSH_YJ_CAM_Z_ADD'] = '-1.5'
            env['DSH_YJ_CAM_RISE_ADD'] = '2'
        if any(k in text for k in ('俯拍', '高机位', '航拍')):
            env['DSH_YJ_CAM_Z_ADD'] = '2.5'
        if any(k in text for k in ('推近', '特写', '近景')):
            env['DSH_YJ_CAM_LENS_SCALE'] = '1.15'
            env['DSH_YJ_CAM_D_SCALE'] = '0.85'
        if any(k in text for k in ('环绕', '绕拍', '旋转')):
            env['DSH_YJ_CAM_AZ_ADD'] = '-18'
    else:
        if any(k in text for k in ('背面跟拍', '跟拍', '背后', '后追')):
            env['DSH_ANIM_CAM_D_SCALE_ADD'] = '0.8'
            env['DSH_ANIM_CAM_RISE_ADD'] = '0.35'
        if any(k in text for k in ('仰拍', '低机位', '低角度')):
            env['DSH_ANIM_CAM_Z_ADD'] = '-0.35'
            env['DSH_ANIM_CAM_RISE_ADD'] = '0.35'
        if any(k in text for k in ('俯拍', '高机位', '航拍')):
            env['DSH_ANIM_CAM_Z_ADD'] = '0.9'
        if any(k in text for k in ('推近', '特写', '近景')):
            env['DSH_ANIM_CAM_LENS_SCALE_ADD'] = '0.15'
            env['DSH_ANIM_CAM_D_SCALE_ADD'] = '-0.15'
        if any(k in text for k in ('环绕', '绕拍', '旋转')):
            env['DSH_ANIM_CAM_AZ_ADD'] = '-18'
    return env


def _shots(prompt, preset, total, base_env):
    text = str(prompt or '')
    one = any(k in text for k in ('一镜到底', '长镜头', '单镜头')) or total <= 72
    if one:
        return [{
            'id': 'S01', 'preset': preset, 'range': [1, total],
            'env': dict(base_env), 'shot_size': '全景' if total > 160 else '中景',
            'camera': '提示词一镜到底', 'note': '按提示词生成',
        }]
    # 三镜：全景建立 → 中景跟随 → 近景收尾；不重叠，便于直接剪。
    a = max(1, int(round(total * 0.40)))
    b = max(a + 1, int(round(total * 0.75)))
    b = min(b, total - 1) if total > 3 else total
    yj = preset == 'yujian'

    def _mul(env, key, k):
        env[key] = str(float(env.get(key, 1.0) or 1.0) * k)

    def _add(env, key, d):
        env[key] = str(float(env.get(key, 0.0) or 0.0) + d)

    # 建立镜头：拉远 + 抬高 + 广一点，先把环境交代清楚
    env_wide = dict(base_env)
    if yj:
        _mul(env_wide, 'DSH_YJ_CAM_D_SCALE', 1.6)
        _add(env_wide, 'DSH_YJ_CAM_Z_ADD', 1.8)
        _mul(env_wide, 'DSH_YJ_CAM_LENS_SCALE', 0.85)
        _add(env_wide, 'DSH_YJ_CAM_AZ_ADD', 25.0)
    else:
        _add(env_wide, 'DSH_ANIM_CAM_D_SCALE_ADD', 0.45)
        _add(env_wide, 'DSH_ANIM_CAM_Z_ADD', 0.8)
        _add(env_wide, 'DSH_ANIM_CAM_LENS_SCALE_ADD', -0.15)
        _add(env_wide, 'DSH_ANIM_CAM_AZ_ADD', 25.0)

    # 收尾近景：推近 + 长焦（非 yujian 配方以前误用了 DSH_YJ_*，等于没生效）
    env_close = dict(base_env)
    if yj:
        _mul(env_close, 'DSH_YJ_CAM_LENS_SCALE', 1.15)
        _mul(env_close, 'DSH_YJ_CAM_D_SCALE', 0.85)
        _add(env_close, 'DSH_YJ_CAM_Z_ADD', -0.4)
        _add(env_close, 'DSH_YJ_CAM_AZ_ADD', -20.0)
    else:
        _add(env_close, 'DSH_ANIM_CAM_D_SCALE_ADD', -0.3)
        _add(env_close, 'DSH_ANIM_CAM_LENS_SCALE_ADD', 0.25)
        _add(env_close, 'DSH_ANIM_CAM_Z_ADD', -0.15)
        _add(env_close, 'DSH_ANIM_CAM_AZ_ADD', -20.0)

    return [
        {'id': 'S01', 'preset': preset, 'range': [1, a],
         'env': env_wide, 'shot_size': '全景', 'camera': '建立镜头（拉远抬高）',
         'note': '按提示词生成：先交代环境'},
        {'id': 'S02', 'preset': preset, 'range': [a + 1, b],
         'env': dict(base_env), 'shot_size': '中景', 'camera': '跟随镜头',
         'note': '动作主体'},
        {'id': 'S03', 'preset': preset, 'range': [b + 1, total],
         'env': env_close, 'shot_size': '近景', 'camera': '收尾推近',
         'note': '落点/情绪'},
    ]


def compile_prompt(prompt, blend=None, fps=None, res=None):
    """提示词 -> mc_film plan dict。blend 必须是已有封面工程。"""
    text = str(prompt or '').strip()
    if not text:
        raise ValueError('prompt 不能为空')
    preset = pick_preset(text)
    rec = _recipe(preset) or {}
    fps = int(fps or rec.get('fps') or 24)
    recipe_total = int(rec.get('duration') or 240)
    seconds = _seconds(text, recipe_total / float(fps))
    raw_total = int(round(seconds * fps))
    # 循环配方（run/walk）可以随手要 10 秒：anim_kit 会补循环遍数。
    # 非循环配方（yujian 等）只在原时长内截断，避免把 10 秒戏放成一帧帧慢动作。
    cyclic = bool(rec.get('cycle') and rec.get('poses'))
    cap = max(recipe_total, int(fps * 60)) if cyclic else recipe_total
    total = max(1, min(raw_total, cap))
    res = res or _ratio(text, rec)
    env = _camera_env(text, preset)
    # 默认保留封面工程里的 Minecraft 地形；只有明确说“棚拍/纯色背景”才切回棚拍。
    if not any(k in text for k in ('棚拍', '纯色背景', '演播室')):
        env['DSH_ANIM_KEEP_WORLD'] = '1'
        env['DSH_ANIM_TERRAIN_SNAP'] = '1'
        env['DSH_ANIM_NATURAL_LIGHT'] = '1'
    shots = _shots(text, preset, total, env)
    title = re.split(r'[，,。；;]', text)[0][:24] or '提示词成片'
    warnings = []
    if raw_total > total:
        if cyclic:
            warnings.append('preset %s 是循环动作，已按 %.1f 秒补足循环遍数。'
                            % (preset, total / float(fps)))
        else:
            warnings.append('preset %s 只有 %.1f 秒（%d 帧），已截到 %.1f 秒；'
                            '要更长只能换配方或放慢 fps。'
                            % (preset, recipe_total / float(fps), recipe_total,
                               total / float(fps)))
    if any(k in text for k in ('两个', '双人', '两人')):
        warnings.append('当前预设是单角色；双人/双角色需要另做角色复制，这条先按单角色生成。')
    if not rec and preset not in ('killaura', 'aura'):
        warnings.append('没有找到 preset %s 的 JSON 配方，退回 yujian。' % preset)
        preset = 'yujian'
        rec = _recipe(preset) or {}
    return {
        'title': title,
        'fps': fps,
        'res': res,
        'samples': int((rec.get('render') or {}).get('samples') or 24),
        'blend': blend,
        'total': total,
        'prompt': text,
        'preset': preset,
        'shots': shots,
        'warnings': warnings,
    }


def compile_anim(prompt, fps=None):
    """提示词 -> mc_anim 的 {preset,total,fps}。"""
    plan = compile_prompt(prompt, blend='__anim__', fps=fps)
    return {'preset': plan['preset'], 'total': plan['total'], 'fps': plan['fps'],
            'res': plan['res'], 'samples': plan['samples'],
            'env': dict((plan.get('shots') or [{}])[0].get('env') or {}),
            'prompt': plan['prompt'], 'warnings': plan['warnings']}
