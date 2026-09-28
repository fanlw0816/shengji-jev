"""补充实测：dxcam 真实性能 + ROI 切片成本 + 真实尺寸模板匹配。"""
import time
import numpy as np
import cv2

def bench(label, fn, n=200):
    try:
        fn()
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        dt = (time.perf_counter() - t0) / n * 1000
        print(f"{label:44s} {dt:8.3f} ms   -> {1000/dt:7.1f} FPS")
        return dt
    except Exception as e:
        print(f"{label:44s} FAILED: {type(e).__name__}: {e}")
        return None

print("=" * 80)
print("1) dxcam 正确用法")
print("=" * 80)
import dxcam
cam = dxcam.create(output_idx=0, output_color="BGR")
print("cam:", type(cam).__name__, "| attrs:", [a for a in dir(cam) if not a.startswith('_')])
frame = cam.grab()
print("frame:", None if frame is None else (frame.shape, frame.dtype))

print()
print("--- grab() 无新帧情况 (重复调用) ---")
bench("cam.grab()", lambda: cam.grab(), n=200)

print()
print("--- 模拟 60Hz 循环: 抓屏 + 4ROI切片 + 差分 ---")
ROIS = [(900,300,1500,500),(900,500,1500,700),(900,700,1500,900),(900,900,1500,1100)]
state = {i: None for i in range(4)}

def full_cycle():
    f = cam.grab()
    if f is None:
        return
    for i, (x0,y0,x1,y1) in enumerate(ROIS):
        roi = f[y0:y1, x0:x1]
        g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        if state[i] is not None:
            d = cv2.absdiff(g, state[i])
            _ = float(d.mean())
        state[i] = g

bench("60Hz cycle: grab + 4x slice + 4x diff", full_cycle, n=200)

print()
print("--- 热路径优化: 抓屏 + 只做 1 次 ROI 差分(仅监控一个区) ---")
def hot_cycle():
    f = cam.grab()
    if f is None:
        return
    roi = f[300:500, 900:1500]
    g = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    if state[0] is not None:
        _ = float(cv2.absdiff(g, state[0]).mean())
    state[0] = g

bench("grab + 1 ROI diff", hot_cycle, n=200)

cam.release()

print()
print("=" * 80)
print("2) dxcam region 模式（只抓 ROI）")
print("=" * 80)
cam2 = dxcam.create(output_idx=0, output_color="BGR")
try:
    r = cam2.grab(region=(900, 300, 1500, 1100))
    print("region grab shape:", None if r is None else r.shape)
    bench("cam.grab(region=600x800)", lambda: cam2.grab(region=(900,300,1500,1100)), n=200)
except Exception as e:
    print("region grab FAILED:", type(e).__name__, e)
cam2.release()

print()
print("=" * 80)
print("3) 真实尺寸模板匹配：牌角标 ~ (30x22) 在牌面 (110x80) 内搜索")
print("=" * 80)
# 真实牌面尺寸估计：一墩 8 张牌挤在 600px 宽 -> 每张约 75px 宽
card = (np.random.rand(100, 70, 3) * 255).astype(np.uint8)
tmpl_corner = (np.random.rand(30, 22, 3) * 255).astype(np.uint8)   # 只取角标
tmpl_full = (np.random.rand(100, 70, 3) * 255).astype(np.uint8)    # 整张牌

dt_corner = bench("matchTemplate corner(30x22) in card(70x100)", lambda: cv2.matchTemplate(card, tmpl_corner, cv2.TM_CCOEFF_NORMED), n=3000)
dt_full = bench("matchTemplate full-card in card (identity)", lambda: cv2.matchTemplate(card, tmpl_full, cv2.TM_CCOEFF_NORMED), n=3000)

if dt_corner:
    print(f"\n  【1 墩 = 最多 8 张牌】x【54 类模板】")
    print(f"    corner 模板: {8*54*dt_corner:8.1f} ms / 墩")
if dt_full:
    print(f"    full   模板: {8*54*dt_full:8.1f} ms / 墩")

print()
print("=" * 80)
print("4) 另一种思路：多尺度 + 只在稳定期识别，量化每秒事件预算")
print("=" * 80)
print("  升级一墩 4 人各出牌，稳定期约 1~3 秒 -> 每 2 秒 1 次识别请求")
print(f"  若单墩识别 200ms: CPU 占用 = 200/2000 = 10% 单核")
