"""BitBlt（mss）后端 —— 降级路径。

实测：整屏单帧约 17.7ms（≈一个完整核心），比 dxcam 慢约 400 倍，
因此必须配合降频使用（见 factory.degraded_poll_interval）。
"""

from __future__ import annotations

import time

import numpy as np

from .base import Frame


class MssBackend:
    def __init__(self, monitor_index: int = 1) -> None:
        import mss  # 延迟导入

        self._sct = mss.MSS()
        mons = self._sct.monitors
        if monitor_index >= len(mons):
            monitor_index = 1 if len(mons) > 1 else 0
        self._mon = mons[monitor_index]
        self._monitor_index = monitor_index

    def grab(self, region: tuple[int, int, int, int] | None = None) -> Frame | None:
        if region is None:
            x, y, w, h = (self._mon["left"], self._mon["top"],
                          self._mon["width"], self._mon["height"])
        else:
            x, y, w, h = region
        shot = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        img = np.asarray(shot)[:, :, :3]  # 去掉 alpha，得到 BGR
        return Frame(image=img, ts=time.perf_counter(), region=(x, y, w, h))

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
