from shengji import constants as C


def test_reference_resolution():
    assert C.REF_W == 1280
    assert C.REF_H == 720


def test_zone_centers_form_symmetric_cross():
    """四区必须构成对称十字 —— 这是 spec §11A 的核心不变量。"""
    z = C.REF_ZONE_CENTERS
    xs = [z[k][0] for k in ("top", "bottom")]
    ys = [z[k][1] for k in ("left", "right")]
    assert abs(xs[0] - xs[1]) < 3, "上下区 x 不对齐"
    assert abs(ys[0] - ys[1]) < 3, "左右区 y 不对齐"

    cx = sum(z[k][0] for k in z) / 4
    cy = sum(z[k][1] for k in z) / 4
    assert abs((z["left"][0] - cx) + (z["right"][0] - cx)) < 3, "左右臂不对称"
    assert abs((z["top"][1] - cy) + (z["bottom"][1] - cy)) < 8, "上下臂不对称"


def test_card_geometry_matches_real_playing_card():
    """实测牌宽高比应接近标准扑克牌 2.5:3.5。"""
    ratio = C.CARD_W / C.CARD_H
    assert abs(ratio - 2.5 / 3.5) < 0.02
    assert C.CARD_W == 56
    assert C.CARD_H == 79
    assert C.CARD_STACK_OFFSET == 16


def test_felt_color_and_threshold():
    assert C.FELT_BGR == (110, 69, 21)
    assert C.FELT_DIST_MAX == 45


def test_card_mask_thresholds():
    assert C.CARD_V_MIN == 165
    assert C.CARD_S_MAX == 110


def test_card_blob_filter_is_sane():
    """牌尺寸块的筛选窗口必须能把真实牌装进去。"""
    assert C.CARD_BLOB_MIN_W <= C.CARD_W <= C.CARD_BLOB_MAX_W
    assert C.CARD_BLOB_MIN_H <= C.CARD_H <= C.CARD_BLOB_MAX_H


def test_card_blob_area_separates_real_cards_from_ui():
    """实测：1 张牌块约 3736px，右侧静态 UI 元素约 1836px，宽高比都是 0.92。

    只有面积能区分二者，因此阈值必须严格落在两者之间。
    """
    assert 1836 < C.CARD_BLOB_MIN_AREA < 3736


def test_zone_assign_dist_is_usable():
    """归属阈值必须小于相邻区中心间距的一半，否则会跨区串味。"""
    z = C.REF_ZONE_CENTERS
    min_gap = min(abs(z["left"][0] - z["top"][0]),
                  abs(z["right"][0] - z["top"][0]),
                  abs(z["top"][1] - z["left"][1]))
    assert C.ZONE_ASSIGN_MAX_DIST < min_gap
