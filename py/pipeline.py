# -*- coding: utf-8 -*-
"""pipeline —— 把"读地图 → 找机位 → 导出 → Blender 渲染 → 审图 → 调参重渲"串起来

用户只需要提供两样东西：
    ① Minecraft 存档目录
    ② Blender 可执行文件路径

其余全部由本模块负责。**审图闭环**是它和"手动调参"的本质差别：
渲染完先跑 vision_check，不合格就按问题类型自动改参数重渲。
"""
import json
import os
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
BLENDER_DIR = os.path.join(HERE, 'blender')
sys.path.insert(0, HERE)

from vision_check import check_image          # noqa: E402

# ────────────────────────────────────────────────────────────
# 默认参数 —— 每一项都是实测调出来的，注释写明为什么
# ────────────────────────────────────────────────────────────
DEFAULTS = {
    'yaw': 25.0,           # 角色朝向。相机在 -Y 方向 → yaw≈25° 时是 3/4 侧脸面向镜头
    'lens': 55.0,          # 焦距。T48 封面建议 80mm，但配合推近会太紧，55 折中
    'view_transform': 'Standard',
    # ⚠️ 坑 53/57：「标准」没有高光压缩。物理天空是辐射度量，
    #    强度一高就削顶成白、动态范围被天空占满、三点布光完全显不出来。
    #    所以配套要求：天空强度 ≤0.05，灯光相应抬高（见 light_scale）。
    'sky_strength': 0.03,
    'light_scale': 1.0,    # 相对基准 320/140/420 的倍率
    'cam_az': 200.0,       # 视线方位角（MC 角度）
    'dist': 11.2,          # 相机距离
    'sink': 0.06,          # ★ 脚陷入地面的深度。精确贴面时间隙 0.019，
                           #   MC 方块边缘锐利，这点缝会读成"悬浮"
    'res': '1280x720',
    'samples': 256,
}


def run(cmd, log=None, timeout=None, cwd=None):
    """跑外部命令。返回 (rc, stdout+stderr)"""
    p = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=timeout, cwd=cwd)
    out = (p.stdout or '') + (p.stderr or '')
    if log:
        with open(log, 'a', encoding='utf-8') as f:
            f.write('\n$ ' + ' '.join(str(c) for c in cmd) + '\n')
            f.write(out)
    return p.returncode, out


def resolve_assets(config=None):
    """找出骨架 / 物品库 / 贴图 / ffmpeg 的实际路径。

    优先级：显式配置 > vendor/manifest.json（setup 装配时写的）> 自动扫描
    这样"配置留空"和"刚跑完 setup"都能用。
    """
    cfg = dict(config or {})
    try:
        from setup_assets import load_manifest, default_sources, scan, choose, WANTED
        man = load_manifest().get('assets', {})
    except Exception:
        man, WANTED = {}, {}

    out = {}
    for kind, keys in (('rig', ('rig', 'rig_path')),
                       ('item', ('item_rig', 'item')),
                       ('textures', ('textures', 'textures_dir')),
                       ('ffmpeg', ('ffmpeg', 'ffmpeg_path'))):
        v = None
        for k in keys:
            if cfg.get(k):
                v = cfg[k]
                break
        if not v and kind in man:
            v = man[kind].get('dst') or man[kind].get('src')
        out[kind] = v

    # manifest 也没有 → 现场扫一遍（只扫默认位置，不递归太深）
    missing = [k for k in ('rig', 'item') if not out.get(k)]
    if missing and WANTED:
        try:
            found = scan(default_sources(), max_depth=4)
            for k in missing:
                out[k] = choose(found.get(k, []), WANTED[k]['prefer'],
                                WANTED[k]['avoid'])
        except Exception:
            pass
    return out


def find_blender(explicit=None):
    """找 Blender 可执行文件"""
    if explicit and os.path.isfile(explicit):
        return explicit
    if explicit:
        raise FileNotFoundError(f'指定的 Blender 不存在: {explicit}')
    cands = []
    for env in ('BLENDER', 'BLENDER_EXE'):
        if os.environ.get(env):
            cands.append(os.environ[env])
    cands += [
        r'C:\Program Files\Blender Foundation\Blender 5.2\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.5\blender.exe',
        r'C:\Program Files\Blender Foundation\Blender 4.2\blender.exe',
    ]
    # Steam 常见位置
    for base in (r'D:\SteamLibrary\steamapps\common\Blender',
                 r'C:\Program Files (x86)\Steam\steamapps\common\Blender',
                 r'E:\SteamLibrary\steamapps\common\Blender'):
        cands.append(os.path.join(base, 'blender.exe'))
    for c in cands:
        if c and os.path.isfile(c):
            return c
    # PATH 里找
    w = shutil.which('blender')
    if w:
        return w
    raise FileNotFoundError(
        '找不到 Blender。请用 --blender 指定，或设环境变量 BLENDER。\n'
        '  下载: https://www.blender.org/download/')


def step_scan(save, area, y=(50, 200), radius=15, az_step=30, top_n=12,
              out=None, progress=print):
    """① 在存档里找机位"""
    from scan import pick_from_save
    x0, x1, z0, z1 = (int(v) for v in area)
    if isinstance(y, str):
        y = tuple(int(v) for v in y.split(','))
    y = (int(y[0]), int(y[1]))
    w, top, shots = pick_from_save(save, x0, x1, z0, z1, y0=y[0], y1=y[1],
                                   radius=radius, az_step=az_step, top_n=top_n,
                                   progress=progress)
    if out:
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(shots, f, ensure_ascii=False, indent=2)
    return shots


def step_export(save, area, center, out_dir, blender=None, radius=44,
                ymin=56, ymax=90, horizon=150, progress=print):
    """② 导出 OBJ（这一步只用到 Python，不需要 Blender）"""
    cmd = [sys.executable, os.path.join(BLENDER_DIR, 'export_obj.py'),
           save, out_dir,
           '--radius', str(radius), '--ymin', str(ymin), '--ymax', str(ymax),
           '--horizon', str(horizon),
           '--center', f'{center[0]},{center[1]}']
    rc, out = run(cmd, log=os.path.join(out_dir, '_export.log'))
    obj = os.path.join(out_dir, '世界.obj')
    if rc != 0 or not os.path.isfile(obj):
        raise RuntimeError(f'导出失败\n{out[-2000:]}')
    progress(f'    导出完成: {obj}')
    return obj


def step_render(blender_exe, obj, out_dir, params, progress=print):
    """③ 用 Blender 搭场景并渲染"""
    script = os.path.join(BLENDER_DIR, 'build_cover.py')
    args = [blender_exe, '-b', '--factory-startup', '--python', script, '--',
            obj, out_dir,
            str(params['yaw']), str(params['lens']), params['view_transform'],
            str(params['sky_strength']), str(params['cam_az']), str(params['dist']),
            str(params['sink'])]
    t0 = time.time()
    rc, out = run(args, log=os.path.join(out_dir, '_build.log'))
    if rc != 0:
        raise RuntimeError(f'搭场景失败\n{out[-2000:]}')
    blend = os.path.join(out_dir, '封面_世界.blend')
    if not os.path.isfile(blend):
        raise RuntimeError(f'没生成 .blend\n{out[-2000:]}')
    rc, out = run([blender_exe, '-b', blend, '-f', '1'],
                  log=os.path.join(out_dir, '_render.log'))
    png = os.path.join(out_dir, '封面_世界0001.png')
    if not os.path.isfile(png):
        raise RuntimeError(f'渲染没出图\n{out[-2000:]}')
    progress(f'    渲染完成 {time.time()-t0:.1f}s: {png}')
    return blend, png


def step_check(png, progress=print, annotate=None):
    """④ 审图"""
    r = check_image(png)
    progress(f"    审图: {r['verdict']} {r['score']} 分")
    for i, s in enumerate(r.get('issues', []), 1):
        progress(f"      ⚠️  [{i}] {s}")
    if annotate:
        os.makedirs(annotate, exist_ok=True)
        try:
            import cv2
            from vision_check import imread_u, imwrite_u
            img = imread_u(png)
            b = r['details'].get('subject_box')
            if b:
                cv2.rectangle(img, (b[0], b[1]), (b[2], b[3]), (0, 0, 255), 3)
            col = {'PASS': (0, 200, 0), 'WARN': (0, 180, 255),
                   'FAIL': (0, 0, 255)}.get(r['verdict'], (255, 255, 255))
            cv2.putText(img, f"{r['verdict']} {r['score']}", (16, 52),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.6, col, 3)
            imwrite_u(os.path.join(annotate, os.path.basename(png)), img)
        except Exception as e:
            progress(f'      (标注失败: {e})')
    return r


def auto_adjust(params, report):
    """★ 审图闭环：按问题类型自动改参数。

    这是本插件区别于"手动调参"的地方 —— 渲完先审，不合格就自己改。
    返回 (新参数, 改动说明列表)；无改动时返回 (None, [])
    """
    new = dict(params)
    fixes = []
    issues = ' | '.join(report.get('issues', []))
    det = report.get('details', {})

    # 过曝 → 降天空强度（「标准」视图转换没有高光压缩，天空是主因）
    if '过曝' in issues:
        new['sky_strength'] = max(0.005, params['sky_strength'] * 0.4)
        fixes.append(f"天空强度 {params['sky_strength']} → {new['sky_strength']}（降过曝）")

    # 看不出布光 → 抬灯光倍率（天空占满动态范围时布光显不出来）
    if '看不出布光' in issues:
        new['light_scale'] = min(4.0, params['light_scale'] * 1.8)
        fixes.append(f"灯光倍率 {params['light_scale']} → {new['light_scale']}")

    # 背面 → 转 180°
    if '背面' in issues:
        new['yaw'] = (params['yaw'] + 180.0) % 360.0
        fixes.append(f"角色朝向 {params['yaw']}° → {new['yaw']}°（转正面）")

    # 脚没踩地 → 加深下沉
    if '脚没踩地' in issues:
        new['sink'] = min(0.35, params['sink'] + 0.08)
        fixes.append(f"下沉 {params['sink']} → {new['sink']}（加强接地）")

    # 主体太小 → 拉近
    if '主体太小' in issues:
        new['dist'] = max(4.0, params['dist'] * 0.78)
        fixes.append(f"相机距离 {params['dist']} → {new['dist']:.1f}（拉近）")
    # 主体太满 → 拉远
    if '主体太满' in issues:
        new['dist'] = params['dist'] * 1.25
        fixes.append(f"相机距离 {params['dist']} → {new['dist']:.1f}（拉远）")

    # 头顶被切 → 抬高看向点（用拉远近似）
    if '头顶几乎没有留白' in issues:
        new['dist'] = params['dist'] * 1.15
        fixes.append(f"相机距离 {params['dist']} → {new['dist']:.1f}（给头顶留白）")

    if not fixes:
        return None, []
    return new, fixes


def shoot(save, area, out_root, blender=None, params=None, center=None,
          radius=44, ymin=56, ymax=90, horizon=150,
          max_attempts=3, target_score=85, progress=print):
    """一站式：找机位 → 导出 → 渲染 → 审图 → 不达标自动重渲

    返回 {'shot':…, 'params':…, 'png':…, 'blend':…, 'report':…, 'attempts':[…]}
    """
    os.makedirs(out_root, exist_ok=True)
    p = dict(DEFAULTS)
    p.update(params or {})

    # ⓿ ★ 开跑之前先检查条件 —— 缺东西就在这里拦住并给出安装指引，
    #    而不是渲染到一半报个看不懂的错。
    #    角色相关的资产标"可选"：没有就只出风景空镜，不阻塞。
    try:
        from requirements import require
        require(['python_deps', 'blender', 'mcprep'], blender=blender)
    except RuntimeError:
        raise
    except Exception as e:
        progress(f'（条件检查跳过: {e}）')
    try:
        from requirements import check_all
        st = {k: ok for k, ok, _, _ in check_all(blender)}
        if not st.get('rig'):
            progress('⚠️  没装 BPS 角色骨架 → 本次只出【风景空镜】，画面里不会有角色')
        if not st.get('item'):
            progress('⚠️  没装物品库 → 角色会空手')
        if not st.get('ffmpeg'):
            progress('⚠️  没装 ffmpeg → 动画只能出帧序列，转视频要你自己来')
    except Exception:
        pass

    # ① 没给机位就自己找
    if center is None:
        progress('[1/5] 在存档里找机位…')
        shots = step_scan(save, area, y=(ymin + 8, ymax), radius=15,
                          out=os.path.join(out_root, '机位.json'), progress=progress)
        if not shots:
            raise RuntimeError('这个区域里没找到合适的开阔平地。'
                               '换 --area，或把 --radius 调小。')
        best = shots[0]
        center = (best['x'], best['z'])
        p['cam_az'] = float(best['az'])
        progress(f"    选中 ({best['x']},{best['z']}) Y={best['y']} "
                 f"开阔度{best['openness']} 方位角{best['az']}°")
    else:
        progress(f'[1/5] 用指定机位 {center}')

    # ② 导出
    progress('[2/5] 导出世界几何…')
    obj = step_export(save, area, center, out_root, radius=radius,
                      ymin=ymin, ymax=ymax, horizon=horizon, progress=progress)

    # ③~⑤ 渲染 + 审图 + 自动重渲
    blender_exe = find_blender(blender)
    progress(f'[3/5] Blender: {blender_exe}')
    history = []
    for attempt in range(1, max_attempts + 1):
        sub = os.path.join(out_root, f'尝试{attempt}')
        os.makedirs(sub, exist_ok=True)
        progress(f'[4/5] 第 {attempt} 次渲染（{p["view_transform"]} / '
                 f'天空{p["sky_strength"]} / 灯×{p["light_scale"]} / '
                 f'yaw{p["yaw"]}° / 距离{p["dist"]:.1f} / 下沉{p["sink"]}）')
        blend, png = step_render(blender_exe, obj, sub, p, progress=progress)
        progress('[5/5] 审图…')
        rep = step_check(png, progress=progress,
                         annotate=os.path.join(out_root, '审图'))
        history.append({'attempt': attempt, 'params': dict(p),
                        'png': png, 'blend': blend,
                        'verdict': rep['verdict'], 'score': rep['score'],
                        'issues': rep['issues']})
        if rep['verdict'] == 'PASS' and rep['score'] >= target_score:
            progress(f'    ✅ 达标（{rep["score"]} ≥ {target_score}），停止迭代')
            break
        nxt, fixes = auto_adjust(p, rep)
        if nxt is None:
            progress('    没有可自动修的项，停止迭代')
            break
        if attempt == max_attempts:
            progress(f'    已达最大尝试次数 {max_attempts}')
            break
        for f in fixes:
            progress(f'    🔧 {f}')
        p = nxt

    final = history[-1]
    result = {'center': center, 'params': final['params'], 'png': final['png'],
              'blend': final['blend'], 'verdict': final['verdict'],
              'score': final['score'], 'issues': final['issues'],
              'attempts': history}
    with open(os.path.join(out_root, '结果.json'), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def animate(blender_exe, blend, out_dir, total=240, fps=24, kind='aura',
            preview=None, progress=print):
    """在封面工程上做动画（默认杀戮光环），再导出 MP4/GIF"""
    script = os.path.join(BLENDER_DIR, 'animate_aura.py' if kind == 'aura'
                          else 'animate_aura.py')
    os.makedirs(out_dir, exist_ok=True)
    args = [blender_exe, '-b', blend, '--python', script, '--',
            out_dir, str(total), str(fps), preview or '']
    rc, out = run(args, log=os.path.join(out_dir, '_anim.log'))
    if rc != 0:
        raise RuntimeError(f'建动画失败\n{out[-2000:]}')
    ablend = os.path.join(out_dir, '杀戮光环.blend')
    if preview:
        return {'blend': ablend, 'frames': None, 'preview': True}
    rc, out = run([blender_exe, '-b', ablend, '-a'],
                  log=os.path.join(out_dir, '_render.log'))
    n = len([f for f in os.listdir(out_dir) if f.startswith('frame_')])
    if n == 0:
        raise RuntimeError(f'动画渲染没出帧\n{out[-2000:]}')
    progress(f'    渲染 {n} 帧')
    return {'blend': ablend, 'frames': n, 'dir': out_dir, 'fps': fps}
