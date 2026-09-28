import numpy as np

from shengji.capture.base import Frame
from shengji.capture.factory import (
    DegradedMode,
    NullBackend,
    build_backend,
    degraded_poll_interval,
)


def test_frame_records_timestamp_and_region():
    f = Frame(image=np.zeros((4, 4, 3), dtype=np.uint8), ts=1.5, region=(0, 0, 4, 4))
    assert f.ts == 1.5
    assert f.region == (0, 0, 4, 4)


def test_null_backend_returns_none():
    b = NullBackend()
    assert b.grab((0, 0, 10, 10)) is None
    b.close()


def test_build_backend_falls_back_to_null_when_all_fail():
    """两种真实后端都不可用时应降级到 NullBackend，而不是抛异常。"""
    b, mode = build_backend(allow_dxcam=False, allow_mss=False)
    assert isinstance(b, NullBackend)
    assert mode is DegradedMode.NONE


def test_degraded_poll_interval_matches_spec():
    """mss 单帧约 17.7ms（约一个核心），必须降频，不能维持 60Hz。"""
    assert degraded_poll_interval(DegradedMode.DXCAM) == 1 / 60
    assert degraded_poll_interval(DegradedMode.MSS) == 1 / 15
    assert degraded_poll_interval(DegradedMode.NONE) == 1 / 5


def test_build_backend_never_raises():
    """无论环境如何，build_backend 都必须返回一个可用对象。"""
    b, mode = build_backend(output_idx=0)
    assert mode in (DegradedMode.DXCAM, DegradedMode.MSS, DegradedMode.NONE)
    assert hasattr(b, "grab") and hasattr(b, "close")
    b.close()
