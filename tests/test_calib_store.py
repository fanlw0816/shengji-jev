import json

from shengji.calib.store import Calibration, load_calibration, save_calibration
from shengji.layout.model import AnchorMode, LayoutModel


def test_round_trip(tmp_path):
    p = tmp_path / "config.json"
    cal = Calibration(
        layout=LayoutModel.from_reference().with_anchor(AnchorMode.CENTER),
        output_idx=1,
        variant=(4, 2),
    )
    save_calibration(p, cal)
    back = load_calibration(p)
    assert back is not None
    assert back.output_idx == 1
    assert back.variant == (4, 2)
    assert back.layout.anchor_mode is AnchorMode.CENTER
    assert back.layout.center == cal.layout.center


def test_missing_file_returns_none(tmp_path):
    assert load_calibration(tmp_path / "nope.json") is None


def test_corrupt_file_returns_none_and_does_not_raise(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load_calibration(p) is None


def test_saved_json_is_human_readable(tmp_path):
    """配置文件要能人工查看与手改。"""
    p = tmp_path / "config.json"
    save_calibration(p, Calibration(layout=LayoutModel.from_reference(),
                                    output_idx=0, variant=(4, 2)))
    d = json.loads(p.read_text(encoding="utf-8"))
    assert d["variant"] == [4, 2]
    assert "layout" in d
    assert d["layout"]["anchor_mode"] == "top_left"
    assert d["schema_version"] == 1


def test_load_returns_none_on_unknown_anchor_mode(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({
        "layout": {**LayoutModel.from_reference().to_dict(),
                   "anchor_mode": "bogus"},
        "output_idx": 0, "variant": [4, 2],
    }), encoding="utf-8")
    assert load_calibration(p) is None


def test_load_returns_none_on_missing_layout(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"output_idx": 0, "variant": [4, 2]}), encoding="utf-8")
    assert load_calibration(p) is None
