from shengji.layout.detect import (
    detect_occupied_zones,
    estimate_card_count,
    nearest_zone,
    select_anchor,
)
from shengji.layout.model import AnchorMode, LayoutModel


def test_estimate_card_count_from_width():
    """宽度模型 w = 56 + (n-1)*16。实测 56->1 张、72->2 张。"""
    assert estimate_card_count(56) == 1
    assert estimate_card_count(72) == 2
    assert estimate_card_count(88) == 3
    assert estimate_card_count(40) == 1     # 极小值兜底
    assert estimate_card_count(120) == 5


def test_nearest_zone_picks_expected_zone():
    m = LayoutModel.from_reference()
    z = m.zones
    for name in ("top", "left", "right", "bottom"):
        cxx, cyy = z[name].center
        got, d = nearest_zone(m, cxx, cyy)
        assert got == name
        assert d <= 0.5


# ---------- 端到端四区定位：用 4 张真实局面截图验收 ----------

def test_empty_state_has_no_occupied_zone(shots):
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["empty"], m)
    assert occ == {}, occ


def test_others_one_occupies_all_but_bottom(shots):
    """其他三家各出一张：上/左/右有牌，自己(下)没出。"""
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["others_one"], m)
    assert set(occ) == {"top", "left", "right"}, occ


def test_next_two_occupies_only_right(shots):
    """下家出两张 —— 实测推得下家 = 右区。

    这条专门防「检索区跨区串味」：先前把区 bbox 外扩 120px 的写法
    会让上区也把右区的牌算进来。
    """
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["next_two"], m)
    assert set(occ) == {"right"}, occ


def test_all_two_occupies_every_zone(shots):
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["all_two"], m)
    assert set(occ) == {"top", "left", "right", "bottom"}, occ


def test_card_count_estimates_from_blob_width(shots):
    """两张牌宽 72、单张 56 -> 应由宽度反推张数。"""
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["all_two"], m)
    for name in ("top", "left", "right", "bottom"):
        assert occ[name]["count"] == 2, f"{name}: {occ[name]['count']}"


def test_card_count_one_when_single_card(shots):
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["others_one"], m)
    for name in ("top", "left", "right"):
        assert occ[name]["count"] == 1, f"{name}: {occ[name]['count']}"


def test_occupied_zones_have_no_blob_split(shots):
    """正常局面下每个区应只有一个牌块。分裂说明形态学处理需要复核。"""
    m = LayoutModel.from_reference()
    for state in ("others_one", "next_two", "all_two"):
        occ = detect_occupied_zones(shots[state], m)
        for name, info in occ.items():
            assert info["n_blobs"] == 1, f"{state}/{name} 分裂成 {info['n_blobs']} 块"


# ---------- 锚定选择 ----------

def test_select_anchor_uses_top_left_on_fixtures(shots):
    """夹具本身就是参考分辨率下的整幅画面，H1 应当通过。"""
    for state in ("others_one", "all_two", "next_two"):
        model, rep, mode = select_anchor(shots[state], (0, 0, 1280, 720),
                                         LayoutModel.from_reference())
        assert mode is AnchorMode.TOP_LEFT
        assert rep.ok, [v.detail for v in rep.violations]
        assert rep.verifiable


def test_select_anchor_on_empty_frame_is_unverified_but_accepted(shots):
    """空桌无法校验，应接受 H1 并把 verifiable=False 传递出去。"""
    model, rep, mode = select_anchor(shots["empty"], (0, 0, 1280, 720),
                                     LayoutModel.from_reference())
    assert mode is AnchorMode.TOP_LEFT
    assert rep.ok
    assert not rep.verifiable


def test_select_anchor_shifts_with_client_origin(shots):
    """客户区不在 (0,0) 时，四区必须随之平移。"""
    m0, _, _ = select_anchor(shots["all_two"], (0, 0, 1280, 720),
                             LayoutModel.from_reference())
    m1, _, _ = select_anchor(shots["all_two"], (300, 120, 1280, 720),
                             LayoutModel.from_reference())
    assert abs(m1.center[0] - (m0.center[0] + 300)) <= 0.01
    assert abs(m1.center[1] - (m0.center[1] + 120)) <= 0.01
