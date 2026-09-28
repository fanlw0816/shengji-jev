"""识别层的载荷类型。"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..cards import Card


@dataclass(frozen=True)
class CardRead:
    """单张牌的识别结果。"""

    card: Card | None          # None = 未识别出
    rank_score: float          # 点数分类器最佳相关度
    suit_score: float          # 花色分类器最佳相关度
    margin: float              # 最佳与次佳的差距，越小越不可靠


@dataclass
class ZoneRead:
    """一个出牌区的识别结果。"""

    zone: str
    cards: list[CardRead] = field(default_factory=list)
    confidence: float = 0.0        # = min(每张的 margin 归一化后)
    rank_agreement: float = 0.0    # 跨帧投票的一致率

    @property
    def ok(self) -> bool:
        return all(c.card is not None for c in self.cards) and bool(self.cards)

    def labels(self) -> list[str]:
        return [c.card.label() if c.card else "?" for c in self.cards]


@dataclass
class RecognitionResult:
    """一帧的整盘识别结果。"""

    zones: dict[str, ZoneRead] = field(default_factory=dict)
    confidence: float = 0.0        # = min(各区 confidence)
    frame_agreement: float = 1.0   # 跨帧投票一致率

    @property
    def ok(self) -> bool:
        return all(z.ok for z in self.zones.values())
