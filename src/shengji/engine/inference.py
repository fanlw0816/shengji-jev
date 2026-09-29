"""按家推断（功能 B）——「最紧可靠超集」与「必然持有」。

依据设计文档 §7.4.2（输出契约）、§7.4.3（为什么必须定义"最紧"）、
§10.1（四项验收断言 B1–B4）。与 `accounting.py` 的分工：

- `accounting.py` 管**牌去哪了**：未见池计数与不变式 I1 / I2
- 本模块管**牌可能在谁手里**：容量受限的强制分配

## 建模的三类约束

| 约束 | 来源 | 作用 |
|---|---|---|
| 面值总数 | 未见池计数 | 某面值剩余的张数必须在「能持有它的堆」之间分配完 |
| 堆容量 | 各家剩余持牌数 / 未知底牌容量 | 每个堆必须**恰好**填满自己的张数 |
| 空门（void） | 出牌历史（有该花色必须跟） | 直接排除整组面值，实战中最强的信息来源 |

求解方式是**上下界传播**（迭代到不动点）：

```
upper[p][f]  堆 p 至多持有几张 f      → candidates[p] = {f : upper ≥ 1}
lower[p][f]  堆 p 至少持有几张 f      → certain[p]    = {f : lower ≥ 1}
```

每一条界都必须在**所有与已记录历史一致的牌局世界**里成立，因此传播是单调的
（`lower` 只增、`upper` 只减，取值空间有限），必然收敛。

## 明确不承诺「最优」，只承诺「可靠且不平凡」

未建模的约束（对子结构、连对可行性、甩牌合法性）只会让 `candidates` **偏宽**。
**偏宽是安全的**（超集仍满足 B1 `ground_truth ⊆ candidates`），偏窄才会破坏
soundness，所以本模块宁可宽一点也不猜紧。

但仅满足 B1 是**可被平凡满足**的（把整个未见池给每一家即可恒真，§7.4.3），
因此实现必须同时做到两件不平凡的事，这也是测试的重点：

- **排除**：可证必然在别家的牌，不得出现在本家 `candidates`（B3）
- **必然**：由容量逼出的牌，必须出现在 `certain`（B4）

### 排除性只来自两类事实（诚实边界）

1. **空门**：整组面值从该家 `candidates` 中消失
2. **容量为 0**：该家牌已打完，或庄家视角下底牌容量为 0

**不推导**：聚合约束（如"这家某花色至少还有 N 张"）、对子与连对的可行性、
任何概率。前者的代价是 `certain` 可能比理论上更空 —— 而**更空是安全的**
（`∅ ⊆ ground_truth` 恒真）。要更紧就得先把这些约束建模进来，目前按 YAGNI 不做。

## 实测风险（尚未核实的假设）

空门推导依赖客户端强制「**有该花色必须跟，且有多少跟多少**」。
若实测证伪，`VoidTracker` 会推错空门，进而**破坏 B1**（而不是仅仅变宽）——
这项与设计文档 §14 的现场标定任务一起实测确认。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from ..cards import Card
from .accounting import AccountingError, UnseenPool
from .trick import PlayedCards, Structure, structure_of
from .trump import TrumpInfo, group_of

# 未知底牌堆的堆标识（不是合法座位号，故用负数）
BOTTOM_PILE = -1


class InferenceError(AccountingError):
    """推断输入自相矛盾 —— 判定为识别错误，必须告警而不是继续算。

    继承 `AccountingError`：上层既有的「记账异常」处理路径能直接接住它，
    不必为推断单独开一条错误通道。
    """


class VoidTracker:
    """从出牌历史推导各家的「空门」（void）。

    判据是标准跟牌规则：**领出该组牌时，有该组的牌就必须跟，有多少跟多少**。
    因此若某家在领出组为 g 的一墩里打出的 g 组牌**少于领出的张数**，
    说明它把 g 组打光了 —— 对 g 组空门。

    为空门保留的意义：这是唯一能**排除**整组面值的信息（B3 的排除性断言靠它）。

    ⚠️ 两条保守的放弃规则（宁可不知道，也不推错）：

    1. 领出是甩牌 / 杂牌（结构 `MIXED`）时不推导 —— 跟牌规则地区差异大
    2. 该家本墩无出牌记录、或本墩结构不统一时不推导
    """

    def __init__(self, players: int) -> None:
        self.players = players
        self._voids: dict[int, set[int]] = {s: set() for s in range(players)}
        self.skipped_mixed_leads = 0
        self.deductions = 0

    # ---------- 事件 ----------

    def on_trick(self, plays: Sequence[PlayedCards], trump: TrumpInfo
                 ) -> tuple[tuple[int, int], ...]:
        """记录一墩的出牌，返回本墩**新推出**的 `(座位, 组)` 空门。

        plays 必须按出牌顺序给出，plays[0] 为领出方。
        """
        if len(plays) < 2:
            return ()
        lead = plays[0]
        if not lead.cards:
            return ()
        if len({group_of(c, trump) for c in lead.cards}) != 1:
            # 领出本身就跨组（甩牌），跟牌规则不适用
            self.skipped_mixed_leads += 1
            return ()
        if structure_of(lead.cards) is Structure.MIXED:
            self.skipped_mixed_leads += 1
            return ()

        lead_group = group_of(lead.cards[0], trump)
        lead_count = len(lead.cards)
        new: list[tuple[int, int]] = []
        for p in plays[1:]:
            if p.seat not in self._voids:
                raise InferenceError(f"非法座位: {p.seat}")
            if not p.cards:
                continue
            followed = sum(1 for c in p.cards if group_of(c, trump) == lead_group)
            if followed >= lead_count:
                continue
            if lead_group not in self._voids[p.seat]:
                self._voids[p.seat].add(lead_group)
                self.deductions += 1
                new.append((p.seat, lead_group))
        return tuple(new)

    # ---------- 查询 ----------

    def is_void(self, seat: int, group: int) -> bool:
        return group in self._voids.get(seat, ())

    def void_groups(self, seat: int) -> frozenset[int]:
        return frozenset(self._voids.get(seat, ()))

    def as_mapping(self) -> dict[int, frozenset[int]]:
        """给 `infer_per_seat(voids=...)` 用。"""
        return {s: frozenset(g) for s, g in self._voids.items()}


@dataclass(frozen=True)
class SeatInference:
    """一次推断的完整结果。

    `candidates` / `certain` 均按**座位号**索引，长度 = 玩家数。
    `certain_min[s][f]` 是「座位 s 至少持有几张 f」，比 `certain[s]` 多带了张数。
    """

    players: int
    own_seat: int
    candidates: tuple[frozenset[Card], ...]
    certain: tuple[frozenset[Card], ...]
    certain_min: tuple[Counter[Card], ...]
    bottom_candidates: frozenset[Card]
    bottom_certain_min: Counter[Card]
    unseen_faces: int                  # 未见池里还有多少种面值（平凡解的规模基准）
    iterations: int
    own_hand_given: bool = False       # 是否拿到了自己手牌的确切内容

    # ---------- 紧致度 ----------

    @property
    def candidate_total(self) -> int:
        """`candidates` 总规模。紧致度回归的基线量：**越小越紧**。

        只统计**其他家**：自己那一家本就是已知集合，不参与紧致度比较。
        """
        return sum(len(self.candidates[s]) for s in range(self.players)
                   if s != self.own_seat)

    @property
    def trivial_candidate_total(self) -> int:
        """平凡解（给其他每一家整个未见池）的规模，用于判断有没有退化成没推断。"""
        return self.unseen_faces * (self.players - 1)

    @property
    def is_trivial(self) -> bool:
        """其他家的 candidates 是否都等于整个未见池（= 等于没有推断）。"""
        return all(len(self.candidates[s]) == self.unseen_faces
                   for s in range(self.players) if s != self.own_seat)

    def certain_of(self, seat: int) -> frozenset[Card]:
        return self.certain[seat]

    def candidates_of(self, seat: int) -> frozenset[Card]:
        return self.candidates[seat]

    # ---------- 显示 ----------

    def seat_line(self, seat: int, label: str, max_certain: int = 4) -> str:
        """一家的紧凑文本，给悬浮窗用。"""
        if seat == self.own_seat:
            return f"{label} {'已知' if self.own_hand_given else '≤已知集合'}"
        n = len(self.candidates[seat])
        line = f"{label} ≤{n} 种"
        marks = sorted(self.certain[seat], key=lambda c: c.label())
        if marks:
            head = "、".join(c.label() for c in marks[:max_certain])
            more = f" 等{len(marks)}种" if len(marks) > max_certain else ""
            line += f" 必持 {head}{more}"
        return line

    def summary_lines(self, labels: Mapping[int, str] | None = None) -> list[str]:
        """所有**其他家**的推断摘要（自己那家已在剩余牌面板里说清了）。"""
        labels = labels or {}
        out = []
        for s in range(self.players):
            if s == self.own_seat:
                continue
            out.append(self.seat_line(s, labels.get(s, f"座位{s}")))
        if self.bottom_certain_min or self.bottom_candidates:
            out.append(f"底牌 ≤{len(self.bottom_candidates)} 种")
        return out


def infer_per_seat(pool: UnseenPool,
                   trump: TrumpInfo,
                   voids: Mapping[int, Iterable[int]] | None = None,
                   own_hand_remaining: Sequence[Card] | None = None,
                   max_iterations: int = 64) -> SeatInference:
    """按家推断主入口。

    参数：
        pool               未见池（提供面值计数、各家持牌数、未知底牌容量）
        trump              主牌信息（判「组」用：空门按组记，不按花色记）
        voids              各家的已知空门：`{座位: 组集合}`，来自 `VoidTracker`
        own_hand_remaining 自己手上**剩余**的牌（可选）。给了就让自己那家
                           精确到张；不给则退化为已知集合这个安全超集
        max_iterations     上下界传播的迭代上限（收敛即提前退出）

    抛出 `InferenceError`：当约束之间无法同时满足时（识别错误的强信号），
    绝不返回一个"看起来能用"的结果。
    """
    rule = pool.rule
    players = rule.players
    own_seat = pool.own_seat
    if not (0 <= own_seat < players):
        raise InferenceError(f"非法座位: {own_seat}")

    counts: dict[Card, int] = {f: n for f, n in pool.as_counter().items() if n > 0}
    faces = sorted(counts)                     # Card 是 order=True，排序稳定
    unseen_faces = len(faces)

    void_map: dict[int, frozenset[int]] = {}
    for s in range(players):
        raw = (voids or {}).get(s, ())
        void_map[s] = frozenset(int(g) for g in raw)

    other_seats = [s for s in range(players) if s != own_seat]
    piles = [*other_seats, BOTTOM_PILE]

    held = pool.seat_holds()
    capacity: dict[int, int] = {s: held[s] for s in other_seats}
    capacity[BOTTOM_PILE] = pool.bottom_unknown()

    # 每个堆能持有的面值（空门直接排除整组）
    allowed: dict[int, frozenset[Card]] = {
        s: frozenset(f for f in faces if group_of(f, trump) not in void_map[s])
        for s in other_seats
    }
    allowed[BOTTOM_PILE] = frozenset(faces)

    lower: dict[tuple[int, Card], int] = {(p, f): 0 for p in piles for f in faces}
    upper: dict[tuple[int, Card], int] = {
        (p, f): (min(counts[f], capacity[p]) if f in allowed[p] else 0)
        for p in piles for f in faces
    }

    iterations = 0
    for iterations in range(1, max_iterations + 1):
        changed = False

        # ---- ① 面值级强制分配：c 张 f 必须落在能持有它的堆里 ----
        for f in faces:
            c = counts[f]
            # 容量为 0 的堆不算"可持有它"——否则底牌容量为 0 时会被误算成藏牌处
            holes = [p for p in piles if f in allowed[p] and capacity[p] > 0]
            if not holes:
                raise InferenceError(
                    f"{f.label()} 还剩 {c} 张，但没有任何堆能持有它 —— "
                    f"空门推断或识别有误")
            room = sum(upper[(p, f)] for p in holes)
            if room < c:
                raise InferenceError(
                    f"{f.label()} 还剩 {c} 张，但其他家与底牌最多只能容纳 "
                    f"{room} 张 —— 识别重复或错认")
            for p in holes:
                need = c - sum(upper[(q, f)] for q in holes if q != p)
                if need > lower[(p, f)]:
                    lower[(p, f)] = need
                    changed = True

        # ---- ② 上界夹紧：别人已经"必然持有"的部分，我就不能有 ----
        for p in piles:
            used = sum(lower[(p, f)] for f in faces)
            if used > capacity[p]:
                raise InferenceError(
                    f"{_pillar_label(p, own_seat)} 只有 {capacity[p]} 张，"
                    f"但约束要求它至少持有 {used} 张 —— 识别错误")
            free = capacity[p] - used
            for f in faces:
                lb = lower[(p, f)]
                ub = 0 if f not in allowed[p] else min(counts[f], capacity[p])
                ub = min(ub, counts[f] - sum(lower[(q, f)] for q in piles if q != p))
                ub = min(ub, free + lb)
                if ub < lb:
                    raise InferenceError(
                        f"关于 {f.label()} 的分配约束自相矛盾（堆 "
                        f"{_pillar_label(p, own_seat)} 至少需要 {lb} 张，"
                        f"最多只能有 {ub} 张）—— 识别错误")
                if ub < upper[(p, f)]:
                    upper[(p, f)] = ub
                    changed = True

        # ---- ③ 堆容量自洽：每家必须能用手上的牌填满自己的张数 ----
        for p in piles:
            hi = sum(upper[(p, f)] for f in faces)
            if hi < capacity[p]:
                raise InferenceError(
                    f"{_pillar_label(p, own_seat)} 有 {capacity[p]} 张牌，"
                    f"但按当前约束它最多只能持有 {hi} 张 —— "
                    f"空门推断或识别有误")

        if not changed:
            break
    else:
        raise InferenceError(f"上下界传播在 {max_iterations} 轮内未收敛")

    # ---------- 结果组装 ----------

    candidates: list[frozenset[Card] | None] = [None] * players
    certain: list[frozenset[Card] | None] = [None] * players
    certain_min: list[Counter[Card]] = [Counter() for _ in range(players)]

    for s in other_seats:
        candidates[s] = frozenset(f for f in faces if upper[(s, f)] >= 1)
        certain_min[s] = Counter({f: lower[(s, f)]
                                  for f in faces if lower[(s, f)] > 0})
        certain[s] = frozenset(certain_min[s])

    own_hand_given = own_hand_remaining is not None
    if own_hand_given:
        own = Counter(own_hand_remaining)
        if sum(own.values()) != held[own_seat]:
            raise InferenceError(
                f"自己手牌应为 {held[own_seat]} 张，实际 {sum(own.values())} 张")
        unknown = own - pool.own_known()
        if unknown:
            labels = "、".join(sorted(c.label() for c in unknown))
            raise InferenceError(f"自己手牌中有不在此前已知集合内的牌: {labels}")
        candidates[own_seat] = frozenset(own)
        certain[own_seat] = frozenset(own)
        certain_min[own_seat] = own
    else:
        # 不知道已打出的具体是哪几张，只能给出安全超集；certain 留空
        candidates[own_seat] = frozenset(pool.own_known())
        certain[own_seat] = frozenset()

    cand_list = [c for c in candidates if c is not None]
    cert_list = [c for c in certain if c is not None]
    if len(cand_list) != players or len(cert_list) != players:
        raise InferenceError("内部错误：推断结果未覆盖全部座位")

    return SeatInference(
        players=players,
        own_seat=own_seat,
        candidates=tuple(cand_list),
        certain=tuple(cert_list),
        certain_min=tuple(certain_min),
        bottom_candidates=frozenset(f for f in faces
                                    if upper[(BOTTOM_PILE, f)] >= 1),
        bottom_certain_min=Counter({f: lower[(BOTTOM_PILE, f)]
                                    for f in faces if lower[(BOTTOM_PILE, f)] > 0}),
        unseen_faces=unseen_faces,
        iterations=iterations,
        own_hand_given=own_hand_given,
    )


def _pillar_label(pile: int, own_seat: int) -> str:
    """报错与日志里指代某个堆。"""
    if pile == BOTTOM_PILE:
        return "未知底牌堆"
    return f"座位 {pile}" + ("（自己）" if pile == own_seat else "")
