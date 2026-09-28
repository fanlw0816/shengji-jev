"""全局热键（设计文档 §14.1）。

悬浮窗默认**鼠标穿透**（不干扰游戏点击），但纠正流程需要点击，二者矛盾。
解法是：默认穿透，用全局热键切换到交互模式。

热键解析是纯逻辑，可完整单测；真正注册到系统的那部分（`HotkeyManager`）
无法在无人值守环境中验证，因此**注册失败必须降级为警告而不是崩溃**。
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

WM_HOTKEY = 0x0312

_MODIFIER_NAMES = {
    "alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "win": MOD_WIN,
    "super": MOD_WIN,
}

# 默认绑定（设计文档 §14.1）
DEFAULT_BINDINGS: dict[str, str] = {
    "toggle_interactive": "ctrl+alt+l",
    "force_snapshot": "ctrl+alt+o",
    "toggle_pause": "ctrl+alt+p",
}

ACTION_DESCRIPTIONS: dict[str, str] = {
    "toggle_interactive": "切换交互模式（关闭鼠标穿透）",
    "force_snapshot": "强制快照当前识别结果",
    "toggle_pause": "暂停 / 恢复采集",
}


@dataclass(frozen=True)
class Hotkey:
    action: str
    modifiers: int
    vk: int

    def describe(self) -> str:
        names = []
        if self.modifiers & MOD_CONTROL:
            names.append("Ctrl")
        if self.modifiers & MOD_ALT:
            names.append("Alt")
        if self.modifiers & MOD_SHIFT:
            names.append("Shift")
        if self.modifiers & MOD_WIN:
            names.append("Win")
        if 0x41 <= self.vk <= 0x5A:
            key = chr(self.vk)
        elif 0x30 <= self.vk <= 0x39:
            key = chr(self.vk)
        elif 0x70 <= self.vk <= 0x87:
            key = f"F{self.vk - 0x70 + 1}"
        else:
            key = f"VK{self.vk:#04x}"
        names.append(key)
        return "+".join(names)


def _parse_key(token: str) -> int:
    t = token.strip().lower()
    if len(t) == 1 and t.isalpha():
        return ord(t.upper())
    if len(t) == 1 and t.isdigit():
        return ord(t)
    if t.startswith("f") and t[1:].isdigit():
        n = int(t[1:])
        if 1 <= n <= 24:
            return 0x70 + n - 1
    raise ValueError(f"无法识别的按键: {token!r}")


def parse_hotkey(spec: str, action: str = "") -> Hotkey:
    """把 "ctrl+alt+l" 解析为 Hotkey。非法输入抛 ValueError。"""
    parts = [p for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError(f"空的热键定义: {spec!r}")

    modifiers = 0
    for token in parts[:-1]:
        name = token.strip().lower()
        if name not in _MODIFIER_NAMES:
            raise ValueError(f"无法识别的修饰键: {token!r}（在 {spec!r} 中）")
        modifiers |= _MODIFIER_NAMES[name]

    vk = _parse_key(parts[-1])
    if modifiers == 0:
        raise ValueError(f"全局热键必须带修饰键，否则会抢占普通按键: {spec!r}")
    return Hotkey(action=action, modifiers=modifiers, vk=vk)


def parse_bindings(bindings: dict[str, str] | None = None) -> dict[str, Hotkey]:
    """解析一组绑定。任一条非法即抛错（配置错误应当立刻暴露）。"""
    src = DEFAULT_BINDINGS if bindings is None else bindings
    return {action: parse_hotkey(spec, action) for action, spec in src.items()}


class HotkeyManager:
    """把热键注册到 Windows，并以轮询方式取回触发事件。

    用 `PeekMessageW` 轮询而非独立线程，便于挂到 Qt 的定时器上，
    避免跨线程消息循环的复杂度。
    """

    def __init__(self, bindings: dict[str, Hotkey] | None = None) -> None:
        self.bindings = dict(parse_bindings() if bindings is None else bindings)
        self._registered: dict[int, str] = {}
        self._user32 = None
        self.failures: list[str] = []

    def _u(self):
        if self._user32 is None:
            self._user32 = ctypes.windll.user32
        return self._user32

    def register(self) -> dict[str, bool]:
        """逐个注册。返回 {action: 是否成功}。失败只记录，不抛异常。"""
        result: dict[str, bool] = {}
        self.failures = []
        for hk_id, (action, hk) in enumerate(self.bindings.items(), start=1):
            try:
                ok = bool(self._u().RegisterHotKey(None, hk_id, hk.modifiers, hk.vk))
            except Exception:
                ok = False
            result[action] = ok
            if ok:
                self._registered[hk_id] = action
            else:
                self.failures.append(
                    f"{action} ({hk.describe()}) 注册失败，可能已被其它程序占用")
        return result

    def unregister(self) -> None:
        for hk_id in list(self._registered):
            try:
                self._u().UnregisterHotKey(None, hk_id)
            except Exception:
                pass
        self._registered.clear()

    def poll(self) -> list[str]:
        """取出本轮触发的动作名。"""
        if self._user32 is None:
            return []
        fired: list[str] = []
        msg = wintypes.MSG()
        while self._u().PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):  # PM_REMOVE
            if msg.message == WM_HOTKEY:
                action = self._registered.get(int(msg.wParam))
                if action:
                    fired.append(action)
        return fired

    @property
    def registered_actions(self) -> tuple[str, ...]:
        return tuple(self._registered.values())
