"""视图模型测试。

viewmodel 是纯函数层，因此可以完整单测，不需要起 GUI。
Qt 部件只负责把这里产出的字符串画出来。
"""

from collections import Counter

from shengji.cards import Card
from shengji.engine.trump import TrumpInfo
from shengji.events.types import PlayEvent
from shengji.session import SessionState
from shengji.ui.viewmodel import (
    SUIT_ORDER,
    OverlayView,
    build_view,
    remaining_rows,
    seat_rows,
    status_line,
)

S, H, D, C = 0, 1, 2, 3


# ---------- 剩余牌统计 ----------

def test_remaining_rows_has_four_suits_plus_jokers():
    rows = remaining_rows(None, decks=2)
    assert len(rows) == 5
    assert [r.suit for r in rows[:4]] == SUIT_ORDER
    assert rows[4].suit == -1
    assert rows[4].label == "王"


def test_remaining_rows_shows_full_deck_when_unseen_unknown():
    """unseen 为 None 时按「整副牌都还没出现」显示，不假装知道。"""
    rows = remaining_rows(None, decks=2)
    for row in rows[:4]:
        assert len(row.cells) == 13
        assert all(n == 2 for _label, n in row.cells), row.text()
    jokers = dict(rows[4].cells)
    assert jokers["小王"] == 2
    assert jokers["大王"] == 2


def test_remaining_rows_reflects_unseen_counter():
    unseen = Counter({Card(rank=5, suit=S): 0,
                      Card(rank=14, suit=S): 1})
    rows = remaining_rows(unseen, decks=2)
    spades = {label: n for label, n in rows[0].cells}
    assert spades["5"] == 0
    assert spades["A"] == 1
    # 未出现在计数器里的面值应显示 0（已被看到）
    assert spades["7"] == 0


def test_remaining_rows_scales_with_decks():
    rows = remaining_rows(None, decks=3)
    assert all(n == 3 for _label, n in rows[0].cells)


def test_remaining_row_text_contains_suit_symbol():
    rows = remaining_rows(None, decks=2)
    assert "♠" in rows[0].text()
    assert "A:2" in rows[0].text()


# ---------- 各家 ----------

def test_seat_rows_labels_and_order():
    s = SessionState()
    rows = seat_rows(s)
    assert [r.zone for r in rows] == ["bottom", "right", "top", "left"]
    assert rows[0].label == "自己"
    assert rows[0].is_self
    assert not rows[1].is_self


def test_seat_rows_carry_points():
    s = SessionState()
    s.points["top"] = 25
    rows = {r.zone: r for r in seat_rows(s)}
    assert rows["top"].points == 25
    assert "25" in rows["top"].text()


# ---------- 状态行 ----------

def test_status_ok_by_default():
    text, level = status_line(SessionState())
    assert level == "ok"
    assert "运行中" in text


def test_status_warn_when_paused():
    s = SessionState(paused=True)
    text, level = status_line(s)
    assert level == "warn"
    assert "暂停" in text


def test_status_warn_when_degraded_to_mss():
    s = SessionState(degraded_mode="mss")
    text, level = status_line(s)
    assert level == "warn"
    assert "降级" in text


def test_status_error_when_no_backend():
    s = SessionState(degraded_mode="none")
    text, level = status_line(s)
    assert level == "error"


def test_status_warn_when_pending():
    s = SessionState(pending_count=2)
    text, level = status_line(s)
    assert level == "warn"
    assert "2" in text


def test_paused_takes_priority_over_pending():
    s = SessionState(paused=True, pending_count=3)
    text, level = status_line(s)
    assert "暂停" in text


# ---------- 组装 ----------

def test_build_view_basic_shape():
    s = SessionState()
    v = build_view(s)
    assert v.title == "升级记牌器"
    assert "第 1 墩" in v.trick_text
    assert len(v.remaining) == 5
    assert len(v.seats) == 4
    assert "Ctrl+Alt+L" in v.footer


def test_build_view_shows_trump_when_known():
    s = SessionState(trump=TrumpInfo(kind="suit", suit=S, level_rank=2))
    v = build_view(s)
    assert "♠" in v.trick_text
    assert "级牌" in v.trick_text


def test_build_view_says_trump_unknown_when_missing():
    v = build_view(SessionState())
    assert "主牌未知" in v.trick_text


def test_build_view_pending_text_only_when_pending():
    assert build_view(SessionState()).pending_text == ""
    s = SessionState(pending_count=2, low_confidence_count=1, missed_count=1)
    text = build_view(s).pending_text
    assert "待确认 2" in text
    assert "低置信 1" in text
    assert "漏抓 1" in text


def test_build_view_prefers_pool_over_unseen():
    class _Pool:
        def as_counter(self):
            return Counter({Card(rank=5, suit=S): 0})

    v = build_view(SessionState(), unseen=None, pool=_Pool())
    spades = {label: n for label, n in v.remaining[0].cells}
    assert spades["5"] == 0


def test_to_lines_is_plain_text():
    v = build_view(SessionState())
    lines = v.to_lines()
    assert lines[0] == "升级记牌器"
    assert any("运行中" in ln for ln in lines)
    assert any("自己" in ln for ln in lines)


def test_overlay_view_defaults_are_renderable():
    v = OverlayView()
    assert v.status_level == "ok"
    assert v.to_lines()[0] == v.title


def test_build_view_after_play_event_shows_updated_trick():
    s = SessionState()
    s.apply(PlayEvent(zone="top", cards=(Card(rank=5, suit=H),), count=1,
                      confidence=1.0, frame_agreement=1.0,
                      trick_index=0, frame_ts=1.0))
    v = build_view(s)
    assert "第 1 墩" in v.trick_text


# ---------- 功能 B：推断行与消息行 ----------

def _fake_inference(lines=("上家 ≤12 种", "对家 ≤30 种")):
    class _Inf:
        def summary_lines(self, labels=None):
            return list(lines)

    return _Inf()


def test_build_view_without_inference_has_no_line():
    """推断不可用时不显示推断行 —— 不显示比显示错的强。"""
    assert build_view(SessionState()).inference_text == ""


def test_build_view_renders_inference_lines():
    v = build_view(SessionState(), inference=_fake_inference())
    assert "上家 ≤12 种" in v.inference_text
    assert "对家 ≤30 种" in v.inference_text
    assert any("上家" in ln for ln in v.to_lines())


def test_inference_labels_follow_session_seats():
    """座位号 → 方位标签取自 session.seats（方位由标定决定，不能写死）。"""
    seen = {}

    class _Inf:
        def summary_lines(self, labels=None):
            seen.update(labels or {})
            return ["x"]

    build_view(SessionState(seats=["bottom", "left", "top", "right"]), inference=_Inf())
    assert seen == {0: "自己", 1: "上家", 2: "对家", 3: "下家"}


def test_build_view_shows_last_message():
    """异常消息必须出现在视图里 —— 否则"失败要可见"就是空话。"""
    s = SessionState(last_message="记账异常：未见池中已无此牌")
    v = build_view(s)
    assert "记账异常" in v.message_text
    assert any("记账异常" in ln for ln in v.to_lines())


def test_build_view_has_no_message_line_when_clean():
    assert build_view(SessionState()).message_text == ""
