"""待确认队列（设计文档 §9.1）。

**这是"防止静默丢牌"的核心。** 低置信度时暂停的是**引擎的自动提交**，
而不是采集与状态机 —— 后者必须继续运行，否则暂停期间打出的牌会彻底消失。

规则：
- 事件按 `frame_ts` 排序入队
- **证据帧在入队时立即落盘**，不依赖只有几秒的环形缓冲
- 每个未纠正事件都是队列中的显式条目，绝不静默丢弃
- `missed_play` 与 `low_confidence` 共用同一队列，仅 `reason` 不同
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from ..imaging import imwrite_unicode
from .types import PendingItem, PlayEvent


class PendingQueue:
    """有序的待确认队列，入队即落盘证据。"""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else None
        self._items: list[PendingItem] = []
        if self.root is not None:
            self.root.mkdir(parents=True, exist_ok=True)
            self._manifest = self.root / "pending.jsonl"

    # ---------- 查询 ----------

    def __len__(self) -> int:
        return len(self._items)

    @property
    def items(self) -> tuple[PendingItem, ...]:
        return tuple(self._items)

    @property
    def empty(self) -> bool:
        return not self._items

    def next(self) -> PendingItem | None:
        """按时间序取出最早的一项（不删除）。"""
        return self._items[0] if self._items else None

    # ---------- 入队 ----------

    def add(self, item: PendingItem) -> PendingItem:
        """入队并保持按 frame_ts 升序。"""
        self._items.append(item)
        self._items.sort(key=lambda x: x.frame_ts)
        self._append_manifest(item)
        return item

    def add_low_confidence(self, event: PlayEvent, seat: int | None,
                           evidence: np.ndarray | None = None) -> PendingItem:
        """登记一个低置信事件。evidence 为 None 时也要入队（不因缺图而丢弃）。"""
        path = self._save_evidence(event.zone, event.frame_ts, event.trick_index,
                                   evidence)
        return self.add(PendingItem(
            frame_ts=event.frame_ts,
            reason="low_confidence",
            zone=event.zone,
            seat=seat,
            proposed_cards=event.cards or (),
            confidence=event.confidence,
            frame_agreement=event.frame_agreement,
            evidence_path=path,
            trick_index=event.trick_index,
        ))

    def add_missed_play(self, zone: str, frame_ts: float, trick_index: int,
                        seat: int | None = None,
                        evidence: np.ndarray | None = None) -> PendingItem:
        """登记一次"一闪而过"的漏抓。"""
        path = self._save_evidence(zone, frame_ts, trick_index, evidence)
        return self.add(PendingItem(
            frame_ts=frame_ts,
            reason="missed_play",
            zone=zone,
            seat=seat,
            proposed_cards=(),
            confidence=0.0,
            frame_agreement=0.0,
            evidence_path=path,
            trick_index=trick_index,
        ))

    # ---------- 纠正 ----------

    def resolve(self, item: PendingItem) -> bool:
        """标记为已处理并移出队列。返回是否确实移除了。"""
        try:
            self._items.remove(item)
            return True
        except ValueError:
            return False

    def resolve_index(self, index: int) -> PendingItem | None:
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def clear(self) -> None:
        self._items.clear()

    # ---------- 内部 ----------

    def _save_evidence(self, zone: str, ts: float, trick_index: int,
                       evidence: np.ndarray | None) -> str:
        if self.root is None or evidence is None:
            return ""
        name = f"pending_trick{trick_index:03d}_{zone}_{ts:.3f}.png"
        imwrite_unicode(self.root / name, evidence)
        return name

    def _append_manifest(self, item: PendingItem) -> None:
        if self.root is None:
            return
        rec = {
            "frame_ts": item.frame_ts,
            "reason": item.reason,
            "zone": item.zone,
            "seat": item.seat,
            "trick_index": item.trick_index,
            "proposed_cards": [c.code() for c in item.proposed_cards],
            "confidence": item.confidence,
            "frame_agreement": item.frame_agreement,
            "evidence_path": item.evidence_path,
        }
        with (self.root / "pending.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
