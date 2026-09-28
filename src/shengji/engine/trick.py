"""墩赢家判定（设计文档 §7.3）。

规则要点：
- 领出方确立「领出结构」（单张 / 对子 / 拖拉机 / 甩牌）
- 只有**结构匹配**的出牌才可能赢得该墩
- 结构匹配者中：若有主牌，则最大的主牌赢；否则领出花色中最大者赢
- 强弱相等时**先出者赢**（升级规则）
- 非领出花色、也非主牌的牌永远不能赢

⚠️ **甩牌（一次出多张杂牌）的赢家判定未完整实现**：
完整规则需要判断甩牌的合法性（是否都是同花色最大）以及各家跟牌的结构匹配，
规则复杂且地区差异大。本模块对甩牌返回 `confident=False`，
由上层走人工确认流程（设计文档 §9.1 的「宁可让你点一下，也不静默算错」）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..cards import Card
from .trump import (
    GROUP_TRUMP,
    TrumpInfo,
    _top_of,
    card_strength,
    group_of,
)


class Structure(str, Enum):
    """一组牌的结构。"""

    SINGLE = "single"
    PAIR = "pair"
    TRACTOR = "tractor"     # 连对
    MIXED = "mixed"         # 甩牌 / 杂牌


@dataclass(frozen=True)
class PlayedCards:
    seat: int
    cards: tuple[Card, ...]


@dataclass(frozen=True)
class TrickOutcome:
    winner_seat: int
    confident: bool          # False = 规则未覆盖（如甩牌），需人工确认
    note: str = ""


def structure_of(cards: tuple[Card, ...]) -> Structure:
    """判断一组牌的结构。"""
    n = len(cards)
    if n == 0:
        raise ValueError("空出牌")
    if n == 1:
        return Structure.SINGLE
    if n == 2:
        return Structure.PAIR if _same_face(cards[0], cards[1]) else Structure.MIXED
    # 4 张及以上：检查是否为连对（拖拉机）
    if n % 2 == 0 and _is_tractor(cards):
        return Structure.TRACTOR
    return Structure.MIXED


def _same_face(a: Card, b: Card) -> bool:
    if a.joker or b.joker:
        return a.joker == b.joker
    return a.rank == b.rank and a.suit == b.suit


def _is_tractor(cards: tuple[Card, ...]) -> bool:
    """是否为连对：每两张成一对，且各对点数连续、花色相同。

    大小王算特殊情况：大王对与小王对不构成拖拉机（点数不连续语义不同）。
    """
    n = len(cards)
    pairs: list[Card] = []
    for i in range(0, n, 2):
        if not _same_face(cards[i], cards[i + 1]):
            return False
        pairs.append(cards[i])
    if any(p.joker for p in pairs):
        return False
    suits = {p.suit for p in pairs}
    if len(suits) != 1:
        return False
    ranks = sorted(p.rank for p in pairs)
    return all(ranks[i + 1] - ranks[i] == 1 for i in range(len(ranks) - 1))


def _can_beat_lead(play: PlayedCards, lead_group: int, trump: TrumpInfo) -> bool:
    """该出牌是否具备赢得本墩的资格。

    主牌永远有资格；副牌必须与领出花色同组。
    """
    if group_of(play.cards[0], trump) == GROUP_TRUMP:
        return True
    return group_of(play.cards[0], trump) == lead_group


def winning_seat(plays: list[PlayedCards], trump: TrumpInfo) -> TrickOutcome:
    """判定本墩赢家。

    plays 必须按出牌顺序给出，plays[0] 为领出方。
    强弱相等时先出者赢，因此用严格大于来替换当前最佳。
    """
    if not plays:
        raise ValueError("无出牌")
    lead = plays[0]
    lead_struct = structure_of(lead.cards)
    lead_group = group_of(lead.cards[0], trump)

    if lead_struct is Structure.MIXED:
        # 甩牌：规则未完整覆盖，保守地让领出方赢，但标记为不可信
        return TrickOutcome(
            winner_seat=lead.seat,
            confident=False,
            note="甩牌（一次出多张杂牌）的赢家判定未完整实现，需人工确认",
        )

    best_seat: int | None = None
    best_key: tuple[int, int] | None = None
    for p in plays:
        if structure_of(p.cards) is not lead_struct:
            continue
        if not _can_beat_lead(p, lead_group, trump):
            continue
        top = _top_of(p.cards, trump)
        is_trump_play = group_of(p.cards[0], trump) == GROUP_TRUMP
        # 主牌整体压过副牌；同组内比牌力
        key = (1 if is_trump_play else 0, 0) + top
        if best_key is None or key > best_key:
            best_key, best_seat = key, p.seat

    if best_seat is None:
        # 无人能压 -> 领出方赢
        return TrickOutcome(winner_seat=lead.seat, confident=True)
    return TrickOutcome(winner_seat=best_seat, confident=True)


def trick_points(plays: list[PlayedCards], point_ranks: tuple[int, ...] = (5, 10, 13)) -> int:
    """本墩的分牌总点数。5=5 分、10=10 分、K=10 分（升级常用计法）。"""
    total = 0
    for p in plays:
        for c in p.cards:
            if c.joker:
                continue
            if c.rank == 5:
                total += 5
            elif c.rank in (10, 13):
                total += 10
    return total


def unplayed_of_suit(cards: list[Card], suit: int, trump: TrumpInfo) -> list[Card]:
    """从给定牌中筛出属于某花色的非主牌（用于推断跟牌能力）。"""
    return [c for c in cards
            if not c.joker and c.suit == suit and not (
                c.rank == trump.level_rank
                or (trump.kind == "suit" and c.suit == trump.suit))]
