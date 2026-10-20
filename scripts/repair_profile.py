# -*- coding: utf-8 -*-
r"""修一个被补丁层搞坏的 DSH profile（不用重装 DSH）

症状：双击 DSH 打不开 / 窗口一闪就没，命令行 `dsh` 报
    failed to parse overlay ...\cordis.patch.yml: YAMLException:
    end of the stream or a document separator is expected
或
    ...\cordis.patch.yml must be a top-level YAML array of loader patch entries

原因：profile 的 cordis.patch.yml（补丁层）必须是**一份**一维 YAML 列表。
两种最常见的坏法，都能让 loadProfile 抛异常、应用直接起不来：
  1) 模板里的 `[]` 占位没删，后面又追加了 `- id: ...` 块 → 一份文档里两个顶层元素；
  2) 文件被清成只剩注释 / 空文件 → 解析成 null，不再是数组。

这个脚本只做行级修复，不重排用户的条目：
    - 把和其它条目共存的 `[]` 占位行注释成 `# []`
    - 文件里一条条目都不剩时，补回 `[]`
    - 写前备份（.bak-<时间戳>），写后用 PyYAML 复查能解析成一维列表

用法：
    python scripts/repair_profile.py                 # 只体检，不改文件
    python scripts/repair_profile.py --apply         # 真修（会先备份）
    python scripts/repair_profile.py --profile desktop --apply
    python scripts/repair_profile.py --restore --apply   # 用最新的 .bak-* 覆盖回去
"""
import argparse
import glob
import os
import re
import shutil
import sys
import time

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PLACEHOLDER_ONLY_RE = re.compile(r'^([ \t]*)\[[ \t]*\][ \t]*(#.*)?$', re.M | re.U)


def dsh_home():
    return os.environ.get('DSH_HOME') or os.path.join(os.path.expanduser('~'), '.dsh')


def profiles_dir():
    return os.path.join(dsh_home(), 'profiles')


def parse(text):
    """返回 (ok, data_or_error)。没有 PyYAML 时 ok=None。"""
    try:
        import yaml
    except ImportError:
        return None, '没有 PyYAML，装一个：python -m pip install pyyaml'
    try:
        data = yaml.safe_load(text)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
    if data is None:
        return False, '顶层是空文档（DSH 要求一维列表，空列表写 `[]`）'
    if not isinstance(data, list):
        return False, '顶层不是列表（解析出 %s）' % type(data).__name__
    return True, data


def has_top_level_entry(text):
    return any(ln.startswith('- ') for ln in text.split('\n'))


def repair_text(text):
    """尽量把补丁层修成合法的一维列表；返回 (新文本, 做了哪些修复的说明列表)。"""
    notes = []
    new = text

    # 坏法 1：`[]` 占位和真实条目共存
    placeholders = [m for m in PLACEHOLDER_ONLY_RE.finditer(new)]
    if placeholders and has_top_level_entry(new):
        new = PLACEHOLDER_ONLY_RE.sub(lambda m: (m.group(1) or '') + '# []', new)
        notes.append('把 %d 处 `[]` 占位注释成 `# []`（它和块条目共存 = 非法 YAML）' % len(placeholders))

    # 坏法 2：只剩注释 / 空文件
    if not has_top_level_entry(new):
        stripped = [ln for ln in new.split('\n')
                    if ln.strip() and not ln.strip().startswith('#')]
        if not stripped:
            if re.search(r'^[ \t]*#[ \t]*\[[ \t]*\][ \t]*$', new, re.M | re.U):
                new = re.sub(r'^([ \t]*)#[ \t]*\[[ \t]*\][ \t]*$', r'\1[]', new, flags=re.M | re.U)
                notes.append('把 `# []` 还原成 `[]`（没有条目时必须留下空列表）')
            else:
                new = new.rstrip('\n') + '\n\n[]\n'
                notes.append('补回 `[]`（文件里一条条目都没有，null 不是数组）')

    return new, notes


def backup(path):
    stamp = time.strftime('%Y%m%d-%H%M%S')
    dst = '%s.bak-%s' % (path, stamp)
    shutil.copy2(path, dst)
    print('    备份: %s' % os.path.basename(dst))
    return dst


def newest_backup(path):
    cands = sorted(glob.glob(path + '.bak-*'))
    return cands[-1] if cands else None


def handle_profile(name, pdir, apply_changes, restore):
    yml = os.path.join(pdir, 'cordis.patch.yml')
    print('\n[%s] %s' % (name, yml))
    if not os.path.exists(yml):
        print('  没有 cordis.patch.yml，跳过')
        return 0

    if restore:
        bak = newest_backup(yml)
        if bak is None:
            print('  没有 .bak-* 备份，没法还原')
            return 1
        print('  用备份还原: %s' % os.path.basename(bak))
        if apply_changes:
            backup(yml)
            shutil.copy2(bak, yml)
        ok, info = parse(open(yml, encoding='utf-8').read())
        print('  还原后解析: %s' % ('OK' if ok else info))
        return 0 if ok else 1

    with open(yml, encoding='utf-8') as f:
        text = f.read()

    ok, info = parse(text)
    if ok is None:
        print('  ! %s' % info)
        return 2
    if ok:
        print('  OK：%d 个顶层条目 %s'
              % (len(info), [e.get('id') for e in info if isinstance(e, dict)]))
        return 0

    print('  坏了：%s' % info)
    new_text, notes = repair_text(text)
    if new_text == text:
        print('  ！这个坏法脚本不会自动修，先手工备份再改；报错原文贴给作者')
        return 1
    for n in notes:
        print('  - ' + n)

    ok2, info2 = parse(new_text)
    if not ok2:
        print('  ！修完还是解析不了：%s' % info2)
        print('    这个文件先备份留证，别继续改')
        return 1
    print('  修完解析: OK，%d 个顶层条目 %s'
          % (len(info2), [e.get('id') for e in info2 if isinstance(e, dict)]))

    if apply_changes:
        backup(yml)
        with open(yml, 'w', encoding='utf-8', newline='\n') as f:
            f.write(new_text)
        print('  已写入')
    else:
        print('  （没落盘，加 --apply 才会写）')
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description='修被补丁层搞坏的 DSH profile')
    ap.add_argument('--profile', help='只处理这个 profile（默认全部）')
    ap.add_argument('--apply', action='store_true', help='真的写文件（默认只体检）')
    ap.add_argument('--restore', action='store_true', help='用最新的 .bak-* 备份还原')
    args = ap.parse_args(argv)

    base = profiles_dir()
    if not os.path.isdir(base):
        print('找不到 profile 目录：%s\n（设 DSH_HOME 指向 DSH 数据目录，例如 C:\\Users\\你\\.dsh）' % base)
        return 2

    names = [args.profile] if args.profile else sorted(
        n for n in os.listdir(base) if os.path.isdir(os.path.join(base, n)))
    print('DSH 数据目录: %s' % dsh_home())
    print('模式: %s' % ('写入' if args.apply else '只体检（加 --apply 才写）'))

    worst = 0
    for name in names:
        rc = handle_profile(name, os.path.join(base, name), args.apply, args.restore)
        worst = max(worst, rc)

    print('\n完成。补丁层修好后重启 DSH 即可；仍打不开就再跑一次，把上面的报错贴出来。')
    return worst


if __name__ == '__main__':
    sys.exit(main())
