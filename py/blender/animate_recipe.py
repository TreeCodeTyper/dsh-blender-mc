# -*- coding: utf-8 -*-
"""通用动画驱动 —— 读一份 JSON 配方，交给 anim_kit 搭出来。

这个文件只有壳，不含任何动画知识：
    blender -b <封面.blend> --python animate_recipe.py -- <输出目录> [总帧数] [fps] [预览帧] [配方]

配方怎么找（按顺序）：
    argv[4]（可以是绝对路径，也可以是 presets 里的名字，如 yujian）
    DSH_ANIM_RECIPE 环境变量

**加一支新动画 = 往 py/blender/presets/ 放一个 .json，不用写 Python。**
"""
import os
import sys

import bpy

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import anim_kit

argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
OUT = os.path.abspath(argv[0])
os.makedirs(OUT, exist_ok=True)
TOTAL = int(argv[1]) if len(argv) > 1 else None
FPS = int(argv[2]) if len(argv) > 2 else None
PREVIEW = argv[3] if len(argv) > 3 else ''
RECIPE = argv[4] if len(argv) > 4 else os.environ.get('DSH_ANIM_RECIPE', '')


def resolve_recipe(name):
    if not name:
        raise SystemExit('没给配方：argv[4] 或 DSH_ANIM_RECIPE 都空')
    if os.path.isabs(name) and os.path.exists(name):
        return name
    if os.path.exists(name):
        return os.path.abspath(name)
    base = name if name.endswith('.json') else name + '.json'
    cand = os.path.join(HERE, 'presets', base)
    if os.path.exists(cand):
        return cand
    raise SystemExit('找不到配方 %r（找过 %s）' % (name, cand))


recipe = anim_kit.load_recipe(resolve_recipe(RECIPE))
res = anim_kit.build(recipe, OUT, TOTAL, FPS)

if PREVIEW:
    frames = [int(x) for x in PREVIEW.split(',') if x.strip()]
    anim_kit.render_preview(res, frames)
else:
    print('\n[8] 用 blender -b 渲染序列（不在本脚本内做）')
    print('    blender -b "%s" -a' % res['blend'])

anim_kit.write_log(OUT)
