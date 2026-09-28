"""精确定位出牌区：用「白牌面」分割（高亮+低饱和），逐状态比较新增内容。
目标：从「所有人出两张」里把四家的牌分别分离出来，确定真实四区坐标。
"""
import glob, os, sys, io, itertools
import numpy as np
import cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
OUT = "spike/shot_analysis"
os.makedirs(OUT, exist_ok=True)


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


def card_mask(img):
    """白牌面：高亮 + 较低饱和（排除彩色 UI 与桌面）。"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    m = ((v > 165) & (s < 110)).astype(np.uint8)
    # 轻微闭运算连接牌内部被花色符号切碎的部分，但不要跨牌合并
    return cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))


def blobs(mask, min_area=250):
    nlab, lab, stats, cent = cv2.connectedComponentsWithStats(mask, 8)
    out = []
    for i in range(1, nlab):
        x, y, w, h, area = stats[i]
        if area < min_area:
            continue
        out.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "area": int(area), "cx": float(cent[i][0]), "cy": float(cent[i][1]),
                    "bh": float(h)})
    out.sort(key=lambda b: (b["cy"], b["cx"]))
    return out


print("=" * 92)
print("1) 各状态的白牌面连通块（按 y 排序）")
print("=" * 92)
masks = {}
for n in names:
    m = card_mask(imgs[n])
    masks[n] = m
    bs = blobs(m)
    print(f"\n  【{n}】 共 {len(bs)} 块 (面积>=250)")
    for b in bs:
        print(f"    x={b['x']:4d} y={b['y']:4d} {b['w']:4d}x{b['h']:3d} 面积={b['area']:6d} "
              f"中心=({b['cx']:6.0f},{b['cy']:6.0f}) 相对=({b['cx']/W:.3f},{b['cy']/H:.3f}) "
              f"宽高比={b['w']/max(b['h'],1):5.2f}")

print()
print("=" * 92)
print("2) 以「没有人出牌」为基线，各状态新增的牌面块 = 出牌区")
print("=" * 92)
base = "手牌+没有人出牌"
if base in imgs:
    base_mask = masks[base]
    for n in names:
        if n == base:
            continue
        # 新增 = 当前有牌 而 基线无牌
        new = cv2.bitwise_and(masks[n], cv2.bitwise_not(base_mask))
        new = cv2.morphologyEx(new, cv2.MORPH_CLOSE,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7)))
        bs = blobs(new)
        print(f"\n  【{n}】 vs 基线: 新增牌面 {len(bs)} 块")
        for b in bs:
            print(f"    新增: x={b['x']:4d} y={b['y']:4d} {b['w']:4d}x{b['h']:3d} "
                  f"面积={b['area']:6d} 中心=({b['cx']:6.0f},{b['cy']:6.0f}) "
                  f"相对=({b['cx']/W:.3f},{b['cy']/H:.3f})")

print()
print("=" * 92)
print("3) 逐列/逐行的牌面密度剖面（定位四区的横向与纵向位置）")
print("=" * 92)
n = "手牌+所有人出两张"
m = masks[n]
colsum = m.sum(axis=0) / 255
rowsum = m.sum(axis=1) / 255
# 找有明显牌面密度的列区间
thr_c = max(20, colsum.max() * 0.15)
runs, cur = [], None
for x in range(W):
    if colsum[x] > thr_c:
        cur = [x, x] if cur is None else [cur[0], x]
    else:
        if cur is not None and cur[1] - cur[0] > 8:
            runs.append(tuple(cur))
        cur = None
if cur is not None:
    runs.append(tuple(cur))
print(f"  {n}: 列方向牌面密集区间 (阈值>{thr_c:.0f}px):")
for a, b_ in runs:
    seg = m[:, a:b_ + 1]
    ys = np.where(seg.sum(axis=1) > 0)[0]
    print(f"    x {a:4d}-{b_:4d} (宽{b_-a+1:3d})  y 范围 {ys.min():4d}-{ys.max():4d}  "
          f"牌面像素={int(seg.sum()/255)}")

print()
print("=" * 92)
print("4) 生成标注图：把检出的牌面块框出来并编号")
print("=" * 92)
for n in names:
    vis = imgs[n].copy()
    for i, b in enumerate(blobs(masks[n])):
        cv2.rectangle(vis, (b["x"], b["y"]), (b["x"] + b["w"], b["y"] + b["h"]),
                      (0, 0, 255), 2)
        cv2.putText(vis, str(i), (b["x"], max(12, b["y"] - 3)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1, cv2.LINE_AA)
    imwrite_u(f"{OUT}/mask_anno_{n}.png", vis)
    imwrite_u(f"{OUT}/mask_raw_{n}.png", masks[n] * 255)
print(f"  已写 {OUT}/mask_anno_*.png （红框=检出牌面块）")
print(f"  已写 {OUT}/mask_raw_*.png  （白=判定为牌面）")
