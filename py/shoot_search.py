# -*- coding: utf-8 -*-
"""shoot_search —— 渲染反馈搜索：用低清图评分，贝叶斯找相机参数，再出正式图。

搜索维度：yaw / cam_az / dist / lens。每次评估调一次 pipeline.shoot(低清)，
分数取 vision_check 的 score；分数越高越好，所以搜索目标是最小化 -score。
"""
import json
import os

import pipeline


def search(save, area, out, center=None, centers=None, top=3, scan_radius=15,
           skin=None, env=None, params=None,
           n_init=6, iters=4, seed=11, final_res='1280x720',
           final_samples=64, progress=print):
    from search import bayes_opt

    out = os.path.abspath(out)
    os.makedirs(out, exist_ok=True)
    if skin:
        os.environ['DSH_SKIN'] = os.path.abspath(skin)
    for k, v in (env or {}).items():
        os.environ[str(k)] = str(v)
    base = dict(params or {})

    def evaluate(x, c, res='480x270', samples=8):
        yaw, az, dist, lens = (float(v) for v in x)
        p = dict(base)
        p.update({'yaw': yaw, 'cam_az': az, 'dist': dist, 'lens': lens,
                  'res': res, 'samples': samples})
        r = pipeline.shoot(save, area, os.path.join(out, '_eval'), center=c,
                           params=p, max_attempts=1, target_score=0,
                           progress=lambda *_: None)
        score = float(r.get('score') or 0)
        progress('  评估 yaw=%.0f az=%.0f dist=%.1f lens=%.0f -> %d'
                 % (yaw, az, dist, lens, score))
        return -score

    if centers:
        cand = [(float(c[0]), float(c[1])) for c in centers]
    elif center:
        cand = [(float(center[0]), float(center[1]))]
    else:
        progress('  先扫 top%d 机位…' % top)
        shots = pipeline.step_scan(save, area, radius=scan_radius, top_n=top,
                                   progress=progress)
        cand = [(float(s['x']), float(s['z'])) for s in shots]
        if not cand:
            raise RuntimeError('没有可用机位')

    base_x = [float(base.get('yaw', 25.0)), float(base.get('cam_az', 200.0)),
              float(base.get('dist', 13.8)), float(base.get('lens', 105.0))]
    center = cand[0]
    base_score = -1e9
    for c in cand:
        sc = -evaluate(base_x, c, res='1280x720', samples=16)
        progress('  基准 (%d,%d) %d 分' % (c[0], c[1], sc))
        if sc > base_score:
            base_score, center = sc, c

    def score_at_best(x):
        return evaluate(x, center)

    best, best_neg, trace = bayes_opt(
        score_at_best, [(0.0, 360.0), (0.0, 360.0), (8.0, 22.0), (50.0, 150.0)],
        n_init=n_init, iters=iters, seed=seed)
    best = [float(v) for v in best]
    if -best_neg < base_score:
        best = base_x
        best_neg = -base_score
        trace.append(best_neg)
    final = dict(base)
    final.update({'yaw': best[0], 'cam_az': best[1], 'dist': best[2],
                  'lens': best[3], 'res': final_res,
                  'samples': final_samples})
    r = pipeline.shoot(save, area, os.path.join(out, 'final'), center=center,
                       params=final, max_attempts=1, target_score=0,
                       progress=progress)
    result = {
        'center': list(center),
        'best': {'yaw': best[0], 'cam_az': best[1], 'dist': best[2],
                 'lens': best[3]},
        'score': -float(best_neg),
        'trace': [round(float(v), 2) for v in trace],
        'png': r.get('png'),
        'verdict': r.get('verdict'), 'issues': r.get('issues'),
    }
    with open(os.path.join(out, 'search.json'), 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result
