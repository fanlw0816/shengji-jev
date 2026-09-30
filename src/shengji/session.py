"""会话状态：把事件流累积成可显示的状态。

刻意与 Qt 无关 —— 这样它可以用纯单测覆盖，而不需要起一个 GUI。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .engine.trick import PlayedCards, trick_points, winning_seat
from .engine.trump import TrumpInfo
from .events.types import PendingItem, PlayEvent, TrickEndEvent


@dataclass
class TrickRecord:
    """一墩的已确认记录。"""

    index: int
    plays: list[PlayEvent] = field(default_factory=list)
    winner_zone: str | None = None
    points: int = 0
    points_known: bool = False       # False = 牌未识别，分牌无法判定
    confident: bool = True


@dataclass
class SessionState:
    """一局的可显示状态。

    只消费**已确认**的事件；待确认事件只计数，不污染确定记录。
    """

    seats: list[str] = field(default_factory=lambda: ["bottom", "right", "top", "left"])
    trump: TrumpInfo | None = None

    trick_index: int = 0
    current: TrickRecord = field(default_factory=lambda: TrickRecord(index=0))
    history: list[TrickRecord] = field(default_factory=list)

    points: dict[str, int] = field(default_factory=dict)
    pending_count: int = 0
    missed_count: int = 0
    low_confidence_count: int = 0
    paused: bool = False
    degraded_mode: str = "dxcam"
    last_ts: float | None = None
    last_message: str = ""

    def __post_init__(self) -> None:
        for s in self.seats:
            self.points.setdefault(s, 0)

    # ---------- 事件消费 ----------

    def apply(self, event) -> None:
        if isinstance(event, PlayEvent):
            self._on_play(event)
        elif isinstance(event, TrickEndEvent):
            self._on_trick_end(event)
        elif isinstance(event, PendingItem):
            self._on_pending(event)

    def _on_play(self, event: PlayEvent) -> None:
        if event.trick_index != self.current.index:
            # 事件属于新的一墩（例如漏掉了墩结束）—— 不静默丢弃，明确记下
            self.current = TrickRecord(index=event.trick_index)
        self.current.plays.append(event)
        self.last_ts = event.frame_ts

    def _on_trick_end(self, event: TrickEndEvent) -> None:
        rec = self.current
        rec.plays = list(event.plays) if event.plays else rec.plays
        self._score(rec)
        self.history.append(rec)
        self.trick_index = event.trick_index + 1
        self.current = TrickRecord(index=self.trick_index)
        self.last_ts = event.frame_ts

    def _score(self, rec: TrickRecord) -> None:
        """算本墩赢家与分牌。牌未识别时如实标记"分牌未知"，不猜。"""
        if not rec.plays:
            rec.points_known = False
            return
        if any(p.cards is None for p in rec.plays):
            rec.points_known = False
            rec.confident = False
            return
        if len(rec.plays) < len(self.seats):
            # 本墩缺人：低置信那一手被门控在待确认队列里（设计文档 §9.1），
            # 或看门狗判定有牌一闪而过。**先不算分** ——
            # 少一手的墩算出的赢家可能根本不是真赢家，那就是静默算错。
            # 用户补录后按事件日志重放，这一墩会连同分数一起重算。
            rec.points_known = False
            rec.confident = False
            return
        if self.trump is None:
            rec.points_known = False
            return

        order = [s for s in self.seats]
        played = [PlayedCards(seat=order.index(p.zone) if p.zone in order else 0,
                              cards=tuple(p.cards)) for p in rec.plays]
        try:
            outcome = winning_seat(played, self.trump)
        except ValueError:
            rec.points_known = False
            rec.confident = False
            return
        rec.points = trick_points(played)
        rec.points_known = True
        rec.confident = outcome.confident

        # ⚠️ `winner_seat` 是**座位号**，不是 `rec.plays` 的下标。
        # 出牌顺序与座位顺序不一致时（先出的不一定是座位靠前的），
        # 用下标索引会把这一墩的赢家记到**别人**头上 —— 分数直接记错人。
        idx = outcome.winner_seat
        for play, pc in zip(rec.plays, played):
            if pc.seat == idx:
                rec.winner_zone = play.zone
                self.points[rec.winner_zone] = (
                    self.points.get(rec.winner_zone, 0) + rec.points)
                break

    def _on_pending(self, item: PendingItem) -> None:
        self.pending_count += 1
        self.last_message = ("这一手不确定，请确认" if item.reason == "low_confidence"
                             else "有一手牌一闪而过，请补录")
        if item.reason == "missed_play":
            self.missed_count += 1
        else:
            self.low_confidence_count += 1
        self.last_ts = item.frame_ts

    # ---------- 查询 ----------

    def resolve_pending(self) -> None:
        """用户处理掉一项待确认。"""
        self.pending_count = max(0, self.pending_count - 1)

    def total_points(self) -> int:
        return sum(self.points.values())

    @property
    def unscored_tricks(self) -> int:
        """已结束但**没能计分**的墩数（缺牌 / 牌未识别 / 主牌未知）。

        这些墩不会凭空算一个结果 —— 它们等用户补录后按事件日志重放再重算。
        必须让用户看见：否则他只会觉得"分数怎么少了"，而不知道是可以补的。
        """
        return sum(1 for rec in self.history if rec.plays and not rec.points_known)

    @property
    def has_attention(self) -> bool:
        return self.pending_count > 0
