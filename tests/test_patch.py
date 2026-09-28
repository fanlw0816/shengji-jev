import numpy as np

from shengji import constants as C
from shengji.layout.model import LayoutModel
from shengji.recognition.patch import (
    extract_card_patches,
    split_patches,
)


def test_all_two_yields_two_patches_per_zone(shots):
    """所有人出两张 -> 4 区各 2 张角标切片。"""
    m = LayoutModel.from_reference()
    patches = extract_card_patches(shots["all_two"], m)
    assert set(patches) == {"top", "left", "right", "bottom"}
    for zone, plist in patches.items():
        assert len(plist) == 2, f"{zone}: {len(plist)}"


def test_others_one_yields_three_patches(shots):
    """其他三家各出一张 -> 3 张切片，自己(下区)没有。"""
    m = LayoutModel.from_reference()
    patches = extract_card_patches(shots["others_one"], m)
    assert set(patches) == {"top", "left", "right"}
    for zone, plist in patches.items():
        assert len(plist) == 1


def test_next_two_yields_two_patches_only_right(shots):
    m = LayoutModel.from_reference()
    patches = extract_card_patches(shots["next_two"], m)
    assert set(patches) == {"right"}
    assert len(patches["right"]) == 2


def test_empty_yields_no_patches(shots):
    m = LayoutModel.from_reference()
    assert extract_card_patches(shots["empty"], m) == {}


def test_patch_geometry_is_exact(shots):
    """每张角标切片必须是 16x28，且点数/花色片尺寸正确。"""
    m = LayoutModel.from_reference()
    patches = extract_card_patches(shots["all_two"], m)
    r0, r1 = C.CORNER_RANK_ROWS
    s0, s1 = C.CORNER_SUIT_ROWS
    for plist in patches.values():
        for p in plist:
            assert p.corner.shape == (C.CORNER_H, C.CARD_SLIVER_W, 3)
            assert p.rank_patch.shape[0] == r1 - r0
            assert p.suit_patch.shape[0] == s1 - s0
            assert p.rank_patch.shape[1] == C.CARD_SLIVER_W
            assert p.is_complete


def test_patch_slots_are_offset_by_stack_offset(shots):
    """同一区相邻两张牌的切片 x 必须相差 CARD_STACK_OFFSET（16px）。"""
    m = LayoutModel.from_reference()
    patches = extract_card_patches(shots["all_two"], m)
    for plist in patches.values():
        xs = sorted(p.x for p in plist)
        assert xs[1] - xs[0] == C.CARD_STACK_OFFSET


def test_patch_does_not_cross_zone(shots):
    """切片必须落在所属区附近，不能串到别的区。"""
    m = LayoutModel.from_reference()
    patches = extract_card_patches(shots["all_two"], m)
    zones = m.zones
    for zone, plist in patches.items():
        zcx, zcy = zones[zone].center
        for p in plist:
            assert abs(p.x + C.CARD_SLIVER_W / 2 - zcx) < C.ZONE_ASSIGN_MAX_DIST
            assert abs(p.y + C.CORNER_H / 2 - zcy) < C.ZONE_ASSIGN_MAX_DIST


def test_split_patches_returns_disjoint_rows():
    corner = np.arange(C.CORNER_H * C.CARD_SLIVER_W * 3,
                       dtype=np.uint8).reshape(C.CORNER_H, C.CARD_SLIVER_W, 3)
    rp, sp = split_patches(corner)
    r0, r1 = C.CORNER_RANK_ROWS
    s0, s1 = C.CORNER_SUIT_ROWS
    assert np.array_equal(rp, corner[r0:r1])
    assert np.array_equal(sp, corner[s0:s1])
    assert r1 <= s0, "点数与花色片不应重叠"


def test_rank_and_suit_bands_have_a_gap(shots):
    """实测点数与花色之间有空隙（中位 row=14）。

    用「其它三家出一张」的 top 区样本验证：第 14~16 行应几乎没有墨迹。
    这条断言把 spike 里的实测发现固化下来，防止常量被随手改坏。
    """
    from shengji.recognition.templates import ink_mask

    m = LayoutModel.from_reference()
    patches = extract_card_patches(shots["others_one"], m)
    corner = patches["top"][0].corner
    rows = ink_mask(corner).sum(axis=1)
    gap = rows[14:17].sum()
    above = rows[0:14].sum()
    below = rows[17:28].sum()
    assert above > 20, f"点数带墨迹过少: {above}"
    assert below > 20, f"花色带墨迹过少: {below}"
    assert gap <= 2, f"切分空隙不干净: {rows[14:17].tolist()}"
