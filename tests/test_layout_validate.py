from dataclasses import replace

from shengji.layout.model import LayoutModel
from shengji.layout.validate import (
    ValidationReport,
    Violation,
    validate_against_frame,
    validate_geometry,
)


# ---------- 几何校验：与画面无关，任何时候都能跑 ----------

def test_reference_layout_passes_geometry():
    rep = validate_geometry(LayoutModel.from_reference())
    assert rep.ok, [v.detail for v in rep.violations]


def test_detects_zones_too_close():
    """左右臂长过小会让四区挤在一起，必须拦下。

    注意：本模型用「单一 arm_x」推导左右区，左右臂**结构上恒对称**，
    因此不存在「左右臂不对称」这种可违反的状态（初稿的 arm_asymmetry_x
    实际是永远为真的空检查，已移除）。真正需要拦的是四区重叠。
    """
    from shengji import constants as C

    bad = replace(LayoutModel.from_reference(), arm_x=float(C.ZONE_W) * 0.3)
    rep = validate_geometry(bad)
    assert any(v.code == "zones_too_close" for v in rep.violations), \
        [v.code for v in rep.violations]


def test_reference_layout_zones_are_well_separated():
    m = LayoutModel.from_reference()
    z = m.zones
    pairs = [("left", "right"), ("top", "bottom"), ("left", "top"),
             ("right", "bottom"), ("left", "bottom"), ("right", "top")]
    from math import hypot

    for a, b in pairs:
        ax, ay = z[a].center
        bx, by = z[b].center
        assert hypot(ax - bx, ay - by) >= m.zone_w, f"{a}-{b} 过近"


def test_detects_wrong_zone_size():
    bad = replace(LayoutModel.from_reference(), zone_h=200)
    rep = validate_geometry(bad)
    assert any(v.code == "zone_size_mismatch" for v in rep.violations)


def test_detects_zone_too_narrow():
    bad = replace(LayoutModel.from_reference(), zone_w=30)
    rep = validate_geometry(bad)
    assert any(v.code == "zone_too_narrow" for v in rep.violations)


def test_detects_arm_asymmetry_y():
    bad = replace(LayoutModel.from_reference(), arm_y_top=40.0, arm_y_bottom=120.0)
    rep = validate_geometry(bad)
    assert any(v.code == "arm_asymmetry_y" for v in rep.violations)


def test_detects_card_width_drift():
    """牌宽偏离基准 —— 用于发现 DPI 缩放被改或客户端改版。"""
    rep = validate_geometry(LayoutModel.from_reference(), card_width=70.0)
    assert any(v.code == "card_width_drift" for v in rep.violations)


def test_violation_is_descriptive():
    v = Violation("arm_asymmetry_x", "左右臂不对称 40.0px")
    assert "arm_asymmetry_x" in repr(v)


# ---------- 牌面块一致性校验：真正有区分力的判据 ----------
# 设计变更（实测驱动）：初稿用「区必须在桌面色上」校验，但实测发现
# 牌桌周围的背景与桌面同色（阈值 90 时空区覆盖 100% 但全图覆盖 74%），
# 该判据没有区分力。改用「牌尺寸块」这一强特征。

def test_against_frame_verifies_reference_on_all_two(shots):
    rep = validate_against_frame(LayoutModel.from_reference(), shots["all_two"])
    assert rep.verifiable
    assert rep.ok, [v.detail for v in rep.violations]


def test_against_frame_verifies_reference_on_others_one(shots):
    rep = validate_against_frame(LayoutModel.from_reference(), shots["others_one"])
    assert rep.verifiable
    assert rep.ok, [v.detail for v in rep.violations]


def test_against_frame_verifies_reference_on_next_two(shots):
    rep = validate_against_frame(LayoutModel.from_reference(), shots["next_two"])
    assert rep.verifiable
    assert rep.ok, [v.detail for v in rep.violations]


def test_against_frame_marks_empty_frame_unverifiable(shots):
    """空桌无法校验 —— 必须显式表达为 verifiable=False，而不是假装通过。

    实测：空桌帧里唯一的「牌尺寸块」是右侧静态 UI 元素 (1042,282) 68x74，
    其面积 1836 低于 CARD_BLOB_MIN_AREA=3000，因此应被滤掉，候选数为 0。
    """
    rep = validate_against_frame(LayoutModel.from_reference(), shots["empty"])
    assert not rep.verifiable
    assert rep.ok


def test_against_frame_rejects_shifted_layout_right(shots):
    """布局整体右移 200px 后，真实牌块全部落到预测区之外，必须被否决。"""
    m = LayoutModel.from_reference()
    cx, cy = m.center
    rep = validate_against_frame(m.with_center(cx + 200, cy), shots["all_two"])
    assert rep.verifiable
    assert not rep.ok
    assert any(v.code == "cards_outside_zones" for v in rep.violations)


def test_against_frame_rejects_shifted_layout_down(shots):
    m = LayoutModel.from_reference()
    cx, cy = m.center
    rep = validate_against_frame(m.with_center(cx, cy + 200), shots["all_two"])
    assert rep.verifiable
    assert not rep.ok


def test_report_default_is_ok_and_verifiable():
    rep = ValidationReport()
    assert rep.ok
    assert rep.verifiable
