import json

import numpy as np

from shengji.cards import Card
from shengji.events.pending import PendingQueue
from shengji.events.types import PendingItem, PlayEvent


def _play(zone: str = "top", ts: float = 1.0, trick: int = 0,
          cards=(Card(rank=5, suit=0),), confidence: float = 0.9) -> PlayEvent:
    return PlayEvent(zone=zone, cards=tuple(cards), count=len(cards),
                     confidence=confidence, frame_agreement=1.0,
                     trick_index=trick, frame_ts=ts)


def _item(ts: float, zone: str = "top") -> PendingItem:
    return PendingItem(frame_ts=ts, reason="low_confidence", zone=zone, seat=None,
                       proposed_cards=(), confidence=0.1, frame_agreement=1.0,
                       evidence_path="", trick_index=0)


def test_queue_keeps_items_sorted_by_ts():
    q = PendingQueue()
    q.add(_item(3.0))
    q.add(_item(1.0))
    q.add(_item(2.0))
    assert [i.frame_ts for i in q.items] == [1.0, 2.0, 3.0]


def test_next_returns_earliest_without_removing():
    q = PendingQueue()
    q.add(_item(2.0))
    q.add(_item(1.0))
    assert q.next().frame_ts == 1.0
    assert len(q) == 2


def test_empty_queue():
    q = PendingQueue()
    assert q.empty
    assert q.next() is None
    assert q.items == ()


def test_resolve_removes_item():
    q = PendingQueue()
    a = q.add(_item(1.0))
    q.add(_item(2.0))
    assert q.resolve(a) is True
    assert [i.frame_ts for i in q.items] == [2.0]
    assert q.resolve(a) is False


def test_resolve_index():
    q = PendingQueue()
    q.add(_item(1.0))
    q.add(_item(2.0))
    got = q.resolve_index(1)
    assert got.frame_ts == 2.0
    assert len(q) == 1
    assert q.resolve_index(99) is None


def test_add_low_confidence_uses_event_fields(tmp_path):
    q = PendingQueue(tmp_path)
    q.add_low_confidence(_play(zone="right", ts=5.0, trick=2), seat=3,
                         evidence=np.zeros((10, 10, 3), np.uint8))
    item = q.next()
    assert item.reason == "low_confidence"
    assert item.zone == "right"
    assert item.seat == 3
    assert item.trick_index == 2
    assert item.proposed_cards == (Card(rank=5, suit=0),)
    assert item.evidence_path.endswith(".png")
    assert (tmp_path / item.evidence_path).exists()


def test_add_low_confidence_without_evidence_still_enqueues(tmp_path):
    """缺图也不能丢事件 —— 这是"绝不静默丢牌"的底线。"""
    q = PendingQueue(tmp_path)
    q.add_low_confidence(_play(), seat=1, evidence=None)
    assert len(q) == 1
    assert q.next().evidence_path == ""


def test_add_missed_play(tmp_path):
    q = PendingQueue(tmp_path)
    q.add_missed_play("left", 3.5, 1, seat=2,
                      evidence=np.zeros((10, 10, 3), np.uint8))
    item = q.next()
    assert item.reason == "missed_play"
    assert item.zone == "left"
    assert item.proposed_cards == ()
    assert (tmp_path / item.evidence_path).exists()


def test_missed_and_low_confidence_share_one_queue(tmp_path):
    """两类待确认项共用同一队列，仅 reason 不同（设计文档 §9.1）。"""
    q = PendingQueue(tmp_path)
    q.add_missed_play("top", 2.0, 0)
    q.add_low_confidence(_play(ts=1.0), seat=0)
    assert [i.reason for i in q.items] == ["low_confidence", "missed_play"]


def test_manifest_appends_every_enqueue(tmp_path):
    q = PendingQueue(tmp_path)
    q.add_missed_play("top", 1.0, 0)
    q.add_low_confidence(_play(ts=2.0), seat=0)
    lines = (tmp_path / "pending.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["reason"] == "missed_play"
    assert rec["zone"] == "top"
    rec2 = json.loads(lines[1])
    assert rec2["proposed_cards"] == ["S5"]


def test_queue_without_root_does_not_touch_disk():
    q = PendingQueue(None)
    q.add_missed_play("top", 1.0, 0, evidence=np.zeros((4, 4, 3), np.uint8))
    assert len(q) == 1
    assert q.next().evidence_path == ""


def test_clear():
    q = PendingQueue()
    q.add(_item(1.0))
    q.clear()
    assert q.empty
