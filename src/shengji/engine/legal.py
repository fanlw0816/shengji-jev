"""合法着法枚举（Plan 7 的 7a；规则基础见设计文档 §7.3）。

把「能出什么」与「该出什么」**彻底分开** —— 本模块只回答前者：
不评估、不排序、不给建议（那是 7b / 7c 的事）。

设计约束（Plan 7 文档 §3）：

- **纯函数**：不碰 IO，可完全单测覆盖
- 复用 `engine/trick.py` 的结构判定做**自洽校验** —— 枚举出的每一手都要能被它判为合法
- 与手牌对账：**绝不枚举手上不存在的牌**（识别错认会在这一步暴露）

### 关于「甩牌」的诚实边界

甩牌合法的完整判据是「所甩各张都是该门最大」，**这依赖别家手牌**，
只看自己手牌无法判定。故本模块对甩牌只做**本家自洽**检查
（所甩是该门从大到小的最大若干张），并用 `Move.verified=False` 标出来。

这正是 Plan 7 把「甩牌合法性规则」列为前置阻塞的原因：
在 Phase 0 实测定案之前，7a **不能声称**甩牌集合是完备的。

### 未验证的规则取值

规则取值全部集中在 `RuleProfile`，每项标注出处；`source="UNVERIFIED"` 的
profile 由 `get_rule_profile()` **拒绝启动**，与 `get_variant_rule` 同机制 ——
绝不用一个编出来的规则去算真实牌局。
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from itertools import combinations

from ..cards import Card
from .trick import PlayedCards, Structure, structure_matches, structure_of
from .trump import TrumpInfo, group_of, sequence_index, sort_desc

MAX_COMBOS = 100_000
"""枚举上限：候选组合数超过此值就**报错拒绝**，而不是悄悄截断。

截断会得到一个「看起来能用但漏了着法」的集合 —— 那比报错危险得多
（设计原则「失败要可见」）。现实牌局里跟牌只需从领出组里取，远达不到这个量级。
"""


class LegalError(Exception):
    """合法着法枚举的前提不满足 —— 必须拒绝，不得返回一个"看起来能用"的集合。"""


# ---------- 规则取值（每项必须有出处）----------


@dataclass(frozen=True)
class RuleProfile:
    """跟牌与甩牌的规则取值。**未知的取值必须标 `UNVERIFIED` 并拒绝启动。**

    这些字段是「地区差异大、必须实测才能定」的那部分规则 ——
    算法可以照写，取值不能猜。
    """

    follow_required: bool = True
    """有领出组（该花色或主牌）时必须跟。设计文档 §7.3。"""

    must_play_as_many_as_possible: bool = True
    """该组张数不足领出张数时，**必须全出**该组，差额才可垫牌。设计文档 §7.3。"""

    structure_follow_required: bool = True
    """领出对子 / 拖拉机时，若手上该组有同型**必须跟同型**，没有才能拆。设计文档 §7.3。"""

    throw_requires_local_top: bool = True
    """甩牌必须是本家该门从大到小的最大若干张（**本家自洽**部分，可离线判定）。"""

    throw_single_group: bool = True
    """甩牌是否必须同门。设计文档 Phase 0 列为待实测项。"""

    throw_max_cards: int = 8
    """甩牌最大张数。⚠️ 8 只是性能估算用的假设值（设计文档 §3 最坏场景），**未实测**。"""

    tractor_skips_level: bool = True
    """主花色连对是否把级牌「挖掉」后仍算连续。

    ⚠️ 现取 True —— 与 `trick.py._is_tractor` / `trump.sequence_index()` 的
    **现行行为一致**，也与设计文档 §7.6 的牌力次序自洽（级牌自成一层，
    主花色其余点数在它之上/之下各自连续）。两者必须同时改，否则 §3 的
    自洽校验会失败（有测试盯着）。

    但仍属 **UNVERIFIED**：级牌语义未经过实测确认（todo.md 的未决项）。
    要改的话**只改 `trump.sequence_index` 一处** —— 墩赢家与合法枚举共用它，
    改一处两边一起变；这个字段只是把取值记下来备查。"""

    source: str = "UNVERIFIED"
    """`"documented"` | `"measured"` | `"UNVERIFIED"`。"""


PROFILES: dict[str, RuleProfile] = {
    # 取值全部照设计文档 §7.3 的默认描述，但**没有任何一项经过实测确认**。
    "default": RuleProfile(source="UNVERIFIED"),
}


def get_rule_profile(name: str = "default") -> RuleProfile:
    """取规则 profile。未验证的取值**拒绝启动**，不采用猜测值。

    与 `accounting.get_variant_rule` 同一套机制：
    数据表 + 出处标记 + 未验证即拒绝。
    """
    profile = PROFILES.get(name)
    if profile is None:
        raise LegalError(f"未知的规则 profile: {name!r}（现有：{sorted(PROFILES)}）")
    if profile.source == "UNVERIFIED":
        raise LegalError(
            f"规则 profile {name!r} 的跟牌 / 甩牌取值尚未实测确认，拒绝启动。"
            f"请先完成 Phase 0 的「跟牌规则验证」与「甩牌合法性规则」标定。")
    return profile


# ---------- 着法 ----------


@dataclass(frozen=True)
class Move:
    """一手候选着法。**只描述"能这么出"，不含任何好坏评价。**"""

    cards: tuple[Card, ...]
    structure: Structure
    group: int                 # 领出时 = 本手所属组；跟牌时 = 领出组
    matched: bool              # 结构是否与领出一致（领出时恒 True）
    verified: bool = True      # False = 依赖本机无法判定的前提（如甩牌的全局合法性）
    note: str = ""

    def label(self) -> str:
        return " ".join(c.label() for c in self.cards)

    def codes(self) -> tuple[str, ...]:
        """本手各张的 ASCII 编码（已排序，用于对账与去重）。"""
        return tuple(sorted(c.code() for c in self.cards))


@dataclass(frozen=True)
class LegalMoves:
    """枚举结果。

    比计划稿的 `list[Move]` 多带两个字段，是刻意为之：
    「集合是不是完备的」本身就是必须让调用方看见的信息 ——
    7c 若在一个**不完备**的合法集合上做搜索，得到的最优解是假的。
    """

    moves: tuple[Move, ...]
    complete: bool = True
    notes: tuple[str, ...] = ()


# ---------- 枚举 ----------


def legal_moves(hand: Sequence[Card], lead: PlayedCards | None,
                trump: TrumpInfo, profile: RuleProfile,
                *, max_combos: int = MAX_COMBOS) -> LegalMoves:
    """枚举当前手牌下的全部合法着法。

    `lead=None` 表示本家领出；否则 `lead` 为已确立的领出（`plays[0]`）。
    """
    cards = list(hand)
    if not cards:
        raise LegalError("手牌为空，无法枚举合法着法（前提缺失应走拒绝流程，不是返回空集）")
    if lead is None:
        return _lead_moves(cards, trump, profile, max_combos)
    return _follow_moves(cards, lead, trump, profile, max_combos)


# --- 领出 ---


def _lead_moves(cards: list[Card], trump: TrumpInfo, profile: RuleProfile,
                max_combos: int) -> LegalMoves:
    moves: list[Move] = []
    notes: list[str] = []

    singles: list[Card] = []
    for c in _distinct(cards):
        singles.append(c)
        moves.append(Move((c,), Structure.SINGLE, group_of(c, trump), True))

    counts = Counter(cards)
    pairs = _pairs(counts)
    for c in pairs:
        moves.append(Move((c, c), Structure.PAIR, group_of(c, trump), True))

    for tractor in _tractors(counts, trump):
        moves.append(Move(tractor, Structure.TRACTOR,
                          group_of(tractor[0], trump), True))

    throws, throw_notes = _throws(cards, trump, profile, max_combos)
    moves.extend(throws)
    notes.extend(throw_notes)

    return LegalMoves(_dedup(moves), not notes, tuple(notes))


# --- 跟牌 ---


def _follow_moves(cards: list[Card], lead: PlayedCards, trump: TrumpInfo,
                  profile: RuleProfile, max_combos: int) -> LegalMoves:
    n = len(lead.cards)
    lead_group = group_of(lead.cards[0], trump)
    lead_struct = structure_of(lead.cards, trump)

    in_group = [c for c in cards if group_of(c, trump) == lead_group]

    if profile.follow_required and in_group:
        if len(in_group) >= n:
            return _from_pool(in_group, n, lead_struct, lead_group,
                              trump, profile, max_combos)
        if not profile.must_play_as_many_as_possible:
            return _from_pool(cards, n, lead_struct, lead_group,
                              trump, profile, max_combos)
        rest = [c for c in cards if group_of(c, trump) != lead_group]
        need = n - len(in_group)
        if need > len(rest):
            raise LegalError(
                f"手牌张数不足以跟出 {n} 张（该组 {len(in_group)} 张 + 其余 {len(rest)} 张）——"
                f"手牌快照可能不完整，应走拒绝流程")
        return _pad_moves(in_group, rest, need, lead_struct, lead_group,
                          trump, max_combos)

    # 无领出组（或规则不强制跟）-> 自由垫牌
    return _from_pool(cards, n, lead_struct, lead_group,
                      trump, profile, max_combos)


def _from_pool(pool: list[Card], n: int, lead_struct: Structure, lead_group: int,
               trump: TrumpInfo, profile: RuleProfile,
               max_combos: int) -> LegalMoves:
    """从给定池子里取 n 张。**结构优先**：能跟同型就必须跟同型。"""
    if profile.structure_follow_required and lead_struct is not Structure.MIXED:
        same = list(_struct_candidates(pool, n, lead_struct, lead_group, trump))
        if same:
            return LegalMoves(tuple(same), True)
    return _combos_of(pool, n, lead_struct, lead_group, trump, max_combos)


def _struct_candidates(pool: list[Card], n: int, lead_struct: Structure,
                       lead_group: int, trump: TrumpInfo) -> Iterator[Move]:
    """池子里结构恰为 `lead_struct` 的候选着法。跟牌时 `group` 一律记领出组。"""
    if lead_struct is Structure.SINGLE:
        for c in _distinct(pool):
            yield Move((c,), Structure.SINGLE, lead_group, True)
        return
    if lead_struct is Structure.PAIR:
        for c in _pairs(Counter(pool)):
            yield Move((c, c), Structure.PAIR, lead_group, True)
        return
    if lead_struct is Structure.TRACTOR:
        for t in _tractors(Counter(pool), trump):
            if len(t) == n:
                yield Move(t, Structure.TRACTOR, lead_group, True)


def _combos_of(pool: list[Card], n: int, lead_struct: Structure, lead_group: int,
               trump: TrumpInfo, max_combos: int) -> LegalMoves:
    """池子里任取 n 张（按多重集去重）。"""
    raw = math.comb(len(pool), n)
    if raw > max_combos:
        raise LegalError(
            f"候选组合数 {raw} 超过上限 {max_combos}（池 {len(pool)} 张取 {n} 张）——"
            f"枚举被拒绝而不是静默截断，截断会漏掉着法而调用方无从察觉")

    out: list[Move] = []
    seen: set[tuple[str, ...]] = set()
    for combo in combinations(pool, n):
        cards = tuple(sort_desc(list(combo), trump))
        key = tuple(sorted(c.code() for c in cards))
        if key in seen:
            continue
        seen.add(key)
        out.append(Move(cards, structure_of(cards, trump), lead_group,
                        structure_matches(cards, lead_struct, trump)))
    return LegalMoves(tuple(out), True)


def _pad_moves(in_group: list[Card], rest: list[Card], need: int,
               lead_struct: Structure, lead_group: int, trump: TrumpInfo,
               max_combos: int) -> LegalMoves:
    """该组不足时的强制着法：**全部该组牌 + 从其余里补 `need` 张**。"""
    raw = math.comb(len(rest), need) if need else 1
    if raw > max_combos:
        raise LegalError(
            f"垫牌候选组合数 {raw} 超过上限 {max_combos}（{len(rest)} 张取 {need} 张）——"
            f"枚举被拒绝而不是静默截断")

    base = tuple(sort_desc(in_group, trump))
    out: list[Move] = []
    seen: set[tuple[str, ...]] = set()
    pad_choices = [()] if need == 0 else combinations(rest, need)
    for combo in pad_choices:
        cards = base + tuple(sort_desc(list(combo), trump))
        key = tuple(sorted(c.code() for c in cards))
        if key in seen:
            continue
        seen.add(key)
        out.append(Move(cards, structure_of(cards, trump), lead_group,
                        structure_matches(cards, lead_struct, trump)))
    return LegalMoves(tuple(out), True)


# --- 甩牌 ---


def _throws(cards: list[Card], trump: TrumpInfo, profile: RuleProfile,
            max_combos: int) -> tuple[tuple[Move, ...], list[str]]:
    """领出的甩牌候选。

    **只做本家自洽检查**（是否为该门最大若干张），全局合法性无法离线判定，
    因此结果一律 `verified=False`。跨门甩牌若规则允许，本函数不枚举（见 notes）。
    """
    notes: list[str] = []
    out: list[Move] = []

    if not profile.throw_single_group:
        notes.append("profile 允许跨门甩牌，但跨门组合未枚举（规则未定，不能凭空生成）")

    by_group: dict[int, list[Card]] = {}
    for c in cards:
        by_group.setdefault(group_of(c, trump), []).append(c)

    limit = max(2, min(profile.throw_max_cards, max_combos))
    for group, group_cards in by_group.items():
        ordered = sort_desc(group_cards, trump)
        if profile.throw_requires_local_top:
            # 只可能是「从大到小的最大若干张」这一串前缀
            for k in range(2, min(len(ordered), limit) + 1):
                cand = tuple(ordered[:k])
                if structure_of(cand, trump) is not Structure.MIXED:
                    continue        # 对子 / 拖拉机不是甩牌，已在前两类里枚举
                out.append(Move(cand, Structure.MIXED, group, True, verified=False,
                                note="甩牌合法性依赖别家手牌，本机无法判定"))
        else:
            notes.append("profile 不要求甩牌为该门最大，但全组合枚举未实现（规则未定）")
            break

    return _dedup(out), notes


# --- 小工具 ---


def _distinct(cards: list[Card]) -> list[Card]:
    seen: set[str] = set()
    out: list[Card] = []
    for c in cards:
        if c.code() in seen:
            continue
        seen.add(c.code())
        out.append(c)
    return out


def _pairs(counts: Counter[Card]) -> list[Card]:
    return [c for c, k in counts.items() if k >= 2]


def _tractors(counts: Counter[Card], trump: TrumpInfo) -> list[tuple[Card, ...]]:
    """枚举全部连对（拖拉机）。

    按「**分组 + 组内序列位置**」切连续段，而不是按「花色 + 裸点数」——
    后者会漏掉主花色的跳级连对（打 10 主 ♠ 时 ♠J♠J + ♠9♠9），
    而回验只能挡住**多生成**、挡不住**漏生成**（Plan 7 §3 的自洽校验是单向的，
    这一点在 2026-09-30 修 `_is_tractor` 时才发现）。

    生成后仍用 `structure_of` 回验：只有被引擎判为 `TRACTOR` 的才输出。
    这样即使日后规则取值变化（如 `tractor_skips_level`），枚举与墩赢家
    也不会各自漂移 —— 两边共用 `trump.sequence_index()`。
    """
    # 分组 → (组内序列位置 → 该位置的那张牌)
    paired: dict[int, dict[int, Card]] = {}
    for c, k in counts.items():
        if k < 2:
            continue
        pos = sequence_index(c, trump)
        if pos is None:
            continue        # 级牌 / 副级 / 王不成对参与连对
        paired.setdefault(group_of(c, trump), {})[pos] = c

    out: list[tuple[Card, ...]] = []
    for by_pos in paired.values():
        for run in _runs(sorted(by_pos)):
            for length in range(2, len(run) + 1):
                for start in range(len(run) - length + 1):
                    seg = run[start:start + length]
                    cand = tuple(c for p in seg for c in (by_pos[p], by_pos[p]))
                    if structure_of(cand, trump) is Structure.TRACTOR:
                        out.append(cand)
    return out


def _runs(positions: list[int]) -> Iterator[list[int]]:
    """把升序的**组内序列位置**列切成极大连续段。"""
    run: list[int] = []
    for r in positions:
        if run and r != run[-1] + 1:
            if len(run) >= 2:
                yield run
            run = []
        run.append(r)
    if len(run) >= 2:
        yield run


def _dedup(moves: list[Move]) -> tuple[Move, ...]:
    seen: set[tuple[str, ...]] = set()
    out: list[Move] = []
    for m in moves:
        key = m.codes()
        if key in seen:
            continue
        seen.add(key)
        out.append(m)
    return tuple(out)
