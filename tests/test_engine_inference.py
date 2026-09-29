"""按家推断（功能 B）测试。

设计文档 §10.1 明确：仅断言 `ground_truth ⊆ candidates` 是**可被平凡满足**的
（把整个未见池给每一家即可恒真），因此本文件的核心是四条断言同时成立：

| # | 断言 | 防的是什么 |
|---|---|---|
| B1 | `ground_truth[s] ⊆ candidates[s]` | soundness |
| B2 | `certain[s] ⊆ ground_truth[s]` | certain 的 soundness |
| B3 | 可证在别家的牌不得出现在本家 candidates | **平凡全集解** |
| B4 | 构造场景中存在 `certain[s] ≠ ∅` | **恒返回空集解** |

另有紧致度回归（`candidate_total` 基线）、不动点收敛与矛盾检测。

## 两类场景

- `Scene`：随机发牌 + 逐墩推进，用于 B1/B2 的"真实对局"式 soundness 检查
- `ConstructedScene`：**手写牌型**，用于 B3/B4。设计文档要求的是"构造场景"，
  随机发牌只能碰运气凑出空门，靠不住；手写牌型可以精确指定谁空门、余牌逼到谁头上

真值来源：场景持有完整手牌，出牌时同步从手牌移除，因此"剩余手牌"就是货真价实的
ground truth，而不是另算一份。
"""

from __future__ import annotations

import random

import pytest

from shengji.cards import Card
from shengji.engine.accounting import (
    VARIANT_RULES,
    AccountingError,
    KnownSet,
    UnseenPool,
    deck_composition,
)
from shengji.engine.inference import (
    BOTTOM_PILE,
    InferenceError,
    SeatInference,
    VoidTracker,
    infer_per_seat,
)
from shengji.engine.trick import PlayedCards
from shengji.engine.trump import GROUP_TRUMP, parse_trump, group_of

S, H, D, C = 0, 1, 2, 3
RULE = VARIANT_RULES[(4, 2)]
TRUMP = parse_trump("S2")          # 主 ♠、级牌 2：级牌属主牌组，与花色判定不同

# 紧致度基线（2026-09-29 实测值；变大即推断退化，变小说明变紧需同步核对）
BASELINE_CONSTRUCTED = 112      # 结构场景：无信息全集为 114
BASELINE_MIDGAME = 113          # 随机对局推进 6 墩后：无信息全集为 114


# ============ 场景 ============

class Scene:
    """随机发牌的完整牌局场景，含真值与出牌历史。"""

    def __init__(self, seed: int = 7, role: str = "defender",
                 own_seat: int = 0, hands: list[list[Card]] | None = None,
                 buried: list[Card] | None = None) -> None:
        if hands is None:
            deck = sorted(deck_composition(2).elements())
            random.Random(seed).shuffle(deck)
            hands = [deck[i * 25:(i + 1) * 25] for i in range(4)]
            buried = deck[100:]
        assert buried is not None
        self.hands = [list(h) for h in hands]
        self.buried = list(buried)
        self.players = len(self.hands)
        self.own_seat = own_seat
        self.role = role
        self.trump = TRUMP

        if role == "declarer":
            known = KnownSet.for_declarer(self.hands[own_seat], self.buried, RULE)
        else:
            known = KnownSet.for_defender(self.hands[own_seat], RULE)
        self.pool = UnseenPool(RULE, known, own_seat=own_seat)
        self.voids = VoidTracker(self.players)
        self.tricks: list[list[PlayedCards]] = []

    # ---------- 出牌 ----------

    def count_group(self, seat: int, group: int) -> int:
        return sum(1 for c in self.hands[seat] if group_of(c, self.trump) == group)

    def leader_for(self, group: int) -> int | None:
        """挑一个手上还有该组牌的座位（优先自己之外、牌最多的）。"""
        usable = [s for s in range(self.players) if self.count_group(s, group) > 0]
        if not usable:
            return None
        return max(usable, key=lambda s: (s != self.own_seat,
                                          self.count_group(s, group)))

    def _follower_cards(self, seat: int, group: int, n: int) -> list[Card]:
        """按跟牌规则出 n 张：有该组就出该组，不够再用别组补。"""
        hand = self.hands[seat]
        same = [c for c in hand if group_of(c, self.trump) == group]
        other = [c for c in hand if group_of(c, self.trump) != group]
        return (same + other)[:n]

    def play_trick(self, leader: int, group: int, n: int = 1) -> list[PlayedCards]:
        n = min(n, self.count_group(leader, group))
        assert n > 0, f"座位 {leader} 没有 {group} 组的牌"
        order = [leader] + [(leader + i) % self.players
                            for i in range(1, self.players)]
        plays: list[PlayedCards] = []
        for i, seat in enumerate(order):
            if i == 0:
                cards = [c for c in self.hands[seat]
                         if group_of(c, self.trump) == group][:n]
            else:
                cards = self._follower_cards(seat, group, n)
            assert len(cards) == n, f"座位 {seat} 出不满 {n} 张"
            for c in cards:
                self.hands[seat].remove(c)
            plays.append(PlayedCards(seat=seat, cards=tuple(cards)))
        for p in plays:
            self.pool.on_play(p.seat, list(p.cards))
        self.voids.on_trick(plays, self.trump)
        self.tricks.append(plays)
        return plays

    def lead_until_void(self, group: int, others_void: int = 3,
                        max_tricks: int = 12) -> None:
        """连续领出同一组，直到至少 `others_void` 家（自己之外）对它空门。"""
        for _ in range(max_tricks):
            voided = [s for s in range(self.players)
                      if s != self.own_seat and self.count_group(s, group) == 0]
            if len(voided) >= others_void:
                return
            leader = self.leader_for(group)
            if leader is None:
                return
            self.play_trick(leader, group, 1)

    def drain(self, victim: int, max_tricks: int = 30) -> None:
        """把某家的手牌全部打完（每墩从它手上拿走一张）。

        领出组从它**当前还有的组**里挑，否则会卡在"领出的组它早就没了"上。
        """
        for _ in range(max_tricks):
            if not self.hands[victim]:
                return
            for group in sorted({group_of(c, self.trump) for c in self.hands[victim]}):
                leader = self.leader_for(group)
                if leader is not None:
                    self.play_trick(leader, group, 1)
                    break
            else:
                return

    # ---------- 推断 ----------

    def ground_truth(self) -> list[set[Card]]:
        return [set(self.hands[s]) for s in range(self.players)]

    def infer(self, **kw) -> SeatInference:
        return infer_per_seat(self.pool, self.trump,
                              voids={**self.voids.as_mapping(), **kw.pop("voids", {})},
                              own_hand_remaining=list(self.hands[self.own_seat]),
                              **kw)


def assert_soundness(scene: Scene, inf: SeatInference) -> None:
    """B1 + B2，逐家检查。"""
    truth = scene.ground_truth()
    for s in range(scene.players):
        assert truth[s] <= inf.candidates[s], (
            f"B1 被破坏：座位 {s} 实际持有 "
            f"{sorted(c.label() for c in truth[s] - inf.candidates[s])}，"
            f"却不在 candidates 里")
        assert inf.certain[s] <= truth[s], (
            f"B2 被破坏：座位 {s} 被断言必持 "
            f"{sorted(c.label() for c in inf.certain[s] - truth[s])}，实际没有")


def _construct_heart_void_scene(role: str = "declarer",
                                own_hearts: int = 25) -> Scene:
    """手写牌型：**红桃只出现在自己与上家手上，下家/对家对红桃空门**。

    - 自己拿 `own_hearts` 张红桃（其余红桃全部落在上家 3）
    - 下家（1）与对家（2）手上全是非红桃 → **结构上**对红桃组空门
    - 庄家视角（底牌容量 0）下，未见池里的红桃**必然**在上家手里（B4）
    - 而 1、2 两家的 candidates 里不得出现任何红桃（B3）

    这是**结构可证**的，不依赖随机发牌碰运气。
    """
    assert 20 <= own_hearts <= 26
    hearts = [Card(rank=r, suit=H) for r in range(2, 15) for _ in range(2)]
    non_hearts = [c for c in sorted(deck_composition(2).elements())
                  if c.joker or c.suit != H]
    assert len(hearts) == 26 and len(non_hearts) == 82

    rest = hearts[own_hearts:]                    # 剩下的红桃，全部给上家
    n_rest = len(rest)
    own = hearts[:own_hearts] + non_hearts[:25 - own_hearts]
    base = 75 - own_hearts                        # 上家非红桃部分的起始下标
    hands = [own,
             non_hearts[25 - own_hearts:50 - own_hearts],
             non_hearts[50 - own_hearts:75 - own_hearts],
             non_hearts[base:base + 25 - n_rest] + rest]
    buried = non_hearts[base + 25 - n_rest:base + 25 - n_rest + 8]
    assert sum(len(h) for h in hands) + len(buried) == 108
    return Scene(role=role, hands=hands, buried=buried)


# ============ 空门推导 ============

def _trick(*plays: tuple[int, tuple[Card, ...]]) -> list[PlayedCards]:
    return [PlayedCards(seat=s, cards=c) for s, c in plays]


def test_void_tracker_detects_off_group_follow():
    vt = VoidTracker(4)
    new = vt.on_trick(_trick((1, (Card(rank=5, suit=H),)),
                             (2, (Card(rank=7, suit=D),))), TRUMP)
    assert new == ((2, H),)
    assert vt.is_void(2, H) and not vt.is_void(1, H)


def test_void_tracker_keeps_quiet_when_following_suit():
    vt = VoidTracker(4)
    assert vt.on_trick(_trick((1, (Card(rank=5, suit=H),)),
                              (2, (Card(rank=9, suit=H),))), TRUMP) == ()
    assert not vt.is_void(2, H)


def test_void_tracker_tracks_trump_group():
    """主牌被领出时跟不出主牌的某家，对**主牌组**空门（不是对某个花色）。"""
    vt = VoidTracker(4)
    vt.on_trick(_trick((1, (Card.big_joker(),)),
                       (2, (Card(rank=5, suit=H),))), TRUMP)
    assert vt.is_void(2, GROUP_TRUMP)


def test_void_tracker_partial_follow_means_exhausted():
    """领出对子、只跟出 1 张同组牌 —— 说明该组已打光。"""
    vt = VoidTracker(4)
    vt.on_trick(_trick((1, (Card(rank=9, suit=D), Card(rank=9, suit=D))),
                       (2, (Card(rank=4, suit=D), Card(rank=6, suit=C)))), TRUMP)
    assert vt.is_void(2, D)


def test_void_tracker_skips_mixed_lead():
    """甩牌（跨组领出）的跟牌规则地区差异大 —— 保守地不推导。"""
    vt = VoidTracker(4)
    lead = (Card(rank=9, suit=D), Card(rank=4, suit=C))
    assert vt.on_trick(_trick((1, lead), (2, (Card(rank=6, suit=H),))), TRUMP) == ()
    assert vt.skipped_mixed_leads == 1
    assert vt.void_groups(2) == frozenset()


def test_void_tracker_rejects_illegal_seat():
    vt = VoidTracker(4)
    with pytest.raises(InferenceError):
        vt.on_trick(_trick((1, (Card(rank=5, suit=H),)),
                           (9, (Card(rank=6, suit=D),))), TRUMP)


# ============ 信息不足时不假装知道 ============

def test_opening_is_honestly_trivial():
    """开局没有任何信息 —— candidates 只能是整个未见池，certain 必须为空。

    这不是实现退化：把某面值给任一其他家都成立，全集是**唯一可靠**的答案。
    紧致度必须来自信息（空门、已出牌），不能来自猜测。
    """
    sc = Scene()
    inf = sc.infer()
    assert_soundness(sc, inf)
    assert inf.certain[1] == inf.certain[2] == inf.certain[3] == frozenset()
    assert inf.is_trivial, "开局无信息，应退化为全集（这是正确的）"


# ============ B1 / B2：出牌推进下的 soundness ============

@pytest.mark.parametrize("role", ["defender", "declarer"])
def test_soundness_midgame(role):
    sc = Scene(seed=11, role=role)
    for _ in range(6):
        leader = sc.leader_for(H)
        if leader is None:
            break
        sc.play_trick(leader, H, 1)
    inf = sc.infer()
    assert_soundness(sc, inf)
    assert inf.iterations >= 1


def test_soundness_with_pair_leads():
    """跟牌张数大于 1（对子）时，空门推导与推断同样不得出错。"""
    sc = Scene(seed=23, role="defender")
    for _ in range(4):
        leader = sc.leader_for(C)
        if leader is None:
            break
        sc.play_trick(leader, C, 2)
    inf = sc.infer()
    assert_soundness(sc, inf)


def test_deduced_voids_always_exclude_their_group():
    """数据驱动的不变量：**每一个**推出来的空门，都必须排除那一整组面值。"""
    sc = Scene(seed=31, role="declarer")
    sc.lead_until_void(H, others_void=3)
    inf = sc.infer()
    assert_soundness(sc, inf)

    unseen = set(sc.pool.as_counter())
    checked = 0
    for seat in range(sc.players):
        if seat == sc.own_seat:
            continue
        for group in sc.voids.void_groups(seat):
            checked += 1
            leaked = [c for c in inf.candidates[seat]
                      if group_of(c, sc.trump) == group]
            assert not leaked, f"座位 {seat} 对组 {group} 空门，却有 {leaked}"
            excluded = {c for c in unseen if group_of(c, sc.trump) == group}
            assert len(inf.candidates[seat]) <= inf.unseen_faces - len(excluded), (
                "空门排除没有体现在 candidates 规模上")
    assert checked > 0, "场景没有推出任何空门，测试本身失效"
    # 注意：连领七八轮红桃后，未见池里的红桃多半已被打完 ——
    # 此时"排除整组"是空操作，candidates 自然可能仍等于全集，不算退化。
    # 非平凡排除由 test_constructed_void_excludes_whole_group 固定。


# ============ B3：排除性（防平凡全集解）===========

def test_constructed_void_excludes_whole_group():
    sc = _construct_heart_void_scene()
    inf = sc.infer(voids={1: {H}, 2: {H}})
    assert_soundness(sc, inf)

    for seat in (1, 2):
        leaked = [c for c in inf.candidates[seat] if group_of(c, sc.trump) == H]
        assert not leaked, f"座位 {seat} 对红桃空门，却出现 {leaked}"
        assert Card(rank=14, suit=H) not in inf.certain[seat]

    assert not inf.is_trivial, "出现了平凡全集解（B3 的意义就在这里）"
    assert inf.candidate_total < inf.trivial_candidate_total


def test_seat_out_of_cards_has_empty_candidates():
    """某家牌已全部打完 —— 容量 0 的排除：它不可能再持有任何牌。"""
    sc = Scene(seed=3, role="declarer")
    victim = 1
    sc.drain(victim)
    assert not sc.hands[victim], "场景没能把该家打完，测试本身失效"

    inf = sc.infer()
    assert inf.candidates[victim] == frozenset()
    assert inf.certain[victim] == frozenset()
    assert_soundness(sc, inf)


def test_candidates_never_exceed_unseen_pool():
    """其他家的 candidates 只能来自未见池：已明牌的牌绝不允许出现。"""
    sc = Scene(seed=17, role="defender")
    for _ in range(5):
        leader = sc.leader_for(D)
        if leader is None:
            break
        sc.play_trick(leader, D, 1)
    inf = sc.infer()
    unseen = set(sc.pool.as_counter())
    for s in range(sc.players):
        if s == sc.own_seat:
            # 自己那一家走的是「已知集合」路径，不在未见池里
            assert inf.candidates[s] <= set(sc.pool.own_known())
            continue
        assert inf.candidates[s] <= unseen


# ============ B4：必然持有（防恒空集解）============

def test_certain_is_nonempty_when_forced():
    """庄家视角（底牌容量 0）+ 1、2 家红桃空门 → 剩余红桃必然全在上家手里。"""
    sc = _construct_heart_void_scene(role="declarer")
    inf = sc.infer(voids={1: {H}, 2: {H}})
    assert_soundness(sc, inf)

    heart = Card(rank=14, suit=H)
    assert heart in inf.candidates[3]
    assert inf.certain[3] == {heart}, "B4 被破坏：被逼死的 ♥A 没进 certain"
    assert inf.certain_min[3][heart] == sc.pool.count(heart) == 1
    # 该花色余牌（未见池里的全部红桃）都在这一家
    unseen_h = {c for c in sc.pool.as_counter() if group_of(c, sc.trump) == H}
    assert unseen_h == {heart} and unseen_h <= inf.certain[3]


def test_declarer_bottom_leaves_no_hidden_pile():
    """庄家视角没有未知底牌堆，因此不会把牌"藏"在底牌里而放弃推断。"""
    sc = _construct_heart_void_scene(role="declarer")
    inf = sc.infer(voids={1: {H}, 2: {H}})
    assert inf.bottom_candidates == frozenset()
    assert inf.bottom_certain_min == {}


def test_defender_bottom_capacity_blocks_certainty():
    """非庄家时 8 张底牌是合法的藏牌处 —— 同一个局面**不敢**下断言。

    这是设计文档缺陷三的验收：底牌槽位参与求解，所以不能把
    "剩下的都在这一家"当成必然（自己手上 25 张红桃 + 底牌 8 张，
    完全可以装下 ♥A）。
    """
    sc = _construct_heart_void_scene(role="defender")
    inf = sc.infer(voids={1: {H}, 2: {H}})
    assert_soundness(sc, inf)
    heart = Card(rank=14, suit=H)
    assert heart in inf.candidates[3]
    assert heart not in inf.certain[3], "底牌能藏下 ♥A，不该断言必然"
    assert not [c for c in inf.certain[3] if group_of(c, sc.trump) == H]


def test_certain_min_carries_multiplicity():
    """两副牌下面值可有多张：certain_min 要给出张数，而不仅是"有没有"。"""
    sc = _construct_heart_void_scene(role="declarer", own_hearts=24)
    inf = sc.infer(voids={1: {H}, 2: {H}})
    assert_soundness(sc, inf)

    heart = Card(rank=14, suit=H)
    assert inf.certain[3] == {heart}
    assert inf.certain_min[3][heart] == 2 == sc.pool.count(heart)
    assert set(inf.certain_min[3]) == inf.certain[3]


# ============ 紧致度回归 ============

def test_tightness_baseline():
    """紧致度基线：`candidate_total` 一变就报警（防止悄悄退化成返回全集）。"""
    sc = _construct_heart_void_scene(role="declarer")
    inf = sc.infer(voids={1: {H}, 2: {H}})
    assert inf.candidate_total == BASELINE_CONSTRUCTED, (
        f"构造场景紧致度变化：{inf.candidate_total} != {BASELINE_CONSTRUCTED}"
        f"（若无正当理由，说明推断退化；变紧也要核对后同步基线）")
    assert inf.candidate_total < inf.trivial_candidate_total, "退化成了平凡全集解"

    sc2 = Scene(seed=11, role="declarer")
    for _ in range(6):
        leader = sc2.leader_for(H)
        if leader is None:
            break
        sc2.play_trick(leader, H, 1)
    inf2 = sc2.infer()
    assert inf2.candidate_total == BASELINE_MIDGAME
    assert inf2.candidate_total < inf2.trivial_candidate_total
    assert not inf2.is_trivial


def test_tightness_grows_with_information():
    """信息越多越紧：被逼死的红桃从 1 张变 3 张，candidates 必须更小。

    这条断言防的是"紧致度与信息无关"的退化实现 ——
    例如把所有约束都忽略、只按空门粗略排除。
    """
    few = _construct_heart_void_scene(role="declarer", own_hearts=25)
    few_inf = few.infer(voids={1: {H}, 2: {H}})
    more = _construct_heart_void_scene(role="declarer", own_hearts=23)
    more_inf = more.infer(voids={1: {H}, 2: {H}})

    assert_soundness(more, more_inf)
    # 被逼死的**张数**：自己少拿 2 张红桃，就有 3 张红桃必须在上家手里
    assert sum(few_inf.certain_min[3].values()) == 1
    assert sum(more_inf.certain_min[3].values()) == 3
    assert more_inf.candidate_total < few_inf.candidate_total


# ============ 矛盾检测：绝不返回"看起来能用"的结果 ============

def test_impossible_voids_are_rejected():
    """三家红桃全空门、底牌容量 0 —— 余牌无处可去，必须报错而不是硬算。"""
    sc = _construct_heart_void_scene(role="declarer")
    with pytest.raises(InferenceError) as ei:
        sc.infer(voids={1: {H}, 2: {H}, 3: {H}})
    assert "没有任何堆能持有" in str(ei.value)


def test_impossible_capacity_is_rejected():
    """某家除了红桃什么都空门，却还有 25 张牌 —— 全盘不可能，必须报错。

    注意错在哪条约束上取决于迭代顺序（既可能是"某堆装不下"，
    也可能是别的堆被逼着接下所有牌）。要断言的是**拒绝**，不是具体措辞。
    """
    sc = _construct_heart_void_scene(role="defender")
    with pytest.raises(InferenceError) as ei:
        sc.infer(voids={1: {GROUP_TRUMP, D, C},
                        2: {GROUP_TRUMP, D, C},
                        3: {GROUP_TRUMP, D, C}})
    msg = str(ei.value)
    assert "识别错误" in msg or "识别重复" in msg
    assert "至少持有" in msg or "最多只能持有" in msg or "最多只能容纳" in msg


def test_inference_error_is_accounting_error():
    """推断矛盾要能被既有的记账异常处理路径接住，不必新开错误通道。"""
    assert issubclass(InferenceError, AccountingError)


# ============ 自己那一家 ============

def test_own_seat_is_exact_when_hand_given():
    sc = Scene(seed=11, role="defender")
    sc.play_trick(1, H, 1)
    sc.play_trick(2, D, 1)
    inf = sc.infer()
    assert inf.own_hand_given
    assert inf.candidates[sc.own_seat] == set(sc.hands[sc.own_seat])
    assert inf.certain[sc.own_seat] == set(sc.hands[sc.own_seat])


def test_own_seat_falls_back_to_safe_superset():
    """不给自己手牌时只能给安全超集，且**不得**声称必然（certain 留空）。"""
    sc = Scene(seed=11, role="defender")
    sc.play_trick(1, H, 1)
    inf = infer_per_seat(sc.pool, sc.trump)
    assert not inf.own_hand_given
    assert sc.ground_truth()[sc.own_seat] <= inf.candidates[sc.own_seat]
    assert inf.certain[sc.own_seat] == frozenset()


def test_own_hand_size_mismatch_is_rejected():
    sc = Scene(seed=11, role="defender")
    with pytest.raises(InferenceError):
        infer_per_seat(sc.pool, sc.trump,
                       own_hand_remaining=list(sc.hands[0])[:24])


def test_own_hand_outside_known_set_is_rejected():
    sc = Scene(seed=11, role="defender")
    bogus = list(sc.hands[0])[:24] + [Card(rank=14, suit=S)]
    with pytest.raises(InferenceError) as ei:
        infer_per_seat(sc.pool, sc.trump, own_hand_remaining=bogus)
    assert "已知集合" in str(ei.value)


def test_illegal_own_seat_is_rejected():
    """防御性检查：座位号被外部改坏时也不能静默算错。"""
    sc = Scene(seed=11)
    sc.pool.own_seat = 9
    with pytest.raises(InferenceError):
        infer_per_seat(sc.pool, sc.trump)


# ============ 显示 ============

def test_summary_lines_describe_each_other_seat():
    sc = _construct_heart_void_scene(role="declarer")
    inf = sc.infer(voids={1: {H}, 2: {H}})
    lines = inf.summary_lines({1: "上家", 2: "对家", 3: "下家"})
    assert len(lines) == 3, "三家（庄家视角没有未知底牌堆，不显示底牌行）"
    assert lines[0].startswith("上家 ≤")
    assert any("必持" in ln for ln in lines)


def test_summary_lines_include_bottom_for_defender():
    """非庄家有 8 张未知底牌 —— 底牌行必须出现，否则用户会以为牌"丢了"。"""
    sc = _construct_heart_void_scene(role="defender")
    inf = sc.infer(voids={1: {H}, 2: {H}})
    lines = inf.summary_lines({1: "上家", 2: "对家", 3: "下家"})
    assert len(lines) == 4
    assert lines[-1].startswith("底牌 ≤")


def test_seat_line_marks_own_seat_as_known():
    sc = _construct_heart_void_scene()
    inf = sc.infer()
    assert "已知" in inf.seat_line(sc.own_seat, "自己")
