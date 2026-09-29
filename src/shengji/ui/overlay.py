"""悬浮窗（设计文档 §2 / §14.1）。

要求：
- 无边框、置顶、**默认鼠标穿透**（不干扰游戏点击）
- 用全局热键切换到交互模式后才能点击
- 半透明深色底，不遮牌面
- 只负责渲染 `OverlayView`，不含任何业务判断

把标签暴露为属性，便于测试断言渲染内容。
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from .viewmodel import OverlayView

LEVEL_COLORS = {
    "ok": "#7bd88f",
    "warn": "#ffc857",
    "error": "#ff6b6b",
}

PANEL_QSS = """
QFrame#panel {
    background-color: rgba(18, 22, 30, 225);
    border: 1px solid rgba(120, 140, 180, 160);
    border-radius: 10px;
}
QLabel { color: #e6edf3; }
QLabel#title { color: #9ecbff; font-weight: bold; }
QLabel#footer { color: #8b98a8; }
"""


def window_flags(interactive: bool) -> Qt.WindowType:
    """构造窗口标志。**纯函数**，便于测试断言穿透行为而不必真的开窗。"""
    flags = (Qt.FramelessWindowHint
             | Qt.WindowStaysOnTopHint
             | Qt.Tool)
    if not interactive:
        flags |= Qt.WindowTransparentForInput
    return flags


class OverlayWindow(QWidget):
    """记牌器悬浮窗。"""

    def __init__(self, *, interactive: bool = False,
                 opacity: float = 0.92, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._interactive = interactive
        self.setWindowTitle("升级记牌器")
        self.setWindowOpacity(opacity)
        self.setWindowFlags(window_flags(interactive))

        self._build_ui()
        self.apply_interactive(interactive)

    # ---------- 构建 ----------

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.panel = QFrame(self)
        self.panel.setObjectName("panel")
        self.panel.setStyleSheet(PANEL_QSS)
        outer.addWidget(self.panel)

        v = QVBoxLayout(self.panel)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(6)

        self.title_label = QLabel(OverlayView().title, self.panel)
        self.title_label.setObjectName("title")
        v.addWidget(self.title_label)

        self.status_label = QLabel("", self.panel)
        v.addWidget(self.status_label)

        self.trick_label = QLabel("", self.panel)
        v.addWidget(self.trick_label)

        self.remaining_grid = QGridLayout()
        self.remaining_grid.setHorizontalSpacing(6)
        self.remaining_grid.setVerticalSpacing(2)
        v.addLayout(self.remaining_grid)
        self._remaining_labels: list[list[QLabel]] = []

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(8)

        self.seats_label = QLabel("", self.panel)
        self.seats_label.setFont(mono)
        v.addWidget(self.seats_label)

        self.pending_label = QLabel("", self.panel)
        self.pending_label.setVisible(False)
        v.addWidget(self.pending_label)
        self.inference_label = QLabel("", self.panel)
        self.inference_label.setWordWrap(True)
        self.inference_label.setVisible(False)
        v.addWidget(self.inference_label)

        self.message_label = QLabel("", self.panel)
        self.message_label.setWordWrap(True)
        self.message_label.setVisible(False)
        v.addWidget(self.message_label)

        self.footer_label = QLabel("", self.panel)
        self.footer_label.setObjectName("footer")
        self.footer_label.setFont(mono)
        v.addWidget(self.footer_label)

        self.setMinimumWidth(430)

    # ---------- 交互模式 ----------

    @property
    def interactive(self) -> bool:
        return self._interactive

    def apply_interactive(self, interactive: bool) -> None:
        """切换鼠标穿透。改变窗口标志后必须重新 show 才生效。

        ⚠️ **调用顺序很关键**：必须**先设属性、后设窗口标志**。
        实测（spike/_dbg_qt2.py）：若先 `setWindowFlags` 再
        `setAttribute(WA_TransparentForMouseEvents, ...)`，
        属性设置会把 `WindowTransparentForInput` 标志重新加回来，
        导致穿透**永远关不掉**——纠正流程也就永远点不到。
        """
        self._interactive = bool(interactive)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, not self._interactive)
        self.setWindowFlags(window_flags(self._interactive))
        if self.isVisible():
            self.show()

    def toggle_interactive(self) -> bool:
        self.apply_interactive(not self._interactive)
        return self._interactive

    # ---------- 渲染 ----------

    def set_view(self, view: OverlayView) -> None:
        """把视图模型画出来。只做渲染，不含业务判断。"""
        self.title_label.setText(view.title)
        self.status_label.setText(view.status)
        color = LEVEL_COLORS.get(view.status_level, LEVEL_COLORS["ok"])
        self.status_label.setStyleSheet(f"color: {color}; font-weight: bold;")
        self.trick_label.setText(view.trick_text)
        self._render_remaining(view)
        self.seats_label.setText("   ".join(s.text() for s in view.seats))
        self.pending_label.setText(view.pending_text)
        self.pending_label.setVisible(bool(view.pending_text))
        if view.pending_text:
            self.pending_label.setStyleSheet(
                f"color: {LEVEL_COLORS['warn']}; font-weight: bold;")
        self.inference_label.setText(view.inference_text)
        self.inference_label.setVisible(bool(view.inference_text))
        self.message_label.setText(view.message_text)
        self.message_label.setVisible(bool(view.message_text))
        if view.message_text:
            self.message_label.setStyleSheet(f"color: {LEVEL_COLORS['warn']};")
        self.footer_label.setText(view.footer)
        self.adjustSize()

    def _render_remaining(self, view: OverlayView) -> None:
        while self.remaining_grid.count():
            item = self.remaining_grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._remaining_labels.clear()

        for r, row in enumerate(view.remaining):
            labels: list[QLabel] = []
            head = QLabel(row.label, self.panel)
            self.remaining_grid.addWidget(head, r, 0)
            for c, (rank, n) in enumerate(row.cells, start=1):
                text = rank if n > 0 else f"({rank})"
                lab = QLabel(f"{text}{n}" if n != 1 else text, self.panel)
                lab.setToolTip(f"{row.label}{rank}: 还剩 {n} 张")
                if n == 0:
                    lab.setStyleSheet("color: #5a6472;")
                labels.append(lab)
                self.remaining_grid.addWidget(lab, r, c)
            self._remaining_labels.append(labels)

    # ---------- 便捷 ----------

    def remaining_texts(self) -> list[list[str]]:
        """供测试读取当前渲染的剩余牌文本。"""
        return [[lab.text() for lab in row] for row in self._remaining_labels]


def default_position(screen_w: int, screen_h: int,
                     win_w: int = 460, win_h: int = 300,
                     margin: int = 16) -> tuple[int, int]:
    """默认摆在屏幕左上角内侧（不遮牌桌中央的四区）。"""
    return (margin, margin)


def ensure_app(argv: list[str] | None = None) -> QApplication:
    """取得或创建 QApplication。"""
    app = QApplication.instance()
    if app is None:
        app = QApplication(argv or [])
    return app
