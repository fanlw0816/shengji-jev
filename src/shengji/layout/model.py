"""布局模型：锚点模式 + 固定像素偏移 -> 实际四区矩形。

关键约束（用户实测确认）：窗口尺寸不影响牌的长宽，
因此布局是固定像素的，偏移量是常量，不做等比缩放。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .. import constants as C
from ..geometry import Rect, layout_from_center


class AnchorMode(str, Enum):
    """牌桌相对客户区的锚定方式（spec §11A 的假设）。

    注意：原 H3「由桌面颜色锚点定位」已作废 —— 实测牌桌与背景同色，
    颜色无法定位牌桌（见 constants.py 中 FELT 相关注释）。
    """

    TOP_LEFT = "top_left"   # H1：客户区左上角 + 固定偏移
    CENTER = "center"       # H2：客户区中心 + 固定偏移


@dataclass(frozen=True)
class LayoutModel:
    """一套布局：四区几何 + 描述的锚定方式。"""

    reference_size: tuple[int, int]
    center: tuple[float, float]
    arm_x: float
    arm_y_top: float
    arm_y_bottom: float
    zone_w: int
    zone_h: int
    anchor_mode: AnchorMode = AnchorMode.TOP_LEFT

    @classmethod
    def from_reference(cls) -> LayoutModel:
        """由 spec §11.2 的实测值构造参考布局。"""
        z = C.REF_ZONE_CENTERS
        cx = sum(p[0] for p in z.values()) / 4.0
        cy = sum(p[1] for p in z.values()) / 4.0
        return cls(
            reference_size=(C.REF_W, C.REF_H),
            center=(cx, cy),
            arm_x=(z["right"][0] - z["left"][0]) / 2.0,
            arm_y_top=cy - z["top"][1],
            arm_y_bottom=z["bottom"][1] - cy,
            zone_w=C.ZONE_W,
            zone_h=C.ZONE_H,
        )

    def with_anchor(self, mode: AnchorMode) -> LayoutModel:
        return replace(self, anchor_mode=mode)

    def with_center(self, cx: float, cy: float) -> LayoutModel:
        return replace(self, center=(cx, cy))

    @property
    def zones(self) -> dict[str, Rect]:
        return layout_from_center(self.center[0], self.center[1],
                                  self.arm_x, self.arm_y_top, self.arm_y_bottom,
                                  self.zone_w, self.zone_h)

    def _layout_origin_in_client(self, cl_w: int, cl_h: int) -> tuple[float, float]:
        """返回『参考坐标系原点』落在客户区里的位置（客户区局部坐标）。"""
        rw, rh = self.reference_size
        if self.anchor_mode is AnchorMode.CENTER:
            return ((cl_w - rw) / 2.0, (cl_h - rh) / 2.0)
        return (0.0, 0.0)

    def for_client(self, cl_x: int, cl_y: int, cl_w: int, cl_h: int) -> LayoutModel:
        """把布局换算到给定客户区（客户区在屏幕上的位置为 cl_x, cl_y）。

        返回的模型其 center 已是屏幕绝对坐标。
        """
        ox, oy = self._layout_origin_in_client(cl_w, cl_h)
        return replace(self, center=(cl_x + ox + self.center[0],
                                     cl_y + oy + self.center[1]))

    def to_dict(self) -> dict:
        return {
            "reference_size": list(self.reference_size),
            "center": list(self.center),
            "arm_x": self.arm_x,
            "arm_y_top": self.arm_y_top,
            "arm_y_bottom": self.arm_y_bottom,
            "zone_w": self.zone_w,
            "zone_h": self.zone_h,
            "anchor_mode": self.anchor_mode.value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> LayoutModel:
        return cls(
            reference_size=tuple(d["reference_size"]),
            center=tuple(d["center"]),
            arm_x=float(d["arm_x"]),
            arm_y_top=float(d["arm_y_top"]),
            arm_y_bottom=float(d["arm_y_bottom"]),
            zone_w=int(d["zone_w"]),
            zone_h=int(d["zone_h"]),
            anchor_mode=AnchorMode(d.get("anchor_mode", "top_left")),
        )
