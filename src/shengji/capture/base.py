"""采集后端抽象。上层只依赖本模块的 Protocol。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class Frame:
    """一帧画面及其元数据。"""

    image: np.ndarray
    ts: float
    region: tuple[int, int, int, int]


@runtime_checkable
class CaptureBackend(Protocol):
    """采集后端。

    grab(region) 在 region 为 None 时抓整个输出；
    屏幕无变化时**应返回 None**（Desktop Duplication 的原生语义），
    这是零成本变化侦测的基础。
    """

    def grab(self, region: tuple[int, int, int, int] | None) -> Frame | None: ...

    def close(self) -> None: ...
