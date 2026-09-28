"""决定性基准 v2：边跑边打印 + 异常兜底。结论决定截图频率设计。"""
import ctypes, time, threading, traceback, json, sys
import numpy as np, cv2
import tkinter as tk

ctypes.windll.user32.SetProcessDPIAware()
def log(*a):
    print(*a, flush=True)

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

def probe():
    R = {}
    try:
        time.sleep(1.0)
        import dxcam
        region = (WIN_X, WIN_Y, WIN_X + WIN_W, WIN_Y + WIN_H)
        cam = dxcam.create(output_idx=0, output_color="BGR")
        log(f"[setup] cam={type(cam).__name__} region={region}")
        for _ in range(20):
            cam.grab(region=region)

        log("\n--- P1 动画中：阻塞 grab 的真实延迟 ---")
        t0 = time.perf_counter(); nonnull = 0; N = 300
        for _ in range(N):
            if cam.grab(region=region) is not None:
                nonnull += 1
        el = time.perf_counter() - t0
        R["p1_ms"] = el / N * 1000; R["p1_fps"] = N / el; R["p1_nonnull"] = nonnull
        log(f"  {R['p1_ms']:.3f} ms/帧 -> {R['p1_fps']:.1f} FPS  (非空 {nonnull}/{N})")

        log("\n--- P2 停止动画：验证『无变化 -> None』语义 ---")
        st["anim"] = False
        time.sleep(0.8)
        for _ in range(5):
            cam.grab(region=region)
        nonnull2 = 0; N2 = 200
        t0 = time.perf_counter()
        for _ in range(N2):
            if cam.grab(region=region) is not None:
                nonnull2 += 1
        el2 = time.perf_counter() - t0
        R["p2_ms"] = el2 / N2 * 1000; R["p2_fps"] = N2 / el2; R["p2_nonnull"] = nonnull2
        log(f"  {R['p2_ms']:.3f} ms/帧 -> {R['p2_fps']:.1f} FPS  (非空 {nonnull2}/{N2})")
        log(f"  >>> 判读: 非空≈0 说明『无变化时返回 None』成立，可用作零成本变更侦测")

        st["anim"] = True
        time.sleep(0.8)

        log("\n--- P3 生产热路径：抓屏 + 4ROI差分 + 稳定判定 ---")
        ROIS = [(0,0,400,200),(400,0,800,200),(0,200,400,400),(400,200,800,400)]
        prev = [None]*4; stable = [0]*4; THRESH = 2.0
        def hot():
            f = cam.grab(region=region)
            if f is None:
                return 0
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            for i,(a,b,c,d) in enumerate(ROIS):
                cur = g[b:d, a:c]
                if prev[i] is not None:
                    if float(cv2.absdiff(cur, prev[i]).mean()) < THRESH:
                        stable[i] += 1
                    else:
                        stable[i] = 0
                prev[i] = cur
            return 1
        for _ in range(20):
            hot()
        t0 = time.perf_counter(); cpu0 = time.process_time(); changed = 0; N3 = 300
        for _ in range(N3):
            changed += hot()
        el3 = time.perf_counter() - t0; cpu3 = time.process_time() - cpu0
        R["p3_ms"] = el3/N3*1000; R["p3_cpu_ms"] = cpu3/N3*1000; R["p3_changed"] = changed
        log(f"  {R['p3_ms']:.3f} ms/帧 (墙钟), CPU {R['p3_cpu_ms']:.3f} ms/帧, 有变化 {changed}/{N3}")
        log(f"  >>> 跑满 60 FPS 的 CPU 成本 ≈ {R['p3_cpu_ms']*60:.2f} ms/s = 单核 {R['p3_cpu_ms']*60/10:.2f}%")

        log("\n--- P4 流式 start(target_fps=60) 真实新帧率 ---")
        cam.release()
        cam2 = dxcam.create(output_idx=0, output_color="BGR")
        cam2.start(region=region, target_fps=60, video_mode=True)
        time.sleep(0.4)
        t0 = time.perf_counter(); last = None; distinct = 0
        while time.perf_counter() - t0 < 3.0:
            f = cam2.get_latest_frame()
            if f is not None:
                tk_ = cam2.latest_frame_ticks
                if tk_ != last:
                    distinct += 1; last = tk_
        el4 = time.perf_counter() - t0
        try:
            cam2.stop()
        except Exception as e:
            log("  stop() 警告:", e)
        try:
            cam2.release()
        except Exception:
            pass
        R["p4_fps"] = distinct/el4; R["p4_distinct"] = distinct
        log(f"  {R['p4_fps']:.1f} FPS ({distinct} 帧 / {el4:.2f}s)")

        with open("_bench_result.json", "w", encoding="utf-8") as fp:
            json.dump(R, fp, ensure_ascii=False, indent=2)
        log("\n[ok] 结果已写入 _bench_result.json")
    except Exception:
        log("\n[FAIL] 探针异常:")
        traceback.print_exc()
    finally:
        st["anim"] = False
        try:
            root.quit()
        except Exception:
            pass

threading.Thread(target=probe, daemon=True).start()
root.mainloop()
try: root.destroy()
except Exception: pass
log("[mainloop 已退出]")
