"""牌张模型。识别层与牌局引擎共用，因此放在中性位置。

编码约定（与设计文档 §7.6 一致）：
- rank: 2..14，其中 J=11 Q=12 K=13 **A=14**（即 A 大于 K）
- suit: 0=♠ 1=♥ 2=♦ 3=♣
- joker: 0=非王 1=小王 2=大王；非 0 时 rank/suit 无意义
"""

from __future__ import annotations

from dataclasses import dataclass

JOKER_NONE = 0
JOKER_SMALL = 1
JOKER_BIG = 2

SUIT_SPADE = 0
SUIT_HEART = 1
SUIT_DIAMOND = 2
SUIT_CLUB = 3

RANK_JACK = 11
RANK_QUEEN = 12
RANK_KING = 13
RANK_ACE = 14

RANK_LABELS: dict[int, str] = {
    2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
    10: "10", RANK_JACK: "J", RANK_QUEEN: "Q", RANK_KING: "K", RANK_ACE: "A",
}
SUIT_LABELS: dict[int, str] = {
    SUIT_SPADE: "♠", SUIT_HEART: "♥", SUIT_DIAMOND: "♦", SUIT_CLUB: "♣",
}
# 用于文件名/CLI 的 ASCII 表示
SUIT_LETTERS: dict[int, str] = {
    SUIT_SPADE: "S", SUIT_HEART: "H", SUIT_DIAMOND: "D", SUIT_CLUB: "C",
}


@dataclass(frozen=True, order=True)
class Card:
    rank: int
    suit: int
    joker: int = JOKER_NONE

    def __post_init__(self) -> None:
        if self.joker not in (JOKER_NONE, JOKER_SMALL, JOKER_BIG):
            raise ValueError(f"非法 joker 值: {self.joker}")
        if self.joker == JOKER_NONE:
            if not (2 <= self.rank <= 14):
                raise ValueError(f"非法 rank: {self.rank}")
            if not (0 <= self.suit <= 3):
                raise ValueError(f"非法 suit: {self.suit}")

    @classmethod
    def small_joker(cls) -> Card:
        return cls(rank=0, suit=0, joker=JOKER_SMALL)

    @classmethod
    def big_joker(cls) -> Card:
        return cls(rank=0, suit=0, joker=JOKER_BIG)

    @property
    def is_joker(self) -> bool:
        return self.joker != JOKER_NONE

    def label(self) -> str:
        """人类可读标签，如 '♠A'、'大王'。"""
        if self.joker == JOKER_SMALL:
            return "小王"
        if self.joker == JOKER_BIG:
            return "大王"
        return f"{SUIT_LABELS[self.suit]}{RANK_LABELS[self.rank]}"

    def code(self) -> str:
        """ASCII 编码，如 'SA'、'H10'、'joker_small'。用于文件名与配置。"""
        if self.joker == JOKER_SMALL:
            return "joker_small"
        if self.joker == JOKER_BIG:
            return "joker_big"
        return f"{SUIT_LETTERS[self.suit]}{RANK_LABELS[self.rank]}"

    def __str__(self) -> str:
        return self.label()


def parse_code(code: str) -> Card | None:
    """解析 Card.code() 的逆操作。非法输入返回 None。"""
    if code == "joker_small":
        return Card.small_joker()
    if code == "joker_big":
        return Card.big_joker()
    if len(code) < 2:
        return None
    letter, rank_part = code[0], code[1:]
    suit = next((s for s, lbl in SUIT_LETTERS.items() if lbl == letter), None)
    if suit is None:
        return None
    rank = next((r for r, lbl in RANK_LABELS.items() if lbl == rank_part), None)
    if rank is None:
        return None
    return Card(rank=rank, suit=suit)
