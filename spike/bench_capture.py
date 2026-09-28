"""实测抓屏成本：决定截图频率的硬数据。用完即弃的探针脚本。"""
import time
import numpy as np

W, H = 2560, 1440
# 模拟四个出牌区 ROI（牌桌中部，每块约 600x200）
ROIS = [
    (900, 300, 1500, 500),
    (900, 500, 1500, 700),
    (900, 700, 1500, 900),
    (900, 900, 1500, 1100),
]


def bench(label, fn, n=200):
    try:
        fn()  # warmup
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        dt = (time.perf_counter() - t0) / n * 1000
        print(f"{label:38s} {dt:8.3f} ms/frame   -> {1000/dt:7.1f} FPS")
        return dt
    except Exception as e:
        print(f"{label:38s} FAILED: {type(e).__name__}: {e}")
        return None


print("=" * 78)
print("1) dxcam (Desktop Duplication API)")
print("=" * 78)
try:
    import dxcam
    print("monitors:", dxcam.output_info())
    cam_full = dxcam.create(output_idx=0, output_color="BGR")
    if cam_full is None:
        print("dxcam.create returned None (no output / unsupported)")
    else:
        print("device:", cam_full.device, "| is_cuda:", getattr(cam_full, "is_cuda", None))
        bench("dxcam full-screen grab", lambda: cam_full.grab())
        cam_full.release()
except Exception as e:
    print("dxcam import/setup FAILED:", type(e).__name__, e)

print()
print("=" * 78)
print("2) mss (BitBlt via GDI, per-region)")
print("=" * 78)
try:
    import mss
    with mss.mss() as sct:
        print("monitors:", sct.monitors)
        mon = sct.monitors[1]

        def mss_4roi():
            for (x0, y0, x1, y1) in ROIS:
                sct.grab({"left": x0, "top": y0, "width": x1 - x0, "height": y1 - y0})

        bench("mss 4 ROI (600x200 each)", mss_4roi)
        bench("mss full screen", lambda: sct.grab(mon))
except Exception as e:
    print("mss FAILED:", type(e).__name__, e)

print()
print("=" * 78)
print("3) 纯计算成本：差分 + 阈值判定（不含抓屏）")
print("=" * 78)
roi = (np.random.rand(200, 600, 3) * 255).astype(np.uint8)
prev = roi.copy()


def d1():
    cv2.absdiff(roi, prev)


def d2():
    g1 = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    g2 = cv2.cvtColor(prev, cv2.COLOR_BGR2GRAY)
    d = cv2.absdiff(g1, g2)
    cv2.mean(d)[0]


try:
    import cv2
    bench("absdiff only (200x600x3)", d1, n=2000)
    bench("gray+absdiff+mean (200x600)", d2, n=2000)
except Exception as e:
    print("cv2 FAILED:", type(e).__name__, e)

print()
print("=" * 78)
print("4) 模板匹配成本（单张牌角标 vs 54 类模板）")
print("=" * 78)
try:
    import cv2
    tmpl = (np.random.rand(28, 20, 3) * 255).astype(np.uint8)
    card = (np.random.rand(120, 80, 3) * 255).astype(np.uint8)

    def match_once():
        cv2.matchTemplate(card, tmpl, cv2.TM_CCOEFF_NORMED)

    dt = bench("matchTemplate 1 card x 1 tmpl", match_once, n=2000)
    if dt:
        # 一墩最多 8 张牌（4人x2副），54 类模板
        print(f"  -> 估算: 8 张牌 x 54 模板 = {8*54*dt:8.1f} ms/墩")
        print(f"  -> 估算: 若用角标区域直接比对(降到 1/4) ~ {8*54*dt/4:8.1f} ms/墩")
except Exception as e:
    print("cv2 match FAILED:", type(e).__name__, e)
