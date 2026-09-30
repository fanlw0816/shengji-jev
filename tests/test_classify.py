
from shengji.cards import Card
from shengji.layout.model import LayoutModel
from shengji.recognition.classify import (
    is_uncertain,
    majority,
    read_frame,
    read_patch,
    vote_frames,
)
from shengji.recognition.types import CardRead, RecognitionResult, ZoneRead
from tests.test_templates import (
    _built_library,
    _corner_from_glyphs,
    _rank_glyph,
    _suit_glyph,
)


def _make_patch(zone: str, slot: int, rank: str, suit: str):
    """构造一个 CardPatch，角标由合成字形拼成。"""
    from shengji.recognition.patch import CardPatch, split_patches

    corner = _corner_from_glyphs(_rank_glyph(rank), _suit_glyph(suit))
    rp, sp = split_patches(corner)
    return CardPatch(zone=zone, slot=slot, count=1, x=0, y=0,
                     corner=corner, rank_patch=rp, suit_patch=sp)


# ---------- 单张识别 ----------

def test_read_patch_identifies_card():
    lib = _built_library()
    read = read_patch(_make_patch("top", 0, "A", "S"), lib)
    assert read.card == Card(rank=14, suit=0)
    assert read.margin > 0


def test_read_patch_maps_suit_letters_to_codes():
    lib = _built_library()
    for letter, suit in (("S", 0), ("H", 1), ("D", 2), ("C", 3)):
        read = read_patch(_make_patch("top", 0, "K", letter), lib)
        assert read.card is not None and read.card.suit == suit


# ---------- 多帧投票 ----------

def _zone_result(zone: str, cards: list[Card | None],
                 margin: float = 0.2) -> RecognitionResult:
    res = RecognitionResult()
    reads = [CardRead(card=c, rank_score=0.9, suit_score=0.9, margin=margin)
             for c in cards]
    zr = ZoneRead(zone=zone, cards=reads)
    zr.confidence = margin
    zr.rank_agreement = 1.0
    res.zones[zone] = zr
    res.confidence = margin
    return res


def test_majority_picks_most_common():
    a, b = Card(rank=5, suit=0), Card(rank=6, suit=0)
    assert majority([a, a, b]) == a


def test_majority_returns_none_on_tie():
    """平票时宁可不认，也不猜。"""
    a, b = Card(rank=5, suit=0), Card(rank=6, suit=0)
    assert majority([a, b]) is None


def test_majority_ignores_none():
    a = Card(rank=5, suit=0)
    assert majority([None, None, a]) == a


def test_majority_all_none():
    assert majority([None, None]) is None


def test_vote_frames_picks_consistent_card():
    a, b = Card(rank=5, suit=0), Card(rank=6, suit=0)
    frames = [_zone_result("top", [a]), _zone_result("top", [a]),
              _zone_result("top", [b])]
    out = vote_frames(frames)
    assert out.zones["top"].cards[0].card == a
    assert out.zones["top"].rank_agreement == 2 / 3


def test_vote_frames_tie_yields_none_and_zero_agreement():
    a, b = Card(rank=5, suit=0), Card(rank=6, suit=0)
    out = vote_frames([_zone_result("top", [a]), _zone_result("top", [b])])
    assert out.zones["top"].cards[0].card is None
    assert out.zones["top"].rank_agreement == 0.0


def test_vote_frames_unions_zones_across_frames():
    a = Card(rank=5, suit=0)
    frames = [_zone_result("top", [a]), _zone_result("left", [a])]
    out = vote_frames(frames)
    assert set(out.zones) == {"top", "left"}


def test_vote_frames_missing_zone_counts_as_disagreement():
    a = Card(rank=5, suit=0)
    out = vote_frames([_zone_result("top", [a]), _zone_result("left", [a])])
    # top 只在第一帧出现 -> 一致率 1/2
    assert out.zones["top"].rank_agreement == 0.5


def test_vote_frames_handles_two_cards_per_zone():
    a, b = Card(rank=5, suit=0), Card(rank=9, suit=1)
    frames = [_zone_result("top", [a, b]), _zone_result("top", [a, b])]
    out = vote_frames(frames)
    assert [c.card for c in out.zones["top"].cards] == [a, b]


def test_vote_frames_empty_input():
    out = vote_frames([])
    assert out.zones == {}


# ---------- 不确定性判定（设计文档 §5.1：两个阈值独立，任一不达标即触发）----------

def test_uncertain_when_margin_low():
    res = _zone_result("top", [Card(rank=5, suit=0)], margin=0.01)
    assert is_uncertain(res, min_margin=0.05, min_agreement=0.5)


def test_uncertain_when_agreement_low():
    a, b = Card(rank=5, suit=0), Card(rank=6, suit=0)
    out = vote_frames([_zone_result("top", [a]), _zone_result("top", [b])])
    assert is_uncertain(out, min_margin=0.0, min_agreement=0.5)


def test_not_uncertain_when_both_pass():
    a = Card(rank=5, suit=0)
    out = vote_frames([_zone_result("top", [a]), _zone_result("top", [a])])
    assert not is_uncertain(out, min_margin=0.05, min_agreement=0.5)


def test_no_zones_is_not_uncertain():
    """空桌没有识别目标，不该报不确定。"""
    assert not is_uncertain(RecognitionResult(), min_margin=0.05, min_agreement=0.5)


# ---------- 整帧识别（真实截图，仅验证结构不验证牌名）----------

def test_read_frame_on_real_fixture_produces_expected_slots(shots):
    """用合成模板库跑真实截图：牌名不可信（模板与真实字形不同），
    但**结构**必须正确 —— 区数与每区张数要跟布局检测一致。"""
    lib = _built_library()
    m = LayoutModel.from_reference()

    res = read_frame(shots["all_two"], m, lib)
    assert set(res.zones) == {"top", "left", "right", "bottom"}
    for zr in res.zones.values():
        assert len(zr.cards) == 2

    res1 = read_frame(shots["others_one"], m, lib)
    assert set(res1.zones) == {"top", "left", "right"}
    for zr in res1.zones.values():
        assert len(zr.cards) == 1

    assert read_frame(shots["empty"], m, lib).zones == {}
