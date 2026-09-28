"""布局校验：几何不变量 + 牌面块一致性。

失败即告警，绝不静默继续。

设计变更（实测驱动）：初稿用「区必须落在桌面色上」校验，
但实测发现牌桌周围的背景与桌面同色（阈值 90 时空区覆盖 100%，全图覆盖 74%），
该判据没有区分力。改用「牌尺寸块」这一强特征：
牌是 56x79、宽高比 0.709、面积 3700+ 的白色块，随机出现的概率极低。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot

import cv2
import numpy as np

from .. import constants as C
from .model import LayoutModel


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str

    def __repr__(self) -> str:  # pragma: no cover - 便于日志阅读
        return f"Violation({self.code}: {self.detail})"


@dataclass
class ValidationReport:
    violations: list[Violation] = field(default_factory=list)
    verifiable: bool = True   # False = 当前帧信息不足（如空桌），不等于「通过」

    @property
    def ok(self) -> bool:
        return not self.violations


def validate_geometry(m: LayoutModel, card_width: float | None = None) -> ValidationReport:
    """与画面无关的几何校验。任何时候都能跑。"""
    rep = ValidationReport()

    if abs(m.zone_h - C.CARD_H) > 10:
        rep.violations.append(Violation(
            "zone_size_mismatch",
            f"区高 {m.zone_h} 与牌高 {C.CARD_H} 相差超过 10px"))
    if m.zone_w < C.CARD_W:
        rep.violations.append(Violation(
            "zone_too_narrow",
            f"区宽 {m.zone_w} 小于单张牌宽 {C.CARD_W}，无法容纳任何出牌"))

    if abs(m.arm_y_top - m.arm_y_bottom) > C.TOL_ARM_SYMMETRY_Y:
        rep.violations.append(Violation(
            "arm_asymmetry_y",
            f"上下臂不对称 {abs(m.arm_y_top - m.arm_y_bottom):.1f}px "
            f"(限 {C.TOL_ARM_SYMMETRY_Y}px)"))

    # 四区必须互相分得开。
    #
    # 说明：本模型用「单一 arm_x」推导左右区，因此左右臂**结构上恒对称**，
    # 检查「左右臂是否对称」是永远为真的空检查（初稿曾有 arm_asymmetry_x，
    # 实测无法构造出违反它的输入）。真正会出错的是四区重叠或挤在一起，
    # 那才是需要拦的。
    zones = m.zones
    names = list(zones)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            ax, ay = zones[names[i]].center
            bx, by = zones[names[j]].center
            d = hypot(ax - bx, ay - by)
            if d < m.zone_w:
                rep.violations.append(Violation(
                    "zones_too_close",
                    f"{names[i]} 与 {names[j]} 中心仅相距 {d:.1f}px "
                    f"(需 ≥ 区宽 {m.zone_w}px)，四区无法区分"))

    if card_width is not None and abs(card_width - C.CARD_W) > C.TOL_CARD_W:
        rep.violations.append(Violation(
            "card_width_drift",
            f"牌宽 {card_width:.1f} 偏离基准 {C.CARD_W}±{C.TOL_CARD_W}，"
            f"可能是 DPI 缩放被改或客户端改版，需重新标定"))

    return rep


def find_card_sized_blobs(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    """全图扫出「牌尺寸」的白色块，返回 (x, y, w, h) 列表。

    面积下限是关键：实测 1 张牌块约 3736px，而右侧静态 UI 元素
    (1042,282) 68x74 面积仅 1836 且宽高比同为 0.92 —— 只有面积能区分。
    """
    from ..imaging import card_sized_blobs

    return [(b["x"], b["y"], b["w"], b["h"]) for b in card_sized_blobs(frame)]


def score_hypothesis(m: LayoutModel, blobs: list[tuple[int, int, int, int]],
                     frame_shape: tuple[int, int]) -> tuple[int, int]:
    """返回 (区内块数, 区外块数)。

    区内判定用「块中心落在该区中心 max_dist 内」，与 detect.nearest_zone 一致，
    避免区 bbox 外扩后相邻区互相覆盖。
    """
    centers = [rect.center for rect in m.zones.values()]
    inside = outside = 0
    for bx, by, bw, bh in blobs:
        bcx, bcy = bx + bw / 2.0, by + bh / 2.0
        d = min(hypot(bcx - zx, bcy - zy) for zx, zy in centers)
        if d <= C.ZONE_ASSIGN_MAX_DIST:
            inside += 1
        else:
            outside += 1
    return inside, outside


def validate_against_frame(m: LayoutModel, frame: np.ndarray) -> ValidationReport:
    """用牌面块校验布局。

    判据（经实测标定）：**区内块数必须多于区外块数**。
    不使用「区外必须为 0」——因为画面中存在静态 UI 元素可能与牌块同形，
    用多数判据可容忍少量杂散块，同时仍能否决明显错位的假设。

    画面中一个牌尺寸块都没有 -> verifiable=False（无法校验，不等于通过）。
    """
    rep = ValidationReport()
    blobs = find_card_sized_blobs(frame)
    if not blobs:
        rep.verifiable = False
        return rep

    inside, outside = score_hypothesis(m, blobs, frame.shape[:2])
    if inside <= outside:
        rep.violations.append(Violation(
            "cards_outside_zones",
            f"预测区内只有 {inside} 个牌块，区外却有 {outside} 个，"
            f"布局与实际牌位不符"))
    return rep
