"""事件流水线：把帧序列变成出牌事件（设计文档 §4.3 / §4.6 / §9.1）。

职责：
- 每个出牌区一个时序状态机（`EMPTY` / `ENTERING` / `SETTLED`）
- 判稳：连续若干帧差异低于阈值才认为牌已摆定
- 只在 `SETTLED` 状态做识别，且识别前先做 pHash 去重
- 墩边界：全部 N 区回到 `EMPTY` 时结束本墩
- 看门狗：一次占用周期内出现两种不同内容 -> 说明前一手被覆盖 -> `missed_play`
- 低置信 -> 进待确认队列（**不中断采集与状态机**）

设计要点（§9.1）：低置信时暂停的是**引擎的自动提交**，不是采集。
否则暂停期间打出的牌会彻底消失 —— 这是本模块刻意避免的失败模式。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from ..cards import Card
from ..layout.detect import detect_occupied_zones
from ..layout.model import LayoutModel
from ..recognition.classify import read_patch, vote_frames
from ..recognition.patch import CardPatch, extract_card_patches
from ..recognition.templates import TemplateLibrary
from ..recognition.types import RecognitionResult, ZoneRead
from .pending import PendingQueue
from .phash import dhash, same_content
from .ringbuffer import RingBuffer
from .types import (
    Event,
    FrameSnapshot,
    PendingItem,
    PlayEvent,
    TrickEndEvent,
    ZoneState,
)

# 连续稳定帧数。注意是**按新帧计数**（`app.tick_once` 在 grab() 返回 None 时直接
# 返回、不喂状态机），所以它对应的**墙钟时延取决于屏幕实际呈现率**：
#   基准机 46.7 FPS -> 约 171 ms；当前虚拟机 ~17 FPS -> 约 470 ms
# （原注释写「60Hz 下约 133ms」，两台机器上都对不上；实测见 README「实测数据」）
SETTLE_FRAMES = 8
SETTLE_DIFF_THRESHOLD = 2.0  # 灰度平均绝对差阈值
VOTE_FRAMES = 3              # 参与投票的帧数
THUMB_W, THUMB_H = 96, 48    # 环形缓冲/哈希用的缩略尺寸


@dataclass
class ZoneTracker:
    """单个出牌区的时序状态机。可独立测试。"""

    zone: str
    state: ZoneState = ZoneState.EMPTY
    stable: int = 0
    prev_gray: np.ndarray | None = None
    occupied: bool = False
    count: int = 0
    cycle_hashes: list[int] = field(default_factory=list)
    patches_history: deque[list[CardPatch]] = field(
        default_factory=lambda: deque(maxlen=VOTE_FRAMES))

    def reset_cycle(self) -> None:
        self.cycle_hashes.clear()
        self.patches_history.clear()

    def update(self, *, thumb: np.ndarray | None, diff: float | None,
               occupied: bool, count: int,
               patches: list[CardPatch]) -> list[str]:
        """喂一帧，返回信号列表：`settled` / `cleared`。"""
        signals: list[str] = []

        if not occupied:
            if self.state is not ZoneState.EMPTY:
                self.state = ZoneState.EMPTY
                self.reset_cycle()
                signals.append("cleared")
            self.stable = 0
            self.occupied = False
            self.count = 0
            if thumb is not None:
                self.prev_gray = thumb
            return signals

        is_stable = diff is not None and diff < SETTLE_DIFF_THRESHOLD

        if self.state is ZoneState.EMPTY:
            self.state = ZoneState.ENTERING
            self.stable = 0
            self.reset_cycle()
        elif self.state is ZoneState.SETTLED and not is_stable:
            # 已摆定后又变了 —— 回到 ENTERING，重新判稳
            self.state = ZoneState.ENTERING
            self.stable = 0

        self.stable = self.stable + 1 if is_stable else 0
        self.occupied = True
        self.count = count
        self.patches_history.append(list(patches))
        if thumb is not None:
            self.prev_gray = thumb

        if self.state is ZoneState.ENTERING and self.stable >= SETTLE_FRAMES:
            self.state = ZoneState.SETTLED
            signals.append("settled")
        return signals


class EventPipeline:
    """把帧序列转换为出牌/墩结束/待确认事件。"""

    def __init__(
        self,
        model: LayoutModel,
        *,
        library: TemplateLibrary | None = None,
        pending: PendingQueue | None = None,
        ring: RingBuffer | None = None,
        settle_frames: int = SETTLE_FRAMES,
        settle_threshold: float = SETTLE_DIFF_THRESHOLD,
        vote_frames: int = VOTE_FRAMES,
        min_margin: float = 0.05,
        min_agreement: float = 0.5,
        zone_to_seat: dict[str, int] | None = None,
    ) -> None:
        self.model = model
        self.library = library
        self.pending = pending
        self.ring = ring
        self.settle_frames = settle_frames
        self.settle_threshold = settle_threshold
        self.min_margin = min_margin
        self.min_agreement = min_agreement
        self.zone_to_seat = dict(zone_to_seat or {})

        self.trackers: dict[str, ZoneTracker] = {
            name: ZoneTracker(zone=name) for name in model.zones}
        self.trick_index = 0
        self._trick_plays: list[PlayEvent] = []
        self._had_occupancy = False

    # ---------- 主入口 ----------

    def on_frame(self, frame_bgr: np.ndarray, ts: float) -> list[Event]:
        """喂一帧。返回本帧产生的事件（可能为空）。"""
        events: list[Event] = []

        occupancy = detect_occupied_zones(frame_bgr, self.model)
        patches = extract_card_patches(frame_bgr, self.model, occupancy)
        thumbs = self._thumbs(frame_bgr)

        for name, tr in self.trackers.items():
            thumb = thumbs.get(name)
            diff = None
            if (thumb is not None and tr.prev_gray is not None
                    and tr.prev_gray.shape == thumb.shape):
                diff = float(cv2.absdiff(thumb, tr.prev_gray).mean())
            info = occupancy.get(name)
            signals = tr.update(
                thumb=thumb,
                diff=diff,
                occupied=info is not None,
                count=int(info["count"]) if info else 0,
                patches=patches.get(name, []),
            )
            if "settled" in signals:
                events.extend(self._on_settled(name, tr, frame_bgr, ts))

        if self.ring is not None:
            self.ring.push(FrameSnapshot(
                ts=ts,
                zone_thumbs=thumbs,
                zone_patches={z: [p.corner for p in pl] for z, pl in patches.items()},
                zone_count={z: len(pl) for z, pl in patches.items()},
            ))

        if any(t.occupied for t in self.trackers.values()):
            self._had_occupancy = True
        elif self._had_occupancy:
            # 全部 N 区都已清空 -> 本墩结束
            events.append(self._end_trick(ts))

        return events

    # ---------- 内部 ----------

    def _thumbs(self, frame: np.ndarray) -> dict[str, np.ndarray]:
        """各区缩略灰度图，用于差分、哈希与环形缓冲。"""
        h, w = frame.shape[:2]
        out: dict[str, np.ndarray] = {}
        for name, rect in self.model.zones.items():
            r = rect.clip(0, 0, w, h)
            if r.w <= 0 or r.h <= 0:
                continue
            seg = frame[r.y0:r.y1, r.x0:r.x1]
            out[name] = cv2.resize(cv2.cvtColor(seg, cv2.COLOR_BGR2GRAY),
                                   (THUMB_W, THUMB_H),
                                   interpolation=cv2.INTER_AREA)
        return out

    def _on_settled(self, zone: str, tr: ZoneTracker, frame: np.ndarray,
                    ts: float) -> list[Event]:
        thumb = tr.prev_gray
        if thumb is None:
            return []

        h = dhash(thumb)
        if tr.cycle_hashes and same_content(h, tr.cycle_hashes[-1]):
            # 内容未变 —— 是同一次显示，不重复计入
            return []

        events: list[Event] = []
        # 看门狗：一次占用周期内出现第二种内容 -> 前一手被覆盖，来不及读取
        if tr.cycle_hashes:
            item = self._enqueue_missed(zone, ts, frame)
            if item is not None:
                events.append(item)
        tr.cycle_hashes.append(h)

        cards, confidence, agreement = self._recognize(zone, tr)
        event = PlayEvent(
            zone=zone,
            cards=cards,
            count=tr.count,
            confidence=confidence,
            frame_agreement=agreement,
            trick_index=self.trick_index,
            frame_ts=ts,
        )

        if self.library is None:
            # 识别层未启用：只报告"该区出了几张牌"，不假装认出了牌，
            # 也不因此把每一手都塞进待确认队列（那会淹没队列）。
            self._trick_plays.append(event)
            events.append(event)
            return events

        uncertain = (cards is None
                     or confidence < self.min_margin
                     or agreement < self.min_agreement)
        if uncertain and self.pending is not None:
            item = self.pending.add_low_confidence(
                event, self.zone_to_seat.get(zone),
                evidence=self._evidence(frame, zone))
            events.append(item)
            # 未确认的出牌**不进入本墩的确定记录**，但事件本身必须流出
            return events

        self._trick_plays.append(event)
        events.append(event)
        return events

    def _recognize(self, zone: str, tr: ZoneTracker
                   ) -> tuple[tuple[Card, ...] | None, float, float]:
        if self.library is None or not tr.patches_history:
            # 识别层未就绪：只报告占用与张数，不假装认出了牌
            return None, 1.0, 1.0

        frames: list[RecognitionResult] = []
        for plist in tr.patches_history:
            reads = [read_patch(p, self.library) for p in plist]
            frames.append(RecognitionResult(zones={zone: ZoneRead(zone=zone, cards=reads)}))

        voted = vote_frames(frames)
        best = voted.zones.get(zone)
        if best is None or not best.cards:
            return None, voted.confidence, voted.frame_agreement
        resolved: list[Card] = []
        for r in best.cards:
            if r.card is None:
                # 有任一槽位未认出 -> 整手不认（宁可让你点一下）
                return None, voted.confidence, voted.frame_agreement
            resolved.append(r.card)
        return tuple(resolved), voted.confidence, voted.frame_agreement

    def _evidence(self, frame: np.ndarray, zone: str) -> np.ndarray | None:
        rect = self.model.zones.get(zone)
        if rect is None:
            return None
        h, w = frame.shape[:2]
        r = rect.expand(24, 24).clip(0, 0, w, h)
        if r.w <= 0 or r.h <= 0:
            return None
        return frame[r.y0:r.y1, r.x0:r.x1].copy()

    def _enqueue_missed(self, zone: str, ts: float, frame: np.ndarray
                        ) -> PendingItem | None:
        if self.pending is None:
            return None
        return self.pending.add_missed_play(
            zone, ts, self.trick_index,
            seat=self.zone_to_seat.get(zone),
            evidence=self._evidence(frame, zone))

    def _end_trick(self, ts: float) -> TrickEndEvent:
        event = TrickEndEvent(trick_index=self.trick_index, frame_ts=ts,
                              plays=tuple(self._trick_plays))
        self.trick_index += 1
        self._trick_plays = []
        self._had_occupancy = False
        return event

    # ---------- 只读状态 ----------

    def states(self) -> dict[str, ZoneState]:
        return {name: t.state for name, t in self.trackers.items()}

    def current_trick_plays(self) -> tuple[PlayEvent, ...]:
        return tuple(self._trick_plays)
