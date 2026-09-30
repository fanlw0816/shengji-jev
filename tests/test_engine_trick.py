import pytest

from shengji.cards import RANK_ACE, RANK_KING, Card
from shengji.engine.trick import (
    PlayedCards,
    Structure,
    structure_matches,
    structure_of,
    trick_points,
    unplayed_of_suit,
    winning_seat,
)
from shengji.engine.trump import TrumpInfo

S, H, D, C = 0, 1, 2, 3


def _t(suit=S, level=2) -> TrumpInfo:
    return TrumpInfo(kind="suit", suit=suit, level_rank=level)


def _c(rank, suit=H) -> Card:
    return Card(rank=rank, suit=suit)


# ---------- 结构判定 ----------

def test_structure_single():
    assert structure_of((_c(5),), _t()) is Structure.SINGLE


def test_structure_pair():
    assert structure_of((_c(5), _c(5)), _t()) is Structure.PAIR


def test_structure_two_different_cards_is_mixed():
    assert structure_of((_c(5), _c(6)), _t()) is Structure.MIXED


def test_structure_pair_of_jokers():
    assert structure_of((Card.big_joker(), Card.big_joker()), _t()) is Structure.PAIR


def test_structure_mixed_jokers_is_not_pair():
    assert structure_of((Card.big_joker(), Card.small_joker()), _t()) is Structure.MIXED


def test_structure_tractor_of_two_consecutive_pairs():
    assert structure_of((_c(5), _c(5), _c(6), _c(6)), _t()) is Structure.TRACTOR


def test_structure_tractor_requires_consecutive():
    assert structure_of((_c(5), _c(5), _c(7), _c(7)), _t()) is Structure.MIXED


def test_structure_tractor_requires_same_suit():
    assert structure_of((_c(5, H), _c(5, H), _c(6, D), _c(6, D)), _t()) is Structure.MIXED


def test_structure_three_consecutive_pairs():
    cards = (_c(5), _c(5), _c(6), _c(6), _c(7), _c(7))
    assert structure_of(cards, _t()) is Structure.TRACTOR


def test_structure_joker_pair_is_not_tractor():
    cards = (Card.big_joker(), Card.big_joker(),
             Card.small_joker(), Card.small_joker())
    assert structure_of(cards, _t()) is Structure.MIXED


def test_structure_empty_raises():
    with pytest.raises(ValueError):
        structure_of((), _t())


# ---------- 连对：级牌语义与跨组（2026-09-30 修正）----------
#
# 修前 `_is_tractor` 只比裸点数差、且只要求花色相同，于是下面这些情形会被
# **自信地**判成拖拉机并算出赢家（都不走人工确认）。修后一律 MIXED。


def _pair(rank: int, suit: int) -> tuple[Card, Card]:
    return (Card(rank=rank, suit=suit), Card(rank=rank, suit=suit))


def test_tractor_cross_group_is_not_tractor():
    """打 10 主 ♠：♥10 是副级（主牌组），♥9 是 ♥ 副牌组 —— 跨组不可比。

    修前被判为 TRACTOR，而且 `cards[0]` 恰是 ♥10 使整手被当成**主牌**连对，
    能与真正的 ♥ 组连对互压，属于最危险的一类自信算错。
    """
    trump = _t(suit=S, level=10)
    assert structure_of(_pair(10, H) + _pair(9, H), trump) is Structure.MIXED
    assert structure_of(_pair(9, H) + _pair(10, H), trump) is Structure.MIXED
    assert structure_of(_pair(11, H) + _pair(10, H), trump) is Structure.MIXED


def test_tractor_level_card_does_not_form_run():
    """级牌自成一层：与同门其余点数都不相邻（打 10 主 ♠）。"""
    trump = _t(suit=S, level=10)
    assert structure_of(_pair(10, S) + _pair(11, S), trump) is Structure.MIXED
    assert structure_of(_pair(10, S) + _pair(9, S), trump) is Structure.MIXED


def test_tractor_side_suit_level_card_belongs_to_trump_group():
    """副牌花色的级牌是主牌，故 ♦J♦J + ♦10♦10 跨组；不带级牌的 ♦ 对仍是连对。"""
    trump = _t(suit=S, level=10)
    assert structure_of(_pair(11, D) + _pair(10, D), trump) is Structure.MIXED
    assert structure_of(_pair(11, D) + _pair(12, D), trump) is Structure.TRACTOR


def test_tractor_skips_level_in_trump_suit():
    """主花色挖掉级牌后其余点数连续 —— ♠J♠J + ♠9♠9 是连对（打 10 主 ♠）。

    ⚠️ 「挖掉后仍算连续」这一取值**未经实测**（todo.md 未决项）。
    改动只需改 `trump.sequence_index()` 一处 —— 墩赢家与合法枚举共用它。
    """
    trump = _t(suit=S, level=10)
    assert structure_of(_pair(11, S) + _pair(9, S), trump) is Structure.TRACTOR
    assert structure_of(_pair(9, S) + _pair(11, S), trump) is Structure.TRACTOR


@pytest.mark.parametrize("level", [3, 10, 13])
def test_tractor_skips_level_for_various_levels(level):
    """跳级连续对不同级数都成立（主花色 L-1 与 L+1 相邻）。"""
    trump = _t(suit=S, level=level)
    assert structure_of(_pair(level + 1, S) + _pair(level - 1, S),
                        trump) is Structure.TRACTOR


@pytest.mark.parametrize("level", [2, 14])
def test_tractor_level_at_sequence_edge(level):
    """级 2 / 级 A 时序列被切在端点，其余点数仍连续，级牌自身不参与。"""
    trump = _t(suit=S, level=level)
    low, high = (3, 4) if level == 2 else (12, 13)
    assert structure_of(_pair(high, S) + _pair(low, S), trump) is Structure.TRACTOR
    assert structure_of(_pair(level, S) + _pair(high, S), trump) is Structure.MIXED


def test_tractor_requires_distinct_faces():
    """两副牌的 4 张同点牌拆成两对，位置相同（差 0），不是连对。"""
    trump = _t(suit=S, level=10)
    four = _pair(5, H) + _pair(5, H)
    assert structure_of(four, trump) is Structure.MIXED


def test_tractor_no_trump_deal_all_level_cards_are_trumps():
    """无主局：四门级牌都是主牌（副级），不参与连对；普通副牌对子照常。"""
    trump = TrumpInfo(kind="no_trump", suit=None, level_rank=10)
    assert structure_of(_pair(10, H) + _pair(9, H), trump) is Structure.MIXED
    assert structure_of(_pair(11, H) + _pair(12, H), trump) is Structure.TRACTOR


def test_tractor_versus_side_tractor_weights_trumps():
    """主花色跳级连对压得住副牌连对。"""
    trump = _t(suit=S, level=10)
    plays = [PlayedCards(0, _pair(11, D) + _pair(12, D)),      # 副牌 ♦J♦J+♦Q♦Q
             PlayedCards(1, _pair(11, S) + _pair(9, S))]       # 主花色 ♠J♠J+♠9♠9
    assert winning_seat(plays, trump).winner_seat == 1


# ---------- 结构匹配（供墩赢家与 legal.py 共用）----------

def test_structure_matches_same_structure():
    t = _t()
    assert structure_matches((_c(5),), Structure.SINGLE, t)
    assert structure_matches((_c(5), _c(5)), Structure.PAIR, t)
    tractor = (_c(5), _c(5), _c(6), _c(6))
    assert structure_matches(tractor, Structure.TRACTOR, t)


def test_structure_matches_rejects_other_structure():
    t = _t()
    assert not structure_matches((_c(5), _c(6)), Structure.PAIR, t)
    assert not structure_matches((_c(5), _c(5)), Structure.TRACTOR, t)
    assert not structure_matches((_c(5), _c(5)), Structure.SINGLE, t)


def test_structure_matches_mixed_lead_accepts_any_nonempty():
    """甩牌的「型」由张数决定 —— 各家按张数跟，不按结构跟。"""
    t = _t()
    assert structure_matches((_c(5), _c(6)), Structure.MIXED, t)
    assert structure_matches((_c(5), _c(5)), Structure.MIXED, t)
    assert not structure_matches((), Structure.MIXED, t)


# ---------- 单张赢家 ----------

def test_single_highest_of_lead_suit_wins():
    t = _t(S)
    plays = [PlayedCards(0, (_c(10, H),)),
             PlayedCards(1, (_c(RANK_ACE, H),)),
             PlayedCards(2, (_c(RANK_KING, H),)),
             PlayedCards(3, (_c(3, H),))]
    assert winning_seat(plays, t).winner_seat == 1


def test_single_trump_beats_lead_suit():
    t = _t(S)
    plays = [PlayedCards(0, (_c(RANK_ACE, H),)),
             PlayedCards(1, (Card(rank=3, suit=S),)),
             PlayedCards(2, (_c(RANK_KING, H),)),
             PlayedCards(3, (_c(4, H),))]
    assert winning_seat(plays, t).winner_seat == 1


def test_single_highest_trump_wins():
    t = _t(S)
    plays = [PlayedCards(0, (_c(RANK_ACE, H),)),
             PlayedCards(1, (Card(rank=3, suit=S),)),
             PlayedCards(2, (Card.big_joker(),)),
             PlayedCards(3, (Card(rank=2, suit=D),))]   # 副级也是主牌
    assert winning_seat(plays, t).winner_seat == 2


def test_single_off_suit_cannot_win():
    """既非领出花色、也非主牌的牌不能赢。"""
    t = _t(S)
    plays = [PlayedCards(0, (_c(5, H),)),
             PlayedCards(1, (_c(3, D),)),
             PlayedCards(2, (_c(4, H),)),
             PlayedCards(3, (_c(6, H),))]
    assert winning_seat(plays, t).winner_seat == 3


def test_single_tie_goes_to_first_played():
    """强弱相等时先出者赢。"""
    t = _t(S)
    plays = [PlayedCards(0, (_c(RANK_ACE, H),)),
             PlayedCards(1, (_c(RANK_ACE, H),))]
    assert winning_seat(plays, t).winner_seat == 0


def test_single_trump_lead_only_trump_can_win():
    t = _t(S)
    plays = [PlayedCards(0, (Card(rank=3, suit=S),)),
             PlayedCards(1, (Card(rank=RANK_ACE, suit=S),)),
             PlayedCards(2, (_c(RANK_ACE, H),))]      # 副牌压不过主牌
    assert winning_seat(plays, t).winner_seat == 1


def test_no_one_can_beat_lead_when_all_off_suit():
    t = _t(S)
    plays = [PlayedCards(0, (_c(5, H),)),
             PlayedCards(1, (_c(9, D),)),
             PlayedCards(2, (_c(9, C),))]
    assert winning_seat(plays, t).winner_seat == 0


# ---------- 对子 ----------

def test_pair_beats_single_when_lead_is_pair():
    t = _t(S)
    plays = [PlayedCards(0, (_c(5, H), _c(5, H))),
             PlayedCards(1, (_c(RANK_ACE, H),)),          # 单张结构不匹配
             PlayedCards(2, (_c(6, H), _c(6, H)))]
    outcome = winning_seat(plays, t)
    assert outcome.winner_seat == 2


def test_pair_of_trumps_beats_pair_of_lead_suit():
    t = _t(S)
    plays = [PlayedCards(0, (_c(RANK_ACE, H), _c(RANK_ACE, H))),
             PlayedCards(1, (Card(rank=3, suit=S), Card(rank=3, suit=S)))]
    assert winning_seat(plays, t).winner_seat == 1


def test_higher_pair_wins_between_pairs():
    t = _t(S)
    plays = [PlayedCards(0, (_c(9, H), _c(9, H))),
             PlayedCards(1, (_c(RANK_ACE, H), _c(RANK_ACE, H)))]
    assert winning_seat(plays, t).winner_seat == 1


# ---------- 拖拉机 ----------

def test_tractor_higher_pair_wins():
    t = _t(S)
    plays = [PlayedCards(0, (_c(5, H), _c(5, H), _c(6, H), _c(6, H))),
             PlayedCards(1, (_c(RANK_KING, H), _c(RANK_KING, H),
                             _c(RANK_ACE, H), _c(RANK_ACE, H)))]
    assert winning_seat(plays, t).winner_seat == 1


def test_tractor_beats_two_unrelated_cards_not_structure_matched():
    t = _t(S)
    plays = [PlayedCards(0, (_c(5, H), _c(5, H), _c(6, H), _c(6, H))),
             PlayedCards(1, (_c(RANK_ACE, H), _c(RANK_ACE, H),
                             _c(3, H), _c(3, H)))]          # 不是连对
    assert winning_seat(plays, t).winner_seat == 0


# ---------- 甩牌：明确标记为不可信 ----------

def test_throw_is_marked_unconfident():
    """甩牌赢家判定未完整实现，必须显式标记而不是猜一个结果。"""
    t = _t(S)
    plays = [PlayedCards(0, (_c(RANK_ACE, H), _c(RANK_KING, H), _c(9, H))),
             PlayedCards(1, (_c(3, H), _c(4, H), _c(5, H)))]
    outcome = winning_seat(plays, t)
    assert not outcome.confident
    assert "甩牌" in outcome.note


def test_normal_trick_is_confident():
    t = _t(S)
    plays = [PlayedCards(0, (_c(5, H),)), PlayedCards(1, (_c(6, H),))]
    assert winning_seat(plays, t).confident


# ---------- 分数 ----------

def test_trick_points_counts_five_ten_king():
    plays = [PlayedCards(0, (_c(5), _c(10))),
             PlayedCards(1, (_c(RANK_KING), _c(3)))]
    assert trick_points(plays) == 5 + 10 + 10


def test_trick_points_ignores_jokers():
    plays = [PlayedCards(0, (Card.big_joker(), Card.small_joker()))]
    assert trick_points(plays) == 0


def test_trick_points_zero_for_no_point_cards():
    plays = [PlayedCards(0, (_c(4), _c(6), _c(7)))]
    assert trick_points(plays) == 0


# ---------- 跟牌能力辅助 ----------

def test_unplayed_of_suit_excludes_trumps():
    """筛「某花色的非主牌」时必须排除级牌与主花色牌。"""
    t = _t(S, level=2)
    cards = [Card(rank=RANK_ACE, suit=H),
             Card(rank=2, suit=H),          # 副级，算主牌
             Card(rank=5, suit=H),
             Card(rank=RANK_ACE, suit=S),   # 主花色
             Card.big_joker()]
    got = unplayed_of_suit(cards, H, t)
    assert set(got) == {Card(rank=RANK_ACE, suit=H), Card(rank=5, suit=H)}


# ---------- 非法输入 ----------

def test_winning_seat_rejects_empty():
    with pytest.raises(ValueError):
        winning_seat([], _t(S))
