"""分析截图中的牌面：能抽出多少张牌？角标结构长什么样？
这决定识别层的模板库怎么建。
"""
import glob, os, sys, io, json
import numpy as np
import cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
OUT = "spike/card_analysis"
os.makedirs(OUT, exist_ok=True)


def imread_u(p):
    return cv2.imdecode(np.fromfile(p, dtype=np.uint8), cv2.IMREAD_COLOR)


def imwrite_u(p, img):
    ok, b = cv2.imencode(os.path.splitext(p)[1], img)
    if ok:
        b.tofile(p)


CARD_W, CARD_H, OFFSET = 56, 79, 16
ZONES = {"top": (521, 207, 594, 286), "left": (411, 283, 484, 362),
         "right": (627, 283, 699, 362), "bottom": (521, 371, 594, 450)}


def card_mask(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 2] > 165) & (hsv[:, :, 1] < 110)).astype(np.uint8)


def card_sized_blobs(img, min_area=3000):
    m = cv2.morphologyEx(card_mask(img), cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area or not (50 <= w <= 200) or not (70 <= h <= 88):
            continue
        out.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h), "area": int(area)})
    return out


def nearest_zone(cx, cy, maxd=60.0):
    best, bd = None, 1e9
    for name, (x0, y0, x1, y1) in ZONES.items():
        zx, zy = (x0 + x1) / 2, (y0 + y1) / 2
        d = ((cx - zx) ** 2 + (cy - zy) ** 2) ** 0.5
        if d < bd:
            best, bd = name, d
    return (best, bd) if bd <= maxd else (None, bd)


def estimate_count(w):
    if w < CARD_W * 0.6:
        return 1
    return max(1, int(1 + round((w - CARD_W) / OFFSET)))


states = ["empty", "others_one", "next_two", "all_two"]
cards = []
for st in states:
    p = f"tests/fixtures/screenshots/{st}.jpg"
    if not os.path.exists(p):
        continue
    img = imread_u(p)
    for b in card_sized_blobs(img):
        cx, cy = b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
        zone, d = nearest_zone(cx, cy)
        if zone is None:
            continue
        n = estimate_count(b["w"])
        for i in range(n):
            # 两张牌叠放：第 i 张左边缘在 x0 + i*OFFSET
            x0 = b["x"] + i * OFFSET
            x1 = min(x0 + CARD_W, img.shape[1])
            if x1 - x0 < CARD_W * 0.7:
                continue
            crop = img[b["y"]:b["y"] + CARD_H, x0:x1]
            if crop.shape[0] < CARD_H or crop.shape[1] < CARD_W:
                continue
            cards.append({"state": st, "zone": zone, "idx": i, "count": n,
                          "img": crop.copy()})

print("=" * 92)
print(f"1) 抽出的牌实例：共 {len(cards)} 张")
print("=" * 92)
for i, c in enumerate(cards):
    im = c["img"]
    print(f"  #{i:2d} {c['state']:11s} {c['zone']:6s} 第{c['idx']+1}/{c['count']}张  "
          f"尺寸={im.shape[1]}x{im.shape[0]}  亮度={im.mean():5.1f}  "
          f"左上8x8均值BGR=({im[0:8,0:8,0].mean():5.1f},{im[0:8,0:8,1].mean():5.1f},"
          f"{im[0:8,0:8,2].mean():5.1f})")
    imwrite_u(f"{OUT}/card_{i:02d}_{c['state']}_{c['zone']}.png",
              cv2.resize(im, None, fx=4, fy=4, interpolation=cv2.INTER_NEAREST))

print()
print("=" * 92)
print("2) 角标结构分析：牌面左上区域（前 26x40）的『墨迹』分布")
print("=" * 92)
print("  说明：牌面是白底，点数与花色是深色/彩色。用『非白像素』当墨迹。")
for i, c in enumerate(cards[:6]):
    im = c["img"]
    corner = im[0:40, 0:26]
    hsv = cv2.cvtColor(corner, cv2.COLOR_BGR2HSV)
    ink = ((hsv[:, :, 2] < 200) | (hsv[:, :, 1] > 90)).astype(np.uint8)
    rows = ink.sum(axis=1)
    cols = ink.sum(axis=0)
    print(f"\n  #{i} {c['state']}/{c['zone']}  角标墨迹总量={int(ink.sum())}px")
    print(f"    逐行墨迹: {rows.tolist()}")
    print(f"    逐列墨迹(前18列): {cols[:18].tolist()}")

print()
print("=" * 92)
print("3) 按整张牌面聚类，看有多少种不同的牌")
print("=" * 92)
# 用灰度缩放后做互相关距离
def feat(im):
    g = cv2.cvtColor(im, cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (28, 40), interpolation=cv2.INTER_AREA)
    g = g.astype(np.float32)
    g -= g.mean()
    n = np.linalg.norm(g)
    return g / n if n > 1e-6 else g


F = [feat(c["img"]) for c in cards]
sim = np.zeros((len(F), len(F)))
for i in range(len(F)):
    for j in range(len(F)):
        sim[i, j] = float((F[i] * F[j]).sum())

THR = 0.90
parent = list(range(len(F)))
def find(a):
    while parent[a] != a:
        parent[a] = parent[parent[a]]
        a = parent[a]
    return a
def union(a, b):
    ra, rb = find(a), find(b)
    if ra != rb:
        parent[rb] = ra

for i in range(len(F)):
    for j in range(i + 1, len(F)):
        if sim[i, j] > THR:
            union(i, j)

groups = {}
for i in range(len(F)):
    groups.setdefault(find(i), []).append(i)
print(f"  相似度阈值 {THR} -> {len(groups)} 个簇（即至少 {len(groups)} 种不同的牌）")
for gid, members in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    desc = ", ".join(f"#{m}({cards[m]['state'][:4]})" for m in members)
    print(f"    簇 {gid:2d} ({len(members)} 张): {desc}")

print()
print("=" * 92)
print("4) 生成待标注清单")
print("=" * 92)
manifest = []
for i, c in enumerate(cards):
    manifest.append({"index": i, "state": c["state"], "zone": c["zone"],
                     "card_file": f"card_{i:02d}_{c['state']}_{c['zone']}.png",
                     "rank": "", "suit": ""})
with open(f"{OUT}/label_manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
print(f"  已写 {OUT}/label_manifest.json（{len(manifest)} 条，rank/suit 待填）")
print(f"  牌面图（4 倍放大）已写在 {OUT}/ 下")
print()
print("  提示：当前模型无法读图，需要人眼标注 rank/suit。")
