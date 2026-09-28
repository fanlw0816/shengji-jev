"""定案实验：区分『Tk 只画了 12Hz』还是『dxcam 在丢帧』。
用 mss(BitBlt，已知可用) 作为地面真值对照。"""
import ctypes, time, threading, traceback, os, sys
import numpy as np, cv2
import tkinter as tk

ctypes.windll.user32.SetProcessDPIAware()
def log(*a): print(*a, flush=True)

WIN_X, WIN_Y, WIN_W, WIN_H = 300, 300, 800, 600
root = tk.Tk()
root.attributes("-topmost", True)
root.geometry(f"{WIN_W}x{WIN_H}+{WIN_X}+{WIN_Y}")
root.update_idletasks()
canvas = tk.Canvas(root, width=WIN_W, height=WIN_H, bg="#203040", highlightthickness=0)
canvas.pack(fill="both", expand=True)
box = canvas.create_rectangle(0, 0, 200, 200, fill="#ff0000", outline="")
st = {"n": 0, "anim": True, "anim_count": 0}

def animate():
    if not st["anim"]:
        return
    st["n"] += 1
    st["anim_count"] += 1
    x = (st["n"] * 13) % (WIN_W - 200)
    canvas.coords(box, x, 150, x + 200, 350)
    canvas.itemconfig(box, fill=["#ff0000","#00ff00","#0000ff","#ffff00"][st["n"] % 4])
    root.after(8, animate)

root.after(0, animate)

def probe():
    try:
        time.sleep(1.0)
        region = (WIN_X, WIN_Y, WIN_X + WIN_W, WIN_Y + WIN_H)

        # ---- 地面真值 1: Tk 自己画了多少次 ----
        c0 = st["anim_count"]; t0 = time.perf_counter()
        time.sleep(2.0)
        tk_rate = (st["anim_count"] - c0) / (time.perf_counter() - t0)
        log(f"[真值] Tk animate() 实际调用率 : {tk_rate:.1f} Hz   <- 组件真实重绘率")

        # ---- 地面真值 2: mss 观察到的内容变化率 ----
        import mss
        with mss.MSS() as sct:
            mon = {"left": WIN_X, "top": WIN_Y, "width": WIN_W, "height": WIN_H}
            prev = None; changes = 0; samples = 0
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < 2.0:
                f = np.array(sct.grab(mon))[:, :, :3]
                g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                samples += 1
                if prev is not None and float(cv2.absdiff(g, prev).mean()) > 1.0:
                    changes += 1
                prev = g
            el = time.perf_counter() - t0
            log(f"[真值] mss 采样率 {samples/el:.1f} Hz, 其中内容真的变了 {changes} 次 "
                f"-> 屏幕实际变化率 ≈ {changes/el:.1f} Hz")

        # ---- 被测对象: dxcam 各种用法 ----
        import dxcam
        cam = dxcam.create(output_idx=0, output_color="BGR")

        # (a) 带 sleep 的轮询：每 16.7ms 抓一次
        got = 0; tries = 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 2.0:
            tries += 1
            if cam.grab(region=region) is not None:
                got += 1
            time.sleep(0.0167)
        el = time.perf_counter() - t0
        log(f"[dxcam] 16.7ms 轮询: {tries} 次调用, 拿到新帧 {got} 次 -> {got/el:.1f} FPS, "
            f"命中率 {got/max(tries,1)*100:.1f}%")

        # (b) 流式，按内容差异计数（不靠 ticks）
        cam.release()
        cam = dxcam.create(output_idx=0, output_color="BGR")
        cam.start(region=region, target_fps=60, video_mode=True)
        time.sleep(0.5)
        prev = None; changes = 0; polls = 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 2.0:
            f = cam.get_latest_frame()
            polls += 1
            if f is not None:
                g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                if prev is not None and float(cv2.absdiff(g, prev).mean()) > 1.0:
                    changes += 1
                prev = g
        el = time.perf_counter() - t0
        log(f"[dxcam] 流式 target_fps=60: 轮询 {polls} 次, 内容变化 {changes} 次 "
            f"-> 有效 {changes/el:.1f} Hz")
        try: cam.stop()
        except Exception as e: log("  stop 警告:", e)
        try: cam.release()
        except Exception: pass

        log("\n>>> 若『Tk 重绘率』本身就只有 ~12Hz，则 dxcam 没丢帧，是动画窗口慢。")
    except Exception:
        log("[FAIL]"); traceback.print_exc()
    finally:
        st["anim"] = False
        try: root.quit()
        except Exception: pass

threading.Thread(target=probe, daemon=True).start()
root.mainloop()
try: root.destroy()
except Exception: pass
log("[结束]")
sys.stdout.flush()
os._exit(0)
