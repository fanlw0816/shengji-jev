"""采集后端选择与降级。

注意：mss 与 dxcam 语义不同 —— mss 永远返回画面（无变化也返回），
dxcam 无变化时返回 None。上层必须按「None 即无变化」处理，
因此 mss 降级时需要上层补充差分。
"""

from __future__ import annotations

from enum import Enum

from .base import CaptureBackend, Frame


class DegradedMode(str, Enum):
    DXCAM = "dxcam"    # 主路径
    MSS = "mss"        # 降级：BitBlt
    NONE = "none"      # 无可用后端


class NullBackend:
    """占位后端：永远没有画面。用于让上层在无采集能力时仍可启动并提示。"""

    def grab(self, region: tuple[int, int, int, int] | None = None) -> Frame | None:
        return None

    def close(self) -> None:
        pass


# 实测依据（spec §4.2 / §9.2）
_POLL = {
    DegradedMode.DXCAM: 1 / 60,   # 60Hz，约 0.25% 单核
    DegradedMode.MSS: 1 / 15,     # 15Hz，因单帧约占一个核心
    DegradedMode.NONE: 1 / 5,     # 5Hz，仅维持存活探测
}


def degraded_poll_interval(mode: DegradedMode) -> float:
    return _POLL[mode]


def build_backend(
    output_idx: int = 0,
    allow_dxcam: bool = True,
    allow_mss: bool = True,
) -> tuple[CaptureBackend, DegradedMode]:
    """按 dxcam -> mss -> Null 顺序尝试，返回后端与所处降级等级。"""
    if allow_dxcam:
        try:
            from .dxcam_backend import DxcamBackend

            return DxcamBackend(output_idx=output_idx), DegradedMode.DXCAM
        except Exception:
            pass
    if allow_mss:
        try:
            from .mss_backend import MssBackend

            return MssBackend(), DegradedMode.MSS
        except Exception:
            pass
    return NullBackend(), DegradedMode.NONE
