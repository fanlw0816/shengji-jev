"""记账模型：已知集合、未见牌池、容量受限的未知底牌堆。

依据设计文档 §7.4.1。**这里修的是评审抓出的最严重缺陷**：
初版公式 `unseen = 全牌 − 自己手牌 − 已亮底牌` 有两类重复扣减。

### 缺陷一：两个快照必须取自同一时点

术语（4 人 2 副为例，H=手牌、P=扣底前底牌、D=扣底后底牌）：

    H = (A₀ ∪ P) − D          D ⊆ A₀ ∪ P          H ∩ D = ∅

- ✅ 扣底前组合：`A₀ ∪ P` = 33 张，互斥
- ✅ 扣底后组合：`H ∪ D` = 33 张，互斥
- ❌ **混用**（扣底后的 H + 扣底前的 P）：`H ∩ P = P − D`，即庄家留下的底牌，**非空**

注意 `A₀ ∪ P = H ∪ D`，两种写法的集合**完全相同**，但不能交叉取。
本模型采用**扣底后 (H, D)**，因为这两个区域在出牌阶段持续可见、可反复校验。

> **实施中修正（2026-09-29）**：初版用「按面值断言 H 与 D 不重叠」来拦混用，
> 这在两副牌下会**对合法牌型误报**（同面值有 2 张，庄家手上 1 张 ♠A、
> 底牌埋另 1 张 ♠A 很正常）。现改为按**牌堆构成**判定，
> 混用则由运行时不变式 I1 捕获。理由与代价详见 `KnownSet.for_declarer`。

### 缺陷二：`played` 与 `known` 重叠

用户自己打出的牌本就在 `known` 里，若用 `全牌 − known − played` 会扣两次，
`unseen` 变负数。本模型改为**实时多重集计数器**，且只对「来自 known 之外」的牌扣减。

### 缺陷三：非庄家时底牌不属于任何一家

8 张底牌停留在 `unseen` 中但不属于任何座位。若不保留这个「槽位」，
计数约束会把底牌派给某家，破坏 `certain[seat] ⊆ ground_truth[seat]`。
因此模型含一个**容量受限未知牌堆** `bottom_unknown`。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from ..cards import Card


class AccountingError(Exception):
    """记账不变式被破坏 —— 说明识别出错或逻辑有误，必须告警而非继续。"""


@dataclass(frozen=True)
class VariantRule:
    """某个 (players, decks) 变体的静态规则数据。"""

    players: int
    decks: int
    hand_size: int
    bottom_size: int
    source: str            # "documented" | "measured" | "UNVERIFIED"


VARIANT_RULES: dict[tuple[int, int], VariantRule] = {
    (4, 2): VariantRule(4, 2, 25, 8, source="documented"),    # 25×4 + 8 = 108 ✓
    (6, 3): VariantRule(6, 3, 25, 12, source="UNVERIFIED"),   # 162 张，待实测
    # (5, 2) 存在但不在本项目范围
}


def get_variant_rule(players: int, decks: int) -> VariantRule:
    """取变体规则。未验证的变体**拒绝启动**，不采用猜测值。"""
    rule = VARIANT_RULES.get((players, decks))
    if rule is None:
        raise AccountingError(f"不支持的变体: {players} 人 {decks} 副")
    if rule.source == "UNVERIFIED":
        raise AccountingError(
            f"变体 {players} 人 {decks} 副的规则数据尚未实测确认"
            f"（手牌数/底牌张数），拒绝启动。请先完成实测标定。")
    return rule


def deck_composition(decks: int = 2) -> Counter[Card]:
    """一副牌或多副牌的完整构成。每副 54 张 = 大小王 + 4 花色 × 13 点数。"""
    c: Counter[Card] = Counter()
    for _ in range(decks):
        c[Card.big_joker()] += 1
        c[Card.small_joker()] += 1
        for suit in range(4):
            for rank in range(2, 15):
                c[Card(rank=rank, suit=suit)] += 1
    return c


@dataclass(frozen=True)
class KnownSet:
    """用户确切知道位置的牌（自己手牌 ∪ 自己扣的底牌）。"""

    cards: Counter[Card]
    role: Literal["declarer", "defender"]
    hand_size: int
    bottom_size: int

    @property
    def size(self) -> int:
        return sum(self.cards.values())

    @property
    def bottom_unknown_capacity(self) -> int:
        """未知底牌堆容量：庄家已知底牌故为 0，非庄家为 bottom_size。"""
        return 0 if self.role == "declarer" else self.bottom_size

    @classmethod
    def for_declarer(cls, post_burial_hand: list[Card], buried: list[Card],
                     rule: VariantRule) -> KnownSet:
        """庄家视角：采用**扣底后**组合 (H, D)。

        断言：|H| == hand_size、|D| == bottom_size、
        每个面值 |H ∪ D| 的张数 ≤ 牌堆构成、|H ∪ D| == hand_size + bottom_size。

        ⚠️ 传进来的必须是**扣底后**的手牌与**扣底后**的底牌。

        ### 为什么不再断言 `H ∩ D == ∅`（实施中修正）

        初版按面值断言手牌与底牌不重叠。**在两副牌下这是错的**：
        同一面值有 2 张，庄家手上 1 张 ♠A、底牌埋另 1 张 ♠A 完全是合法牌型
        （实测发牌即会命中，`for_declarer` 会对合法输入抛异常）。

        真正该拦的是「同一面值的张数超过牌堆构成」——那才是识别重复
        （同一张牌被认了两次）或快照混用的信号，所以断言改为按**牌堆构成**判。

        代价要说清楚：快照混用（扣底后的 H + 扣底前的 P）**在构造时不再总能拦下**
        （若重叠面值恰好都是 2 张一副，则各面值计数仍然合法）。
        这类错误改由运行时捕获：被重复计入已知的那几张，别人一打出就会触发
        `UnseenPool` 的「未见池中已无此牌」（不变式 I1）。
        见 `test_mixed_snapshots_surface_at_play_time`。
        """
        hand = Counter(post_burial_hand)
        bot = Counter(buried)
        if sum(hand.values()) != rule.hand_size:
            raise AccountingError(
                f"庄家手牌应为 {rule.hand_size} 张，实际 {sum(hand.values())} 张")
        if sum(bot.values()) != rule.bottom_size:
            raise AccountingError(
                f"底牌应为 {rule.bottom_size} 张，实际 {sum(bot.values())} 张")
        merged = hand + bot
        deck = deck_composition(rule.decks)
        over = {f: n for f, n in merged.items() if n > deck.get(f, 0)}
        if over:
            detail = "、".join(f"{f.label()} {n} 张（牌堆只有 {deck.get(f, 0)} 张）"
                              for f, n in over.items())
            raise AccountingError(
                f"手牌与底牌合计超出牌堆构成：{detail}。"
                f"常见原因：同一张牌被识别了两次，"
                f"或把扣底前的手牌与扣底前的底牌混用")
        total = sum(merged.values())
        if total != rule.hand_size + rule.bottom_size:
            raise AccountingError(f"已知牌总数应为 {rule.hand_size + rule.bottom_size}，实际 {total}")
        return cls(cards=merged, role="declarer",
                   hand_size=rule.hand_size, bottom_size=rule.bottom_size)

    @classmethod
    def for_defender(cls, hand: list[Card], rule: VariantRule) -> KnownSet:
        """非庄家视角：只知道自己的手牌，底牌是未知牌堆。"""
        h = Counter(hand)
        if sum(h.values()) != rule.hand_size:
            raise AccountingError(
                f"手牌应为 {rule.hand_size} 张，实际 {sum(h.values())} 张")
        return cls(cards=h, role="defender",
                   hand_size=rule.hand_size, bottom_size=rule.bottom_size)


class UnseenPool:
    """未见牌池：实时多重集计数器。

    不变式（任一被破坏即判定为识别错误，走人工纠正流程）：
        I1  unseen[f] >= 0 对所有 f
        I2  sum(unseen) == 其他座位持牌总数 + 未知底牌数
    """

    def __init__(self, rule: VariantRule, known: KnownSet, own_seat: int) -> None:
        self.rule = rule
        self.known = known
        self.own_seat = own_seat

        self._deck = deck_composition(rule.decks)
        self._counts: Counter[Card] = Counter()
        for face, n in self._deck.items():
            rem = n - known.cards.get(face, 0)
            if rem < 0:
                raise AccountingError(
                    f"已知牌中 {face.label()} 有 {-rem} 张超出牌堆构成")
            if rem:
                self._counts[face] = rem

        # 每个座位当前持牌数（进入出牌阶段时人人都是 hand_size）
        self._seat_holds: dict[int, int] = dict.fromkeys(range(rule.players), rule.hand_size)
        self._bottom_unknown = known.bottom_unknown_capacity
        self._check()

    # ---------- 查询 ----------

    def count(self, card: Card) -> int:
        return self._counts.get(card, 0)

    def total(self) -> int:
        return sum(self._counts.values())

    def as_counter(self) -> Counter[Card]:
        return Counter(self._counts)

    def own_known(self) -> Counter[Card]:
        """自己确切知道的牌（不含已打出的）。"""
        return Counter(self.known.cards)

    def others_total(self) -> int:
        return sum(n for s, n in self._seat_holds.items() if s != self.own_seat)

    def bottom_unknown(self) -> int:
        return self._bottom_unknown

    def seat_holds(self) -> dict[int, int]:
        return dict(self._seat_holds)

    # ---------- 事件 ----------

    def on_play(self, seat: int, cards: list[Card]) -> None:
        """记录一次公开出牌。

        自己打出的牌本就在 known 中，**不扣减 unseen**（否则会重复扣减、算出负数）；
        只把该座位的持牌数减掉。
        """
        if seat not in self._seat_holds:
            raise AccountingError(f"非法座位: {seat}")
        if len(cards) > self._seat_holds[seat]:
            raise AccountingError(
                f"座位 {seat} 只剩 {self._seat_holds[seat]} 张，却打出 {len(cards)} 张")

        self._seat_holds[seat] -= len(cards)

        if seat == self.own_seat:
            # 自己的牌在 known 内，不扣未见池
            for c in cards:
                if self.known.cards.get(c, 0) <= 0:
                    raise AccountingError(
                        f"自己打出了已知集合之外的牌: {c.label()}")
            self._check()
            return

        for c in cards:
            if self._counts.get(c, 0) <= 0:
                raise AccountingError(
                    f"座位 {seat} 打出 {c.label()}，但未见池中已无此牌 —— "
                    f"识别重复或错认（不变式 I1 被破坏）")
            self._counts[c] -= 1
            if self._counts[c] == 0:
                del self._counts[c]
        self._check()

    def on_bottom_revealed(self, cards: list[Card]) -> None:
        """底牌亮出（非庄家视角）：从未知底牌堆移入已知，unseen 相应扣减。"""
        if self.known.role == "declarer":
            raise AccountingError("庄家视角的底牌本就已知，不应再亮")
        if len(cards) > self._bottom_unknown:
            raise AccountingError("亮出的底牌多于未知底牌堆容量")
        for c in cards:
            if self._counts.get(c, 0) <= 0:
                raise AccountingError(f"亮出的底牌 {c.label()} 不在未见池中")
            self._counts[c] -= 1
            if self._counts[c] == 0:
                del self._counts[c]
        self._bottom_unknown -= len(cards)
        self._check()

    # ---------- 不变式 ----------

    def _check(self) -> None:
        # I1
        bad = {c.label(): -n for c, n in self._counts.items() if n < 0}
        if bad:
            raise AccountingError(f"未见池出现负数（不变式 I1）: {bad}")
        # 每种牌不得超过牌堆构成
        over = {c.label(): n for c, n in self._counts.items()
                if n > self._deck.get(c, 0)}
        if over:
            raise AccountingError(f"未见池超出牌堆构成: {over}")
        # I2
        expected = self.others_total() + self._bottom_unknown
        if self.total() != expected:
            raise AccountingError(
                f"总账不平衡（不变式 I2）：未见池 {self.total()} 张，"
                f"但其他座位 {self.others_total()} 张 + 未知底牌 "
                f"{self._bottom_unknown} 张 = {expected} 张")

    def check(self) -> None:
        """供外部显式校验（例如每墩结束时）。"""
        self._check()
