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
#
# ⚠️ 1/15 这个值保留不改，理由有三（2026-09-29 复测后定）：
#   1. 降级本身就是异常路径 —— dxcam 挂了才会走到这里，此时首要目标是
#      "还能跑、不雪崩"，而不是榨性能。把频率往上调只会更快地把核吃满。
#   2. 单帧成本是硬约束：基准机 17.7 ms（= 单核 27%），本机虚拟显示上
#      60.3 ms（= 单核 90%）。本机再怎么调频也压不到 60 Hz —— 调高无益。
#   3. 该值有外部约定：spec §9.2 与 `plans/2026-09-28-capture-and-layout.md`
#      Task 11 的测试都断言 1/15，改动会同时破坏文档与测试。
#
# 本机实际表现已在 README「实测数据」标注：降级模式跑 15 Hz 要吃掉一个核，
# 属"能用但别指望"的兜底，而非可用路径。
_POLL = {
    DegradedMode.DXCAM: 1 / 60,   # 60Hz，约 0.25% 单核
    DegradedMode.MSS: 1 / 15,     # 15Hz，因单帧约占一个核心（见上）
    DegradedMode.NONE: 1 / 5,     # 5Hz，仅维持存活探测
}


def degraded_poll_interval(mode: DegradedMode) -> float:
    """返回该降级等级下的建议采样间隔（秒）。

    数值来自实测定标，不要凭手感调；改动前先读模块顶部的保留理由。
    """
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
