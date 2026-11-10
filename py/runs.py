"""任务档案（runs）：每次 shoot / anim / film 结束落一份 json。

为什么要有这个：
  以前跑完一次 shoot，参数、审图结论、产出的图只活在返回值里，agent 关了
  就全丢；同一个镜头想复现只能凭记忆重敲一遍参数。这里把「这一趟到底干了
  什么」写成文件，既给人看（cat 一下就知道），也给 --replay 用（照着同一份
  参数重跑）。

契约（字段名是稳定的，docs/修复计划与验收.md 里写了）：
  id / kind / ts / goal / inputs / params / artifacts / verdict / score /
  next_action / replay

  - inputs: 这次吃了什么（存档、区域、皮肤、分镜表…）
  - params: 生效参数（含 auto_adjust 最后的调整结果）
  - artifacts: 产物路径（png / blend / mp4）
  - verdict: 审图结论原文；score: 分数
  - next_action: 人（或 agent）下一步该干什么，留给下一次读
  - replay: 重放这一趟所需的最小 kwargs
"""
import json
import os
import time

ENV_DIR = 'DSH_RUNS_DIR'
ENV_DIR_FALLBACK = 'DSH_RUNS_DIR_FALLBACK'


def runs_dir():
    """档案目录。默认 ~/.dsh/blender-mc/runs；写不进去就退到当前目录下的 .runs。

    ⚠️ 实测：在 DSH 的 workspace-write 沙箱里，~/.dsh 是【不可写】的
    （WinError 5）。插件正式跑时宿主进程能写，但工具单独跑会撞墙，
    所以这里必须能退回一个可写目录，而不是直接把整个 shoot 搞挂。
    """
    base = os.environ.get(ENV_DIR) or os.path.join(
        os.path.expanduser('~'), '.dsh', 'blender-mc', 'runs')
    try:
        os.makedirs(base, exist_ok=True)
        return base
    except OSError:
        fallback = os.environ.get(ENV_DIR_FALLBACK) or os.path.join(
            os.getcwd(), '.runs')
        os.makedirs(fallback, exist_ok=True)
        return fallback


def _new_id(kind):
    stamp = time.strftime('%Y%m%d_%H%M%S')
    base = f'{stamp}_{kind}'
    rid, n = base, 1
    while os.path.exists(os.path.join(runs_dir(), rid + '.json')):
        n += 1
        rid = f'{base}_{n}'
    return rid


def write(kind, goal='', inputs=None, params=None, artifacts=None,
          verdict=None, score=None, next_action='', replay=None):
    """写一份档案，返回 (路径, 记录)。任何异常都不该把正式任务搞挂。"""
    rid = _new_id(kind)
    rec = {
        'id': rid,
        'kind': kind,
        'ts': time.strftime('%Y-%m-%d %H:%M:%S'),
        'goal': goal or '',
        'inputs': _plain(inputs or {}),
        'params': _plain(params or {}),
        'artifacts': _plain(artifacts or []),
        'verdict': verdict,
        'score': score,
        'next_action': next_action or '',
        'replay': _plain(replay or {}),
    }
    path = os.path.join(runs_dir(), rid + '.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    return path, rec


def _plain(v):
    """把 Path / 元组 之类变成能直接 json 化的值。"""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def load(rid):
    """按 id 或路径读档案。"""
    path = rid if os.path.isfile(rid) else os.path.join(runs_dir(), rid + '.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def listing(limit=30):
    """最近的档案，新的在前。"""
    d = runs_dir()
    names = [n for n in os.listdir(d) if n.endswith('.json')]
    names.sort(reverse=True)
    out = []
    for n in names[:limit]:
        try:
            with open(os.path.join(d, n), encoding='utf-8') as f:
                r = json.load(f)
        except Exception:
            continue
        out.append(r)
    return out
