from shengji import constants as C
from shengji.layout.model import AnchorMode, LayoutModel


def _ref_model():
    return LayoutModel.from_reference()


def test_from_reference_matches_measured_centers():
    """由「中心 + 臂长」重建的十字应与实测值接近（容差 4px）。

    实测十字本身并不完美对称，重建必然有偏差：
    - 左右臂导出的中心 x = 555.25，上下臂导出 x = 557.5（差 2.25px）
    - 左右区实测 y = 322.5，而十字中心 y = 325.5（侧臂比中心高 3px）
    模型用「单一中心 + 两个纵向臂长」描述，无法复现这 3px 的侧臂偏移。
    该偏差相对 79px 的牌高约 4%，且远小于 ZONE_ASSIGN_MAX_DIST=60px，
    不影响归属判定，故接受并显式记录，而不是悄悄放宽到看不出问题。
    """
    m = _ref_model()
    assert m.reference_size == (1280, 720)
    z = m.zones
    for name, (mx, my) in C.REF_ZONE_CENTERS.items():
        cx, cy = z[name].center
        assert abs(cx - mx) <= 4.0, f"{name} x 偏差 {cx - mx:+.1f}"
        assert abs(cy - my) <= 4.0, f"{name} y 偏差 {cy - my:+.1f}"


def test_reference_arms_are_measured_values():
    m = _ref_model()
    assert abs(m.arm_x - 107.75) <= 0.01
    assert abs(m.arm_y_top - 79.0) <= 0.01
    assert abs(m.arm_y_bottom - 85.0) <= 0.01


def test_reference_anchor_is_identity():
    """参考尺寸 + H1(左上角) 锚定时，布局应等于重建坐标本身。"""
    m = _ref_model()
    assert m.anchor_mode is AnchorMode.TOP_LEFT
    shifted = m.for_client(0, 0, 1280, 720)
    assert shifted.center == m.center


def test_top_left_anchor_shifts_by_origin():
    m = _ref_model()
    s = m.for_client(100, 50, 1280, 720)
    assert abs(s.center[0] - (m.center[0] + 100)) <= 0.01
    assert abs(s.center[1] - (m.center[1] + 50)) <= 0.01
    # 四区整体平移，臂长不变
    for name in m.zones:
        assert abs(s.zones[name].center[0] - (m.zones[name].center[0] + 100)) <= 0.51


def test_center_anchor_centers_layout_in_client():
    m = _ref_model().with_anchor(AnchorMode.CENTER)
    # 客户区比参考画面大 200x100 -> 布局整体右移 100、下移 50
    s = m.for_client(0, 0, 1480, 820)
    assert abs(s.center[0] - (m.center[0] + 100)) <= 0.01
    assert abs(s.center[1] - (m.center[1] + 50)) <= 0.01


def test_center_anchor_no_shift_at_reference_size():
    m = _ref_model().with_anchor(AnchorMode.CENTER)
    s = m.for_client(0, 0, 1280, 720)
    assert s.center == m.center


def test_arm_lengths_preserved_across_anchors():
    m = _ref_model()
    for mode in (AnchorMode.TOP_LEFT, AnchorMode.CENTER):
        s = m.with_anchor(mode).for_client(0, 0, 1280, 720)
        z = s.zones
        zcx = sum(r.center[0] for r in z.values()) / 4
        assert abs((zcx - z["left"].center[0]) - 107.75) <= 0.5
        assert abs((z["right"].center[0] - zcx) - 107.75) <= 0.5


def test_to_dict_from_dict_round_trip():
    m = _ref_model().with_anchor(AnchorMode.CENTER)
    back = LayoutModel.from_dict(m.to_dict())
    assert back == m
