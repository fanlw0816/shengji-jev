"""分析 4 张不同局面的真实截图。
用途：
  1) 判断截图是否可用（尺寸/格式/一致性）
  2) 两两差分定位「出牌区」——直接验证 §4.3 的 ROI 方案
  3) 牌面卡片可检测性初判（颜色/亮度分割）
注意：Windows 中文路径必须用 np.fromfile + cv2.imdecode，cv2.imread 会失败。
"""
import glob, os, sys, io, itertools, json
import numpy as np
import cv2

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
OUT = "spike/shot_analysis"
os.makedirs(OUT, exist_ok=True)


def imread_unicode(path, flags=cv2.IMREAD_COLOR):
    """Windows 中文路径安全读图。"""
    buf = np.fromfile(path, dtype=np.uint8)
    return cv2.imdecode(buf, flags)


def imwrite_unicode(path, img):
    ext = os.path.splitext(path)[1]
    ok, buf = cv2.imencode(ext, img)
    if ok:
        buf.tofile(path)
    return ok


files = sorted(glob.glob("png/*.jpg") + glob.glob("png/*.png"))
print("=" * 88)
print("1) 基本属性")
print("=" * 88)
imgs = {}
for p in files:
    img = imread_unicode(p)
    name = os.path.splitext(os.path.basename(p))[0]
    if img is None:
        print(f"  {name:28s} 读取失败")
        continue
    imgs[name] = img
    print(f"  {name:28s} {img.shape[1]}x{img.shape[0]}  ch={img.shape[2]}  "
          f"mean={img.mean():6.1f}  {os.path.getsize(p)/1024:6.0f}KB")

names = list(imgs.keys())
if len(names) < 2:
    print("图片不足两张，无法差分"); raise SystemExit

shapes = {imgs[n].shape for n in names}
print(f"\n  尺寸是否完全一致: {'是' if len(shapes)==1 else '否 -> ' + str(shapes)}")

print()
print("=" * 88)
print("2) 两两差分 —— 变化区域即出牌区（验证 ROI 方案）")
print("=" * 88)
H, W = imgs[names[0]].shape[:2]
for a, b in itertools.combinations(names, 2):
    d = cv2.absdiff(imgs[a], imgs[b])
    g = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
    m = (g > 25).astype(np.uint8)
    n_px = int(m.sum())
    print(f"\n  【{a}】 vs 【{b}】")
    print(f"    差异像素: {n_px} ({n_px/(H*W)*100:.2f}%)  均值差异={g.mean():.2f}")
    if n_px == 0:
        print("    无差异")
        continue
    # 形态学连接邻近变化（同一张牌的各部分）
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25))
    mc = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k, iterations=2)
    nlab, lab, stats, cent = cv2.connectedComponentsWithStats(mc, 8)
    boxes = []
    for i in range(1, nlab):
        x, y, w, h, area = stats[i]
        if area < 400:
            continue
        boxes.append((area, x, y, w, h))
    boxes.sort(reverse=True)
    for area, x, y, w, h in boxes[:8]:
        cx, cy = x + w / 2, y + h / 2
        print(f"    变化块: x={x:4d} y={y:4d} w={w:4d} h={h:4d}  面积={area:7d}  "
              f"中心=({cx:6.0f},{cy:6.0f})  相对=({cx/W:.3f},{cy/H:.3f})")

print()
print("=" * 88)
print("3) 牌面可检测性（亮色卡片分割）")
print("=" * 88)
for n in names:
    img = imgs[n]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    # 扑克牌牌面偏白/亮，牌桌底色通常暗
    v = hsv[:, :, 2]
    for thr in (170, 200):
        mask = (v > thr).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (9, 9)))
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cand = []
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if w < 20 or h < 30:
                continue
            ar = w / h
            if not (0.35 < ar < 1.6):   # 扑克牌大致竖长或方
                continue
            cand.append((w * h, x, y, w, h))
        cand.sort(reverse=True)
        print(f"  {n:28s} V>{thr}: {len(cand):3d} 个候选")
        if thr == 200:
            for area, x, y, w, h in cand[:10]:
                print(f"        x={x:4d} y={y:4d} {w:3d}x{h:3d} 宽高比={w/h:.2f}")
    # 保存亮度掩码供人工核对
    imwrite_unicode(f"{OUT}/{n}_V200_mask.png", (v > 200).astype(np.uint8) * 255)

print()
print("=" * 88)
print("4) 图像整体结构（行/列亮度剖面，粗略定位手牌区与牌桌区）")
print("=" * 88)
for n in names:
    g = cv2.cvtColor(imgs[n], cv2.COLOR_BGR2GRAY)
    rows = g.mean(axis=1)
    cols = g.mean(axis=0)
    print(f"\n  {n}")
    print(f"    行亮度: 上1/3={rows[:H//3].mean():5.1f}  中1/3={rows[H//3:2*H//3].mean():5.1f}  "
          f"下1/3={rows[2*H//3:].mean():5.1f}")
    print(f"    最亮行区间: {np.argsort(rows)[-3:][::-1]} (亮度 {np.sort(rows)[-3:][::-1]})")
    print(f"    最暗行区间: {np.argsort(rows)[:3]} (亮度 {np.sort(rows)[:3]})")

print()
print("=" * 88)
print("5) 生成人工核对用标注图")
print("=" * 88)
base = names[0] if "手牌+没有人出牌" not in names else "手牌+没有人出牌"
if "手牌+没有人出牌" in names and "手牌+所有人出两张" in names:
    a, b = "手牌+没有人出牌", "手牌+所有人出两张"
    d = cv2.absdiff(imgs[a], imgs[b])
    g = cv2.cvtColor(d, cv2.COLOR_BGR2GRAY)
    m = (g > 25).astype(np.uint8)
    mc = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                          cv2.getStructuringElement(cv2.MORPH_RECT, (25, 25)), iterations=2)
    vis = imgs[b].copy()
    nlab, lab, stats, cent = cv2.connectedComponentsWithStats(mc, 8)
    for i in range(1, nlab):
        x, y, w, h, area = stats[i]
        if area < 400:
            continue
        cv2.rectangle(vis, (x, y), (x + w, y + h), (0, 0, 255), 3)
    imwrite_unicode(f"{OUT}/diff_anno.png", vis)
    # 差异热力图叠加
    heat = cv2.applyColorMap(cv2.normalize(g, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8),
                             cv2.COLORMAP_JET)
    imwrite_unicode(f"{OUT}/diff_heat.png", cv2.addWeighted(imgs[b], 0.45, heat, 0.55, 0))
    print(f"  已写: {OUT}/diff_anno.png  (红框=变化区)")
    print(f"  已写: {OUT}/diff_heat.png  (热力图)")
for n in names:
    imwrite_unicode(f"{OUT}/{n}.png", imgs[n])
print(f"  已写: {OUT}/ 下各原图副本 + 亮度掩码")
