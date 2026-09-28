"""字形特征与模板库。

特征提取的关键是**先把墨迹裁到紧致外接框再缩放**：
实测直接用原始角标像素做相关时，字形在 16px 切片内的 ±1px 位置抖动
会把相关性拉低到无法区分（簇内最小 0.756 < 簇间最大 0.828）。
裁到紧致外接框后再缩放到固定尺寸，可消除平移抖动。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .. import constants as C


def ink_mask(patch: np.ndarray) -> np.ndarray:
    """角标墨迹：偏暗 或 高饱和彩色。返回 bool 数组。

    同时接受 BGR 三通道与单通道灰度输入（灰度视为亮度，只判偏暗）。
    """
    if patch.ndim == 2:
        return patch < C.INK_V_MAX
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    return (hsv[:, :, 2] < C.INK_V_MAX) | (hsv[:, :, 1] > C.INK_S_MIN)


def glyph_feature(patch: np.ndarray) -> np.ndarray | None:
    """把字形切片转成归一化特征向量；无墨迹时返回 None。

    步骤：墨迹 -> 紧致外接框 -> 缩放固定尺寸 -> L2 归一化。

    ⚠️ **刻意不做零均值化**：零均值（Pearson 相关）对亮度差异更稳健，
    但它会把**常值图像减成全零向量**。实心/近实心的字形（例如一个实心方块，
    或模糊到只剩一团的小字形）在缩放到固定尺寸后会接近常值，此时范数为 0，
    特征直接丢失。改用非负二值形状做 L2 归一化，既保留形状判别力，
    又不会塌陷。
    """
    ink = ink_mask(patch).astype(np.uint8)
    if int(ink.sum()) < 4:
        return None
    ys, xs = np.nonzero(ink)
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    crop = ink[y0:y1, x0:x1].astype(np.float32)
    feat = cv2.resize(crop, (C.GLYPH_FEAT_W, C.GLYPH_FEAT_H),
                      interpolation=cv2.INTER_AREA).reshape(-1)
    norm = float(np.linalg.norm(feat))
    if norm < 1e-6:
        return None
    return feat / norm


@dataclass(frozen=True)
class GlyphTemplate:
    label: str            # 如 "A" / "S"
    vector: np.ndarray    # 归一化特征

    def to_json(self) -> dict:
        return {"label": self.label, "vector": [round(float(v), 5) for v in self.vector]}

    @classmethod
    def from_json(cls, d: dict) -> "GlyphTemplate":
        return cls(label=str(d["label"]),
                   vector=np.asarray(d["vector"], dtype=np.float32))


class TemplateLibrary:
    """点数与花色两套模板。

    识别 = 与所有模板求相关，取最高分；置信度由「最佳与次佳的差」给出。
    """

    def __init__(self,
                 ranks: list[GlyphTemplate] | None = None,
                 suits: list[GlyphTemplate] | None = None) -> None:
        self.ranks: list[GlyphTemplate] = list(ranks or [])
        self.suits: list[GlyphTemplate] = list(suits or [])

    # ---------- 查询 ----------

    @property
    def ready(self) -> bool:
        return len(self.ranks) >= 2 and len(self.suits) >= 2

    def _match(self, feat: np.ndarray,
               templates: list[GlyphTemplate]) -> tuple[str | None, float, float]:
        """返回 (最佳标签, 最佳分, 次佳分)。"""
        if not templates:
            return None, 0.0, 0.0
        scores = [(float(np.dot(feat, t.vector)), t.label) for t in templates]
        scores.sort(reverse=True)
        best_score, best_label = scores[0]
        second = scores[1][0] if len(scores) > 1 else 0.0
        return best_label, best_score, second

    def classify_rank(self, patch: np.ndarray) -> tuple[str | None, float, float]:
        feat = glyph_feature(patch)
        if feat is None:
            return None, 0.0, 0.0
        return self._match(feat, self.ranks)

    def classify_suit(self, patch: np.ndarray) -> tuple[str | None, float, float]:
        feat = glyph_feature(patch)
        if feat is None:
            return None, 0.0, 0.0
        return self._match(feat, self.suits)

    # ---------- 构建 ----------

    def add_rank(self, label: str, patch: np.ndarray) -> bool:
        feat = glyph_feature(patch)
        if feat is None:
            return False
        self.ranks.append(GlyphTemplate(label, feat))
        return True

    def add_suit(self, label: str, patch: np.ndarray) -> bool:
        feat = glyph_feature(patch)
        if feat is None:
            return False
        self.suits.append(GlyphTemplate(label, feat))
        return True

    def rank_labels(self) -> list[str]:
        return [t.label for t in self.ranks]

    def suit_labels(self) -> list[str]:
        return [t.label for t in self.suits]

    # ---------- 持久化 ----------

    def to_dict(self) -> dict:
        return {"ranks": [t.to_json() for t in self.ranks],
                "suits": [t.to_json() for t in self.suits]}

    @classmethod
    def from_dict(cls, d: dict) -> "TemplateLibrary":
        return cls(ranks=[GlyphTemplate.from_json(x) for x in d.get("ranks", [])],
                   suits=[GlyphTemplate.from_json(x) for x in d.get("suits", [])])

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), ensure_ascii=False),
                     encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "TemplateLibrary | None":
        """读取模板库。文件缺失或损坏返回 None，绝不抛异常。"""
        p = Path(path)
        if not p.exists():
            return None
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return cls.from_dict(d)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError,
                KeyError, TypeError, ValueError):
            return None
