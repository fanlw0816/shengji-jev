import pytest

from shengji.window.win32 import (
    ClientRect,
    enable_per_monitor_dpi_awareness,
    find_windows_by_title,
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
