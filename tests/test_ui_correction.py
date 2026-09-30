"""纠正面板纯逻辑测试（设计文档 §9.1 / §14.1）。

面板的"业务判断"（一项显示成什么、点选算几张）都在这里，
Qt 只负责画出来 —— 所以这部分能纯单测，不用起 GUI。
"""

from shengji.cards import Card
from shengji.events.pending import PendingQueue
from shengji.events.types import PendingItem
from shengji.ui.correction import (
    CardSelection,
    current_row,
    pending_rows,
    row_for,
)

S, H = 0, 1


def _item(zone: str = "bottom", trick: int = 0, ts: float = 1.0,
          cards=(), reason: str = "low_confidence") -> PendingItem:
    return PendingItem(frame_ts=ts, reason=reason, zone=zone, seat=None,
                       proposed_cards=tuple(cards), confidence=0.2,
                       frame_agreement=0.2, evidence_path="", trick_index=trick)


# ---------- 呈现行 ----------

def test_pending_rows_keep_frame_ts_order():
    q = PendingQueue()
    q.add(_item(ts=3.0, zone="top"))
    q.add(_item(ts=1.0, zone="bottom"))
    q.add(_item(ts=2.0, zone="left"))

    rows = pending_rows(q)

    assert [r.index for r in rows] == [0, 1, 2]
    assert [r.zone for r in rows] == ["bottom", "left", "top"]


def test_current_row_is_the_earliest_one():
    q = PendingQueue()
    assert current_row(q) is None

    q.add(_item(ts=5.0, zone="right"))
    q.add(_item(ts=2.0, zone="top"))

    row = current_row(q)
    assert row is not None and row.zone == "top" and row.index == 0


def test_row_labels_and_headline():
    q = PendingQueue()
    q.add(_item(zone="top", trick=2, cards=(Card(rank=14, suit=S),)))

    row = current_row(q)

    assert row.zone_label == "对家"
    assert row.reason_label == "低置信"
    assert row.headline() == "第 3 墩 · 对家 · 低置信"
    assert row.proposed_text() == "识别：♠A"


def test_missed_play_has_no_proposal():
    """漏抓没有候选 —— 面板必须把"采纳识别结果"这条按钮禁掉。"""
    q = PendingQueue()
    q.add(_item(reason="missed_play", zone="left"))

    row = current_row(q)

    assert row.can_accept_proposed is False
    assert row.reason_label == "漏抓"
    assert row.proposed_text() == "无候选，请人工录入"


def test_row_for_matches_by_identity():
    """两个字段完全相同的待确认项，必须能分别定位（按身份而非相等性）。"""
    q = PendingQueue()
    a = q.add(_item(ts=1.0))
    b = q.add(_item(ts=1.0))

    assert a is not b and a == b
    assert row_for(q, a).index == 0
    assert row_for(q, b).index == 1
    assert row_for(q, _item(ts=9.0)) is None


# ---------- 点选 ----------

def test_toggle_cycles_through_zero_one_two():
    sel = CardSelection(decks=2)
    card = Card(rank=5, suit=H)

    assert sel.toggle(card) == 1
    assert sel.toggle(card) == 2
    assert sel.toggle(card) == 0        # 两副牌最多 2 张，再点回到 0
    assert sel.is_empty


def test_toggle_respects_deck_count():
    """三副牌时同一张可以点到 3 张 —— 上限跟着牌堆走，不是写死的 2。"""
    sel = CardSelection(decks=3)
    card = Card(rank=5, suit=H)

    assert [sel.toggle(card) for _ in range(4)] == [1, 2, 3, 0]


def test_add_clamps_to_deck_count():
    sel = CardSelection(decks=2)
    card = Card(rank=7, suit=S)

    assert sel.add(card, 5) == 2
    assert sel.count(card) == 2
    assert sel.add(card, -1) == 0
    assert sel.is_empty


def test_cards_expands_duplicates_and_sorts():
    """同一张选 2 张要展开成 2 张；顺序固定，便于复现。"""
    sel = CardSelection(decks=2)
    sel.add(Card(rank=10, suit=H))
    sel.add(Card(rank=3, suit=S), 2)

    assert sel.cards() == [Card(rank=3, suit=S), Card(rank=3, suit=S),
                           Card(rank=10, suit=H)]
    assert sel.total == 3


def test_set_cards_replaces_selection():
    """「采纳识别结果」用一组牌整体替换当前选择。"""
    sel = CardSelection(decks=2)
    sel.add(Card(rank=9, suit=S))

    sel.set_cards([Card(rank=5, suit=H), Card(rank=5, suit=H)])

    assert sel.count(Card(rank=9, suit=S)) == 0
    assert sel.count(Card(rank=5, suit=H)) == 2
    assert sel.cards() == [Card(rank=5, suit=H), Card(rank=5, suit=H)]


def test_set_cards_clamps_over_deck_count():
    """识别结果给出超过牌堆张数的牌时钳制上限，不产生不可能的牌。"""
    sel = CardSelection(decks=2)
    sel.set_cards([Card(rank=5, suit=H)] * 3)

    assert sel.count(Card(rank=5, suit=H)) == 2


def test_text_reports_empty_and_selected():
    sel = CardSelection(decks=2)
    assert sel.text() == "未选牌"

    sel.add(Card(rank=14, suit=S))
    assert sel.text() == "已选：♠A"

    sel.toggle(Card(rank=14, suit=S))       # 变成 2 张
    assert sel.text() == "已选：♠A ♠A"


def test_jokers_are_selectable():
    sel = CardSelection(decks=2)
    sel.add(Card.big_joker())
    assert sel.cards() == [Card.big_joker()]
    assert sel.text() == "已选：大王"
