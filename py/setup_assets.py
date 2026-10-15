# -*- coding: utf-8 -*-
"""setup_assets —— 从本地已有的资源包里"装配"插件运行所需的外部资产

为什么不直接打进插件包
----------------------
插件需要的外部资产全是**别人的东西**，各自有各自的许可：
  · MCprep         —— GPL，可再分发但要保留许可与出处
  · BPS Rig v4     —— BlenderPlasticStudio 的作品
  · Minecraft 素材 —— Mojang 的版权
把它们的副本塞进本插件再分发，既臃肿（整包 551 MB，实际只用 1 个 15.7 MB 文件）
也不合适。所以改成：**插件只带自己的代码，资产从用户本地已有的资源目录搬过来。**

同时顺手砍掉真正用不上的：
  · Mineways / MCSkin3D —— 本插件用纯 Python 直接读 .mca（py/anvil.py），
    不需要 Mineways 那套 GUI 导出流程；皮肤编辑也不在本插件的职责里。
  · ffmpeg 整包（96 MB）—— 只需要一个可执行文件，而且系统里往往已经有。

装配后的目录结构::

    dsh-blender-mc/
      vendor/
        manifest.json          记下每个资产的来源、大小、校验
        rig/BPS Character Rig V4.0.9.blend
        item/Minecraft_Item_Rig.blend
        bin/ffmpeg.exe         （可选，没有就用系统 PATH 里的）
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
# 装配产物的存放处。默认放插件自己的 vendor/；
# 用 DSH_MC_VENDOR 可以指到别处 —— 装成 npm 包时插件目录会被重装覆盖，
# 把 vendor/ 放到稳定的地方才不会被冲掉。
VENDOR = os.environ.get('DSH_MC_VENDOR') or os.path.join(os.path.dirname(HERE), 'vendor')

# 每项资产：目标相对路径、认文件名用的关键词、必需的候选名、说明
WANTED = {
    'rig': {
        'desc': 'BPS 角色骨架（放置角色时用）',
        'ext': '.blend',
        'match': lambda n: n.startswith('BPS') and 'Rig' in n,
        # ★ 4.0.10 BETA1 的 head 网格是【平的】（16 顶点，会 Z-fighting 闪烁），
        #   4.0.9 的是平滑的（14 顶点）。优先要 4.0.9。
        'prefer': ['BPS Character Rig V4.0.9.blend'],
        'avoid': ['BETA'],
        'required': True,
    },
    'item': {
        'desc': 'Minecraft 物品库（手持钻石剑等）',
        'ext': '.blend',
        'match': lambda n: 'Item_Rig' in n and 'Old_Textures' not in n,
        'prefer': ['Minecraft_Item_Rig.blend'],
        'avoid': ['Old_Textures'],
        'required': False,
    },
    'mcprep': {
        'desc': 'MCprep 附加组件（方块材质染色，必须装进 Blender）',
        'ext': '.zip',
        'match': lambda n: 'MCprep' in n,
        'prefer': [],
        'avoid': [],
        'required': True,
    },
    'ffmpeg': {
        'desc': 'ffmpeg（把帧序列转成 MP4/GIF）',
        'ext': '.exe',
        'match': lambda n: n.lower() == 'ffmpeg.exe',
        'prefer': [],
        'avoid': [],
        'required': False,
        # ★ 96 MB 的可执行文件没必要复制一份进插件 —— 那是系统工具不是资产。
        #   只把路径记进 manifest，用的时候按路径调。
        'no_copy': True,
    },
    'textures': {
        'desc': 'MC 方块贴图目录（缺失贴图时回退用）',
        'ext': None,          # 目录
        'match': None,
        'prefer': [],
        'avoid': [],
        'required': False,
    },
}


def _sha1(path, limit=8 * 1024 * 1024):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        read = 0
        while read < limit:
            b = f.read(min(1 << 20, limit - read))
            if not b:
                break
            h.update(b)
            read += len(b)
    return h.hexdigest()[:16]


def scan(dirs, max_depth=5, progress=None):
    """在给定目录里找所有想要的资产。返回 {kind: [路径,…]}"""
    found = {k: [] for k in WANTED}
    for d in dirs:
        if not os.path.isdir(d):
            continue
        base_depth = d.rstrip(os.sep).count(os.sep)
        for dp, dn, fn in os.walk(d):
            if dp.count(os.sep) - base_depth > max_depth:
                dn[:] = []
                continue
            # 别钻进巨大的缓存目录
            dn[:] = [x for x in dn if x not in ('.git', '__pycache__', 'node_modules')]
            low = dp.lower()
            # 方块贴图目录：路径含 minecraft 且直接躺着大量 png。
            # 第一版还要求路径里有 'textures'，结果漏掉了
            # 03_素材库\方块贴图\minecraft\block\ 这种命名（没有 textures 字样）。
            if 'minecraft' in low and not found['textures']:
                pngs = [f for f in fn if f.lower().endswith('.png')]
                if len(pngs) > 200:
                    found['textures'].append(dp)
            for f in fn:
                fl = f.lower()
                for kind, spec in WANTED.items():
                    if spec['match'] is None:
                        continue
                    ext = spec['ext']
                    if ext and not fl.endswith(ext):
                        continue
                    try:
                        if spec['match'](f):
                            p = os.path.join(dp, f)
                            if p not in found[kind]:
                                found[kind].append(p)
                    except Exception:
                        pass
            # 都找齐了就不必再走 —— 但判据必须覆盖【所有】种类。
            # ⚠️ 坑 65：第一版只检查了 rig/item/mcprep/ffmpeg 四样，
            #   pack/ 里这四样都有，于是扫描在【第一个目录】就提前返回，
            #   根本没走到 03_素材库\方块贴图\ —— 贴图永远找不到。
            if progress and all(found[k] for k in WANTED):
                return found
    return found


def choose(paths, prefer=None, avoid=None):
    """从候选里挑最好的：先按 prefer 精确匹配，再排除 avoid，最后取最小的"""
    if not paths:
        return None
    for name in (prefer or []):
        for p in paths:
            if os.path.basename(p) == name:
                return p
    pool = [p for p in paths
            if not any(a.lower() in os.path.basename(p).lower()
                       for a in (avoid or []))]
    if not pool:
        pool = paths
    # 同名的取路径最短的（更可能是主份而不是备份）
    pool.sort(key=lambda p: (len(p), os.path.getsize(p) if os.path.isfile(p) else 0))
    return pool[0]


def install_mcprep(blender_exe, zip_path, progress=print):
    """把 MCprep 解压装到 Blender 的 addons 目录。

    用 Blender 自己的脚本目录最稳 —— 不同版本/安装方式路径不一样，
    所以问 Blender 而不是猜。
    """
    expr = ("import bpy,os;"
            "print('ADDONS=' + bpy.utils.user_resource('SCRIPTS', path='addons'))")
    r = subprocess.run([blender_exe, '-b', '--factory-startup', '--python-expr', expr],
                       capture_output=True, text=True, encoding='utf-8',
                       errors='replace', timeout=300)
    addons = None
    for ln in (r.stdout or '').splitlines():
        if ln.startswith('ADDONS='):
            addons = ln.split('=', 1)[1].strip()
    if not addons:
        raise RuntimeError('问不出 Blender 的 addons 目录')
    os.makedirs(addons, exist_ok=True)
    progress(f'    Blender addons 目录: {addons}')

    # 已装过就跳过
    if os.path.isdir(os.path.join(addons, 'MCprep_addon')):
        progress('    MCprep_addon 已存在，跳过')
        return os.path.join(addons, 'MCprep_addon')

    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        # zip 里可能有顶层目录，判断一下
        tops = {n.split('/')[0] for n in names if '/' in n}
        progress(f'    解压 {len(names)} 个条目，顶层: {sorted(tops)[:4]}')
        z.extractall(addons)
    # 如果解出来是 MCprep_addon/ 就好；如果多了一层，往下找
    if not os.path.isdir(os.path.join(addons, 'MCprep_addon')):
        for dp, dn, fn in os.walk(addons):
            if os.path.basename(dp) == 'MCprep_addon':
                progress(f'    实际在 {dp}')
                return dp
        raise RuntimeError('解压后没找到 MCprep_addon 目录')
    return os.path.join(addons, 'MCprep_addon')


def assemble(sources, blender=None, progress=print, copy=True, dry=False):
    """主流程：扫描 → 挑选 → 搬到 vendor/ → 装 MCprep → 写 manifest"""
    os.makedirs(VENDOR, exist_ok=True)
    progress('[1/4] 扫描资源目录…')
    for s in sources:
        progress(f'    {s}  {"存在" if os.path.isdir(s) else "不存在"}')
    found = scan(sources, progress=progress)
    for k, v in found.items():
        if v:
            progress(f'    {k:9s} 找到 {len(v)} 个候选')

    manifest = {'vendor': VENDOR, 'assets': {}}
    progress('\n[2/4] 挑选并搬运…')
    picks = {}
    for kind, spec in WANTED.items():
        p = choose(found[kind], spec['prefer'], spec['avoid'])
        picks[kind] = p
        if p is None:
            if spec['required']:
                progress(f'    ❌ {kind:9s} 没找到 —— {spec["desc"]}')
            else:
                progress(f'    ⚠️  {kind:9s} 没找到（可选）—— {spec["desc"]}')
            continue
        if os.path.isdir(p):
            sz = sum(os.path.getsize(os.path.join(dp, f))
                     for dp, dn, fn in os.walk(p) for f in fn)
            progress(f'    ✅ {kind:9s} {p}  ({sz/1048576:.1f} MB)')
            manifest['assets'][kind] = {'src': p, 'size': sz, 'kind': 'dir'}
            continue
        sz = os.path.getsize(p)
        progress(f'    ✅ {kind:9s} {os.path.basename(p)}  ({sz/1048576:.1f} MB)')
        manifest['assets'][kind] = {'src': p, 'size': sz, 'sha1_16': _sha1(p)}

    if dry:
        progress('\n(--dry-run，不实际搬运)')
        return manifest

    for kind in ('rig', 'item', 'ffmpeg'):
        p = picks.get(kind)
        if not p:
            continue
        if WANTED[kind].get('no_copy'):
            manifest['assets'][kind]['dst'] = p        # 只记路径，不复制
            progress(f'    (不复制，只记路径) {p}')
            continue
        sub = {'rig': 'rig', 'item': 'item', 'ffmpeg': 'bin'}[kind]
        dst_dir = os.path.join(VENDOR, sub)
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, os.path.basename(p))
        if os.path.abspath(p) == os.path.abspath(dst):
            continue
        if copy:
            shutil.copy2(p, dst)
        else:
            try:
                os.link(p, dst)
            except OSError:
                shutil.copy2(p, dst)
        manifest['assets'][kind]['dst'] = dst
        progress(f'    → {dst}')

    if picks.get('textures'):
        manifest['assets']['textures']['dst'] = picks['textures']

    progress('\n[3/4] 安装 MCprep…')
    if blender and picks.get('mcprep'):
        try:
            d = install_mcprep(blender, picks['mcprep'], progress=progress)
            manifest['assets']['mcprep']['installed_to'] = d
        except Exception as e:
            progress(f'    ❌ 装 MCprep 失败: {e}')
            progress(f'       手动装：Blender → 编辑 → 偏好设置 → 附加组件 → '
                     f'从磁盘安装 → 选 {picks["mcprep"]}')
    else:
        progress('    （跳过，需要 --blender）')

    progress('\n[4/4] 写 manifest…')
    mf = os.path.join(VENDOR, 'manifest.json')
    with open(mf, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    progress(f'    {mf}')
    return manifest


def default_sources():
    """没给资源目录时的默认搜索位置。

    优先级：
      ① 环境变量 DSH_MC_SOURCES（DSH 插件从 config 映射过来，分号分隔）
      ② 从插件目录逐级往上找，遇到含 pack/ 或 03_素材库/ 的就当工作区根
      ③ 退回插件上一级（本地开发时的常见情况）

    ⚠️ 为什么不能只按固定层数往上推：
      本地开发时布局是 <工作区>/04_工具脚本/dsh-blender-mc/py —— 往上三层
      正好是工作区根。但 npm 安装后变成 <profile>/node_modules/dsh-blender-mc/py，
      往上三层是 profile 目录，在那儿找 pack/ 永远找不到，
      用户只会看到"贴图/骨架没找到"，不知道为什么。
      所以改成【按特征找】而不是【按层数找】。
    """
    out = []

    # ① 环境变量
    for p in os.environ.get('DSH_MC_SOURCES', '').split(';'):
        p = p.strip()
        if p and os.path.isdir(p) and p not in out:
            out.append(p)
    if out:
        return out

    # ② 逐级往上找工作区特征
    marks = ('pack',
             os.path.join('03_素材库', '方块贴图'),
             os.path.join('03_素材库', '方块贴图', 'minecraft', 'block'))
    d = HERE
    for _ in range(8):                     # 最多往上 8 层
        nd = os.path.dirname(d)
        if not nd or nd == d:
            break
        d = nd
        for rel in marks:
            p = os.path.join(d, rel)
            if os.path.isdir(p) and p not in out:
                out.append(p)
        if out:
            return out

    # ③ 退回插件上一级
    up = os.path.dirname(os.path.dirname(HERE))
    if os.path.isdir(up) and up not in out:
        out.append(up)
    return out


def load_manifest():
    """读 vendor/manifest.json，给 pipeline 用（配置留空时的兜底）"""
    mf = os.path.join(VENDOR, 'manifest.json')
    if not os.path.isfile(mf):
        return {}
    with open(mf, encoding='utf-8') as f:
        return json.load(f)


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='装配插件需要的外部资产')
    ap.add_argument('sources', nargs='*', help='资源目录（如 pack/）')
    ap.add_argument('--blender', default=None)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--link', action='store_true', help='用硬链接代替复制（省空间）')
    a = ap.parse_args()
    srcs = a.sources or default_sources()
    assemble(srcs, blender=a.blender, dry=a.dry_run,
             copy=not a.link, progress=print)
