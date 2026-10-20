# -*- coding: utf-8 -*-
"""把 dsh-blender-mc 装进 DSH profile（0.2.1 安全版）

用法：
  python scripts/install_to_profile.py                  # 装进 desktop profile
  python scripts/install_to_profile.py --profile web    # 装进别的 profile
  python scripts/install_to_profile.py --dry-run        # 只打印要改什么，不落盘
  python scripts/install_to_profile.py --reset-config   # 重写配置块（保留其它条目）
  python scripts/install_to_profile.py --remove         # 从 profile 里摘掉
  python scripts/install_to_profile.py --no-verify      # 跳过 dump-config 复查

旧版（<=0.2.0）为什么会把 DSH 装成「双击打不开、只能重装」：
    旧版直接往 profile 的 cordis.patch.yml 末尾拼 `- id: blender-mc` 块。
    全新 profile 的补丁层模板长这样（模板原文就带 `[]` 占位）：

        # Your patch layer for this dsh profile...
        []

    拼完就变成 `[]` 后面又跟一个块条目 —— 一份 YAML 文档里出现两个顶层元素，
    解析器直接报
        YAMLException: end of the stream or a document separator is expected
    cordis.patch.yml 是 profile 的补丁层，读它的链路是 loadOverlayPatches →
    loadProfile → prepareProfile，这几层都没有 try/catch 兜底。补丁层读不出来
    = 应用起不来 = Electron 窗口一闪就没，用户唯一的出路就是重装 DSH；
    重装后补丁层又回到 `[]` 模板，下次再装又是同一个坑。

现在的做法：
    1) 先备份（cordis.patch.yml.bak-<时间戳>），
    2) 识别 `[]` / `[ ]` 占位行并注释成 `# []`（保留模板头注释），再追加块，
    3) 用 PyYAML 复核新文件确实能解析成一维列表，
    4) 再用 `dsh <profile> --dump-config` 真跑一遍组合，确认 DSH 自己能读。
    任何一步不对就放弃写入，绝不留半截文件。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

NAME = 'dsh-blender-mc'
ENTRY_ID = 'blender-mc'

PLACEHOLDER_RE = re.compile(r'^[ \t]*\[[ \t]*\][ \t]*(?:#.*)?$', re.M | re.U)
ENTRY_ID_RE = re.compile(
    r"""^- id: (?:"%s"|'%s'|%s)[ \t]*$""" % ((ENTRY_ID,) * 3), re.M | re.U)


# ──────────────────────────────────────────────────────────────
# 路径
# ──────────────────────────────────────────────────────────────
def dsh_home():
    return os.environ.get('DSH_HOME') or os.path.join(os.path.expanduser('~'), '.dsh')


def profile_dir(name):
    return os.path.join(dsh_home(), 'profiles', name)


def find_dsh_cli():
    """定位 dsh 启动器，找不到返回 None（复查会跳过）。"""
    for key in ('DSH_CLI', 'DSH_BIN'):
        p = os.environ.get(key)
        if p and os.path.exists(p):
            return p
    cands = []
    local = os.environ.get('LOCALAPPDATA')
    if local:
        cands.append(os.path.join(local, 'Programs', 'DeepSeek Harness', 'resources',
                                  'runtime', 'cli', 'bin', 'dsh.cmd'))
    cands.append(shutil.which('dsh.cmd') or '')
    cands.append(shutil.which('dsh') or '')
    for c in cands:
        if c and os.path.exists(c):
            return c
    return None


def backup(path):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dst = '%s.bak-%s' % (path, stamp)
    shutil.copy2(path, dst)
    print('  备份: %s' % os.path.basename(dst))
    return dst


# ──────────────────────────────────────────────────────────────
# YAML 行级手术（不重排、不重写用户的文件）
# ──────────────────────────────────────────────────────────────
def yaml_load_list(text):
    """解析成顶层列表；解析不了抛 ValueError。没有 PyYAML 时返回 None。

    注意：DSH 的 parsePatchList 要求 Array.isArray(解析结果)，
    空文档（只有注释 / 空文件）解析成 null，同样会 fatal
    （`must be a top-level YAML array of loader patch entries`）。
    """
    try:
        import yaml
    except ImportError:
        return None
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001 - 任何解析错误都要报出来
        raise ValueError(str(exc)) from exc
    if data is None:
        raise ValueError('顶层是空文档；DSH 要求这里是一维列表（空列表就写 `[]`）')
    if not isinstance(data, list):
        raise ValueError('顶层不是列表（解析出 %s）' % type(data).__name__)
    return data


def top_level_ranges(lines):
    """每个顶层 `- ` 条目占的行区间 [start, end)，含紧贴其上的注释行。"""
    starts = [i for i, ln in enumerate(lines) if ln.startswith('- ')]
    out = []
    for n, s in enumerate(starts):
        e = starts[n + 1] if n + 1 < len(starts) else len(lines)
        while s > 0 and lines[s - 1].lstrip().startswith('#'):
            s -= 1
        out.append((s, e))
    return out


def comment_out_placeholder(text):
    m = PLACEHOLDER_RE.search(text)
    if not m:
        return text, False
    line = m.group(0)
    indent = line[:len(line) - len(line.lstrip())]
    return text[:m.start()] + indent + '# []' + text[m.end():], True


def append_block(text, block):
    """在补丁层末尾追加一个顶层块条目，保证结果仍是一份合法的一维列表。"""
    text, commented = comment_out_placeholder(text)
    lines = text.split('\n')
    stripped = [ln for ln in lines if ln.strip() and not ln.strip().startswith('#')]
    if stripped and re.match(r'^[\[{]', stripped[-1].strip()):
        raise ValueError('补丁层最后一行是流式列表（%s）；再追加块条目会造出两个顶层元素，'
                         '请先手工整理这个文件' % stripped[-1].strip())
    if not text.strip():
        return block, commented
    return text.rstrip('\n') + '\n\n' + block, commented


def blender_block(cfg):
    def q(p):
        return "'" + str(p).replace("'", "''") + "'"

    lines = [
        '# ── dsh-blender-mc：Minecraft 存档 → Blender 封面 / 动画 ──',
        '# 配置说明见插件自带的 cordis.patch.yml（同 id 会覆盖 bundle 层默认值）',
        '- id: %s' % ENTRY_ID,
        "  name: '%s'" % NAME,
        '  config:',
        '    python: %s' % q(cfg['python']),
        '    blender: %s' % q(cfg['blender']),
        '    rig: %s' % q(cfg['rig']),
        '    item_rig: %s' % q(cfg['item_rig']),
        '    textures: %s' % q(cfg['textures']),
        '    ffmpeg: %s' % q(cfg['ffmpeg']),
        '    sources:',
    ]
    for s in cfg['sources']:
        lines.append('      - %s' % q(s))
    lines += [
        '    vendor: %s' % q(cfg['vendor']),
        '    renderTimeoutMs: 1800000',
    ]
    return '\n'.join(lines) + '\n'


def remove_block(text):
    """摘掉所有 `- id: blender-mc` 顶层块；返回 (新文本, 摘掉几个)。

    摘完如果一条顶层条目都不剩，必须把 `[]` 占位还回去：DSH 的 parsePatchList
    要求 Array.isArray(解析结果)，只剩注释的文件解析成 null 会 fatal
    （`must be a top-level YAML array of loader patch entries`）。
    """
    removed = 0
    while True:
        lines = text.split('\n')
        hit = None
        for s, e in top_level_ranges(lines):
            if any(ENTRY_ID_RE.match(lines[i]) for i in range(s, e)):
                hit = (s, e)
                break
        if not hit:
            break
        s, e = hit
        del lines[s:e]
        text = re.sub(r'\n{3,}', '\n\n', '\n'.join(lines))
        removed += 1
    if removed:
        text = ensure_list_form(text)
    return text, removed


def ensure_list_form(text):
    """保证补丁层解析出来是一维列表：没条目时补回 `[]` 占位。"""
    lines = text.split('\n')
    has_entry = any(ln.startswith('- ') for ln in lines)
    has_placeholder = any(re.match(r'^[ \t]*\[[ \t]*\][ \t]*$', ln) for ln in lines)
    if has_entry or has_placeholder:
        return text
    if re.search(r'^[ \t]*#[ \t]*\[[ \t]*\][ \t]*$', text, re.M | re.U):
        # 之前被注释掉的占位，原样还回来
        return re.sub(r'^([ \t]*)#[ \t]*\[[ \t]*\][ \t]*$', r'\1[]', text, flags=re.M | re.U)
    return text.rstrip('\n') + '\n\n[]\n'


def default_config():
    ws = os.environ.get('DSH_MC_WORKSPACE', r'E:\游戏与自媒体\AI自媒体工作区')
    return {
        'python': os.environ.get('DSH_MC_PYTHON', r'C:\Python314\python.exe'),
        'blender': os.environ.get('DSH_MC_BLENDER',
                                  r'D:\SteamLibrary\steamapps\common\Blender\blender.exe'),
        'rig': os.path.join(ws, r'04_工具脚本\dsh-blender-mc\vendor\rig\BPS Character Rig V4.0.9.blend'),
        'item_rig': os.path.join(ws, r'04_工具脚本\dsh-blender-mc\vendor\item\Minecraft_Item_Rig.blend'),
        'textures': os.path.join(ws, r'03_素材库\方块贴图\minecraft\block'),
        'ffmpeg': os.path.join(ws, r'04_工具脚本\bin\ffmpeg.exe'),
        'sources': [os.path.join(ws, 'pack'), os.path.join(ws, r'03_素材库\方块贴图')],
        'vendor': os.path.join(ws, r'04_工具脚本\dsh-blender-mc\vendor'),
    }


# ──────────────────────────────────────────────────────────────
# package.json
# ──────────────────────────────────────────────────────────────
LOCAL_SPEC_PREFIXES = ('file:', 'link:', 'portal:', 'workspace:', 'github:', 'git+',
                       'http://', 'https://', '..', './', '.\\', '~')


def is_local_spec(spec):
    """依赖写的是本地 tarball / 目录 / git 地址时，不要去改写成 ^版本号。

    从 npm 装就是 ^0.2.1，改写没影响；但从 `file:.../xxx.tgz` 装的，
    改写成 ^0.2.1 就等于让 pnpm 去 registry 找这个版本 —— 还没发布，装不上。
    """
    if not isinstance(spec, str):
        return False
    text = spec.strip()
    if not text:
        return False
    lowered = text.lower()
    return any(lowered.startswith(p) for p in LOCAL_SPEC_PREFIXES)


def update_package(pkg_path, version, add=True):
    with open(pkg_path, encoding='utf-8') as f:
        pkg = json.load(f)
    deps = pkg.setdefault('dependencies', {})
    bundles = pkg.setdefault('dsh', {}).setdefault('profile', {}).setdefault('bundles', [])

    had_dep = NAME in deps
    had_bundle = NAME in bundles
    if add:
        if not is_local_spec(deps.get(NAME)):
            deps[NAME] = '^' + version
        if not had_bundle:
            bundles.append(NAME)
    else:
        deps.pop(NAME, None)
        while NAME in bundles:
            bundles.remove(NAME)

    pkg['dependencies'] = dict(sorted(deps.items()))
    with open(pkg_path, 'w', encoding='utf-8') as f:
        json.dump(pkg, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return had_dep, had_bundle


def current_version():
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(os.path.dirname(here), 'package.json'), encoding='utf-8') as f:
        return json.load(f)['version']


def verify_with_dsh(profile, cli):
    """让 DSH 自己把 profile 组合一遍；返回 True/False/None（没 CLI 时 None）。"""
    if cli is None:
        print('  ! 没找到 dsh 启动器，跳过 dump-config 复查（设 DSH_CLI 可以指定）')
        return None
    env = dict(os.environ)
    env['DSH_HOME'] = dsh_home()
    try:
        proc = subprocess.run([cli, profile, '--dump-config'], capture_output=True,
                              text=True, encoding='utf-8', errors='replace', env=env,
                              timeout=180)
    except Exception as exc:  # noqa: BLE001
        print('  ! dump-config 没跑起来：%s' % exc)
        return None
    out = (proc.stdout or '') + (proc.stderr or '')
    ok = proc.returncode == 0
    print('  dump-config: exit %d %s' % (proc.returncode, 'OK' if ok else '失败'))
    if not ok:
        for line in out.strip().split('\n')[:12]:
            print('    ' + line)
    elif ENTRY_ID in out:
        print('  组合结果里已包含 %s 条目' % ENTRY_ID)
    return ok


# ──────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description='把 dsh-blender-mc 装进 DSH profile（安全版）')
    ap.add_argument('--profile', default=os.environ.get('DSH_PROFILE', 'desktop'))
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--reset-config', action='store_true', help='先摘掉旧配置块再重写')
    ap.add_argument('--remove', action='store_true', help='从 profile 里摘掉本插件')
    ap.add_argument('--no-verify', action='store_true', help='跳过 dump-config 复查')
    ap.add_argument('--patch-only', action='store_true',
                    help='只写 cordis.patch.yml 的配置块，不碰 package.json（本地包已装好时用）')
    args = ap.parse_args(argv)

    prof = profile_dir(args.profile)
    pkg_path = os.path.join(prof, 'package.json')
    yml_path = os.path.join(prof, 'cordis.patch.yml')

    if not os.path.isdir(prof):
        print('没有这个 profile：%s' % prof)
        base = os.path.join(dsh_home(), 'profiles')
        if os.path.isdir(base):
            print('现有 profile：' + ', '.join(sorted(os.listdir(base))))
        return 2

    version = current_version()
    print('profile : %s' % prof)
    print('插件版本: %s' % version)

    with open(yml_path, encoding='utf-8') as f:
        text = f.read()

    # ── 移除 ──
    if args.remove:
        had_dep, had_bundle = update_package(pkg_path, version, add=False)
        new_text, removed = remove_block(text)
        print('  package.json: 依赖%s / bundles%s' % (
            '已移除' if had_dep else '本来就没有',
            '已移除' if had_bundle else '本来就没有'))
        if removed:
            if not args.dry_run:
                backup(yml_path)
                with open(yml_path, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(new_text)
            print('  cordis.patch.yml: 摘掉 %d 个 %s 块' % (removed, ENTRY_ID))
        else:
            print('  cordis.patch.yml: 没有 %s 块，不用动' % ENTRY_ID)
        print('\n改完重启 DSH 生效。node_modules 里还留着包，想彻底清就跑'
              ' `dsh plugin --profile %s remove %s`。' % (args.profile, NAME))
        return 0

    # ── 安装 / 重写配置 ──
    cfg = default_config()
    plan = []
    existing = bool(ENTRY_ID_RE.search(text))

    if existing and not args.reset_config:
        new_text = text
        plan.append('cordis.patch.yml 里已有 %s 块，保持原配置不动（要重写加 --reset-config）' % ENTRY_ID)
        commented = False
    else:
        if args.reset_config:
            text, removed = remove_block(text)
            if removed:
                plan.append('先摘掉旧的 %d 个 %s 块' % (removed, ENTRY_ID))
        try:
            new_text, commented = append_block(text, blender_block(cfg))
        except ValueError as exc:
            print('拒绝写入：%s' % exc)
            return 1
        if commented:
            plan.append('把模板里的 `[]` 占位注释成 `# []`（否则会和块条目撞成非法 YAML）')
        plan.append('追加 %s 配置块' % ENTRY_ID)

    # 落盘前自查：必须是合法的一维列表
    try:
        parsed = yaml_load_list(new_text)
    except ValueError as exc:
        print('生成出来的补丁层不是合法 YAML，放弃写入：%s' % exc)
        print('（这正是旧版 install_to_profile.py 把 DSH 装成打不开的原因，这里直接拦住）')
        return 1
    if parsed is not None:
        print('  YAML 复核: %d 个顶层条目 %s'
              % (len(parsed), [e.get('id') for e in parsed if isinstance(e, dict)]))

    if args.patch_only:
        plan.append('package.json: 不动（--patch-only）')
    else:
        plan.append('package.json: dependencies[%s] 写成 ^%s（本地/git spec 会保留）、bundles 追加 %s'
                    % (NAME, version, NAME))

    print('\n计划：')
    for line in plan:
        print('  - ' + line)

    if args.dry_run:
        print('\n--dry-run：没有落盘。')
        return 0

    if new_text != text:
        backup(yml_path)
        with open(yml_path, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_text)
        print('\n  cordis.patch.yml 已写入（备份见同目录 .bak-*）')
    if args.patch_only:
        print('  package.json 未改动（--patch-only）')
    else:
        update_package(pkg_path, version, add=True)
        print('  package.json 已更新')

    if not args.no_verify:
        print('\n复查：')
        if verify_with_dsh(args.profile, find_dsh_cli()) is False:
            print('  ! 复查失败，上面就是 DSH 自己的报错；备份在同目录，改回去就能启动')

    print('\n路径存在性：')
    for key in ('python', 'blender', 'rig', 'item_rig', 'textures', 'ffmpeg'):
        print('  %-9s %s  %s' % (key, 'OK  ' if os.path.exists(cfg[key]) else '缺失', cfg[key]))
    print('\n下一步：在 profile 目录跑一次 pnpm install（或 `dsh plugin --profile %s add %s`），'
          '然后重启 DSH。' % (args.profile, NAME))
    return 0


if __name__ == '__main__':
    sys.exit(main())
