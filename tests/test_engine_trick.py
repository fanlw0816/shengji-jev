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
    assert structure_of((_c(5),)) is Structure.SINGLE


def test_structure_pair():
    assert structure_of((_c(5), _c(5))) is Structure.PAIR


def test_structure_two_different_cards_is_mixed():
    assert structure_of((_c(5), _c(6))) is Structure.MIXED


def test_structure_pair_of_jokers():
    assert structure_of((Card.big_joker(), Card.big_joker())) is Structure.PAIR


def test_structure_mixed_jokers_is_not_pair():
    assert structure_of((Card.big_joker(), Card.small_joker())) is Structure.MIXED


def test_structure_tractor_of_two_consecutive_pairs():
    assert structure_of((_c(5), _c(5), _c(6), _c(6))) is Structure.TRACTOR


def test_structure_tractor_requires_consecutive():
    assert structure_of((_c(5), _c(5), _c(7), _c(7))) is Structure.MIXED


def test_structure_tractor_requires_same_suit():
    assert structure_of((_c(5, H), _c(5, H), _c(6, D), _c(6, D))) is Structure.MIXED


def test_structure_three_consecutive_pairs():
    cards = (_c(5), _c(5), _c(6), _c(6), _c(7), _c(7))
    assert structure_of(cards) is Structure.TRACTOR


def test_structure_joker_pair_is_not_tractor():
    cards = (Card.big_joker(), Card.big_joker(),
             Card.small_joker(), Card.small_joker())
    assert structure_of(cards) is Structure.MIXED


def test_structure_empty_raises():
    with pytest.raises(ValueError):
        structure_of(())


# ---------- 结构匹配（供墩赢家与 legal.py 共用）----------

def test_structure_matches_same_structure():
    assert structure_matches((_c(5),), Structure.SINGLE)
    assert structure_matches((_c(5), _c(5)), Structure.PAIR)
    tractor = (_c(5), _c(5), _c(6), _c(6))
    assert structure_matches(tractor, Structure.TRACTOR)


def test_structure_matches_rejects_other_structure():
    assert not structure_matches((_c(5), _c(6)), Structure.PAIR)
    assert not structure_matches((_c(5), _c(5)), Structure.TRACTOR)
    assert not structure_matches((_c(5), _c(5)), Structure.SINGLE)


def test_structure_matches_mixed_lead_accepts_any_nonempty():
    """甩牌的「型」由张数决定 —— 各家按张数跟，不按结构跟。"""
    assert structure_matches((_c(5), _c(6)), Structure.MIXED)
    assert structure_matches((_c(5), _c(5)), Structure.MIXED)
    assert not structure_matches((), Structure.MIXED)


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
