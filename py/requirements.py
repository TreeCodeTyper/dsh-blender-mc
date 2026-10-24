# -*- coding: utf-8 -*-
"""requirements —— 检查运行条件，缺什么就给出【明确的安装指引】

设计原则
--------
"没找到" 是一句没用的话。缺东西时必须告诉用户四件事：

    ① 这是什么、为什么需要
    ② 没有它会怎样（能不能降级继续用）
    ③ 去哪下载（给准确链接，不给错的）
    ④ 怎么装（具体到命令或 Blender 里的菜单路径）

另外把「必需」和「可选」分清楚 —— 缺可选的只降级、不阻塞，
免得用户以为跑不起来。
"""
import os
import shutil
import subprocess
import sys

# ────────────────────────────────────────────────────────────
# 一切需要的东西
#   required=True  → 缺了直接跑不了
#   required=False → 缺了只是少个功能，会明确说明少了什么
# ────────────────────────────────────────────────────────────
REQUIREMENTS = {
    # ── 运行环境 ──
    'python_deps': {
        'label': 'Python 依赖（opencv-python + numpy）',
        'why': 'OpenCV 用来审图（判断朝向/接地/曝光/构图），numpy 是它的依赖',
        'without': '跑不了。审图闭环是本插件的核心，没有它就只能出图不检查',
        'required': True,
        'where': 'PyPI',
        'how': [
            'pip install opencv-python numpy',
            '',
            '# 如果 pip 不在 PATH（Windows 常见）就用完整路径：',
            r'C:\\Python314\\python.exe -m pip install opencv-python numpy',
            '',
            '# 国内下载慢可以换镜像：',
            'pip install opencv-python numpy -i https://pypi.tuna.tsinghua.edu.cn/simple',
        ],
        'verify': 'python -c "import cv2, numpy; print(cv2.__version__, numpy.__version__)"',
    },
    'blender': {
        'label': 'Blender',
        'why': '渲染引擎。本插件负责"把创意翻译成 Blender 能懂的东西"，实际的导入/材质/渲染/动画全由 Blender 做',
        'without': '跑不了',
        'required': True,
        'where': 'https://www.blender.org/download/',
        'how': [
            '① 去上面的网址下 Windows 安装包（.msi），双击装完即可',
            '② 插件会自动在这些位置找：',
            r'     C:\\Program Files\\Blender Foundation\\Blender <版本>\\blender.exe',
            r'     D:\\SteamLibrary\\steamapps\\common\\Blender\\blender.exe',
            '   PATH 里的 blender',
            '③ 装在别处就显式指定：',
            '     python py/cli.py --blender "D:/某处/blender.exe" doctor',
            '   或者设环境变量 BLENDER',
        ],
        'verify': 'blender --version',
        'note': '版本建议 4.2 LTS 以上（本插件在 5.2 LTS 上验证过）。',
    },

    # ── Blender 侧 ──
    'mcprep': {
        'label': 'MCprep 附加组件',
        'why': '方块材质处理。存档导出的 OBJ 里材质名就是 MC 贴图名，MCprep 会把它们'
               '变成能渲染的 PBR 材质，并自动做【生物群系染色】'
               '（草和树叶的贴图是灰度的，绿色是运行时乘上去的）',
        'without': '世界会渲染成灰白或纯色 —— 草地不会变绿，贴图也可能丢失。'
                   '0.2.0 起会自动走「内置材质兜底」（贴图 + 群系染色 + 调平材质），'
                   '能出可用的图，但草的层次不如 MCprep 细',
        'required': True,
        'where': 'https://github.com/Moo-Ack-Productions/MCprep',
        'how': [
            '方法 A（推荐，插件代装）：把 MCprep 的 zip 放到任一资源目录，然后',
            '     python py/cli.py setup <资源目录>',
            '   插件会解压到 Blender 的 addons 目录（路径是问 Blender 要的）',
            '',
            '方法 B（手动）：Blender → 编辑 → 偏好设置 → 附加组件 →',
            '     右上角下拉 → 从磁盘安装 → 选那个 zip → 勾选启用',
        ],
        'verify': 'Blender → 偏好设置 → 附加组件 → 搜 "MCprep"',
        'note': 'GPL 许可。本插件不附带它，需自行下载。',
    },

    # ── 外部资产（可选）──
    'rig': {
        'label': 'BPS 角色骨架',
        'why': '放置角色用。带 184 根骨骼、手部骨（Hand.R/L）供挂手持物品，'
               '还有 IK/FK 切换和表情系统',
        'without': '只出【风景空镜】，画面里没有角色。世界部分的渲染完全不受影响',
        'required': False,
        'where': 'https://archive.blackplasma.xyz/assets/bps-character-rig/',
        'how': [
            '① 从上面的网址下载 BPS Character Rig',
            '② 解压后丢进工作区的资源目录（或任意目录），然后',
            '     python py/cli.py setup <那个目录>',
            '',
            '★ 版本选择很重要：插件会自动优先选 **V4.0.9** 而不是 4.0.10 BETA1 ——',
            '  BETA1 的 mesh head 是平的（16 顶点），渲染时会 Z-fighting 闪烁。',
        ],
        'verify': 'python py/cli.py doctor',
        'note': '第三方作品，本插件不附带。',
    },
    'item': {
        'label': 'Minecraft 物品库（Item Rig）',
        'why': '角色手持物品用。里面是拆好的方块和物品模型，本插件用其中的 '
               '"Sword: Diamond" 挂到角色的 Hand.R 骨骼上',
        'without': '角色空手。其余一切正常',
        'required': False,
        'where': '搜索 "Cycles Minecraft Rig BSS Edit" （作者 bss 的整合包）',
        'how': [
            '① 下载整包后解压，里面有很多 .blend',
            '② 只需要其中的 Minecraft_Item_Rig.blend（约 15.7 MB，整包 551 MB）',
            '③ 让插件自己找：',
            '     python py/cli.py setup <解压目录>',
            '   它会自动跳过 _Old_Textures 版本',
        ],
        'verify': 'python py/cli.py doctor',
        'note': '第三方作品，本插件不附带。',
    },
    'textures': {
        'label': 'MC 方块贴图目录',
        'why': '存档里有些方块来自模组，贴图找不到时用这个目录兜底回退',
        'without': '模组方块的贴图可能变成品红（Blender 的缺失贴图色）',
        'required': False,
        'where': '从你自己的 Minecraft 客户端提取',
        'how': [
            '① 找到客户端的版本 jar（.minecraft/versions/<版本>/<版本>.jar）',
            '② 用解压工具打开，取出 assets/minecraft/textures/block/ 整个目录',
            '③ 丢进工作区任意位置，然后 python py/cli.py setup <那个目录>',
            '',
            '   （mod 的贴图在各自 mod 的 jar 里，同样的办法取 resources/.../block/）',
        ],
        'verify': 'python py/cli.py doctor',
        'note': 'Mojang 版权素材，本插件不附带。',
    },
    'ffmpeg': {
        'label': 'ffmpeg',
        'why': '把渲染出的 PNG 序列帧转成 MP4 / GIF',
        'without': '动画仍然能渲染出帧序列，只是要你自己用别的工具合成视频。'
                   '渲染那一步不受影响',
        'required': False,
        'where': 'https://www.gyan.dev/ffmpeg/builds/  （Windows 常用构建）\n'
                 'https://ffmpeg.org/download.html  （官方下载页）',
        'how': [
            '① 下 "release essentials" 那个包，解压',
            '② 只要 bin/ffmpeg.exe（本插件不用 ffplay，那个 98 MB 可以删）',
            '③ 指定路径（二选一）：',
            '     设环境变量：set FFMPEG=D:\\某处\\ffmpeg.exe',
            '     或在 cordis.patch.yml 的 config 里写 ffmpeg: D:\\某处\\ffmpeg.exe',
            '④ 让插件记住：python py/cli.py setup <解压目录>',
        ],
        'verify': 'ffmpeg -version',
        'note': '本插件不附带（96 MB 的可执行文件没必要在插件里再放一份）。',
    },
}


# ────────────────────────────────────────────────────────────
# 检测
# ────────────────────────────────────────────────────────────
def _has_module(name):
    try:
        m = __import__(name)
        return getattr(m, '__version__', '?')
    except Exception:
        return None


def _find_blender(explicit=None):
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from pipeline import find_blender
        return find_blender(explicit)
    except Exception:
        # ⚠️ 坑 79：pipeline 顶层原来还牵着 cv2（见 pipeline.py 坑 77）。这里一句
        #   `return None` 会把「缺 opencv」讲成「没装 Blender」，指引整套都是错的。
        #   退回不依赖任何第三方库的粗略搜索 —— 宁可少报，不可误报。
        return _find_blender_fallback(explicit)


def _find_blender_fallback(explicit=None):
    """不 import 任何第三方库的 Blender 搜索（pipeline 不可 import 时兜底）。"""
    if explicit and os.path.isfile(explicit):
        return explicit
    for env in ('BLENDER', 'BLENDER_EXE', 'DSH_MC_BLENDER'):
        p = os.environ.get(env)
        if p and os.path.isfile(p):
            return p
    return shutil.which('blender')


def _looks_like_addon(path):
    """目录里有真文件才算装上 —— 空的 MCprep_addon/ 是卸载残留，不是插件。"""
    try:
        if not os.path.isdir(path):
            return False
        return any(f.endswith('.py') or f == '__init__.py'
                   for f in os.listdir(path))
    except OSError:
        return False


def _has_mcprep(blender_exe):
    if not blender_exe:
        return None
    for addons in _addon_dirs():
        if not os.path.isdir(addons):
            continue
        try:
            names = os.listdir(addons)
        except OSError:
            continue
        for d in names:
            if d.lower().startswith('mcprep') and _looks_like_addon(os.path.join(addons, d)):
                return True
    # 也问一下 Blender 本人（可能装在别的地方）
    expr = ("import addon_utils;"
            "print('MCPREP_OK' if any('MCprep' in m.__name__ "
            "for m in addon_utils.modules()) else 'MCPREP_NO')")
    try:
        r = subprocess.run([blender_exe, '-b', '--factory-startup',
                            '--python-expr', expr],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=300)
        return 'MCPREP_OK' in (r.stdout or '')
    except Exception:
        return None


def probe_mcprep_operator(blender_exe=None):
    """真探一次 MCprep 的 prep_materials 操作符是否可用。

    ⚠️ 装目录里有 ≠ addon 注册成功。实测：addon 注册时要写 tracker.json，
    写盘被拒会中断 register()，结果 bpy.ops.mcprep.* 全部消失 ——
    只看目录会误报"已安装"，渲染时才发现草是灰的。
    返回 True / False / None（无法判断）
    """
    exe = _find_blender(blender_exe)
    if not exe:
        return None
    expr = (
        "import bpy, addon_utils\n"
        "try:\n"
        "    addon_utils.enable('MCprep_addon', default_set=True)\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    bpy.ops.mcprep.prep_materials.get_rna_type()\n"
        "    print('MCPREP_OP_OK')\n"
        "except Exception as e:\n"
        "    print('MCPREP_OP_FAIL', e)\n"
    )
    try:
        r = subprocess.run([exe, '-b', '--factory-startup',
                            '--python-expr', expr],
                           capture_output=True, text=True, encoding='utf-8',
                           errors='replace', timeout=180)
    except Exception:
        return None
    out = (r.stdout or '') + (r.stderr or '')
    if 'MCPREP_OP_OK' in out:
        return True
    if 'MCPREP_OP_FAIL' in out:
        return False
    return None


def _addon_dirs():
    out = []
    appdata = os.environ.get('APPDATA')
    if appdata:
        base = os.path.join(appdata, 'Blender Foundation', 'Blender')
        if os.path.isdir(base):
            for v in os.listdir(base):
                out.append(os.path.join(base, v, 'scripts', 'addons'))
    return out


def check_all(blender=None):
    """检查全部条件。返回 [(key, ok, detail, spec)]，ok 为 True/False/None(无法判断)"""
    res = []
    cv2v = _has_module('cv2')
    npv = _has_module('numpy')
    res.append(('python_deps', bool(cv2v and npv),
                f'cv2 {cv2v or "缺失"} / numpy {npv or "缺失"}', REQUIREMENTS['python_deps']))

    b = _find_blender(blender)
    res.append(('blender', bool(b), b or '找不到', REQUIREMENTS['blender']))

    mp = _has_mcprep(b)
    res.append(('mcprep', bool(mp),
                '已安装' if mp else ('未安装' if mp is False else '无法判断（Blender 不可用）'),
                REQUIREMENTS['mcprep']))

    # 外部资产走 pipeline 的解析
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from pipeline import resolve_assets
        assets = resolve_assets()
    except Exception:
        assets = {}
    for key, label in (('rig', 'rig'), ('item', 'item'),
                       ('textures', 'textures'), ('ffmpeg', 'ffmpeg')):
        v = assets.get(key)
        ok = bool(v and os.path.exists(v))
        res.append((key, ok, v if ok else '没找到', REQUIREMENTS[key]))
    return res


def format_report(blender=None, verbose=False):
    """人类可读的检查报告。缺什么就把完整安装指引打出来。"""
    res = check_all(blender)
    # ★ MCprep 装目录在 ≠ 操作符可用：真跑一次 Blender 探测（约 5-10 秒）。
    #   探测失败只加一行警告，不把整项判死 —— 0.2.0 起有内置材质兜底。
    op_ok = None
    if any(k == 'mcprep' and ok for k, ok, _, _ in res):
        op_ok = probe_mcprep_operator(blender)
    lines = []
    missing_required = []
    missing_optional = []

    lines.append('=' * 70)
    lines.append('运行条件检查')
    lines.append('=' * 70)
    for key, ok, detail, spec in res:
        mark = '✅' if ok else ('❌' if spec['required'] else '⚠️ ')
        lines.append(f'  {mark} {spec["label"]:<34} {detail}')
        if key == 'mcprep' and ok and op_ok is False:
            lines.append('      ⚠️  prep_materials 操作符没注册上（addon 加载中断）——'
                         ' 渲染会自动走内置材质兜底，草色略平')
        if not ok:
            (missing_required if spec['required'] else missing_optional).append(
                (key, spec))

    if missing_required:
        lines.append('')
        lines.append('=' * 70)
        lines.append(f'❌ 缺 {len(missing_required)} 项必需的东西，跑不起来：')
        lines.append('=' * 70)
        for key, spec in missing_required:
            lines += _block(spec)
    if missing_optional:
        lines.append('')
        lines.append('=' * 70)
        lines.append(f'⚠️  缺 {len(missing_optional)} 项可选的东西。'
                     f'不会报错，只是少相应功能：')
        lines.append('=' * 70)
        for key, spec in missing_optional:
            lines += _block(spec)

    if not missing_required and not missing_optional:
        lines.append('')
        lines.append('✅ 全部齐了，可以直接用：')
        lines.append('   python py/cli.py shoot <存档> --area x0,x1,z0,z1 --out ./出图')
    elif not missing_required:
        lines.append('')
        lines.append('✅ 必需的都齐了，现在就能出【风景空镜】：')
        lines.append('   python py/cli.py shoot <存档> --area x0,x1,z0,z1 --out ./出图')
        lines.append('   把上面缺的可选项补上，就能出带角色的封面和动画。')
    return '\n'.join(lines), res


def _block(spec):
    out = ['']
    out.append(f'  ┌─ {spec["label"]}')
    out.append(f'  │  【是什么】{spec["why"]}')
    out.append(f'  │  【没它会怎样】{spec["without"]}')
    # where 可能有多行（多个链接），每行都要跟框线，否则排版会断
    wlines = str(spec['where']).splitlines()
    out.append(f'  │  【去哪下】{wlines[0]}')
    for w in wlines[1:]:
        out.append(f'  │            {w.strip()}')
    out.append(f'  │  【怎么装】')
    for h in spec['how']:
        out.append(f'  │      {h}' if h else '  │')
    if spec.get('verify'):
        out.append(f'  │  【验证】{spec["verify"]}')
    if spec.get('note'):
        out.append(f'  │  【注意】{spec["note"]}')
    out.append(f'  └─')
    return out


def require(keys, blender=None):
    """在真正开跑前检查。缺必需的项就抛出带完整指引的异常。

    ★ 关键是【在动手之前】就拦住 —— 而不是渲染到一半报个看不懂的错。
    """
    res = {k: (ok, d, spec) for k, ok, d, spec in check_all(blender)}
    bad = []
    for k in keys:
        if k not in res:
            continue
        ok, detail, spec = res[k]
        if not ok:
            bad.append((k, spec, detail))
    if not bad:
        return True
    hard = [b for b in bad if b[1]['required']]
    soft = [b for b in bad if not b[1]['required']]
    lines = []
    if hard:
        lines.append('❌ 缺少必需组件，无法继续：')
    for k, spec, detail in hard + soft:
        tag = '必需' if spec['required'] else '可选'
        lines.append('')
        lines.append(f'[{tag}] {spec["label"]}  （当前状态: {detail}）')
        lines.append(f'  为什么需要: {spec["why"]}')
        lines.append(f'  没有会怎样: {spec["without"]}')
        lines.append(f'  去哪下载  : {spec["where"]}')
        lines.append(f'  怎么安装  :')
        for h in spec['how']:
            lines.append(f'      {h}' if h else '')
        if spec.get('verify'):
            lines.append(f'  装完验证  : {spec["verify"]}')
    if soft:
        lines.append('')
        lines.append('（上面标"可选"的可以不装，只是少对应功能）')
    if hard:
        raise RuntimeError('\n'.join(lines))
    return True


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='检查运行条件并给出安装指引')
    ap.add_argument('--blender', default=None)
    a = ap.parse_args()
    txt, _ = format_report(a.blender)
    print(txt)
