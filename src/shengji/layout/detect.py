"""出牌区占用检测与锚定选择（spec §11A 的假设）。

关键设计：**不做「区域内包含」判定，而是把每个牌块就近分配给最近的区中心**。
原因（实测驱动）：左右区中心只隔 108px，先前用「区 bbox 外扩 120px 再判包含」
的写法会让相邻区的检索范围重叠，导致「下家出两张」被误判成上区也有牌；
同时外扩后白色占比从 0.86 稀释到 0.09，会让所有区都被判为空。
就近分配同时天然容纳甩牌导致的中心漂移。
"""

from __future__ import annotations

from math import hypot

import numpy as np

from .. import constants as C
from ..imaging import card_sized_blobs
from .model import AnchorMode, LayoutModel
from .validate import ValidationReport, validate_against_frame, validate_geometry


def estimate_card_count(blob_w: int, card_w: float = C.CARD_W) -> int:
    """由外接框宽度反推牌张数。

    宽度模型：w = card_w + (n-1) * CARD_STACK_OFFSET
    实测校验：56 -> 1 张，72 -> 2 张。
    """
    if blob_w < card_w * 0.6:
        return 1
    n = 1 + round((blob_w - card_w) / C.CARD_STACK_OFFSET)
    return max(1, int(n))


def nearest_zone(model: LayoutModel, cx: float, cy: float) -> tuple[str | None, float]:
    """返回距 (cx, cy) 最近的区名与距离。"""
    best: str | None = None
    best_d = float("inf")
    for name, rect in model.zones.items():
        zcx, zcy = rect.center
        d = hypot(cx - zcx, cy - zcy)
        if d < best_d:
            best, best_d = name, d
    return best, best_d


def detect_occupied_zones(frame: np.ndarray, model: LayoutModel,
                          max_dist: float | None = None) -> dict[str, dict]:
    """返回 {区名: 占用信息}，只含实际有牌的区。

    每个符合条件的牌块分配给最近的区中心；距离超过 max_dist 的块视为
    「区外」而丢弃（例如右侧那个与牌块同形的静态 UI 元素）。
    同一个区若分到多个块，保留面积最大的那个，并用 n_blobs 暴露分裂情况
    （勒牌被花色符号切成多块时会发生，调用方可据此降级处理）。
    """
    md = C.ZONE_ASSIGN_MAX_DIST if max_dist is None else max_dist
    out: dict[str, dict] = {}
    for b in card_sized_blobs(frame):
        bcx = b["x"] + b["w"] / 2.0
        bcy = b["y"] + b["h"] / 2.0
        name, d = nearest_zone(model, bcx, bcy)
        if name is None or d > md:
            continue
        prev = out.get(name)
        if prev is None:
            out[name] = {
                "blob": b,
                "width": b["w"],
                "height": b["h"],
                "count": estimate_card_count(b["w"]),
                "dist": d,
                "n_blobs": 1,
            }
        else:
            prev["n_blobs"] += 1
            if b["area"] > prev["blob"]["area"]:
                prev.update({
                    "blob": b,
                    "width": b["w"],
                    "height": b["h"],
                    "count": estimate_card_count(b["w"]),
                    "dist": d,
                })
    return out


def select_anchor(frame: np.ndarray, client: tuple[int, int, int, int],
                  base: LayoutModel) -> tuple[LayoutModel, ValidationReport, AnchorMode]:
    """按 H1 -> H2 顺序选择锚定方式，返回第一个通过校验的。

    client = (x, y, w, h) 为窗口客户区在屏幕上的矩形。

    设计要点（实测驱动）：
    - H3「桌面颜色锚点」已**作废** —— 实测牌桌与背景同色，颜色无法定位牌桌
    - 空桌时无法校验（画面里没有牌尺寸块），此时**默认 H1 并标记未验证**，
      由调用方在首墩出牌后复核；这比胡乱切换假设更安全
    - 全部失败时返回校验未通过的结果与报告，交由调用方提示重新标定
    """
    cl_x, cl_y, cl_w, cl_h = client
    last_report: ValidationReport = ValidationReport()
    last_model = base

    for mode in (AnchorMode.TOP_LEFT, AnchorMode.CENTER):
        cand = base.with_anchor(mode).for_client(cl_x, cl_y, cl_w, cl_h)
        greport = validate_geometry(cand)
        if not greport.ok:
            last_report, last_model = greport, cand
            continue
        freport = validate_against_frame(cand, frame)
        if freport.ok:
            # 空桌时 freport.verifiable=False —— 仍然采用，但把「未验证」传递出去
            freport.violations.extend(greport.violations)
            return cand, freport, mode
        last_report, last_model = freport, cand

    return last_model, last_report, AnchorMode.TOP_LEFT
