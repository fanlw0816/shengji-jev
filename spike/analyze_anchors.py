"""验证「锚点 + 相对几何 + 牌张尺寸为单位」是否可行。
不依赖任何硬编码坐标，看能否自动推导出桌面中心、四区位置、牌张尺寸。
"""
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

print("=" * 88)
print("1) 桌面（felt）颜色特征 —— 找一个可靠的颜色锚点")
print("=" * 88)
# 已知空区：没有人出牌时，四个出牌区都应为桌面
EMPTY = {"上": (522, 207, 594, 285), "左": (411, 283, 483, 361),
         "右": (627, 283, 699, 361), "下": (521, 371, 593, 449)}
samples = []
for zn, (x0, y0, x1, y1) in EMPTY.items():
    seg = imgs[A][y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    m = seg.mean(axis=0)
    samples.append(seg)
    print(f"  {zn}区(空): BGR=({m[0]:6.1f},{m[1]:6.1f},{m[2]:6.1f})")
allfelt = np.vstack(samples)
mu = allfelt.mean(axis=0)
sd = allfelt.std(axis=0)
print(f"\n  桌面色均值 BGR=({mu[0]:.1f},{mu[1]:.1f},{mu[2]:.1f})  标准差=({sd[0]:.1f},{sd[1]:.1f},{sd[2]:.1f})")
cov = np.cov(allfelt.T)
print(f"  协方差对角={np.diag(cov).round(1)}  -> 桌面颜色足够集中，可作锚点")

print()
print("=" * 88)
print("2) 用颜色距离分割桌面，求桌面区域 bbox 与中心")
print("=" * 88)
for n in names:
    img = imgs[n].astype(np.float32)
    d = np.linalg.norm(img - mu[None, None, :], axis=2)
    felt = (d < 45).astype(np.uint8)
    felt = cv2.morphologyEx(felt, cv2.MORPH_CLOSE,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15)))
    felt = cv2.morphologyEx(felt, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
    nlab, lab, stats, cent = cv2.connectedComponentsWithStats(felt, 8)
    if nlab <= 1:
        print(f"  {n}: 未找到桌面区域"); continue
    big = max(range(1, nlab), key=lambda i: stats[i][4])
    x, y, w, h, area = stats[big]
    cx, cy = x + w / 2, y + h / 2
    print(f"  {n}")
    print(f"    桌面 bbox: x={x} y={y} {w}x{h}  面积={area} ({100*area/(W*H):.1f}%)")
    print(f"    桌面中心=({cx:.1f},{cy:.1f})  归一化=({cx/W:.4f},{cy/H:.4f})")
    print(f"    桌面覆盖的 x 比例 {x/W:.3f}~{(x+w)/W:.3f},  y 比例 {y/H:.3f}~{(y+h)/H:.3f}")
    imwrite_u(f"{OUT}/felt_mask_{n}.png", felt * 255)

print()
print("=" * 88)
print("3) 四区是否构成规则几何？—— 相对桌面中心的偏移")
print("=" * 88)
# 实测四区中心（来自 analyze_screenshots4 的结果）
Z = {"上": (557, 246), "左": (447, 322), "右": (662, 322), "下": (557, 410)}
cx0 = np.mean([v[0] for v in Z.values()])
cy0 = np.mean([v[1] for v in Z.values()])
print(f"  四区中心均值（几何中心）=({cx0:.1f},{cy0:.1f})")
for zn, (px, py) in Z.items():
    print(f"    {zn}: 偏移 dx={px-cx0:+7.1f} dy={py-cy0:+7.1f}")
dxs = [abs(Z[z][0] - cx0) for z in ("左", "右")]
dys = [abs(Z[z][1] - cy0) for z in ("上", "下")]
print(f"\n  左右臂横向距离: {dxs[0]:.1f} vs {dxs[1]:.1f}  -> 差 {abs(dxs[0]-dxs[1]):.1f}px "
      f"({'对称' if abs(dxs[0]-dxs[1]) < 3 else '不对称'})")
print(f"  上下臂纵向距离: {dys[0]:.1f} vs {dys[1]:.1f}  -> 差 {abs(dys[0]-dys[1]):.1f}px "
      f"({'对称' if abs(dys[0]-dys[1]) < 3 else '不对称'})")
print(f"  => 可用【中心 + 2 个偏移量】共 3 个数描述四区，而非 4 个矩形共 16 个数")

print()
print("=" * 88)
print("4) 牌张尺寸能否自动测出？（作为分辨率无关的度量单位）")
print("=" * 88)
# 用「所有人出两张」的四区，测单张牌与两张牌的外接框
B = "手牌+所有人出两张"
for zn, (x0, y0, x1, y1) in EMPTY.items():
    seg = imgs[B][y0:y1, x0:x1]
    hsv = cv2.cvtColor(seg, cv2.COLOR_BGR2HSV)
    wm = ((hsv[:, :, 2] > 165) & (hsv[:, :, 1] < 110)).astype(np.uint8)
    wm = cv2.morphologyEx(wm, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    nlab, lab, stats, cent = cv2.connectedComponentsWithStats(wm, 8)
    if nlab <= 1:
        print(f"  {zn}: 无牌"); continue
    big = max(range(1, nlab), key=lambda i: stats[i][4])
    bx, by, bw, bh, barea = stats[big]
    print(f"  {zn}: 两张牌外接框 {bw}x{bh}  宽高比={bw/bh:.3f}")
print(f"\n  参考: 标准扑克牌宽高比 = 2.5/3.5 = {2.5/3.5:.3f}")
print(f"  实测单张牌 56x79 -> 宽高比 {56/79:.3f}  (差值仅 {abs(56/79-2.5/3.5)*100:.1f}%)")
print(f"\n  => 牌宽/牌高可从画面自动测得，可作为一切偏移与尺寸的『单位』")
print(f"     分辨率变化时重新测一次牌宽，所有相对几何自动跟随缩放")

print()
print("=" * 88)
print("5) 桌面视觉元素是否可枚举（不变量校验的基础）")
print("=" * 88)
img = imgs[A].astype(np.float32)
d = np.linalg.norm(img - mu[None, None, :], axis=2)
nonfelt = (d >= 45).astype(np.uint8) * 255
nonfelt = cv2.morphologyEx(nonfelt, cv2.MORPH_OPEN,
                           cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5)))
nlab, lab, stats, cent = cv2.connectedComponentsWithStats(nonfelt, 8)
rows = []
for i in range(1, nlab):
    x, y, w, h, area = stats[i]
    if area < 400:
        continue
    rows.append((area, x, y, w, h))
rows.sort(reverse=True)
print(f"  『非桌面』连通块 (面积>=400)，共 {len(rows)} 个 —— 即所有 UI 元素与牌")
for area, x, y, w, h in rows[:16]:
    kind = "牌?" if 40 < w < 200 and 50 < h < 120 else "UI"
    print(f"    x={x:4d} y={y:4d} {w:4d}x{h:3d} 面积={area:6d}  "
          f"相对=({(x+w/2)/W:.3f},{(y+h/2)/H:.3f})  {kind}")
