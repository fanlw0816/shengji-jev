"""深度分析：牌面到底是什么样子？出牌区在哪？常驻动画元素是什么？
因为当前模型无法读图，本脚本负责用数值回答，并把关键区域切图供人工核对。
"""
import glob, os, sys, io, itertools
import numpy as np
import cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
OUT = "spike/shot_analysis"
os.makedirs(OUT, exist_ok=True)


def imread_u(path, flags=cv2.IMREAD_COLOR):
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), flags)


def imwrite_u(path, img):
    ok, buf = cv2.imencode(os.path.splitext(path)[1], img)
    if ok:
        buf.tofile(path)


names, imgs = [], {}
for p in sorted(glob.glob("png/*.jpg") + glob.glob("png/*.png")):
    n = os.path.splitext(os.path.basename(p))[0]
    im = imread_u(p)
    if im is not None:
        names.append(n); imgs[n] = im
H, W = imgs[names[0]].shape[:2]

print("=" * 88)
print("1) 亮度分布 —— 牌面是亮底还是暗底？")
print("=" * 88)
for n in names:
    hsv = cv2.cvtColor(imgs[n], cv2.COLOR_BGR2HSV)
    v = hsv[:, :, 2]; s = hsv[:, :, 1]
    tot = v.size
    print(f"  {n}")
    print(f"    V 分位: p50={np.percentile(v,50):5.0f} p90={np.percentile(v,90):5.0f} "
          f"p99={np.percentile(v,99):5.0f} max={v.max()}")
    print(f"    V>150 占比={100*(v>150).mean():5.2f}%   V>180={100*(v>180).mean():5.2f}%   "
          f"V>210={100*(v>210).mean():5.2f}%")
    # 低饱和高亮 = 白色牌面
    white = ((v > 150) & (s < 60))
    print(f"    『低饱和+高亮』(疑似白牌面) 占比={100*white.mean():5.2f}%")
    # 高饱和 = 彩色区域
    print(f"    S>120 (高饱和彩色) 占比={100*(s>120).mean():5.2f}%")

print()
print("=" * 88)
print("2) 分区颜色特征（上=对家区 中=牌桌 下=手牌）")
print("=" * 88)
bands = [("上1/3", 0, H // 3), ("中1/3", H // 3, 2 * H // 3), ("下1/3", 2 * H // 3, H)]
for n in names:
    img = imgs[n]
    print(f"  {n}")
    for label, y0, y1 in bands:
        seg = img[y0:y1]
        b, g, r = seg[:, :, 0].mean(), seg[:, :, 1].mean(), seg[:, :, 2].mean()
        print(f"    {label}: BGR=({b:5.1f},{g:5.1f},{r:5.1f})  "
              f"亮度={seg.mean():5.1f}  标准差={seg.std():5.1f}")

print()
print("=" * 88)
print("3) 手牌条带分析（找出牌的边界与数量）")
print("=" * 88)
for n in names:
    img = imgs[n]
    # 手牌大致在下 1/3，避开最底部的亮条（y>685）
    strip = img[600:690, :]
    g = cv2.cvtColor(strip, cv2.COLOR_BGR2GRAY)
    colmean = g.mean(axis=0)
    # 用列梯度找牌的竖向边界
    grad = np.abs(np.diff(colmean))
    peaks = [i for i in range(1, len(grad) - 1)
             if grad[i] > max(3.0, grad.mean() + 2 * grad.std())]
    # 合并相邻峰
    merged = []
    for p in peaks:
        if merged and p - merged[-1][-1] <= 3:
            merged[-1].append(p)
        else:
            merged.append([p])
    centers = [int(np.mean(m)) for m in merged]
    print(f"  {n:28s} 手牌条带 y600-690: 列梯度峰值 {len(centers)} 个 位置={centers[:40]}")
    if len(centers) >= 2:
        d = np.diff(centers)
        print(f"      相邻间距: {d[:30].tolist()}")
        print(f"      中位间距={np.median(d):.1f}px  -> 估算牌宽≈{np.median(d):.0f}px")

print()
print("=" * 88)
print("4) 采样出牌区的真实颜色（判断牌面外观）")
print("=" * 88)
# 来自差分结果的候选出牌区
ZONES = {
    "自己区_底中": (355, 520, 767, 606),
    "对家区_上中": (411, 207, 699, 363),
    "中部小块":   (521, 370, 594, 450),
    "右侧块":     (1039, 248, 1109, 346),
    "左侧块":     (0, 513, 86, 608),
}
ref = "手牌+所有人出两张"
for zn, (x0, y0, x1, y1) in ZONES.items():
    print(f"\n  【{zn}】 x{x0}-{x1} y{y0}-{y1}")
    for n in names:
        seg = imgs[n][y0:y1, x0:x1]
        hsv = cv2.cvtColor(seg, cv2.COLOR_BGR2HSV)
        print(f"    {n:28s} 均值BGR=({seg[:,:,0].mean():5.1f},{seg[:,:,1].mean():5.1f},"
              f"{seg[:,:,2].mean():5.1f}) V均值={hsv[:,:,2].mean():5.1f} "
              f"V>150占比={100*(hsv[:,:,2]>150).mean():5.1f}%")

print()
print("=" * 88)
print("5) 常驻变化元素（在所有对比中都变的区域）")
print("=" * 88)
change_count = np.zeros((H, W), dtype=np.int32)
for a, b in itertools.combinations(names, 2):
    g = cv2.cvtColor(cv2.absdiff(imgs[a], imgs[b]), cv2.COLOR_BGR2GRAY)
    change_count += (g > 25).astype(np.int32)
always = (change_count >= 5).astype(np.uint8)   # 6 对中至少 5 对都变
always = cv2.morphologyEx(always, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
nlab, lab, stats, cent = cv2.connectedComponentsWithStats(always, 8)
print("  在 >=5/6 对比中都变化的区域（疑似常驻动画，会持续触发『画面有变化』）：")
rows = []
for i in range(1, nlab):
    x, y, w, h, area = stats[i]
    if area < 300:
        continue
    rows.append((area, x, y, w, h))
rows.sort(reverse=True)
for area, x, y, w, h in rows[:12]:
    print(f"    x={x:4d} y={y:4d} w={w:4d} h={h:4d} 面积={area:6d}  "
          f"相对=({(x+w/2)/W:.3f},{(y+h/2)/H:.3f})")
if not rows:
    print("    无 —— 说明变化都是局面相关，非常驻动画")

print()
print("=" * 88)
print("6) 裁切关键区域，供人工核对")
print("=" * 88)
for zn, (x0, y0, x1, y1) in ZONES.items():
    for n in names:
        crop = imgs[n][y0:y1, x0:x1]
        # 放大 3 倍便于查看
        big = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_NEAREST)
        imwrite_u(f"{OUT}/zone_{zn}_{n}.png", big)
# 手牌条带整条
for n in names:
    imwrite_u(f"{OUT}/hand_{n}.png",
              cv2.resize(imgs[n][595:700, :], None, fx=2, fy=2, interpolation=cv2.INTER_NEAREST))
print(f"  已写 {OUT}/zone_*.png  （各区域 3 倍放大）")
print(f"  已写 {OUT}/hand_*.png   （手牌条带 2 倍放大）")
print()
print("  说明: 当前模型无法读图，这些裁切图需由人眼核对。")
