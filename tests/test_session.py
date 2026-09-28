"""会话状态测试。

session 层刻意与 Qt 无关，因此可以纯单测覆盖，不需要起 GUI。
"""

from shengji.cards import RANK_KING, Card
from shengji.engine.trump import TrumpInfo
from shengji.events.types import PendingItem, PlayEvent, TrickEndEvent
from shengji.session import SessionState, TrickRecord

S, H, D, C = 0, 1, 2, 3
TRUMP_S_LEVEL2 = TrumpInfo(kind="suit", suit=S, level_rank=2)


def _play(zone: str, cards, ts: float = 1.0, trick: int = 0) -> PlayEvent:
    return PlayEvent(zone=zone, cards=None if cards is None else tuple(cards),
                     count=len(cards) if cards else 1,
                     confidence=0.9, frame_agreement=1.0,
                     trick_index=trick, frame_ts=ts)


def _pending(reason: str = "low_confidence", ts: float = 2.0) -> PendingItem:
    return PendingItem(frame_ts=ts, reason=reason, zone="top", seat=None,
                       proposed_cards=(), confidence=0.1, frame_agreement=1.0,
                       evidence_path="", trick_index=0)


# ---------- 基本累积 ----------

def test_starts_empty():
    s = SessionState()
    assert s.trick_index == 0
    assert s.current.plays == []
    assert s.history == []
    assert s.pending_count == 0
    assert not s.has_attention


def test_points_initialized_for_all_seats():
    s = SessionState()
    for zone in ("bottom", "right", "top", "left"):
        assert s.points[zone] == 0


def test_play_events_accumulate_into_current_trick():
    s = SessionState()
    s.apply(_play("bottom", [Card(rank=5, suit=H)], ts=1.0))
    s.apply(_play("right", [Card(rank=10, suit=H)], ts=1.1))
    assert len(s.current.plays) == 2
    assert s.last_ts == 1.1


def test_play_event_with_unexpected_trick_index_rebuilds_record():
    """事件属于新的一墩（例如漏掉了墩结束）—— 明确重建，不静默丢弃。"""
    s = SessionState()
    s.apply(_play("bottom", [Card(rank=5, suit=H)], trick=0))
    s.apply(_play("right", [Card(rank=10, suit=H)], trick=3))
    assert s.current.index == 3
    assert len(s.current.plays) == 1, "旧墩的残留不应混进来"


# ---------- 算分 ----------

def test_trick_scoring_picks_highest_of_lead_suit():
    s = SessionState(trump=TRUMP_S_LEVEL2)
    plays = [
        _play("bottom", [Card(rank=5, suit=H)]),
        _play("right", [Card(rank=10, suit=H)]),
        _play("top", [Card(rank=RANK_KING, suit=H)]),
        _play("left", [Card(rank=3, suit=H)]),
    ]
    s.apply(TrickEndEvent(trick_index=0, frame_ts=9.0, plays=tuple(plays)))

    rec = s.history[0]
    assert rec.winner_zone == "top"
    assert rec.points == 5 + 10 + 10        # 5 / 10 / K，3 无分
    assert rec.points_known
    assert rec.confident
    assert s.points["top"] == 25
    assert s.trick_index == 1


def test_trick_scoring_trump_beats_lead_suit():
    """主牌压过领出花色的最大牌，并且分牌归赢家。

    注意计分：5→5 分、10→10 分、K→10 分，其余 0 分。
    本手为 5♥ / 3♠(主) / K♥ / 10♥，合计 5+0+10+10 = 25。
    """
    s = SessionState(trump=TRUMP_S_LEVEL2)
    plays = [
        _play("bottom", [Card(rank=5, suit=H)]),
        _play("right", [Card(rank=3, suit=S)]),      # 主牌，虽小但压得过副牌
        _play("top", [Card(rank=RANK_KING, suit=H)]),
        _play("left", [Card(rank=10, suit=H)]),
    ]
    s.apply(TrickEndEvent(trick_index=0, frame_ts=9.0, plays=tuple(plays)))
    assert s.history[0].winner_zone == "right"
    assert s.history[0].points == 25
    assert s.points["right"] == 25
    assert s.points["top"] == 0, "非赢家不应得分"


def test_trick_scoring_uses_accumulated_plays_when_event_carries_none():
    s = SessionState(trump=TRUMP_S_LEVEL2)
    s.apply(_play("bottom", [Card(rank=5, suit=H)]))
    s.apply(_play("right", [Card(rank=10, suit=H)]))
    s.apply(TrickEndEvent(trick_index=0, frame_ts=9.0, plays=()))
    rec = s.history[0]
    assert len(rec.plays) == 2
    assert rec.points == 15
    assert rec.winner_zone == "right"


def test_unrecognised_cards_mark_points_unknown():
    """牌未识别时如实标记「分牌未知」，绝不猜一个结果。"""
    s = SessionState(trump=TRUMP_S_LEVEL2)
    plays = [_play("bottom", None), _play("right", None)]
    s.apply(TrickEndEvent(trick_index=0, frame_ts=9.0, plays=tuple(plays)))
    rec = s.history[0]
    assert not rec.points_known
    assert not rec.confident
    assert rec.points == 0
    assert s.points["bottom"] == 0


def test_missing_trump_marks_points_unknown_without_raising():
    s = SessionState(trump=None)
    plays = [_play("bottom", [Card(rank=5, suit=H)])]
    s.apply(TrickEndEvent(trick_index=0, frame_ts=9.0, plays=tuple(plays)))
    assert not s.history[0].points_known


def test_empty_trick_does_not_raise():
    s = SessionState()
    s.apply(TrickEndEvent(trick_index=0, frame_ts=1.0, plays=()))
    assert s.history[0].points_known is False


# ---------- 待确认项 ----------

def test_pending_counts_by_reason():
    s = SessionState()
    s.apply(_pending("low_confidence"))
    s.apply(_pending("low_confidence", ts=2.1))
    s.apply(_pending("missed_play", ts=2.2))
    assert s.pending_count == 3
    assert s.low_confidence_count == 2
    assert s.missed_count == 1
    assert s.has_attention


def test_pending_does_not_pollute_confirmed_record():
    s = SessionState(trump=TRUMP_S_LEVEL2)
    s.apply(_pending())
    assert s.current.plays == []
    assert s.history == []


def test_pending_sets_attention_message():
    s = SessionState()
    s.apply(_pending("low_confidence"))
    assert "不确定" in s.last_message
    s2 = SessionState()
    s2.apply(_pending("missed_play"))
    assert "补录" in s2.last_message


def test_resolve_pending_decrements_and_floors_at_zero():
    s = SessionState()
    s.apply(_pending())
    s.apply(_pending(ts=2.1))
    s.resolve_pending()
    assert s.pending_count == 1
    s.resolve_pending()
    s.resolve_pending()
    assert s.pending_count == 0


# ---------- 其他 ----------

def test_total_points_sums_all_seats():
    s = SessionState(trump=TRUMP_S_LEVEL2)
    s.points["top"] = 15
    s.points["left"] = 10
    assert s.total_points() == 25


def test_trick_record_defaults():
    rec = TrickRecord(index=0)
    assert rec.plays == []
    assert rec.winner_zone is None
    assert rec.points == 0
    assert rec.points_known is False
    assert rec.confident is True


def test_multiple_tricks_accumulate_points():
    s = SessionState(trump=TRUMP_S_LEVEL2)
    for t in range(2):
        plays = [_play("bottom", [Card(rank=5, suit=H)], trick=t),
                 _play("right", [Card(rank=10, suit=H)], trick=t)]
        s.apply(PlayEvent(zone="bottom", cards=(Card(rank=5, suit=H),), count=1,
                          confidence=1.0, frame_agreement=1.0,
                          trick_index=t, frame_ts=float(t)))
        s.apply(PlayEvent(zone="right", cards=(Card(rank=10, suit=H),), count=1,
                          confidence=1.0, frame_agreement=1.0,
                          trick_index=t, frame_ts=float(t) + 0.1))
        s.apply(TrickEndEvent(trick_index=t, frame_ts=float(t) + 1, plays=()))
    assert s.trick_index == 2
    assert len(s.history) == 2
    assert s.points["right"] == 30      # 两墩各 15 分
