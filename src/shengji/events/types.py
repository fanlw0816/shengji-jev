"""事件层的载荷类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Union

import numpy as np

from ..cards import Card


class ZoneState(str, Enum):
    """单个出牌区的时序状态（设计文档 §4.3）。

    注意与"采样档位"无关：采样侧只有「采集中 / 挂起」两档，
    这里的三个状态属于**区域状态机**。
    """

    EMPTY = "empty"          # 该区无牌
    ENTERING = "entering"    # 检测到变化，动画进行中
    SETTLED = "settled"      # 连续若干帧差异低于阈值，牌已摆定


@dataclass(frozen=True)
class FrameSnapshot:
    """一帧的轻量快照，供环形缓冲保存与回溯重算。"""

    ts: float
    zone_thumbs: dict[str, np.ndarray]                 # 区缩略灰度，用于差分与哈希
    zone_patches: dict[str, list[np.ndarray]] = field(default_factory=dict)
    zone_count: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class PlayEvent:
    """一次出牌事件。"""

    zone: str
    cards: tuple[Card, ...] | None    # None = 未识别（识别层未就绪）
    count: int                        # 该区本手牌张数（即使未识别也有值）
    confidence: float
    frame_agreement: float
    trick_index: int
    frame_ts: float
    evidence_path: str | None = None

    def labels(self) -> list[str]:
        if self.cards is None:
            return ["?"] * self.count
        return [c.label() for c in self.cards]


@dataclass(frozen=True)
class TrickEndEvent:
    """一墩结束（全部 N 区同帧清空）。"""

    trick_index: int
    frame_ts: float
    plays: tuple[PlayEvent, ...]


@dataclass(frozen=True)
class PendingItem:
    """待确认项（设计文档 §5.1 / §9.1）。

    每个未纠正的事件都是**显式条目**，绝不静默丢弃。
    """

    frame_ts: float
    reason: Literal["low_confidence", "missed_play"]
    zone: str
    seat: int | None
    proposed_cards: tuple[Card, ...]
    confidence: float
    frame_agreement: float
    evidence_path: str
    trick_index: int


Event = Union[PlayEvent, TrickEndEvent, PendingItem]
