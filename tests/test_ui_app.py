"""应用控制器测试。

用假后端驱动 `tick_once()`，因此不需要显卡、游戏或定时器，完全确定性。
"""

import numpy as np
import pytest

from shengji.engine.trump import parse_trump
from shengji.events.pending import PendingQueue
from shengji.events.ringbuffer import RingBuffer
from shengji.events.types import PendingItem, PlayEvent, TrickEndEvent, ZoneState
from shengji.layout.model import LayoutModel
from shengji.ui.app import CounterApp

pytest.importorskip("PySide6")


class FakeBackend:
    """按脚本逐帧返回固定画面；脚本取完后返回 None（等价于"屏幕无变化"）。"""

    def __init__(self, frames: list[np.ndarray]) -> None:
        self.frames = list(frames)
        self.i = 0
        self.closed = False

    def grab(self, region=None):
        from shengji.capture.base import Frame

        if self.i >= len(self.frames):
            return None
        img = self.frames[self.i]
        self.i += 1
        return Frame(image=img, ts=self.i / 60.0, region=(0, 0, img.shape[1], img.shape[0]))

    def close(self):
        self.closed = True


def _app(frames: list[np.ndarray], **kw) -> CounterApp:
    return CounterApp(
        LayoutModel.from_reference(),
        backend=FakeBackend(frames),
        library=None,
        pending=kw.pop("pending", PendingQueue()),
        ring=kw.pop("ring", RingBuffer(capacity=200)),
        use_hotkeys=False,
        **kw,
    )


def _drive(app: CounterApp, frame: np.ndarray, n: int) -> list:
    events = []
    for _ in range(n):
        events.extend(app.tick_once(frame, ts=app.frames_seen / 60.0))
    return events


# ---------- 主牌规格解析 ----------

def test_parse_trump_suit_with_digit():
    t = parse_trump("S2")
    assert t.kind == "suit" and t.suit == 0 and t.level_rank == 2


def test_parse_trump_suit_lowercase_and_symbol():
    assert parse_trump("h5").suit == 1
    assert parse_trump("♠A").suit == 0 and parse_trump("♠A").level_rank == 14


def test_parse_trump_ten():
    t = parse_trump("D10")
    assert t.suit == 2 and t.level_rank == 10


def test_parse_trump_no_trump_forms():
    for spec in ("NT5", "N5", "无主5", "无将5"):
        t = parse_trump(spec)
        assert t.kind == "no_trump", spec
        assert t.level_rank == 5


def test_parse_trump_rejects_bad_input():
    for bad in ("", "X2", "S", "S99", "S1", "NT"):
        with pytest.raises(ValueError):
            parse_trump(bad)


# ---------- 帧循环 ----------

def test_tick_returns_empty_when_backend_has_nothing():
    app = _app([])
    assert app.tick_once() == []
    assert app.frames_seen == 0


def test_empty_frames_produce_no_events(shots):
    app = _app([])
    assert _drive(app, shots["empty"], 20) == []


def test_frames_produce_play_events(shots):
    app = _app([])
    events = _drive(app, shots["others_one"], 20)
    plays = [e for e in events if isinstance(e, PlayEvent)]
    assert sorted(e.zone for e in plays) == ["left", "right", "top"]


def test_session_updated_from_events(shots):
    app = _app([])
    _drive(app, shots["others_one"], 20)
    assert len(app.session.current.plays) == 3
    assert app.pipeline.trick_index == 0


def test_trick_end_recorded(shots):
    app = _app([])
    _drive(app, shots["others_one"], 20)
    _drive(app, shots["empty"], 5)
    assert app.session.trick_index == 1
    assert len(app.session.history) == 1


# ---------- 暂停 ----------

def test_paused_does_not_consume_frames(shots):
    """暂停时不应消费帧 —— 否则帧流会在暂停期间被悄悄吃掉。"""
    app = _app([])
    app.session.paused = True
    assert app.tick_once(shots["others_one"], ts=0.0) == []
    assert app.frames_seen == 0
    app.session.paused = False
    events = _drive(app, shots["others_one"], 20)
    assert len([e for e in events if isinstance(e, PlayEvent)]) == 3


def test_toggle_pause_hotkey(shots):
    app = _app([])
    app.handle_hotkey("toggle_pause")
    assert app.session.paused is True
    app.handle_hotkey("toggle_pause")
    assert app.session.paused is False


# ---------- 强制重读 ----------

def test_force_snapshot_resets_settled_zones(shots):
    app = _app([])
    _drive(app, shots["others_one"], 20)
    assert all(t.state is ZoneState.SETTLED
               for z, t in app.pipeline.trackers.items() if z != "bottom")

    n = app.force_snapshot()
    assert n == 3
    assert all(t.state is ZoneState.ENTERING
               for z, t in app.pipeline.trackers.items() if z != "bottom")
    assert all(not t.cycle_hashes
               for z, t in app.pipeline.trackers.items() if z != "bottom")


def test_force_snapshot_re_emits_after_resettle(shots):
    """强制重读应让未变的内容也被重新读出（否则去重会挡住它）。"""
    app = _app([])
    _drive(app, shots["others_one"], 20)
    app.force_snapshot()
    events = _drive(app, shots["others_one"], 20)
    assert len([e for e in events if isinstance(e, PlayEvent)]) == 3


def test_force_snapshot_on_empty_table_does_nothing(shots):
    app = _app([])
    _drive(app, shots["empty"], 20)
    assert app.force_snapshot() == 0


# ---------- 悬浮窗联动 ----------

def test_refresh_without_overlay_is_safe(shots):
    app = _app([])
    app.refresh()          # 不应抛异常


def test_refresh_updates_overlay(shots, qtbot):
    from shengji.ui.overlay import OverlayWindow

    overlay = OverlayWindow()
    qtbot.addWidget(overlay)
    app = _app([])
    app.overlay = overlay
    _drive(app, shots["others_one"], 20)
    assert "第 1 墩" in overlay.trick_label.text()
    assert "运行中" in overlay.status_label.text()


def test_hotkey_toggle_interactive_moves_overlay(qtbot):
    from shengji.ui.overlay import OverlayWindow

    overlay = OverlayWindow()
    qtbot.addWidget(overlay)
    app = _app([])
    app.overlay = overlay
    assert overlay.interactive is False
    app.handle_hotkey("toggle_interactive")
    assert overlay.interactive is True


# ---------- 未见牌池 ----------

def test_pool_is_none_until_initialised(shots):
    app = _app([])
    assert app.pool is None


def test_init_pool_with_own_hand():
    from shengji.cards import Card

    app = _app([])
    hand = [Card(rank=r, suit=s) for r in range(2, 15) for s in range(2)][:25]
    app.init_pool(hand)
    assert app.pool is not None
    assert app.pool.total() == 108 - 25


def test_pool_updated_by_opponent_plays(shots):
    from shengji.cards import Card

    hand = [Card(rank=r, suit=s) for r in range(2, 15) for s in range(2)][:25]
    app = _app([])
    app.init_pool(hand)
    before = app.pool.total()
    # 识别层未启用时 cards 为 None，池不应变化
    _drive(app, shots["others_one"], 20)
    assert app.pool.total() == before


def test_pool_invariant_breakage_is_surfaced_not_swallowed():
    """记账异常必须写进会话消息，不能静默吞掉。

    构造：手牌 25 张（含 (14,0) 但不含 (14,1)），
    却报出一手「自己打出了不在已知集合里的牌」—— 这是识别错认的典型症状。
    """
    from shengji.cards import Card

    hand = [Card(rank=r, suit=s) for r in range(2, 15) for s in range(2)][:25]
    assert Card(rank=14, suit=1) not in hand

    app = _app([])
    app.init_pool(hand)
    assert app.pool is not None

    # zone="top" 未配置 zone_to_seat，会落到 own_seat，从而走「自己出牌」的校验分支
    ev = PlayEvent(zone="top", cards=(Card(rank=14, suit=1),), count=1,
                   confidence=1.0, frame_agreement=1.0,
                   trick_index=0, frame_ts=1.0)
    app._update_pool(ev)          # 不应抛异常

    assert "记账异常" in app.session.last_message
    assert "已知集合之外" in app.session.last_message


# ---------- 生命周期 ----------

def test_stop_closes_backend():
    app = _app([])
    app.stop()
    assert app.backend.closed


def test_stop_is_idempotent():
    app = _app([])
    app.stop()
    app.stop()
    assert app.backend.closed


def test_degraded_mode_recorded_on_session():
    from shengji.capture.factory import DegradedMode

    app = _app([], backend_mode=DegradedMode.MSS)
    assert app.session.degraded_mode == "mss"
