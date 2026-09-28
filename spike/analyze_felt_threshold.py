"""实测：桌面色阈值能收多紧而不丢失空区覆盖？
目标：找到「空区覆盖 100%」前提下最小的阈值，让桌面掩码尽量只覆盖牌桌本身。
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
A = [n for n in names if "没有人出牌" in n][0]
B = [n for n in names if "所有人出两张" in n][0]

# 四区（1280x720，来自实测 blob）
Z = {"top": (521, 207, 594, 286), "left": (411, 283, 484, 362),
     "right": (627, 283, 699, 362), "bottom": (521, 371, 594, 450)}

# 空区采样得到桌面色基准
samples = np.vstack([imgs[A][y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
                     for (x0, y0, x1, y1) in Z.values()])
mu = samples.mean(axis=0)
print(f"桌面色基准 BGR = ({mu[0]:.1f},{mu[1]:.1f},{mu[2]:.1f})")
print(f"样本内标准差 = {samples.std(axis=0).round(1)}")
print()

print("=" * 96)
print("阈值扫描：空区覆盖率 vs 全图覆盖率 vs 连通块结构")
print("=" * 96)
print(f"{'thr':>5} {'空区覆盖':>10} {'全图覆盖':>10} {'最大连通块':>12} "
      f"{'块bbox':>22} {'占全图比':>10}")
for thr in (12, 18, 24, 30, 36, 45, 60, 90):
    d = np.linalg.norm(imgs[A].astype(np.float32) - mu[None, None, :], axis=2)
    m = (d < thr).astype(np.uint8)
    # 空区覆盖
    cov = np.mean([m[y0:y1, x0:x1].mean() for (x0, y0, x1, y1) in Z.values()])
    full = m.mean()
    mc = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    mc = cv2.morphologyEx(mc, cv2.MORPH_OPEN,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(mc, 8)
    if n > 1:
        big = max(range(1, n), key=lambda i: stats[i][4])
        x, y, bw, bh, area = stats[big]
        bbox = f"({x},{y}) {bw}x{bh}"
        frac = area / (H * W)
    else:
        bbox, frac = "-", 0.0
    print(f"{thr:5d} {cov*100:9.1f}% {full*100:9.1f}% {'':>12} {bbox:>22} {frac*100:9.1f}%")

print()
print("=" * 96)
print("关键判读：")
print("  · 空区覆盖要尽量 100%（否则空区会被误判为有牌/非桌面）")
print("  · 全图覆盖要尽量小（否则『区必须在桌面上』这条校验没有区分力）")
print("  · 若某阈值下最大连通块 bbox 明显小于全图 -> 该阈值能把牌桌与背景分开")
print("=" * 96)

# 额外：看看「所有人出两张」下，牌面(白)与桌面是否能干净二分
print()
print("=" * 96)
print("牌面与桌面是否干净二分（alpha 分离度）")
print("=" * 96)
for thr in (24, 45):
    d = np.linalg.norm(imgs[A].astype(np.float32) - mu[None, None, :], axis=2)
    felt = d < thr
    hsv = cv2.cvtColor(imgs[A], cv2.COLOR_BGR2HSV)
    card = (hsv[:, :, 2] > 165) & (hsv[:, :, 1] < 110)
    both = (felt & card).mean()
    neither = (~felt & ~card).mean()
    print(f"  thr={thr:3d}: 同时被判为桌面+牌面 = {both*100:5.2f}%   两者都不是 = {neither*100:5.2f}%")
print("  (理想：两者都很小 -> 桌面/牌面/其它 三分清晰)")
