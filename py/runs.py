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
import tempfile
import time

ENV_DIR = 'DSH_RUNS_DIR'
ENV_DIR_FALLBACK = 'DSH_RUNS_DIR_FALLBACK'

_cached_dir = None
_probed = False
_warned = False


def _writable(d):
    """真的写一个探针文件再删掉 —— 只「建成目录」证明不了能写。

    ★ R01 修复：以前只用 makedirs 探测，装机后 base(~/.dsh) 和
      fallback(cwd=插件包目录) 都建不了也写不了，异常直接从 runs_dir()
      抛出来，`runs list` 硬失败、每次 shoot 白刷一行「档案没写成」。
    """
    if not d:
        return False
    try:
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, '.probe-%d' % os.getpid())
        with open(probe, 'w', encoding='utf-8') as f:
            f.write('ok')
        os.remove(probe)
        return True
    except OSError:
        return False


def runs_dir():
    """档案目录；四级候选，逐级用【实写探针】判定。全不可写时返回 None。

    候选顺序：DSH_RUNS_DIR → ~/.dsh/blender-mc/runs → 系统临时目录
    → DSH_RUNS_DIR_FALLBACK / <cwd>/.runs

    ★ 返回 None 的语义：档案功能不可用，但**调用方不该崩**——
      写不了就不写，正式任务照常跑完。
    """
    global _cached_dir, _probed
    if _probed:
        return _cached_dir
    _probed = True
    home_runs = os.path.join(os.path.expanduser('~'), '.dsh', 'blender-mc', 'runs')
    temp_runs = os.path.join(tempfile.gettempdir(), 'dsh-blender-mc', 'runs')
    cands = [
        os.environ.get(ENV_DIR),
        home_runs,
        temp_runs,
        os.environ.get(ENV_DIR_FALLBACK) or os.path.join(os.getcwd(), '.runs'),
    ]
    for d in cands:
        if _writable(d):
            _cached_dir = d
            return d
    _cached_dir = None
    return None


def unavailable_hint():
    """档案不可用时给人看的一句话（只提示一次）。"""
    global _warned
    if _warned:
        return ''
    _warned = True
    return ('⚠️  任务档案不可用：没有可写的目录。'
            '设环境变量 DSH_RUNS_DIR=<可写目录> 后重试（不影响出图/出片）。')


def _new_id(kind):
    stamp = time.strftime('%Y%m%d_%H%M%S')
    base = f'{stamp}_{kind}'
    rid, n = base, 1
    d = runs_dir()
    if not d:
        return rid
    while os.path.exists(os.path.join(d, rid + '.json')):
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
    path = os.path.join(runs_dir(), rid + '.json') if runs_dir() else None
    if not path:
        hint = unavailable_hint()
        if hint:
            print(hint)
        return None, rec
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
    if os.path.isfile(rid):
        path = rid
    else:
        d = runs_dir()
        if not d:
            raise FileNotFoundError('任务档案不可用：没有可写的目录（设 DSH_RUNS_DIR）')
        path = os.path.join(d, rid + '.json')
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def listing(limit=30):
    """最近的档案，新的在前。没有可用目录时返回空列表（不抛）。"""
    d = runs_dir()
    if not d or not os.path.isdir(d):
        return []
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
