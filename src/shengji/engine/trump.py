"""主牌判定与牌力次序。

依据设计文档 §7.6。**级牌是关键**：升级中当前级数对应的点数是主牌，
初版设计曾遗漏它，会导致墩赢家判错。

完整次序（从大到小）：

    大王 > 小王
         > 正级（主花色 L）
         > 副级（其余三门的 L）
         > 主花色的其余点数：A > K > Q > J > ... > 2   （**不含 L**，L 已提升为正级）
         > 任意副牌（每门内 A > K > ... > 2，不含 L）

无主局：大小王为主牌；四门 L 均为主牌，无正/副级之分。

⚠️ **待实测确认**（设计文档 §7.6.3）：副级三门之间是否可互相比较，
不同规则集有差异。本模块的当前实现是「副级之间同级、不可互压」
（`card_strength` 对四门级牌一律返回 `(TIER_OFF_LEVEL, 0)`）。

⚠️ 但**没有**可供切换的开关 —— 早期文档写过一个 `TrumpInfo.off_level_ordered`
参数，那个字段**从未实现**（2026-09-30 查明，全仓只有 docstring 提到它）。
要切换只能改 `card_strength` 本身。该差异**直接影响墩赢家与分牌归属**，
必须用实际客户端对局验证。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..cards import JOKER_BIG, JOKER_SMALL, Card

# 分组标识：主牌用 -1，副牌用花色号 0..3
GROUP_TRUMP = -1

# 牌力层级（数值越大越强）
TIER_SIDE = 1        # 副牌
TIER_TRUMP_SUIT = 2  # 主花色的普通牌
TIER_OFF_LEVEL = 3   # 副级
TIER_MAIN_LEVEL = 4  # 正级
TIER_SMALL_JOKER = 5
TIER_BIG_JOKER = 6


@dataclass(frozen=True)
class TrumpInfo:
    """当前主牌信息。"""

    kind: Literal["suit", "no_trump"]
    suit: int | None          # kind == "suit" 时为主花色
    level_rank: int           # 当前级数 2..14

    def __post_init__(self) -> None:
        if self.kind == "suit" and self.suit is None:
            raise ValueError("kind='suit' 时必须给出主花色")
        if not (2 <= self.level_rank <= 14):
            raise ValueError(f"非法级数: {self.level_rank}")

    @property
    def level_label(self) -> str:
        from ..cards import RANK_LABELS

        return RANK_LABELS[self.level_rank]

    def describe(self) -> str:
        if self.kind == "no_trump":
            return f"无主，级牌 {self.level_label}"
        assert self.suit is not None
        from ..cards import SUIT_LABELS

        return f"主 {SUIT_LABELS[self.suit]}，级牌 {self.level_label}"


def is_joker(card: Card) -> bool:
    return card.joker != 0


def is_trump(card: Card, trump: TrumpInfo) -> bool:
    """是否为主牌。"""
    if is_joker(card):
        return True
    if card.rank == trump.level_rank:
        # 任何花色的级牌都是主牌
        return True
    return trump.kind == "suit" and card.suit == trump.suit


def group_of(card: Card, trump: TrumpInfo) -> int:
    """返回牌所属的比较分组：GROUP_TRUMP 或花色号。"""
    return GROUP_TRUMP if is_trump(card, trump) else card.suit


def card_strength(card: Card, trump: TrumpInfo) -> tuple[int, int]:
    """牌力键，元组越大越强。只在同一分组内比较才有意义，跨组由调用方处理。"""
    if card.joker == JOKER_BIG:
        return (TIER_BIG_JOKER, 0)
    if card.joker == JOKER_SMALL:
        return (TIER_SMALL_JOKER, 0)

    if card.rank == trump.level_rank:
        if trump.kind == "suit" and card.suit == trump.suit:
            return (TIER_MAIN_LEVEL, 0)     # 正级
        return (TIER_OFF_LEVEL, 0)          # 副级（默认同级）

    if trump.kind == "suit" and card.suit == trump.suit:
        return (TIER_TRUMP_SUIT, card.rank)
    return (TIER_SIDE, card.rank)


def sequence_index(card: Card, trump: TrumpInfo) -> int | None:
    """牌在其所属分组「连对序列」中的位置；**不参与连对则返回 None**。

    这是连对（拖拉机）判定的**唯一序列定义**，由 `engine/trick.py`（墩赢家）与
    `engine/legal.py`（合法着法枚举）共用 —— 两处各写一份「连续」的定义，
    迟早会出现「枚举说合法、算赢家说不匹配」这类最难查的不一致。

    位置值只在**同一分组内**可比（同分组内不同牌的位置必不相同），
    故调用方必须先确认各对属于同一分组，再比较位置是否相差 1。

    三类牌**不参与**连对，返回 `None`：

    - **大小王** —— 没有点数序列
    - **级牌（任何花色）** —— 正级自成一层（在主花色内提升到副级之上），
      副级四门同级。级牌与同门其余点数之间没有「相邻」可言
    - 由此导出：**副级之间也不参与**（同级无先后次序）

    主花色的其余点数按「级牌被挖掉」后的次序编号：等级数以上的点数整体下移一位。
    打 10 主 ♠ 时 ♠9 → 9、♠J → 10、♠Q → 11，于是 **♠J♠J + ♠9♠9 是连对**
    （设计文档 §7.6 的次序本身就把 ♠10 从主花色序列里提走了）。

    ⚠️ 「级牌被挖掉后仍算连续」这一取值属 `RuleProfile.tractor_skips_level`，
    **未经实测确认**（todo.md「连对（拖拉机）的级牌语义」）。若 Phase 0 实测相反，
    改这里一处即可 —— 枚举与赢家会一起变，因为它们共用本函数。
    """
    if is_joker(card):
        return None
    if card.rank == trump.level_rank:
        return None
    if trump.kind == "suit" and card.suit == trump.suit and card.rank > trump.level_rank:
        return card.rank - 1
    return card.rank


def compare(cards_a: tuple[Card, ...], cards_b: tuple[Card, ...],
            trump: TrumpInfo) -> int:
    """比较两组同结构牌的大小。返回 1 / 0 / -1。

    约定：只比较**结构相同**的两组（对子比对子、拖拉机对拖拉机）。
    结构不同时由 trick 模块负责判定，不在此处理。
    """
    ka = _top_of(cards_a, trump)
    kb = _top_of(cards_b, trump)
    ga = group_of(cards_a[0], trump)
    gb = group_of(cards_b[0], trump)
    # 主牌组整体大于任意副牌组
    if ga == GROUP_TRUMP and gb != GROUP_TRUMP:
        return 1
    if gb == GROUP_TRUMP and ga != GROUP_TRUMP:
        return -1
    if ga != gb:
        raise ValueError("不同副牌花色之间不可比较大小")
    if ka > kb:
        return 1
    if ka < kb:
        return -1
    return 0


def _top_of(cards: tuple[Card, ...], trump: TrumpInfo) -> tuple[int, int]:
    """一组牌里最强的那张的牌力键（拖拉机以最高对子为准，取最大值即可）。"""
    return max(card_strength(c, trump) for c in cards)


def sort_desc(cards: list[Card], trump: TrumpInfo) -> list[Card]:
    """按牌力从大到小排序。主牌在前，同组内按牌力。"""
    def key(c: Card) -> tuple:
        grp = group_of(c, trump)
        grp_rank = 1 if grp == GROUP_TRUMP else 0
        return (grp_rank, card_strength(c, trump), c.suit, c.rank)

    return sorted(cards, key=key, reverse=True)


def all_trumps(trump: TrumpInfo, decks: int = 2) -> list[Card]:
    """列出当前主牌集合（含级牌与大小王），用于记牌统计。

    ⚠️ 注意：**四门花色的级牌都是主牌**，不只是主花色的那一张。
    早期实现只加了主花色的级牌，两副牌下会少算 6 张主牌。
    """
    out: list[Card] = []
    for _ in range(decks):
        out.append(Card.big_joker())
        out.append(Card.small_joker())
    # 四门级牌一律是主牌
    for s in (0, 1, 2, 3):
        for _ in range(decks):
            out.append(Card(rank=trump.level_rank, suit=s))
    # 主花色的其余点数
    if trump.kind == "suit":
        assert trump.suit is not None
        for rank in range(2, 15):
            if rank == trump.level_rank:
                continue
            for _ in range(decks):
                out.append(Card(rank=rank, suit=trump.suit))
    return out


# --- 主牌规格解析（CLI 与配置文件用）---

_SPEC_SUITS: dict[str, int] = {
    "S": 0, "H": 1, "D": 2, "C": 3,
    "♠": 0, "♥": 1, "♦": 2, "♣": 3,
}
_NO_TRUMP_PREFIXES = ("NOTRUMP", "NO_TRUMP", "NT", "N", "无主", "无将")


def _parse_level(token: str) -> int:
    from ..cards import RANK_LABELS

    t = token.strip().upper()
    if not t:
        raise ValueError("缺少级牌点数")
    if t.isdigit():
        n = int(t)
        if 2 <= n <= 14:
            return n
        raise ValueError(f"级牌点数超出 2..14: {token!r}")
    rev = {v: k for k, v in RANK_LABELS.items()}
    if t in rev:
        return rev[t]
    raise ValueError(f"无法识别的级牌点数: {token!r}")


def parse_trump(spec: str) -> TrumpInfo:
    """把 "S2" / "H10" / "♠A" / "NT5" / "无主5" 解析为 TrumpInfo。

    格式：主花色字母 + 级牌点数；无主用 NT / N / 无主 / 无将 前缀。
    """
    s = spec.strip().replace(" ", "")
    if not s:
        raise ValueError("空的主牌规格")

    for prefix in _NO_TRUMP_PREFIXES:
        if s.upper().startswith(prefix.upper()) and len(s) > len(prefix):
            return TrumpInfo(kind="no_trump", suit=None,
                             level_rank=_parse_level(s[len(prefix):]))

    first = s[0]
    suit = _SPEC_SUITS.get(first) if first in _SPEC_SUITS else _SPEC_SUITS.get(first.upper())
    if suit is None:
        raise ValueError(f"无法识别主花色: {spec!r}（可用 S/H/D/C/♠/♥/♦/♣，无主用 NT）")
    if len(s) < 2:
        raise ValueError(f"缺少级牌点数: {spec!r}")
    return TrumpInfo(kind="suit", suit=suit, level_rank=_parse_level(s[1:]))
