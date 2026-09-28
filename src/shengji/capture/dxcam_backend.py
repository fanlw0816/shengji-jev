"""Desktop Duplication（dxcam）后端 —— 主路径。

实测：单次 grab 无新帧 0.038ms、有变化 0.042ms；60FPS 下约 0.25% 单核；
屏幕无变化时返回 None。
"""

from __future__ import annotations

import time

from .base import Frame


class DxcamBackend:
    def __init__(self, output_idx: int = 0, output_color: str = "BGR") -> None:
        import dxcam  # 延迟导入，便于在无显卡环境测试其它部分

        self._cam = dxcam.create(output_idx=output_idx, output_color=output_color)
        if self._cam is None:
            raise RuntimeError(f"dxcam 无法创建输出 {output_idx}")
        self._output_idx = output_idx

    def grab(self, region: tuple[int, int, int, int] | None = None) -> Frame | None:
        img = self._cam.grab(region=region)
        if img is None:
            return None
        ts = time.perf_counter()
        h, w = img.shape[:2]
        rg = region if region is not None else (0, 0, w, h)
        return Frame(image=img, ts=ts, region=rg)

    def close(self) -> None:
        try:
            self._cam.release()
        except Exception:
            pass
