"""模板库与字形特征测试。

真实模板库需要人工给角标字形起名，因此这里用**合成字形**验证机械部分：
绘制已知的点数字形与花色图形，检查特征提取与最近邻分类能否正确还原。
"""

import cv2
import numpy as np

from shengji import constants as C
from shengji.recognition.templates import (
    TemplateLibrary,
    glyph_feature,
    ink_mask,
)

RANK_LABELS = ["2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K", "A"]
SUIT_LABELS = ["S", "H", "D", "C"]


# ---------- 合成字形 ----------

def _blank(w: int, h: int) -> np.ndarray:
    """白色底（与真实扑克牌一致：白底深字）。"""
    return np.full((h, w), 255, np.uint8)


def _rank_glyph(label: str, w: int = C.CARD_SLIVER_W,
                h: int = 15, dx: int = 0, dy: int = 0) -> np.ndarray:
    """白底深字的点数字形，返回 BGR。dx/dy 用于制造位置抖动。

    注意极性：真实牌面是白底深字，若做成黑底白字，
    ink_mask 会把背景当成墨迹，特征就反了。
    """
    img = _blank(w, h)
    font = cv2.FONT_HERSHEY_SIMPLEX
    scale = 0.32
    (tw, th), _ = cv2.getTextSize(label, font, scale, 1)
    x = max(0, min(w - tw - 1, (w - tw) // 2 + dx))
    y = max(th, min(h - 1, (h + th) // 2 + dy))
    cv2.putText(img, label, (x, y), font, scale, 0, 1, cv2.LINE_AA)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _suit_glyph(kind: str, w: int = C.CARD_SLIVER_W, h: int = 12,
                dx: int = 0, dy: int = 0) -> np.ndarray:
    """白底深字的花色图形（四种形状互不相同），返回 BGR。"""
    img = _blank(w, h)
    cx, cy = w // 2 + dx, h // 2 + dy
    if kind == "S":
        pts = np.array([[cx, cy - 4], [cx - 4, cy + 3], [cx + 4, cy + 3]], np.int32)
        cv2.fillPoly(img, [pts], 0)
    elif kind == "H":
        cv2.circle(img, (cx, cy), 4, 0, -1)
    elif kind == "D":
        pts = np.array([[cx, cy - 4], [cx + 4, cy], [cx, cy + 4], [cx - 4, cy]], np.int32)
        cv2.fillPoly(img, [pts], 0)
    elif kind == "C":
        cv2.rectangle(img, (cx - 4, cy - 4), (cx + 4, cy + 4), 0, -1)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _corner_from_glyphs(rank_g: np.ndarray, suit_g: np.ndarray) -> np.ndarray:
    """把点数与花色字形拼成 BGR 角标切片（白底深字）。"""
    corner = np.full((C.CORNER_H, C.CARD_SLIVER_W, 3), 255, np.uint8)
    r0, r1 = C.CORNER_RANK_ROWS
    s0, s1 = C.CORNER_SUIT_ROWS
    h1 = min(rank_g.shape[0], r1 - r0)
    corner[r0:r0 + h1] = rank_g[:h1]
    h2 = min(suit_g.shape[0], s1 - s0)
    corner[s0:s0 + h2] = suit_g[:h2]
    return corner


def _built_library() -> TemplateLibrary:
    lib = TemplateLibrary()
    for lbl in RANK_LABELS:
        assert lib.add_rank(lbl, _rank_glyph(lbl))
    for lbl in SUIT_LABELS:
        assert lib.add_suit(lbl, _suit_glyph(lbl))
    return lib


# ---------- 特征提取 ----------

def test_ink_mask_finds_dark_glyph_on_white():
    patch = np.full((10, 10, 3), 255, np.uint8)
    patch[3:6, 3:6] = 0
    m = ink_mask(patch)
    assert m.sum() == 9
    assert not m[0, 0]


def test_ink_mask_finds_saturated_colour():
    patch = np.full((10, 10, 3), 255, np.uint8)
    patch[2:4, 2:4] = (0, 0, 255)   # 纯红：亮但高饱和
    assert ink_mask(patch)[2, 2]


def test_glyph_feature_returns_none_for_blank():
    assert glyph_feature(np.full((12, 16, 3), 255, np.uint8)) is None


def test_glyph_feature_is_normalized():
    f = glyph_feature(_rank_glyph("A"))
    assert f is not None
    assert abs(float(np.linalg.norm(f)) - 1.0) < 1e-5
    assert float(f.min()) >= 0.0, "特征应为非负（二值形状），不做零均值化"


def test_glyph_feature_works_for_solid_shape():
    """实心字形也必须能提出特征。

    回归测试：早期实现做了零均值归一化，实心（或模糊到近实心）的字形
    在缩放后会变成常值图，减掉均值即全零向量，范数为 0，特征直接丢失。
    """
    solid = np.full((12, 16, 3), 255, np.uint8)
    solid[3:9, 5:11] = 0
    f = glyph_feature(solid)
    assert f is not None, "实心形状不该丢特征"
    assert abs(float(np.linalg.norm(f)) - 1.0) < 1e-5


def test_glyph_feature_removes_translation():
    """核心性质：裁到紧致外接框后，位置抖动的同一字形特征几乎相同。

    这正是 spike 里发现「不裁外接框则无法区分」的原因。
    """
    a = glyph_feature(_rank_glyph("8", dx=0, dy=0))
    b = glyph_feature(_rank_glyph("8", dx=1, dy=1))
    assert a is not None and b is not None
    assert float(np.dot(a, b)) > 0.90


# ---------- 分类 ----------

def test_library_ready_flag():
    lib = TemplateLibrary()
    assert not lib.ready
    lib.add_rank("A", _rank_glyph("A"))
    assert not lib.ready          # 花色还没有
    lib.add_suit("S", _suit_glyph("S"))
    lib.add_suit("H", _suit_glyph("H"))
    lib.add_rank("K", _rank_glyph("K"))
    assert lib.ready


def test_classify_recovers_all_synthetic_ranks():
    lib = _built_library()
    for lbl in RANK_LABELS:
        got, score, second = lib.classify_rank(_rank_glyph(lbl))
        assert got == lbl, f"{lbl} 被识别为 {got}"
        assert score > second


def test_classify_recovers_all_synthetic_suits():
    lib = _built_library()
    for lbl in SUIT_LABELS:
        got, score, second = lib.classify_suit(_suit_glyph(lbl))
        assert got == lbl, f"{lbl} 被识别为 {got}"
        assert score > second


def test_classify_survives_translation_jitter():
    """位置抖动 1px 后仍应识别正确 —— 这是 real-world 必然发生的情况。"""
    lib = _built_library()
    for lbl in RANK_LABELS:
        got, _s, _2 = lib.classify_rank(_rank_glyph(lbl, dx=1, dy=-1))
        assert got == lbl, f"{lbl} 抖动后被识别为 {got}"


def test_margin_is_positive_when_confident():
    lib = _built_library()
    _got, score, second = lib.classify_rank(_rank_glyph("5"))
    assert score - second > 0.05


def test_classify_on_blank_returns_none():
    lib = _built_library()
    got, score, second = lib.classify_rank(np.full((15, 16, 3), 255, np.uint8))
    assert got is None
    assert score == 0.0 and second == 0.0


def test_classify_with_empty_library_returns_none():
    lib = TemplateLibrary()
    got, _s, _2 = lib.classify_rank(_rank_glyph("A"))
    assert got is None


# ---------- 持久化 ----------

def test_library_round_trip(tmp_path):
    lib = _built_library()
    p = tmp_path / "t.json"
    lib.save(p)
    back = TemplateLibrary.load(p)
    assert back is not None
    assert back.rank_labels() == lib.rank_labels()
    assert back.suit_labels() == lib.suit_labels()
    got, _s, _2 = back.classify_rank(_rank_glyph("Q"))
    assert got == "Q"


def test_load_missing_returns_none(tmp_path):
    assert TemplateLibrary.load(tmp_path / "nope.json") is None


def test_load_corrupt_returns_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    assert TemplateLibrary.load(p) is None


def test_load_malformed_returns_none(tmp_path):
    p = tmp_path / "bad2.json"
    p.write_text('{"ranks": [{"label": "A"}]}', encoding="utf-8")
    assert TemplateLibrary.load(p) is None
