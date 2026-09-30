"""视图模型：把状态转成「可直接渲染」的数据。

刻意与 Qt 无关 —— 纯函数，可用单测覆盖。
Qt 部件只负责把这些字符串/数字画出来，不含任何业务判断。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from ..cards import RANK_LABELS, SUIT_LABELS, Card
from ..engine.accounting import UnseenPool, deck_composition
from ..engine.inference import SeatInference
from ..session import SessionState

# 显示用的花色顺序
SUIT_ORDER = [0, 1, 2, 3]
ZONE_LABELS = {"bottom": "自己", "right": "下家", "top": "对家", "left": "上家"}


@dataclass(frozen=True)
class RemainingRow:
    """一行剩余牌统计（一个花色）。"""

    suit: int
    label: str                      # "♠"
    cells: list[tuple[str, int]]    # [(点数标签, 剩余张数), ...]

    def text(self) -> str:
        parts = [f"{r}:{n}" for r, n in self.cells]
        return f"{self.label} " + " ".join(parts)


@dataclass(frozen=True)
class SeatRow:
    """一家的统计行。"""

    zone: str
    label: str
    points: int
    is_self: bool

    def text(self) -> str:
        return f"{self.label}  {self.points} 分"


@dataclass
class OverlayView:
    """悬浮窗要渲染的全部内容。"""

    title: str = "升级记牌器"
    status: str = ""
    status_level: str = "ok"        # ok / warn / error
    trick_text: str = ""
    remaining: list[RemainingRow] = field(default_factory=list)
    seats: list[SeatRow] = field(default_factory=list)
    pending_text: str = ""
    inference_text: str = ""        # 功能 B：各家的可能持牌范围 / 必然持有
    message_text: str = ""          # 最近一条异常或提示（失败要可见）
    footer: str = ""

    def to_lines(self) -> list[str]:
        """纯文本表示，便于测试与调试输出。"""
        out = [self.title, self.status, self.trick_text]
        out += [r.text() for r in self.remaining]
        out += [s.text() for s in self.seats]
        if self.pending_text:
            out.append(self.pending_text)
        if self.inference_text:
            out.append(self.inference_text)
        if self.message_text:
            out.append(self.message_text)
        if self.footer:
            out.append(self.footer)
        return out


def remaining_rows(unseen: Counter[Card] | None,
                   decks: int = 2,
                   suits: list[int] | None = None) -> list[RemainingRow]:
    """按花色组织剩余牌统计。

    剩余 = 该面值在整副牌中的张数 − 已出现张数。unseen 为 None 时
    按「全部未出现」显示（即整副牌张数），不假装知道。
    """
    full = deck_composition(decks)
    rows: list[RemainingRow] = []
    for suit in (suits if suits is not None else SUIT_ORDER):
        cells: list[tuple[str, int]] = []
        for rank in range(2, 15):
            face = Card(rank=rank, suit=suit)
            total = full.get(face, 0)
            left = total if unseen is None else unseen.get(face, 0)
            cells.append((RANK_LABELS[rank], left))
        rows.append(RemainingRow(suit=suit, label=SUIT_LABELS[suit], cells=cells))
    # 大小王单独一行
    joker_cells = [
        ("小王", full.get(Card.small_joker(), 0) if unseen is None
         else unseen.get(Card.small_joker(), 0)),
        ("大王", full.get(Card.big_joker(), 0) if unseen is None
         else unseen.get(Card.big_joker(), 0)),
    ]
    rows.append(RemainingRow(suit=-1, label="王", cells=joker_cells))
    return rows


def seat_rows(session: SessionState) -> list[SeatRow]:
    out: list[SeatRow] = []
    for zone in session.seats:
        out.append(SeatRow(zone=zone,
                           label=ZONE_LABELS.get(zone, zone),
                           points=session.points.get(zone, 0),
                           is_self=(zone == "bottom")))
    return out


def status_line(session: SessionState) -> tuple[str, str]:
    """返回 (文案, 级别)。级别用于上色。"""
    if session.paused:
        return ("已暂停（采集停止）", "warn")
    if session.degraded_mode == "mss":
        return ("降级模式（BitBlt，已降频到 15Hz）", "warn")
    if session.degraded_mode == "none":
        return ("无可用采集后端", "error")
    if session.pending_count > 0:
        return (f"有 {session.pending_count} 项待确认", "warn")
    return ("运行中", "ok")


def seat_labels(session: SessionState) -> dict[int, str]:
    """座位号 → 中文方位。座位号即 `session.seats` 的下标（出牌方向由标定决定）。"""
    return {i: ZONE_LABELS.get(z, z) for i, z in enumerate(session.seats)}


def build_view(session: SessionState,
               unseen: Counter[Card] | None = None,
               pool: UnseenPool | None = None,
               inference: SeatInference | None = None) -> OverlayView:
    """组装悬浮窗视图。

    unseen 优先取 pool；两者都为空时按"整副牌都还没出现"显示。
    inference 为空时不显示推断行 —— 不显示比显示错的强。
    """
    view = OverlayView()
    text, level = status_line(session)
    view.status = text
    view.status_level = level

    trump = session.trump
    view.trick_text = (f"第 {session.trick_index + 1} 墩"
                       + (f" · {trump.describe()}" if trump else " · 主牌未知"))

    counter = pool.as_counter() if pool is not None else unseen
    view.remaining = remaining_rows(counter)
    view.seats = seat_rows(session)

    if inference is not None:
        view.inference_text = "  ·  ".join(
            inference.summary_lines(seat_labels(session)))

    if session.pending_count:
        view.pending_text = (f"待确认 {session.pending_count}"
                             f"（低置信 {session.low_confidence_count}"
                             f" / 漏抓 {session.missed_count}）")
    if session.unscored_tricks:
        # 有墩没算分就必须说出来 —— 用户看到分数少了，得知道那是可补的
        count = f"{session.unscored_tricks} 墩分牌待重算"
        view.pending_text = (f"{view.pending_text} · {count}" if view.pending_text
                             else count)
    view.message_text = session.last_message
    view.footer = "Ctrl+Alt+L 交互 · Ctrl+Alt+O 快照 · Ctrl+Alt+P 暂停"
    return view
