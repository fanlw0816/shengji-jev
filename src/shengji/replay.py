"""事件溯源重放（设计文档 §9.1 / §10.2）。

**为什么需要这一层。** 低置信事件被"门控"在待确认队列里 —— 它**不进入**
`TrickEndEvent.plays`，因此那一墩在会话状态中是"缺一手"的。用户补录时，
简单地把牌塞进当前状态是错的：牌会挂到**正在进行的墩**上，而它其实属于更早的墩。

设计文档 §9.1 定的语义是「用户纠正完成后，**按 `frame_ts` 顺序重放**待确认队列」，
§10.2 的验收断言 3 是「重放后最终状态与无低置信介入的参照序列一致」。
要满足它，状态就必须是**事件序列的函数**，而不是一堆就地累加的字段。

本模块把这件事显式化：

- `new_state()`       —— 从零建立派生状态
- `apply_event()`     —— 单事件增量应用（正常路径，性能不受影响）
- `replay_events()`   —— 从零重建（纠正后）

**两条路径共用同一份应用逻辑**，这是刻意的：正常路径与重放路径若各写一遍，
迟早会出现"纠正后结果与不纠正时不一致"这类最难查的缺陷。

墩级覆盖 `trick_overrides` 是补录的落点：`{trick_index: 该墩完整 plays}`。
`TrickEndEvent.plays` 会**覆盖**会话中已累积的出牌，所以补录的牌必须落到这里，
否则重放时会被覆盖掉 —— 这正是"静默丢牌"的另一种形式。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from .cards import Card
from .engine.accounting import AccountingError, KnownSet, UnseenPool, VariantRule
from .engine.inference import InferenceError, VoidTracker
from .engine.trick import PlayedCards
from .engine.trump import TrumpInfo
from .events.types import Event, PlayEvent, TrickEndEvent
from .session import SessionState

#: 设计文档 §11.2 实测布局的方位顺序（bottom=自己 / right=下家 / top=对家 / left=上家）
DEFAULT_SEATS: tuple[str, ...] = ("bottom", "right", "top", "left")


@dataclass(frozen=True)
class ReplayContext:
    """重放所需的不变量（一局之内不随事件变化的那些参数）。

    `own_hand` 是自己开局手牌；为 None 表示手牌未标定 —— 此时不建未见牌池，
    剩余牌统计按"整副牌都还没出现"显示，而不是假装知道。
    """

    rule: VariantRule
    seats: tuple[str, ...] = DEFAULT_SEATS
    trump: TrumpInfo | None = None
    own_seat: int = 0
    own_hand: tuple[Card, ...] | None = None
    zone_to_seat: Mapping[str, int] = field(default_factory=dict)
    degraded_mode: str = "dxcam"
    paused: bool = False

    def with_own_hand(self, hand: Sequence[Card] | None) -> "ReplayContext":
        return replace(self, own_hand=None if hand is None else tuple(hand))


@dataclass
class CounterState:
    """记牌器的全部**派生**状态 —— 完全由事件序列决定。

    这里不包含 `inference`：它是纯派生量，每次刷新时重算即可，不必进状态。
    """

    session: SessionState
    pool: UnseenPool | None = None
    voids: VoidTracker | None = None
    own_played: Counter[Card] = field(default_factory=Counter)

    def own_hand_remaining(self, own_hand: Sequence[Card] | None) -> list[Card] | None:
        """自己手上还剩哪些牌（已知手牌 − 已打出的）。未标定手牌时为 None。"""
        if own_hand is None:
            return None
        return list((Counter(own_hand) - self.own_played).elements())


def new_state(ctx: ReplayContext) -> CounterState:
    """从零建立派生状态（未消费任何事件）。"""
    session = SessionState(seats=list(ctx.seats), trump=ctx.trump)
    session.degraded_mode = ctx.degraded_mode
    session.paused = ctx.paused
    pool: UnseenPool | None = None
    if ctx.own_hand is not None:
        known = KnownSet.for_defender(list(ctx.own_hand), ctx.rule)
        pool = UnseenPool(ctx.rule, known, own_seat=ctx.own_seat)
    return CounterState(session=session, pool=pool,
                        voids=VoidTracker(len(ctx.seats)))


def apply_event(
    state: CounterState,
    event: Event,
    ctx: ReplayContext,
    *,
    trick_overrides: Mapping[int, Sequence[PlayEvent]] | None = None,
) -> str:
    """把一个事件应用到状态上，返回需要显示的错误消息（正常时为空串）。

    调用方负责决定错误消息去哪（悬浮窗的状态行）—— 本层不碰 UI。
    """
    if isinstance(event, TrickEndEvent):
        plays = (trick_overrides or {}).get(event.trick_index)
        if plays is not None:
            # 补录的牌在这里接回本墩 —— 否则会被 event.plays 覆盖掉
            event = replace(event, plays=tuple(plays))
        state.session.apply(event)
        return track_voids(state, event, ctx)

    state.session.apply(event)
    if isinstance(event, PlayEvent) and event.cards is not None:
        return _apply_play(state, event, ctx)
    return ""


def replay_events(
    events: Sequence[Event],
    ctx: ReplayContext,
    *,
    trick_overrides: Mapping[int, Sequence[PlayEvent]] | None = None,
) -> tuple[CounterState, list[str]]:
    """从零重建状态，返回 (状态, 错误消息列表)。

    返回错误列表而不是只留最后一条：一批事件里可能有多个不变式失败，
    测试需要能把它们都断言出来。
    """
    state = new_state(ctx)
    errors: list[str] = []
    for event in events:
        msg = apply_event(state, event, ctx, trick_overrides=trick_overrides)
        if msg:
            errors.append(msg)
    return state, errors


# ---------- 内部 ----------


def _apply_play(state: CounterState, event: PlayEvent, ctx: ReplayContext) -> str:
    if state.pool is None:
        return ""
    # 座位归属未标定时退回 own_seat（与改造前行为一致）——
    # "未标定"这件事本身已在启动时明确提示，此处不再重复告警
    seat = ctx.zone_to_seat.get(event.zone, ctx.own_seat)
    try:
        state.pool.on_play(seat, list(event.cards or ()))
    except AccountingError as exc:
        return f"记账异常：{exc}"
    if seat == ctx.own_seat:
        state.own_played.update(event.cards or ())
    return ""


def track_voids(state: CounterState, event: TrickEndEvent,
                ctx: ReplayContext) -> str:
    """墩结束时把本墩出牌喂给空门追踪器（设计文档 §7.4.2 的信息来源）。

    **领出方判定用 `frame_ts` 最早的那一手** —— 这是现有信号里最好的近似
    （区是"摆定后"才出事件的，摆定时刻即该手牌出现时刻的近似）。
    若最早两手同帧落定，则谁领出无法判定，此时宁可不推（保守放弃）。

    有任一手未识别（`cards is None`）、或某个区的座位归属未标定时也放弃 ——
    缺一手、错归属都会把跟牌关系推歪，而**推歪空门会破坏 soundness**，
    不只是变宽而已。

    主牌取自 `state.session.trump` 而非 `ctx.trump`：主牌的来源有优先级
    （设计文档 §7.5：用户手动指定 > 指示区识别 > 上局推算），运行时可能被修正，
    「当前生效值」才是该用的那个。
    """
    if state.voids is None:
        return ""
    trump = state.session.trump
    if trump is None or len(event.plays) < 2:
        return ""

    entries: list[tuple[float, int, tuple[Card, ...]]] = []
    for p in event.plays:
        if p.cards is None:
            return ""
        seat = ctx.zone_to_seat.get(p.zone)
        if seat is None:
            return ""
        entries.append((p.frame_ts, int(seat), p.cards))
    entries.sort(key=lambda e: e[0])
    if entries[0][0] == entries[1][0]:
        return ""

    try:
        state.voids.on_trick(
            [PlayedCards(seat=s, cards=c) for _, s, c in entries], trump)
    except InferenceError as exc:
        return f"空门推断异常：{exc}"
    return ""


# ---------- 纠正 ----------


def make_corrected_play(*, zone: str, cards: Sequence[Card],
                        trick_index: int, frame_ts: float) -> PlayEvent:
    """把用户补录/改正的牌包装成一个 `PlayEvent`。

    置信度记 1.0：这是**用户确认过**的结果，不是识别结果 ——
    把它标成低置信会让它再次被门控，造成循环。
    """
    return PlayEvent(
        zone=zone,
        cards=tuple(cards),
        count=len(cards),
        confidence=1.0,
        frame_agreement=1.0,
        trick_index=int(trick_index),
        frame_ts=float(frame_ts),
    )


def merge_trick_play(existing: Sequence[PlayEvent],
                     play: PlayEvent) -> tuple[PlayEvent, ...]:
    """把补录的一手并入本墩的出牌序列，按 `frame_ts` 保持有序。"""
    merged = list(existing) + [play]
    merged.sort(key=lambda p: p.frame_ts)
    return tuple(merged)
