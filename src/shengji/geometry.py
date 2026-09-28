"""矩形与四区几何。纯计算，无 IO、无状态。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """轴对齐矩形，(x0, y0) 左上、(x1, y1) 右下，x1/y1 为开区间。"""

    x0: int
    y0: int
    x1: int
    y1: int

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(f"矩形坐标反转: {(self.x0, self.y0, self.x1, self.y1)}")

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.w / 2.0, self.y0 + self.h / 2.0)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def clip(self, bx0: int, by0: int, bx1: int, by1: int) -> "Rect":
        """裁到给定边界内。"""
        return Rect(max(self.x0, bx0), max(self.y0, by0),
                    min(self.x1, bx1), min(self.y1, by1))

    def expand(self, dx: int, dy: int, bounds: tuple[int, int] | None = None) -> "Rect":
        """四周外扩。bounds=(W, H) 时同时裁到画面内。"""
        r = Rect(self.x0 - dx, self.y0 - dy, self.x1 + dx, self.y1 + dy)
        if bounds is not None:
            r = r.clip(0, 0, bounds[0], bounds[1])
        return r


def layout_from_center(
    cx: float,
    cy: float,
    arm_x: float,
    arm_y_top: float,
    arm_y_bottom: float,
    zone_w: int,
    zone_h: int,
) -> dict[str, Rect]:
    """由「中心 + 臂长」推导四个出牌区。

    实测表明四区构成规则十字（spec §11A），因此 3 个数即可描述四区，
    而非 4 个独立矩形。上下臂长分开传参，因为实测上下并不完全对称
    （top 79px / bottom 85px），保留这一差异以免把真实布局「拉正」。
    """
    arms = {
        "top": (0.0, -arm_y_top),
        "bottom": (0.0, +arm_y_bottom),
        "left": (-arm_x, 0.0),
        "right": (+arm_x, 0.0),
    }
    out: dict[str, Rect] = {}
    for name, (dx, dy) in arms.items():
        zx, zy = cx + dx, cy + dy
        x0 = int(round(zx - zone_w / 2))
        y0 = int(round(zy - zone_h / 2))
        out[name] = Rect(x0, y0, x0 + zone_w, y0 + zone_h)
    return out
