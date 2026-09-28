import os
from pathlib import Path

import pytest

# Qt 测试必须在 offscreen 平台下跑（无人值守环境没有真实显示）。
# 必须在任何 PySide6 导入之前设置，因此放在 conftest 顶层。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FIXTURES = Path(__file__).parent / "fixtures" / "screenshots"


@pytest.fixture(scope="session")
def shots():
    """返回 {名称: BGR ndarray}。缺失夹具时明确失败，而非静默跳过。"""
    from shengji.imaging import imread_unicode

    names = ["empty", "others_one", "next_two", "all_two"]
    out = {}
    for n in names:
        p = FIXTURES / f"{n}.jpg"
        assert p.exists(), f"缺少测试夹具: {p}"
        img = imread_unicode(p)
        assert img is not None, f"无法解码夹具: {p}"
        out[n] = img
    return out
