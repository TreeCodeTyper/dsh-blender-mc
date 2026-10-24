# -*- coding: utf-8 -*-
"""readiness —— 廉价的运行条件快照，供插件主动告诉用户「还缺什么、怎么装」

为什么单独一个模块，而不直接复用 requirements.check_all
------------------------------------------------------
check_all 里有两处贵得不能放进会话启动路径的东西：

    _has_mcprep()            在 addons 目录里找不到 MCprep 时会起一次 Blender
                             子进程（timeout 300s）
    probe_mcprep_operator()  每次 5-10 秒，专门验证操作符是否注册上

会话建立 / 每一步模型组装都会读这个东西，所以这里只做「读目录、读文件、
import 一个模块」级别的检查（最贵是 import cv2，约 0.2 秒），而且只在
后台子进程里跑一次，结果落盘缓存。

产物
----
    <DSH_HOME|%USERPROFILE%/.dsh>/blender-mc/readiness.json

lib/index.js 读这份缓存，把「缺哪些 + 怎么装」注进模型上下文；
真正需要深挖（操作符能不能用）时仍然走 `cli.py doctor`。

⚠️ 这里写的 fix 必须是一两行、能直接照做的。整段完整指引留在
   requirements.REQUIREMENTS[...]['how'] 里，doctor 会打出来。
"""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 缺东西时给模型看的一行解法（不是给用户照抄的完整教程）
FIX = {
    'python_deps':
        'pip install opencv-python numpy；没有 pip 就用 Blender 自带的 python：'
        '"<Blender>/5.2/python/bin/python.exe" -m pip install opencv-python numpy',
    'blender':
        '装 Blender 4.2 LTS 或更新版本（https://www.blender.org/download/），'
        '装完把路径写进插件设置或环境变量 BLENDER',
    'mcprep':
        'GPL 第三方 addon，下载前必须先征得用户同意；下载 zip 后 '
        'python py/cli.py setup <zip 所在目录> 会自动装进 Blender 的 addons',
    'rig':
        '可选：BPS Character Rig V4.0.9（别用 4.0.10 BETA1，头部网格是平的），'
        '解压后 python py/cli.py setup <目录>',
    'item':
        '可选：Minecraft_Item_Rig.blend（Cycles Minecraft Rig 整合包里），'
        'python py/cli.py setup <解压目录>',
    'textures':
        '可选：从 .minecraft/versions/<版本>/<版本>.jar 里取 '
        'assets/minecraft/textures/block/，再 python py/cli.py setup <目录>',
    'ffmpeg':
        '可选：ffmpeg.exe 放进 PATH，或 python py/cli.py setup <解压目录>；'
        '只缺它时动画仍能渲出 PNG 序列帧',
}


def _dsh_home():
    return (os.environ.get('DSH_HOME')
            or os.path.join(os.environ.get('USERPROFILE') or os.path.expanduser('~'), '.dsh'))


def cache_path():
    return os.path.join(_dsh_home(), 'blender-mc', 'readiness.json')


def _module_version(name):
    try:
        m = __import__(name)
        return getattr(m, '__version__', '?')
    except Exception:
        return None


def _plugin_version():
    try:
        with open(os.path.join(HERE, '..', 'package.json'), encoding='utf-8') as f:
            return json.load(f).get('version')
    except Exception:
        return None


def _mcprep_dir():
    """只在 addons 目录里找 —— 不起 Blender（贵的那条路留给 doctor）。"""
    from requirements import _addon_dirs, _looks_like_addon
    for addons in _addon_dirs():
        try:
            if os.path.isdir(addons) and any(
                    d.lower().startswith('mcprep')
                    and _looks_like_addon(os.path.join(addons, d))
                    for d in os.listdir(addons)):
                return addons
        except OSError:
            continue
    return None


def _assets():
    try:
        from pipeline import resolve_assets
        return resolve_assets() or {}
    except Exception:
        return {}


def snapshot(blender=None, deep=False, with_hint=True):
    """当前机器缺什么。全部是便宜检查；deep=True 才问 Blender 要操作符。"""
    from requirements import REQUIREMENTS, _find_blender

    items = []

    def add(key, ok, detail):
        spec = REQUIREMENTS[key]
        items.append({
            'key': key,
            'label': spec['label'],
            'required': bool(spec['required']),
            'ok': bool(ok),
            'detail': detail,
            'fix': FIX.get(key, ''),
        })

    cv2v = _module_version('cv2')
    npv = _module_version('numpy')
    add('python_deps', bool(cv2v and npv),
        f'cv2 {cv2v or "缺失"} / numpy {npv or "缺失"}')

    b = _find_blender(blender)
    add('blender', bool(b), b or '找不到')

    mp_dir = _mcprep_dir()
    if deep and b:
        # ⚠️ 坑 80：以前 `if mp_dir:` 写在 `elif deep:` 前面 —— 只要 addons 目录里
        #   有个同名目录，--deep 就永远走不到，「我确实要诊断」等于没加。
        from requirements import probe_mcprep_operator
        op = probe_mcprep_operator(b)
        if op is True:
            add('mcprep', True, 'Blender 确认已注册（操作符可用）')
        elif op is False:
            add('mcprep', False, '目录在，但 addon 没注册上（操作符不可用，跑 mc_doctor 看原因）')
        else:
            add('mcprep', bool(mp_dir),
                '已安装（addons 目录里有，操作符没验成）' if mp_dir
                else 'addons 目录里没找到（可能装在别处，跑 mc_doctor 复核）')
    elif mp_dir:
        add('mcprep', True, '已安装（addons 目录里有）')
    else:
        add('mcprep', False, 'addons 目录里没找到（可能装在别处，跑 mc_doctor 复核）')

    assets = _assets()
    for key in ('rig', 'item', 'textures', 'ffmpeg'):
        v = assets.get(key)
        add(key, bool(v and os.path.exists(str(v))), str(v) if v else '没找到')

    missing_required = [i for i in items if i['required'] and not i['ok']]
    missing_optional = [i for i in items if not i['required'] and not i['ok']]

    snap = {
        'ok': not missing_required,
        'version': _plugin_version(),
        'checked_at': int(time.time()),
        'checked_at_text': time.strftime('%Y-%m-%d %H:%M', time.localtime()),
        'python': sys.executable,
        'blender': b,
        'items': items,
        'missing_required': [i['key'] for i in missing_required],
        'missing_optional': [i['key'] for i in missing_optional],
        'fingerprint': {
            'blender': b,
            'python': sys.executable,
            'version': _plugin_version(),
        },
    }
    if with_hint:
        snap['hint'] = hint(snap)
    return snap


def hint(snap):
    """给模型看的一小段话（有缺口时才非空）。

    ★ 长度是硬约束：这段会进每一步的上下文，写长了等于给所有人加税。
      目标 ≤ 700 字；超了宁可砍掉可选项的细节。
    """
    if not snap or snap.get('ok') and not snap.get('missing_optional'):
        return ''
    by_key = {i['key']: i for i in snap['items']}
    hard = [by_key[k] for k in snap.get('missing_required', [])]
    soft = [by_key[k] for k in snap.get('missing_optional', [])]

    out = [f"### dsh-blender-mc 运行条件（插件自检缓存，{snap.get('checked_at_text', '')}）"]
    if hard:
        out.append(f"**必需项缺 {len(hard)} 项，现在调 mc_shoot / mc_anim 会直接失败：**")
        for it in hard:
            out.append(f"- {it['label']}（{it['detail']}）→ {it['fix']}")
    if soft:
        names = '、'.join(f"{it['label']}（{it['detail'][:40]}）" for it in soft)
        out.append(f"可选项缺 {len(soft)} 项，不影响出图，只少对应功能：{names}")
    out.append(
        '→ 用户第一次要出图/动画时，主动把上面缺的东西和对应命令讲给他，'
        '别等他撞到报错才发现；装完让他跑 mc_doctor 复核（那份完整指引更细）。')
    text = '\n'.join(out)
    return text if len(text) <= 1600 else text[:1600] + '…'


def write_cache(snap):
    path = cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def load_cache():
    try:
        with open(cache_path(), encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def main(argv=None):
    """cli.py readiness：算一份快照、写缓存，并把 hint（或 json）打出来。"""
    import argparse
    ap = argparse.ArgumentParser(prog='dsh-blender-mc readiness')
    ap.add_argument('--blender', default=None)
    ap.add_argument('--deep', action='store_true',
                    help='额外问一次 Blender（5-10 秒），用于 doctor 复核')
    ap.add_argument('--json', action='store_true', help='输出完整 json')
    ap.add_argument('--no-write', action='store_true', help='只算不写缓存')
    a = ap.parse_args(argv)

    snap = snapshot(a.blender, deep=a.deep)
    if not a.no_write:
        try:
            snap['cache_file'] = write_cache(snap)
        except Exception as e:      # 缓存写不了不能影响主流程
            snap['cache_error'] = str(e)
    if a.json:
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(snap['hint'] or '✅ 运行条件齐全，没有要提示的。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
