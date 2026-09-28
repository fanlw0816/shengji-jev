"""第二轮：用「角标切片」而非整张牌面做分析。
验证：1) 点数/花色切分线是否普遍存在  2) 角标是否能区分不同的牌
"""
import os, sys, io, json
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
SLIVER_W = 16      # 每张牌可见宽度
CORNER_H = 28      # 角标高度（点数 + 花色）
ZONES = {"top": (521, 207, 594, 286), "left": (411, 283, 484, 362),
         "right": (627, 283, 699, 362), "bottom": (521, 371, 594, 450)}


def card_mask(img):
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    return ((hsv[:, :, 2] > 165) & (hsv[:, :, 1] < 110)).astype(np.uint8)


def blobs(img, min_area=3000):
    m = cv2.morphologyEx(card_mask(img), cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area or not (50 <= w <= 200) or not (70 <= h <= 88):
            continue
        out.append((int(x), int(y), int(w), int(h)))
    return out


def nearest_zone(cx, cy, maxd=60.0):
    best, bd = None, 1e9
    for name, (x0, y0, x1, y1) in ZONES.items():
        zx, zy = (x0 + x1) / 2, (y0 + y1) / 2
        d = ((cx - zx) ** 2 + (cy - zy) ** 2) ** 0.5
        if d < bd:
            best, bd = name, d
    return best if bd <= maxd else None


def ink_of(patch):
    """角标墨迹：非白像素（暗 或 高饱和彩色）。"""
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    return (((hsv[:, :, 2] < 200) | (hsv[:, :, 1] > 90))).astype(np.uint8)


# ---- 抽取每张牌的角标切片 ----
items = []
for st in ["empty", "others_one", "next_two", "all_two"]:
    p = f"tests/fixtures/screenshots/{st}.jpg"
    if not os.path.exists(p):
        continue
    img = imread_u(p)
    for (bx, by, bw, bh) in blobs(img):
        zone = nearest_zone(bx + bw / 2, by + bh / 2)
        if zone is None:
            continue
        n = max(1, int(1 + round((bw - CARD_W) / OFFSET)))
        for i in range(n):
            x0 = bx + i * OFFSET
            x1 = min(x0 + SLIVER_W, img.shape[1])
            if x1 - x0 < SLIVER_W:
                continue
            corner = img[by:by + CORNER_H, x0:x1]
            if corner.shape[:2] != (CORNER_H, SLIVER_W):
                continue
            items.append({"state": st, "zone": zone, "slot": i, "n": n,
                          "corner": corner.copy(), "x0": x0, "y0": by})

print("=" * 94)
print(f"1) 角标切片：共 {len(items)} 张（每张 {SLIVER_W}x{CORNER_H}）")
print("=" * 94)

print()
print("=" * 94)
print("2) 点数/花色切分线验证：逐行墨迹，找中间的空隙行")
print("=" * 94)
splits = []
for i, it in enumerate(items):
    ink = ink_of(it["corner"])
    rows = ink.sum(axis=1)
    # 在上半部找连续零行（点数与花色之间）
    zero_runs = []
    run = None
    for r in range(1, CORNER_H - 1):
        if rows[r] == 0:
            run = [r, r] if run is None else [run[0], r]
        else:
            if run is not None:
                zero_runs.append(tuple(run))
                run = None
    if run is not None:
        zero_runs.append(tuple(run))
    # 只关心不贴边的空隙（既不在最顶也不在最底）
    mid = [z for z in zero_runs if z[0] > 2 and z[1] < CORNER_H - 3]
    splits.append(mid[0] if mid else None)
    tag = f"空隙 {mid[0]}" if mid else "**无空隙**"
    print(f"  #{i:2d} {it['state']:11s}{it['zone']:7s} slot{it['slot']}/{it['n']} "
          f"墨迹={int(ink.sum()):4d}  {tag}")
    print(f"       行墨迹={rows.tolist()}")

n_ok = sum(1 for s in splits if s)
print(f"\n  有干净切分线的: {n_ok}/{len(items)}")
if n_ok:
    split_rows = [s[0] for s in splits if s]
    print(f"  切分线位置: 中位={int(np.median(split_rows))} 范围={min(split_rows)}~{max(split_rows)}")

print()
print("=" * 94)
print("3) 用角标切片聚类 —— 这次特征应该够判别")
print("=" * 94)


def feat(corner):
    """角标特征：二值化墨迹 -> 缩放到固定尺寸 -> 归一化。"""
    ink = ink_of(corner).astype(np.float32)
    g = cv2.resize(ink, (12, 24), interpolation=cv2.INTER_AREA)
    g -= g.mean()
    n = np.linalg.norm(g)
    return (g / n) if n > 1e-6 else g


F = [feat(it["corner"]) for it in items]
sim = np.zeros((len(F), len(F)))
for i in range(len(F)):
    for j in range(len(F)):
        sim[i, j] = float((F[i] * F[j]).sum())

THR = 0.85
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
print(f"  阈值 {THR} -> {len(groups)} 个簇")
for gid, mem in sorted(groups.items(), key=lambda kv: -len(kv[1])):
    desc = ", ".join(f"#{m}({items[m]['state'][:4]}/{items[m]['zone'][:3]})" for m in mem)
    print(f"    簇{gid:2d} ({len(mem)}): {desc}")

print()
print("  簇内相似度 / 簇间最大相似度（判别力检查）：")
within, between = [], []
for gid, mem in groups.items():
    for a in range(len(mem)):
        for b in range(a + 1, len(mem)):
            within.append(sim[mem[a], mem[b]])
for g1 in groups.values():
    for g2 in groups.values():
        if g1 is g2 or g1[0] > g2[0]:
            continue
        for a in g1:
            for b in g2:
                between.append(sim[a, b])
if within:
    print(f"    簇内相似度: 最小={min(within):.3f} 中位={np.median(within):.3f}")
if between:
    print(f"    簇间相似度: 最大={max(between):.3f} 中位={np.median(between):.3f}")
    print(f"    -> 区分间隔 = {min(within) - max(between):+.3f} "
          f"({'可区分' if min(within) > max(between) else '不可区分！'})")

print()
print("=" * 94)
print("4) 导出角标切片（8 倍放大）供人工标注")
print("=" * 94)
manifest = []
for i, it in enumerate(items):
    name = f"corner_{i:02d}_{it['state']}_{it['zone']}_s{it['slot']}.png"
    big = cv2.resize(it["corner"], None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    imwrite_u(f"{OUT}/{name}", big)
    manifest.append({"index": i, "state": it["state"], "zone": it["zone"],
                     "slot": it["slot"], "file": name, "rank": "", "suit": ""})
with open(f"{OUT}/corner_manifest.json", "w", encoding="utf-8") as f:
    json.dump(manifest, f, ensure_ascii=False, indent=2)
print(f"  {len(manifest)} 张角标切片（8 倍放大）-> {OUT}/corner_*.png")
print(f"  清单 -> {OUT}/corner_manifest.json")
