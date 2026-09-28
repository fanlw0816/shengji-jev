"""事件层测试。

重点是**用真实截图做离线回放**：把 4 张不同局面的截图按顺序喂进流水线，
断言产出的 PlayEvent / TrickEndEvent 序列与局面语义一致。
不需要游戏在跑。
"""

import numpy as np
import pytest

from shengji import constants as C
from shengji.cards import Card
from shengji.events.pending import PendingQueue
from shengji.events.phash import dhash
from shengji.events.pipeline import (
    SETTLE_FRAMES,
    EventPipeline,
    ZoneTracker,
)
from shengji.events.ringbuffer import RingBuffer
from shengji.events.types import (
    FrameSnapshot,
    PendingItem,
    PlayEvent,
    TrickEndEvent,
    ZoneState,
)
from shengji.layout.model import LayoutModel
from shengji.recognition.templates import TemplateLibrary

from tests.test_templates import _rank_glyph, _suit_glyph


def _model() -> LayoutModel:
    return LayoutModel.from_reference()


def _feed(pipeline: EventPipeline, frame: np.ndarray, n: int,
          ts0: float = 0.0, dt: float = 1 / 60) -> tuple[list, float]:
    """连续喂 n 帧相同画面，返回 (事件列表, 结束时间戳)。"""
    events: list = []
    ts = ts0
    for _ in range(n):
        events.extend(pipeline.on_frame(frame, ts))
        ts += dt
    return events, ts


def _plays(events) -> list[PlayEvent]:
    return [e for e in events if isinstance(e, PlayEvent)]


def _trick_ends(events) -> list[TrickEndEvent]:
    return [e for e in events if isinstance(e, TrickEndEvent)]


def _pendings(events) -> list[PendingItem]:
    return [e for e in events if isinstance(e, PendingItem)]


def _confusable_library() -> TemplateLibrary:
    """所有点数模板相同、所有花色模板相同 -> 任何输入的次佳分都等于最佳分。

    用于确定性地触发"低置信度"分支（margin = 0）。
    """
    lib = TemplateLibrary()
    same_rank = _rank_glyph("8")
    same_suit = _suit_glyph("H")
    for lbl in ("2", "A"):
        lib.add_rank(lbl, same_rank)
    for lbl in ("S", "H"):
        lib.add_suit(lbl, same_suit)
    return lib


# ============ ZoneTracker 单元测试 ============

def test_tracker_starts_empty():
    tr = ZoneTracker(zone="top")
    assert tr.state is ZoneState.EMPTY
    assert not tr.occupied


def test_tracker_needs_settle_frames_to_settle():
    tr = ZoneTracker(zone="top")
    thumb = np.zeros((8, 8), np.uint8)
    signals: list[str] = []
    for _ in range(SETTLE_FRAMES - 1):
        signals += tr.update(thumb=thumb, diff=0.0, occupied=True,
                             count=1, patches=[])
    assert "settled" not in signals
    assert tr.state is ZoneState.ENTERING

    signals += tr.update(thumb=thumb, diff=0.0, occupied=True, count=1, patches=[])
    assert "settled" in signals
    assert tr.state is ZoneState.SETTLED


def test_tracker_unstable_frame_resets_counter():
    tr = ZoneTracker(zone="top")
    thumb = np.zeros((8, 8), np.uint8)
    for _ in range(SETTLE_FRAMES - 1):
        tr.update(thumb=thumb, diff=0.0, occupied=True, count=1, patches=[])
    tr.update(thumb=thumb, diff=99.0, occupied=True, count=1, patches=[])
    assert tr.stable == 0
    assert tr.state is ZoneState.ENTERING


def test_tracker_first_frame_is_never_stable():
    """刚出现的那一帧没有前帧可比，不能算稳定（否则动画会被当成摆定）。"""
    tr = ZoneTracker(zone="top")
    thumb = np.zeros((8, 8), np.uint8)
    tr.update(thumb=thumb, diff=None, occupied=True, count=1, patches=[])
    assert tr.stable == 0


def test_tracker_cleared_signal_and_reset():
    tr = ZoneTracker(zone="top")
    thumb = np.zeros((8, 8), np.uint8)
    for _ in range(SETTLE_FRAMES):
        tr.update(thumb=thumb, diff=0.0, occupied=True, count=1, patches=[])
    tr.cycle_hashes.append(123)
    signals = tr.update(thumb=thumb, diff=0.0, occupied=False, count=0, patches=[])
    assert "cleared" in signals
    assert tr.state is ZoneState.EMPTY
    assert tr.cycle_hashes == [], "清空后应重置周期，下一手才算新内容"


def test_tracker_returns_to_entering_when_content_changes_after_settle():
    """已摆定后又变了 -> 回到 ENTERING 重新判稳（看门狗的基础）。"""
    tr = ZoneTracker(zone="top")
    thumb = np.zeros((8, 8), np.uint8)
    for _ in range(SETTLE_FRAMES):
        tr.update(thumb=thumb, diff=0.0, occupied=True, count=1, patches=[])
    assert tr.state is ZoneState.SETTLED

    tr.update(thumb=thumb, diff=99.0, occupied=True, count=1, patches=[])
    assert tr.state is ZoneState.ENTERING
    assert tr.stable == 0


# ============ 离线回放：真实截图 ============

def test_empty_frames_produce_no_events(shots):
    p = EventPipeline(_model())
    events, _ = _feed(p, shots["empty"], 20)
    assert events == []
    assert all(s is ZoneState.EMPTY for s in p.states().values())


def test_others_one_settles_three_zones(shots):
    """其他三家各出一张 -> 恰好 3 个 PlayEvent（上/左/右），自己(下)没有。"""
    p = EventPipeline(_model())
    events, _ = _feed(p, shots["others_one"], 20)
    plays = _plays(events)
    assert sorted(e.zone for e in plays) == ["left", "right", "top"]
    for e in plays:
        assert e.count == 1
        assert e.trick_index == 0


def test_recognition_disabled_reports_count_not_cards(shots):
    """识别层未启用时只报告张数，不假装认出了牌，也不塞进待确认队列。"""
    q = PendingQueue()
    p = EventPipeline(_model(), library=None, pending=q)
    events, _ = _feed(p, shots["others_one"], 20)
    plays = _plays(events)
    assert len(plays) == 3
    assert all(e.cards is None for e in plays)
    assert list(q.items) == [], "识别未启用不应产生待确认项"


def test_trick_ends_when_all_zones_clear(shots):
    p = EventPipeline(_model())
    _, ts = _feed(p, shots["others_one"], 20)
    events, _ = _feed(p, shots["empty"], 5, ts0=ts)
    ends = _trick_ends(events)
    assert len(ends) == 1
    assert len(ends[0].plays) == 3
    assert p.trick_index == 1


def test_trick_index_increments_across_tricks(shots):
    p = EventPipeline(_model())
    _, ts = _feed(p, shots["others_one"], 20)
    _, ts = _feed(p, shots["empty"], 5, ts0=ts)
    events, _ = _feed(p, shots["all_two"], 20, ts0=ts)
    plays = _plays(events)
    assert len(plays) == 4
    assert all(e.trick_index == 1 for e in plays)


def test_next_two_occupies_only_right(shots):
    p = EventPipeline(_model())
    events, _ = _feed(p, shots["next_two"], 20)
    plays = _plays(events)
    assert [e.zone for e in plays] == ["right"]
    assert plays[0].count == 2


def test_no_trick_end_before_any_occupancy(shots):
    """开局全是空的，不能凭空报一墩结束。"""
    p = EventPipeline(_model())
    events, _ = _feed(p, shots["empty"], 30)
    assert _trick_ends(events) == []
    assert p.trick_index == 0


# ============ 去重 ============

def test_same_content_re_settle_is_deduplicated(shots):
    """同一内容再次判稳不应重复计入。

    人工把 top 区打回 ENTERING（模拟动画抖动导致重新判稳），
    但内容未变 —— 周期内的哈希相同，必须被去重。
    """
    p = EventPipeline(_model())
    events, ts = _feed(p, shots["others_one"], 20)
    assert len(_plays(events)) == 3

    tr = p.trackers["top"]
    tr.state = ZoneState.ENTERING
    tr.stable = 0

    events2, _ = _feed(p, shots["others_one"], 20, ts0=ts)
    assert _plays(events2) == [], "内容未变不应再次计入"


def test_clearing_resets_dedup_so_same_cards_next_trick_count_again(shots):
    """跨墩的同样内容必须重新计入 —— 清空后周期重置。"""
    p = EventPipeline(_model())
    _, ts = _feed(p, shots["others_one"], 20)
    _, ts = _feed(p, shots["empty"], 5, ts0=ts)
    events, _ = _feed(p, shots["others_one"], 20, ts0=ts)
    assert len(_plays(events)) == 3


# ============ 看门狗：missed_play ============

def test_content_replaced_without_clearing_raises_missed_play(shots):
    """right 区从"一张"变成"两张"，中间没有清空 -> 前一手来不及读，记 missed_play。"""
    q = PendingQueue()
    p = EventPipeline(_model(), pending=q)
    _, ts = _feed(p, shots["others_one"], 20)      # right 一张
    events, _ = _feed(p, shots["next_two"], 20, ts0=ts)   # right 两张

    misses = [i for i in _pendings(events) if i.reason == "missed_play"]
    assert len(misses) == 1
    assert misses[0].zone == "right"
    # 新旧两手都要表达：missed_play 提示 + 新的 PlayEvent
    assert [e.zone for e in _plays(events)] == ["right"]


def test_missed_play_not_raised_on_clean_clear(shots):
    q = PendingQueue()
    p = EventPipeline(_model(), pending=q)
    _, ts = _feed(p, shots["others_one"], 20)
    _, ts = _feed(p, shots["empty"], 5, ts0=ts)
    events, _ = _feed(p, shots["next_two"], 20, ts0=ts)
    assert [i for i in _pendings(events) if i.reason == "missed_play"] == []


# ============ 低置信 -> 待确认队列 ============

def test_low_confidence_goes_to_pending_queue(shots):
    """模板互相混淆时 margin=0 -> 必须进待确认队列，且不进入本墩确定记录。"""
    q = PendingQueue()
    p = EventPipeline(_model(), library=_confusable_library(), pending=q,
                      min_margin=0.2, min_agreement=0.5)
    events, _ = _feed(p, shots["others_one"], 20)

    items = _pendings(events)
    assert len(items) == 3
    assert all(i.reason == "low_confidence" for i in items)
    assert all(i.confidence < 0.2 for i in items)
    assert p.current_trick_plays() == (), "未确认的出牌不得进入确定记录"
    assert len(q) == 3


def test_pending_items_are_ordered_by_frame_ts(shots):
    q = PendingQueue()
    p = EventPipeline(_model(), library=_confusable_library(), pending=q,
                      min_margin=0.2)
    _feed(p, shots["others_one"], 20)
    ts_list = [i.frame_ts for i in q.items]
    assert ts_list == sorted(ts_list)


# ============ 环形缓冲 ============

def test_ring_buffer_receives_snapshots(shots):
    rb = RingBuffer(capacity=50)
    p = EventPipeline(_model(), ring=rb)
    _feed(p, shots["others_one"], 20)
    assert len(rb) == 20
    snap = rb.latest()
    assert isinstance(snap, FrameSnapshot)
    assert set(snap.zone_thumbs) == set(_model().zones)
    # 有牌的区应当带上了角标切片，供回溯重算
    assert "top" in snap.zone_patches
    assert snap.zone_count["top"] == 1


def test_ring_buffer_window_supports_lookback(shots):
    """置信度低时可用时间窗倒回去重算 —— 这是环形缓冲的存在意义。

    喂 1 秒（60 帧 @60Hz），再取最近 0.5 秒的窗口，应拿到约 30 帧。
    """
    rb = RingBuffer(capacity=200)
    p = EventPipeline(_model(), ring=rb)
    _, ts = _feed(p, shots["others_one"], 60)
    assert len(rb) == 60

    recent = rb.window(ts - 0.5, ts)
    assert 28 <= len(recent) <= 32, f"0.5 秒窗口应约 30 帧，实际 {len(recent)}"


# ============ 状态查询 ============

def test_states_reflect_zone_machine(shots):
    p = EventPipeline(_model())
    _feed(p, shots["others_one"], 20)
    st = p.states()
    assert st["top"] is ZoneState.SETTLED
    assert st["left"] is ZoneState.SETTLED
    assert st["right"] is ZoneState.SETTLED
    assert st["bottom"] is ZoneState.EMPTY, "自己没出牌，下区应保持空"
