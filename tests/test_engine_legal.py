"""合法着法枚举（Plan 7 的 7a）与结构匹配的测试。

重点：把 R1「合法性」与计划要求的「自洽校验」钉死 ——
枚举出的**每一手**都必须能被 `trick.py` 判为合法，且**绝不枚举手上不存在的牌**。
"""

from dataclasses import replace

import pytest

from shengji.cards import Card, parse_code
from shengji.engine.legal import (
    PROFILES,
    LegalError,
    RuleProfile,
    get_rule_profile,
    legal_moves,
)
from shengji.engine.trick import (
    PlayedCards,
    Structure,
    structure_of,
)
from shengji.engine.trump import GROUP_TRUMP, TrumpInfo, sort_desc

S, H, D, C = 0, 1, 2, 3

# 直接取数据表里的 default：source=UNVERIFIED，正好用来测**算法本身**
# （拒绝启动的纪律由 get_rule_profile 负责，见文末）
PROFILE = PROFILES["default"]


def _t(suit=S, level=2) -> TrumpInfo:
    return TrumpInfo(kind="suit", suit=suit, level_rank=level)


def _hand(spec: str) -> list[Card]:
    """按 ASCII 编码建手牌，如 'HK HQ H10 H10 joker_small'。"""
    return [parse_code(tok) for tok in spec.split()]  # type: ignore[misc]


def _by_structure(res) -> dict[Structure, list]:
    out: dict[Structure, list] = {}
    for m in res.moves:
        out.setdefault(m.structure, []).append(m)
    return out


# ---------- 自洽校验（计划 §3 的硬要求）----------


@pytest.mark.parametrize("spec,trump_suit", [
    ("HJ HQ HK HA H10 H10", S),
    ("HK HK HQ HQ HJ HJ", S),
    ("S2 S2 SA SK SK SQ", S),
    ("joker_small joker_small HA HK", S),
    ("HJ HQ HK HA H10 H10 SA SA S5 S6 S7", H),   # 主牌为 ♥，花色构成不同
])
def test_every_enumerated_move_passes_engine_structure_check(spec, trump_suit):
    """枚举出的每一手都要能被 `structure_of` 判为它自己声称的结构。"""
    hand = _hand(spec)
    res = legal_moves(hand, None, _t(suit=trump_suit), PROFILE)
    assert res.moves
    for m in res.moves:
        assert structure_of(m.cards) is m.structure, m.label()


@pytest.mark.parametrize("spec,lead_spec", [
    ("HJ HQ HK HA H10 H10", "H10"),
    ("HJ HQ HK HA H10 H10", "H10 H10"),
    ("HK HK HQ HQ HJ HJ", "HK HK"),
    ("SA SK SQ SJ S10", "SA SA"),
    ("HJ HQ HK HA H10 SA S5 S6", "SA SA SA"),
])
def test_follow_moves_pass_engine_structure_check(spec, lead_spec):
    hand = _hand(spec)
    lead_cards = tuple(_hand(lead_spec))
    res = legal_moves(hand, PlayedCards(seat=1, cards=lead_cards), _t(), PROFILE)
    for m in res.moves:
        assert structure_of(m.cards) is m.structure, m.label()
        assert len(m.cards) == len(lead_cards)


def test_never_enumerates_cards_not_in_hand():
    """识别错认（手上没这张牌）必须在这一步暴露。"""
    hand = _hand("HJ HQ HK HA H10 H10 SA S5")
    trump = _t()
    for lead in (None,
                 PlayedCards(seat=1, cards=(parse_code("H5"),)),
                 PlayedCards(seat=1, cards=(parse_code("H10"),) * 2),
                 PlayedCards(seat=1, cards=(parse_code("D5"),) * 2),
                 PlayedCards(seat=1, cards=(parse_code("C3"),) * 3)):
        res = legal_moves(hand, lead, trump, PROFILE)
        for m in res.moves:
            for card in m.cards:
                assert hand.count(card) >= m.cards.count(card), m.label()


def test_moves_are_unique_by_multiset():
    res = legal_moves(_hand("HJ HQ HK HA H10 H10"), None, _t(), PROFILE)
    keys = [m.codes() for m in res.moves]
    assert len(keys) == len(set(keys))


# ---------- 领出 ----------


def test_lead_enumerates_singles_pairs_and_throws():
    res = legal_moves(_hand("HJ HQ HK HA H10 H10"), None, _t(), PROFILE)
    grouped = _by_structure(res)

    assert {m.codes()[0] for m in grouped[Structure.SINGLE]} == {
        "H10", "HA", "HJ", "HK", "HQ"}
    assert len(grouped[Structure.PAIR]) == 1
    assert grouped[Structure.PAIR][0].codes() == ("H10", "H10")
    assert grouped[Structure.SINGLE][0].group == H
    assert grouped[Structure.MIXED][0].group == H


def test_lead_enumerates_tractor():
    res = legal_moves(_hand("HK HK HQ HQ"), None, _t(), PROFILE)
    tractors = _by_structure(res).get(Structure.TRACTOR, [])
    assert [m.codes() for m in tractors] == [("HK", "HK", "HQ", "HQ")]


def test_lead_enumerates_pair_of_jokers_as_pair_not_throw():
    res = legal_moves(_hand("joker_small joker_small"), None, _t(), PROFILE)
    grouped = _by_structure(res)
    assert grouped[Structure.PAIR][0].group == GROUP_TRUMP
    assert Structure.MIXED not in grouped


# ---------- 跟牌：强制与结构优先 ----------


def test_follow_single_only_from_lead_group():
    hand = _hand("HJ HQ HK HA H10 SA S5 S6")
    lead = PlayedCards(seat=1, cards=(parse_code("H5"),))
    res = legal_moves(hand, lead, _t(), PROFILE)
    assert len(res.moves) == 5                       # ♥ 的 5 个不同点数
    assert all(m.cards[0].suit == H for m in res.moves)
    assert all(m.matched for m in res.moves)


def test_follow_pair_prefers_pair_when_available():
    hand = _hand("HK HK HQ HJ H10")
    lead = PlayedCards(seat=1, cards=(parse_code("H10"),) * 2)
    res = legal_moves(hand, lead, _t(), PROFILE)
    assert len(res.moves) == 1
    assert res.moves[0].codes() == ("HK", "HK")
    assert res.moves[0].matched


def test_follow_pair_without_pair_falls_back_to_all_combos():
    hand = _hand("HK HQ HJ H10")                     # 无对子
    lead = PlayedCards(seat=1, cards=(parse_code("H10"),) * 2)
    res = legal_moves(hand, lead, _t(), PROFILE)
    assert len(res.moves) == 6                       # C(4,2)
    assert all(not m.matched for m in res.moves)


def test_follow_insufficient_group_plays_all_of_it_plus_padding():
    hand = _hand("H5 SA SK SQ SJ")                   # ♥ 只有 1 张
    lead = PlayedCards(seat=1,
                       cards=(parse_code("HA"), parse_code("HK"), parse_code("HQ")))
    res = legal_moves(hand, lead, _t(), PROFILE)
    assert res.moves
    for m in res.moves:
        assert len(m.cards) == 3
        assert parse_code("H5") in m.cards                    # 该组必须全出
        assert sum(1 for c in m.cards if c.suit == S) == 2    # 差额垫主牌


def test_follow_count_equals_lead_count_for_throws():
    hand = _hand("SA SK SQ SJ H10 S2 S2")
    lead = PlayedCards(seat=1, cards=(parse_code("HA"),) * 3)
    res = legal_moves(hand, lead, _t(), PROFILE)
    assert res.moves
    assert {len(m.cards) for m in res.moves} == {3}


def test_follow_required_false_allows_free_discard():
    hand = _hand("HJ HQ HK HA H10 SA S5")
    lead = PlayedCards(seat=1, cards=(parse_code("H5"),))
    free = replace(PROFILE, follow_required=False)
    res = legal_moves(hand, lead, _t(), free)
    assert any(m.cards[0].suit == S for m in res.moves)      # 可以垫别的花色


def test_must_play_as_many_as_possible_false_allows_keeping_group_cards():
    hand = _hand("H5 H6 SA SK SQ SJ")
    lead = PlayedCards(seat=1, cards=(parse_code("HA"),) * 3)
    loose = replace(PROFILE, must_play_as_many_as_possible=False)
    res = legal_moves(hand, lead, _t(), loose)
    assert res.moves
    # 允许不从该组出满 -> 可以出现完全不含 ♥ 的着法
    assert any(all(c.suit == S for c in m.cards) for m in res.moves)


# ---------- 甩牌：诚实边界 ----------


def test_throws_are_marked_unverified():
    res = legal_moves(_hand("HA HK HQ HJ H10 H10"), None, _t(), PROFILE)
    throws = _by_structure(res)[Structure.MIXED]
    assert throws
    assert all(not m.verified for m in throws)
    assert all("本机无法判定" in m.note for m in throws)


def test_throw_cards_are_top_of_their_group():
    hand = _hand("HA HK HQ HJ H10")
    trump = _t()
    res = legal_moves(hand, None, trump, PROFILE)
    ordered = [c.code() for c in sort_desc(list(hand), trump)]
    throws = _by_structure(res)[Structure.MIXED]
    assert throws
    for m in throws:
        assert set(m.codes()) == set(ordered[:len(m.cards)])


def test_throw_respects_max_cards():
    small = replace(PROFILE, throw_max_cards=2)
    res = legal_moves(_hand("HA HK HQ HJ H10"), None, _t(), small)
    throws = _by_structure(res)[Structure.MIXED]
    assert throws
    assert {len(m.cards) for m in throws} == {2}


def test_cross_group_throw_rule_unknown_is_reported_not_invented():
    cross = replace(PROFILE, throw_single_group=False)
    res = legal_moves(_hand("HA HK HQ"), None, _t(), cross)
    assert res.complete is False
    assert any("跨门" in n for n in res.notes)


# ---------- 拒绝路径（失败要可见）----------


def test_unverified_profile_refuses_to_start():
    with pytest.raises(LegalError) as ei:
        get_rule_profile("default")
    assert "尚未实测确认" in str(ei.value)
    assert "拒绝启动" in str(ei.value)


def test_unknown_profile_name_raises():
    with pytest.raises(LegalError):
        get_rule_profile("不存在的 profile")


def test_verified_profile_is_returned():
    from shengji.engine import legal as legal_mod

    legal_mod.PROFILES["_test_ok"] = RuleProfile(source="measured")
    try:
        assert get_rule_profile("_test_ok").source == "measured"
    finally:
        del legal_mod.PROFILES["_test_ok"]


def test_empty_hand_raises():
    with pytest.raises(LegalError):
        legal_moves([], None, _t(), PROFILE)


def test_combo_explosion_raises_instead_of_truncating():
    """超上限必须报错 —— 静默截断会得到一个「漏了着法但看起来能用」的集合。"""
    hand = [Card(rank=r, suit=S) for r in range(2, 15)] * 2   # 26 张 → 取 25
    hand = hand[:25]
    lead = PlayedCards(seat=1, cards=(parse_code("HA"),) * 3)
    with pytest.raises(LegalError) as ei:
        legal_moves(hand, lead, _t(), PROFILE, max_combos=10)
    assert "超过上限" in str(ei.value)
    assert "静默截断" in str(ei.value)


def test_insufficient_hand_for_lead_count_raises():
    hand = _hand("H5 SA")                            # 共 2 张
    lead = PlayedCards(seat=1, cards=(parse_code("HA"),) * 5)
    with pytest.raises(LegalError):
        legal_moves(hand, lead, _t(), PROFILE)
