"""应用控制器：把采集、事件层、会话状态与悬浮窗串起来。

设计上刻意把「每帧做什么」暴露成 `tick_once()`，而不是只藏在 QTimer 回调里，
这样可以用假后端做确定性测试，不需要真显卡、真游戏或真定时器。

**状态是事件序列的函数**（见 `shengji.replay`）：本类维护一份事件日志，
正常路径增量应用事件，用户纠正待确认项时按日志**全量重建** ——
这是设计文档 §9.1「纠正后按 `frame_ts` 顺序重放」的落地方式。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import replace

import numpy as np
from PySide6.QtCore import QObject, QTimer

from ..capture.factory import DegradedMode, build_backend, degraded_poll_interval
from ..cards import Card
from ..engine.accounting import UnseenPool, get_variant_rule
from ..engine.inference import (
    InferenceError,
    SeatInference,
    VoidTracker,
    infer_per_seat,
)
from ..engine.trump import TrumpInfo
from ..events.pending import PendingQueue
from ..events.pipeline import EventPipeline
from ..events.ringbuffer import RingBuffer
from ..events.types import Event, PendingItem, PlayEvent, TrickEndEvent, ZoneState
from ..layout.model import LayoutModel
from ..recognition.templates import TemplateLibrary
from ..replay import (
    DEFAULT_SEATS,
    ReplayContext,
    apply_event,
    make_corrected_play,
    merge_trick_play,
    new_state,
    replay_events,
)
from ..session import SessionState
from .correction import current_row
from .hotkeys import HotkeyManager
from .overlay import OverlayWindow, default_position, ensure_app
from .viewmodel import ZONE_LABELS, build_view

DEFAULT_RING_SECONDS = 3.0
DEFAULT_FPS = 60.0

# 区 → 座位映射的默认值，取自设计文档 §11.2 的**实测布局**
# （bottom=自己 / right=下家 / top=对家 / left=上家），与 SessionState.seats 顺序一致。
#
# ⚠️ 这是**可覆盖的默认值，不是隐式约定**：客户端座位方位或出牌方向不同时必须由
# 标定结果覆盖（设计文档 §7.5）。非 4 人局不使用它 —— 映射为空时宁可把出牌归错，
# 也要在状态栏明说"未标定"，而不是悄悄算错。
DEFAULT_ZONE_TO_SEAT: dict[str, int] = {
    "bottom": 0, "right": 1, "top": 2, "left": 3,
}


class CounterApp(QObject):
    """记牌器应用。"""

    def __init__(
        self,
        model: LayoutModel | None = None,
        *,
        backend=None,
        backend_mode: DegradedMode = DegradedMode.DXCAM,
        library: TemplateLibrary | None = None,
        overlay: OverlayWindow | None = None,
        pending: PendingQueue | None = None,
        ring: RingBuffer | None = None,
        output_idx: int = 0,
        trump: TrumpInfo | None = None,
        players: int = 4,
        decks: int = 2,
        own_seat: int = 0,
        use_hotkeys: bool = True,
        zone_to_seat: dict[str, int] | None = None,
        ring_seconds: float = DEFAULT_RING_SECONDS,
        fps: float = DEFAULT_FPS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self.model = model or LayoutModel.from_reference()

        if backend is None:
            backend, backend_mode = build_backend(output_idx=output_idx)
        self.backend = backend
        self.backend_mode = backend_mode

        self.ring = ring if ring is not None else RingBuffer(
            capacity=int(ring_seconds * fps))
        self.pending = pending if pending is not None else PendingQueue()
        self.library = library

        if zone_to_seat is not None:
            mapping = dict(zone_to_seat)
        elif players == len(DEFAULT_ZONE_TO_SEAT):
            mapping = dict(DEFAULT_ZONE_TO_SEAT)
        else:
            mapping = {}

        self.pipeline = EventPipeline(
            self.model,
            library=library,
            pending=self.pending,
            ring=self.ring,
            zone_to_seat=mapping,
        )
        self.rule = get_variant_rule(players, decks)
        self.own_seat = own_seat

        # 状态 = f(事件序列)。日志是真相来源，_state 是当前的派生结果；
        # 纠正时会整体重建，所以外部**不要缓存 session / pool 对象的引用**。
        self._ctx = ReplayContext(
            rule=self.rule,
            seats=DEFAULT_SEATS,
            trump=trump,
            own_seat=own_seat,
            own_hand=None,
            zone_to_seat=mapping,
            degraded_mode=backend_mode.value,
        )
        self._state = new_state(self._ctx)
        self._log: list[Event] = []
        #: 墩级覆盖 {trick_index: 该墩完整 plays} —— 补录的牌落在这里（见 replay 模块）
        self._trick_overrides: dict[int, tuple[PlayEvent, ...]] = {}
        # 功能 B：最近一次按家推断结果
        self._inference: SeatInference | None = None

        if not mapping:
            # 归属未标定就必须说出来：出牌会被记到错误的人头上
            self.session.last_message = "未标定区→座位映射，出牌归属不可靠"

        self.overlay = overlay
        if self.overlay is not None:
            self.overlay.set_correction_handler(self._on_correction_submitted)
        self.hotkeys = HotkeyManager() if use_hotkeys else None
        self._timer: QTimer | None = None
        self._interval_ms = max(1, int(degraded_poll_interval(backend_mode) * 1000))
        self.frames_seen = 0

    # ---------- 派生状态的只读入口 ----------

    @property
    def session(self) -> SessionState:
        """一局的会话状态。纠正后会整体重建，**不要跨调用缓存这个对象**。"""
        return self._state.session

    @property
    def pool(self) -> UnseenPool | None:
        return self._state.pool

    @property
    def voids(self) -> VoidTracker | None:
        return self._state.voids

    @property
    def inference(self) -> SeatInference | None:
        """最近一次按家推断结果（无手牌 / 无主牌 / 约束矛盾时为 None）。"""
        return self._inference

    def init_pool(self, known_seat_cards: list[Card] | None = None) -> None:
        """标定自己手牌并（重新）建立未见牌池。

        未提供手牌时清空牌池 —— 此时剩余牌统计按「整副牌都还没出现」显示，
        不假装知道。若日志里已有事件（对局中途才标定手牌的情形），会一并重放。
        """
        self._ctx = self._ctx.with_own_hand(known_seat_cards)
        self._rebuild()

    def own_hand_remaining(self) -> list[Card] | None:
        """自己手上还剩哪些牌（已知手牌 − 已打出的）。未标定手牌时返回 None。"""
        return self._state.own_hand_remaining(self._ctx.own_hand)

    # ---------- 帧循环 ----------

    def tick_once(self, frame_image: np.ndarray | None = None,
                  ts: float | None = None) -> list[Event]:
        """处理一帧。frame_image 为 None 时向采集后端取一帧。

        返回本帧产生的事件。暂停时直接返回空列表，不消费帧 ——
        若暂停期间继续消费帧，那些帧会被悄悄吃掉，而用户以为只是在暂停显示。
        """
        if self.session.paused:
            return []

        if frame_image is None:
            f = self.backend.grab(None)
            if f is None:
                return []
            frame_image, ts = f.image, f.ts
        if ts is None:
            ts = time.perf_counter()

        self.frames_seen += 1
        events = self.pipeline.on_frame(frame_image, ts)
        self._commit(events)
        self.refresh()
        return events

    def _commit(self, events: Sequence[Event]) -> None:
        """把事件并入日志并增量应用。

        应用逻辑与重放路径**共用同一个函数**（见 `shengji.replay`）——
        两条路径若各写一遍，迟早会出现「纠正后结果与不纠正时不一致」的缺陷。
        """
        for e in events:
            self._log.append(e)
            msg = apply_event(self._state, e, self._ctx,
                              trick_overrides=self._trick_overrides)
            if msg:
                # 不变式被破坏就显示出来，绝不静默继续算
                self.session.last_message = msg

    # ---------- 纠正面板（设计文档 §9.1 / §14.1）----------

    @property
    def pending_items(self) -> tuple[PendingItem, ...]:
        """尚未处理完的待确认项（按 `frame_ts` 有序）。"""
        return self.pending.items

    @property
    def pending_first(self) -> PendingItem | None:
        """队首待确认项 —— 纠正面板一次只处理一项，从最早的开始。"""
        return self.pending.next()

    def resolve_pending(self, item: PendingItem,
                        cards: Sequence[Card] | None = None,
                        *, drop: bool = False) -> bool:
        """处理一项待确认，返回是否确实处理掉了。

        - `cards` 非空：用户给出的**正确牌**（"采纳识别结果"传 `item.proposed_cards`）
        - `drop=True`：用户明确放弃记账（宁可少记一手，也不记错一手）

        做法是**把它从事件日志里摘掉、再把正确的一手并回该墩，然后整体重放**。
        不就地补一张牌的原因：低置信那一手原本不在 `TrickEndEvent.plays` 里，
        就地补录会把它挂到**当前正在进行的墩**上 —— 那是静默算错。
        """
        if not self._forget_from_log(item):
            # 不在日志里 —— 已被处理过，或不是本应用产生的项
            return False
        self.pending.resolve(item)

        label = ZONE_LABELS.get(item.zone, item.zone)
        if not drop and cards:
            play = make_corrected_play(zone=item.zone, cards=cards,
                                       trick_index=item.trick_index,
                                       frame_ts=item.frame_ts)
            base = self._trick_plays_for(item.trick_index)
            self._trick_overrides[item.trick_index] = merge_trick_play(base, play)
            # 两件事都要做，少一件就有一半状态是错的：
            #   · 进日志 -> 走一次出牌事件，牌池/自己手牌跟着扣
            #   · 进墩覆盖 -> `TrickEndEvent.plays` 会**覆盖**累积记录，
            #     不并进覆盖里那手会被丢掉，这一墩按缺人处理
            self._insert_play_into_log(play)
            message = (f"已补录 第 {item.trick_index + 1} 墩 {label}："
                       + " ".join(c.label() for c in cards))
        else:
            message = f"已跳过 第 {item.trick_index + 1} 墩 {label}（不计入记账）"

        errors = self._rebuild()
        # 重放出的不变式问题优先显示 —— 它比"补录成功"更需要用户看见
        self.session.last_message = errors[-1] if errors else message
        return True

    def _insert_play_into_log(self, play: PlayEvent) -> None:
        """把补录的一手插到日志中**它该在的位置**。

        位置必须落在本墩 `TrickEndEvent` 之前、且按 `frame_ts` 排好 ——
        直接 append 的话，`SessionState` 会把它算到别的墩上（顺序错了，
        账面就跟着错，而且看起来像是"识别错了"）。
        """
        bound = len(self._log)
        for i, ev in enumerate(self._log):
            if isinstance(ev, TrickEndEvent) and ev.trick_index == play.trick_index:
                bound = i
                break
        pos = bound
        while pos > 0 and self._log[pos - 1].frame_ts > play.frame_ts:
            pos -= 1
        self._log.insert(pos, play)

    def _forget_from_log(self, item: PendingItem) -> bool:
        """把某一项从事件日志中摘掉。

        按**身份**比较（`is`）而非 dataclass 相等性：两个字段完全相同的
        待确认项理论上可能同时存在，按相等性删除会误删更早的那个。
        """
        for i, ev in enumerate(self._log):
            if ev is item:
                del self._log[i]
                return True
        return False

    def _trick_plays_for(self, trick_index: int) -> tuple[PlayEvent, ...]:
        """取某墩当前已知的出牌（优先取覆盖表 —— 它含此前已补录的牌）。"""
        existing = self._trick_overrides.get(trick_index)
        if existing is not None:
            return existing
        for ev in self._log:
            if isinstance(ev, TrickEndEvent) and ev.trick_index == trick_index:
                return ev.plays
        return ()

    def _sync_context(self) -> None:
        """把会话里的**非派生**字段收回 `ctx`。

        重建会新建一个 `SessionState`，而主牌 / 暂停 / 降级档位不是由事件推导出来的 ——
        不收回就会在纠正后被打回构造时的初值（"纠正一次主牌就丢了"这类失效
        在测试之外很难被发现）。派生字段（墩次、分数、待确认计数）不回填，
        它们正是重放要重算的东西。
        """
        self._ctx = replace(self._ctx,
                            trump=self.session.trump,
                            paused=self.session.paused,
                            degraded_mode=self.session.degraded_mode)

    def _rebuild(self) -> list[str]:
        """按事件日志从零重建全部派生状态，返回重放中出现的错误消息。

        这是设计文档 §9.1 的「按 `frame_ts` 顺序重放」：
        正常路径是增量应用，只有用户纠正时才走全量重建。
        """
        self._sync_context()
        self._state, errors = replay_events(self._log, self._ctx,
                                            trick_overrides=self._trick_overrides)
        if errors:
            self.session.last_message = errors[-1]
        self.refresh()
        return errors

    # ---------- 功能 B：按家推断 ----------

    def _recompute_inference(self) -> None:
        """重算按家推断范围。矛盾时明确报错并撤下面板，不展示可疑结果。"""
        if self.pool is None or self.session.trump is None:
            self._inference = None
            return
        try:
            self._inference = infer_per_seat(
                self.pool, self.session.trump,
                voids=self.voids.as_mapping(),
                own_hand_remaining=self.own_hand_remaining(),
            )
        except InferenceError as exc:
            self._inference = None
            self.session.last_message = f"推断异常（已停止显示推断）：{exc}"

    def refresh(self) -> None:
        """重算按家推断，并把当前状态刷到悬浮窗。

        推断先算、与 overlay 无关：它同时也是给测试与后续消费者（如纠正面板）用的状态。
        """
        self._recompute_inference()
        if self.overlay is None:
            return
        self.overlay.set_view(build_view(self.session, pool=self.pool,
                                         inference=self._inference))
        item = self.pending_first
        # key 用「时点 + 区 + 墩」标识队首项：同一项的多次刷新不能重置用户的选择
        self.overlay.set_correction(
            current_row(self.pending),
            pending_total=len(self.pending),
            key=None if item is None
            else (item.frame_ts, item.zone, item.trick_index))

    def _on_correction_submitted(self, cards: list[Card], drop: bool) -> None:
        """悬浮窗纠正面板的回调：处理队首那一项。

        面板一次只编辑队首项，所以这里也固定取队首 —— 若中途队列变了，
        宁可什么都不做，也不要改错项。
        """
        item = self.pending_first
        if item is None:
            return
        self.resolve_pending(item, cards, drop=drop)

    # ---------- 热键动作 ----------

    def handle_hotkey(self, action: str) -> None:
        if action == "toggle_interactive":
            if self.overlay is not None:
                self.overlay.toggle_interactive()
        elif action == "toggle_pause":
            self.session.paused = not self.session.paused
        elif action == "force_snapshot":
            self.force_snapshot()
        self.refresh()

    def force_snapshot(self) -> int:
        """强制重新判稳并重读各出牌区（漏抓时手动补）。

        清掉周期哈希与判稳计数，让下一次稳定帧重新触发识别 ——
        这样即使内容没变也会被重新读出，而不是被去重逻辑挡住。
        """
        n = 0
        for tracker in self.pipeline.trackers.values():
            if tracker.state is ZoneState.EMPTY:
                continue
            tracker.cycle_hashes.clear()
            tracker.state = ZoneState.ENTERING
            tracker.stable = 0
            n += 1
        self.session.last_message = f"已强制重读 {n} 个出牌区"
        return n

    # ---------- 生命周期 ----------

    def show_overlay(self, interactive: bool = False) -> None:
        if self.overlay is None:
            return
        w, h = 460, 320
        x, y = default_position(1920, 1080, w, h)
        self.overlay.move(x, y)
        self.refresh()
        self.overlay.apply_interactive(interactive)
        self.overlay.show()

    def start(self) -> None:
        ensure_app()
        self.show_overlay()
        if self.hotkeys is not None:
            result = self.hotkeys.register()
            failed = [k for k, ok in result.items() if not ok]
            if failed:
                self.session.last_message = "热键注册失败：" + "、".join(failed)
        self._timer = QTimer(self)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._on_timer)
        self._timer.start()

    def _on_timer(self) -> None:
        if self.hotkeys is not None:
            for action in self.hotkeys.poll():
                self.handle_hotkey(action)
        self.tick_once()

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        if self.hotkeys is not None:
            self.hotkeys.unregister()
        try:
            self.backend.close()
        except Exception:
            pass
