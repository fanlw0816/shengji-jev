"""决定性基准：dxcam 真实吞吐 + "无变化返回 None" 的增量语义验证。
这个结论直接决定截图频率该怎么设计。"""
import ctypes, time, threading
import numpy as np, cv2
import tkinter as tk

ctypes.windll.user32.SetProcessDPIAware()

WIN_X, WIN_Y, WIN_W, WIN_H = 300, 300, 800, 600
root = tk.Tk()
root.attributes("-topmost", True)
root.geometry(f"{WIN_W}x{WIN_H}+{WIN_X}+{WIN_Y}")
root.update_idletasks()
canvas = tk.Canvas(root, width=WIN_W, height=WIN_H, bg="#203040", highlightthickness=0)
canvas.pack(fill="both", expand=True)
box = canvas.create_rectangle(0, 0, 200, 200, fill="#ff0000", outline="")
st = {"n": 0, "anim": True}

def animate():
    if not st["anim"]:
        return
    st["n"] += 1
    x = (st["n"] * 13) % (WIN_W - 200)
    canvas.coords(box, x, 150, x + 200, 350)
    canvas.itemconfig(box, fill=["#ff0000", "#00ff00", "#0000ff", "#ffff00"][st["n"] % 4])
    root.after(8, animate)

root.after(0, animate)
R = {}

def probe():
    time.sleep(1.0)
    import dxcam
    region = (WIN_X, WIN_Y, WIN_X + WIN_W, WIN_Y + WIN_H)
    cam = dxcam.create(output_idx=0, output_color="BGR")
    for _ in range(20):
        cam.grab(region=region)

    # ---- Part 1: 动画中，阻塞式 grab 真实延迟 ----
    t0 = time.perf_counter(); nonnull = 0; N = 300
    for _ in range(N):
        if cam.grab(region=region) is not None:
            nonnull += 1
    el = time.perf_counter() - t0
    R["p1_ms"] = el / N * 1000
    R["p1_fps"] = N / el
    R["p1_nonnull"] = nonnull

    # ---- Part 2: 停止动画，验证"无变化 -> None" ----
    st["anim"] = False
    time.sleep(0.6)
    for _ in range(10):
        cam.grab(region=region)
    nonnull2 = 0; N2 = 200
    t0 = time.perf_counter()
    for _ in range(N2):
        if cam.grab(region=region) is not None:
            nonnull2 += 1
    el2 = time.perf_counter() - t0
    R["p2_ms"] = el2 / N2 * 1000
    R["p2_fps"] = N2 / el2
    R["p2_nonnull"] = nonnull2
    st["anim"] = True
    time.sleep(0.5)

    # ---- Part 3: 生产热路径（抓屏 + 4ROI差分 + 稳定判定）----
    ROIS = [(0, 0, 400, 200), (400, 0, 800, 200), (0, 200, 400, 400), (400, 200, 800, 400)]
    prev = [None] * 4; stable = [0] * 4; THRESH = 2.0
    def hot():
        f = cam.grab(region=region)
        if f is None:
            return "nochange"
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        for i, (a, b, c, d) in enumerate(ROIS):
            cur = g[b:d, a:c]
            if prev[i] is not None:
                if float(cv2.absdiff(cur, prev[i]).mean()) < THRESH:
                    stable[i] += 1
                else:
                    stable[i] = 0
            prev[i] = cur
        return "change"

    for _ in range(20):
        hot()
    t0 = time.perf_counter(); cpu0 = time.process_time()
    counts = {"change": 0, "nochange": 0}
    N3 = 300
    for _ in range(N3):
        counts[hot()] += 1
    el3 = time.perf_counter() - t0
    cpu3 = time.process_time() - cpu0
    R["p3_ms"] = el3 / N3 * 1000
    R["p3_cpu_ms"] = cpu3 / N3 * 1000
    R["p3_counts"] = counts

    # ---- Part 4: start(target_fps=60) 流式真实新帧率 ----
    cam.release()
    cam = dxcam.create(output_idx=0, output_color="BGR")
    cam.start(region=region, target_fps=60, video_mode=True)
    time.sleep(0.4)
    t0 = time.perf_counter(); last = None; distinct = 0
    while time.perf_counter() - t0 < 3.0:
        f = cam.get_latest_frame()
        if f is not None:
            tk_ = cam.latest_frame_ticks
            if tk_ != last:
                distinct += 1; last = tk_
    el4 = time.perf_counter() - t0
    cam.stop(); cam.release()
    R["p4_distinct"] = distinct
    R["p4_secs"] = el4
    R["p4_fps"] = distinct / el4

    st["anim"] = False
    root.quit()

threading.Thread(target=probe, daemon=True).start()
root.mainloop()
try: root.destroy()
except Exception: pass

print("=" * 80)
print("dxcam 决定性基准（窗口 800x600 内有 60Hz 动画）")
print("=" * 80)
print(f"P1 动画中 · 阻塞 grab(region)    : {R['p1_ms']:.3f} ms/帧 -> {R['p1_fps']:.1f} FPS   "
      f"(非空 {R['p1_nonnull']}/300)")
print(f"P2 静止时 · 同一调用              : {R['p2_ms']:.3f} ms/帧 -> {R['p2_fps']:.1f} FPS   "
      f"(非空 {R['p2_nonnull']}/200)")
print(f"P3 生产热路径(抓+4ROI差分+判稳)  : {R['p3_ms']:.3f} ms/帧, CPU {R['p3_cpu_ms']:.3f} ms/帧  "
      f"{R['p3_counts']}")
print(f"P4 流式 target_fps=60 真实新帧    : {R['p4_fps']:.1f} FPS ({R['p4_distinct']} 帧/{R['p4_secs']:.2f}s)")
print()
print("参照: mss 整屏 BitBlt = 17.7 ms (56 FPS)")
print(f"结论: 热路径单帧 CPU {R['p3_cpu_ms']:.3f} ms -> 跑满 60 FPS 仅需 {R['p3_cpu_ms']*60:.1f} ms/s "
      f"= 单核 {R['p3_cpu_ms']*60/10:.1f}%")
