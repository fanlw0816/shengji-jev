import pytest

from shengji.cards import RANK_ACE, RANK_KING, Card
from shengji.engine.trump import (
    GROUP_TRUMP,
    TIER_BIG_JOKER,
    TIER_MAIN_LEVEL,
    TIER_OFF_LEVEL,
    TIER_SIDE,
    TIER_SMALL_JOKER,
    TIER_TRUMP_SUIT,
    TrumpInfo,
    all_trumps,
    card_strength,
    compare,
    group_of,
    is_trump,
    sort_desc,
)

S, H, D, C = 0, 1, 2, 3


def _t(suit: int | None = S, level: int = 2) -> TrumpInfo:
    if suit is None:
        return TrumpInfo(kind="no_trump", suit=None, level_rank=level)
    return TrumpInfo(kind="suit", suit=suit, level_rank=level)


# ---------- 主牌判定 ----------

def test_trump_suit_cards_are_trump():
    t = _t(S)
    assert is_trump(Card(rank=RANK_ACE, suit=S), t)
    assert not is_trump(Card(rank=RANK_ACE, suit=H), t)


def test_jokers_are_always_trump():
    t = _t(S)
    assert is_trump(Card.small_joker(), t)
    assert is_trump(Card.big_joker(), t)


def test_level_cards_of_every_suit_are_trump():
    """关键规则：任何花色的级牌都是主牌。初版设计曾遗漏级牌。"""
    t = _t(S, level=2)
    for suit in (S, H, D, C):
        assert is_trump(Card(rank=2, suit=suit), t), f"花色{suit}的级牌应为主牌"


def test_no_trump_makes_level_cards_trump_only():
    t = _t(None, level=5)
    assert is_trump(Card(rank=5, suit=H), t)
    assert is_trump(Card(rank=5, suit=S), t)
    assert not is_trump(Card(rank=RANK_ACE, suit=S), t)


# ---------- 牌力次序 ----------

def test_strength_order_matches_spec():
    """大王 > 小王 > 正级 > 副级 > 主花色A > ... > 副牌。"""
    t = _t(S, level=2)
    big = card_strength(Card.big_joker(), t)
    small = card_strength(Card.small_joker(), t)
    main_level = card_strength(Card(rank=2, suit=S), t)
    off_level = card_strength(Card(rank=2, suit=H), t)
    trump_ace = card_strength(Card(rank=RANK_ACE, suit=S), t)
    side_ace = card_strength(Card(rank=RANK_ACE, suit=H), t)

    assert big > small > main_level > off_level > trump_ace > side_ace
    assert big[0] == TIER_BIG_JOKER
    assert small[0] == TIER_SMALL_JOKER
    assert main_level[0] == TIER_MAIN_LEVEL
    assert off_level[0] == TIER_OFF_LEVEL
    assert trump_ace[0] == TIER_TRUMP_SUIT
    assert side_ace[0] == TIER_SIDE


def test_off_level_cards_are_equal_by_default():
    """默认规则：副级三门同级、不可互压（设计文档 §7.6.3 待实测确认）。"""
    t = _t(S, level=7)
    assert card_strength(Card(rank=7, suit=H), t) == \
        card_strength(Card(rank=7, suit=D), t) == \
        card_strength(Card(rank=7, suit=C), t)


def test_level_rank_is_skipped_in_trump_suit_order():
    """主花色内，级牌被提到正级位置，不再按点数参与普通比较。"""
    t = _t(S, level=10)
    # 主花色 10 是正级，应强于主花色 A
    assert card_strength(Card(rank=10, suit=S), t) > \
        card_strength(Card(rank=RANK_ACE, suit=S), t)
    # 主花色 J 仍是普通主牌，弱于 A
    assert card_strength(Card(rank=11, suit=S), t) < \
        card_strength(Card(rank=RANK_ACE, suit=S), t)


def test_no_trump_level_cards_share_one_tier():
    t = _t(None, level=5)
    tiers = {card_strength(Card(rank=5, suit=s), t) for s in (S, H, D, C)}
    assert tiers == {(TIER_OFF_LEVEL, 0)}


def test_side_cards_compare_by_rank_only_in_no_trump():
    """无主局：副牌只比点数，花色无关。"""
    t = _t(None, level=5)
    assert card_strength(Card(rank=RANK_ACE, suit=S), t) == \
        card_strength(Card(rank=RANK_ACE, suit=H), t)


# ---------- 分组 ----------

def test_group_of():
    t = _t(S, level=2)
    assert group_of(Card.big_joker(), t) == GROUP_TRUMP
    assert group_of(Card(rank=2, suit=H), t) == GROUP_TRUMP     # 副级也是主牌组
    assert group_of(Card(rank=RANK_ACE, suit=S), t) == GROUP_TRUMP
    assert group_of(Card(rank=RANK_ACE, suit=H), t) == H


# ---------- 比较 ----------

def test_compare_trump_beats_side_suit():
    t = _t(S, level=2)
    trump_low = (Card(rank=3, suit=S),)
    side_ace = (Card(rank=RANK_ACE, suit=H),)
    assert compare(trump_low, side_ace, t) == 1
    assert compare(side_ace, trump_low, t) == -1


def test_compare_equal_strength_is_zero():
    t = _t(S, level=2)
    a = (Card(rank=RANK_ACE, suit=H),)
    b = (Card(rank=RANK_ACE, suit=H),)
    assert compare(a, b, t) == 0


def test_compare_refuses_cross_side_suit():
    """不同副牌花色之间不可比较大小 —— 应由上层按跟牌规则处理。"""
    t = _t(S, level=2)
    with pytest.raises(ValueError):
        compare((Card(rank=RANK_ACE, suit=H),), (Card(rank=3, suit=D),), t)


def test_compare_pair_uses_pair_strength():
    t = _t(S, level=2)
    pair_ace = (Card(rank=RANK_ACE, suit=H), Card(rank=RANK_ACE, suit=H))
    pair_king = (Card(rank=RANK_KING, suit=H), Card(rank=RANK_KING, suit=H))
    assert compare(pair_ace, pair_king, t) == 1


def test_compare_tractor_uses_highest_pair():
    t = _t(S, level=2)
    low = (Card(rank=5, suit=H), Card(rank=5, suit=H),
           Card(rank=6, suit=H), Card(rank=6, suit=H))
    high = (Card(rank=RANK_KING, suit=H), Card(rank=RANK_KING, suit=H),
            Card(rank=RANK_ACE, suit=H), Card(rank=RANK_ACE, suit=H))
    assert compare(high, low, t) == 1


# ---------- 排序 ----------

def test_sort_desc_puts_trumps_first():
    t = _t(S, level=2)
    cards = [Card(rank=RANK_ACE, suit=H), Card(rank=3, suit=S),
             Card.big_joker(), Card(rank=2, suit=H), Card(rank=RANK_KING, suit=H)]
    ordered = sort_desc(cards, t)
    assert ordered[0] == Card.big_joker()
    # 主牌全部排在副牌之前
    groups = [group_of(c, t) for c in ordered]
    first_side = next(i for i, g in enumerate(groups) if g != GROUP_TRUMP)
    assert all(g == GROUP_TRUMP for g in groups[:first_side])
    assert all(g != GROUP_TRUMP for g in groups[first_side:])


# ---------- 主牌集合 ----------

def test_all_trumps_counts_for_two_decks():
    """四门花色的级牌都是主牌，不只是主花色的那一张。

    回归测试：早期实现只加了主花色的级牌，两副牌下少算 6 张主牌。
    """
    t = _t(S, level=2)
    trumps = all_trumps(t, decks=2)
    # 2 大王 + 2 小王 = 4
    # 四门级牌各 2 张 = 8
    # 主花色其余 12 个点数各 2 张 = 24
    assert len(trumps) == 4 + 8 + 24
    assert trumps.count(Card.big_joker()) == 2
    assert trumps.count(Card.small_joker()) == 2
    for suit in (S, H, D, C):
        assert trumps.count(Card(rank=2, suit=suit)) == 2, f"花色{suit}的级牌应计入"
    assert trumps.count(Card(rank=RANK_ACE, suit=S)) == 2
    assert trumps.count(Card(rank=RANK_ACE, suit=H)) == 0


def test_all_trumps_has_no_duplicates_beyond_deck_count():
    """每种牌的出现次数必须恰好等于副数。"""
    from collections import Counter

    t = _t(S, level=7)
    counts = Counter(all_trumps(t, decks=2))
    assert set(counts.values()) == {2}, "不应有重复计数或漏计"


def test_all_trumps_for_no_trump():
    t = _t(None, level=5)
    trumps = all_trumps(t, decks=2)
    # 2 大王 + 2 小王 + 四门级牌各 2 张 = 12，无主花色普通牌
    assert len(trumps) == 12


# ---------- 非法输入 ----------

def test_trump_info_rejects_illegal_values():
    with pytest.raises(ValueError):
        TrumpInfo(kind="suit", suit=None, level_rank=2)
    with pytest.raises(ValueError):
        TrumpInfo(kind="suit", suit=S, level_rank=1)
    with pytest.raises(ValueError):
        TrumpInfo(kind="suit", suit=S, level_rank=15)


def test_describe_is_readable():
    assert "♠" in _t(S, level=2).describe()
    assert "无主" in _t(None, level=2).describe()
    assert "2" in _t(S, level=2).describe()
