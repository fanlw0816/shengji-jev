"""把检测到的布局画成标注图，供人工核对（自动检测 + 用户确认流程的产出）。

用法：
    uv run python -m shengji.tools.dump_layout --all-fixtures
    uv run python -m shengji.tools.dump_layout png/手牌+所有人出两张.jpg out.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from ..imaging import imread_unicode, imwrite_unicode
from ..layout.detect import detect_occupied_zones, select_anchor
from ..layout.model import LayoutModel

COLORS = {
    "top": (0, 255, 255),
    "left": (255, 128, 0),
    "right": (255, 0, 255),
    "bottom": (0, 255, 0),
}
# 与 detect_occupied_zones 的归属一致：上/左/右/下 的语义
SEAT_LABEL = {"top": "top(op)", "left": "left", "right": "right(next)", "bottom": "bottom(self)"}


def annotate(frame: np.ndarray, model: LayoutModel) -> tuple[np.ndarray, dict]:
    vis = frame.copy()
    occ = detect_occupied_zones(frame, model)
    for name, rect in model.zones.items():
        info = occ.get(name)
        color = COLORS[name]
        thick = 3 if info else 1
        cv2.rectangle(vis, (rect.x0, rect.y0), (rect.x1, rect.y1), color, thick)
        label = SEAT_LABEL[name] if info is None else f"{SEAT_LABEL[name]} x{info['count']}"
        cv2.putText(vis, label, (max(0, rect.x0 - 40), max(14, rect.y0 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2, cv2.LINE_AA)
        if info is not None:
            b = info["blob"]
            cv2.rectangle(vis, (b["x"], b["y"]),
                          (b["x"] + b["w"], b["y"] + b["h"]), (255, 255, 255), 1)
    return vis, occ


def report(path_label: str, model, rep, mode, occ) -> None:
    print(f"{path_label}: 锚定={mode.value} "
          f"占用={ {k: v['count'] for k, v in sorted(occ.items())} } "
          f"校验={'通过' if rep.ok else '失败'}"
          f"{'' if rep.verifiable else ' (空桌，未验证)'}")
    for v in rep.violations:
        print(f"   ! {v.code}: {v.detail}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出布局标注图")
    ap.add_argument("image", nargs="?", help="输入截图路径")
    ap.add_argument("out", nargs="?", help="输出图片路径")
    ap.add_argument("--all-fixtures", action="store_true",
                    help="对 tests/fixtures/screenshots 下全部夹具出图")
    ap.add_argument("--outdir", default="spike/shot_analysis")
    args = ap.parse_args(argv)

    base = LayoutModel.from_reference()
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.all_fixtures:
        fx = Path("tests/fixtures/screenshots")
        files = sorted(fx.glob("*.jpg"))
        if not files:
            print(f"未找到夹具: {fx}", file=sys.stderr)
            return 1
        for p in files:
            img = imread_unicode(p)
            if img is None:
                print(f"无法读取 {p}", file=sys.stderr)
                continue
            h, w = img.shape[:2]
            model, rep, mode = select_anchor(img, (0, 0, w, h), base)
            vis, occ = annotate(img, model)
            dst = outdir / f"layout_{p.stem}.png"
            imwrite_unicode(dst, vis)
            report(p.name, model, rep, mode, occ)
            print(f"   -> {dst}")
        return 0

    if not args.image or not args.out:
        ap.print_help()
        return 1
    img = imread_unicode(args.image)
    if img is None:
        print(f"无法读取 {args.image}", file=sys.stderr)
        return 1
    h, w = img.shape[:2]
    model, rep, mode = select_anchor(img, (0, 0, w, h), base)
    vis, occ = annotate(img, model)
    imwrite_unicode(args.out, vis)
    report(Path(args.image).name, model, rep, mode, occ)
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
