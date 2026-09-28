import numpy as np

from shengji.tools.record import SampleWriter, frame_diff_score, should_save


def test_should_save_on_occupancy_change():
    assert should_save(prev=(), now=("top",)) is True
    assert should_save(prev=("top",), now=("top",)) is False
    assert should_save(prev=("top",), now=("top", "left")) is True
    assert should_save(prev=("top", "left"), now=("left", "top")) is False


def test_frame_diff_score_zero_for_identical():
    a = np.zeros((10, 10, 3), dtype=np.uint8)
    assert frame_diff_score(a, a) == 0.0


def test_frame_diff_score_handles_shape_mismatch():
    a = np.zeros((10, 10, 3), dtype=np.uint8)
    b = np.zeros((20, 20, 3), dtype=np.uint8)
    assert frame_diff_score(a, b) > 1e8


def test_frame_diff_score_positive_for_different():
    a = np.zeros((10, 10, 3), dtype=np.uint8)
    b = np.full((10, 10, 3), 255, dtype=np.uint8)
    assert frame_diff_score(a, b) > 1.0


def test_sample_writer_creates_zone_files(tmp_path):
    w = SampleWriter(tmp_path, deal_id="d1", trick_index=0, seat=2)
    img = np.zeros((79, 60, 3), dtype=np.uint8)
    p = w.write_zone("top", img, count=2, ts=12.5)
    assert p.exists()
    assert "trick000" in str(p)
    assert "seat2" in str(p)
    assert "top" in str(p)


def test_sample_writer_manifest_appends(tmp_path):
    w = SampleWriter(tmp_path, deal_id="d1", trick_index=1, seat=0)
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    w.write_zone("left", img, count=1, ts=1.0)
    w.write_zone("right", img, count=1, ts=1.1)
    lines = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert "left" in lines[0]
    assert "right" in lines[1]
