"""本机（KVM 虚拟机）抓屏性能实测。

背景：README「实测数据」表中的数值原为**另一台机器**（Windows 11 / RTX 5060 Ti /
双 2560x1440 @60Hz）测得。本脚本在当前机器上按**同样的测法**重测一遍。

方法论与原脚本保持一致（这是结论可信的前提）：
  - 无新帧语义 / 阻塞延迟      -> bench_decisive2.py 的 P1、P2
  - 生产热路径 4ROI 差分判稳    -> bench_decisive2.py 的 P3
  - mss 作为地面真值交叉验证    -> bench_truth.py
  - 模板匹配成本               -> bench_capture2.py 第 3 节
  - 牌几何（机器无关，确认输入一致）-> constants.py 的出处

与原脚本的两点差异：
  1. 本机 Python 未装 tkinter，改用 PySide6（项目已有依赖）造动画窗口
  2. 「动画中」的三段（mss 变化率真值 / 热路径 / 流式帧率）都在动画**开着**时测
     —— 否则 grab() 大量返回 None，会低估热路径成本（首版踩过这个坑）

用法：uv run python spike/bench_this_machine.py
输出：终端表格 + spike/_bench_this_machine.json
"""
from __future__ import annotations

import ctypes
import json
import os
import pathlib
import platform
import threading
import time
import traceback

import cv2
import numpy as np

ctypes.windll.user32.SetProcessDPIAware()

WIN_X, WIN_Y, WIN_W, WIN_H = 300, 300, 800, 600
REGION = (WIN_X, WIN_Y, WIN_X + WIN_W, WIN_Y + WIN_H)
HERE = pathlib.Path(__file__).resolve().parent
SHOTS = HERE.parent / "tests" / "fixtures" / "screenshots"

R: dict[str, object] = {}


def log(*a) -> None:
    print(*a, flush=True)


def head(n: str, title: str) -> None:
    log("\n" + "=" * 84)
    log(f"{n}) {title}")
    log("=" * 84)


def bench(label: str, fn, n: int = 200) -> float | None:
    """计时 n 次取均值（ms/次）。与原 bench_capture2.py 的 bench() 同构。"""
    try:
        fn()
        t0 = time.perf_counter()
        for _ in range(n):
            fn()
        dt = (time.perf_counter() - t0) / n * 1000
        log(f"  {label:52s} {dt:9.3f} ms  -> {1000 / dt:8.1f} FPS")
        return dt
    except Exception as e:
        log(f"  {label:52s} FAILED: {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------- 动画窗口
def make_anim_window():
    """PySide6 动画窗口：每 8ms 移动并换色一个方块，制造持续屏幕变化。"""
    os.environ.setdefault("QT_QPA_PLATFORM", "windows")
    from PySide6.QtCore import QRect, Qt, QTimer
    from PySide6.QtGui import QColor, QPainter
    from PySide6.QtWidgets import QApplication, QWidget

    COLORS = ["#ff0000", "#00ff00", "#0000ff", "#ffff00"]

    class AnimWindow(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle("bench_this_machine")
            self.setGeometry(WIN_X, WIN_Y, WIN_W, WIN_H)
            self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            self.state = {"n": 0, "paints": 0, "anim": True}
            self.timer = QTimer(self)
            self.timer.timeout.connect(self._tick)
            self.timer.start(8)

        def _tick(self) -> None:
            if not self.state["anim"]:
                return
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
    win = AnimWindow()
    win.show()
    app.processEvents()
    return app, win


# ---------------------------------------------------------------- 0 环境
def section_env() -> None:
    head("0", "环境")
    u = ctypes.windll.user32
    sw, sh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    nmon = u.GetSystemMetrics(80)
    R["env"] = {"screen": f"{sw}x{sh}", "monitors": nmon,
                "python": platform.python_version(),
                "numpy": np.__version__, "cv2": cv2.__version__,
                "processor": platform.processor(), "machine": platform.machine()}
    log(f"  屏幕 {sw}x{sh} · 显示器 {nmon} 个 · Python {platform.python_version()}")
    log(f"  numpy {np.__version__} · cv2 {cv2.__version__}")
    log(f"  processor={platform.processor()!r}")

    try:
        hdc = u.GetDC(0)
        VREFRESH = 116
        hz = ctypes.windll.gdi32.GetDeviceCaps(hdc, VREFRESH)
        u.ReleaseDC(0, hdc)
        R["env"]["refresh_hz"] = hz
        log(f"  >>> 显示器报告刷新率 VREFRESH = {hz} Hz")
    except Exception as e:
        log("  刷新率探测失败:", e)

    try:
        k = ctypes.windll.kernel32
        feats = {10: "SSE2", 13: "SSE3", 37: "SSE4.2", 39: "AVX", 41: "POPCNT"}
        got = {v: bool(k.IsProcessorFeaturePresent(c)) for c, v in feats.items()}
        R["env"]["cpu_features"] = got
        log("  指令集 " + " ".join(f"{a}={b}" for a, b in got.items()))
    except Exception as e:
        log("  指令集探测失败:", e)


# ---------------------------------------------------------------- 1 mss 成本
def section_mss_cost() -> None:
    head("1", "mss 整屏 BitBlt 成本（降级路径）")
    import mss
    with mss.MSS() as sct:
        mon = sct.monitors[1]
        log(f"  监视器 {mon['width']}x{mon['height']}")
        dt = bench("mss 整屏 grab + np.array",
                   lambda: np.array(sct.grab(mon)), n=60)
        if dt:
            R["mss_full_ms"] = dt
            log(f"  >>> 跑满 60 FPS 需 {dt * 60 / 10:.0f}% 单核（按 10ms = 100% 单核）")
            log(f"      降频到 15 Hz 需 {dt * 15 / 10:.0f}% 单核")


# ---------------------------------------------------------------- 2 静态语义
def section_dxcam_idle() -> None:
    head("2", "dxcam 全屏 grab —— 无变化时的语义（对应 bench_decisive2 P2）")
    import dxcam
    cam = dxcam.create(output_idx=0, output_color="BGR")
    for _ in range(20):
        cam.grab()
    nonnull = 0
    N = 300
    t0 = time.perf_counter()
    for _ in range(N):
        if cam.grab() is not None:
            nonnull += 1
    dt = (time.perf_counter() - t0) / N * 1000
    R["dxcam_full_idle_ms"] = dt
    R["dxcam_full_idle_nonnull"] = nonnull
    log(f"  全屏 grab（静态）: {dt:.3f} ms/次 -> {1000 / dt:.1f} FPS  (非空 {nonnull}/{N})")
    log("  >>> 非空≈0 即『无变化返回 None』语义成立 = 免费的变更侦测")
    cam.release()


# ------------------------------------------------------- 3 动画中的地面真值
def section_mss_truth() -> None:
    """动画开着时，用 mss 测『屏幕内容真的变了多少次』—— 地面真值。"""
    head("3", "地面真值：动画开着时，屏幕内容实际变化率（mss）")
    import mss
    with mss.MSS() as sct:
        mon = {"left": WIN_X, "top": WIN_Y, "width": WIN_W, "height": WIN_H}
        prev = None
        changes = samples = 0
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 3.0:
            f = np.asarray(sct.grab(mon))[:, :, :3]
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            samples += 1
            if prev is not None and float(cv2.absdiff(g, prev).mean()) > 1.0:
                changes += 1
            prev = g
        el = time.perf_counter() - t0
    R["mss_region_sample_hz"] = samples / el
    R["mss_content_change_hz"] = changes / el
    log(f"  mss 区域采样 {samples / el:.1f} Hz, 内容真的变了 {changes} 次"
        f" -> 屏幕变化率 ≈ {changes / el:.1f} Hz")
    log("  >>> 这是『屏幕本身刷新了多少次』的上限，dxcam 的新帧率不会超过它")


# ---------------------------------------------------------------- 4 热路径
def section_hot_path() -> None:
    """动画开着时测 —— 与原 bench_decisive2 P3 的条件一致。"""
    head("4", "生产热路径：grab + 4ROI 差分 + 判稳（对应 bench_decisive2 P3）")
    import dxcam
    cam = dxcam.create(output_idx=0, output_color="BGR")
    ROIS = ((0, 0, 400, 200), (400, 0, 800, 200),
            (0, 200, 400, 400), (400, 200, 800, 400))
    prev: list = [None] * 4
    stable = [0] * 4
    THRESH = 2.0

    def hot() -> int:
        f = cam.grab(region=REGION)
        if f is None:
            return 0
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        for i, (a, b, c, d) in enumerate(ROIS):
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
    N = 300
    t0 = time.perf_counter()
    cpu0 = time.process_time()
    changed = 0
    for _ in range(N):
        changed += hot()
    el = time.perf_counter() - t0
    cpu = time.process_time() - cpu0
    ms, cpu_ms = el / N * 1000, cpu / N * 1000
    R["hot_ms"], R["hot_cpu_ms"], R["hot_changed"] = ms, cpu_ms, changed
    log(f"  {ms:.3f} ms/帧（墙钟）, CPU {cpu_ms:.3f} ms/帧, 有变化 {changed}/{N}")
    log(f"  >>> 跑满 60 FPS 的 CPU 成本 ≈ {cpu_ms * 60:.2f} ms/s"
        f" = 单核 {cpu_ms * 60 / 10:.2f}%")
    cam.release()


# ---------------------------------------------------------------- 5 流式帧率
def section_streaming() -> None:
    head("5", "dxcam 流式新帧率（动画开着 + 与 mss 真值对照，对应 bench_truth）")
    import dxcam
    cam = dxcam.create(output_idx=0, output_color="BGR")
    got = tries = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 2.0:
        tries += 1
        if cam.grab(region=REGION) is not None:
            got += 1
        time.sleep(0.0167)
    el = time.perf_counter() - t0
    R["poll167_tries"], R["poll167_got"] = tries, got
    R["poll167_hit_pct"] = got / max(tries, 1) * 100
    R["poll167_fps"] = got / el
    log(f"  16.7ms 轮询: {tries} 次调用, 拿到新帧 {got} 次"
        f" -> {got / el:.1f} FPS, 命中率 {got / max(tries, 1) * 100:.1f}%")

    cam.release()
    cam = dxcam.create(output_idx=0, output_color="BGR")
    cam.start(region=REGION, target_fps=60, video_mode=True)
    time.sleep(0.5)
    prev = None
    changes = polls = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 3.0:
        f = cam.get_latest_frame()
        polls += 1
        if f is not None:
            g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
            if prev is not None and float(cv2.absdiff(g, prev).mean()) > 1.0:
                changes += 1
            prev = g
    el = time.perf_counter() - t0
    R["stream_polls"], R["stream_changes"] = polls, changes
    R["stream_effective_hz"] = changes / el
    log(f"  流式 target_fps=60: 轮询 {polls} 次, 内容变化 {changes} 次"
        f" -> 有效 {changes / el:.1f} Hz")
    try:
        cam.stop()
    except Exception as e:
        log("  stop 警告:", e)
    try:
        cam.release()
    except Exception:
        pass


def section_animated_probe() -> None:
    section_mss_truth()
    section_hot_path()
    section_streaming()


# ---------------------------------------------------------------- 6 模板匹配
def section_template_match() -> None:
    head("6", "模板匹配成本（对应 bench_capture2 第 3 节）")
    rng = np.random.default_rng(0)
    card = (rng.random((100, 70, 3)) * 255).astype(np.uint8)
    tmpl_corner = (rng.random((30, 22, 3)) * 255).astype(np.uint8)
    dt_corner = bench("matchTemplate corner(30x22) in card(70x100)",
                      lambda: cv2.matchTemplate(card, tmpl_corner,
                                                cv2.TM_CCOEFF_NORMED), n=3000)
    dt_full = bench("matchTemplate full-card in card (identity)",
                    lambda: cv2.matchTemplate(card, card.copy(),
                                              cv2.TM_CCOEFF_NORMED), n=3000)
    if dt_corner:
        R["tmpl_corner_ms"] = dt_corner
    if dt_full:
        R["tmpl_full_ms"] = dt_full

    real_card = (rng.random((79, 56, 3)) * 255).astype(np.uint8)
    real_corner = (rng.random((28, 16, 3)) * 255).astype(np.uint8)
    dt_real = bench("matchTemplate 真实角标(16x28) in 牌面(56x79)",
                    lambda: cv2.matchTemplate(real_card, real_corner,
                                              cv2.TM_CCOEFF_NORMED), n=3000)
    if dt_real:
        R["tmpl_real_corner_ms"] = dt_real
        log(f"  >>> 1 墩 8 张 x 17 类（13 点数 + 4 花色）= {8 * 17 * dt_real:.2f} ms/墩")


# ---------------------------------------------------------------- 7 牌几何
def section_geometry() -> None:
    head("7", "牌几何（来自 fixture 截图，与机器无关 —— 确认输入数据一致）")
    from shengji.imaging import card_sized_blobs, imread_unicode

    singles: list[tuple[int, int]] = []
    stacks: list[tuple[int, int]] = []
    for p in sorted(SHOTS.glob("*.jpg")):
        img = imread_unicode(str(p))
        if img is None:
            continue
        blobs = card_sized_blobs(img)
        log(f"  {p.name:14s} {img.shape[1]}x{img.shape[0]}  牌块 {len(blobs)} 个")
        for b in blobs:
            w, h = b["w"], b["h"]
            log(f"      块 ({b['x']},{b['y']}) {w}x{h} 宽高比={w / h:.3f} "
                f"面积={b['area']}")
            if h < 70:
                continue
            (singles if w < 60 else stacks).append((w, h))

    if singles:
        w = min(s[0] for s in singles)
        h = max(s[1] for s in singles)
        R["card_w_measured"], R["card_h_measured"] = w, h
        R["card_aspect_measured"] = round(w / h, 3)
        log(f"  >>> 单张牌实测 {w}x{h}，宽高比 {w / h:.3f}"
            f"（常量 CARD_W=56 / CARD_H=79 / CARD_ASPECT=0.709）")
    else:
        log("  [!] 未扫到单张牌块（该夹具可能全是叠放）")
    if stacks and singles:
        w2 = min(s[0] for s in stacks)
        R["card_stack_w_measured"] = w2
        log(f"  >>> 两张叠放宽实测 {w2}px -> 叠放偏移 {w2 - min(s[0] for s in singles)}px"
            f"（常量 CARD_STACK_OFFSET=16）")
    elif stacks:
        w2 = min(s[0] for s in stacks)
        log(f"  >>> 两张叠放宽实测 {w2}px（无单张可对照，无法反推偏移）")


# ---------------------------------------------------------------- main
def main() -> None:
    log("本机抓屏性能实测 —— spike/bench_this_machine.py")
    section_env()
    section_mss_cost()
    section_dxcam_idle()

    try:
        app, win = make_anim_window()
    except Exception:
        log("[FAIL] 动画窗口创建失败（PySide6 不可用），跳过动画相关三段:")
        traceback.print_exc()
        app = win = None

    if app is not None and win is not None:
        win.state["anim"] = True
        for _ in range(80):
            app.processEvents()
            time.sleep(0.01)
        # Qt 自己的重绘率 —— 动画源是否够快
        t0 = time.perf_counter()
        c0 = win.state["paints"]
        while time.perf_counter() - t0 < 2.0:
            app.processEvents()
            time.sleep(0.002)
        qt_hz = (win.state["paints"] - c0) / (time.perf_counter() - t0)
        R["qt_repaint_hz"] = qt_hz
        log("\n" + "=" * 84)
        log("3a) 动画源：Qt paintEvent 实际调用率")
        log("=" * 84)
        log(f"  [真值] Qt 重绘 {qt_hz:.1f} Hz  <- 动画源本身够不够快")
        log("  >>> 若它远高于下面 dxcam 的新帧率，说明瓶颈不在动画源")

        th = threading.Thread(target=section_animated_probe, daemon=True)
        th.start()
        while th.is_alive():
            app.processEvents()
            time.sleep(0.002)
        win.state["anim"] = False
        app.quit()

    section_template_match()
    section_geometry()

    out = HERE / "_bench_this_machine.json"
    out.write_text(json.dumps(R, ensure_ascii=False, indent=2), encoding="utf-8")
    log("\n" + "=" * 84)
    log(f"[ok] 结果已写入 {out}")
    log("=" * 84)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
