import numpy as np

from shengji.constants import REF_ZONE_CENTERS
from shengji.imaging import (
    card_mask,
    felt_mask,
    imread_unicode,
    imwrite_unicode,
    largest_card_blob,
    measure_card_width,
    zone_rect,
)

# 注意：本测试刻意使用中文文件名，以覆盖 Windows 上 cv2.imread 无法打开
# 非 ASCII 路径的问题（静默返回 None）。


def test_imread_unicode_handles_chinese_path(tmp_path):
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img[:, :, 2] = 255  # 纯红
    p = tmp_path / "中文文件名.png"
    assert imwrite_unicode(p, img) is True

    back = imread_unicode(p)
    assert back is not None
    assert back.shape == (8, 8, 3)
    assert int(back[0, 0, 2]) == 255


def test_imread_unicode_returns_none_for_missing(tmp_path):
    assert imread_unicode(tmp_path / "不存在.png") is None


def test_fixtures_all_1280x720(shots):
    for name, img in shots.items():
        assert img.shape == (720, 1280, 3), f"{name} 尺寸异常: {img.shape}"


def _crop(img, name):
    x0, y0, x1, y1 = zone_rect(name)
    return img[y0:y1, x0:x1]


def test_felt_mask_says_all_felt_when_nobody_played(shots):
    """没有人出牌时，四个出牌区必须几乎全部判定为桌面。

    阈值取 0.85 而非 0.90：实测下区（自己，y371-450）为 87.8%，
    因为它比其它三区更靠近手牌与底部 UI，边缘混入更多非桌面像素。
    其余三区均 > 0.96。
    """
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["empty"], name)
        frac = felt_mask(seg).mean()
        assert frac > 0.85, f"{name} 空区应几乎全是桌面，实测 {frac:.2%}"


def test_card_mask_finds_cards_when_everyone_played_two(shots):
    """所有人出两张时，四个区都必须检出牌面。"""
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["all_two"], name)
        frac = card_mask(seg).mean()
        assert frac > 0.70, f"{name} 应检出大量牌面，实测 {frac:.2%}"


def test_card_mask_zero_when_nobody_played(shots):
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["empty"], name)
        assert card_mask(seg).mean() < 0.02, f"{name} 空区不应有牌面"


def test_felt_and_card_masks_are_complementary(shots):
    """同一块区域不应同时被判为桌面和牌面。"""
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["all_two"], name)
        overlap = (felt_mask(seg) & card_mask(seg)).mean()
        assert overlap < 0.05, f"{name} 桌面与牌面判定重叠 {overlap:.2%}"


def test_largest_card_blob_on_two_cards(shots):
    """所有人出两张时，每区最大连通块应是『两张牌叠放』= 72x78 左右。"""
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["all_two"], name)
        blob = largest_card_blob(seg, min_area=200)
        assert blob is not None, f"{name} 未找到牌面连通块"
        w, h = blob["w"], blob["h"]
        assert 60 <= w <= 90, f"{name} 两张牌宽度异常: {w}"
        assert abs(h - 79) <= 10, f"{name} 牌高异常: {h}"


def test_measure_card_width_recovers_56(shots):
    """由「两张牌宽 72」与「叠放偏移 16」应反推出单张牌宽 56。"""
    w = measure_card_width(shots["all_two"])
    assert w is not None
    assert abs(w - 56) <= 8, f"反推牌宽异常: {w}"


def test_measure_card_width_none_when_no_cards(shots):
    assert measure_card_width(shots["empty"]) is None
