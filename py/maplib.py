# -*- coding: utf-8 -*-
"""mc_maplib —— 背景地图库（把玩家上传的存档 zip 解压、识别、入库）

为什么单独一个模块：
  · 玩家上传的是一整个整合包存档 zip（几百 MB 到几 GB），JS 侧不适合做
    解压与存档识别；这里用标准库 zipfile 一次做完，Node 只当 HTTP 薄壳。
  · 入库是唯一的磁盘写入者：索引 index.json 只由本模块写，Node 侧只读，
    避免两边同时改索引打架。

目录结构（root 默认 <DSH_HOME>/blender-mc/maps）::

    root/
      index.json            {version, defaultId, maps: {id: meta}}
      maps/<id>/meta.json   单张地图的元数据（索引损坏时的最后防线）
      maps/<id>/world/      解压后的存档目录（直接喂 mc_shoot 的 save）
      maps/<id>/thumb.png   缩略图（存档自带的 icon.png）
      .tmp/                 解压临时区，随进程结束清理

命令行（由 lib/map-routes.js 调用，也可以用 --job 传 JSON 文件）::

    python py/cli.py map import  --job job.json --json
    python py/cli.py map import  --zip a.zip [--name 名字] [--root 目录] --json
    python py/cli.py map list    --root 目录 --json
    python py/cli.py map get     --root 目录 --id m_xxxxxxxxxxxx --json
    python py/cli.py map rename  --root 目录 --id m_x --name 新名字 --json
    python py/cli.py map delete  --root 目录 --id m_x --json
    python py/cli.py map default --root 目录 --id m_x --json
    python py/cli.py map doctor  --root 目录 --json

统一输出：{"ok": true, "data": ...} 或 {"ok": false, "error": {"code","message","params"}}
"""
import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import time
import uuid
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

ID_RE = re.compile(r'^m_[0-9a-f]{12}$')
REGION_RE = re.compile(r'^r\.(-?\d+)\.(-?\d+)\.mca$')
DRIVE_RE = re.compile(r'^[A-Za-z]:')
MIB = 1024 * 1024

# Windows 上不能作为文件名的保留名（带扩展名也算，例如 con.txt）
RESERVED = set('con prn aux nul'.split()) | {'com%d' % i for i in range(1, 10)} | \
           {'lpt%d' % i for i in range(1, 10)} | {'clock$'}

DEFAULTS = {
    'max_zip_bytes': 2048 * MIB,      # 单个 zip 上限 2 GiB
    'max_unpacked': 6144 * MIB,       # 解压总量上限 6 GiB
    'max_entries': 200000,            # 条目数上限
    'max_entry': 1024 * MIB,          # 单条目上限 1 GiB
    'max_ratio': 100,                 # 单条目压缩比上限（防炸弹）
    'max_depth': 12,                  # 路径深度上限
    'max_path': 240,                  # 相对路径长度上限
    'max_level_bytes': 64 * MIB,      # level.dat 读取上限
    'max_thumb_bytes': 8 * MIB,       # icon.png 读取上限
}

BACKUP_WORDS = ('backup', 'bak/', '副本', '/old', 'old/', 'copy', 'snapshot', '备份')
JUNK_PREFIX = ('__MACOSX/',)
JUNK_NAMES = ('.DS_Store', 'Thumbs.db', 'desktop.ini')


class MapError(Exception):
    """带错误码的业务异常，直接转成 HTTP 错误体。"""

    def __init__(self, code, message, params=None, status=400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.params = params or {}
        self.status = status

    def body(self):
        return {'code': self.code, 'message': self.message, 'params': self.params}


# ------------------------------------------------------------------ 基础设施

def now_ms():
    return int(time.time() * 1000)


def default_root(env=None):
    env = env or os.environ
    home = env.get('DSH_HOME') or os.path.join(os.path.expanduser('~'), '.dsh')
    # 库根目录：<DSH_HOME>/blender-mc
    # 每个存档落在这里的 maps/<id>/world（ensure_dirs/_map_dir 会再拼 'maps'），
    # 所以这里不能再带 /maps，否则会出现 maps/maps/<id> 双层目录（0.3.1 及以前）。
    return os.path.join(home, 'blender-mc')


def ensure_dirs(root):
    for sub in ('', 'maps', '.tmp'):
        os.makedirs(os.path.join(root, sub), exist_ok=True)


def _read_json(path, default):
    try:
        # utf-8-sig：PowerShell 5.1 的 `Set-Content -Encoding utf8` 会写 BOM，
        # 带 BOM 的 job 文件用 utf-8 解析会抛异常，表现为「缺少 --zip」这种莫名错误。
        with open(path, 'r', encoding='utf-8-sig') as f:
            return json.load(f)
    except FileNotFoundError:
        return default
    except Exception:
        return default


def _write_json(path, obj):
    tmp = '%s.tmp-%d' % (path, os.getpid())
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _sha256_file(path, chunk=4 * MIB):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def index_path(root):
    return os.path.join(root, 'index.json')


def read_index(root):
    idx = _read_json(index_path(root), None)
    if not isinstance(idx, dict) or not isinstance(idx.get('maps'), dict):
        idx = {'version': 1, 'defaultId': None, 'maps': {}}
    idx.setdefault('version', 1)
    idx.setdefault('defaultId', None)
    return idx


def _sanitize_name(name, fallback='未命名地图'):
    if not isinstance(name, str):
        return fallback
    s = name.replace('\r', ' ').replace('\n', ' ').replace('\t', ' ').strip()
    s = ''.join(c for c in s if ord(c) >= 0x20)
    s = s.strip(' .')
    if len(s) > 60:
        s = s[:60].rstrip()
    return s or fallback


def _map_dir(root, map_id):
    if not isinstance(map_id, str) or not ID_RE.match(map_id):
        raise MapError('map.bad-id', '地图 id 不合法', status=400)
    return os.path.join(root, 'maps', map_id)


# ------------------------------------------------------------------ zip 安全

def _safe_rel(name, limits):
    """把 zip 条目名规范成安全的相对路径；返回 None 表示忽略（根目录/垃圾文件）。"""
    n = name.replace('\\', '/')
    while n.startswith('./'):
        n = n[2:]
    if not n.strip('/'):
        return None
    if n.startswith('/'):
        raise MapError('zip.slip-entry', 'zip 里有绝对路径条目：%s' % name[:80])
    if DRIVE_RE.match(n):
        raise MapError('zip.slip-entry', 'zip 里有盘符路径条目：%s' % name[:80])
    if n.startswith('//') or n.startswith('\\\\'):
        raise MapError('zip.slip-entry', 'zip 里有 UNC 路径条目：%s' % name[:80])

    parts = []
    for seg in n.split('/'):
        if seg in ('', '.'):
            continue
        if seg == '..':
            raise MapError('zip.slip-entry', 'zip 里有 .. 上级目录条目：%s' % name[:80])
        if any(ord(c) < 0x20 for c in seg):
            raise MapError('zip.slip-entry', 'zip 条目名含控制字符：%r' % name[:80])
        if seg.rstrip(' .') != seg:
            raise MapError('zip.slip-entry', 'zip 条目名以空格或点结尾（Windows 不允许）：%s' % seg[:60])
        if ':' in seg:
            raise MapError('zip.slip-entry', 'zip 条目名含冒号（NTFS 数据流）：%s' % seg[:60])
        base = seg.split('.')[0].lower()
        if base in RESERVED:
            raise MapError('zip.slip-entry', 'zip 条目名是 Windows 保留设备名：%s' % seg[:60])
        parts.append(seg)

    if not parts:
        return None
    rel = '/'.join(parts)
    if len(parts) > limits['max_depth']:
        raise MapError('zip.too-deep', 'zip 内目录层级过深（>%d）：%s' % (limits['max_depth'], rel[:80]))
    if len(rel) > limits['max_path']:
        raise MapError('zip.path-too-long', 'zip 内路径过长（>%d 字符）：%s' % (limits['max_path'], rel[:80]))
    if rel.endswith('.zip') or rel.endswith('.ZIP'):
        # 嵌套 zip 不解（会变成 zip 炸弹的踏脚石），只当普通文件放着
        return rel
    return rel


def _is_junk(rel):
    low = rel.lower()
    if any(low.startswith(p.lower()) for p in JUNK_PREFIX):
        return True
    base = rel.rsplit('/', 1)[-1]
    if base in JUNK_NAMES:
        return True
    if base.startswith('._'):
        return True
    return False


def _mode_of(info):
    return (info.external_attr >> 16) & 0o170000


def _extract_zip(zip_path, dest, limits, on_stage=None):
    """解压到 dest，全程校验。返回 (文件数, 解压字节数, 条目名样本)。"""
    real_root = os.path.realpath(dest)
    total = 0
    count = 0
    names = []
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise MapError('zip.unreadable', '这不是一个有效的 zip 包（或已损坏）')
    except Exception as exc:  # noqa: BLE001
        raise MapError('zip.unreadable', 'zip 打不开：%s' % exc)

    with zf:
        infos = zf.infolist()
        if len(infos) > limits['max_entries']:
            raise MapError('zip.too-many-entries',
                           'zip 里有 %d 个条目，超过上限 %d' % (len(infos), limits['max_entries']))
        declared = sum(i.file_size for i in infos if not i.is_dir())
        if declared > limits['max_unpacked']:
            raise MapError('zip.too-large',
                           '解压后约 %.1f GiB，超过上限 %.1f GiB'
                           % (declared / 1024.0 ** 3, limits['max_unpacked'] / 1024.0 ** 3))

        seen = {}
        for info in infos:
            rel = _safe_rel(info.filename, limits)
            if rel is None or _is_junk(rel):
                continue
            mode = _mode_of(info)
            if mode in (stat.S_IFLNK, stat.S_IFCHR, stat.S_IFIFO, stat.S_IFBLK):
                raise MapError('zip.unsafe-entry', 'zip 里有符号链接/设备文件：%s' % rel[:80])
            if info.flag_bits & 0x1:
                raise MapError('zip.encrypted', 'zip 有密码保护，无法解压：%s' % rel[:80])
            if info.is_dir():
                os.makedirs(os.path.join(real_root, *rel.split('/')), exist_ok=True)
                continue
            if info.file_size > limits['max_entry']:
                raise MapError('zip.entry-too-large',
                               '单个文件 %.0f MiB 超过上限 %d MiB：%s'
                               % (info.file_size / MIB, limits['max_entry'] // MIB, rel[:80]))
            if info.compress_size > 0 and info.file_size > MIB and \
                    info.file_size / float(info.compress_size) > limits['max_ratio']:
                raise MapError('zip.ratio-suspicious',
                               '压缩比异常（%.0f:1），疑似解压炸弹：%s'
                               % (info.file_size / float(info.compress_size), rel[:80]))

            key = rel.casefold()
            if key in seen:
                # 大小写重名（Windows 上会互相覆盖），只保留第一个
                continue
            seen[key] = rel

            target = os.path.join(real_root, *rel.split('/'))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not os.path.realpath(target).startswith(real_root + os.sep):
                raise MapError('zip.slip-entry', 'zip 条目会写到目录外：%s' % rel[:80])
            try:
                with zf.open(info) as src, open(target, 'wb') as dst:
                    while True:
                        buf = src.read(MIB)
                        if not buf:
                            break
                        total += len(buf)
                        if total > limits['max_unpacked']:
                            raise MapError('zip.too-large',
                                           '解压总量超过上限 %.1f GiB'
                                           % (limits['max_unpacked'] / 1024.0 ** 3))
                        dst.write(buf)
            except MapError:
                raise
            except NotImplementedError:
                raise MapError('zip.unreadable',
                               'zip 使用了不支持的压缩方式（zip64/加密）：%s' % rel[:80])
            except RuntimeError as exc:
                low = str(exc).lower()
                if 'encrypt' in low or 'password' in low:
                    raise MapError('zip.encrypted', 'zip 有密码保护，无法解压：%s' % rel[:80])
                raise
            except zipfile.BadZipFile as exc:
                raise MapError('zip.corrupt', 'zip 条目校验失败：%s（%s）' % (rel[:80], exc))
            count += 1
            if on_stage and count % 500 == 0:
                on_stage(count, total)
    return count, total, names


# ------------------------------------------------------------------ 存档识别

def _region_stats(save_dir):
    """返回 (region 文件数, bounds, region 列表)。"""
    rdir = os.path.join(save_dir, 'region')
    xs, zs = [], []
    n = 0
    try:
        for nm in os.listdir(rdir):
            m = REGION_RE.match(nm)
            if not m:
                continue
            n += 1
            xs.append(int(m.group(1)))
            zs.append(int(m.group(2)))
    except OSError:
        return 0, None, []
    if not xs:
        return 0, None, []
    # 按「主簇」算范围，而不是 min/max。
    # 整合包/联机存档里经常残留一撮几百万格外的前世探索残片（实测
    # 「方可梦世界」有 region\r.25931.-58593.mca 这样一整簇），直接用 min/max
    # 会算出 1300 万 × 3000 万格这种荒唐的「世界范围」，卡片上没法看，
    # 没有出生点时推荐机位也会被带到空白区。
    pairs = list(zip(xs, zs))
    bounds, outliers = _cluster_bounds(pairs)
    if outliers:
        bounds['outliers'] = outliers
    return n, bounds, pairs


def _cluster_bounds(pairs, gap=8):
    """把 region 坐标按「相距 <= gap 个 region」聚成簇，取最大簇的包围盒。

    返回 (bounds, 被丢掉的 region 数)。gap=8 个 region = 4096 格，
    正常连续探索的世界不会被切开，几千格之外的孤岛残片会被丢掉。
    """
    pts = sorted(pairs)
    clusters = []
    cur = [pts[0]]
    for p in pts[1:]:
        near = any(abs(p[0] - q[0]) <= gap and abs(p[1] - q[1]) <= gap for q in cur[-64:])
        if near:
            cur.append(p)
        else:
            clusters.append(cur)
            cur = [p]
    clusters.append(cur)
    best = max(clusters, key=len)
    bx = [p[0] for p in best]
    bz = [p[1] for p in best]
    bounds = {
        'xMin': min(bx) * 512,
        'xMax': (max(bx) + 1) * 512 - 1,
        'zMin': min(bz) * 512,
        'zMax': (max(bz) + 1) * 512 - 1,
    }
    dropped = len(pts) - len(best)
    return bounds, dropped


def _score_save(path, rel):
    """给候选存档打分：越像「玩家真正在玩的那个世界」分越高。"""
    low = '/' + rel.lower().replace('\\', '/') + '/'
    if '/mods/' in low or '/datapacks/' in low or '/resourcepacks/' in low:
        return -10000
    regions, bounds, _ = _region_stats(path)
    if not regions:
        return -10000
    score = 40 + min(regions, 300) / 3.0
    for sub, pts in (('DIM-1', 15), ('DIM1', 15), ('dimensions', 15), ('datapacks', 10)):
        if os.path.isdir(os.path.join(path, sub)):
            score += pts
    if os.path.isfile(os.path.join(path, 'icon.png')):
        score += 5
    if os.path.isfile(os.path.join(path, 'level.dat')):
        score += 10
    if any(w in low for w in BACKUP_WORDS):
        score -= 40
    score -= rel.count('/') * 2
    return score


def find_saves(root):
    """在解压目录里找所有像存档的目录（含 level.dat + region/*.mca）。"""
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        low = dirpath.lower()
        # 剪枝：整合包里的 mods/datapacks 子树不可能藏着玩家的主世界
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in ('mods', 'resourcepacks', 'shaderpacks', '.git')]
        if 'level.dat' not in filenames:
            continue
        rel = os.path.relpath(dirpath, root).replace('\\', '/')
        if rel == '.':
            rel = ''
        score = _score_save(dirpath, rel)
        if score <= -1000:
            continue
        out.append({'path': dirpath, 'rel': rel or '.', 'score': score})
    out.sort(key=lambda s: (-s['score'], s['rel'].count('/'), s['rel']))
    return out


def read_level_dat(save_dir, limits=None):
    """读 level.dat（gzip+NBT）：版本、名字、出生点、上次游玩时间。"""
    limits = limits or DEFAULTS
    p = os.path.join(save_dir, 'level.dat')
    info = {'levelName': None, 'mcVersion': None, 'dataVersion': None,
            'spawn': None, 'lastPlayed': None, 'hardcore': False}
    try:
        with open(p, 'rb') as f:
            raw = f.read(limits['max_level_bytes'])
        if raw[:2] == b'\x1f\x8b':
            raw = gzip.decompress(raw)
        from anvil import parse_nbt
        root = parse_nbt(raw)
    except Exception:  # noqa: BLE001 —— level.dat 坏了不该让整次上传失败
        return info
    data = root.get('Data') if isinstance(root.get('Data'), dict) else root
    if not isinstance(data, dict):
        return info
    name = data.get('LevelName')
    if isinstance(name, str):
        info['levelName'] = name
    ver = data.get('Version')
    if isinstance(ver, dict):
        info['mcVersion'] = ver.get('Name') if isinstance(ver.get('Name'), str) else None
        if isinstance(ver.get('Id'), int):
            info['dataVersion'] = ver['Id']
    if info['dataVersion'] is None and isinstance(data.get('DataVersion'), int):
        info['dataVersion'] = data['DataVersion']
    if isinstance(data.get('LastPlayed'), int):
        info['lastPlayed'] = data['LastPlayed']

    # 出生点：老版本 SpawnX/Y/Z，1.20+ 也可能是 SpawnPos 列表，1.21.9+ 是 Spawn{pos:[x,y,z]}
    for key in ('SpawnX', 'SpawnY', 'SpawnZ'):
        if isinstance(data.get(key), int):
            info.setdefault('_s', {})[key[-1]] = data[key]
    s = info.pop('_s', None)
    if s and len(s) == 3:
        info['spawn'] = {'x': s['X'], 'y': s['Y'], 'z': s['Z'], 'source': 'SpawnX'}
    else:
        cand = None
        sp = data.get('SpawnPos')
        if isinstance(sp, list) and len(sp) >= 3 and all(isinstance(v, (int, float)) for v in sp[:3]):
            cand = sp[:3]
        elif isinstance(data.get('Spawn'), dict):
            pos = data['Spawn'].get('pos')
            if isinstance(pos, list) and len(pos) >= 3:
                cand = pos[:3]
        if cand:
            info['spawn'] = {'x': int(cand[0]), 'y': int(cand[1]), 'z': int(cand[2]),
                             'source': 'SpawnPos'}
    return info


def suggest_area(bounds, spawn, half=384):
    """给 mc_scan/mc_shoot 一个默认扫描区：以出生点为中心的方框，钳进 bounds。\n\n    ★ half 从 240 提到 384：出生点附近常常是玩家自己铺的广场/房子，\n      ±240 里挑不出像样的自然地形（实测 方可梦世界 只有 5 个候选，全是石砖广场）；\n      放大到 ±384 才能扫到草地/围墙/花丛那种"像个世界"的背景。\n    """""
    if not bounds:
        return None
    if spawn:
        cx, cz = int(spawn['x']), int(spawn['z'])
    else:
        cx = (bounds['xMin'] + bounds['xMax']) // 2
        cz = (bounds['zMin'] + bounds['zMax']) // 2
    x0 = max(bounds['xMin'], cx - half)
    x1 = min(bounds['xMax'], cx + half)
    z0 = max(bounds['zMin'], cz - half)
    z1 = min(bounds['zMax'], cz + half)
    if x1 - x0 < 32 or z1 - z0 < 32:
        # 出生点跑到世界边界外了，退回包围盒中心
        cx = (bounds['xMin'] + bounds['xMax']) // 2
        cz = (bounds['zMin'] + bounds['zMax']) // 2
        x0, x1 = cx - half, cx + half
        z0, z1 = cz - half, cz + half
    return '%d,%d,%d,%d' % (x0, x1, z0, z1)


def _grab_thumb(save_dir, dest_path, limits):
    src = os.path.join(save_dir, 'icon.png')
    if not os.path.isfile(src):
        return False
    try:
        if os.path.getsize(src) > limits['max_thumb_bytes']:
            return False
        with open(src, 'rb') as f:
            head = f.read(8)
        if head[:8] != b'\x89PNG\r\n\x1a\n':
            return False
        shutil.copyfile(src, dest_path)
        return True
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ 公开 API

def dto(meta, root=None):
    """给前端的 DTO：字段名跟 client.js 对齐（bounds 用 xMin.. 形式）。"""
    m = dict(meta)
    m.setdefault('boundsKnown', bool(m.get('bounds')))
    m['hasThumb'] = bool(m.get('thumb'))
    return m


def list_maps(root=None):
    root = root or default_root()
    ensure_dirs(root)
    idx = read_index(root)
    maps = list(idx['maps'].values())
    maps.sort(key=lambda m: (0 if m.get('id') == idx.get('defaultId') else 1,
                             -(m.get('updatedAt') or 0)))
    return {
        'maps': [dto(m) for m in maps if ID_RE.match(str(m.get('id', '')))],
        'defaultId': idx.get('defaultId'),
        'limits': {'maxUploadBytes': DEFAULTS['max_zip_bytes']},
        'root': root,
    }


def get_map(map_id, root=None):
    root = root or default_root()
    idx = read_index(root)
    meta = idx['maps'].get(map_id)
    if not meta:
        d = _map_dir(root, map_id)
        meta = _read_json(os.path.join(d, 'meta.json'), None)
    if not meta:
        raise MapError('map.not-found', '这个地图不在库里（可能已被删除）', status=404)
    return dto(meta)


def resolve_world(map_id, root=None):
    """map id → 存档目录绝对路径（给 mc_shoot/mc_scan 用）。"""
    root = root or default_root()
    meta = get_map(map_id, root=root)
    p = meta.get('savePath')
    if not p or not os.path.isdir(p):
        # 自愈：记录里的绝对路径失效（库目录搬过家、或 0.3.1 的 maps/maps 双层）
        # 时，按「库根 + maps/<id>/<worldRel>」重算一次，能对上就用它并回写。
        fallback = os.path.join(_map_dir(root, map_id), meta.get('worldRel') or 'world')
        if os.path.isdir(fallback):
            p = fallback
            try:
                raw = _read_json(os.path.join(_map_dir(root, map_id), 'meta.json'), None)
                if isinstance(raw, dict):
                    raw['savePath'] = p
                    _write_json(os.path.join(_map_dir(root, map_id), 'meta.json'), raw)
                idx = read_index(root)
                if map_id in idx['maps']:
                    idx['maps'][map_id]['savePath'] = p
                    _write_json(index_path(root), idx)
            except Exception:
                pass
        else:
            raise MapError('map.missing-world', '地图记录还在，但存档目录不见了：%s' % (p or '?'),
                           status=409)
    return p, meta


def rename_map(map_id, name, root=None):
    root = root or default_root()
    idx = read_index(root)
    meta = idx['maps'].get(map_id)
    if not meta:
        raise MapError('map.not-found', '这个地图不在库里', status=404)
    clean = _sanitize_name(name, fallback=meta.get('name') or '未命名地图')
    if not isinstance(name, str) or not name.strip():
        raise MapError('map.name-required', '名字不能为空')
    if len(clean) > 60:
        raise MapError('map.name-too-long', '名字不能超过 60 个字')
    meta['name'] = clean
    meta['updatedAt'] = now_ms()
    idx['maps'][map_id] = meta
    _write_json(index_path(root), idx)
    _write_json(os.path.join(_map_dir(root, map_id), 'meta.json'), meta)
    return dto(meta)


def set_default(map_id, root=None):
    root = root or default_root()
    idx = read_index(root)
    if map_id and map_id not in idx['maps']:
        raise MapError('map.not-found', '这个地图不在库里', status=404)
    idx['defaultId'] = map_id or None
    _write_json(index_path(root), idx)
    return {'defaultId': idx['defaultId']}


def delete_map(map_id, root=None):
    root = root or default_root()
    idx = read_index(root)
    d = _map_dir(root, map_id)
    if map_id not in idx['maps'] and not os.path.isdir(d):
        raise MapError('map.not-found', '这个地图不在库里', status=404)
    meta = idx['maps'].pop(map_id, None)
    if idx.get('defaultId') == map_id:
        idx['defaultId'] = None
    _write_json(index_path(root), idx)
    trash = os.path.join(root, 'trash')
    try:
        if os.path.isdir(d):
            os.makedirs(trash, exist_ok=True)
            dst = os.path.join(trash, '%s-%d' % (map_id, now_ms()))
            os.replace(d, dst) if os.path.dirname(d) == trash else shutil.move(d, dst)
    except Exception:  # noqa: BLE001 —— 移不动就直接删
        shutil.rmtree(d, ignore_errors=True)
    return {'id': map_id, 'name': (meta or {}).get('name'), 'defaultId': idx.get('defaultId')}


def _norm_sha(s):
    if isinstance(s, str) and re.match(r'^[0-9a-f]{64}$', s):
        return s
    return None


def import_zip(zip_path, name=None, root=None, sha256=None, source_filename=None,
               limits=None, keep_zip=False):
    """上传主流程：校验 → 解压 → 识别存档 → 入库。返回 meta。"""
    limits = dict(DEFAULTS, **(limits or {}))
    root = root or default_root()
    ensure_dirs(root)
    if not os.path.isfile(zip_path):
        raise MapError('map.no-file', '上传的文件不存在：%s' % zip_path, status=400)
    size = os.path.getsize(zip_path)
    if size <= 0:
        raise MapError('map.empty', '这个文件是空的')
    if size > limits['max_zip_bytes']:
        raise MapError('map.too-large', 'zip 有 %.1f GiB，超过上限 %.1f GiB'
                       % (size / 1024.0 ** 3, limits['max_zip_bytes'] / 1024.0 ** 3),
                       status=413)

    sha = _norm_sha(sha256) or _sha256_file(zip_path)
    map_id = 'm_' + sha[:12]
    idx = read_index(root)
    if map_id in idx['maps']:
        old = idx['maps'][map_id]
        raise MapError('map.duplicate', '这个存档已经上传过了（%s）' % (old.get('name') or map_id),
                       params={'id': map_id, 'name': old.get('name')}, status=409)

    tmp_base = os.path.join(root, '.tmp')
    os.makedirs(tmp_base, exist_ok=True)
    work = os.path.join(tmp_base, '%s-%s' % (map_id, uuid.uuid4().hex[:8]))
    os.makedirs(work, exist_ok=True)
    unpack = os.path.join(work, 'unpack')
    os.makedirs(unpack, exist_ok=True)
    warnings = []
    try:
        files, unpacked, _ = _extract_zip(zip_path, unpack, limits)
        if files == 0:
            raise MapError('zip.empty', 'zip 里没有可用的文件')

        saves = find_saves(unpack)
        if not saves:
            raise MapError(
                'save.not-found',
                'zip 里没找到 Minecraft 存档（需要包含 level.dat 和 region/*.mca）。'
                '请上传世界文件夹或包含世界文件夹的整合包压缩包。')
        best = saves[0]
        if len(saves) > 1:
            warnings.append('zip 里有 %d 个存档，已选「%s」' % (len(saves), best['rel']))
        # 明显是备份的，即使被选中也提醒一句
        if any(w in ('/' + best['rel'].lower() + '/') for w in BACKUP_WORDS):
            warnings.append('选中的目录名字像备份，可能不是最新的那个存档')

        save_dir = best['path']
        regions, bounds, _ = _region_stats(save_dir)
        if regions == 0:
            raise MapError('save.no-region', '存档里一个 region 文件都没有，像是空世界')

        level = read_level_dat(save_dir, limits)
        spawn = level.get('spawn')
        if spawn and bounds and not (bounds['xMin'] <= spawn['x'] <= bounds['xMax']
                                     and bounds['zMin'] <= spawn['z'] <= bounds['zMax']):
            # 出生点落进了被分位数裁掉的那一小撮 region 里（存档真正在玩的那块
            # 偏在一角）→ 把范围扩到包含出生点，而不是丢掉出生点。
            warnings.append('出生点超出主簇范围，已把世界范围扩展到包含出生点')
            bounds = {
                'xMin': min(bounds['xMin'], spawn['x'] - 256),
                'xMax': max(bounds['xMax'], spawn['x'] + 256),
                'zMin': min(bounds['zMin'], spawn['z'] - 256),
                'zMax': max(bounds['zMax'], spawn['z'] + 256),
            }
        area = suggest_area(bounds, spawn)
        if not area:
            raise MapError('save.no-region', '算不出世界范围，无法推荐机位')

        dest = _map_dir(root, map_id)
        os.makedirs(dest, exist_ok=True)
        world = os.path.join(dest, 'world')
        if os.path.isdir(world):
            shutil.rmtree(world, ignore_errors=True)
        shutil.move(save_dir, world)

        thumb = 'thumb.png'
        has_thumb = _grab_thumb(world, os.path.join(dest, thumb), limits)

        clean_name = _sanitize_name(
            name or level.get('levelName') or
            os.path.splitext(os.path.basename(source_filename or zip_path))[0])

        ts = now_ms()
        meta = {
            'id': map_id,
            'name': clean_name,
            'sourceFilename': source_filename or os.path.basename(zip_path),
            'sizeBytes': size,
            'sha256': sha,
            'createdAt': ts,
            'updatedAt': ts,
            'savePath': world,
            'worldRel': 'world',
            'levelName': level.get('levelName'),
            'mcVersion': level.get('mcVersion'),
            'dataVersion': level.get('dataVersion'),
            'lastPlayed': level.get('lastPlayed'),
            'spawn': spawn,
            'bounds': bounds,
            'regionCount': regions,
            'archives': [a for a in ('DIM-1', 'DIM1', 'dimensions')
                         if os.path.isdir(os.path.join(world, a))],
            'thumb': thumb if has_thumb else None,
            'area': area,
            'files': files,
            'unpackedBytes': unpacked,
            'warnings': warnings,
        }
        _write_json(os.path.join(dest, 'meta.json'), meta)
        idx['maps'][map_id] = meta
        if not idx.get('defaultId'):
            idx['defaultId'] = map_id
        _write_json(index_path(root), idx)
        return dto(meta)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def doctor(root=None):
    root = root or default_root()
    out = {'root': root, 'exists': os.path.isdir(root), 'maps': 0,
           'defaultId': None, 'problems': []}
    if not os.path.isdir(root):
        return out
    ensure_dirs(root)
    idx = read_index(root)
    out['defaultId'] = idx.get('defaultId')
    out['maps'] = len(idx['maps'])
    for mid, meta in idx['maps'].items():
        if not ID_RE.match(str(mid)):
            out['problems'].append('索引里有非法 id：%r' % mid)
            continue
        p = meta.get('savePath')
        if not p or not os.path.isdir(p):
            out['problems'].append('%s 的存档目录不见了：%s' % (mid, p))
        elif not os.path.isfile(os.path.join(p, 'level.dat')):
            out['problems'].append('%s 的存档缺 level.dat' % mid)
    return out


# ------------------------------------------------------------------ 命令行

def _emit(obj, as_json):
    if as_json:
        print(json.dumps(obj, ensure_ascii=False))
        return 0
    if obj.get('ok'):
        data = obj.get('data')
        if isinstance(data, dict) and 'maps' in data:
            print('地图库：%s（%d 张）' % (data.get('root'), len(data['maps'])))
            for m in data['maps']:
                flag = '*' if m.get('id') == data.get('defaultId') else ' '
                print('  %s %-16s %-24s %s' % (flag, m.get('id'), (m.get('name') or '')[:24],
                                               m.get('area') or ''))
        else:
            print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0
    err = obj.get('error', {})
    print('错误 %s：%s' % (err.get('code'), err.get('message')), file=sys.stderr)
    return 1


def _run(fn, as_json, **kw):
    try:
        return _emit({'ok': True, 'data': fn(**kw)}, as_json)
    except MapError as exc:
        return _emit({'ok': False, 'error': exc.body()}, as_json)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        return _emit({'ok': False, 'error': {'code': 'map.internal',
                                             'message': '%s: %s' % (type(exc).__name__, exc),
                                             'params': {}}}, as_json)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    ap = argparse.ArgumentParser(prog='mc_maplib', description='背景地图库')
    ap.add_argument('action', nargs='?', default='list',
                    choices=['import', 'list', 'get', 'rename', 'delete', 'default',
                             'doctor', 'resolve'])
    ap.add_argument('--root', default=None)
    ap.add_argument('--zip', default=None)
    ap.add_argument('--job', default=None, help='JSON 任务文件（由 Node 侧写，免转义）')
    ap.add_argument('--id', default=None)
    ap.add_argument('--name', default=None)
    ap.add_argument('--sha256', default=None)
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args(argv)

    if a.job:
        job = _read_json(a.job, None) or {}
        a.root = job.get('root') or a.root
        a.zip = job.get('zip') or a.zip
        a.name = job.get('name') if job.get('name') is not None else a.name
        a.id = job.get('id') or a.id
        a.sha256 = job.get('sha256') or a.sha256
        a.source = job.get('sourceFilename')
    else:
        a.source = None

    # 库根目录一律转成绝对路径：存档路径要原样交给 mc_shoot / Blender，
    # 相对路径会在子进程（cwd=py/）里解析错，表现为「地图记录还在但存档目录不见了」。
    root = os.path.abspath(a.root or default_root())
    if a.action == 'import':
        if not a.zip:
            return _emit({'ok': False, 'error': {'code': 'map.no-file',
                                                 'message': '缺少 --zip', 'params': {}}}, a.json)
        return _run(import_zip, a.json, zip_path=a.zip, name=a.name, root=root,
                    sha256=a.sha256, source_filename=getattr(a, 'source', None))
    if a.action == 'list':
        return _run(list_maps, a.json, root=root)
    if a.action == 'get':
        return _run(get_map, a.json, map_id=a.id, root=root)
    if a.action == 'resolve':
        return _run(lambda **kw: dict(zip(('savePath', 'meta'), resolve_world(**kw))),
                    a.json, map_id=a.id, root=root)
    if a.action == 'rename':
        return _run(rename_map, a.json, map_id=a.id, name=a.name, root=root)
    if a.action == 'delete':
        return _run(delete_map, a.json, map_id=a.id, root=root)
    if a.action == 'default':
        return _run(set_default, a.json, map_id=a.id, root=root)
    if a.action == 'doctor':
        return _run(doctor, a.json, root=root)
    return 1


if __name__ == '__main__':
    sys.exit(main())
