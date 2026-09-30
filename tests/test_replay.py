"""事件溯源重放测试（设计文档 §9.1 / §10.2）。

重点验三件事：

1. **增量应用与全量重放给出同样的状态** —— 否则用户一纠正，账面就和没纠正时对不上了
2. **墩级覆盖把补录的一手接回"正确的那一墩"**，而不是当前正在进行的墩
3. 缺手 / 错归属时**保守放弃**空门推断，而不是推歪（推歪会破坏 soundness）
"""

from shengji.cards import Card
from shengji.engine.accounting import get_variant_rule
from shengji.engine.trump import parse_trump
from shengji.events.types import PendingItem, PlayEvent, TrickEndEvent
from shengji.replay import (
    ReplayContext,
    apply_event,
    make_corrected_play,
    merge_trick_play,
    new_state,
    replay_events,
)

ZONE_TO_SEAT = {"bottom": 0, "right": 1, "top": 2, "left": 3}


def _hand() -> list[Card]:
    """25 张手牌：♠2..♠K + ♥2..♥K + ♠A。"""
    return [Card(rank=r, suit=s) for r in range(2, 15) for s in range(2)][:25]


def _ctx(hand=None, *, trump: str | None = "S2", zone_to_seat=None) -> ReplayContext:
    return ReplayContext(
        rule=get_variant_rule(4, 2),
        trump=None if trump is None else parse_trump(trump),
        own_seat=0,
        own_hand=None if hand is None else tuple(hand),
        zone_to_seat=ZONE_TO_SEAT if zone_to_seat is None else zone_to_seat,
    )


def _play(zone: str, rank: int, suit: int, ts: float,
          trick: int = 0) -> PlayEvent:
    return PlayEvent(zone=zone, cards=(Card(rank=rank, suit=suit),), count=1,
                     confidence=1.0, frame_agreement=1.0,
                     trick_index=trick, frame_ts=ts)


# ---------- 建状态 ----------

def test_new_state_without_hand_builds_no_pool():
    """手牌未标定就不建牌池 —— 不假装知道剩余牌。"""
    state = new_state(_ctx(None))
    assert state.pool is None
    assert state.own_hand_remaining(None) is None


def test_new_state_with_hand_builds_pool_and_voids():
    ctx = _ctx(_hand())
    state = new_state(ctx)
    assert state.pool is not None
    assert state.voids is not None
    assert len(state.own_hand_remaining(ctx.own_hand)) == 25


# ---------- 增量 vs 重放 ----------

def test_incremental_matches_replay():
    """逐事件应用与一次性重放必须给出相同状态 —— 这是纠正能成立的前提。"""
    ctx = _ctx(_hand())
    events = [
        _play("left", 5, 1, 1.0),
        _play("top", 6, 2, 1.1),
        _play("right", 7, 1, 1.2),
        _play("bottom", 8, 1, 1.3),
        TrickEndEvent(trick_index=0, frame_ts=1.4, plays=(
            _play("left", 5, 1, 1.0), _play("top", 6, 2, 1.1),
            _play("right", 7, 1, 1.2), _play("bottom", 8, 1, 1.3))),
    ]

    incremental = new_state(ctx)
    for e in events:
        apply_event(incremental, e, ctx)

    replayed, errors = replay_events(events, ctx)

    assert errors == []
    assert replayed.session.trick_index == incremental.session.trick_index
    assert replayed.session.points == incremental.session.points
    assert len(replayed.session.history) == len(incremental.session.history)
    assert (replayed.pool.as_counter() == incremental.pool.as_counter())
    assert replayed.own_played == incremental.own_played


def test_own_play_decrements_own_hand():
    ctx = _ctx(_hand())
    state = new_state(ctx)
    msg = apply_event(state, _play("bottom", 5, 1, 1.0), ctx)

    assert msg == ""
    assert Card(rank=5, suit=1) not in state.own_hand_remaining(ctx.own_hand)
    assert len(state.own_hand_remaining(ctx.own_hand)) == 24


def test_pool_invariant_breakage_is_reported_not_swallowed():
    """出了不在已知集合里的牌 —— 必须报出来，不能静默继续算。"""
    ctx = _ctx(_hand(), zone_to_seat={"bottom": 0, "top": 0, "left": 0, "right": 0})
    state = new_state(ctx)
    assert Card(rank=14, suit=1) not in _hand()

    msg = apply_event(state, _play("top", 14, 1, 1.0), ctx)

    assert "记账异常" in msg


# ---------- 墩级覆盖 ----------

def test_trick_override_backfills_the_right_trick():
    """补录的一手并入**指定墩**，且分数按补全后的四手重算。"""
    ctx = _ctx(_hand())
    partial = (
        _play("left", 5, 1, 1.0),      # 领出 ♥5
        _play("top", 6, 2, 1.1),       # 跟 ♦6
        _play("right", 7, 1, 1.2),     # 跟 ♥7
    )
    end = TrickEndEvent(trick_index=0, frame_ts=1.4, plays=partial)

    without, _ = replay_events([*partial, end], ctx)
    assert without.session.history[0].points_known is False   # 缺一手 -> 不算分
    assert without.session.history[0].winner_zone is None

    corrected = make_corrected_play(zone="bottom", cards=(Card(rank=8, suit=1),),
                                    trick_index=0, frame_ts=1.3)
    overrides = {0: merge_trick_play(partial, corrected)}
    with_fix, errors = replay_events([*partial, end], ctx, trick_overrides=overrides)

    assert errors == []
    rec = with_fix.session.history[0]
    assert len(rec.plays) == 4
    assert rec.points_known is True
    assert rec.points == 5          # 只有领出的 ♥5 是分牌（5/10/K 计分）
    # 补全后是 ♥8（自己）压过 ♥7（下家）—— 赢家必须按**座位号**归属，
    # 不能按 plays 下标（出牌顺序与座位顺序无关）
    assert rec.winner_zone == "bottom"


def test_corrected_play_is_full_confidence():
    """用户确认的结果置信度记为 1.0 —— 否则会被再次门控，陷入循环。"""
    play = make_corrected_play(zone="bottom", cards=(Card(rank=8, suit=1),),
                               trick_index=2, frame_ts=3.5)
    assert play.confidence == 1.0
    assert play.frame_agreement == 1.0
    assert play.trick_index == 2
    assert play.frame_ts == 3.5
    assert play.count == 1


def test_merge_trick_play_keeps_timestamp_order():
    early = _play("left", 5, 1, 1.0)
    late = _play("right", 7, 1, 1.2)
    filled = make_corrected_play(zone="top", cards=(Card(rank=6, suit=2),),
                                 trick_index=0, frame_ts=1.1)

    merged = merge_trick_play((late, early), filled)

    assert [p.frame_ts for p in merged] == [1.0, 1.1, 1.2]


# ---------- 空门推断的保守性 ----------

def test_void_deduction_needs_trump_and_two_plays():
    ctx = _ctx(_hand(), trump=None)
    state = new_state(ctx)
    msg = apply_event(state, TrickEndEvent(
        trick_index=0, frame_ts=1.4,
        plays=(_play("left", 5, 1, 1.0), _play("top", 6, 2, 1.1))), ctx)

    assert msg == ""
    assert state.voids.deductions == 0


def test_unrecognised_play_skips_void_deduction():
    """有一手没认出来 -> 整墩不推空门（缺一手会把跟牌关系推歪）。"""
    ctx = _ctx(_hand())
    state = new_state(ctx)
    plays = list((_play("left", 5, 1, 1.0), _play("top", 6, 2, 1.1),
                  _play("right", 7, 1, 1.2)))
    plays[1] = PlayEvent(zone="top", cards=None, count=1, confidence=0.0,
                         frame_agreement=0.0, trick_index=0, frame_ts=1.1)

    apply_event(state, TrickEndEvent(trick_index=0, frame_ts=1.4,
                                     plays=tuple(plays)), ctx)

    assert state.voids.deductions == 0


def test_unmapped_zone_skips_void_deduction():
    """区→座位未标定 -> 归属不可靠 -> 不推空门。"""
    ctx = _ctx(_hand(), zone_to_seat={})
    state = new_state(ctx)
    apply_event(state, TrickEndEvent(
        trick_index=0, frame_ts=1.4,
        plays=(_play("left", 5, 1, 1.0), _play("top", 6, 2, 1.1))), ctx)

    assert state.voids.deductions == 0


def test_void_deduction_records_follow_suit_failure():
    """跟不出领出花色的一家被记为空门（这是功能 B 的信息来源）。"""
    ctx = _ctx(_hand())
    state = new_state(ctx)
    # 上家(3)领出 ♥5；对家(2)跟 ♦6 -> 对家对红桃空门
    apply_event(state, TrickEndEvent(
        trick_index=0, frame_ts=1.4,
        plays=(_play("left", 5, 1, 1.0), _play("top", 6, 2, 1.1),
               _play("right", 7, 1, 1.2), _play("bottom", 8, 1, 1.3))), ctx)

    assert state.voids.is_void(2, 1)
    assert not state.voids.is_void(3, 1)
