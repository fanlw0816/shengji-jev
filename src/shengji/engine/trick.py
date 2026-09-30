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

⚠️ **结构判定需要 `trump`**：连对（拖拉机）的「连续」是在**所属分组的序列**上说的，
不是裸点数相邻。早期实现只比整数点差、也不看分组，导致两类**自信算错**
（2026-09-30 修）：

| 情形（打 10、主 ♠） | 修前 | 修后 | 错在哪 |
|---|---|---|---|
| ♥10♥10 + ♥9♥9 | `TRACTOR` | `MIXED` | 跨组 —— ♥10 是副级（主牌组），♥9 是 ♥ 组，两张牌不在同一组，根本不可比 |
| ♠10♠10 + ♠J♠J | `TRACTOR` | `MIXED` | 级牌不参与连对 —— ♠10 是正级，与主花色其余点数不在同一层 |
| ♠10♠10 + ♠9♠9 | `TRACTOR` | `MIXED` | 同上 |
| ♠J♠J + ♠9♠9 | `MIXED` | `TRACTOR` | 漏判 —— ♠10 被挖走后 ♠J 与 ♠9 在主花色序列上相邻 |

序列定义集中在 `trump.sequence_index()`，与 `engine/legal.py` 的枚举共用。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from ..cards import Card
from .trump import (
    GROUP_TRUMP,
    TrumpInfo,
    _top_of,
    group_of,
    sequence_index,
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


def structure_of(cards: tuple[Card, ...], trump: TrumpInfo) -> Structure:
    """判断一组牌的结构。

    `trump` 是**必需参数**（不给默认值）：连对的「连续」依赖分组与级牌，
    不给主牌信息就只能退回裸点数相邻 —— 那正是 2026-09-30 修掉的那类自信算错
    （见模块 docstring 的表）。强制传参让「忘了传」在调用点就暴露，
    而不是在真实牌局里静默算错。
    """
    n = len(cards)
    if n == 0:
        raise ValueError("空出牌")
    if n == 1:
        return Structure.SINGLE
    if n == 2:
        return Structure.PAIR if _same_face(cards[0], cards[1]) else Structure.MIXED
    # 4 张及以上：检查是否为连对（拖拉机）
    if n % 2 == 0 and _is_tractor(cards, trump):
        return Structure.TRACTOR
    return Structure.MIXED


def structure_matches(cards: tuple[Card, ...], lead_structure: Structure,
                      trump: TrumpInfo) -> bool:
    """跟牌的结构是否足以赢过该领出结构（设计文档 §7.3）。

    抽成独立函数是为了让**合法着法枚举（`engine/legal.py`）与墩赢家共用同一份判定** ——
    各写一遍迟早会出现「枚举说合法、算赢家说不匹配」这类最难查的不一致。

    - `SINGLE` / `PAIR` / `TRACTOR`：要求结构枚举完全相等
    - `MIXED`（甩牌 / 杂牌）：甩牌的「型」由**张数**决定，各家按张数跟、不按结构跟，
      故只要求非空。甩牌本身的合法性依赖别家手牌，不在此判定（见 `engine/legal.py`）。
    """
    if not cards:
        return False
    if lead_structure is Structure.MIXED:
        return True
    return structure_of(cards, trump) is lead_structure


def _same_face(a: Card, b: Card) -> bool:
    if a.joker or b.joker:
        return a.joker == b.joker
    return a.rank == b.rank and a.suit == b.suit


def _is_tractor(cards: tuple[Card, ...], trump: TrumpInfo) -> bool:
    """是否为连对。

    三条**都必须满足**（2026-09-30 补后两条，此前只比裸点数差、不看分组）：

    1. 每两张成一对（同花色同点数）
    2. **同一分组** —— 四门级牌都属主牌组，于是打 10 主 ♠ 时
       ♥10♥10 + ♥9♥9 是「副级对 + ♥ 副牌对」，跨组不可比，不是连对
    3. 各对在**组内连对序列**上连续 —— 交给 `trump.sequence_index()`，
       它同时表达「级牌不参与连对」与「主花色挖掉级牌后其余点数连续」

    大小王：同色王成对（走 `_same_face` 的第 1 条）但 `sequence_index` 返回 None，
    故 4 张王不构成连对，与旧行为一致。
    """
    n = len(cards)
    pairs: list[Card] = []
    for i in range(0, n, 2):
        if not _same_face(cards[i], cards[i + 1]):
            return False
        pairs.append(cards[i])
    if any(p.joker for p in pairs):
        return False
    if len({group_of(p, trump) for p in pairs}) != 1:
        return False

    idx: list[int] = []
    for p in pairs:
        pos = sequence_index(p, trump)
        if pos is None:
            return False        # 级牌 / 副级 / 王：不在任何连对序列里
        idx.append(pos)
    idx.sort()
    # 同一对点数重复（如两副牌的 4 张 ♠5 拆成两对）差为 0，在此被拒
    return all(idx[i + 1] - idx[i] == 1 for i in range(len(idx) - 1))


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
    lead_struct = structure_of(lead.cards, trump)
    lead_group = group_of(lead.cards[0], trump)

    if lead_struct is Structure.MIXED:
        # 甩牌：规则未完整覆盖，保守地让领出方赢，但标记为不可信
        return TrickOutcome(
            winner_seat=lead.seat,
            confident=False,
            note="甩牌（一次出多张杂牌）的赢家判定未完整实现，需人工确认",
        )

    best_seat: int | None = None
    best_key: tuple[int, ...] | None = None
    for p in plays:
        if not structure_matches(p.cards, lead_struct, trump):
            continue
        if not _can_beat_lead(p, lead_group, trump):
            continue
        top = _top_of(p.cards, trump)
        is_trump_play = group_of(p.cards[0], trump) == GROUP_TRUMP
        # 主牌整体压过副牌；同组内比牌力
        key = (1 if is_trump_play else 0, 0, *top)
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
