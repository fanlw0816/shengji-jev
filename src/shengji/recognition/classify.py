"""识别：单帧分类、多帧投票、置信度。

置信度语义（与设计文档 §5.1 一致）：
    confidence    = 每张牌「最佳与次佳模板分之差」的最小值，越小越不可靠
    frame_agreement = 跨帧投票一致率
两者是**独立的两个阈值**，任一不达标即触发人工纠正（合取式触发，见 §5.1）。
"""

from __future__ import annotations

from collections import Counter

import numpy as np

from ..cards import (
    RANK_LABELS,
    SUIT_LETTERS,
    Card,
)
from ..layout.model import LayoutModel
from .patch import CardPatch, extract_card_patches
from .templates import TemplateLibrary
from .types import CardRead, RecognitionResult, ZoneRead

_RANK_BY_LABEL = {v: k for k, v in RANK_LABELS.items()}
_SUIT_BY_LETTER = {v: k for k, v in SUIT_LETTERS.items()}


def read_patch(patch: CardPatch, lib: TemplateLibrary) -> CardRead:
    """识别单张牌的角标。"""
    r_label, r_score, r_second = lib.classify_rank(patch.rank_patch)
    s_label, s_score, s_second = lib.classify_suit(patch.suit_patch)
    margin = min(r_score - r_second, s_score - s_second)

    card: Card | None = None
    if r_label == "joker_small":
        card = Card.small_joker()
    elif r_label == "joker_big":
        card = Card.big_joker()
    elif r_label in _RANK_BY_LABEL and s_label in _SUIT_BY_LETTER:
        card = Card(rank=_RANK_BY_LABEL[r_label], suit=_SUIT_BY_LETTER[s_label])
    return CardRead(card=card, rank_score=r_score, suit_score=s_score,
                    margin=float(margin))


def read_frame(frame: np.ndarray, model: LayoutModel,
               lib: TemplateLibrary) -> RecognitionResult:
    """识别一帧画面中所有出牌区。"""
    res = RecognitionResult()
    patches = extract_card_patches(frame, model)
    for zone, plist in patches.items():
        reads = [read_patch(p, lib) for p in plist]
        zr = ZoneRead(zone=zone, cards=reads)
        zr.confidence = min((r.margin for r in reads), default=0.0)
        res.zones[zone] = zr
    res.confidence = min((z.confidence for z in res.zones.values()), default=0.0)
    return res


def majority(cards: list[Card | None]) -> Card | None:
    """对同一张牌的多次识别做多数投票。平票时返回 None（宁可不认）。"""
    real = [c for c in cards if c is not None]
    if not real:
        return None
    counts = Counter(real)
    top = counts.most_common()
    if len(top) > 1 and top[0][1] == top[1][1]:
        return None
    return top[0][0]


def vote_frames(frames: list[RecognitionResult]) -> RecognitionResult:
    """对同一墩的多帧识别结果做逐张多数投票。

    只处理所有帧都出现的区；某帧缺该区时以 None 参与投票（降低一致率）。
    """
    if not frames:
        return RecognitionResult()
    zones: list[str] = []
    for f in frames:
        for z in f.zones:
            if z not in zones:
                zones.append(z)

    out = RecognitionResult()
    agreements: list[float] = []
    for zone in zones:
        slot_count = max((len(f.zones[zone].cards) for f in frames
                          if zone in f.zones), default=0)
        votes_per_slot: list[list[Card | None]] = []
        for slot in range(slot_count):
            votes: list[Card | None] = []
            for f in frames:
                zr = f.zones.get(zone)
                if zr is None or slot >= len(zr.cards):
                    votes.append(None)
                else:
                    votes.append(zr.cards[slot].card)
            votes_per_slot.append(votes)

        reads: list[CardRead] = []
        slot_agreements: list[float] = []
        for slot, votes in enumerate(votes_per_slot):
            won = majority(votes)
            agree = (sum(1 for v in votes if v == won) / len(votes)) if won else 0.0
            slot_agreements.append(agree)
            # 分数取该牌在各帧中的最高分，作为「最好情况」的乐观估计
            best_r = best_s = best_m = 0.0
            for f in frames:
                zr = f.zones.get(zone)
                if zr is not None and slot < len(zr.cards):
                    cr = zr.cards[slot]
                    best_r = max(best_r, cr.rank_score)
                    best_s = max(best_s, cr.suit_score)
                    best_m = max(best_m, cr.margin)
            reads.append(CardRead(card=won, rank_score=best_r,
                                  suit_score=best_s, margin=best_m))

        zr_out = ZoneRead(zone=zone, cards=reads)
        zr_out.confidence = min((r.margin for r in reads), default=0.0)
        zr_out.rank_agreement = (min(slot_agreements) if slot_agreements else 0.0)
        agreements.append(zr_out.rank_agreement)
        out.zones[zone] = zr_out

    out.confidence = min((z.confidence for z in out.zones.values()), default=0.0)
    out.frame_agreement = min(agreements, default=0.0)
    return out


def is_uncertain(res: RecognitionResult, min_margin: float,
                 min_agreement: float) -> bool:
    """是否需要人工纠正。两个阈值独立，任一不达标即触发（设计文档 §5.1）。"""
    if not res.zones:
        return False
    return res.confidence < min_margin or res.frame_agreement < min_agreement
