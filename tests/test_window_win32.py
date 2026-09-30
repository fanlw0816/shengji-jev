import pytest

from shengji.window.win32 import (
    ClientRect,
    enable_per_monitor_dpi_awareness,
    find_windows_by_title,
    user32,
)


def test_enable_dpi_awareness_is_idempotent():
    """重复调用不应抛异常（进程 DPI 感知只能设置一次）。"""
    assert enable_per_monitor_dpi_awareness() in (True, False)
    assert enable_per_monitor_dpi_awareness() in (True, False)


def test_find_windows_returns_list_for_nonexistent_title():
    res = find_windows_by_title("__不存在的窗口标题__")
    assert isinstance(res, list)
    assert res == []


def test_client_rect_dataclass():
    r = ClientRect(hwnd=1, x=10, y=20, w=800, h=600, title="t")
    assert r.origin == (10, 20)
    assert r.size == (800, 600)


def test_enumerate_all_windows_or_skip():
    """本环境至少应能找到一些可见窗口；找不到就跳过（无桌面环境）。"""
    res = find_windows_by_title("")
    if not res:
        pytest.skip("当前环境无可见顶层窗口")
    assert all(isinstance(r, ClientRect) for r in res)
    assert all(r.w > 0 and r.h > 0 for r in res)


# ---------- ctypes 句柄宽度（2026-09-30 修）----------
#
# HWND 在 64 位下是指针，实际值经常大于 2^31。`user32` 上不声明 argtypes 时
# ctypes 按 C `int` 转换实参 → OverflowError。而枚举回调里的异常会被 ctypes **吞掉**，
# 于是那个窗口既不进结果也不报错 —— 「找游戏窗口有时找得到有时找不到」。
# 这两条一起钉住：取值正确 + 声明不被删掉。

def test_handle_larger_than_int32_does_not_raise():
    big = 0x7FFF_FFFF_FFFF          # 超过 C int、但在指针范围内
    assert user32.IsWindowVisible(big) in (0, 1)


def test_hwnd_argtypes_are_declared():
    for name in ("IsWindowVisible", "GetWindowTextLengthW", "GetWindowTextW",
                 "GetClientRect", "ClientToScreen"):
        fn = getattr(user32, name)
        assert fn.argtypes, f"{name} 未声明 argtypes，大句柄会静默丢窗口"
        assert fn.restype is not None, f"{name} 未声明 restype"
