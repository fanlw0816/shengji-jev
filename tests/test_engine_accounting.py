"""记账模型测试。

重点覆盖设计评审抓出的三类重复扣减缺陷，每类都有对应的回归测试。
"""

import pytest

from shengji.cards import Card
from shengji.engine.accounting import (
    VARIANT_RULES,
    AccountingError,
    KnownSet,
    UnseenPool,
    deck_composition,
    get_variant_rule,
)

S, H, D, C = 0, 1, 2, 3
RULE_4P2D = VARIANT_RULES[(4, 2)]


def _cards(n: int, start_rank: int = 2, suit: int = H) -> list[Card]:
    """生成 n 张互不相同的牌（跨花色），仅用于凑数。"""
    out: list[Card] = []
    rank, s = start_rank, suit
    while len(out) < n:
        out.append(Card(rank=rank, suit=s))
        rank += 1
        if rank > 14:
            rank = 2
            s = (s + 1) % 4
    return out


# ---------- 牌堆构成 ----------

def test_deck_composition_two_decks():
    d = deck_composition(2)
    assert sum(d.values()) == 108
    assert d[Card.big_joker()] == 2
    assert d[Card(rank=14, suit=S)] == 2
    assert len(d) == 54


def test_deck_composition_three_decks():
    d = deck_composition(3)
    assert sum(d.values()) == 162


# ---------- 变体规则表 ----------

def test_documented_variant_is_accepted():
    rule = get_variant_rule(4, 2)
    assert rule.hand_size == 25 and rule.bottom_size == 8
    assert 25 * 4 + 8 == 108


def test_unverified_variant_is_refused():
    """三副牌规则数据未实测，必须拒绝启动而不是用猜测值。"""
    with pytest.raises(AccountingError) as ei:
        get_variant_rule(6, 3)
    assert "尚未实测确认" in str(ei.value)
    assert VARIANT_RULES[(6, 3)].source == "UNVERIFIED"


def test_unsupported_variant_is_refused():
    with pytest.raises(AccountingError):
        get_variant_rule(5, 2)


# ---------- 已知集合 ----------

def test_declarer_known_set_is_33():
    hand = _cards(25)
    buried = _cards(8, start_rank=2, suit=C)
    # 保证不重叠
    assert not set(hand) & set(buried)
    k = KnownSet.for_declarer(hand, buried, RULE_4P2D)
    assert k.size == 33
    assert k.role == "declarer"
    assert k.bottom_unknown_capacity == 0


def test_declarer_known_set_rejects_wrong_sizes():
    with pytest.raises(AccountingError):
        KnownSet.for_declarer(_cards(24), _cards(8), RULE_4P2D)
    with pytest.raises(AccountingError):
        KnownSet.for_declarer(_cards(25), _cards(7), RULE_4P2D)


def test_declarer_known_set_allows_same_face_in_hand_and_bottom():
    """回归（实施中修正）：多副牌下同面值出现在手牌与底牌里是**合法**的。

    两副牌有 2 张 ♠A，庄家手上 1 张、底牌埋另 1 张完全正常。
    初版按面值断言「H ∩ D == ∅」会对这种合法输入误报 —— 真实发牌就会命中。
    """
    hand = _cards(25)
    ace = Card(rank=14, suit=S)
    hand[0] = ace
    buried = _cards(8, start_rank=2, suit=C)
    assert ace not in buried
    buried[0] = ace                      # 另一张 ♠A 埋进底牌
    k = KnownSet.for_declarer(hand, buried, RULE_4P2D)
    assert k.cards[ace] == 2             # 已知两张，正是两副牌的全部
    assert k.size == 33


def test_declarer_known_set_rejects_face_exceeding_deck():
    """同一面值合计超过牌堆构成 —— 这才是该拦的（识别重复或快照混用）。"""
    hand = _cards(25)
    ace = Card(rank=14, suit=S)
    hand[0], hand[1] = ace, ace          # 手上两张 ♠A（已用完全部）
    buried = _cards(8, start_rank=2, suit=C)
    buried[0] = ace                      # 底牌又来一张 —— 3 张，牌堆只有 2 张
    with pytest.raises(AccountingError) as ei:
        KnownSet.for_declarer(hand, buried, RULE_4P2D)
    assert "超出牌堆构成" in str(ei.value)


def test_mixed_snapshots_surface_at_play_time():
    """快照混用（扣底后的 H + 扣底前的 P）：构造时可能合法，运行时必被逮到。

    混用会把 P − D 那几张重复计入已知，于是未见池里这几张被错扣成 0；
    真实存在的那一张一旦被别人打出，就触发不变式 I1。
    这是「构造期断言」降级为「运行期不变式」后的兜底路径。
    """
    hand = _cards(25)                              # 13 ♥ + ♦2..♦13
    pre_burial = _cards(8, start_rank=9, suit=D)   # ♦9..♦A + ♣2 ♣3
    ks = KnownSet.for_declarer(hand, pre_burial, RULE_4P2D)   # 不再报错
    pool = UnseenPool(RULE_4P2D, ks, own_seat=0)

    dup = Card(rank=9, suit=D)
    assert ks.cards[dup] == 2, "该面值被重复计入已知"
    assert pool.count(dup) == 0, "未见池被错扣成 0（真实牌局里还剩 1 张）"

    with pytest.raises(AccountingError) as ei:
        pool.on_play(1, [dup])
    assert "未见池中已无此牌" in str(ei.value)


def test_defender_known_set_is_25_with_bottom_unknown_8():
    k = KnownSet.for_defender(_cards(25), RULE_4P2D)
    assert k.size == 25
    assert k.role == "defender"
    assert k.bottom_unknown_capacity == 8


def test_defender_known_set_rejects_wrong_size():
    with pytest.raises(AccountingError):
        KnownSet.for_defender(_cards(24), RULE_4P2D)


# ---------- 未见牌池：初始状态 ----------

def test_defender_pool_initial_total():
    k = KnownSet.for_defender(_cards(25), RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    # 108 − 25 = 83 = 其他三家 75 张 + 未知底牌 8 张
    assert pool.total() == 83
    assert pool.others_total() == 75
    assert pool.bottom_unknown() == 8


def test_declarer_pool_initial_total():
    hand = _cards(25)
    buried = _cards(8, start_rank=2, suit=C)
    assert not set(hand) & set(buried)
    k = KnownSet.for_declarer(hand, buried, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    # 108 − 33 = 75 = 其他三家 75 张 + 未知底牌 0 张
    assert pool.total() == 75
    assert pool.others_total() == 75
    assert pool.bottom_unknown() == 0


def test_pool_rejects_known_cards_exceeding_deck():
    """已知牌中某种牌的数量不得超过牌堆构成（两副牌只有 2 张大王）。

    注意检查点在 UnseenPool 构造时，因为它才持有牌堆构成。
    """
    many = [Card.big_joker()] * 3
    ks = KnownSet.for_defender(many + _cards(22), RULE_4P2D)
    with pytest.raises(AccountingError) as ei:
        UnseenPool(RULE_4P2D, ks, own_seat=0)
    assert "大王" in str(ei.value)


# ---------- 未见牌池：自己出牌不扣减（缺陷二）----------

def test_own_play_does_not_decrement_unseen():
    """回归：自己的牌本就在 known 里，再扣一次会让 unseen 变负。

    注意语义：未见池按**面值**计数。两副牌下某面值有 2 张，
    1 张在自己手上时，未见池里仍有 1 张（另一副的那张）。
    所以正确断言是「自己出牌前后该面值计数不变」，而不是归零。
    """
    hand = _cards(25)
    k = KnownSet.for_defender(hand, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)

    before_total = pool.total()
    face = hand[0]
    before_face = pool.count(face)
    assert before_face == 1, "两副牌下自己持 1 张，未见池应剩 1 张"

    pool.on_play(0, hand[:2])          # 自己打出两张

    assert pool.total() == before_total, "自己出牌不应改变未见池总数"
    assert pool.count(face) == before_face, "自己出的牌不从未见池扣减"
    assert pool.seat_holds()[0] == 23


def test_opponent_play_decrements_unseen():
    hand = _cards(25)
    k = KnownSet.for_defender(hand, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    before = pool.total()

    target = next(c for c in pool.as_counter() if not c.joker)
    pool.on_play(1, [target])

    assert pool.total() == before - 1
    assert pool.seat_holds()[1] == 24


def test_repeated_opponent_play_beyond_deck_raises():
    """同一张牌被识别成出了 3 次（两副牌只有 2 张）—— 必须告警。"""
    hand = _cards(25)
    k = KnownSet.for_defender(hand, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    target = next(c for c in pool.as_counter() if not c.joker)
    pool.on_play(1, [target])
    pool.on_play(2, [target])
    with pytest.raises(AccountingError) as ei:
        pool.on_play(3, [target])
    assert "未见池中已无此牌" in str(ei.value)


def test_own_play_of_unknown_card_raises():
    hand = _cards(25)
    k = KnownSet.for_defender(hand, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    outside = next(c for c in deck_composition(2) if c not in set(hand))
    with pytest.raises(AccountingError) as ei:
        pool.on_play(0, [outside])
    assert "已知集合之外" in str(ei.value)


def test_play_more_than_holdings_raises():
    hand = _cards(25)
    k = KnownSet.for_defender(hand, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    pool.on_play(1, [next(iter(pool.as_counter()))] * 1)
    with pytest.raises(AccountingError):
        pool.on_play(1, _cards(30))


# ---------- 总数守恒：打完全部牌 ----------

def test_full_deal_conserves_totals():
    """把牌全部打完，未见池应归零，且过程中不变式始终成立。"""
    hand = _cards(25)
    k = KnownSet.for_defender(hand, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)

    others = list(pool.as_counter().elements())
    # 其他三家各 25 张
    for seat in (1, 2, 3):
        chunk = others[:25]
        others = others[25:]
        pool.on_play(seat, chunk)
        pool.check()
    # 自己 25 张
    pool.on_play(0, hand)
    pool.check()

    assert pool.total() == 8, "剩下的应是 8 张未知底牌"
    assert pool.bottom_unknown() == 8

    # 亮底
    remaining = list(pool.as_counter().elements())
    pool.on_bottom_revealed(remaining)
    pool.check()
    assert pool.total() == 0
    assert pool.bottom_unknown() == 0


# ---------- 未知底牌堆（缺陷三）----------

def test_declarer_cannot_reveal_bottom_again():
    hand = _cards(25)
    buried = _cards(8, start_rank=2, suit=C)
    assert not set(hand) & set(buried)
    k = KnownSet.for_declarer(hand, buried, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    with pytest.raises(AccountingError):
        pool.on_bottom_revealed(_cards(8, start_rank=3, suit=D))


def test_revealing_more_than_bottom_capacity_raises():
    k = KnownSet.for_defender(_cards(25), RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    with pytest.raises(AccountingError):
        pool.on_bottom_revealed(_cards(9, start_rank=2, suit=C))


def test_bottom_unknown_is_reserved_so_invariants_hold():
    """底牌槽位必须参与总账，否则 I2 不成立。

    这是缺陷三的核心：非庄家时 8 张底牌不属于任何座位，
    若不显式保留容量，总账会少 8 张。
    """
    k = KnownSet.for_defender(_cards(25), RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    pool.check()
    assert pool.total() == pool.others_total() + pool.bottom_unknown()


def test_bottom_revealed_shrinks_pool_and_capacity():
    k = KnownSet.for_defender(_cards(25), RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    before = pool.total()
    bottom = list(pool.as_counter().elements())[:8]
    pool.on_bottom_revealed(bottom)
    assert pool.total() == before - 8
    assert pool.bottom_unknown() == 0
    pool.check()


# ---------- 座位与非法输入 ----------

def test_illegal_seat_raises():
    k = KnownSet.for_defender(_cards(25), RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=0)
    with pytest.raises(AccountingError):
        pool.on_play(9, [Card(rank=5, suit=H)])


def test_own_known_reflects_known_set():
    hand = _cards(25)
    k = KnownSet.for_defender(hand, RULE_4P2D)
    pool = UnseenPool(RULE_4P2D, k, own_seat=2)
    assert pool.own_known() == k.cards
