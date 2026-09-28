"""标定配置的读写。配置为可人工查看与手改的 JSON。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..layout.model import LayoutModel

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Calibration:
    layout: LayoutModel
    output_idx: int
    variant: tuple[int, int]   # (players, decks)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "output_idx": self.output_idx,
            "variant": list(self.variant),
            "layout": self.layout.to_dict(),
        }


def save_calibration(path: str | Path, cal: Calibration) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cal.to_dict(), ensure_ascii=False, indent=2),
                 encoding="utf-8")


def load_calibration(path: str | Path) -> Calibration | None:
    """读取配置。文件缺失、损坏或字段非法时返回 None，绝不抛异常。

    返回 None 的语义是「需要重新标定」，由调用方提示用户。
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    try:
        layout = LayoutModel.from_dict(d["layout"])
        variant = tuple(d.get("variant", (4, 2)))
        if len(variant) != 2:
            return None
        return Calibration(layout=layout,
                           output_idx=int(d.get("output_idx", 0)),
                           variant=(int(variant[0]), int(variant[1])))
    except (KeyError, TypeError, ValueError):
        # ValueError 覆盖 AnchorMode 值非法的情况
        return None
