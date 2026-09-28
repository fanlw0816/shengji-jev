"""最终确认：手牌区精确位置 + 每个出牌区里有几张牌 + 紧密差分（不做膨胀）。"""
import glob, os, sys, io
import numpy as np
import cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
OUT = "spike/shot_analysis"


def imread_u(p, f=cv2.IMREAD_COLOR):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), f)


def imwrite_u(p, img):
    ok, b = cv2.imencode(os.path.splitext(p)[1], img)
    if ok:
        b.tofile(p)


names, imgs = [], {}
for p in sorted(glob.glob("png/*.jpg") + glob.glob("png/*.png")):
    n = os.path.splitext(os.path.basename(p))[0]
    im = imread_u(p)
    if im is not None:
        names.append(n); imgs[n] = im
H, W = imgs[names[0]].shape[:2]

A = "手牌+没有人出牌"
B = "手牌+所有人出两张"

print("=" * 90)
print("1) 紧密差分（无形态学膨胀）：没有人出牌 -> 所有人出两张")
print("=" * 90)
d = cv2.absdiff(imgs[A], imgs[B])
g = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
m = (g > 30).astype(np.uint8)
# 只做 3x3 链接相邻像素，避免膨胀夸大边界
m2 = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                      cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
nlab, lab, stats, cent = cv2.connectedComponentsWithStats(m2, 8)
rows = []
for i in range(1, nlab):
    x, y, w, h, area = stats[i]
    if area < 60:
        continue
    rows.append((area, x, y, w, h, cent[i]))
rows.sort(key=lambda r: (r[2], r[1]))
for area, x, y, w, h, c in rows:
    print(f"  x={x:4d} y={y:4d} {w:4d}x{h:3d} 面积={area:6d} "
          f"中心=({c[0]:6.0f},{c[1]:6.0f}) 相对=({c[0]/W:.3f},{c[1]/H:.3f})")
print(f"  共 {len(rows)} 块 (面积>=60)")

print()
print("=" * 90)
print("2) 手牌区精确位置：逐行扫描白牌面像素横向跨度")
print("=" * 90)
hsv = cv2.cvtColor(imgs[A], cv2.COLOR_BGR2HSV)
v, s = hsv[:, :, 2], hsv[:, :, 1]
white = (v > 165) & (s < 110)
for y in range(440, 720, 10):
    row = white[y]
    xs = np.where(row)[0]
    if len(xs) == 0:
        print(f"  y={y:4d}: 无白牌面")
        continue
    # 找连续段
    segs, start = [], xs[0]
    for i in range(1, len(xs)):
        if xs[i] != xs[i - 1] + 1:
            if xs[i - 1] - start > 10:
                segs.append((start, xs[i - 1]))
            start = xs[i]
    if xs[-1] - start > 10:
        segs.append((start, xs[-1]))
    print(f"  y={y:4d}: 白像素 {len(xs):4d}  横向段 {segs[:6]}")

print()
print("=" * 90)
print("3) 出牌区内是几张牌？检测内部竖向边界")
print("=" * 90)
ZONES = {"上": (522, 207, 594, 285), "左": (412, 284, 484, 362),
         "右": (627, 284, 699, 362), "下": (522, 371, 594, 449)}
for zn, (x0, y0, x1, y1) in ZONES.items():
    print(f"\n  【{zn}区】 x{x0}-{x1} y{y0}-{y1}")
    for n in names:
        seg = imgs[n][y0:y1, x0:x1]
        h2 = cv2.cvtColor(seg, cv2.COLOR_BGR2HSV)
        wmask = ((h2[:, :, 2] > 165) & (h2[:, :, 1] < 110))
        frac = wmask.mean()
        if frac < 0.05:
            print(f"    {n:26s} 空（白牌面占比 {frac*100:5.1f}%）")
            continue
        # 逐列统计白像素，找内部低谷 = 两张牌的分界
        colw = wmask.sum(axis=0)
        # 有效列范围
        valid = np.where(colw > 5)[0]
        if len(valid) == 0:
            print(f"    {n:26s} 白占比 {frac*100:5.1f}% 但无有效列")
            continue
        c0, c1 = valid.min(), valid.max()
        sub = colw[c0:c1 + 1]
        mid = len(sub) // 2
        # 找中部区域的低谷
        lo = sub[max(0, mid - 14):mid + 14]
        dip = int(lo.min()) if len(lo) else 0
        print(f"    {n:26s} 白占比 {frac*100:5.1f}%  有效列 x{c0}-{c1} (宽{c1-c0+1})  "
              f"中部最低白像素={dip}  列剖面={sub[::4].tolist()}")

print()
print("=" * 90)
print("4) 生成最终标注图（十字四区 + 手牌区）")
print("=" * 90)
ZCOL = {"上": (0, 255, 255), "左": (255, 128, 0), "右": (255, 0, 255), "下": (0, 255, 0)}
COLOR_NAME = {"上": "TOP", "左": "LEFT", "右": "RIGHT", "下": "BOTTOM=self"}
for n in names:
    vis = imgs[n].copy()
    for zn, (x0, y0, x1, y1) in ZONES.items():
        cv2.rectangle(vis, (x0, y0), (x1, y1), ZCOL[zn], 2)
        cv2.putText(vis, COLOR_NAME[zn], (x0, y0 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, ZCOL[zn], 1, cv2.LINE_AA)
    cv2.rectangle(vis, (350, 515), (770, 612), (0, 0, 255), 2)
    cv2.putText(vis, "HAND?", (350, 512), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1)
    cv2.rectangle(vis, (0, 0), (W - 1, H - 1), (255, 255, 255), 1)
    imwrite_u(f"{OUT}/FINAL_{n}.png", vis)
print(f"  已写 {OUT}/FINAL_*.png （十字四区着色 + 手牌区红框）")
