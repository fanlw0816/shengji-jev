"""环形缓冲：常驻最近若干秒的帧快照（设计文档 §4.5）。

用途：识别置信度低时**倒回去重算**，而不是依赖"当时恰好抓到的那一帧"。
因为牌只显示几秒，而人工纠正往往超过这个时间。
"""

from __future__ import annotations

from collections import deque

from .types import FrameSnapshot


class RingBuffer:
    """按时间窗口保存帧快照的定长环形缓冲。"""

    def __init__(self, capacity: int = 180) -> None:
        if capacity <= 0:
            raise ValueError("capacity 必须为正")
        self.capacity = capacity
        self._buf: deque[FrameSnapshot] = deque(maxlen=capacity)

    def __len__(self) -> int:
        return len(self._buf)

    def push(self, snap: FrameSnapshot) -> None:
        self._buf.append(snap)

    def clear(self) -> None:
        self._buf.clear()

    def latest(self) -> FrameSnapshot | None:
        return self._buf[-1] if self._buf else None

    def last_n(self, n: int) -> list[FrameSnapshot]:
        """最近 n 帧，按时间升序。"""
        if n <= 0:
            return []
        return list(self._buf)[-n:]

    def since(self, ts: float) -> list[FrameSnapshot]:
        """返回 ts 之后（含）的所有快照，按时间升序。"""
        return [s for s in self._buf if s.ts >= ts]

    def window(self, ts_from: float, ts_to: float) -> list[FrameSnapshot]:
        """时间窗内的快照，按时间升序。"""
        return [s for s in self._buf if ts_from <= s.ts <= ts_to]
