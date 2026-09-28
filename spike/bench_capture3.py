"""真实抓屏成本：开一个 60FPS 动画窗口，测 dxcam 等待新帧的真实耗时。"""
import threading, time
import tkinter as tk
import numpy as np
import cv2
import dxcam

WIN_X, WIN_Y, WIN_W, WIN_H = 200, 200, 900, 700

# ---------- 动画窗口（模拟游戏画面持续刷新）----------
root = tk.Tk()
root.geometry(f"{WIN_W}x{WIN_H}+{WIN_X}+{WIN_Y}")
root.title("anim")
canvas = tk.Canvas(root, width=WIN_W, height=WIN_H, bg="black", highlightthickness=0)
canvas.pack()
rect = canvas.create_rectangle(0, 0, 120, 120, fill="red", outline="")
state = {"n": 0, "running": True}

def animate():
    if not state["running"]:
        return
    state["n"] += 1
    x = (state["n"] * 7) % (WIN_W - 120)
    canvas.coords(rect, x, 100, x + 120, 220)
    canvas.itemconfig(rect, fill=["red", "green", "blue", "yellow"][state["n"] % 4])
    root.after(8, animate)   # ~120Hz 请求，实际受 60Hz vsync 限制

root.after(0, animate)

# ---------- 抓屏测量（后台线程）----------
results = {}

def capture_bench():
    time.sleep(1.5)  # 等窗口稳定
    cam = dxcam.create(output_idx=0, output_color="BGR")
    region = (WIN_X, WIN_Y, WIN_X + WIN_W, WIN_Y + WIN_H)

    # A) 阻塞式 grab() 的真实耗时
    for _ in range(10):
        cam.grab(region=region)
    t0 = time.perf_counter()
    N = 120
    got = 0
    for _ in range(N):
        f = cam.grab(region=region)
        if f is not None:
            got += 1
    dt = (time.perf_counter() - t0) / N * 1000
    results["grab_blocking_ms"] = dt
    results["grab_fps"] = 1000 / dt
    results["grab_nonnull"] = got

    # B) start(target_fps=60) + get_latest_frame 的真实吞吐
    cam.release()
    cam = dxcam.create(output_idx=0, output_color="BGR")
    cam.start(region=region, target_fps=60, video_mode=True)
    time.sleep(0.5)
    t0 = time.perf_counter()
    ticks = set()
    distinct = 0
    last = None
    while time.perf_counter() - t0 < 3.0:
        f = cam.get_latest_frame()
        if f is not None:
            tk_ = cam.latest_frame_ticks
            if tk_ != last:
                distinct += 1
                last = tk_
        ticks.add(f is not None)
    elapsed = time.perf_counter() - t0
    cam.stop()
    cam.release()
    results["stream_distinct_fps"] = distinct / elapsed
    results["stream_distinct"] = distinct
    results["stream_elapsed"] = elapsed

    # C) 抓到的帧内容是否真的是动画（非黑屏验证）
    cam = dxcam.create(output_idx=0, output_color="BGR")
    f1 = cam.grab(region=region)
    time.sleep(0.3)
    f2 = cam.grab(region=region)
    if f1 is not None and f2 is not None:
        results["frame_shape"] = f1.shape
        results["frame_mean"] = float(f1.mean())
        results["frames_differ"] = float(cv2.absdiff(
            cv2.cvtColor(f1, cv2.COLOR_BGR2GRAY),
            cv2.cvtColor(f2, cv2.COLOR_BGR2GRAY)).mean())
        # 做成图片证据
        cv2.imwrite("_bench_capture_sample.png", f1)
    cam.release()
    state["running"] = False
    root.quit()

threading.Thread(target=capture_bench, daemon=True).start()
root.mainloop()
try:
    root.destroy()
except Exception:
    pass

print("=" * 74)
print("真实抓屏成本（窗口内 60Hz 动画持续刷新）")
print("=" * 74)
print(f"  A) 阻塞式 grab(region=900x700) 平均: {results.get('grab_blocking_ms', float('nan')):.2f} ms")
print(f"     -> 等效 {results.get('grab_fps', float('nan')):.1f} FPS   (非空帧 {results.get('grab_nonnull')}/120)")
print(f"  B) start(target_fps=60) 流式真实新帧率: {results.get('stream_distinct_fps', float('nan')):.1f} FPS "
      f"({results.get('stream_distinct')} 帧 / {results.get('stream_elapsed', 0):.2f}s)")
print(f"  C) 抓帧形状: {results.get('frame_shape')}  均值: {results.get('frame_mean', float('nan')):.1f}  "
      f"两帧差异: {results.get('frames_differ', float('nan')):.1f}")
print()
print("  参考: mss 整屏 BitBlt = 18.75 ms (53 FPS)")
print("=" * 74)
