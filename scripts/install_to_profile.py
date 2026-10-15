# -*- coding: utf-8 -*-
"""把 dsh-blender-mc 装进 DSH profile

改两处：
  package.json     dependencies 加包 + dsh.profile.bundles 加条目
  cordis.patch.yml 末尾追加插件配置块（含本机各路径）
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

PROF = os.path.join(os.environ['USERPROFILE'], '.dsh', 'profiles', 'desktop')
PKG = os.path.join(PROF, 'package.json')
YML = os.path.join(PROF, 'cordis.patch.yml')

NAME = 'dsh-blender-mc'
VER = '^0.1.1'

# ── 本机路径 ──────────────────────────────────────
WS = r'E:\游戏与自媒体\AI自媒体工作区'
CONFIG = {
    'python':   r'C:\Python314\python.exe',
    'blender':  r'D:\SteamLibrary\steamapps\common\Blender\blender.exe',
    'rig':      WS + r'\04_工具脚本\dsh-blender-mc\vendor\rig\BPS Character Rig V4.0.9.blend',
    'item_rig': WS + r'\04_工具脚本\dsh-blender-mc\vendor\item\Minecraft_Item_Rig.blend',
    'textures': WS + r'\03_素材库\方块贴图\minecraft\block',
    'ffmpeg':   WS + r'\04_工具脚本\bin\ffmpeg.exe',
    'sources':  [WS + r'\pack', WS + r'\03_素材库\方块贴图'],
    'vendor':   WS + r'\04_工具脚本\dsh-blender-mc\vendor',
}

# ── 1. package.json ──────────────────────────────
with open(PKG, encoding='utf-8') as f:
    pkg = json.load(f)

before_dep = NAME in pkg.setdefault('dependencies', {})
before_bnd = NAME in pkg.setdefault('dsh', {}).setdefault('profile', {}).setdefault('bundles', [])

pkg['dependencies'][NAME] = VER
if not before_bnd:
    pkg['dsh']['profile']['bundles'].append(NAME)

# 依赖按字母序排一下，和原文件风格一致
pkg['dependencies'] = dict(sorted(pkg['dependencies'].items()))

with open(PKG, 'w', encoding='utf-8') as f:
    json.dump(pkg, f, ensure_ascii=False, indent=2)
    f.write('\n')
print('  package.json 已更新')
print('    dependencies 加 %s: %s' % (NAME, '本来就有的' if before_dep else '新增'))
print('    bundles 加 %s: %s' % (NAME, '本来就有的' if before_bnd else '新增'))

# ── 2. cordis.patch.yml ──────────────────────────
with open(YML, encoding='utf-8') as f:
    yml = f.read()

if NAME in yml:
    print('  cordis.patch.yml 里已有 %s，跳过' % NAME)
else:
    def q(p):
        return "'" + str(p).replace("'", "''") + "'"

    lines = [
        '',
        '# ── dsh-blender-mc：Minecraft 存档 → Blender 封面 / 动画 ──',
        '# 配置说明见插件自带的 cordis.patch.yml',
        '- id: blender-mc',
        "  name: '%s'" % NAME,
        '  config:',
        "    python: %s" % q(CONFIG['python']),
        "    blender: %s" % q(CONFIG['blender']),
        "    rig: %s" % q(CONFIG['rig']),
        "    item_rig: %s" % q(CONFIG['item_rig']),
        "    textures: %s" % q(CONFIG['textures']),
        "    ffmpeg: %s" % q(CONFIG['ffmpeg']),
        '    sources:',
    ]
    for s in CONFIG['sources']:
        lines.append('      - %s' % q(s))
    lines += [
        "    vendor: %s" % q(CONFIG['vendor']),
        '    renderTimeoutMs: 1800000',
        '',
    ]
    if not yml.endswith('\n'):
        yml += '\n'
    yml += '\n'.join(lines)
    with open(YML, 'w', encoding='utf-8') as f:
        f.write(yml)
    print('  cordis.patch.yml 已追加配置块（%d 行）' % len(lines))

# ── 3. 复查 ──────────────────────────────────────
print()
with open(PKG, encoding='utf-8') as f:
    pkg2 = json.load(f)
print('  复查 package.json:')
print('    dependencies 里有 %s: %s' % (NAME, NAME in pkg2['dependencies']))
print('    bundles 里有 %s     : %s' % (NAME, NAME in pkg2['dsh']['profile']['bundles']))
print('    bundles 共 %d 条' % len(pkg2['dsh']['profile']['bundles']))

checks = [('rig', CONFIG['rig']), ('item_rig', CONFIG['item_rig']),
          ('textures', CONFIG['textures']), ('ffmpeg', CONFIG['ffmpeg']),
          ('blender', CONFIG['blender']), ('python', CONFIG['python'])]
print()
print('  路径存在性:')
for k, v in checks:
    print('    %-9s %s  %s' % (k, 'OK  ' if os.path.exists(v) else '缺失', v))
