"""示例标定文件与 CLI 接线。

`calib/store.py` 一度是**有实现没接线**：只有自己的测试在用，运行时从不读它。
这里把两件事一起钉住：仓库里的示例文件不许腐烂，以及 CLI 的取值优先级。
"""

import argparse
from pathlib import Path

from shengji.calib.store import load_calibration
from shengji.layout.model import LayoutModel
from shengji.tools.run_counter import _resolve_layout

EXAMPLE = Path(__file__).resolve().parents[1] / "calib.example.json"


def _args(**kw) -> argparse.Namespace:
    base = {"calib": str(EXAMPLE), "players": None, "decks": None, "output_idx": None}
    base.update(kw)
    return argparse.Namespace(**base)


def test_example_calibration_exists_and_loads():
    cal = load_calibration(EXAMPLE)
    assert cal is not None, "示例标定文件缺失或损坏 —— 它同时是格式说明与标定模板"
    assert cal.variant == (4, 2)
    assert cal.output_idx == 0


def test_example_calibration_matches_reference_layout():
    """示例文件必须与 `LayoutModel.from_reference()` 一致，否则它会悄悄腐烂。"""
    cal = load_calibration(EXAMPLE)
    assert cal is not None
    assert cal.layout.zones == LayoutModel.from_reference().zones


def test_resolve_layout_prefers_calibration_over_defaults():
    model, output_idx, players, decks = _resolve_layout(_args(), None)
    assert (players, decks, output_idx) == (4, 2, 0)
    assert model.zones == LayoutModel.from_reference().zones


def test_resolve_layout_cli_overrides_calibration():
    model, output_idx, players, decks = _resolve_layout(
        _args(players=6, decks=3, output_idx=2), None)
    assert (players, decks, output_idx) == (6, 3, 2)
    assert model.zones == LayoutModel.from_reference().zones


def test_resolve_layout_falls_back_when_calibration_missing(tmp_path, capsys):
    missing = tmp_path / "nope.json"
    model, output_idx, players, decks = _resolve_layout(_args(calib=str(missing)), None)
    assert (players, decks, output_idx) == (4, 2, 0)   # 兜底默认
    assert model.zones == LayoutModel.from_reference().zones
    assert "未找到可用标定" in capsys.readouterr().err


def test_resolve_layout_uses_calibration_output_idx(tmp_path):
    """标定里的 output_idx 应当被采纳，而不是永远用 0。"""
    from shengji.calib.store import Calibration, save_calibration

    p = tmp_path / "calib.json"
    save_calibration(p, Calibration(layout=LayoutModel.from_reference(),
                                    output_idx=2, variant=(4, 2)))
    _, output_idx, _, _ = _resolve_layout(_args(calib=str(p)), None)
    assert output_idx == 2
