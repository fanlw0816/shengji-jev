"""全局热键测试。

热键**解析**是纯逻辑，完整覆盖。
真正注册到系统的那部分需要交互式桌面，无法在无人值守环境中验证 ——
因此这里只断言"注册失败不崩溃"这条降级路径。
"""

import pytest

from shengji.ui.hotkeys import (
    DEFAULT_BINDINGS,
    MOD_ALT,
    MOD_CONTROL,
    MOD_SHIFT,
    MOD_WIN,
    Hotkey,
    HotkeyManager,
    parse_bindings,
    parse_hotkey,
)

# ---------- 解析 ----------

def test_parse_single_modifier_letter():
    hk = parse_hotkey("ctrl+l", "act")
    assert hk.action == "act"
    assert hk.modifiers == MOD_CONTROL
    assert hk.vk == ord("L")


def test_parse_multiple_modifiers_is_case_insensitive():
    hk = parse_hotkey("Ctrl+Alt+L")
    assert hk.modifiers == (MOD_CONTROL | MOD_ALT)
    assert hk.vk == ord("L")


def test_parse_all_modifiers():
    hk = parse_hotkey("ctrl+alt+shift+win+x")
    assert hk.modifiers == (MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_WIN)


def test_parse_control_alias():
    assert parse_hotkey("control+a").modifiers == MOD_CONTROL
    assert parse_hotkey("super+a").modifiers == MOD_WIN


def test_parse_digits():
    assert parse_hotkey("ctrl+alt+1").vk == ord("1")


def test_parse_function_keys():
    assert parse_hotkey("ctrl+f1").vk == 0x70
    assert parse_hotkey("ctrl+f12").vk == 0x70 + 11
    assert parse_hotkey("ctrl+f24").vk == 0x70 + 23


def test_parse_ignores_extra_spaces():
    hk = parse_hotkey("  ctrl + alt + l  ")
    assert hk.modifiers == (MOD_CONTROL | MOD_ALT)
    assert hk.vk == ord("L")


def test_parse_rejects_empty():
    with pytest.raises(ValueError):
        parse_hotkey("")


def test_parse_rejects_unknown_modifier():
    with pytest.raises(ValueError) as ei:
        parse_hotkey("hyper+l")
    assert "修饰键" in str(ei.value)


def test_parse_rejects_unknown_key():
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+f99")


def test_parse_rejects_bare_key_without_modifier():
    """全局热键必须带修饰键，否则会抢占普通按键。"""
    with pytest.raises(ValueError) as ei:
        parse_hotkey("l")
    assert "修饰键" in str(ei.value)


# ---------- 描述 ----------

def test_describe_is_readable():
    assert parse_hotkey("ctrl+alt+l").describe() == "Ctrl+Alt+L"
    assert parse_hotkey("shift+f1").describe() == "Shift+F1"


def test_describe_unknown_vk_falls_back():
    hk = Hotkey(action="x", modifiers=MOD_CONTROL, vk=0x01)
    assert "VK0x01" in hk.describe()


# ---------- 绑定表 ----------

def test_default_bindings_match_spec():
    """默认绑定与设计文档 §14.1 一致。"""
    assert DEFAULT_BINDINGS["toggle_interactive"] == "ctrl+alt+l"
    assert DEFAULT_BINDINGS["force_snapshot"] == "ctrl+alt+o"
    assert DEFAULT_BINDINGS["toggle_pause"] == "ctrl+alt+p"


def test_parse_bindings_returns_all_three():
    b = parse_bindings()
    assert set(b) == {"toggle_interactive", "force_snapshot", "toggle_pause"}
    assert b["toggle_pause"].vk == ord("P")


def test_parse_bindings_rejects_bad_entry():
    with pytest.raises(ValueError):
        parse_bindings({"bad": "l"})


def test_parse_bindings_custom():
    b = parse_bindings({"act": "ctrl+shift+9"})
    assert b["act"].modifiers == (MOD_CONTROL | MOD_SHIFT)
    assert b["act"].vk == ord("9")


# ---------- 管理器：降级路径 ----------

def test_manager_builds_bindings_without_registering():
    m = HotkeyManager()
    assert len(m.bindings) == 3
    assert m.registered_actions == ()


def test_manager_poll_before_register_returns_empty():
    m = HotkeyManager()
    assert m.poll() == []


def test_manager_register_does_not_raise_and_reports_per_action():
    """注册可能失败（热键被占用 / 无交互桌面），必须降级为报告而非崩溃。"""
    m = HotkeyManager()
    result = m.register()
    assert set(result) == {"toggle_interactive", "force_snapshot", "toggle_pause"}
    assert all(isinstance(v, bool) for v in result.values())
    if not any(result.values()):
        assert m.failures, "全部失败时必须给出失败原因"
    m.unregister()


def test_manager_unregister_is_idempotent():
    m = HotkeyManager()
    m.register()
    m.unregister()
    m.unregister()
    assert m.registered_actions == ()
