"""悬浮窗（设计文档 §2 / §14.1）。

要求：
- 无边框、置顶、**默认鼠标穿透**（不干扰游戏点击）
- 用全局热键切换到交互模式后才能点击
- 半透明深色底，不遮牌面
- 只负责渲染 `OverlayView`，不含任何业务判断

把标签暴露为属性，便于测试断言渲染内容。
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from functools import partial

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..cards import RANK_LABELS, SUIT_LABELS, Card
from .correction import GRID_RANKS, GRID_SUITS, CardSelection, PendingRow
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
QLabel#correctionHead { color: #ffc857; font-weight: bold; }
QPushButton {
    background-color: rgba(50, 60, 78, 220);
    color: #e6edf3;
    border: 1px solid rgba(120, 140, 180, 160);
    border-radius: 4px;
    padding: 2px 6px;
}
QPushButton:disabled { color: #6b7686; background-color: rgba(38, 44, 56, 180); }
QPushButton:hover:enabled { background-color: rgba(70, 84, 108, 230); }
"""

#: 纠正面板单张牌按钮的选中态配色（深底亮字，与整体深色面板一致）
CARD_SELECTED_QSS = "background-color: #2f6b4f; color: #eafff2;"


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
        self._correction_handler: (
            Callable[[list[Card], bool], None] | None) = None
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

        self._build_correction_panel(v)

        self.setMinimumWidth(470)

    def _build_correction_panel(self, parent_layout: QVBoxLayout) -> None:
        """纠正面板（设计文档 §9.1 / §14.1）—— 只在交互模式下可见。

        面板本身不含业务判断：待确认项的文案由 `ui/correction.py` 算好，
        本类只负责画出来，并把点击翻译成一次回调。
        """
        self.correction_panel = QWidget(self.panel)
        v = QVBoxLayout(self.correction_panel)
        v.setContentsMargins(0, 8, 0, 0)
        v.setSpacing(4)

        self.correction_head = QLabel("", self.correction_panel)
        self.correction_head.setObjectName("correctionHead")
        self.correction_head.setWordWrap(True)
        v.addWidget(self.correction_head)

        self.correction_proposed = QLabel("", self.correction_panel)
        self.correction_proposed.setWordWrap(True)
        v.addWidget(self.correction_proposed)

        grid = QGridLayout()
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(2)
        self._card_buttons: dict[Card, QPushButton] = {}
        self._card_base_labels: dict[Card, str] = {}
        for r, suit in enumerate(GRID_SUITS):
            grid.addWidget(QLabel(SUIT_LABELS[suit], self.correction_panel), r, 0)
            for c, rank in enumerate(GRID_RANKS, start=1):
                self._add_card_button(grid, r, c, Card(rank=rank, suit=suit),
                                      RANK_LABELS[rank])
        joker_row = len(GRID_SUITS)
        grid.addWidget(QLabel("王", self.correction_panel), joker_row, 0)
        for c, card in enumerate((Card.small_joker(), Card.big_joker()), start=1):
            self._add_card_button(grid, joker_row, c, card, card.label())
        v.addLayout(grid)

        self.correction_selection = QLabel("", self.correction_panel)
        self.correction_selection.setWordWrap(True)
        v.addWidget(self.correction_selection)

        buttons = QHBoxLayout()
        self.accept_button = QPushButton("采纳识别结果", self.correction_panel)
        self.accept_button.clicked.connect(self._on_accept_clicked)
        buttons.addWidget(self.accept_button)

        self.submit_button = QPushButton("提交所选", self.correction_panel)
        self.submit_button.clicked.connect(self._on_submit_clicked)
        buttons.addWidget(self.submit_button)

        self.skip_button = QPushButton("跳过不记账", self.correction_panel)
        self.skip_button.clicked.connect(self._on_skip_clicked)
        buttons.addWidget(self.skip_button)
        v.addLayout(buttons)

        parent_layout.addWidget(self.correction_panel)

        # 纯 UI 编辑状态（"选了几张"的语义在 ui/correction.py 里）
        self._selection = CardSelection()
        self._current_row: PendingRow | None = None
        self._correction_key: object | None = None
        self.correction_panel.setVisible(False)
        self._refresh_selection()

    def _add_card_button(self, grid: QGridLayout, r: int, c: int,
                         card: Card, label: str) -> None:
        btn = QPushButton(label, self.correction_panel)
        btn.setFixedSize(30, 22)
        font = QFont("Consolas")
        font.setPointSize(8)
        btn.setFont(font)
        btn.setToolTip(f"{card.label()}（点击循环 0→1→2→0）")
        btn.clicked.connect(partial(self._on_card_clicked, card))
        grid.addWidget(btn, r, c)
        self._card_buttons[card] = btn
        self._card_base_labels[card] = label

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
        if hasattr(self, "correction_panel"):
            # 面板只在交互模式下露出 —— 非交互时它既看不见也点不到，
            # 留着只会撑大窗口、挡住游戏
            self._sync_correction_visibility()
        if self.isVisible():
            self.show()

    def toggle_interactive(self) -> bool:
        self.apply_interactive(not self._interactive)
        return self._interactive

    # ---------- 纠正面板 ----------

    def set_correction_handler(
            self, handler: Callable[[list[Card], bool], None] | None) -> None:
        """注册"用户提交了一项纠正"的回调：`handler(cards, drop)`。

        `drop=True` 表示用户明确放弃记账（此时 `cards` 为空）。
        面板不直接改任何状态，只把意图交出去 —— 怎么应用是应用控制器的事。
        """
        self._correction_handler = handler

    def set_correction(self, row: PendingRow | None, *,
                       pending_total: int = 0,
                       key: object | None = None) -> None:
        """渲染队首待确认项。

        `key` 用来判断"还是不是同一项"：`refresh()` 每帧都会调用本方法，
        若不比较 key 就重置选择，用户刚点的牌会在下一帧被清空。
        """
        if row is None:
            self._current_row = None
            self._correction_key = None
            self.correction_panel.setVisible(False)
            return

        if key is None or key != self._correction_key:
            self._correction_key = key
            self._current_row = row
            self._selection.clear()
            self._refresh_selection()
        else:
            self._current_row = row

        total = max(pending_total, row.index + 1)
        self.correction_head.setText(
            f"待确认 {row.index + 1}/{total} · {row.headline()}")
        self.correction_proposed.setText(row.proposed_text())
        self.accept_button.setEnabled(row.can_accept_proposed)
        self._sync_correction_visibility()
        self.adjustSize()

    def _sync_correction_visibility(self) -> None:
        self.correction_panel.setVisible(
            self._interactive and self._current_row is not None)

    def _on_card_clicked(self, card: Card) -> None:
        self._selection.toggle(card)
        self._refresh_selection()

    def _refresh_selection(self) -> None:
        for card, btn in self._card_buttons.items():
            n = self._selection.count(card)
            base = self._card_base_labels[card]
            btn.setText(base if n <= 1 else f"{base}×{n}")
            btn.setStyleSheet(CARD_SELECTED_QSS if n else "")
        self.correction_selection.setText(self._selection.text())
        self.submit_button.setEnabled(not self._selection.is_empty)

    def _on_accept_clicked(self) -> None:
        row = self._current_row
        if row is None or not row.can_accept_proposed:
            return
        self._emit_correction(list(row.proposed), False)

    def _on_submit_clicked(self) -> None:
        if self._selection.is_empty:
            return
        self._emit_correction(self._selection.cards(), False)

    def _on_skip_clicked(self) -> None:
        self._emit_correction([], True)

    def _emit_correction(self, cards: Sequence[Card], drop: bool) -> None:
        if self._correction_handler is None:
            return
        self._correction_handler(list(cards), drop)

    # ---------- 纠正面板的测试入口 ----------

    def correction_cards(self) -> list[Card]:
        """当前点选出的牌（供测试与调试读取）。"""
        return self._selection.cards()

    def card_button(self, card: Card) -> QPushButton | None:
        """某个牌格子的按钮（供测试与调试读取）。"""
        return self._card_buttons.get(card)

    def correction_texts(self) -> tuple[str, str, str]:
        """(标题, 识别结果, 已选) 三行文案。"""
        return (self.correction_head.text(), self.correction_proposed.text(),
                self.correction_selection.text())

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
            if item is None:
                break
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
