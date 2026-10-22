# -*- coding: utf-8 -*-
"""dsh-blender-mc 命令行入口

    python cli.py doctor                        自检（Blender / 依赖 / 骨架 / 物品库）
    python cli.py extract <资源目录> --out <目录>          提取 Bedrock 模型 → OBJ
    python cli.py scan   <存档> --area x0,x1,z0,z1        在存档里找机位
    python cli.py shoot  <存档> --area ... --out <目录>    出封面（含审图闭环）
    python cli.py anim   <存档> --area ... --out <目录>    出 10 秒动画
    python cli.py check  <图...>                          只审图
"""
import argparse
import os
import sys

# ⚠️ 坑 66：Windows 控制台默认是 GBK，打印中文以外的字符（✅ ❌ ⚠️ 这些）
#   会直接 UnicodeEncodeError 崩掉。实测被这个绊了两次。
#   与其要求用户记得设 PYTHONIOENCODING=utf-8，不如这里自己强制过去。
for _s in (sys.stdout, sys.stderr):
    try:
        if (_s.encoding or '').lower().replace('-', '') != 'utf8':
            _s.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, 'blender'))


def _area(s):
    v = [int(x) for x in s.split(',')]
    if len(v) != 4:
        raise argparse.ArgumentTypeError('area 要 4 个数: x0,x1,z0,z1')
    return tuple(v)


def cmd_setup(a):
    """装配外部资产：从本地已有的资源目录搬到 vendor/，并把 MCprep 装进 Blender"""
    from setup_assets import assemble, default_sources, WANTED
    srcs = a.sources
    if not srcs:
        srcs = default_sources()
        print(f'（没给资源目录，默认扫这些:）')
        for s in srcs:
            print(f'    {s}')
    if not srcs:
        print('❌ 没给资源目录，也没找到默认的 pack/')
        print('   用法: python cli.py setup <资源目录> [<资源目录2> ...]')
        print('\n需要的东西:')
        for k, s in WANTED.items():
            mark = '必需' if s['required'] else '可选'
            print(f'   [{mark}] {k:9s} {s["desc"]}')
        return 1
    m = assemble(srcs, blender=a.blender, dry=a.dry_run,
                 copy=not a.link, progress=print)
    n = len(m.get('assets', {}))
    print(f'\n装配了 {n} 项资产 → {m["vendor"]}')
    return 0 if n else 1


def cmd_doctor(a):
    """检查运行条件。缺什么就把【完整安装指引】打出来。"""
    from requirements import format_report
    txt, res = format_report(a.blender)
    print(txt)
    return 0 if all(ok for _, ok, _, spec in res if spec['required']) else 1


def cmd_extract(a):
    """把资源目录里的 Bedrock 几何批量转成 OBJ，交给 Blender 摆放。"""
    import json
    from extract import extract
    m = extract(a.assets, a.out, keyword=a.filter, anim=a.anim,
                anim_name=a.anim_name, list_only=a.list, progress=print)
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(m, f, ensure_ascii=False, indent=2)
        print(f'\n已写 {a.json}')
    return 0 if m.get('count') else 1


def cmd_scan(a):
    from pipeline import step_scan
    y = tuple(int(v) for v in str(a.y).split(','))
    shots = step_scan(a.save, a.area, y=y, radius=a.radius,
                      top_n=a.top, progress=print)
    print(f'\n推荐机位 {len(shots)} 个（开阔度 / 坐标 / 地面Y / 方位角 / 平坦格数）:')
    for s in shots:
        print("  开阔度%.2f  (%5d,%5d) Y=%d  az=%3d°  平坦%4d"
              % (s['openness'], s['x'], s['z'], s['y'], s['az'], s['flat']))
    if a.json:
        import json
        with open(a.json, 'w', encoding='utf-8') as f:
            json.dump(shots, f, ensure_ascii=False, indent=2)
        print(f'\n已写 {a.json}')
    return 0


def cmd_shoot(a):
    from pipeline import shoot
    params = {}
    if a.yaw is not None:
        params['yaw'] = a.yaw
    if a.cam_az is not None:
        params['cam_az'] = a.cam_az
    if a.dist is not None:
        params['dist'] = a.dist
    if a.sky is not None:
        params['sky_strength'] = a.sky
    if a.view_transform:
        params['view_transform'] = a.view_transform
    if a.sink is not None:
        params['sink'] = a.sink
    if a.res:
        params['res'] = a.res
    if a.samples:
        params['samples'] = a.samples
    center = tuple(int(v) for v in a.center.split(',')) if a.center else None
    r = shoot(a.save, a.area, a.out, blender=a.blender, params=params,
              center=center, radius=a.radius, max_attempts=a.attempts,
              target_score=a.target, progress=print)
    print('\n' + '=' * 66)
    print(f"最终: {r['verdict']} {r['score']} 分   {r['png']}")
    print(f"尺寸: {r.get('res_actual') or '?'}（请求 {r.get('res_requested') or '默认'}）")
    print(f"机位 ({r['center'][0]},{r['center'][1]})   "
          f"yaw={r['params']['yaw']}° 方位={r['params']['cam_az']}° "
          f"距离={r['params']['dist']:.1f}")
    for i, s in enumerate(r['issues'], 1):
        print(f'  ⚠️  [{i}] {s}')
    return 0


def cmd_anim(a):
    from pipeline import animate, find_blender
    b = find_blender(a.blender)
    r = animate(b, a.blend, a.out, total=a.frames, fps=a.fps,
                preview=a.preview, progress=print)
    print(f"\n动画工程: {r['blend']}")
    if r.get('frames'):
        print(f"帧序列: {r['frames']} 帧 @ {r['fps']}fps → {r['dir']}")
        print('\n转视频:')
        print(f'  ffmpeg -framerate {r["fps"]} -i "{r["dir"]}\\frame_%04d.png" '
              f'-c:v libx264 -crf 18 -pix_fmt yuv420p -movflags +faststart out.mp4')
    return 0


def cmd_check(a):
    import json
    from vision_check import check_image
    for p in a.images:
        r = check_image(p)
        if a.json:
            print(json.dumps({'path': p, **r}, ensure_ascii=False, indent=2))
        else:
            print(f"\n{'='*66}\n{p}\n{'='*66}")
            print(f"  {r.get('verdict')}  {r.get('score')} 分")
            for i, s in enumerate(r.get('issues', []), 1):
                print(f'  ⚠️  [{i}] {s}')
    return 0


def cmd_map(a):
    """背景地图库：上传的存档 zip → 解压识别入库（逻辑全在 maplib.py）"""
    import maplib
    return maplib.main(a.rest)


def main():
    ap = argparse.ArgumentParser(
        prog='dsh-blender-mc',
        description='Minecraft 存档 → Blender 封面 / 动画（含自动审图闭环）')
    ap.add_argument('--blender', default=None, help='blender.exe 路径')
    sub = ap.add_subparsers(dest='cmd', required=True)

    d = sub.add_parser('doctor', help='自检')
    d.set_defaults(func=cmd_doctor)

    st = sub.add_parser('setup', help='装配外部资产（从本地资源目录搬到 vendor/）')
    st.add_argument('sources', nargs='*', help='资源目录，如 pack/')
    st.add_argument('--dry-run', action='store_true', help='只看会搬什么，不实际搬')
    st.add_argument('--link', action='store_true', help='用硬链接代替复制')
    st.set_defaults(func=cmd_setup)

    e = sub.add_parser('extract', help='提取 Bedrock 模型（.geo.json + 贴图）→ OBJ')
    e.add_argument('assets', help='资源包 / 模组解包目录（会递归找 *.geo.json）')
    e.add_argument('--out', required=True, help='输出目录')
    e.add_argument('--filter', default=None, help='只转路径里含这个关键词的模型')
    e.add_argument('--anim', default=None, help='animation.json（可选，套一段静态姿势）')
    e.add_argument('--anim-name', default=None, help='要套用的动画名，缺省取第一条')
    e.add_argument('--list', action='store_true', help='只列出会转什么，不动文件')
    e.add_argument('--json', default=None, help='把清单另存为 json')
    e.set_defaults(func=cmd_extract)

    s = sub.add_parser('scan', help='在存档里找机位')
    s.add_argument('save')
    s.add_argument('--area', type=_area, required=True)
    s.add_argument('--y', default='50,200')
    s.add_argument('--radius', type=int, default=15)
    s.add_argument('--top', type=int, default=12)
    s.add_argument('--json', default=None)
    s.set_defaults(func=cmd_scan)

    h = sub.add_parser('shoot', help='出封面')
    h.add_argument('save')
    h.add_argument('--area', type=_area, required=True)
    h.add_argument('--out', required=True)
    h.add_argument('--center', default=None, help='x,z —— 不给就自动找')
    h.add_argument('--radius', type=int, default=44)
    h.add_argument('--yaw', type=float, default=None)
    h.add_argument('--cam-az', type=float, default=None)
    h.add_argument('--dist', type=float, default=None)
    h.add_argument('--sky', type=float, default=None)
    h.add_argument('--sink', type=float, default=None)
    h.add_argument('--res', default=None, help='输出尺寸，如 1920x1080（默认 1280x720）')
    h.add_argument('--samples', type=int, default=None, help='EEVEE 采样数，默认 256')
    h.add_argument('--view-transform', default=None)
    h.add_argument('--attempts', type=int, default=3)
    h.add_argument('--target', type=int, default=85)
    h.set_defaults(func=cmd_shoot)

    n = sub.add_parser('anim', help='出动画（10 秒杀戮光环）')
    n.add_argument('blend', help='封面工程 .blend')
    n.add_argument('--out', required=True)
    n.add_argument('--frames', type=int, default=240)
    n.add_argument('--fps', type=int, default=24)
    n.add_argument('--preview', default=None, help='只渲这些帧，如 1,60,120')
    n.set_defaults(func=cmd_anim)

    c = sub.add_parser('check', help='只审图')
    c.add_argument('images', nargs='+')
    c.add_argument('--json', action='store_true')
    c.set_defaults(func=cmd_check)

    mp = sub.add_parser('map', help='背景地图库（存档 zip 入库 / 列表 / 改名 / 删除）')
    mp.add_argument('rest', nargs=argparse.REMAINDER,
                    help='import|list|get|resolve|rename|delete|default|doctor + 各自参数')
    mp.set_defaults(func=cmd_map)

    a = ap.parse_args()
    return a.func(a)


if __name__ == '__main__':
    sys.exit(main())
