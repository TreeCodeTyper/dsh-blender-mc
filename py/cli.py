# -*- coding: utf-8 -*-
"""dsh-blender-mc 命令行入口

    python cli.py doctor                        自检（Blender / 依赖 / 骨架 / 物品库）
    python cli.py extract <资源目录> --out <目录>          提取 Bedrock 模型 → OBJ
    python cli.py scan   <存档> --area x0,x1,z0,z1        在存档里找机位
    python cli.py shoot  <存档> --area ... --out <目录>    出封面（含审图闭环）
    python cli.py anim   <存档> --area ... --out <目录>    出 10 秒动画
    python cli.py check  <图...>                          只审图
"""
import argparse
import json
import os
import sys

# ⚠️ 坑 66：Windows 控制台默认是 GBK，打印中文以外的字符（✅ ❌ ⚠️ 这些）
#   会直接 UnicodeEncodeError 崩掉。实测被这个绊了两次。
#   与其要求用户记得设 PYTHONIOENCODING=utf-8，不如这里自己强制过去。
for _s in (sys.stdout, sys.stderr):
    try:
        if (_s.encoding or '').lower().replace('-', '') != 'utf8':
            _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'blender'))


def _area(s):
    try:
        v = [int(x) for x in s.split(',')]
    except ValueError:
        raise argparse.ArgumentTypeError('area 要是 4 个整数: x0,x1,z0,z1（收到 %r）' % s)
    if len(v) != 4:
        raise argparse.ArgumentTypeError('area 要 4 个数: x0,x1,z0,z1')
    x0, x1, z0, z1 = v
    if x0 > x1 or z0 > z1:
        # 反着写是最常见的输入错误：以前会静默返回 0 个机位，让人以为地图有问题
        x0, x1 = min(x0, x1), max(x0, x1)
        z0, z1 = min(z0, z1), max(z0, z1)
        print('（area 的起止写反了，已自动按 %d,%d,%d,%d 处理）' % (x0, x1, z0, z1))
    return (x0, x1, z0, z1)


def _res(s):
    """尺寸必须是 WxH，且不能是 0/负数 —— 以前写 'abc' 会一路传到渲染才失败。"""
    t = str(s).lower().replace('*', 'x').replace('×', 'x').strip()
    if 'x' not in t:
        raise argparse.ArgumentTypeError('尺寸要写成 宽x高，例如 1920x1080（收到 %r）' % s)
    a, b = t.split('x', 1)
    try:
        w, h = int(a), int(b)
    except ValueError:
        raise argparse.ArgumentTypeError('尺寸要写成 宽x高，例如 1920x1080（收到 %r）' % s)
    if w < 16 or h < 16:
        raise argparse.ArgumentTypeError('尺寸太小: %dx%d（最小 16x16）' % (w, h))
    if w > 7680 or h > 4320:
        raise argparse.ArgumentTypeError('尺寸太大: %dx%d（上限 7680x4320）' % (w, h))
    return '%dx%d' % (w, h)


def _pair_int(s):
    """x,z / y0,y1 这种两个整数：以前直接 int() 会甩 ValueError traceback。"""
    try:
        a, b = [int(x) for x in str(s).split(',')]
    except ValueError:
        raise argparse.ArgumentTypeError('要写成 两个整数，用逗号分隔，例如 100,-40（收到 %r）' % s)
    return (a, b)


def _posint(s):
    """正整数：--samples 0 / --attempts 0 以前会被静默接受，渲出来是废图。"""
    try:
        v = int(s)
    except ValueError:
        raise argparse.ArgumentTypeError('要是整数（收到 %r）' % s)
    if v < 1:
        raise argparse.ArgumentTypeError('必须 ≥ 1（收到 %d）' % v)
    return v


def _nonneg_int(s):
    """非负整数：--trees 0 是合法值（不要树），负数/小数要拦住。"""
    try:
        v = int(str(s).strip())
    except ValueError:
        raise argparse.ArgumentTypeError('要是整数（收到 %r）' % s)
    if v < 0:
        raise argparse.ArgumentTypeError('不能是负数（收到 %d）' % v)
    return v


def _float_in(lo, hi):
    """生成一个「该范围内的数」校验器（--fog / --hour 用）。"""
    def _check(s):
        try:
            v = float(s)
        except ValueError:
            raise argparse.ArgumentTypeError('要是数字（收到 %r）' % s)
        if v < lo or v > hi:
            raise argparse.ArgumentTypeError('要在 %g–%g 之间（收到 %g）' % (lo, hi, v))
        return v
    return _check


def _hour(s):
    """时刻：0–24，或预设名 morning|noon|dusk|night。

    也顺手认 6am / 18:30 这类写法 —— 但认不出就报错，别静默当成 8 点。
    """
    t = str(s).strip().lower()
    if t in ('morning', 'noon', 'dusk', 'night'):
        return t
    if ':' in t:
        try:
            hh, mm = [float(x) for x in t.split(':', 1)]
        except ValueError:
            raise argparse.ArgumentTypeError(
                '时刻要写成 0-24，或 morning|noon|dusk|night（收到 %r）' % s)
        if not (0 <= hh <= 24 and 0 <= mm < 60):
            raise argparse.ArgumentTypeError('时刻超出范围: %r' % s)
        return round(hh + mm / 60.0, 4)
    if t.endswith('am') or t.endswith('pm'):
        try:
            v = float(t[:-2])
        except ValueError:
            raise argparse.ArgumentTypeError('时刻要写成 0-24（收到 %r）' % s)
        if t.endswith('pm') and v < 12:
            v += 12
        elif t.endswith('am') and v >= 12:
            v -= 12
        return min(24.0, max(0.0, v))
    try:
        v = float(t)
    except ValueError:
        raise argparse.ArgumentTypeError(
            '时刻要写成 0-24，或 morning|noon|dusk|night（收到 %r）' % s)
    if v < 0 or v > 24:
        raise argparse.ArgumentTypeError('时刻要在 0–24 之间（收到 %g）' % v)
    return v


def _pair_float(s):
    """'min,max' 两个数：--tree-dist / --tree-h 用。写反了自动换个头。"""
    parts = str(s).replace(' ', '').split(',')
    if len(parts) != 2:
        raise argparse.ArgumentTypeError('要写成 两个数，逗号分隔，例如 7,20（收到 %r）' % s)
    try:
        a, b = float(parts[0]), float(parts[1])
    except ValueError:
        raise argparse.ArgumentTypeError('要写成 两个数，逗号分隔，例如 7,20（收到 %r）' % s)
    if a < 0 or b < 0:
        raise argparse.ArgumentTypeError('不能是负数（收到 %r）' % s)
    if a > b:
        a, b = b, a
        print('（%s 的起止写反了，已自动按 %g,%g 处理）' % ('tree 区间', a, b))
    return '%g,%g' % (a, b)


def _switch(s):
    """开/关：--clouds 1|0（也认 on/off/true/false/yes/no）。"""
    t = str(s).strip().lower()
    if t in ('1', 'on', 'true', 'yes', 'y'):
        return 1
    if t in ('0', 'off', 'false', 'no', 'n'):
        return 0
    raise argparse.ArgumentTypeError('要是 1 或 0（收到 %r）' % s)


def _need_save(save):
    """存档目录先查一遍，别等 anvil 抛 FileNotFoundError 把 traceback 甩给用户。"""
    if not os.path.isdir(save):
        print('❌ 存档目录不存在: %s' % save)
        print('   上传过的地图可以先用: python py/cli.py map list')
        print('   或指定: python py/cli.py map resolve --id <id> --json')
        return False
    try:
        import maplib
        rel = maplib.find_region_dir(save)[0]
    except Exception:  # noqa: BLE001 —— 别为了个提示把 CLI 弄崩
        rel = 'region' if os.path.isdir(os.path.join(save, 'region')) else None
    if not rel:
        print('❌ 这个目录里找不到 region/，也不像 26.x 的 dimensions/ 布局: %s' % save)
        print('   要选到含 level.dat 的那一层（经典布局里就是含 region/ 的那一层）')
        return False
    return True


def cmd_setup(a):
    """装配外部资产：从本地已有的资源目录搬到 vendor/，并把 MCprep 装进 Blender"""
    from setup_assets import assemble, default_sources, WANTED
    srcs = a.sources
    if not srcs:
        srcs = default_sources()
        print('（没给资源目录，默认扫这些:）')
        for s in srcs:
            print(f'    {s}')
    if not srcs:
        print('❌ 没给资源目录，也没找到默认的 pack/')
        print('   用法: python cli.py setup <资源目录> [<资源目录2> ...]')
        print('\n需要的东西:')
        for k, s in WANTED.items():
            mark = '必需' if s['required'] else '可选'
            print(f'   [{mark}] {k:9s} {s["desc"]}')
        return 1
    m = assemble(srcs, blender=a.blender, dry=a.dry_run,
                 copy=not a.link, progress=print)
    n = len(m.get('assets', {}))
    print(f'\n装配了 {n} 项资产 → {m["vendor"]}')
    return 0 if n else 1


def cmd_doctor(a):
    """检查运行条件。缺什么就把【完整安装指引】打出来。"""
    from requirements import format_report
    txt, res = format_report(a.blender)
    print(txt)
    return 0 if all(ok for _, ok, _, spec in res if spec['required']) else 1


def _cmd_readiness(a):
    """便宜版自检：算快照、写缓存、把给模型看的 hint 打出来。

    ⚠️ 这里刻意不复用 doctor：doctor 会打印几十行完整指引、还可能花 5-10 秒
       问 Blender；readiness 是后台几十毫秒跑完的东西（插件每台机器每次都会跑）。
    """
    import readiness
    snap = readiness.snapshot(getattr(a, 'blender', None), deep=getattr(a, 'deep', False))
    if not getattr(a, 'no_write', False):
        try:
            snap['cache_file'] = readiness.write_cache(snap)
        except Exception as e:      # 缓存写不进去不能影响主流程
            snap['cache_error'] = str(e)
    if getattr(a, 'json', False):
        import json
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(snap['hint'] or '✅ 运行条件齐全，没有要提示的。')
    return 0


def cmd_extract(a):
    """把资源目录里的 Bedrock 几何批量转成 OBJ，交给 Blender 摆放。"""
    import json
    from extract import extract
    m = extract(a.assets, a.out, keyword=a.filter, anim=a.anim,
                anim_name=a.anim_name, list_only=a.list, progress=print)
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        print(f'\n已写 {a.json}')
    return 0 if m.get('count') else 1


def cmd_scan(a):
    from pipeline import step_scan
    if not _need_save(a.save):
        return 1
    y = a.y
    shots = step_scan(a.save, a.area, y=y, radius=a.radius,
                      top_n=a.top, progress=print)
    print(f'\n推荐机位 {len(shots)} 个（开阔度 / 坐标 / 地面Y / 方位角 / 平坦格数）:')
    for s in shots:
        print("  开阔度%.2f  (%5d,%5d) Y=%d  az=%3d°  平坦%4d"
              % (s['openness'], s['x'], s['z'], s['y'], s['az'], s['flat']))
    if a.json:
        import json
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(shots, f, ensure_ascii=False, indent=2)
        print(f'\n已写 {a.json}')
    return 0


def cmd_camera_path(a):
    from anvil import World
    from scan import surface_map
    from camera_path import plan_camera_path
    if not _need_save(a.save):
        return 1
    x0, x1, z0, z1 = a.area
    st = a.start
    gl = a.goal
    w = World(a.save)
    print(f'相机路径: ({st[0]},{st[1]}) -> ({gl[0]},{gl[1]})')
    top = surface_map(w, x0, x1, z0, z1, y0=a.y[0], y1=a.y[1])
    pts = plan_camera_path(top, st, gl, bounds=(x0, x1, z0, z1),
                           samples_per_seg=a.samples, height_offset=a.height)
    data = {'start': list(st), 'goal': list(gl), 'points': pts}
    print(f'路径点: {len(pts)}')
    if a.out:
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        with open(a.out, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f'已写 {a.out}')
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if pts else 1


def cmd_shoot_search(a):
    from shoot_search import search
    if not _need_save(a.save):
        return 1
    env = {}
    if a.pose:
        env['DSH_POSE'] = a.pose
    if a.hour:
        env['DSH_HOUR'] = str(a.hour)
    if a.face:
        env['DSH_FACE'] = str(a.face)
    if a.trees is not None:
        env['DSH_TREES'] = str(a.trees)
    if a.item_right:
        env['DSH_ITEM_R'] = a.item_right
    if a.armor:
        env['DSH_ARMOR'] = a.armor
    r = search(a.save, a.area, a.out, center=a.center, top=a.top,
               scan_radius=a.radius, skin=a.skin, env=env,
               n_init=a.n_init, iters=a.iters, seed=a.seed,
               progress=print)
    print('\n最佳参数:', r['best'])
    print('搜索分数:', r['score'])
    print('成图:', r['png'])
    return 0


def cmd_shoot(a):
    from pipeline import shoot
    if not _need_save(a.save):
        return 1
    skin = getattr(a, 'skin', None)
    if skin:
        # 皮肤认「绝对路径」或「皮肤库里的名字」；build_cover.py 直接读 DSH_SKIN
        path = skin
        if not os.path.isfile(path):
            home = os.environ.get('DSH_HOME') or os.path.join(
                os.environ.get('USERPROFILE') or os.path.expanduser('~'), '.dsh')
            for cand in (os.path.join(home, 'blender-mc', 'skins', skin),
                         os.path.join(home, 'blender-mc', 'skins', skin + '.png')):
                if os.path.isfile(cand):
                    path = cand
                    break
        if not os.path.isfile(path):
            print(f'❌ 找不到皮肤: {skin}')
            print('   给绝对路径，或把 png 放进 <DSH_HOME>/blender-mc/skins/ 再用名字')
            return 1
        os.environ['DSH_SKIN'] = os.path.abspath(path)
        print(f'皮肤: {os.environ["DSH_SKIN"]}')
    params = {}
    if a.yaw is not None:
        params['yaw'] = a.yaw
    if a.cam_az is not None:
        params['cam_az'] = a.cam_az
    if a.dist is not None:
        params['dist'] = a.dist
    if a.sky is not None:
        params['sky_strength'] = a.sky
    if a.view_transform:
        params['view_transform'] = a.view_transform
    if a.sink is not None:
        params['sink'] = a.sink
    if a.res:
        params['res'] = a.res
    if a.samples:
        params['samples'] = a.samples
    if getattr(a, 'vol_samples', None):
        params['vol_samples'] = a.vol_samples
    # ★ P0-2 干净包
    if getattr(a, 'backdrop', None):
        params['backdrop'] = a.backdrop
    if getattr(a, 'rim_scale', None) is not None:
        params['rim_scale'] = a.rim_scale
    if getattr(a, 'goal', None):
        params['goal'] = a.goal
    # ── v0.5.0 新参数：只把「用户真给了」的塞进去 ─────────────────────
    #    item 的空字符串是合法值（= 空手），所以按 is not None 判断；
    #    pipeline.step_render 也按 is not None 决定要不要设 env，
    #    于是不传任何新参数时 Blender 侧看到的 env 与 v0.4.6 一模一样。
    #    ⚠️ CLI 叫 --item-right，pipeline 里的键是 item_r —— 这里统一改名，
    #       不要直接把 argparse 的 dest 塞进 params（否则静默不生效）。
    for arg_name, key in (('item_right', 'item_r'),
                          ('item_left', 'item_l'),
                          ('items', 'items'),
                          ('armor', 'armor'),
                          ('armor_color', 'armor_color'),
                          ('trees', 'trees'),
                          ('tree_dist', 'tree_dist'),
                          ('tree_h', 'tree_h'),
                          ('hour', 'hour'),
                          ('fog', 'fog'),
                          ('clouds', 'clouds'),
                          ('face', 'face'),
                          ('pose', 'pose'),
                          ('skin3d_thick', 'skin3d_thick')):
        v = getattr(a, arg_name, None)
        if v is not None:
            params[key] = v
    if getattr(a, 'skin3d', False):
        params['skin3d'] = True
    if params.get('items') is not None and (
            params.get('item_r') is not None or params.get('item_l') is not None):
        # 契约：--items 给了就覆盖 --item-right/--item-left
        print('（同时给了 --items 和 --item-right/--item-left，以 --items 为准）')
        params.pop('item_r', None)
        params.pop('item_l', None)
    center = a.center
    r = shoot(a.save, a.area, a.out, blender=a.blender, params=params,
              center=center, radius=a.radius, max_attempts=a.attempts,
              target_score=a.target, progress=print)
    print('\n' + '=' * 66)
    print(f"最终: {r['verdict']} {r['score']} 分   {r['png']}")
    print(f"尺寸: {r.get('res_actual') or '?'}（请求 {r.get('res_requested') or '默认'}）")
    print(f"机位 ({r['center'][0]},{r['center'][1]})   "
          f"yaw={r['params']['yaw']}° 方位={r['params']['cam_az']}° "
          f"距离={r['params']['dist']:.1f}")
    for i, s in enumerate(r['issues'], 1):
        print(f'  ⚠️  [{i}] {s}')
    return 0


def _ai_skin_path(value):
    """steve / alex / 用户自己的 64x64 皮肤路径。"""
    key = (value or 'steve').strip()
    low = key.lower()
    if low in ('', 'steve', 'default', '史蒂夫'):
        return os.path.join(HERE, '..', 'assets', 'skins', 'steve.png')
    if low in ('alex', 'slim', '艾利克斯'):
        return os.path.join(HERE, '..', 'assets', 'skins', 'alex.png')
    return os.path.abspath(os.path.expanduser(key))


def _ai_rig_path(skin_key='steve', out_dir=None):
    """AI 人偶 rig 的缓存路径；优先放在输出目录，DSH_AI_RIG 可覆盖。"""
    env = os.environ.get('DSH_AI_RIG')
    if env:
        return os.path.abspath(env)
    data = os.environ.get('DSH_MC_DATA') or os.path.join(
        os.path.expanduser('~'), '.dsh', 'blender-mc')
    key = (skin_key or 'steve').strip().lower()
    if key in ('alex', 'slim', '艾利克斯'):
        key = 'alex'
    elif key in ('', 'steve', 'default', '史蒂夫'):
        key = 'steve'
    else:
        key = 'custom'
    if out_dir:
        return os.path.join(os.path.abspath(out_dir),
                            f'AI_Character_Rig_v0.3_{key}.blend')
    return os.path.join(data, 'ai_rig', f'AI_Character_Rig_v0.3_{key}.blend')


def cmd_ai_char(a):
    """提示词 → CharacterSpec → 原创 AI 人偶渲染（M1，实验性）。"""
    import json
    import subprocess
    from char_control import compile_prompt, validate_spec, summarize, normalize_camera
    from pipeline import find_blender

    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    prompt = a.prompt
    if a.action and a.action.lower() not in prompt.lower():
        prompt = prompt + ' ' + a.action
    spec = compile_prompt(prompt)
    if a.format:
        spec['camera']['framing'] = a.format
    normalize_camera(spec)
    errors = validate_spec(spec)
    if errors:
        print('❌ spec 不合法:')
        for e in errors:
            print('  - ' + e)
        return 1
    spec_path = os.path.join(out, 'spec.json')
    with open(spec_path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print(summarize(spec))
    print('spec:', spec_path)
    if a.spec_only:
        return 0

    blender = find_blender(a.blender)
    skin_key = getattr(a, 'skin', None) or 'steve'
    skin_path = _ai_skin_path(skin_key)
    if not os.path.exists(skin_path):
        print('❌ 找不到皮肤文件:', skin_path)
        return 1
    rig = os.path.abspath(a.rig) if a.rig else _ai_rig_path(skin_key, out)
    if getattr(a, 'skin3d', False):
        os.environ['DSH_AI_SKIN3D'] = '1'
        if not a.rig:
            rig = rig[:-6] + '_3d.blend' if rig.endswith('.blend') else rig + '_3d.blend'
    script = os.path.join(HERE, 'blender', 'ai_rig.py')
    if a.rebuild or not os.path.exists(rig):
        os.makedirs(os.path.dirname(rig), exist_ok=True)
        print('[1/2] 构建原创 AI 人偶 rig…')
        cmd = [blender, '-b', '--factory-startup', '--python', script,
               '--', '--build', rig, '--res', a.res, '--skin', skin_path]
        if getattr(a, 'skin3d', False):
            cmd.append('--skin3d')
        log_path = os.path.join(out, 'build.log')
        with open(log_path, 'w', encoding='utf-8') as log:
            p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
        if p.returncode != 0 or not os.path.exists(rig):
            print('❌ 构建失败，日志:', log_path)
            print(open(log_path, encoding='utf-8', errors='replace').read()[-2000:])
            return 1
    else:
        print('[1/2] 复用 rig:', rig)

    png = os.path.join(out, '人偶.png')
    print('[2/2] 按 spec 渲染…')
    cmd = [blender, '-b', '--factory-startup', '--python', script,
           '--', '--rig', rig, '--spec', spec_path,
           '--out', png, '--samples', str(a.samples)]
    log_path = os.path.join(out, 'render.log')
    with open(log_path, 'w', encoding='utf-8') as log:
        p = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
    if p.returncode != 0 or not os.path.exists(png):
        print('❌ 渲染失败，日志:', log_path)
        print(open(log_path, encoding='utf-8', errors='replace').read()[-2000:])
        return 1

    result = {'prompt': a.prompt, 'spec': spec_path, 'rig': rig,
              'skin': skin_path, 'png': png, 'action': spec.get('action'),
              'camera': spec.get('camera', {}).get('framing')}
    if a.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print('\\n' + '=' * 66)
        print('人偶:', png)
        print('spec:', spec_path)
        print('rig: ', rig)
        print('皮肤:', result['skin'])
        print('动作:', result['action'], ' 画幅:', result['camera'])
    return 0


def cmd_map_import(a):
    """加载存档 → 可编辑的 Blender 背景地图（原创实现）。"""
    import json
    from pipeline import import_map, find_blender
    save = getattr(a, 'save', None)
    if getattr(a, 'map', None):
        import maplib
        try:
            save, _meta = maplib.resolve_world(a.map)
        except Exception as e:
            print(f'❌ 地图库解析失败: {e}')
            return 1
    if not save or not _need_save(save):
        return 1
    y0, y1 = a.y
    try:
        r = import_map(save, a.area, a.out, center=a.center, radius=a.radius,
                       ymin=y0, ymax=y1, name=a.name, res=a.res,
                       samples=a.samples, preview=not a.no_preview,
                       blender=a.blender, force=a.force, progress=print)
    except Exception as e:
        print(f'❌ 背景地图生成失败: {type(e).__name__}: {e}')
        return 1
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
    else:
        print('\n' + '=' * 66)
        print(f"背景地图: {r['blend']}")
        if r.get('preview'):
            print(f"预览:     {r['preview']}")
        print(f"范围:     {r['area']}  中心 {r['center']}  半径 {r['radius']}")
        print(f"Y:        {r['ymin']}..{r['ymax']}")
        print('集合:     MAP / LIGHTS / CAMERA')
    if a.open:
        try:
            import subprocess
            subprocess.Popen([find_blender(a.blender), r['blend']])
        except Exception as e:
            print(f'（自动打开失败，手动打开上面的 .blend 即可: {e}）')
    return 0


def cmd_anim(a):
    from pipeline import animate, find_blender
    if not os.path.isfile(a.blend):
        print('❌ 找不到 .blend 工程: %s' % a.blend)
        print('   先把封面渲出来（shoot），工程就在输出目录里: <out>/尝试1/封面_世界.blend')
        return 1
    b = find_blender(a.blender)
    preset = getattr(a, 'preset', 'killaura')
    frames, fps = a.frames, a.fps
    if getattr(a, 'prompt', None):
        from prompt_film import compile_anim
        spec = compile_anim(a.prompt, fps=fps)
        preset, frames, fps = spec['preset'], spec['total'], spec['fps']
        os.environ['DSH_ANIM_RES'] = spec['res']
        os.environ['DSH_ANIM_SAMPLES'] = str(spec['samples'])
        os.environ.update(spec.get('env') or {})
        _res_key = {'yujian': ('DSH_YJ_RES', 'DSH_YJ_SAMPLES'),
                    'aura': ('DSH_AURA_RES', 'DSH_AURA_SAMPLES'),
                    'killaura': ('DSH_KA_RES', 'DSH_KA_SAMPLES')}.get(preset)
        if _res_key:
            os.environ[_res_key[0]] = spec['res']
            os.environ[_res_key[1]] = str(spec['samples'])
        print('提示词 → 预设 %s / %d 帧 / %d fps / %s'
              % (preset, frames, fps, spec['res']))
        for w in spec.get('warnings') or []:
            print('⚠️  ' + w)
    r = animate(b, a.blend, a.out, total=frames, fps=fps,
                kind=preset,
                preview=a.preview, progress=print)
    print(f"\n动画工程: {r['blend']}")
    if r.get('frames'):
        print(f"帧序列: {r['frames']} 帧 @ {r['fps']}fps → {r['dir']}")
        print('\n转视频:')
        print(f'  ffmpeg -framerate {r["fps"]} -i "{r["dir"]}\\frame_%04d.png" '
              f'-c:v libx264 -crf 18 -pix_fmt yuv420p -movflags +faststart out.mp4')
    return 0


def cmd_film(a):
    """分镜表 → 多镜头 → 成片（逻辑在 film.py）"""
    import json
    from pipeline import find_blender
    import film as film_mod

    if getattr(a, 'prompt', None):
        if not a.blend:
            print('❌ 用 --prompt 时要同时给 --blend <封面工程.blend>')
            return 1
        from prompt_film import compile_prompt
        plan = compile_prompt(a.prompt, blend=a.blend)
        print('提示词 → preset=%s，%d 帧 / %.1f 秒 / %s'
              % (plan['preset'], plan['total'], plan['total'] / plan['fps'], plan['res']))
        for w in plan.get('warnings') or []:
            print('⚠️  ' + w)
    elif a.plan:
        plan = film_mod.load_plan(a.plan)
    elif a.plan_json:
        plan = film_mod.load_plan(a.plan_json)
    else:
        print('❌ 要给 --prompt + --blend，或 --plan / --plan-json')
        return 1
    if a.blend:
        plan['blend'] = a.blend
    b = find_blender(a.blender)
    r = film_mod.film(b, plan, a.out, animatic=a.animatic, progress=print)
    print()
    print(json.dumps({k: v for k, v in r.items() if k != 'shots'},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_check(a):
    import json
    from vision_check import check_image
    bad = 0
    for p in a.images:
        r = check_image(p)
        if not r.get('ok', True):
            print('❌ %s' % r.get('error'))
            bad += 1
            continue
        if a.json:
            print(json.dumps({'path': p, **r}, ensure_ascii=False, indent=2))
        else:
            print(f"\n{'='*66}\n{p}\n{'='*66}")
            print(f"  {r.get('verdict')}  {r.get('score')} 分")
            for i, s in enumerate(r.get('issues', []), 1):
                print(f'  ⚠️  [{i}] {s}')
    return 1 if bad else 0



def cmd_mobs(a):
    """MCprep 生物 / 玩家 rig 库（课程 T52 的生物生成器，无界面版）。"""
    import mobs
    if a.action in ('list', 'find'):
        return mobs.main([a.action] + ([a.query] if a.query else []))
    if a.action == 'spawn':
        exe = a.blender or _default_blender()
        if not exe or not os.path.exists(exe):
            print('❌ 找不到 Blender：请用 --blender 指定路径，或把 blender 加进 PATH')
            return 2
        ok, log = mobs.spawn(a.query, a.blend, a.out, exe,
                             pos=[float(v) for v in a.pos.split(',')],
                             scale=float(a.scale))
        for ln in [x for x in log.splitlines() if x.startswith('[mobs]')]:
            print(ln)
        if not ok:
            print('生成失败，日志尾部：')
            print('\n'.join(log.splitlines()[-12:]))
            return 1
        return 0
    print('未知动作：%s（list / find / spawn）' % a.action)
    return 2


def _default_blender():
    """requirements 里的查找函数叫 _find_blender（没有公开别名）。

    以前这里写的是 requirements.find_blender()，AttributeError 被 except 吞成
    'blender'，而 'blender' 通常不在 PATH → spawn 时报 WinError 2。返回 None
    让调用方给出人能看懂的提示。
    """
    import requirements
    try:
        return requirements._find_blender()
    except Exception:
        return None


def cmd_runs(a):
    """任务档案：list / show / replay / dir。

    replay 的意义：以前想复现某张封面，只能翻聊天记录找当时敲的参数；
    现在 `runs replay <id>` 直接拿档案里存的 kwargs 再跑一遍。
    """
    import runs
    act = a.action
    if act == 'dir':
        print(runs.runs_dir())
        return 0
    if act == 'list':
        rows = runs.listing(limit=a.limit)
        if not rows:
            print('（还没有档案）')
            return 0
        print(f'{"id":28} {"分数":>4}  {"结论":6} 目标')
        for r in rows:
            sc = r.get('score')
            print(f'{r["id"]:28} {("" if sc is None else sc):>4}  '
                  f'{str(r.get("verdict") or "-")[:10]:10} '
                  f'{(r.get("goal") or "")[:40]}')
        print(f'\n档案目录：{runs.runs_dir()}')
        return 0
    rid = a.rid
    if not rid:
        print('❌ 要给档案 id（先 runs list 看）')
        return 1
    rec = runs.load(rid)
    if act == 'show':
        print(json.dumps(rec, ensure_ascii=False, indent=2))
        return 0
    # ---- replay ----
    rp = rec.get('replay') or {}
    kind = rp.get('kind') or rec.get('kind')
    print(f'重放 {rec["id"]}（{kind}）')
    if kind == 'shoot':
        from pipeline import shoot
        out = rp.get('out') or os.path.join(os.getcwd(), 'replay')
        return 0 if shoot(rp['save'], rp.get('area'), out,
                          blender=a.blender, params=rp.get('params'),
                          center=rp.get('center'), radius=rp.get('radius'),
                          progress=print) else 1
    if kind == 'anim':
        from pipeline import animate, find_blender
        b = find_blender(a.blender)
        animate(b, rp['blend'], rp.get('out'), total=int(rp.get('frames') or 240),
                fps=float(rp.get('fps') or 24), kind=rp.get('preset') or 'aura',
                preview=None if not rp.get('preview') else '1')
        return 0
    if kind == 'film':
        import film as film_mod
        from pipeline import find_blender
        b = find_blender(a.blender)
        plan = dict(rp.get('plan') or {})
        plan['_dir'] = rp.get('plan_dir') or os.getcwd()
        film_mod.film(b, plan, rp.get('out') or os.path.join(os.getcwd(), 'film'),
                      animatic=bool(rp.get('animatic')))
        return 0
    print(f'❌ 不认识的档案类型：{kind}（这份档案可能来自更老的版本）')
    return 1


def cmd_map(a):
    """背景地图库：上传的存档 zip → 解压识别入库（逻辑全在 maplib.py）"""
    import maplib
    return maplib.main(a.rest)


def _hoist_blender(argv):
    """把任意位置的 `--blender X` / `--blender=X` 挪到最前面。

    ⚠️ 坑 83：--blender 定义在顶层 parser 上，写在子命令后面会被 argparse 当
    未知参数拒掉（`doctor --blender X` → exit 2）。人就是会这么写，所以先提。
    """
    rest, value, i = [], None, 0
    while i < len(argv):
        tok = argv[i]
        if tok == '--blender':
            if i + 1 < len(argv):
                value = argv[i + 1]
                i += 2
                continue
            rest.append(tok)
        elif tok.startswith('--blender='):
            value = tok.split('=', 1)[1]
        else:
            rest.append(tok)
        i += 1
    return (['--blender', value] + rest) if value else rest


def main():
    ap = argparse.ArgumentParser(
        prog='dsh-blender-mc',
        description='Minecraft 存档 → Blender 封面 / 动画（含自动审图闭环）')
    ap.add_argument('--blender', default=None, help='blender.exe 路径')
    sub = ap.add_subparsers(dest='cmd', required=True)

    d = sub.add_parser('doctor', help='自检')
    d.set_defaults(func=cmd_doctor)

    # 便宜版自检：只读目录/环境变量，结果落盘给插件在会话里主动提示用。
    # ⚠️ 和 doctor 的分工要记清：doctor 是「人想知道到底哪坏了」，会给完整指引、
    #   会花 5-10 秒问 Blender；readiness 是「插件需要一句话知道缺什么」，
    #   必须在后台几十毫秒内跑完，绝不能反过来调 doctor。
    rd = sub.add_parser('readiness', help='廉价的运行条件快照（写缓存，供插件提示用）')
    rd.add_argument('--deep', action='store_true', help='额外问一次 Blender（5-10 秒）')
    rd.add_argument('--json', action='store_true', help='输出完整 json')
    rd.add_argument('--no-write', action='store_true', help='只算不写缓存')
    rd.set_defaults(func=lambda a: _cmd_readiness(a))

    st = sub.add_parser('setup', help='装配外部资产（从本地资源目录搬到 vendor/）')
    st.add_argument('sources', nargs='*', help='资源目录，如 pack/')
    st.add_argument('--dry-run', action='store_true', help='只看会搬什么，不实际搬')
    st.add_argument('--link', action='store_true', help='用硬链接代替复制')
    st.set_defaults(func=cmd_setup)

    e = sub.add_parser('extract', help='提取 Bedrock 模型（.geo.json + 贴图）→ OBJ')
    e.add_argument('assets', help='资源包 / 模组解包目录（会递归找 *.geo.json）')
    e.add_argument('--out', required=True, help='输出目录')
    e.add_argument('--filter', default=None, help='只转路径里含这个关键词的模型')
    e.add_argument('--anim', default=None, help='animation.json（可选，套一段静态姿势）')
    e.add_argument('--anim-name', default=None, help='要套用的动画名，缺省取第一条')
    e.add_argument('--list', action='store_true', help='只列出会转什么，不动文件')
    e.add_argument('--json', default=None, help='把清单另存为 json')
    e.set_defaults(func=cmd_extract)

    s = sub.add_parser('scan', help='在存档里找机位')
    s.add_argument('save')
    s.add_argument('--area', type=_area, required=True)
    s.add_argument('--y', type=_pair_int, default=(50, 200))
    s.add_argument('--radius', type=_posint, default=15)
    s.add_argument('--top', type=_posint, default=12)
    s.add_argument('--json', default=None)
    s.set_defaults(func=cmd_scan)

    cp = sub.add_parser('camera-path', help='A*/Theta*/Catmull-Rom 相机路径')
    cp.add_argument('save')
    cp.add_argument('--area', type=_area, required=True)
    cp.add_argument('--start', type=_pair_int, required=True, help='x,z')
    cp.add_argument('--goal', type=_pair_int, required=True, help='x,z')
    cp.add_argument('--y', type=_pair_int, default=(50, 200))
    cp.add_argument('--height', type=float, default=2.5)
    cp.add_argument('--samples', type=int, default=8)
    cp.add_argument('--out', default=None)
    cp.set_defaults(func=cmd_camera_path)

    ss = sub.add_parser('shoot-search', help='渲染反馈搜索机位（贝叶斯）')
    ss.add_argument('save')
    ss.add_argument('--area', type=_area, required=True)
    ss.add_argument('--out', required=True)
    ss.add_argument('--center', type=_pair_int, default=None)
    ss.add_argument('--top', type=int, default=3, help='没给 center 时，取扫描前 N 个机位做基准')
    ss.add_argument('--radius', type=int, default=15, help='扫描平坦半径')
    ss.add_argument('--skin', default=None)
    ss.add_argument('--pose', default=None)
    ss.add_argument('--hour', default=None)
    ss.add_argument('--face', default=None)
    ss.add_argument('--trees', type=int, default=None)
    ss.add_argument('--item-right', default=None)
    ss.add_argument('--armor', default=None)
    ss.add_argument('--n-init', type=int, default=6)
    ss.add_argument('--iters', type=int, default=4)
    ss.add_argument('--seed', type=int, default=11)
    ss.set_defaults(func=cmd_shoot_search)

    h = sub.add_parser('shoot', help='出封面')
    h.add_argument('save')
    h.add_argument('--area', type=_area, required=True)
    h.add_argument('--out', required=True)
    h.add_argument('--center', type=_pair_int, default=None, help='x,z —— 不给就自动找')
    h.add_argument('--radius', type=_posint, default=44)
    h.add_argument('--yaw', type=float, default=None)
    h.add_argument('--cam-az', type=float, default=None)
    h.add_argument('--dist', type=float, default=None)
    h.add_argument('--sky', type=float, default=None)
    h.add_argument('--sink', type=float, default=None)
    h.add_argument('--res', type=_res, default=None, help='输出尺寸，如 1920x1080（默认 1280x720）')
    h.add_argument('--samples', type=_posint, default=None,
                   help='正式档 EEVEE 采样数，默认 48（试渲档固定 16；调高更干净、更慢）')
    h.add_argument('--vol-samples', dest='vol_samples', type=_posint, default=None,
                   help='体积雾采样，默认 32（96 是渲染耗时大头）')
    h.add_argument('--view-transform', default=None)
    # ★ P0-2 干净包：背景三档 + 轮廓光单独倍率
    h.add_argument('--backdrop', default=None,
                   choices=['world', 'clean', 'studio'],
                   help='背景：world=用导出的地形（默认，可能带切面土墙/建筑残骸）/ '
                        'clean=隐藏地形换平铺草地（背景干净）/ '
                        'studio=中性灰地面（棚拍/抠图）')
    h.add_argument('--rim-scale', dest='rim_scale', type=float, default=None,
                   help='轮廓光倍率（单独加 Rim 灯，默认跟 light_scale 一致）')
    h.add_argument('--goal', default=None,
                   help='这一趟想干什么（写进任务档案，方便以后翻；例：'
                        '「修仙封面 A 版，鸟居+三剑」）')
    h.add_argument('--skin', default=None,
                   help='人物皮肤：绝对路径，或皮肤库里的名字（<DSH_HOME>/blender-mc/skins/）')
    h.add_argument('--attempts', type=_posint, default=2,
                   help='迭代轮数上限（试渲档），默认 2；最后总会再出一张正式档定稿')
    h.add_argument('--target', type=int, default=85)
    # ── v0.4.9 新参数：全部可选，不给 = 与 v0.4.6 完全一致 ────────────────
    #    名字必须与 docs/NEW_PARAMS.md 第三节一致：lib/index.js 是按
    #    key.replaceAll('_','-') 拼这些 flag 的，写错一个就静默不生效。
    h.add_argument('--item-right', dest='item_right', default=None, metavar='TEXT',
                   help='右手物品：物品库里的名字（如 "Sword: Diamond"）、'
                        'MC 方块/物品 id（如 anvil）、或空字符串=空手')
    h.add_argument('--item-left', dest='item_left', default=None, metavar='TEXT',
                   help='左手物品（同上）')
    h.add_argument('--items', default=None, metavar='TEXT',
                   help='简写 "R=anvil,L=crafting_table"；给了就覆盖 --item-right/--item-left')
    h.add_argument('--armor-color', default=None, metavar='HEX',
                   help='盔甲染色：#RRGGBB（也可以直接写在 --armor 里，如 leather#8B4513）')
    h.add_argument('--armor', default=None, metavar='TEXT',
                   help='盔甲：材质名 leather|iron|gold|diamond|netherite，'
                        '或 helmet=Netherite,chest=Gold；none=不穿')
    h.add_argument('--trees', type=_nonneg_int, default=None,
                   help='身后树林数量（0=不要树）')
    h.add_argument('--tree-dist', dest='tree_dist', type=_pair_float, default=None,
                   metavar='MIN,MAX', help='树的距离区间，默认 7,20')
    h.add_argument('--tree-h', dest='tree_h', type=_pair_float, default=None,
                   metavar='MIN,MAX', help='树的高度区间，默认 4,7')
    h.add_argument('--hour', type=_hour, default=None,
                   help='时刻 0-24，或 morning|noon|dusk|night（默认 8）')
    h.add_argument('--fog', type=_float_in(0, 100), default=None,
                   help='雾浓度倍率（0=关雾；不给=保持旧行为）')
    h.add_argument('--clouds', type=_switch, default=None,
                   help='云 1/0（0=隐藏云层/雾体积/方块云）')
    h.add_argument('--face', default=None,
                   help='面部表情：auto（默认，仅 snapshot 姿势笑）| happy | angry | sad | '
                        'surprised | curious | neutral | blink（眨眼）| 0（关）')
    h.add_argument('--pose', default=None,
                   help='姿势：action（默认）| hero | raise | guard | snapshot（照相取景框）| '
                        'run | jump | point | wave | sit | legacy')
    h.add_argument('--skin3d', action='store_true', default=False,
                   help='3D 皮肤外层：把头发/帽子/衣服的不透明像素挤出厚度')
    h.add_argument('--skin3d-thick', dest='skin3d_thick', type=float, default=None,
                   metavar='T', help='3D 皮肤厚度（格），默认 0.02')
    h.set_defaults(func=cmd_shoot)

    n = sub.add_parser('anim', help='出动画（10 秒杀戮光环）')
    n.add_argument('blend', help='封面工程 .blend')
    n.add_argument('--out', required=True)
    n.add_argument('--frames', type=_posint, default=240)
    n.add_argument('--fps', type=_posint, default=24)
    n.add_argument('--preview', default=None, help='只渲这些帧，如 1,60,120')
    n.add_argument('--preset', default='killaura',
                   help='预设名：killaura=进圈就死（默认）；aura=挥砍+装饰光环；'
                        'yujian=御剑飞天；也可以是自己放进 '
                        'py/blender/presets/ 的任意 <名字>.json（写配方即出新动画，不用改代码）')
    n.add_argument('--prompt', default=None,
                   help='自然语言提示词；给了就按提示词选预设，覆盖 --preset')
    n.set_defaults(func=cmd_anim)

    f = sub.add_parser('film', help='分镜表 → 多镜头 → 剪成一支成片（含分镜表.md）')
    f.add_argument('--plan', default=None, help='分镜表 .json 路径')
    f.add_argument('--plan-json', default=None, help='直接给分镜表 JSON 字符串')
    f.add_argument('--prompt', default=None,
                   help='自然语言提示词；给了就自动编译成多镜头分镜（需配 --blend）')
    f.add_argument('--blend', default=None, help='覆盖分镜表里的封面工程路径')
    f.add_argument('--out', required=True)
    f.add_argument('--animatic', action='store_true',
                   help='低清样片（480x270 / 8 samples），只看节奏')
    f.set_defaults(func=cmd_film)

    c = sub.add_parser('check', help='只审图')
    c.add_argument('images', nargs='+')
    c.add_argument('--json', action='store_true')
    c.set_defaults(func=cmd_check)

    mp = sub.add_parser('map', help='背景地图库（存档 zip 入库 / 列表 / 改名 / 删除）')
    mp.add_argument('rest', nargs=argparse.REMAINDER,
                    help='import|list|get|resolve|rename|delete|default|doctor + 各自参数')
    mp.set_defaults(func=cmd_map)

    mi = sub.add_parser('map-import',
                        help='加载存档 → 可编辑的 Blender 背景地图（MAP/LIGHTS/CAMERA）')
    g = mi.add_mutually_exclusive_group(required=True)
    g.add_argument('--save', help='存档目录（含 region/）')
    g.add_argument('--map', help='背景地图库里的名字或 id')
    mi.add_argument('--area', type=_area, default=None, help='x0,x1,z0,z1（不给就按 region 自动算）')
    mi.add_argument('--center', type=_pair_int, default=None, help='x,z（默认区域中心）')
    mi.add_argument('--radius', type=_posint, default=96, help='导出半径（默认 96）')
    mi.add_argument('--y', type=_pair_int, default=(0, 320), help='Y 范围 y0,y1（默认 0,320）')
    mi.add_argument('--out', required=True, help='输出目录')
    mi.add_argument('--name', default='背景_世界', help='.blend 名（默认 背景_世界）')
    mi.add_argument('--res', type=_res, default='1280x720', help='预览尺寸')
    mi.add_argument('--samples', type=_posint, default=64, help='预览采样（默认 64）')
    mi.add_argument('--no-preview', action='store_true', help='不渲染预览图')
    mi.add_argument('--open', action='store_true', help='完成后用 Blender 打开 .blend')
    mi.add_argument('--force', action='store_true', help='忽略导出缓存')
    mi.add_argument('--json', action='store_true', help='输出完整 json')
    mi.set_defaults(func=cmd_map_import)

    ac = sub.add_parser('ai-char',
                        help='提示词 → AI 人偶 CharacterSpec → 原创 rig 渲染（实验性 M1）')
    ac.add_argument('--prompt', required=True, help='自然语言提示词（中文/英文）')
    ac.add_argument('--out', required=True, help='输出目录（spec.json + 人偶.png）')
    ac.add_argument('--format', default=None,
                    help='覆盖画幅：9:16 / 16:9 / 1:1 / 4:5')
    ac.add_argument('--action', default=None,
                    help='覆盖动作：sit/run/jump/wave/point/stand')
    ac.add_argument('--rig', default=None, help='现有 AI rig .blend（默认数据目录缓存）')
    ac.add_argument('--skin', default='steve', help='steve / alex / 64x64 皮肤 PNG 路径')
    ac.add_argument('--skin3d', action='store_true',
                    help='叠加 3D 皮肤外层（帽子/头发、外套、裤腿 overlay）')
    ac.add_argument('--rebuild', action='store_true', help='强制重建 rig')
    ac.add_argument('--res', default='720x1280', help='构建 rig 时的参考画幅')
    ac.add_argument('--samples', type=_posint, default=16, help='渲染采样（默认 16）')
    ac.add_argument('--spec-only', action='store_true', help='只编译 spec，不渲染')
    ac.add_argument('--json', action='store_true', help='输出完整 json')
    ac.set_defaults(func=cmd_ai_char)

    mo = sub.add_parser('mobs', help='MCprep 生物 rig 库：列出来 / 搜 / 生成进工程')
    mo.add_argument('action', choices=['list', 'find', 'spawn'])
    mo.add_argument('query', nargs='?', default='', help='find 的关键词 / spawn 的生物名')
    mo.add_argument('--blend', default=None, help='spawn：目标工程 .blend')
    mo.add_argument('--out', default=None, help='spawn：另存的 .blend（默认覆盖 blend）')
    mo.add_argument('--pos', default='0,0,0', help='spawn：摆放位置 x,y,z（默认 0,0,0）')
    mo.add_argument('--scale', default='1.0', help='spawn：缩放（默认 1.0）')
    mo.set_defaults(func=cmd_mobs)

    rp = sub.add_parser('runs', help='任务档案：列出 / 查看 / 重放')
    rp.add_argument('action', choices=['list', 'show', 'replay', 'dir'])
    rp.add_argument('rid', nargs='?', default='', help='show/replay 的档案 id')
    rp.add_argument('--limit', type=int, default=20, help='list 显示几条')
    rp.add_argument('--blender', default=None, help='replay 用哪个 Blender')
    rp.set_defaults(func=cmd_runs)


    a = ap.parse_args(_hoist_blender(sys.argv[1:]))
    try:
        return a.func(a)
    except FileNotFoundError as e:
        print('❌ %s' % e)
        return 1
    except PermissionError as e:
        print('❌ 没有权限访问: %s' % e)
        return 1


if __name__ == '__main__':
    sys.exit(main())
