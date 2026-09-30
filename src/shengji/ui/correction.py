"""纠正面板的呈现模型与选择状态（纯逻辑，与 Qt 无关）。

设计文档 §9.1 / §14.1：低置信或漏抓的一手牌进入待确认队列，
用户在交互模式（`Ctrl+Alt+L` 关闭鼠标穿透）下**点选正确的牌**补录。

本模块只做两件事，都是纯数据 + 纯函数：

- `PendingRow`   —— 一项待确认转成可直接渲染的一行
- `CardSelection` —— "用户在牌网格上点出了哪几张"这个编辑状态

Qt 部件只负责把字符串画出来、把点击喂进 `CardSelection`。
这样"点选逻辑"可以纯单测覆盖，不必起 GUI。

**一次只处理队首一项**（`current_row`）：队列本身按 `frame_ts` 有序，
逐项处理既符合"按时间序重放"的语义，也让面板不必处理"多项并发编辑"。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..cards import Card
from ..events.pending import PendingQueue
from ..events.types import PendingItem
from .viewmodel import ZONE_LABELS

REASON_LABELS: dict[str, str] = {
    "low_confidence": "低置信",
    "missed_play": "漏抓",
}

#: 牌网格的点数列（2..A）与花色行（♠♥♦♣）
GRID_RANKS: tuple[int, ...] = tuple(range(2, 15))
GRID_SUITS: tuple[int, ...] = (0, 1, 2, 3)


@dataclass(frozen=True)
class PendingRow:
    """一项待确认的呈现数据。"""

    index: int                  # 在队列中的位置（从 0 起）
    trick_index: int
    zone: str
    seat: int | None
    reason: str
    proposed: tuple[Card, ...]

    @property
    def zone_label(self) -> str:
        return ZONE_LABELS.get(self.zone, self.zone)

    @property
    def reason_label(self) -> str:
        return REASON_LABELS.get(self.reason, self.reason)

    @property
    def can_accept_proposed(self) -> bool:
        """能否直接采纳识别结果 —— 没有候选（漏抓）时只能人工录入。"""
        return bool(self.proposed)

    def headline(self) -> str:
        return (f"第 {self.trick_index + 1} 墩 · {self.zone_label}"
                f" · {self.reason_label}")

    def proposed_text(self) -> str:
        if not self.proposed:
            return "无候选，请人工录入"
        return "识别：" + " ".join(c.label() for c in self.proposed)

    def text(self) -> str:
        return f"#{self.index + 1} {self.headline()} — {self.proposed_text()}"


def pending_rows(queue: PendingQueue) -> list[PendingRow]:
    """把待确认队列转成呈现行（保持 `frame_ts` 顺序）。"""
    return [
        PendingRow(index=i, trick_index=item.trick_index, zone=item.zone,
                   seat=item.seat, reason=item.reason,
                   proposed=tuple(item.proposed_cards))
        for i, item in enumerate(queue.items)
    ]


def current_row(queue: PendingQueue) -> PendingRow | None:
    """队首待确认项 —— 面板一次只编辑它。"""
    rows = pending_rows(queue)
    return rows[0] if rows else None


def row_for(queue: PendingQueue, item: PendingItem) -> PendingRow | None:
    """按**身份**找到某一项对应的呈现行（不依赖 dataclass 相等性）。"""
    for i, candidate in enumerate(queue.items):
        if candidate is item:
            return PendingRow(index=i, trick_index=item.trick_index,
                              zone=item.zone, seat=item.seat,
                              reason=item.reason,
                              proposed=tuple(item.proposed_cards))
    return None


@dataclass
class CardSelection:
    """用户在牌网格上点出来的选择。

    同一张牌在牌堆里有多张（两副牌 2 张、三副牌 3 张），所以每格记的是**计数**
    而不是布尔标记：点击按 `0 → 1 → … → max_per_card → 0` 循环。
    用布尔标记的话，两副牌里的第二张永远录不进去 —— 而那正是记牌要的精度。
    """

    decks: int = 2
    _counts: Counter[Card] = field(default_factory=Counter, repr=False)

    @property
    def max_per_card(self) -> int:
        return max(1, int(self.decks))

    def count(self, card: Card) -> int:
        return self._counts.get(card, 0)

    def is_selected(self, card: Card) -> bool:
        return self._counts.get(card, 0) > 0

    def toggle(self, card: Card) -> int:
        """循环切换一张牌的计数，返回切换后的值。"""
        n = self._counts.get(card, 0)
        n = 0 if n >= self.max_per_card else n + 1
        if n:
            self._counts[card] = n
        else:
            self._counts.pop(card, None)
        return n

    def add(self, card: Card, n: int = 1) -> int:
        """直接设为某个计数（上限钳制），返回最终值。"""
        n = max(0, min(int(n), self.max_per_card))
        if n:
            self._counts[card] = n
        else:
            self._counts.pop(card, None)
        return n

    def set_cards(self, cards: Sequence[Card]) -> None:
        """用一组牌整体替换当前选择（用于"采纳识别结果"）。"""
        self.clear()
        for c in cards:
            self.add(c, self.count(c) + 1)

    def clear(self) -> None:
        self._counts.clear()

    @property
    def total(self) -> int:
        return sum(self._counts.values())

    @property
    def is_empty(self) -> bool:
        return not self._counts

    def cards(self) -> list[Card]:
        """展开成有序牌列表（含重复张）。

        排序用 `Card` 自身的次序（点数 → 花色 → 王），不保留点击顺序 ——
        这样同样的选择永远给出同样的序列，便于测试与复现。
        """
        return sorted(self._counts.elements())

    def text(self) -> str:
        if self.is_empty:
            return "未选牌"
        return "已选：" + " ".join(c.label() for c in self.cards())
