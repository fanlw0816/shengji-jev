import numpy as np
import pytest

from shengji.events.ringbuffer import RingBuffer
from shengji.events.types import FrameSnapshot


def _snap(ts: float) -> FrameSnapshot:
    return FrameSnapshot(ts=ts, zone_thumbs={"top": np.zeros((4, 4), np.uint8)})


def test_push_and_len():
    rb = RingBuffer(capacity=5)
    assert len(rb) == 0
    rb.push(_snap(0.0))
    assert len(rb) == 1


def test_capacity_evicts_oldest():
    rb = RingBuffer(capacity=3)
    for i in range(5):
        rb.push(_snap(float(i)))
    assert len(rb) == 3
    assert [s.ts for s in rb.last_n(3)] == [2.0, 3.0, 4.0]


def test_latest():
    rb = RingBuffer(capacity=3)
    assert rb.latest() is None
    rb.push(_snap(1.0))
    rb.push(_snap(2.0))
    assert rb.latest().ts == 2.0


def test_last_n_returns_ascending():
    rb = RingBuffer(capacity=10)
    for i in range(6):
        rb.push(_snap(float(i)))
    assert [s.ts for s in rb.last_n(3)] == [3.0, 4.0, 5.0]


def test_last_n_edge_cases():
    rb = RingBuffer(capacity=3)
    rb.push(_snap(1.0))
    assert rb.last_n(0) == []
    assert rb.last_n(-1) == []
    assert [s.ts for s in rb.last_n(99)] == [1.0]


def test_since():
    rb = RingBuffer(capacity=10)
    for i in range(5):
        rb.push(_snap(float(i)))
    assert [s.ts for s in rb.since(2.0)] == [2.0, 3.0, 4.0]
    assert rb.since(99.0) == []


def test_window():
    """按时间窗回溯 —— 这是"置信度低时倒回去重算"的基础。"""
    rb = RingBuffer(capacity=10)
    for i in range(10):
        rb.push(_snap(float(i)))
    assert [s.ts for s in rb.window(3.0, 6.0)] == [3.0, 4.0, 5.0, 6.0]


def test_clear():
    rb = RingBuffer(capacity=3)
    rb.push(_snap(1.0))
    rb.clear()
    assert len(rb) == 0


def test_rejects_non_positive_capacity():
    with pytest.raises(ValueError):
        RingBuffer(capacity=0)
    with pytest.raises(ValueError):
        RingBuffer(capacity=-1)


def test_stores_patches_payload():
    rb = RingBuffer(capacity=3)
    rb.push(FrameSnapshot(ts=1.0, zone_thumbs={},
                          zone_patches={"top": [np.zeros((28, 16, 3), np.uint8)]},
                          zone_count={"top": 2}))
    got = rb.latest()
    assert got.zone_count["top"] == 2
    assert len(got.zone_patches["top"]) == 1
