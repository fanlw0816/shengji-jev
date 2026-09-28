import pytest

from shengji.geometry import Rect, layout_from_center


def test_rect_basics():
    r = Rect(10, 20, 30, 40)  # x0 y0 x1 y1
    assert r.w == 20
    assert r.h == 20
    assert r.center == (20.0, 30.0)


def test_rect_rejects_inverted():
    with pytest.raises(ValueError):
        Rect(30, 20, 10, 40)


def test_rect_clip_to_bounds():
    r = Rect(-5, -5, 50, 50)
    c = r.clip(0, 0, 100, 100)
    assert (c.x0, c.y0, c.x1, c.y1) == (0, 0, 50, 50)


def test_rect_expand_and_clip():
    r = Rect(40, 40, 60, 60)
    e = r.expand(30, 20)
    assert (e.x0, e.y0, e.x1, e.y1) == (10, 20, 90, 80)
    ec = r.expand(30, 20, bounds=(50, 50))
    assert (ec.x0, ec.y0, ec.x1, ec.y1) == (10, 20, 50, 50)


# 注意：layout_from_center 内部有 round()，且实测十字本身略不对称
# （左右臂导出中心 555.25、上下臂导出 557.5），故断言一律用容差而非等值。

def test_layout_from_center_produces_symmetric_cross():
    zones = layout_from_center(cx=600.0, cy=300.0, arm_x=107.5,
                               arm_y_top=82.0, arm_y_bottom=82.0,
                               zone_w=73, zone_h=79)
    assert set(zones) == {"top", "left", "right", "bottom"}
    for name, want in {"top": (600.0, 218.0), "bottom": (600.0, 382.0),
                       "left": (492.5, 300.0), "right": (707.5, 300.0)}.items():
        cx, cy = zones[name].center
        assert abs(cx - want[0]) <= 0.5, f"{name} x={cx} 期望 {want[0]}"
        assert abs(cy - want[1]) <= 0.5, f"{name} y={cy} 期望 {want[1]}"


def test_layout_from_center_keeps_zone_size():
    zones = layout_from_center(600.0, 300.0, 107.5, 82.0, 82.0, 73, 79)
    for r in zones.values():
        assert r.w == 73
        assert r.h == 79


def test_layout_from_center_asymmetric_arms_are_respected():
    """上下臂长不同时必须如实反映，不能把真实布局「拉正」。"""
    zones = layout_from_center(600.0, 300.0, 100.0, 70.0, 90.0, 73, 79)
    assert abs(zones["top"].center[1] - 230.0) <= 0.5
    assert abs(zones["bottom"].center[1] - 390.0) <= 0.5
