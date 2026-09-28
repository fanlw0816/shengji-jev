"""应用控制器：把采集、事件层、会话状态与悬浮窗串起来。

设计上刻意把「每帧做什么」暴露成 `tick_once()`，而不是只藏在 QTimer 回调里，
这样可以用假后端做确定性测试，不需要真显卡、真游戏或真定时器。
"""

from __future__ import annotations

import time

import numpy as np
from PySide6.QtCore import QObject, QTimer

from ..capture.factory import DegradedMode, build_backend, degraded_poll_interval
from ..cards import Card
from ..engine.accounting import UnseenPool, get_variant_rule
from ..engine.trump import TrumpInfo
from ..events.pending import PendingQueue
from ..events.pipeline import EventPipeline
from ..events.ringbuffer import RingBuffer
from ..events.types import Event, PendingItem, PlayEvent, TrickEndEvent, ZoneState
from ..layout.model import LayoutModel
from ..recognition.templates import TemplateLibrary
from ..session import SessionState
from .hotkeys import HotkeyManager
from .overlay import OverlayWindow, default_position, ensure_app
from .viewmodel import build_view

DEFAULT_RING_SECONDS = 3.0
DEFAULT_FPS = 60.0


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

        self.pipeline = EventPipeline(
            self.model,
            library=library,
            pending=self.pending,
            ring=self.ring,
            zone_to_seat=zone_to_seat,
        )
        self.session = SessionState(trump=trump)
        self.session.degraded_mode = backend_mode.value

        self.rule = get_variant_rule(players, decks)
        self.own_seat = own_seat
        self._pool: UnseenPool | None = None

        self.overlay = overlay
        self.hotkeys = HotkeyManager() if use_hotkeys else None
        self._timer: QTimer | None = None
        self._interval_ms = max(1, int(degraded_poll_interval(backend_mode) * 1000))
        self.frames_seen = 0

    # ---------- 未见牌池 ----------

    @property
    def pool(self) -> UnseenPool | None:
        return self._pool

    def init_pool(self, known_seat_cards: list[Card] | None = None) -> None:
        """建立未见牌池。未提供自己手牌时按「整副牌都还没出现」显示。"""
        from ..engine.accounting import KnownSet

        if known_seat_cards is None:
            self._pool = None
            return
        known = KnownSet.for_defender(known_seat_cards, self.rule)
        self._pool = UnseenPool(self.rule, known, own_seat=self.own_seat)

    # ---------- 帧循环 ----------

    def tick_once(self, frame_image: np.ndarray | None = None,
                  ts: float | None = None) -> list[Event]:
        """处理一帧。frame_image 为 None 时向采集后端取一帧。

        返回本帧产生的事件。暂停时直接返回空列表，不消费帧。
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
        for e in events:
            self.session.apply(e)
            self._update_pool(e)
        self.refresh()
        return events

    def _update_pool(self, event: Event) -> None:
        if self._pool is None:
            return
        if isinstance(event, PlayEvent) and event.cards is not None:
            seat = self.pipeline.zone_to_seat.get(event.zone, self.own_seat)
            try:
                self._pool.on_play(seat, list(event.cards))
            except Exception as exc:  # 不变式被破坏 -> 记下来，不静默继续
                self.session.last_message = f"记账异常：{exc}"

    def refresh(self) -> None:
        """把当前状态刷到悬浮窗。"""
        if self.overlay is None:
            return
        self.overlay.set_view(build_view(self.session, pool=self._pool))

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
