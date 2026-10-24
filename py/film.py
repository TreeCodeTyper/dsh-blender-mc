# -*- coding: utf-8 -*-
"""电影化流水线：一份"分镜表"（shots plan）→ 多镜头 → 拼成一支成片。

为什么要有这一层
----------------
animate_yujian / animate_aura / animate_killaura 都是【单镜头】脚本：一次跑
240 帧、一台相机。真实片子不是这么来的 —— 是"分镜表定几个镜头，每镜单独渲，
最后剪起来"。这一层只做三件事，不碰动作本身：

    建工程（每个镜头一份 blend，动作相同、机位不同）
      → 按帧区间渲（blender -b <blend> -s f0 -e f1 -a）
      → 按分镜顺序重编号 + ffmpeg 拼接

于是"同一个动作，三种景别"就是三次建工程 + 三段区间，不需要写新动作。
导演该做的四个决定（讲什么/观众站哪/给多少时间/砍哪条）在这个 JSON 里：

    {
      "title": "御剑飞天",
      "fps": 24, "res": "960x540", "samples": 24,
      "blend": "封面/封面_世界.blend",
      "shots": [
        {"id": "S01", "preset": "yujian", "range": [1, 72],
         "env": {"DSH_YJ_CAM_D_SCALE": "0.78"},
         "shot_size": "中景", "camera": "平视缓推", "note": "符阵亮、剑从土里浮起"},
        {"id": "S02", "preset": "yujian", "range": [66, 150],
         "env": {"DSH_YJ_CAM_AZ_ADD": "-16"},
         "shot_size": "全景", "camera": "低机位仰拍跟随", "note": "转身踏剑冲天"}
      ]
    }

`env` 直接覆盖 animate_* 脚本读的环境变量（DSH_YJ_*），所以每个镜头可以有
自己的机位/焦距/侧倾，而动作时间轴完全一致 —— 这就是"同一场戏多机位"。

两个坑（都踩过）
----------------
1. 相对输出目录会被 Blender 解析到它自己的 cwd，必须 abspath。
2. 同一 preset + 同一 env 的镜头只建一次工程（缓存），否则 3 个镜头要建 3 遍。
"""
import json
import os
import shutil
import time

import pipeline

PRESET_SCRIPTS = {
    'yujian': 'animate_yujian.py',
    'aura': 'animate_aura.py',
    'killaura': 'animate_killaura.py',
}
PRESET_BLENDS = {
    'yujian': '御剑飞天.blend',
    'aura': '杀戮光环.blend',
    'killaura': '杀戮光环.blend',
}
PRESET_ENV_RES = {
    'yujian': ('DSH_YJ_RES', 'DSH_YJ_SAMPLES'),
    'aura': ('DSH_AURA_RES', 'DSH_AURA_SAMPLES'),
    'killaura': ('DSH_KA_RES', 'DSH_KA_SAMPLES'),
}
DEFAULTS = {
    'title': '成片', 'fps': 24, 'res': '960x540', 'samples': 24,
    'total': 240, 'preset': 'yujian', 'crf': 18,
}


def _ffmpeg():
    """找 ffmpeg。pipeline.resolve_assets 在没有插件 config 时会全返回 None
    （它依赖 setup_assets 的清单和环境变量），所以这里自己按优先级找一遍，
    找不到就返回 None —— 那就只出帧序列，把 ffmpeg 命令打给用户。"""
    cands = [os.environ.get('DSH_FILM_FFMPEG'), os.environ.get('DSH_MC_FFMPEG')]
    try:
        from setup_assets import load_manifest
        man = load_manifest().get('assets', {})
        cands.append((man.get('ffmpeg') or {}).get('dst'))
    except Exception:
        pass
    # load_manifest 在没有插件 config 时常常返回空 dict（它按 process env 找清单），
    # 而清单实际就在 ~/.dsh/blender-mc/vendor/manifest.json，直接读它最稳。
    try:
        home = os.environ.get('DSH_HOME') or os.path.join(os.path.expanduser('~'), '.dsh')
        mp = os.path.join(home, 'blender-mc', 'vendor', 'manifest.json')
        if os.path.isfile(mp):
            with open(mp, 'r', encoding='utf-8') as f:
                cands.append((json.load(f).get('assets', {}).get('ffmpeg') or {}).get('dst'))
    except Exception:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    cands += [os.path.join(here, '..', 'vendor', 'ffmpeg', 'bin', 'ffmpeg.exe'),
              os.path.join(here, '..', 'vendor', 'ffmpeg', 'ffmpeg.exe')]
    cands.append(shutil.which('ffmpeg'))
    for p in cands:
        if p and os.path.isfile(p):
            return p
    return None


def _label_secs(n, fps):
    """6 帧 = 0.25 秒；240 帧 = 10 秒。别把 0.5 打成 0。"""
    v = round(n / float(fps), 2)
    s = ('%.2f' % v).rstrip('0').rstrip('.')
    return s or '0'


def load_plan(src):
    """src 可以是 .json 路径，也可以直接是 dict / JSON 字符串。"""
    if isinstance(src, dict):
        return dict(src)
    if isinstance(src, str) and src.lstrip().startswith('{'):
        return json.loads(src)
    with open(src, 'r', encoding='utf-8') as f:
        plan = json.load(f)
    plan['_dir'] = os.path.dirname(os.path.abspath(src))
    return plan


def _resolve(src, base):
    if not src:
        return None
    if os.path.isabs(src):
        return src
    for root in (base, os.getcwd()):
        p = os.path.abspath(os.path.join(root or '.', src))
        if os.path.exists(p):
            return p
    return os.path.abspath(os.path.join(base or '.', src))


def _norm_shot(shot, i, plan):
    s = dict(shot)
    s['id'] = str(s.get('id') or 'S%02d' % i)
    s['preset'] = s.get('preset') or plan.get('preset', DEFAULTS['preset'])
    rng = s.get('range') or [1, plan.get('total', DEFAULTS['total'])]
    try:
        f0, f1 = int(rng[0]), int(rng[1])
    except (TypeError, ValueError, IndexError):
        raise ValueError('镜头 %s 的 range 要写成 [起始帧, 结束帧]' % s['id'])
    if f1 < f0:
        f0, f1 = f1, f0
    s['range'] = [f0, f1]
    s['frames'] = f1 - f0 + 1
    s['env'] = {str(k): str(v) for k, v in (s.get('env') or {}).items()}
    return s


def _shot_print(i, n, s, fps=24.0):
    head = '%s %s' % (s['id'], s.get('shot_size') or '镜头')
    tail = s.get('note') or s.get('camera') or ''
    return '  %d/%d %s 帧 %d-%d（%d 帧 / %.2fs）%s' % (
        i, n, head, s['range'][0], s['range'][1], s['frames'],
        s['frames'] / float(fps), ('  — ' + tail) if tail else '')


def film(blender_exe, plan, out_dir, animatic=False, progress=print):
    """跑完整条流水线，返回结果 dict（mp4 路径 / 帧数 / 分镜表）。"""
    out_dir = os.path.abspath(out_dir)
    base = plan.get('_dir') or os.getcwd()
    blend_src = _resolve(plan.get('blend'), base)
    if not blend_src or not os.path.exists(blend_src):
        raise ValueError('分镜表里 blend（封面工程）找不到: %r' % plan.get('blend'))
    fps = float(plan.get('fps', DEFAULTS['fps']))
    total = int(plan.get('total', DEFAULTS['total']))
    res = str(plan.get('res', DEFAULTS['res'])).replace('×', 'x')
    samples = int(plan.get('samples', DEFAULTS['samples']))
    if animatic:
        res, samples = '480x270', 8
        out_dir = os.path.join(out_dir, 'animatic')
    shots = [_norm_shot(s, i, plan) for i, s in enumerate(plan.get('shots') or [], 1)]
    if not shots:
        raise ValueError('分镜表里没有任何镜头（shots 为空）')
    os.makedirs(out_dir, exist_ok=True)

    progress('分镜：%d 个镜头，共 %d 帧 / %.2f 秒，%s @ %d fps%s'
             % (len(shots), sum(s['frames'] for s in shots),
                sum(s['frames'] for s in shots) / fps, res, fps,
                '（animatic 低清预览）' if animatic else ''))
    for i, s in enumerate(shots, 1):
        progress(_shot_print(i, len(shots), s, fps))

    work = os.path.join(out_dir, '_shots')
    seq = os.path.join(out_dir, '_seq')
    for d in (work, seq):
        if os.path.isdir(d):
            shutil.rmtree(d)
        os.makedirs(d)

    builds = {}          # (preset, env签名) -> 已建好的 blend
    segments = []        # [(shot, 帧文件列表)]
    t0 = time.time()
    for i, s in enumerate(shots, 1):
        preset = s['preset']
        if preset not in PRESET_SCRIPTS:
            raise ValueError('不认识的 preset: %r（可选 %s）'
                             % (preset, '/'.join(PRESET_SCRIPTS)))
        env = os.environ.copy()
        env.update(s['env'])
        key_r, key_s = PRESET_ENV_RES.get(preset, ('', ''))
        if key_r:
            env[key_r] = res
        if key_s:
            env[key_s] = str(samples)
        watch = set(s['env']) | {key_r, key_s}
        sig = (preset, tuple(sorted((k, env.get(k)) for k in watch if k)))
        d = os.path.join(work, '%02d_%s' % (i, s['id']))
        os.makedirs(d, exist_ok=True)

        if sig in builds:
            ablend = builds[sig]
            progress('  %s 复用工程 %s' % (s['id'], os.path.basename(os.path.dirname(ablend))))
        else:
            script = os.path.join(pipeline.BLENDER_DIR, PRESET_SCRIPTS[preset])
            cmd = [blender_exe, '-b', blend_src, '--python', script, '--',
                   d, str(total), str(int(fps)), '']
            rc, out = pipeline.run(cmd, log=os.path.join(d, '_build.log'), env=env)
            if rc != 0:
                raise RuntimeError('镜头 %s 建工程失败（preset=%s）\n%s'
                                   % (s['id'], preset, out[-1800:]))
            ablend = os.path.join(d, PRESET_BLENDS.get(preset, '动画.blend'))
            if not os.path.exists(ablend):
                raise RuntimeError('镜头 %s 建工程后没找到 %s' % (s['id'], ablend))
            builds[sig] = ablend

        f0, f1 = s['range']
        progress('  %s 渲染 %d-%d …' % (s['id'], f0, f1))
        rc, out = pipeline.run([blender_exe, '-b', ablend,
                                '-s', str(f0), '-e', str(f1), '-a'],
                               log=os.path.join(d, '_render.log'))
        frames = sorted(f for f in os.listdir(d)
                        if f.startswith('frame_') and f.endswith('.png'))
        if not frames:
            raise RuntimeError('镜头 %s 没渲出帧（%d-%d）\n%s'
                               % (s['id'], f0, f1, out[-1800:]))
        if len(frames) != s['frames']:
            progress('    ⚠️ 帧数不符：期望 %d，实际 %d' % (s['frames'], len(frames)))
        segments.append((s, d, frames))
        progress('    %s 完成 %d 帧' % (s['id'], len(frames)))

    # —— 重编号：成片要连续的 seq_0001.png ……
    idx = 1
    for s, d, frames in segments:
        for name in frames:
            dst = os.path.join(seq, 'seq_%04d.png' % idx)
            shutil.copyfile(os.path.join(d, name), dst)
            idx += 1
    n_total = idx - 1
    progress('分段拼好：%d 帧 / %.2f 秒' % (n_total, n_total / fps))

    title = plan.get('title') or DEFAULTS['title']
    safe = ''.join('_' if ch in '\\/:*?"<>|' else ch for ch in str(title))
    mp4 = os.path.join(out_dir, '%s_%s秒_%s.mp4'
                       % (safe, _label_secs(n_total, fps), res))
    crf = str(int(plan.get('crf', DEFAULTS['crf'])))
    ffmpeg = _ffmpeg()
    if not ffmpeg:
        progress('⚠️ 没找到 ffmpeg，帧序列留在 %s，可自行转换：\n'
                 '   ffmpeg -framerate %g -i "%s/seq_%%04d.png" -c:v libx264 '
                 '-crf %s -pix_fmt yuv420p -movflags +faststart "%s"'
                 % (seq, fps, seq, crf, mp4))
        mp4 = None
    else:
        rc, out = pipeline.run([ffmpeg, '-y', '-framerate', '%g' % fps,
                                '-i', os.path.join(seq, 'seq_%04d.png'),
                                '-c:v', 'libx264', '-crf', crf,
                                '-pix_fmt', 'yuv420p', '-movflags', '+faststart',
                                mp4], log=os.path.join(out_dir, '_ffmpeg.log'))
        if rc != 0:
            raise RuntimeError('ffmpeg 合成失败\n%s' % out[-1500:])
        progress('成片：%s（%.1f MB）' % (mp4, os.path.getsize(mp4) / 1e6))

    # —— 分镜表留档（人看的东西，和片子放一起）
    md = os.path.join(out_dir, '分镜表.md')
    with open(md, 'w', encoding='utf-8') as f:
        f.write('# %s 分镜表\n\n' % title)
        f.write('- %s @ %g fps，共 %d 帧 / %.2f 秒\n' % (res, fps, n_total, n_total / fps))
        f.write('- 封面工程：`%s`\n' % blend_src)
        f.write('- 渲染：%d samples，H.264 crf %s\n\n' % (samples, crf))
        f.write('| 镜号 | 景别 | 机位 / 运动 | 帧区间 | 时长 | 画面内容 |\n')
        f.write('|---|---|---|---|---|---|\n')
        for s in shots:
            f.write('| %s | %s | %s | %d–%d | %.2fs | %s |\n'
                    % (s['id'], s.get('shot_size') or '—', s.get('camera') or '—',
                       s['range'][0], s['range'][1], s['frames'] / fps,
                       s.get('note') or '—'))
        f.write('\n每个镜头的参数覆盖（env）：\n\n')
        for s in shots:
            if s['env']:
                f.write('- **%s**：%s\n' % (s['id'], '，'.join(
                    '%s=%s' % (k, v) for k, v in sorted(s['env'].items()))))

    with open(os.path.join(out_dir, '分镜表.json'), 'w', encoding='utf-8') as f:
        json.dump({'plan': {k: v for k, v in plan.items() if not k.startswith('_')},
                   'shots': shots, 'frames': n_total, 'fps': fps,
                   'res': res, 'mp4': mp4,
                   'finished_at': time.strftime('%Y-%m-%d %H:%M:%S')},
                  f, ensure_ascii=False, indent=2)
    return {'mp4': mp4, 'frames': n_total, 'fps': fps, 'res': res,
            'shots': shots, 'storyboard': md, 'dir': out_dir,
            'seconds': round(n_total / fps, 2),
            'elapsed': round(time.time() - t0, 1)}
