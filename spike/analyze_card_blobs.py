"""实测：全图扫「牌尺寸白色块」，看会不会把手牌/UI 也扫进来（误报源）。
这决定校验逻辑能不能用「全图不得有预测区之外的牌尺寸块」这个判据。
"""
import glob, os, sys, io
import numpy as np
import cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def imread_u(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


names, imgs = [], {}
for p in sorted(glob.glob("png/*.jpg")):
    n = os.path.splitext(os.path.basename(p))[0]
    imgs[n] = imread_u(p); names.append(n)
H, W = imgs[names[0]].shape[:2]

# 参考四区（1280x720）
Z = {"top": (521, 207, 594, 286), "left": (411, 283, 484, 362),
     "right": (627, 283, 699, 362), "bottom": (521, 371, 594, 450)}
MARGIN_X, MARGIN_Y = 120, 40


def card_mask(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 2] > 165) & (hsv[:, :, 1] < 110)).astype(np.uint8)


# 候选筛选窗口（来自计划常量）
MIN_W, MAX_W = 50, 200
MIN_H, MAX_H = 70, 88
MIN_AREA = 800


def blobs(img):
    m = cv2.morphologyEx(card_mask(img), cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        out.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "area": int(area), "cx": float(cent[i][0]), "cy": float(cent[i][1])})
    out.sort(key=lambda b: -b["area"])
    return out


def in_any_zone(b):
    for (x0, y0, x1, y1) in Z.values():
        if (x0 - MARGIN_X <= b["cx"] <= x1 + MARGIN_X
                and y0 - MARGIN_Y <= b["cy"] <= y1 + MARGIN_Y):
            return True
    return False


print("=" * 100)
print("全部白色块（按面积降序）—— 标 * 者为「牌尺寸窗口」内的候选")
print("=" * 100)
for n in names:
    print(f"\n【{n}】")
    allb = blobs(imgs[n])
    for b in allb[:12]:
        is_cand = (MIN_W <= b["w"] <= MAX_W and MIN_H <= b["h"] <= MAX_H
                   and b["area"] >= MIN_AREA)
        mark = "*" if is_cand else " "
        zone = "区内" if in_any_zone(b) else "区外"
        print(f"  {mark} x={b['x']:4d} y={b['y']:4d} {b['w']:4d}x{b['h']:3d} "
              f"面积={b['area']:6d} 中心=({b['cx']:6.0f},{b['cy']:6.0f}) "
              f"宽高比={b['w']/max(b['h'],1):5.2f} {zone}")
    cands = [b for b in allb
             if MIN_W <= b["w"] <= MAX_W and MIN_H <= b["h"] <= MAX_H and b["area"] >= MIN_AREA]
    inside = [b for b in cands if in_any_zone(b)]
    outside = [b for b in cands if not in_any_zone(b)]
    print(f"  --> 候选 {len(cands)} 个：区内 {len(inside)}，区外 {len(outside)}")
    for b in outside:
        print(f"      !! 区外候选: ({b['x']},{b['y']}) {b['w']}x{b['h']} "
              f"面积={b['area']} 宽高比={b['w']/b['h']:.2f}")

print()
print("=" * 100)
print("判读：若「区外候选」恒为 0，则「全图不得有区外牌尺寸块」这条判据安全可用。")
print("      若非 0，需加入排除规则（例如排除手牌带），否则会误报布局错误。")
print("=" * 100)
