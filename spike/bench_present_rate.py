"""定案实验 v2：本机屏幕的**实际呈现率**是多少？dxcam 到底有没有丢帧？

v1 的两个测法缺陷（保留记录，避免重犯）：
  1. 用 ctypes+GDI BitBlt 当"高采样率地面真值"，但 GDI 抓 800x200 要 59ms ——
     与 mss 整屏同量级，说明它**同样被截断在 ~17 Hz**，起不到真值作用
  2. dxcam 那段跑在主线程却没 pump Qt 事件 -> 动画被冻住 -> 测出 0 Hz

v2 改为：
  A. 量 GDI / dxcam 的抓取成本**随区域大小**怎么变 —— 用来判断那 ~59ms
     是"固定停顿"（= 与显示刷新同步，可作为呈现率的间接证据）还是"跟像素成正比"
  B. 间隔扫描时，dxcam 在**工作线程**里测，主线程持续 pump Qt 事件（与
     bench_this_machine.py 同构）

用法：uv run python spike/bench_present_rate.py
"""
from __future__ import annotations

import ctypes
import json
import os
import pathlib
import threading
import time
import traceback
from ctypes import wintypes

import cv2
import numpy as np

ctypes.windll.user32.SetProcessDPIAware()
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

WIN_X, WIN_Y, WIN_W, WIN_H = 300, 300, 800, 600
BOX = (WIN_X, WIN_Y + 150, WIN_W, 200)      # 会动的横条
HERE = pathlib.Path(__file__).resolve().parent


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


def make_grabber(w: int, h: int):
    hdc = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(memdc, bmp)
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)

    def grab(x: int, y: int) -> np.ndarray:
        gdi32.BitBlt(memdc, 0, 0, w, h, hdc, x, y, SRCCOPY)
        gdi32.GetDIBits(memdc, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
        return np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)[:, :, :3]

    return grab


def log(*a) -> None:
    print(*a, flush=True)


def make_window():
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PySide6.QtCore import QRect, Qt, QTimer
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication, QWidget

    COLORS = ["#ff0000", "#00ff00", "#0000ff", "#ffff00"]

    class W(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("bench_present_rate")
            self.setGeometry(WIN_X, WIN_Y, WIN_W, WIN_H)
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.state = {"n": 0, "paints": 0, "anim": True}
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(8)

        def _tick(self) -> None:
            if self.state["anim"]:
                self.state["n"] += 1
                self.update()

        def paintEvent(self, _e) -> None:  # noqa: N802
            self.state["paints"] += 1
            n = self.state["n"]
            p = QPainter(self)
            p.fillRect(self.rect(), QColor("#203040"))
            x = (n * 13) % (WIN_W - 200)
            p.fillRect(QRect(x, 150, 200, 200), QColor(COLORS[n % 4]))

    app = QApplication.instance() or QApplication([])
    win = W()
    win.show()
    app.processEvents()
    return app, win


def pump(app, seconds: float) -> None:
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        app.processEvents()
        time.sleep(0.001)


def cost(label: str, fn, n: int = 40) -> float:
    for _ in range(3):
        fn()
    t0 = time.perf_counter()
    for _ in range(n):
        fn()
    dt = (time.perf_counter() - t0) / n * 1000
    log(f"  {label:34s} {dt:8.2f} ms  -> {1000 / dt:7.1f} Hz")
    return dt


def main() -> None:
    out: dict = {}
    log("屏幕实际呈现率定案 v2 —— spike/bench_present_rate.py")
    app, win = make_window()
    win.state["anim"] = False              # A 段先静止，排除"内容在变"的影响
    pump(app, 0.8)

    # ---------------- A. 抓取成本 vs 区域大小 ----------------
    log("\n" + "=" * 80)
    log("A) 抓取成本 vs 区域大小（屏幕静止）—— 判断那 ~59ms 是固定停顿还是像素相关")
    log("=" * 80)
    import dxcam
    cam = dxcam.create(output_idx=0, output_color="BGR")
    for _ in range(10):
        cam.grab()
    sizes = [(16, 16), (64, 64), (200, 200), (400, 300), (800, 200), (1916, 998)]
    gdi_rows = []
    dx_rows = []
    for w, h in sizes:
        g = make_grabber(w, h)
        px = w * h
        t_g = cost(f"GDI  BitBlt {w}x{h} ({px / 1000:.0f}k px)", lambda g=g: g(0, 0), n=30)
        cam.release()
        cam = dxcam.create(output_idx=0, output_color="BGR")
        r = (0, 0, w, h)
        t_d = cost(f"dxcam grab   {w}x{h} ({px / 1000:.0f}k px)",
                   lambda c=cam, r=r: c.grab(region=r), n=30)
        gdi_rows.append({"w": w, "h": h, "px": px, "ms": t_g})
        dx_rows.append({"w": w, "h": h, "px": px, "ms": t_d})
    out["gdi_vs_size"] = gdi_rows
    out["dxcam_vs_size"] = dx_rows
    cam.release()

    smallest = gdi_rows[0]["ms"]
    largest = gdi_rows[-1]["ms"]
    log(f"\n  GDI: 16x16 = {smallest:.2f} ms，1916x998 = {largest:.2f} ms"
        f"（像素数差 {gdi_rows[-1]['px'] / gdi_rows[0]['px']:.0f} 倍）")
    if largest < smallest * 2:
        log("  >>> GDI 成本**几乎与像素数无关** -> 是**每调用一次的固定停顿**")
        log(f"      固定停顿 {smallest:.1f} ms ≈ {1000 / smallest:.1f} Hz"
            " —— 与显示刷新同步的特征")
        out["gdi_fixed_stall_ms"] = smallest
    else:
        log("  >>> GDI 成本随像素数增长 -> 是像素搬运受限，与刷新无关")
    d_small = dx_rows[0]["ms"]
    d_large = dx_rows[-1]["ms"]
    log(f"  dxcam: 16x16 = {d_small:.3f} ms，1916x998 = {d_large:.3f} ms")
    if d_large < 5:
        log("  >>> dxcam 全屏抓取 < 5ms -> **dxcam 路径本身不受那 60ms 拖累**"
            "（主路径没问题，慢的是 mss/GDI 的 BitBlt 路径）")

    # ---------------- B. 间隔扫描（dxcam 在工作线程） ----------------
    log("\n" + "=" * 80)
    log("B) 间隔扫描：动画以已知速率变化，dxcam 在工作线程观察（主线程持续 pump）")
    log("=" * 80)
    log(f"  {'请求间隔':>9s} {'请求速率':>9s} {'dxcam 实测':>12s} {'变化次数':>9s}  判读")
    rows = []
    win.state["anim"] = True

    def measure_dxcam(region, seconds: float, res: dict) -> None:
        c = dxcam.create(output_idx=0, output_color="BGR")
        c.start(region=region, target_fps=60, video_mode=True)
        time.sleep(0.4)
        prev = None
        changes = 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < seconds:
            f = c.get_latest_frame()
            if f is not None:
                g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
                if prev is not None and float(cv2.absdiff(g, prev).mean()) > 1.0:
                    changes += 1
                prev = g
        el = time.perf_counter() - t0
        res["hz"] = changes / el
        res["changes"] = changes
        try:
            c.stop()
        except Exception:
            pass
        try:
            c.release()
        except Exception:
            pass

    region = (BOX[0], BOX[1], BOX[0] + BOX[2], BOX[1] + BOX[3])
    for ms in (200, 100, 50, 33, 16, 8):
        win.timer.setInterval(ms)
        win.state["n"] = 0
        pump(app, 0.7)
        res: dict = {}
        th = threading.Thread(target=measure_dxcam, args=(region, 2.0, res), daemon=True)
        th.start()
        while th.is_alive():
            app.processEvents()
            time.sleep(0.002)
        want = 1000 / ms
        hz = res.get("hz", 0.0)
        if hz > want * 0.8:
            verdict = "dxcam 跟得上"
        elif hz > 0:
            verdict = "dxcam 在此速率饱和"
        else:
            verdict = "dxcam 无变化（异常）"
        rows.append({"interval_ms": ms, "want_hz": want, "dxcam_hz": hz,
                     "changes": res.get("changes", 0), "verdict": verdict})
        log(f"  {ms:>7d}ms {want:>8.1f}Hz {hz:>11.1f}Hz {res.get('changes', 0):>9d}  {verdict}")
        pump(app, 0.3)

    out["sweep"] = rows
    win.state["anim"] = False
    app.quit()

    # ---------------- C. 结论 ----------------
    log("\n" + "=" * 80)
    log("C) 结论")
    log("=" * 80)
    sat = max((r["dxcam_hz"] for r in rows), default=0.0)
    resolved = [r for r in rows if r["dxcam_hz"] > r["want_hz"] * 0.8]
    log(f"  dxcam 能跟上的最高请求速率: "
        f"{max((r['want_hz'] for r in resolved), default=0.0):.0f} Hz")
    log(f"  dxcam 观察到的饱和速率    : {sat:.1f} Hz")
    log(f"  GDI/mss 固定停顿对应的速率: {1000 / out.get('gdi_fixed_stall_ms', float('nan')):.1f} Hz")
    out["dxcam_saturate_hz"] = sat
    p = HERE / "_bench_present_rate.json"
    p.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"\n[ok] 结果已写入 {p}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
