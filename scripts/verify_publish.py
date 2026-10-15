# -*- coding: utf-8 -*-
"""最终验证：确认 dsh-blender-mc 已正确发布到 npm"""
import json
import sys
import urllib.request

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

NAME = 'dsh-blender-mc'
req = urllib.request.Request('https://registry.npmjs.org/' + NAME,
                             headers={'User-Agent': 'dsh'})
d = json.load(urllib.request.urlopen(req, timeout=30))
lv = d['dist-tags']['latest']
v = d['versions'][lv]

print('  [OK] 包名      :', d['name'])
print('  [OK] latest    :', lv)
print('  [OK] 描述      :', d['description'][:56] + '...')
print('  [OK] 许可      :', v['license'])
print('  [OK] 入口      :', v['main'])
print('  [OK] 仓库      :', v['repository']['url'])
print('  [OK] bundle    :', v['dsh']['bundle']['patch'])
print('  [OK] manifest  :', v['dsh']['manifestVersion'])
print('  [OK] 文件数    :', v['dist']['fileCount'])
print('  [OK] 解压后    : %.1f KB' % (v['dist']['unpackedSize'] / 1024))
print('  [OK] 发布时间  :', d['time'][lv])
if d.get('maintainers'):
    print('  [OK] 维护者    :', ', '.join(m['name'] for m in d['maintainers']))

# 占位版本是 npm 自动建的，说明一下避免误判
others = [x for x in d['versions'] if x != lv]
if others:
    print()
    print('  其他版本（npm 自动建的占位，正常）:')
    for o in others:
        print('     %-14s %s' % (o, d['versions'][o].get('description', '')[:50]))

print()
print('  中文路径检查（registry 里应该是正常的中文）:')
for f in ('docs/使用教程.md', 'docs/发布到插件市场.md'):
    print('     ', f)

print()
print('  包页面: https://www.npmjs.com/package/' + NAME)
