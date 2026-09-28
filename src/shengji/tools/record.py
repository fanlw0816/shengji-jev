"""采样录制工具：自动把出牌区的牌面切片存盘，供剪模板与调阈值。

触发逻辑：只在「某区占用状态发生变化」且画面判稳时存盘，
因此不需要人工抓那几秒的显示窗口。

用法：
    uv run python -m shengji.tools.record --out samples/ --seconds 600
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..capture.factory import build_backend, degraded_poll_interval
from ..imaging import imwrite_unicode
from ..layout.detect import detect_occupied_zones, select_anchor
from ..layout.model import AnchorMode, LayoutModel
from ..window.win32 import enable_per_monitor_dpi_awareness, find_game_window

# 判稳参数（spec §4.3）：连续若干帧差异低于阈值才算「牌已摆定」
SETTLE_FRAMES = 8
SETTLE_DIFF_THRESHOLD = 2.0


def should_save(prev: tuple[str, ...], now: tuple[str, ...]) -> bool:
    """占用集合发生变化才存盘（去重，避免同一墩重复采样）。"""
    return tuple(sorted(prev)) != tuple(sorted(now))


def frame_diff_score(a: np.ndarray, b: np.ndarray) -> float:
    """两帧的平均绝对灰度差，用于判稳。"""
    import cv2

    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    if ga.shape != gb.shape:
        return 1e9
    return float(cv2.absdiff(ga, gb).mean())


@dataclass
class SampleWriter:
    """把牌面切片与元数据写入磁盘。"""

    root: Path
    deal_id: str
    trick_index: int
    seat: int

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._manifest = self.root / "manifest.jsonl"

    def write_zone(self, zone: str, img: np.ndarray, count: int, ts: float) -> Path:
        name = (f"deal{self.deal_id}_trick{self.trick_index:03d}"
                f"_seat{self.seat}_{zone}_{count}.png")
        p = self.root / name
        imwrite_unicode(p, img)
        rec = {
            "path": name,
            "deal_id": self.deal_id,
            "trick_index": self.trick_index,
            "seat": self.seat,
            "zone": zone,
            "count": count,
            "ts": ts,
        }
        with self._manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return p


def run(out_dir: Path, seconds: float, output_idx: int, max_bursts: int) -> int:
    enable_per_monitor_dpi_awareness()  # 已设置过则返回 False，属正常
    win = find_game_window()
    if win is None:
        print("未找到游戏窗口。请先启动 QQ 游戏并进入升级牌局。", file=sys.stderr)
        print("可用以下命令列出当前所有窗口标题，以确定候选关键字：", file=sys.stderr)
        print('  uv run python -c "from shengji.window.win32 import '
              "find_windows_by_title as f; [print(r.title, r.size) for r in f('')]\"",
              file=sys.stderr)
        return 2

    backend, mode = build_backend(output_idx=output_idx)
    interval = degraded_poll_interval(mode)
    print(f"窗口: {win.title} 客户区={win.origin} {win.size}")
    print(f"后端: {mode.value}  轮询间隔: {interval * 1000:.1f}ms")

    base = LayoutModel.from_reference()

    # 先抓一帧用于锚定选择；抓不到就退回 H1（左上角锚定）
    first = backend.grab(None)
    if first is not None:
        model, rep, mode_anchor = select_anchor(
            first.image, (win.x, win.y, win.w, win.h), base)
        if not rep.verifiable:
            print(f"提示：当前牌桌为空，锚定方式暂按 {mode_anchor.value} 假定，"
                  f"将在首墩出牌后自动复核。")
        if not rep.ok:
            for v in rep.violations:
                print(f"  ! {v.code}: {v.detail}", file=sys.stderr)
    else:
        model = base.with_anchor(AnchorMode.TOP_LEFT).for_client(
            win.x, win.y, win.w, win.h)
        print("提示：首帧抓取失败，暂按 H1（左上角锚定）运行。")

    prev_occ: tuple[str, ...] = ()
    stable = 0
    prev_gray = None
    saved = 0
    trick = 0
    t_end = time.perf_counter() + seconds

    while time.perf_counter() < t_end:
        f = backend.grab(None)
        if f is None:
            time.sleep(interval)
            continue

        full = f.image
        # 全局判稳：整幅画面稳定若干帧才认为动画结束
        if prev_gray is not None and prev_gray.shape == full.shape:
            if frame_diff_score(full, prev_gray) < SETTLE_DIFF_THRESHOLD:
                stable += 1
            else:
                stable = 0
        prev_gray = full

        if stable < SETTLE_FRAMES:
            time.sleep(interval)
            continue

        occ = detect_occupied_zones(full, model)
        now = tuple(sorted(occ))
        if should_save(prev_occ, now):
            if now == ():
                trick += 1
            elif saved < max_bursts:
                hgt, wid = full.shape[:2]
                writer = SampleWriter(out_dir, deal_id="live",
                                      trick_index=trick, seat=-1)
                for zone, info in occ.items():
                    r = info["blob"]
                    y0 = max(0, r["y"] - 5)
                    y1 = min(hgt, r["y"] + r["h"] + 5)
                    x0 = max(0, r["x"] - 5)
                    x1 = min(wid, r["x"] + r["w"] + 5)
                    crop = full[y0:y1, x0:x1]
                    writer.write_zone(zone, crop, info["count"], f.ts)
                    saved += 1
                print(f"  墩{trick} 占用={list(now)} -> 已存 {len(occ)} 张切片")
            prev_occ = now

        time.sleep(interval)

    backend.close()
    print(f"完成：共存 {saved} 张切片 -> {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="采样录制（自动切片存盘）")
    ap.add_argument("--out", default="samples", help="输出目录")
    ap.add_argument("--seconds", type=float, default=600.0, help="录制时长（秒）")
    ap.add_argument("--output-idx", type=int, default=0, help="显示器序号")
    ap.add_argument("--max-bursts", type=int, default=400, help="切片数量上限")
    args = ap.parse_args(argv)
    return run(Path(args.out), args.seconds, args.output_idx, args.max_bursts)


if __name__ == "__main__":
    raise SystemExit(main())
