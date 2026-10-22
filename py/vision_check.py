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
  接地      脚底和地面是否接触     —— 脚下/旁边地面的【色相】是否一致（阴影只压暗不改色相）
  曝光      死白 / 死黑 / 动态范围 —— 全图直方图
  光源      是否看得出布光         —— 地面区域的明暗方差 + 投影方向性
  构图      主体大小 / 位置 / 留白 —— 外接框与画面的比例关系
  天空      饱和度/色相离散/渐变/高光 —— 全画面上部（优化方案 P0-5）
  景深      主体 vs 背景清晰度比    —— Laplacian 方差（原始值 + 比值）
  轮廓光    主体边缘比内部亮多少    —— 5px 光环检测
  三分      主体中心是否落三分线    —— 构图规则

用法::

    from vision_check import check_image
    r = check_image('render.png')                  # 自动找主体
    r = check_image('render.png', subject_box=(x0,y0,x1,y1))
    print(r['verdict'], r['issues'])
"""
import json
import math
import os

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

# ★ 坑 70：暖光（金色轮廓光）会把头发提亮到 V≈100，落进旧 SKIN 掩码（V≥90），
#   于是背面图的"皮肤占比"被抬到 0.23，verify3 明明是后脑勺却判成 front。
#   收紧后的判据：皮肤必须【高饱和 + 更亮】（S≥55, V≥135），
#   头发必须【高饱和 + 更暗】（S≥100, V≤115），两者不再重叠。
SKIN2_LO = np.array([5, 55, 135], np.uint8)
SKIN2_HI = np.array([28, 175, 245], np.uint8)
HAIR2_LO = np.array([0, 100, 0], np.uint8)
HAIR2_HI = np.array([30, 255, 115], np.uint8)
# 眼白：低饱和 + 高亮（S<45, V>185），但只统计贴着主体掩膜的那些，
# 否则背面図头顶的亮天空会被当成"眼睛"（实测正面 0.014~0.020 / 背面 0.000）。
EYE_LO = np.array([0, 0, 186], np.uint8)
EYE_HI = np.array([180, 45, 255], np.uint8)


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
        # ★ 兜底：暖光土墙这类场景里，墙本身是肤色 → 皮肤掩码和背景连成一片，
        #   被 w>0.75W 滤掉，直接返回 None。这时改用【衬衫+裤子+暗发】再找一次
        #   （背景很少同时是青衬衫色 + 蓝裤子色），并按竖长比例过滤。
        m2 = (_mask(img, CYAN_LO, CYAN_HI) | _mask(img, BLUE_LO, BLUE_HI)
              | _mask(img, HAIR2_LO, HAIR2_HI))
        m2 = cv2.morphologyEx(m2, cv2.MORPH_CLOSE,
                              np.ones((9, 9), np.uint8), iterations=1)
        n2, _l2, st2, _c2 = cv2.connectedComponentsWithStats(m2, 8)
        for i in range(1, n2):
            x, y, w, h, area = st2[i]
            if area < (H * W) * 0.004 or h < H * 0.12:
                continue
            if w > W * 0.60 or h > H * 0.92:
                continue
            ar = h / max(1.0, w)
            if not (0.8 <= ar <= 5.0):
                continue
            if best is None or area > best[0]:
                best = (area, x, y, w, h, area)
        if best is not None:
            _, x, y, w, h, area = best
            return (int(x), int(y), int(x + w), int(y + h)), m2, area
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
    n = max(1, head.shape[0] * head.shape[1])
    sm = _mask(head, SKIN_LO, SKIN_HI)
    hm = _mask(head, HAIR_LO, HAIR_HI)
    skin = float(np.count_nonzero(sm)) / n
    hair = float(np.count_nonzero(hm)) / n
    # ★ 收紧后的皮肤/头发 + 眼白（解决暖光把头发判成皮肤的坑 70）
    s2 = _mask(head, SKIN2_LO, SKIN2_HI)
    h2 = _mask(head, HAIR2_LO, HAIR2_HI)
    skin2 = float(np.count_nonzero(s2)) / n
    hair2 = float(np.count_nonzero(h2)) / n
    subj = s2 | h2
    subj |= _mask(head, CYAN_LO, CYAN_HI) | _mask(head, BLUE_LO, BLUE_HI)
    subj = cv2.morphologyEx(subj, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    near = cv2.dilate(subj, np.ones((9, 9), np.uint8))
    eye = cv2.bitwise_and(_mask(head, EYE_LO, EYE_HI), near)
    eye_ratio = float(np.count_nonzero(eye)) / n

    # 判据（阈值来自 5 张标定图，见方案 §P0-5）：
    #   正面旧图 skin2 0.19/0.23、暖光土墙正面 0.14、暗脸正面 0.045+眼白 0.09
    #   真背面 skin2 0.013、眼白 0.000
    if eye_ratio < 0.0015 and skin2 < 0.06:
        f = 'back'
    elif eye_ratio >= 0.002 and skin2 >= 0.005:
        f = 'front'
    elif skin2 >= 0.10:
        f = 'front'
    elif skin2 >= 0.06:
        f = 'three-quarter'
    else:
        f = 'unknown'
    return {'facing': f, 'skin_ratio': round(skin, 3),
            'skin2_ratio': round(skin2, 3), 'hair_ratio': round(hair, 3),
            'hair2_ratio': round(hair2, 3), 'eye_ratio': round(eye_ratio, 4)}


def _hue_light(bgr):
    """一串 BGR 像素 → (平均色相 0-360, 平均明度 0-1, 平均饱和度 0-1)。

    ⚠️ 坑 68：旧版直接拿 RGB 欧氏距离判接地，**阴影一暗就被判 floating**。
    实测一张完全正常的接触阴影：脚下 (130,142,100) vs 旁边 (156,172,134)，
    距离 52.7 → 判 floating 扣 20 分。但两者色相几乎一样，只是暗了一点。
    所以判据必须换成【色相】（阴影不该改变色相），明度单独用来量阴影强度。
    """
    px = bgr.reshape(-1, 3)
    hsv = cv2.cvtColor(px.reshape(-1, 1, 3), cv2.COLOR_BGR2HSV).reshape(-1, 3)
    ang = np.deg2rad(hsv[:, 0].astype(np.float32) * 2.0)
    w = hsv[:, 1].astype(np.float32) + 1.0       # 用饱和度加权，低饱和像素不参与定相
    hx = float((np.cos(ang) * w).sum() / w.sum())
    hy = float((np.sin(ang) * w).sum() / w.sum())
    hue = math.degrees(math.atan2(hy, hx)) % 360.0
    return hue, float(hsv[:, 2].mean()) / 255.0, float(hsv[:, 1].mean()) / 255.0


def _angdiff(a, b):
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def check_ground_contact(img, box, band=0.10):
    """接地：按【色相】判脚下是不是同一片地面（优化方案 P0-5）。

    判据：
      d_hue < 18° 且 L_under <= L_side*1.02 → grounded（同一片地面，只是被阴影压暗）
      d_hue < 30°                             → borderline
      否则                                     → floating?（脚下露出的是别的地面/天空）
    同时输出 shadow_strength = 1 - L_under/L_side，正常接触阴影应在 0.15~0.45。
    """
    x0, y0, x1, y1 = box
    H, W = img.shape[:2]
    w = x1 - x0
    bh = max(3, int((y1 - y0) * band * 0.35))
    under = img[min(H - 1, y1 + 1):min(H, y1 + 1 + bh), max(0, x0):min(W, x1)]
    sy0 = max(0, y1 - int(0.15 * (y1 - y0)))
    sy1 = min(H, y1 + bh)
    side_l = img[sy0:sy1, max(0, x0 - w // 2):max(1, x0)]
    side_r = img[sy0:sy1, min(W - 1, x1):min(W, x1 + w // 2)]
    if under.size < 9 or (side_l.size == 0 and side_r.size == 0):
        return {'contact': 'unknown'}
    parts = [p.reshape(-1, 3) for p in (side_l, side_r) if p.size > 0]
    ms_px = np.vstack(parts)

    h_under, L_under, _ = _hue_light(under)
    h_side, L_side, _ = _hue_light(ms_px)
    d_hue = _angdiff(h_under, h_side)
    shadow = max(0.0, 1.0 - L_under / max(L_side, 1e-6))
    mu = under.reshape(-1, 3).mean(0)
    ms = ms_px.mean(0)
    d_rgb = float(np.linalg.norm(mu - ms))

    if d_hue < 18.0 and L_under <= L_side * 1.02:
        c = 'grounded'
    elif d_hue < 30.0:
        c = 'borderline'
    else:
        c = 'floating?'
    return {'contact': c, 'color_delta': round(d_rgb, 1),
            'd_hue': round(d_hue, 1),
            'L_under': round(L_under, 3), 'L_side': round(L_side, 3),
            'shadow_strength': round(shadow, 3),
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


def _clamp_box(box, W, H):
    x0, y0, x1, y1 = box
    x0 = max(0, min(int(x0), W - 1))
    x1 = max(x0 + 1, min(int(round(x1)), W))
    y0 = max(0, min(int(y0), H - 1))
    y1 = max(y0 + 1, min(int(round(y1)), H))
    return x0, y0, x1, y1


def _subject_mask(img, box):
    """框内主体掩膜（配色 + 最大连通块）。

    只用于量"轮廓光/边缘对比"这种像素级指标，
    **不用它判"框里是不是角色"**（那是 check_facing / 审图框的活）。
    """
    H, W = img.shape[:2]
    x0, y0, x1, y1 = _clamp_box(box, W, H)
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return None, None
    m = (_mask(roi, SKIN_LO, SKIN_HI) | _mask(roi, CYAN_LO, CYAN_HI)
         | _mask(roi, BLUE_LO, BLUE_HI) | _mask(roi, HAIR_LO, HAIR_HI))
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=1)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        return None, roi
    i = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
    return (lab == i).astype(np.uint8), roi


def check_sky(img, box):
    """天空：饱和度 / 色相离散 / 地平线渐变 / 高光斑（优化方案 P0-5）。

    全画面上部（截止到主体框顶的 72%）取样。死板纯蓝色块 = 没有大气散射的
    层次，正常物理天空应该有色相漂移 + 地平线渐变 + 少量高光。
    所有数值都是**原始值**，阈值随分辨率不变。
    """
    H, W = img.shape[:2]
    y_end = int(box[1] * 0.72) if box else int(H * 0.30)
    y_end = max(4, min(y_end, H - 1))
    xa, xb = max(0, int(W * 0.08)), max(1, int(W * 0.92))
    reg = img[0:y_end, xa:xb]
    if reg.size < 500:
        return {'ok': False}
    hsv = cv2.cvtColor(reg, cv2.COLOR_BGR2HSV)
    sat = hsv[..., 1].astype(np.float32) / 255.0
    val = hsv[..., 2].astype(np.float32) / 255.0
    hue = hsv[..., 0].astype(np.float32) * 2.0
    sel = hsv[..., 1] > 30
    hue_std = float(hue[sel].std()) if int(sel.sum()) > 20 else 0.0
    g = cv2.cvtColor(reg, cv2.COLOR_BGR2GRAY).astype(np.float32)
    row_grad = float(np.abs(np.diff(g.mean(axis=1))).mean()) if g.shape[0] > 1 else 0.0
    return {'ok': True,
            'sky_sat': round(float(sat.mean()), 4),
            'sky_hue_std': round(hue_std, 2),
            'sky_row_grad': round(row_grad, 3),
            'sky_hot_ratio': round(float((val > 0.92).mean()), 5),
            'pixels': int(reg.shape[0] * reg.shape[1])}


def check_depth(img, box):
    """景深：主体 vs 前景/背景的**梯度强度**比（优化方案 P0-5，lead 修 §B）。

    ⚠️ 坑 71：最初用 Laplacian 方差比，结果「背景比主体清晰 1.14 倍」——
    背景取样带正压在「天空—地形」那条高对比硬剪影上，不管糊多厉害，方差都很大。
    实测：同一张图 f/8 与 f/1.4 的 bg_var 几乎不变（515 vs 515），
    而目视 f/0.15 已经糊成一团 —— 说明指标量错了对象，不是没开景深。

    改法（三条都做）：
      ① 只统计【非极值】像素（20<V<240），把死白天空和黑剪影边缘排掉；
      ② 用【梯度均值 + p95】而不是方差 —— 糊掉的边缘是"低而宽"，峰值和均值都塌；
      ③ 主体侧只在【角色色掩膜】上统计，不要拿整块矩形框（框里有背景）。
    dof_score = 100*(1 - 0.5*ratio_mean - 0.5*ratio_p95)，>=45 视为有景深分离。
    所有原始数值都输出，便于跨分辨率人工复核。
    """
    H, W = img.shape[:2]
    x0, y0, x1, y1 = _clamp_box(box, W, H)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
    grad = np.hypot(gx, gy) * 0.25
    ok = (gray > 20) & (gray < 240)

    sub_sel = np.zeros((H, W), bool)
    m, _roi = _subject_mask(img, box)
    if m is not None:
        mm = (m > 0).astype(np.uint8)
        mm = cv2.dilate(mm, np.ones((3, 3), np.uint8)) > 0
        sub_sel[y0:y1, x0:x1] = mm
    sub_sel &= ok
    b0 = max(0, y0 - int(0.22 * H))
    bg_sel = np.zeros((H, W), bool)
    bg_sel[b0:y0, x0:x1] = True
    bg_sel &= ok
    f1 = min(H, y1 + int(0.06 * H))
    fg_sel = np.zeros((H, W), bool)
    fg_sel[f1:H, x0:x1] = True
    fg_sel &= ok

    n_sub, n_bg, n_fg = int(sub_sel.sum()), int(bg_sel.sum()), int(fg_sel.sum())
    if n_sub < 50 or n_bg < 50:
        return {'ok': False, 'dof_score': None, 'n_sub': n_sub, 'n_bg': n_bg}

    sub_g, bg_g = grad[sub_sel], grad[bg_sel]
    fg_g = grad[fg_sel] if n_fg >= 50 else np.zeros(1, np.float32)
    s_mean, b_mean, f_mean = float(sub_g.mean()), float(bg_g.mean()), float(fg_g.mean())
    s_p95, b_p95 = float(np.percentile(sub_g, 95)), float(np.percentile(bg_g, 95))
    r_mean = b_mean / max(s_mean, 1e-6)
    r_p95 = b_p95 / max(s_p95, 1e-6)
    ratio = 0.5 * r_mean + 0.5 * r_p95
    dof_score = max(0.0, min(1.0, 1.0 - ratio)) * 100.0

    # 兼容字段（旧报告里有）：Laplacian 方差仍算，只做参考不当判据
    lap = cv2.Laplacian(gray.astype(np.uint8), cv2.CV_64F)
    sub_var = float(lap[y0:y1, x0:x1].var())
    bg_var = float(lap[b0:y0, x0:x1].var()) if y0 - b0 > 2 else 0.0
    return {'ok': True,
            'sub_grad_mean': round(s_mean, 3), 'bg_grad_mean': round(b_mean, 3),
            'fg_grad_mean': round(f_mean, 3),
            'sub_grad_p95': round(s_p95, 2), 'bg_grad_p95': round(b_p95, 2),
            'grad_ratio_bg_sub': round(r_mean, 3),
            'p95_ratio_bg_sub': round(r_p95, 3),
            'sharp_ratio_bg_sub': round(ratio, 3),     # 兼容：现在是混合比
            'dof_score': round(dof_score, 1),
            'n_sub': n_sub, 'n_bg': n_bg, 'n_fg': n_fg,
            'sub_var': round(sub_var, 1), 'bg_var': round(bg_var, 1),
            'note': '梯度均值+p95，已排除 V<20/V>240 极值像素'}


def check_rim(img, box):
    """轮廓光：主体外 5px 光环比 2px 光环亮多少（优化方案 P0-5）。

    没有轮廓光时，主体边缘和背景亮度接近，整个人"糊"在背景里。
    rim_bright_frac = 外环像素比内环平均亮 >28 灰阶的比例。
    """
    m, roi = _subject_mask(img, box)
    if m is None or roi.size == 0:
        return {'ok': False, 'rim_bright_frac': None}
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY).astype(np.float32)
    k5 = np.ones((5, 5), np.uint8)
    k2 = np.ones((2, 2), np.uint8)
    outer = (cv2.dilate(m, k5) > 0) & (cv2.dilate(m, k2) == 0)
    inner = cv2.erode(m, k2) > 0
    if int(outer.sum()) < 20 or int(inner.sum()) < 20:
        return {'ok': False, 'rim_bright_frac': None}
    hi = float(gray[inner].mean())
    lo = float(gray[outer].mean())
    bright = float(((gray[outer] - hi) > 28).mean())
    return {'ok': True, 'rim_ratio': round(lo / max(hi, 1e-6), 3),
            'rim_inner_mean': round(hi, 1), 'rim_outer_mean': round(lo, 1),
            'rim_bright_frac': round(bright, 4)}


def check_rule_of_thirds(img, box):
    """三分构图：主体中心是否落在左/右三分线，高度是否在理想区间（P0-5）。"""
    H, W = img.shape[:2]
    x0, y0, x1, y1 = _clamp_box(box, W, H)
    cx = ((x0 + x1) / 2.0) / W
    height = (y1 - y0) / H
    centered = abs(cx - 0.5) < 0.05
    on_third = abs(cx - 0.382) < 0.10 or abs(cx - 0.618) < 0.10
    return {'subject_center_x': round(cx, 3),
            'center_offset': round(abs(cx - 0.382), 3),
            'subject_height_ratio': round(height, 3),
            'is_centered': bool(centered), 'on_thirds': bool(on_third)}


def _subject_pixels(img, box):
    """框里到底有没有主体像素（P0-0 的落地校验）。

    两路证据，任一路够强就认为框里有东西：
      · color_ratio：角色专属配色（皮肤/青衬衫/蓝裤/头发）的占比
      · fg_ratio   ：和【框四边颜色中位数】差 >28 的像素占比（前景对比）
    雾很重 / 强色偏时 color_ratio 会掉，所以不能只靠颜色。
    """
    H, W = img.shape[:2]
    x0, y0, x1, y1 = _clamp_box(box, W, H)
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return {'color_ratio': 0.0, 'fg_ratio': 0.0}
    m = (_mask(roi, SKIN_LO, SKIN_HI) | _mask(roi, CYAN_LO, CYAN_HI)
         | _mask(roi, BLUE_LO, BLUE_HI) | _mask(roi, HAIR_LO, HAIR_HI))
    color_ratio = float(np.count_nonzero(m)) / max(1, m.size)
    b = max(1, int(min(roi.shape[0], roi.shape[1]) * 0.06))
    border = np.vstack([roi[:b].reshape(-1, 3), roi[-b:].reshape(-1, 3),
                        roi[:, :b].reshape(-1, 3), roi[:, -b:].reshape(-1, 3)])
    med = np.median(border, axis=0)
    d = np.linalg.norm(roi.astype(np.float32) - med[None, None, :], axis=2)
    fg_ratio = float((d > 28).mean())
    return {'color_ratio': round(color_ratio, 4), 'fg_ratio': round(fg_ratio, 4)}


def check_image(path, subject_box=None, verbose=False):
    """主入口：返回 {verdict, score, issues, details}"""
    img = imread_u(path)
    if img is None:
        return {'ok': False, 'error': f'读不到图: {path}'}
    H, W = img.shape[:2]

    auto = None
    box_src = 'none'
    if subject_box is None:
        # ★ 先看同目录有没有 Blender 投影出来的框（尺寸必须对得上，防止误用陈旧文件）
        jp = os.path.join(os.path.dirname(os.path.abspath(path)), '_subject_box.json')
        try:
            if os.path.isfile(jp):
                with open(jp, encoding='utf-8') as f:
                    jj = json.load(f)
                if jj.get('subject_box') and jj.get('size') == [W, H]:
                    subject_box = tuple(int(v) for v in jj['subject_box'])
                    box_src = 'blender_json'
        except Exception:
            pass
    if subject_box is None:
        found = find_subject(img)
        if found:
            auto, _, _ = found
            box_src = 'auto_color'
        subject_box = auto
    elif box_src == 'none':
        box_src = 'arg'
    box_given = subject_box is not None
    box_note = None
    if subject_box is not None:
        # 投影框可能越界 → 夹进画面；夹完太小就当没框（宁可不判，也别判错）
        x0, y0, x1, y1 = _clamp_box(subject_box, W, H)
        if (x1 - x0) < 8 or (y1 - y0) < 8:
            box_note = (f"审图框无效：{subject_box} 夹进画面只剩 "
                        f"{x1 - x0}×{y1 - y0} 像素")
            subject_box = None
        else:
            subject_box = (x0, y0, x1, y1)

    det = {'size': [W, H], 'subject_box': subject_box,
           'subject_box_given': box_given, 'subject_box_from': box_src}
    issues = []
    score = 100.0
    if box_note:
        issues.append(box_note + " → 退回按颜色自动找主体")
        score -= 4

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
        # ★ P0-0：先确认框里真的有主体像素（Blender 投影的框可能罩住空气）
        det['subject_px'] = _subject_pixels(img, subject_box)
        sp = det['subject_px']
        if sp['color_ratio'] < 0.02 and sp['fg_ratio'] < 0.05:
            issues.append(f"subject_box 有效但框里没有主体像素（角色色块 "
                          f"{sp['color_ratio']*100:.1f}%，前景对比 "
                          f"{sp['fg_ratio']*100:.1f}%）—— 投影框和画面脱节了")
            score -= 40
        elif sp['color_ratio'] < 0.05:
            issues.append(f"框内角色色块偏少（{sp['color_ratio']*100:.1f}%，"
                          f"前景对比 {sp['fg_ratio']*100:.1f}%）—— 雾/色偏可能把配色带跑了")
            score -= 6

        det['facing'] = check_facing(img, subject_box)
        f = det['facing']['facing']
        if f == 'back':
            issues.append(f"看到的是【背面】（头部皮肤占比仅 "
                          f"{det['facing']['skin_ratio']*100:.1f}%）"
                          f"—— 角色朝向要转过来")
            score -= 30

        det['contact'] = check_ground_contact(img, subject_box)
        c = det['contact']['contact']
        ct = det['contact']
        if c == 'floating?':
            issues.append(f"疑似【脚没踩地】：脚下与旁边地面色相差 "
                          f"{ct.get('d_hue')}°（>30° = 脚下是别的东西）；"
                          f"明度 {ct.get('L_under')} vs {ct.get('L_side')}")
            score -= 20
        elif c == 'borderline':
            issues.append(f"接地可疑（色相差 {ct.get('d_hue')}°，"
                          f"阴影强度 {ct.get('shadow_strength')}）")
            score -= 8

        det['lighting'] = check_lighting(img, subject_box)
        if det['lighting'].get('has_shading') is False:
            issues.append(f"看不出布光：角色周围地面灰阶标准差仅 "
                          f"{det['lighting']['ground_std']}"
                          f"（太均匀 = 平光，多半是天空占满了动态范围）")
            score -= 18

        # ★ P0-5 新增四维：天空 / 景深 / 轮廓光 / 三分
        det['sky'] = check_sky(img, subject_box)
        sk = det['sky']
        if sk.get('ok'):
            solid = sk['sky_sat'] < 0.05 and sk['sky_hue_std'] < 6.0
            if solid:
                issues.append(f"天空是一块死板纯色（饱和度 {sk['sky_sat']:.3f}，"
                              f"色相离散 {sk['sky_hue_std']:.1f}）—— 缺大气层次")
                score -= 12
            elif sk['sky_row_grad'] < 1.2 and sk['sky_hot_ratio'] < 0.001:
                issues.append(f"天空既没有地平线渐变也没有高光（行梯度 "
                              f"{sk['sky_row_grad']}，高光占比 {sk['sky_hot_ratio']}）")
                score -= 8
            if not solid and sk['sky_hot_ratio'] < 0.001:
                issues.append(f"天空没有任何高光斑（hot_ratio="
                              f"{sk['sky_hot_ratio']}，采样 {sk['pixels']} px）")
                score -= 6
            elif sk['sky_hot_ratio'] > 0.55:
                # ★ 反过来的病：天空整片顶到近白（verify5 实测 0.79），没有黄金时刻颜色
                issues.append(f"天空过曝成一块白板（hot_ratio="
                              f"{sk['sky_hot_ratio']:.2f}，天空饱和度 "
                              f"{sk['sky_sat']:.3f}）—— 压天空强度/曝光，"
                              f"只留一小块高光斑")
                score -= 8

        det['depth'] = check_depth(img, subject_box)
        dp = det['depth']
        if dp.get('ok') and dp.get('dof_score') is not None \
                and dp['dof_score'] < 45:
            if dp['sharp_ratio_bg_sub'] > 0.9:
                issues.append(f"几乎没有景深：背景梯度是主体的 "
                              f"{dp['sharp_ratio_bg_sub']:.2f} 倍"
                              f"（dof_score {dp['dof_score']:.0f}/100，"
                              f"主体梯度 {dp['sub_grad_mean']} vs 背景 "
                              f"{dp['bg_grad_mean']}）")
                score -= 10
            else:
                issues.append(f"景深偏弱（背景/主体梯度比 "
                              f"{dp['sharp_ratio_bg_sub']:.2f}，dof_score "
                              f"{dp['dof_score']:.0f}/100）")
                score -= 5

        det['rim'] = check_rim(img, subject_box)
        rm = det['rim']
        if rm.get('ok') and rm['rim_bright_frac'] is not None \
                and rm['rim_bright_frac'] < 0.30:
            issues.append(f"主体边缘没有轮廓光（rim_bright_frac="
                          f"{rm['rim_bright_frac']:.3f}，rim_ratio "
                          f"{rm['rim_ratio']:.2f}）—— 人和背景糊在一起")
            score -= 8

        det['thirds'] = check_rule_of_thirds(img, subject_box)
        th = det['thirds']
        if not th['is_centered'] and not th['on_thirds']:
            issues.append(f"主体不在三分线也不在中央（center_x="
                          f"{th['subject_center_x']:.3f}，"
                          f"height_ratio={th['subject_height_ratio']:.2f}）")
            score -= 6

        det['composition'] = check_composition(img, subject_box)
        co = det['composition']
        # ★ 理想 0.55~0.68（旧阈值 0.14/0.95 是给远景图用的）
        if co['subject_height_ratio'] < 0.45:
            issues.append(f"主体偏小（占画面高度 {co['subject_height_ratio']*100:.0f}%，"
                          f"理想 55~68%），建议拉近或加长焦")
            score -= 12
        elif co['subject_height_ratio'] > 0.80:
            issues.append(f"主体太满（占画面高度 {co['subject_height_ratio']*100:.0f}%），"
                          f"可能被裁切")
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
    import sys
    try:                                   # Windows 控制台默认 GBK，⚠️ 会 UnicodeEncodeError
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
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
            if d.get('subject_px'):
                print(f"  框内主体: {d['subject_px']}")
            if d.get('sky', {}).get('ok'):
                print(f"  天空: {d['sky']}")
            if d.get('depth'):
                print(f"  景深: {d['depth']}")
            if d.get('rim'):
                print(f"  轮廓光: {d['rim']}")
            if d.get('thirds'):
                print(f"  三分: {d['thirds']}")
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
