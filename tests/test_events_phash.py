import numpy as np
import pytest

from shengji.events.phash import dhash, hamming, same_content


def _thumb(seed: int = 0, w: int = 96, h: int = 48) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.integers(0, 256, (h, w), dtype=np.uint8)


def test_dhash_is_deterministic():
    g = _thumb(1)
    assert dhash(g) == dhash(g)


def test_dhash_same_content_gives_same_hash():
    g = _thumb(2)
    assert dhash(g.copy()) == dhash(g)


def test_dhash_different_content_gives_different_hash():
    assert dhash(_thumb(3)) != dhash(_thumb(4))


def test_dhash_tolerates_small_noise():
    """JPEG 压缩/抗锯齿带来的极小差异不应改变哈希。"""
    g = _thumb(5).astype(np.int16)
    noisy = np.clip(g + 1, 0, 255).astype(np.uint8)
    assert hamming(dhash(g.astype(np.uint8)), dhash(noisy)) <= 4


def test_dhash_rejects_colour_input():
    with pytest.raises(ValueError):
        dhash(np.zeros((8, 8, 3), dtype=np.uint8))


def test_dhash_rejects_empty():
    with pytest.raises(ValueError):
        dhash(np.zeros((0, 0), dtype=np.uint8))


def test_dhash_handles_constant_image():
    """常值图不报错（横向比较全为 False，哈希为 0）。"""
    assert dhash(np.full((10, 10), 128, dtype=np.uint8)) == 0


def test_hamming_counts_bit_differences():
    assert hamming(0b1010, 0b1010) == 0
    assert hamming(0b1010, 0b1000) == 1
    assert hamming(0, 0b1111) == 4


def test_same_content_default_is_strict():
    """默认严格相等：宁可当成新的一手去识别，也不要漏掉一手。"""
    assert same_content(123, 123)
    assert not same_content(123, 122)


def test_same_content_with_tolerance():
    assert same_content(0b1111, 0b1110, tolerance=1)
    assert not same_content(0b1111, 0b1100, tolerance=1)
