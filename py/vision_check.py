# -*- coding: utf-8 -*-
"""vision_check —— 渲完自动审图（OpenCV）

为什么需要它
------------
手工调试渲染时最耗时的不是"渲"，是"渲完看不出问题"：
背影当成正面、死白当成正常、脚没踩地、剑没握住……全靠人眼抓。
这个模块用 OpenCV 把那些"一眼就该看出来"的问题自动判掉。

设计原则：**几何告诉我们该看哪里，CV 告诉我们那里是什么。**
调用方（Blender 侧）知道角色在 3D 里的位置和相机参数，能算出
角色在画面上的预期外接框；本模块只在这个框里做局部判断，
比全图盲扫稳得多。不给框时退化为按颜色自动找主体。

检查项
------
  朝向      正面 / 背面 / 侧面     —— 头部区域里的皮肤色占比
  接地      脚底和地面是否接触     —— 外接框底边外一条带里的地面连续性
  曝光      死白 / 死黑 / 动态范围 —— 全图直方图
  光源      是否看得出布光         —— 地面区域的明暗方差 + 投影方向性
  构图      主体大小 / 位置 / 留白 —— 外接框与画面的比例关系

用法::

    from vision_check import check_image
    r = check_image('render.png')                  # 自动找主体
    r = check_image('render.png', subject_box=(x0,y0,x1,y1))
    print(r['verdict'], r['issues'])
"""
import math

import cv2
import numpy as np


# ─────────────────────────────────────────────────────────────
# ⚠️ 坑 62：Windows 下 cv2.imread / imwrite 【不支持中文路径】，
#    直接传路径会静默返回 None（或写不出去），报
#        [WARN] cv::findDecoder imread_('...'): can't open/read file
#    本项目的工作目录全是中文（"AI自媒体工作区"），所以必须绕开：
#        读: np.fromfile(path, uint8) → cv2.imdecode
#        写: cv2.imencode → ndarray.tofile
# ─────────────────────────────────────────────────────────────
def imread_u(path, flags=cv2.IMREAD_COLOR):
    """读图，支持中文/Unicode 路径"""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError as e:
        raise FileNotFoundError(f'读不到文件: {path} ({e})')
    if buf.size == 0:
        raise FileNotFoundError(f'文件是空的: {path}')
    img = cv2.imdecode(buf, flags)
    if img is None:
        raise ValueError(f'不是可识别的图像: {path}')
    return img


def imwrite_u(path, img):
    """写图，支持中文/Unicode 路径"""
    ext = '.' + path.rsplit('.', 1)[-1] if '.' in path else '.png'
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        raise ValueError(f'编码失败: {path}')
    buf.tofile(path)
    return True

# ── Steve 皮肤的 HSV 范围（OpenCV: H 0-179, S/V 0-255）──
SKIN_LO = np.array([5, 40, 90], np.uint8)
SKIN_HI = np.array([28, 190, 245], np.uint8)
# 青色衬衫
CYAN_LO = np.array([78, 70, 70], np.uint8)
CYAN_HI = np.array([105, 255, 255], np.uint8)
# 蓝色裤子
BLUE_LO = np.array([105, 80, 60], np.uint8)
BLUE_HI = np.array([135, 255, 255], np.uint8)
# ★ Steve 的头发：深棕色。和皮肤色的区分点是【明度】——
#   皮肤 V 高（90~245），头发 V 低（20~110）。
HAIR_LO = np.array([0, 40, 20], np.uint8)
HAIR_HI = np.array([25, 200, 110], np.uint8)


def _mask(img, lo, hi):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    m = cv2.inRange(hsv, lo, hi)
    # 开运算去掉噪点
    k = np.ones((3, 3), np.uint8)
    return cv2.morphologyEx(m, cv2.MORPH_OPEN, k, iterations=1)


def find_subject(img):
    """按颜色找角色外接框。返回 ((x0,y0,x1,y1), mask, area) 或 None。

    ⚠️ 坑 63：第一版直接把 皮肤∪青衬衫∪蓝裤子 做 15×15 闭运算×2 再取最大连通块，
    结果【海面和天空被当成角色】：
      · 海是饱和蓝，和裤子色域重叠；
      · 大核闭运算把大片背景连成一块；
      · 包围盒横跨整个画面右侧 → "朝向"对每张图都判成背面。

    正确判据：**只有角色有皮肤色**（天空/海/草都没有）。
    所以逐连通块统计里面的皮肤像素数，皮肤数为 0 的一律丢掉。
    """
    skin = _mask(img, SKIN_LO, SKIN_HI)
    body = _mask(img, CYAN_LO, CYAN_HI) | _mask(img, BLUE_LO, BLUE_HI)
    m = skin | body
    # 小核闭运算：只把角色自身各部件连起来，不把背景连进来
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8), iterations=1)

    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None
    H, W = img.shape[:2]
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < (H * W) * 0.0015:
            continue
        if w > W * 0.75 or h > H * 0.9:      # 横跨大半画面 → 是背景不是角色
            continue
        nskin = int(np.count_nonzero(skin[y:y + h, x:x + w]))
        if nskin < 20:                        # ★ 没有皮肤 → 不是角色
            continue
        score = nskin * 10 + area             # 皮肤多 + 块大 者优先
        if best is None or score > best[0]:
            best = (score, x, y, w, h, area)
    if best is None:
        return None
    _, x, y, w, h, area = best
    return (int(x), int(y), int(x + w), int(y + h)), m, area


def check_facing(img, box):
    """朝向：头部区域的【皮肤色 vs 头发色】占比。

    ⚠️ 坑 64：第一版取主体高度的【上 35%】当"头部"，把肩膀和手臂也框进来了
    —— 手臂本来就是皮肤色，于是背影图的"皮肤占比"被抬到 0.30，
    判成正面，PASS 92 放行了一张纯背影图（实测踩到）。

    MC 角色比例是 头 8px / 全身 32px，**头只占身高 25%**。
    所以只取顶部 22%（留余量避开肩膀），并且同时算头发色交叉验证：
      正面 / 3/4 → 头部有脸（皮肤多、头发相对少）
      背面       → 头部全是头发（皮肤几乎没有、头发多）
    """
    x0, y0, x1, y1 = box
    h = y1 - y0
    hy1 = y0 + max(4, int(h * 0.22))          # ★ 只取顶部 22% = 头部
    head = img[max(0, y0):hy1, max(0, x0):x1]
    if head.size == 0:
        return {'facing': 'unknown', 'skin_ratio': 0.0}
    sm = _mask(head, SKIN_LO, SKIN_HI)
    hm = _mask(head, HAIR_LO, HAIR_HI)
    n = max(1, head.shape[0] * head.shape[1])
    skin = float(np.count_nonzero(sm)) / n
    hair = float(np.count_nonzero(hm)) / n

    if skin > 0.20 and hair < 0.55:
        f = 'front'
    elif skin > 0.09:
        f = 'three-quarter'
    elif hair > 0.22:
        f = 'back'
    else:
        f = 'unknown'
    return {'facing': f, 'skin_ratio': round(skin, 3),
            'hair_ratio': round(hair, 3)}
    return {'facing': f, 'skin_ratio': round(ratio, 3)}


def check_ground_contact(img, box, band=0.10):
    """接地：外接框底边【下方】那条带里，颜色是否和角色【旁边】的地面一致。

    悬浮时，脚底下方会露出更远的地面/天空（颜色和近处地面不同）。
    """
    x0, y0, x1, y1 = box
    H, W = img.shape[:2]
    w = x1 - x0
    bh = max(3, int((y1 - y0) * band * 0.35))
    under = img[min(H - 1, y1 + 1):min(H, y1 + 1 + bh), max(0, x0):min(W, x1)]
    side_l = img[max(0, y0):y1, max(0, x0 - w // 2):max(1, x0)]
    side_r = img[max(0, y0):y1, min(W - 1, x1):min(W, x1 + w // 2)]
    if under.size == 0 or side_l.size == 0 or side_r.size == 0:
        return {'contact': 'unknown'}
    mu = under.reshape(-1, 3).mean(0)
    ms = np.vstack([side_l.reshape(-1, 3), side_r.reshape(-1, 3)]).mean(0)
    d = float(np.linalg.norm(mu - ms))
    # 色差越小 → 脚底下的东西和旁边地面越像 → 越像踩在地上
    if d < 22:
        c = 'grounded'
    elif d < 45:
        c = 'borderline'
    else:
        c = 'floating?'
    return {'contact': c, 'color_delta': round(d, 1),
            'under_rgb': tuple(int(v) for v in mu[::-1]),
            'side_rgb': tuple(int(v) for v in ms[::-1])}


def check_exposure(img):
    """曝光：死白/死黑比例 + 动态范围"""
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h = cv2.calcHist([g], [0], None, [256], [0, 256]).ravel()
    n = g.size
    blown = float(h[250:].sum()) / n          # 近白
    crushed = float(h[:6].sum()) / n          # 近黑
    p1, p50, p99 = (float(np.percentile(g, q)) for q in (1, 50, 99))
    rng = p99 - p1
    return {'blown_ratio': round(blown, 4), 'crushed_ratio': round(crushed, 4),
            'p1': p1, 'p50': p50, 'p99': p99, 'dynamic_range': round(rng, 1)}


def check_lighting(img, box):
    """布光是否看得出来：角色周围地面区域的明暗方差 + 是否有暗部（投影）"""
    x0, y0, x1, y1 = box
    H, W = img.shape[:2]
    w, h = x1 - x0, y1 - y0
    # 取角色下方一片地面
    gx0, gx1 = max(0, x0 - w), min(W, x1 + w)
    gy0, gy1 = min(H - 1, y1 + int(h * 0.15)), min(H, y1 + int(h * 0.9))
    ground = img[gy0:gy1, gx0:gx1]
    if ground.size < 100:
        return {'has_shading': None}
    g = cv2.cvtColor(ground, cv2.COLOR_BGR2GRAY)
    std = float(g.std())
    dark = float((g < g.mean() * 0.75).sum()) / g.size
    return {'ground_std': round(std, 1), 'ground_dark_ratio': round(dark, 4),
            'has_shading': bool(std > 6.0)}


def check_composition(img, box):
    """构图：主体占比、水平位置、头顶留白"""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = box
    bh = (y1 - y0) / H
    cx = ((x0 + x1) / 2) / W
    headroom = y0 / H
    return {'subject_height_ratio': round(bh, 3),
            'subject_center_x': round(cx, 3),
            'headroom_ratio': round(headroom, 3),
            'subject_on_left': cx < 0.5}


def check_image(path, subject_box=None, verbose=False):
    """主入口：返回 {verdict, score, issues, details}"""
    img = imread_u(path)
    if img is None:
        return {'ok': False, 'error': f'读不到图: {path}'}
    H, W = img.shape[:2]

    auto = None
    if subject_box is None:
        found = find_subject(img)
        if found:
            auto, _, _ = found
        subject_box = auto

    det = {'size': [W, H], 'subject_box': subject_box}
    issues = []
    score = 100.0

    det['exposure'] = check_exposure(img)
    ex = det['exposure']
    if ex['blown_ratio'] > 0.06:
        issues.append(f"过曝：{ex['blown_ratio']*100:.1f}% 的像素接近死白"
                      f"（建议降天空强度/灯光，或换 Filmic/AgX）")
        score -= 25
    if ex['p1'] > 40 and ex['p99'] < 220:
        issues.append(f"动态范围偏窄（p1={ex['p1']:.0f} p99={ex['p99']:.0f}），画面发灰")
        score -= 10
    if ex['crushed_ratio'] > 0.25 and ex['p50'] < 45:
        issues.append("整体偏暗")
        score -= 15

    if subject_box:
        det['facing'] = check_facing(img, subject_box)
        f = det['facing']['facing']
        if f == 'back':
            issues.append(f"看到的是【背面】（头部皮肤占比仅 "
                          f"{det['facing']['skin_ratio']*100:.1f}%）"
                          f"—— 角色朝向要转过来")
            score -= 30

        det['contact'] = check_ground_contact(img, subject_box)
        c = det['contact']['contact']
        if c == 'floating?':
            issues.append(f"疑似【脚没踩地】：脚底下方色差 "
                          f"{det['contact']['color_delta']}"
                          f"（脚下方 {det['contact']['under_rgb']} vs "
                          f"旁边地面 {det['contact']['side_rgb']}）")
            score -= 20
        elif c == 'borderline':
            issues.append(f"接地可疑（色差 {det['contact']['color_delta']}）")
            score -= 8

        det['lighting'] = check_lighting(img, subject_box)
        if det['lighting'].get('has_shading') is False:
            issues.append(f"看不出布光：角色周围地面灰阶标准差仅 "
                          f"{det['lighting']['ground_std']}"
                          f"（太均匀 = 平光，多半是天空占满了动态范围）")
            score -= 18

        det['composition'] = check_composition(img, subject_box)
        co = det['composition']
        if co['subject_height_ratio'] < 0.14:
            issues.append(f"主体太小（占画面高度 {co['subject_height_ratio']*100:.0f}%），"
                          f"建议拉近或加长焦")
            score -= 12
        elif co['subject_height_ratio'] > 0.95:
            issues.append(f"主体太满（占画面高度 {co['subject_height_ratio']*100:.0f}%），"
                          f"可能有裁切")
            score -= 10
        if co['headroom_ratio'] < 0.02:
            issues.append("头顶几乎没有留白，可能被切")
            score -= 12
    else:
        issues.append("没能在画面里找到角色（颜色和背景不匹配？）")
        score -= 30

    score = max(0, int(round(score)))
    verdict = 'PASS' if score >= 80 else ('WARN' if score >= 60 else 'FAIL')
    return {'ok': True, 'verdict': verdict, 'score': score,
            'issues': issues, 'details': det, 'auto_box': auto}


# ────────────────────────────────────────────────────────────
# 命令行
# ────────────────────────────────────────────────────────────
def _main():
    import argparse
    import json
    ap = argparse.ArgumentParser(description='渲染结果自动审图')
    ap.add_argument('images', nargs='+')
    ap.add_argument('--box', default=None, help='x0,y0,x1,y1（可选）')
    ap.add_argument('--json', action='store_true')
    ap.add_argument('--annotate', default=None, help='把主体框画出来另存到该目录')
    a = ap.parse_args()
    box = tuple(int(v) for v in a.box.split(',')) if a.box else None
    out = []
    for p in a.images:
        r = check_image(p, subject_box=box)
        out.append((p, r))
        if not a.json:
            print(f"\n{'='*66}")
            print(f"{p}")
            print(f"{'='*66}")
            print(f"  结论: {r.get('verdict')}  评分 {r.get('score')}")
            d = r.get('details', {})
            if d.get('subject_box'):
                print(f"  主体框: {d['subject_box']}   画面: {d['size']}")
            if d.get('facing'):
                print(f"  朝向: {d['facing']}")
            if d.get('contact'):
                print(f"  接地: {d['contact']}")
            if d.get('exposure'):
                e = d['exposure']
                print(f"  曝光: 死白 {e['blown_ratio']*100:.1f}%  死黑 "
                      f"{e['crushed_ratio']*100:.1f}%  动态范围 {e['dynamic_range']}")
            if d.get('lighting'):
                print(f"  布光: {d['lighting']}")
            if d.get('composition'):
                print(f"  构图: {d['composition']}")
            for i, s in enumerate(r.get('issues', []), 1):
                print(f"  ⚠️  [{i}] {s}")
        if a.annotate and r.get('details', {}).get('subject_box'):
            img = imread_u(p)
            x0, y0, x1, y1 = r['details']['subject_box']
            cv2.rectangle(img, (x0, y0), (x1, y1), (0, 0, 255), 2)
            col = {'PASS': (0, 200, 0), 'WARN': (0, 180, 255),
                   'FAIL': (0, 0, 255)}.get(r['verdict'], (255, 255, 255))
            cv2.putText(img, f"{r['verdict']} {r['score']}", (10, 34),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 2)
            import os
            os.makedirs(a.annotate, exist_ok=True)
            imwrite_u(os.path.join(a.annotate, os.path.basename(p)), img)
    if a.json:
        print(json.dumps([{'path': p, **r} for p, r in out],
                         ensure_ascii=False, indent=2))


if __name__ == '__main__':
    _main()
