import pytest

from shengji.cards import (
    JOKER_BIG,
    JOKER_SMALL,
    RANK_ACE,
    RANK_KING,
    SUIT_HEART,
    SUIT_SPADE,
    Card,
    parse_code,
)


def test_rank_ace_is_highest():
    """A 必须大于 K（设计文档 §7.6）。"""
    assert RANK_ACE > RANK_KING
    assert Card(rank=RANK_ACE, suit=SUIT_SPADE) > Card(rank=RANK_KING, suit=SUIT_SPADE)


def test_card_label_and_code():
    assert Card(rank=RANK_ACE, suit=SUIT_SPADE).label() == "♠A"
    assert Card(rank=RANK_ACE, suit=SUIT_SPADE).code() == "SA"
    assert Card(rank=10, suit=SUIT_HEART).code() == "H10"
    assert Card.small_joker().label() == "小王"
    assert Card.big_joker().code() == "joker_big"


def test_code_round_trip_for_all_normal_cards():
    for rank in range(2, 15):
        for suit in range(4):
            c = Card(rank=rank, suit=suit)
            assert parse_code(c.code()) == c


def test_code_round_trip_jokers():
    assert parse_code("joker_small") == Card.small_joker()
    assert parse_code("joker_big") == Card.big_joker()


def test_parse_code_rejects_garbage():
    for bad in ("", "X", "S", "SX", "S11", "Z2", "joker"):
        assert parse_code(bad) is None, bad


def test_card_rejects_illegal_values():
    with pytest.raises(ValueError):
        Card(rank=1, suit=0)
    with pytest.raises(ValueError):
        Card(rank=15, suit=0)
    with pytest.raises(ValueError):
        Card(rank=5, suit=4)
    with pytest.raises(ValueError):
        Card(rank=5, suit=0, joker=9)


def test_joker_flag():
    assert Card.small_joker().is_joker
    assert Card.big_joker().is_joker
    assert not Card(rank=5, suit=0).is_joker
    assert Card(rank=5, suit=0).joker == 0


def test_joker_values():
    assert Card.small_joker().joker == JOKER_SMALL
    assert Card.big_joker().joker == JOKER_BIG


def test_cards_are_hashable_and_orderable():
    """牌要能进 set/frozenset（记账模型依赖），也要能排序。"""
    s = {Card(rank=5, suit=0), Card(rank=5, suit=0), Card(rank=6, suit=1)}
    assert len(s) == 2
    ordered = sorted([Card(rank=6, suit=0), Card(rank=5, suit=0)])
    assert ordered[0].rank == 5
