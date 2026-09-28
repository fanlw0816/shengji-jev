"""角标切片提取。

核心事实（实测，见 spike/analyze_cards2.py）：
叠放时每张牌只露出左侧 16px，但点数与花色的角标正好在其中有完整信息。
因此**识别单元是每张牌的角标切片**，而不是整张牌面。

多张牌的外接框宽度 = CARD_W + (n-1) * CARD_STACK_OFFSET
第 i 张牌的左边缘 = 外接框左边缘 + i * CARD_STACK_OFFSET
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .. import constants as C
from ..layout.detect import detect_occupied_zones
from ..layout.model import LayoutModel


@dataclass(frozen=True)
class CardPatch:
    """一张牌的角标切片。"""

    zone: str
    slot: int            # 该区第几张（0 起）
    count: int           # 该区共几张
    x: int
    y: int
    corner: np.ndarray   # CORNER_H x CARD_SLIVER_W x 3
    rank_patch: np.ndarray
    suit_patch: np.ndarray

    @property
    def is_complete(self) -> bool:
        """角标是否完整（未被画面边缘截断）。"""
        h, w = self.corner.shape[:2]
        return h == C.CORNER_H and w == C.CARD_SLIVER_W


def split_patches(corner: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """把角标切成点数片与花色片。实测两者之间有干净空隙（中位 row=14）。"""
    r0, r1 = C.CORNER_RANK_ROWS
    s0, s1 = C.CORNER_SUIT_ROWS
    return corner[r0:r1], corner[s0:min(s1, corner.shape[0])]


def extract_card_patches(frame: np.ndarray,
                         model: LayoutModel) -> dict[str, list[CardPatch]]:
    """提取画面中各出牌区的角标切片。

    返回 {区名: [CardPatch, ...]}，只含有牌的区。
    画面边缘导致切片不完整的牌会被丢弃（宁可少认，不可认错）。
    """
    h, w = frame.shape[:2]
    out: dict[str, list[CardPatch]] = {}
    for zone, info in detect_occupied_zones(frame, model).items():
        blob = info["blob"]
        count = int(info["count"])
        patches: list[CardPatch] = []
        for i in range(count):
            x0 = blob["x"] + i * C.CARD_STACK_OFFSET
            y0 = blob["y"]
            x1 = x0 + C.CARD_SLIVER_W
            y1 = y0 + C.CORNER_H
            if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
                continue
            corner = frame[y0:y1, x0:x1].copy()
            if corner.shape[:2] != (C.CORNER_H, C.CARD_SLIVER_W):
                continue
            rp, sp = split_patches(corner)
            patches.append(CardPatch(zone=zone, slot=i, count=count,
                                     x=x0, y=y0, corner=corner,
                                     rank_patch=rp, suit_patch=sp))
        if patches:
            out[zone] = patches
    return out
