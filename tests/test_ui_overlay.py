"""悬浮窗测试（offscreen 平台）。

Qt 部件只做渲染，因此这里断言的是"标签内容与窗口行为"，
业务判断留在 viewmodel 与 session 的测试里。
"""

import pytest
from PySide6.QtCore import Qt

from shengji.cards import Card
from shengji.engine.trump import TrumpInfo
from shengji.events.types import PendingItem, PlayEvent
from shengji.session import SessionState
from shengji.ui.overlay import OverlayWindow, default_position, window_flags
from shengji.ui.viewmodel import build_view

pytest.importorskip("PySide6")

S, H = 0, 1


@pytest.fixture
def overlay(qtbot):
    w = OverlayWindow()
    qtbot.addWidget(w)
    return w


# ---------- 窗口标志（纯函数，不需要开窗）----------

def test_window_flags_are_transparent_by_default():
    """默认必须鼠标穿透，否则会挡住游戏点击。"""
    flags = window_flags(interactive=False)
    assert flags & Qt.WindowTransparentForInput
    assert flags & Qt.FramelessWindowHint
    assert flags & Qt.WindowStaysOnTopHint
    assert flags & Qt.Tool


def test_window_flags_drop_transparency_in_interactive_mode():
    flags = window_flags(interactive=True)
    assert not (flags & Qt.WindowTransparentForInput)
    assert flags & Qt.WindowStaysOnTopHint


def test_default_position_is_inside_screen():
    x, y = default_position(2560, 1440)
    assert x >= 0 and y >= 0
    assert x < 2560 and y < 1440


# ---------- 初始状态 ----------

def test_starts_non_interactive(overlay):
    assert overlay.interactive is False
    assert overlay.windowFlags() & Qt.WindowTransparentForInput


def test_toggle_interactive_flips_and_back(overlay):
    assert overlay.toggle_interactive() is True
    assert not (overlay.windowFlags() & Qt.WindowTransparentForInput)
    assert overlay.toggle_interactive() is False
    assert overlay.windowFlags() & Qt.WindowTransparentForInput


def test_apply_interactive_is_idempotent(overlay):
    overlay.apply_interactive(True)
    overlay.apply_interactive(True)
    assert overlay.interactive is True


# ---------- 渲染 ----------

def test_set_view_renders_status_and_trick(overlay):
    s = SessionState(trump=TrumpInfo(kind="suit", suit=S, level_rank=2))
    overlay.set_view(build_view(s))
    assert "运行中" in overlay.status_label.text()
    assert "第 1 墩" in overlay.trick_label.text()


def test_set_view_renders_remaining_grid(overlay):
    overlay.set_view(build_view(SessionState()))
    texts = overlay.remaining_texts()
    assert len(texts) == 5, "四门花色 + 大小王"
    assert len(texts[0]) == 13, "一行 13 个点数"
    # 未出现任何牌时，每个点数应显示满副张数
    assert all("2" in t for t in texts[0])


def test_set_view_renders_seats(overlay):
    s = SessionState()
    s.points["top"] = 25
    overlay.set_view(build_view(s))
    text = overlay.seats_label.text()
    assert "自己" in text and "对家" in text
    assert "25" in text


def test_pending_label_hidden_when_no_pending(overlay):
    overlay.set_view(build_view(SessionState()))
    assert overlay.pending_label.isVisible() is False
    assert overlay.pending_label.text() == ""


def test_pending_label_shown_and_warns(overlay):
    s = SessionState(pending_count=2, low_confidence_count=1, missed_count=1)
    overlay.set_view(build_view(s))
    assert "待确认 2" in overlay.pending_label.text()
    assert "warn" not in overlay.pending_label.styleSheet()  # 存的是颜色值
    assert "#ffc857" in overlay.pending_label.styleSheet()


def test_status_colour_reflects_level(overlay):
    overlay.set_view(build_view(SessionState(paused=True)))
    assert "#ffc857" in overlay.status_label.styleSheet()
    overlay.set_view(build_view(SessionState(degraded_mode="none")))
    assert "#ff6b6b" in overlay.status_label.styleSheet()
    overlay.set_view(build_view(SessionState()))
    assert "#7bd88f" in overlay.status_label.styleSheet()


def test_footer_lists_hotkeys(overlay):
    overlay.set_view(build_view(SessionState()))
    assert "Ctrl+Alt+L" in overlay.footer_label.text()
    assert "Ctrl+Alt+P" in overlay.footer_label.text()


def test_remaining_grid_is_rebuilt_not_appended(overlay):
    """重复 set_view 不应不断累积标签（那会让窗口无限变宽）。

    结构是 4 行花色 × （1 个花色标签 + 13 个点数）+ 1 行王 × （1 + 2）。
    """
    expected = 5 + 4 * 13 + 2
    overlay.set_view(build_view(SessionState()))
    first = overlay.remaining_grid.count()
    for _ in range(3):
        overlay.set_view(build_view(SessionState()))
    assert overlay.remaining_grid.count() == first == expected


def test_set_view_after_events(overlay):
    s = SessionState()
    s.apply(PlayEvent(zone="top", cards=(Card(rank=5, suit=H),), count=1,
                      confidence=1.0, frame_agreement=1.0,
                      trick_index=0, frame_ts=1.0))
    s.apply(PendingItem(frame_ts=1.1, reason="missed_play", zone="left",
                        seat=None, proposed_cards=(), confidence=0.0,
                        frame_agreement=0.0, evidence_path="", trick_index=0))
    overlay.set_view(build_view(s))
    assert "待确认 1" in overlay.pending_label.text()
    assert "漏抓 1" in overlay.pending_label.text()


def test_window_opacity_is_set(overlay):
    assert 0.0 < overlay.windowOpacity() <= 1.0
