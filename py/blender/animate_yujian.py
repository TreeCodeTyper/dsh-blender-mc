# -*- coding: utf-8 -*-
"""御剑飞天 —— 10 秒「立定蓄力 → 脱手化剑 → 踏剑冲天」

⚠️ 这里已经**没有动画代码**了。
动作、武器、特效、相机、灯光、渲染参数全部在 `presets/yujian.json` 里，
由通用的 `anim_kit.build()` 执行。这个文件只是为了兼容老调用（pipeline 里
`yujian → animate_yujian.py`）而保留的转发壳。

要改这支片子：改 presets/yujian.json。
要加一支新片子：新建 presets/<名字>.json，一行 Python 都不用写。
"""
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ.setdefault('DSH_ANIM_RECIPE', 'yujian')
runpy.run_path(os.path.join(HERE, 'animate_recipe.py'), run_name='__main__')
