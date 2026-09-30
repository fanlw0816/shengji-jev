"""Windows 窗口定位。所有坐标一律用『客户区』，不用窗口外框。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.windll.user32

# ⚠️ **必须显式声明 argtypes / restype**（2026-09-30 修）。
#
# HWND 在 64 位下是指针，实际句柄值经常大于 2^31。不声明时 ctypes 按默认的
# C `int` 转换实参 → 抛 `OverflowError: int too long to convert`。
# 而 `find_windows_by_title` 的枚举回调是被 ctypes 调用的，**回调里的异常被吞掉**，
# 于是那个窗口既不进结果也不报错 —— 表现为「找游戏窗口有时找得到有时找不到」，
# 取决于句柄数值，重现性极差（设计原则「失败要可见」最忌讳的一类）。
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL

_DPI_AWARE_SET = False


def enable_per_monitor_dpi_awareness() -> bool:
    """声明 Per-Monitor DPI Aware，避免逻辑/物理坐标错位。

    必须在创建任何窗口前调用。重复调用返回 False 而不抛异常。
    """
    global _DPI_AWARE_SET
    if _DPI_AWARE_SET:
        return False
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            return False
    _DPI_AWARE_SET = True
    return True


@dataclass(frozen=True)
class ClientRect:
    """窗口客户区在屏幕上的位置（物理像素）。"""

    hwnd: int
    x: int
    y: int
    w: int
    h: int
    title: str

    @property
    def origin(self) -> tuple[int, int]:
        return (self.x, self.y)

    @property
    def size(self) -> tuple[int, int]:
        return (self.w, self.h)


def _client_rect_of(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x, pt.y, w, h)


def _title_of(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_windows_by_title(substr: str, visible_only: bool = True) -> list[ClientRect]:
    """枚举顶层窗口，返回标题包含 substr 的窗口客户区列表。

    substr 为空字符串时返回所有可见顶层窗口。
    """
    out: list[ClientRect] = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        title = _title_of(hwnd)
        if substr and substr not in title:
            return True
        cr = _client_rect_of(hwnd)
        if cr is None:
            return True
        x, y, w, h = cr
        out.append(ClientRect(hwnd=int(hwnd), x=x, y=y, w=w, h=h, title=title))
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    return out


# 待 Phase 0 实测确认后填入真实类名/标题关键字。
# 不要凭猜测写死；用 find_windows_by_title("") 现场枚举。
CANDIDATE_TITLE_KEYWORDS: tuple[str, ...] = ("QQ游戏", "升级", "拖拉机", "欢乐")


def find_game_window() -> ClientRect | None:
    """按候选关键字查找游戏窗口，返回面积最大的一个。"""
    found: list[ClientRect] = []
    for kw in CANDIDATE_TITLE_KEYWORDS:
        found.extend(find_windows_by_title(kw))
    if not found:
        return None
    return max(found, key=lambda r: r.w * r.h)
