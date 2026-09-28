"""诊断：DPI 缩放 + dxcam/mss 实际抓到的内容对比。"""
import ctypes, ctypes.wintypes as wt, time
import numpy as np, cv2

# ---------- 1) DPI 缩放 ----------
ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
shcore = ctypes.windll.shcore
user32 = ctypes.windll.user32
print("=" * 78)
print("1) 显示与 DPI")
print("=" * 78)
try:
    user32.SetProcessDPIAware()
except Exception:
    pass
try:
    print("  进程 DPI 感知:", shcore.GetProcessDpiAwareness(None))
except Exception as e:
    print("  GetProcessDpiAwareness 失败:", e)

try:
    GetDpiForMonitor = shcore.GetDpiForMonitor
    for i, name in enumerate(["PRIMARY", "SECONDARY"]):
        dx, dy = wt.UINT(), wt.UINT()
        hr = GetDpiForMonitor(i, 0, ctypes.byref(dx), ctypes.byref(dy))
        print(f"  Monitor{i} ({name}): dpiX={dx.value} dpiY={dy.value} -> 缩放 {dx.value/96*100:.0f}%  (hr={hr})")
except Exception as e:
    print("  GetDpiForMonitor 失败:", e)

print("  GetSystemMetrics(SM_CXSCREEN) =", user32.GetSystemMetrics(0), "x", user32.GetSystemMetrics(1))
print("  虚拟桌面 =", user32.GetSystemMetrics(78), "x", user32.GetSystemMetrics(79))

# ---------- 2) 动画窗口，强制置顶 + 已知物理坐标 ----------
import tkinter as tk
WIN_X, WIN_Y, WIN_W, WIN_H = 300, 300, 800, 600
root = tk.Tk()
root.overrideredirect(False)
root.attributes("-topmost", True)
root.geometry(f"{WIN_W}x{WIN_H}+{WIN_X}+{WIN_Y}")
root.update_idletasks()
# 拿到 Tk 认为的真实位置
print(f"\n  Tk 窗口 geometry: {root.winfo_geometry()}  rootx={root.winfo_rootx()} rooty={root.winfo_rooty()}")

canvas = tk.Canvas(root, width=WIN_W, height=WIN_H, bg="#203040", highlightthickness=0)
canvas.pack(fill="both", expand=True)
box = canvas.create_rectangle(0, 0, 200, 200, fill="#ff0000", outline="")
state = {"n": 0, "run": True}
def animate():
    if not state["run"]:
        return
    state["n"] += 1
    x = (state["n"] * 13) % (WIN_W - 200)
    canvas.coords(box, x, 150, x + 200, 350)
    canvas.itemconfig(box, fill=["#ff0000", "#00ff00", "#0000ff", "#ffff00"][state["n"] % 4])
    root.after(8, animate)
root.after(0, animate)

import threading
out = {}

def probe():
    time.sleep(1.2)
    import dxcam, mss
    region = (WIN_X, WIN_Y, WIN_X + WIN_W, WIN_Y + WIN_H)

    # dxcam: 抓两次，间隔 0.4s
    cam = dxcam.create(output_idx=0, output_color="BGR")
    d1 = cam.grab(region=region); time.sleep(0.4); d2 = cam.grab(region=region)
    out["dxcam_ok"] = d1 is not None and d2 is not None
    if out["dxcam_ok"]:
        out["dxcam_shape"] = d1.shape
        out["dxcam_mean"] = float(d1.mean())
        out["dxcam_diff"] = float(cv2.absdiff(cv2.cvtColor(d1, cv2.COLOR_BGR2GRAY),
                                             cv2.cvtColor(d2, cv2.COLOR_BGR2GRAY)).mean())
        cv2.imwrite("_probe_dxcam.png", d1)
    # dxcam 全屏抓 + 裁同一区域（对照 region 模式）
    dfull = cam.grab()
    if dfull is not None:
        crop = dfull[WIN_Y:WIN_Y+WIN_H, WIN_X:WIN_X+WIN_W]
        out["dxcam_fullshape"] = dfull.shape
        out["dxcam_full_mean"] = float(dfull.mean())
        if out["dxcam_ok"]:
            out["dxcam_region_vs_crop_diff"] = float(cv2.absdiff(
                cv2.cvtColor(d1, cv2.COLOR_BGR2GRAY), cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)).mean())
    cam.release()

    # mss: 同一区域两次
    with mss.MSS() as sct:
        m1 = np.array(sct.grab({"left": WIN_X, "top": WIN_Y, "width": WIN_W, "height": WIN_H}))[:, :, :3]
        time.sleep(0.4)
        m2 = np.array(sct.grab({"left": WIN_X, "top": WIN_Y, "width": WIN_W, "height": WIN_H}))[:, :, :3]
        out["mss_shape"] = m1.shape
        out["mss_mean"] = float(m1.mean())
        out["mss_diff"] = float(cv2.absdiff(cv2.cvtColor(m1, cv2.COLOR_BGR2GRAY),
                                            cv2.cvtColor(m2, cv2.COLOR_BGR2GRAY)).mean())
        cv2.imwrite("_probe_mss.png", m1)
        # mss 整屏成本
        t0 = time.perf_counter()
        for _ in range(60):
            sct.grab(sct.monitors[1])
        out["mss_full_ms"] = (time.perf_counter() - t0) / 60 * 1000

    state["run"] = False
    root.quit()

threading.Thread(target=probe, daemon=True).start()
root.mainloop()
try: root.destroy()
except Exception: pass

print()
print("=" * 78)
print("2) 抓屏内容对比（同一物理区域 800x600，窗口内有移动色块）")
print("=" * 78)
print(f"  dxcam region 模式 : shape={out.get('dxcam_shape')} mean={out.get('dxcam_mean', float('nan')):.1f} "
      f"两帧差异={out.get('dxcam_diff', float('nan')):.2f}")
print(f"  dxcam 全屏       : shape={out.get('dxcam_fullshape')} mean={out.get('dxcam_full_mean', float('nan')):.1f}")
print(f"     region vs 全屏裁剪 差异 = {out.get('dxcam_region_vs_crop_diff', float('nan')):.2f}")
print(f"  mss              : shape={out.get('mss_shape')} mean={out.get('mss_mean', float('nan')):.1f} "
      f"两帧差异={out.get('mss_diff', float('nan')):.2f}")
print(f"  mss 整屏成本     : {out.get('mss_full_ms', float('nan')):.2f} ms")
print()
print("  判读标准: 两帧差异 > 1.0 说明抓到了动画; ≈0 说明抓到的是静止画面(抓错区域或复制失败)")
