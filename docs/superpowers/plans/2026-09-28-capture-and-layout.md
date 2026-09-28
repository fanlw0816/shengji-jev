# 采集、锚定与标定基础 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立一个能自动定位升级牌桌四个出牌区、并可靠采集屏幕帧的 Python 基础层，为后续识别与牌局引擎提供稳定的输入。

**Architecture:** 分四层解耦：`imaging`（颜色分割与牌张度量，纯函数）、`layout`（三假设预测 + 不变量校验 + 锚定选择，纯计算）、`capture`（dxcam 主路径 + mss 降级，Protocol 可替换）、`tools`（CLI 工具，含采样录制与布局可视化）。核心算法全部可用真实截图离线验证，不需要游戏在运行。

**Tech Stack:** Python 3.13, uv, numpy, opencv-python 5.x, dxcam, mss, pywin32, pytest

---

## 设计依据（来自 spec 的实测数据）

本计划中的常量**全部来自 4 张真实截图的实测**（`docs/superpowers/specs/2026-09-28-shengji-cardcounter-design.md` §11.2 / §11A）：

| 常量 | 实测值 | 来源 |
|---|---|---|
| 参考分辨率 | 1280 × 720 | 4 张截图尺寸一致 |
| 四区几何中心 | (556.4, 325.5) | 四区 blob 中心均值 |
| 四区相对偏移 | 上(dx+1.1, dy−79.0) 左(dx−108.9, dy−3.0) 右(dx+106.6, dy−3.0) 下(dx+1.1, dy+85.0) | 同上 |
| 出牌区尺寸 | 73 × 79 px | 连通块外接框 |
| 单张牌尺寸 | 56 × 79 px，宽高比 0.709 | 与标准扑克 0.714 差 0.5% |
| 两张牌叠放宽度 | 72 px → 叠放偏移 16 px | 单张 56px 对照 |
| 桌面色 | BGR ≈ (110, 69, 21)，颜色距离阈值 45 | 空区采样，四图锚点 bbox 逐像素一致 |
| 牌面判定 | V > 165 且 S < 110 | 空区 0% / 有牌 65~86% 对比 |
| 牌块面积 | 真实牌块 3736~4954；右侧静态 UI 元素 1836 | 面积是区分牌块与同形 UI 元素的唯一特征 |
| 牌尺寸块筛选 | 宽 50~200、高 70~88、面积 ≥ 3000 | 实测标定，见 `spike/analyze_card_blobs.py` |

**⚠️ 一条作废的结论（重要）**：初稿曾把「桌面色」当作**定位牌桌**的锚点，
理由是四张截图的锚点 bbox 逐像素相同。**实测证明这是误判**：
那个 bbox 是整个蓝色背景区，而非牌桌。阈值扫描（`spike/analyze_felt_threshold.py`）显示
空区覆盖 100% 需要阈值 90，而此时全图覆盖已达 74%、最大连通块 bbox 贴满画面
→ **牌桌与背景同色，颜色无法定位牌桌**。

因此本计划中：`felt_mask` 仅保留作辅助用途，**定位与校验一律改用「牌尺寸块」**。

**关键约束**：用户已确认**窗口尺寸不影响牌的长宽** → 布局为固定像素，偏移量是常量，**不做等比缩放**。牌宽的角色是**校验常量**而非缩放单位。

---

## File Structure

```
pyproject.toml                       # 修改：加入 pytest 配置与 src 布局
src/shengji/__init__.py              # 创建：包入口
src/shengji/constants.py             # 创建：上表全部实测常量（单一来源）
src/shengji/imaging.py               # 创建：中文路径安全 I/O、桌面色/牌面分割、牌张度量
src/shengji/geometry.py              # 创建：Rect/Region、归一化换算、四区推导
src/shengji/layout/__init__.py       # 创建
src/shengji/layout/model.py          # 创建：LayoutModel 数据类
src/shengji/layout/validate.py       # 创建：不变量校验
src/shengji/layout/detect.py         # 创建：三假设预测 + 锚定选择 + 占用区检测
src/shengji/capture/__init__.py      # 创建
src/shengji/capture/base.py          # 创建：CaptureBackend Protocol、Frame
src/shengji/capture/dxcam_backend.py # 创建：Desktop Duplication 主路径
src/shengji/capture/mss_backend.py   # 创建：BitBlt 降级路径
src/shengji/capture/factory.py       # 创建：自动选择 + 降级
src/shengji/window/__init__.py       # 创建
src/shengji/window/win32.py          # 创建：窗口定位、客户区矩形、DPI 感知
src/shengji/calib/__init__.py        # 创建
src/shengji/calib/store.py           # 创建：JSON 配置读写
src/shengji/tools/__init__.py        # 创建
src/shengji/tools/dump_layout.py     # 创建：布局可视化导出（CLI）
src/shengji/tools/record.py          # 创建：采样录制（CLI）
tests/conftest.py                    # 创建：fixtures 加载器
tests/fixtures/screenshots/*.jpg     # 创建：4 张真实截图（ASCII 重命名）
tests/test_imaging.py                # 创建
tests/test_geometry.py               # 创建
tests/test_layout_validate.py        # 创建
tests/test_layout_detect.py          # 创建：端到端四区定位
tests/test_capture_factory.py        # 创建
tests/test_calib_store.py            # 创建
```

**为什么这样分**：`imaging` 与 `layout` 是纯函数/纯计算，不含任何 IO 或线程，因此可以用截图做确定性单元测试。`capture` 与 `window` 依赖操作系统，隔离在薄封装里，用 Protocol 让上层不感知具体后端。`tools` 只做编排。

---

### Task 1: 项目骨架与 pytest 基建

**Files:**
- Modify: `pyproject.toml`
- Create: `src/shengji/__init__.py`
- Create: `tests/__init__.py`（空文件，便于导入）

- [ ] **Step 1: 配置 pytest 与 src 布局**

修改 `pyproject.toml`，在文件末尾追加：

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-q"
```

同时确认 `[project]` 段有 `requires-python = ">=3.13"`（uv init 已生成）。

- [ ] **Step 2: 创建包入口**

创建 `src/shengji/__init__.py`：

```python
"""升级（拖拉机）记牌器。"""

__version__ = "0.1.0"
```

创建空的 `tests/__init__.py`。

- [ ] **Step 3: 写第一个测试验证包可导入**

创建 `tests/test_smoke.py`：

```python
import shengji


def test_package_imports():
    assert shengji.__version__ == "0.1.0"
```

- [ ] **Step 4: 运行测试**

Run: `uv run pytest tests/test_smoke.py -v`
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add pyproject.toml src/shengji/__init__.py tests/__init__.py tests/test_smoke.py
git commit -m "chore: 建立 src 布局与 pytest 基建"
```

---

### Task 2: 测试夹具与中文路径安全的图像 I/O

**Files:**
- Create: `tests/fixtures/screenshots/{empty,others_one,next_two,all_two}.jpg`
- Create: `src/shengji/imaging.py`
- Create: `tests/conftest.py`
- Test: `tests/test_imaging.py`

- [ ] **Step 1: 把 4 张截图纳入夹具（改用 ASCII 文件名）**

在 PowerShell 中执行：

```powershell
New-Item -ItemType Directory -Force -Path tests\fixtures\screenshots | Out-Null
Copy-Item "png\手牌+没有人出牌.jpg"    tests\fixtures\screenshots\empty.jpg      -Force
Copy-Item "png\手牌+其他三家出一张.jpg" tests\fixtures\screenshots\others_one.jpg -Force
Copy-Item "png\手牌+下家出两张.jpg"    tests\fixtures\screenshots\next_two.jpg   -Force
Copy-Item "png\手牌+所有人出两张.jpg"   tests\fixtures\screenshots\all_two.jpg    -Force
```

夹具命名含义：`empty`=没人出牌，`others_one`=其他三家各出一张，`next_two`=下家出两张，`all_two`=所有人各出两张。

- [ ] **Step 2: 写 fixtures 加载器**

创建 `tests/conftest.py`：

```python
from pathlib import Path

import pytest

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
```

- [ ] **Step 3: 写失败的测试**

创建 `tests/test_imaging.py`：

```python
import numpy as np

from shengji.imaging import imread_unicode, imwrite_unicode


def test_imread_unicode_handles_chinese_path(tmp_path):
    """Windows 上 cv2.imread 无法打开中文路径，必须用 imdecode 路径。"""
    img = np.zeros((8, 8, 3), dtype=np.uint8)
    img[:, :, 2] = 255  # 纯红
    p = tmp_path / "中文文件名.png"
    assert imwrite_unicode(p, img) is True

    back = imread_unicode(p)
    assert back is not None
    assert back.shape == (8, 8, 3)
    assert int(back[0, 0, 2]) == 255


def test_imread_unicode_returns_none_for_missing(tmp_path):
    assert imread_unicode(tmp_path / "不存在.png") is None


def test_fixtures_all_1280x720(shots):
    for name, img in shots.items():
        assert img.shape == (720, 1280, 3), f"{name} 尺寸异常: {img.shape}"
```

- [ ] **Step 4: 运行测试确认失败**

Run: `uv run pytest tests/test_imaging.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.imaging'`

- [ ] **Step 5: 实现 imaging.py 的 I/O 部分**

创建 `src/shengji/imaging.py`：

```python
"""图像 I/O 与颜色分割。

所有函数为纯函数，不持有状态，便于用真实截图做确定性测试。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread_unicode(path: str | Path, flags: int = cv2.IMREAD_COLOR) -> np.ndarray | None:
    """读取图像，支持 Windows 中文路径。

    cv2.imread 在 Windows 上无法打开非 ASCII 路径（静默返回 None），
    因此统一走 np.fromfile + cv2.imdecode。
    """
    p = Path(path)
    if not p.exists():
        return None
    try:
        buf = np.fromfile(str(p), dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, flags)


def imwrite_unicode(path: str | Path, img: np.ndarray) -> bool:
    """写出图像，支持 Windows 中文路径。返回是否成功。"""
    p = Path(path)
    suffix = p.suffix or ".png"
    ok, buf = cv2.imencode(suffix, img)
    if not ok:
        return False
    try:
        buf.tofile(str(p))
    except OSError:
        return False
    return True
```

- [ ] **Step 6: 运行测试确认通过**

Run: `uv run pytest tests/test_imaging.py -v`
Expected: PASS（4 项）

- [ ] **Step 7: 提交**

```bash
git add tests/fixtures tests/conftest.py tests/test_imaging.py src/shengji/imaging.py
git commit -m "feat: 中文路径安全的图像 I/O 与真实截图测试夹具"
```

---

### Task 3: 实测常量单一来源

**Files:**
- Create: `src/shengji/constants.py`
- Test: `tests/test_constants.py`

- [ ] **Step 1: 写失败的测试（锁定实测值，防止后续被随手改）**

创建 `tests/test_constants.py`：

```python
from shengji import constants as C


def test_reference_resolution():
    assert C.REF_W == 1280
    assert C.REF_H == 720


def test_zone_centers_form_symmetric_cross():
    """四区必须构成对称十字 —— 这是 spec §11A 的核心不变量。"""
    z = C.REF_ZONE_CENTERS
    xs = [z[k][0] for k in ("top", "bottom")]
    ys = [z[k][1] for k in ("left", "right")]
    assert abs(xs[0] - xs[1]) < 3, "上下区 x 不对齐"
    assert abs(ys[0] - ys[1]) < 3, "左右区 y 不对齐"

    cx = sum(z[k][0] for k in z) / 4
    cy = sum(z[k][1] for k in z) / 4
    assert abs((z["left"][0] - cx) + (z["right"][0] - cx)) < 3, "左右臂不对称"
    assert abs((z["top"][1] - cy) + (z["bottom"][1] - cy)) < 8, "上下臂不对称"


def test_card_geometry_matches_real_playing_card():
    """实测牌宽高比应接近标准扑克牌 2.5:3.5。"""
    ratio = C.CARD_W / C.CARD_H
    assert abs(ratio - 2.5 / 3.5) < 0.02
    assert C.CARD_W == 56
    assert C.CARD_H == 79
    assert C.CARD_STACK_OFFSET == 16


def test_felt_color_and_threshold():
    assert C.FELT_BGR == (110, 69, 21)
    assert C.FELT_DIST_MAX == 45


def test_card_mask_thresholds():
    assert C.CARD_V_MIN == 165
    assert C.CARD_S_MAX == 110


def test_card_blob_filter_is_sane():
    """牌尺寸块的筛选窗口必须能把真实牌装进去。"""
    assert C.CARD_BLOB_MIN_W <= C.CARD_W <= C.CARD_BLOB_MAX_W
    assert C.CARD_BLOB_MIN_H <= C.CARD_H <= C.CARD_BLOB_MAX_H


def test_card_blob_area_separates_real_cards_from_ui():
    """实测：1 张牌块约 3736px，右侧静态 UI 元素约 1836px，宽高比都是 0.92。

    只有面积能区分二者，因此阈值必须严格落在两者之间。
    """
    assert 1836 < C.CARD_BLOB_MIN_AREA < 3736
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_constants.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.constants'`

- [ ] **Step 3: 实现 constants.py**

创建 `src/shengji/constants.py`：

```python
"""实测常量单一来源。

全部数值来自 4 张真实截图（1280x720）的实测，
见 docs/superpowers/specs/2026-09-28-shengji-cardcounter-design.md §11.2 / §11A。

修改这些值前请先重跑 spike/analyze_*.py 复核，并同步更新 spec。
"""

from __future__ import annotations

# --- 参考分辨率（截图尺寸，布局常量均基于此坐标系）---
REF_W = 1280
REF_H = 720

# --- 四个出牌区的中心（1280x720 坐标）---
# 实测来源：all_two.jpg 中四个 73x79 连通块的中心
REF_ZONE_CENTERS: dict[str, tuple[float, float]] = {
    "top": (557.5, 246.5),     # 对家
    "left": (447.5, 322.5),    # 上家
    "right": (663.0, 322.5),   # 下家（实测推得）
    "bottom": (557.5, 410.5),  # 自己（实测推得）
}

# --- 出牌区尺寸 ---
# 实测为 73x79。宽度需容纳甩牌（一次出多张），故检索时放宽；
# 上限待 Phase 0 实测甩牌张数后收紧。
ZONE_W = 73
ZONE_H = 79
ZONE_SEARCH_MARGIN_X = 120   # 检索区在 bbox 左右各放宽，容纳甩牌
ZONE_SEARCH_MARGIN_Y = 40

# --- 牌张几何 ---
CARD_W = 56          # 单张牌外接框宽
CARD_H = 79          # 单张牌外接框高（与 ZONE_H 相同 —— 牌高即区高）
CARD_ASPECT = CARD_W / CARD_H   # 0.709，标准扑克为 0.714
CARD_STACK_OFFSET = 16          # 两张牌叠放时的横向偏移（实测 72 - 56）

# --- 背景/桌面色 ---
# ⚠️ 实测结论：牌桌周围的背景与桌面是同一种颜色，本掩码**不能用于定位牌桌**
#    （阈值 45 时空区覆盖 96.6%、全图覆盖 65.5%，最大连通块 bbox 贴满画面；
#      阈值升到 90 才能 100% 覆盖空区，但全图覆盖已达 74%）
#    仅用于辅助判断「某区域是否为空」，定位与校验一律改用牌面块。
FELT_BGR = (110, 69, 21)
FELT_DIST_MAX = 45

# --- 牌面分割（高亮 + 低饱和）---
CARD_V_MIN = 165
CARD_S_MAX = 110

# --- 「牌尺寸块」筛选（校验的强特征）---
# 牌是 56x79、宽高比 0.709 的白色块。放宽一档以容纳 2~3 张叠放与边缘抗锯齿。
CARD_BLOB_MIN_W = 50
CARD_BLOB_MAX_W = 200      # 3 张叠放 88；放宽到 200 以容纳更长甩牌
CARD_BLOB_MIN_H = 70
CARD_BLOB_MAX_H = 88
# 面积下限经实测标定（spike/analyze_card_blobs.py）：
#   真实牌块面积 3736~4954（1 张 ~3736，2 张 ~4900）
#   右侧静态 UI 元素 (1042,282) 68x74 面积仅 1836，宽高比同为 0.92 无法区分
# 只有面积能把两者分开。取 3000，两侧各留约 24% / 39% 余量。
CARD_BLOB_MIN_AREA = 3000

# --- 校验容差 ---
TOL_ARM_SYMMETRY_X = 3.0     # 左右臂对称容差（spec §11A）
TOL_ARM_SYMMETRY_Y = 8.0     # 上下臂对称容差
TOL_CARD_W = 6               # 牌宽校验容差（±px）
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_constants.py -v`
Expected: PASS（5 项）

- [ ] **Step 5: 提交**

```bash
git add src/shengji/constants.py tests/test_constants.py
git commit -m "feat: 把实测布局常量收敛为单一来源并加测试锁定"
```

---

### Task 4: 桌面色与牌面分割

**Files:**
- Modify: `src/shengji/imaging.py`
- Test: `tests/test_imaging.py`

- [ ] **Step 1: 写失败的测试**

在 `tests/test_imaging.py` 末尾追加：

```python
from shengji.constants import REF_ZONE_CENTERS, ZONE_H, ZONE_W
from shengji.imaging import card_mask, felt_mask, zone_rect


def _crop(img, name):
    x0, y0, x1, y1 = zone_rect(name)
    return img[y0:y1, x0:x1]


def test_felt_mask_says_all_felt_when_nobody_played(shots):
    """没有人出牌时，四个出牌区必须全部判定为桌面。"""
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["empty"], name)
        frac = felt_mask(seg).mean()
        assert frac > 0.90, f"{name} 空区应几乎全是桌面，实测 {frac:.2%}"


def test_card_mask_finds_cards_when_everyone_played_two(shots):
    """所有人出两张时，四个区都必须检出牌面。"""
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["all_two"], name)
        frac = card_mask(seg).mean()
        assert frac > 0.70, f"{name} 应检出大量牌面，实测 {frac:.2%}"


def test_card_mask_zero_when_nobody_played(shots):
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["empty"], name)
        assert card_mask(seg).mean() < 0.02, f"{name} 空区不应有牌面"


def test_felt_and_card_masks_are_complementary(shots):
    """同一块区域不应同时被判为桌面和牌面。"""
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["all_two"], name)
        overlap = (felt_mask(seg) & card_mask(seg)).mean()
        assert overlap < 0.05, f"{name} 桌面与牌面判定重叠 {overlap:.2%}"
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_imaging.py -v`
Expected: FAIL — `ImportError: cannot import name 'card_mask'`

- [ ] **Step 3: 实现分割函数**

在 `src/shengji/imaging.py` 末尾追加：

```python
from . import constants as C


def felt_mask(img: np.ndarray, dist_max: int = C.FELT_DIST_MAX) -> np.ndarray:
    """桌面色掩码：到实测桌面色均值的欧氏距离小于阈值。

    返回 bool 数组，形状为 img.shape[:2]。
    """
    ref = np.array(C.FELT_BGR, dtype=np.float32)
    d = np.linalg.norm(img.astype(np.float32) - ref[None, None, :], axis=2)
    return d < dist_max


def card_mask(img: np.ndarray) -> np.ndarray:
    """牌面掩码：高亮度 + 低饱和（白牌面）。

    实测依据：空区牌面占比 0%，有牌区 65~86%。
    """
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    v, s = hsv[:, :, 2], hsv[:, :, 1]
    return (v > C.CARD_V_MIN) & (s < C.CARD_S_MAX)


def zone_rect(name: str) -> tuple[int, int, int, int]:
    """按常量返回四区在参考坐标系下的 (x0, y0, x1, y1)。"""
    cx, cy = C.REF_ZONE_CENTERS[name]
    x0 = int(round(cx - C.ZONE_W / 2))
    y0 = int(round(cy - C.ZONE_H / 2))
    return x0, y0, x0 + C.ZONE_W, y0 + C.ZONE_H
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_imaging.py -v`
Expected: PASS（8 项）

若 `test_felt_and_card_masks_are_complementary` 失败，说明阈值需要复核——
先跑 `uv run python spike/analyze_anchors.py` 看实测重叠率，再调 `CARD_V_MIN` / `CARD_S_MAX`，
不要靠猜。

- [ ] **Step 5: 提交**

```bash
git add src/shengji/imaging.py tests/test_imaging.py
git commit -m "feat: 桌面色与牌面分割，用真实截图验证空区/有牌区判定"
```

---

### Task 5: 牌张尺寸自动测量（校验常量）

**Files:**
- Modify: `src/shengji/imaging.py`
- Test: `tests/test_imaging.py`

**为什么需要**：用户确认窗口尺寸不影响牌宽，所以牌宽是**校验常量**——
它应该恒为 56×79；一旦变化说明 DPI 缩放被改或客户端更新，必须告警而非静默适配。

- [ ] **Step 1: 写失败的测试**

在 `tests/test_imaging.py` 末尾追加：

```python
from shengji.imaging import largest_card_blob, measure_card_width


def test_largest_card_blob_on_two_cards(shots):
    """所有人出两张时，每区最大连通块应是『两张牌叠放』= 72x79 左右。"""
    for name in REF_ZONE_CENTERS:
        seg = _crop(shots["all_two"], name)
        blob = largest_card_blob(seg, min_area=200)
        assert blob is not None, f"{name} 未找到牌面连通块"
        w, h = blob["w"], blob["h"]
        assert 60 <= w <= 90, f"{name} 两张牌宽度异常: {w}"
        assert abs(h - 79) <= 10, f"{name} 牌高异常: {h}"


def test_measure_card_width_recovers_56(shots):
    """由「两张牌宽 72」与「叠放偏移 16」应反推出单张牌宽 56。"""
    w = measure_card_width(shots["all_two"])
    assert w is not None
    assert abs(w - 56) <= 8, f"反推牌宽异常: {w}"


def test_measure_card_width_none_when_no_cards(shots):
    assert measure_card_width(shots["empty"]) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_imaging.py -v`
Expected: FAIL — `ImportError: cannot import name 'largest_card_blob'`

- [ ] **Step 3: 实现测量函数**

在 `src/shengji/imaging.py` 末尾追加：

```python
def largest_card_blob(seg: np.ndarray, min_area: int = 200) -> dict | None:
    """在区域内找最大的牌面连通块，返回其外接框；无则 None。"""
    m = card_mask(seg).astype(np.uint8)
    # 仅连接牌内部被花色符号切碎的部分，不做大核膨胀（避免跨牌/跨区合并）
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < min_area:
            continue
        if best is None or area > best["area"]:
            best = {"x": int(x), "y": int(y), "w": int(w), "h": int(h), "area": int(area)}
    return best


def measure_card_width(img: np.ndarray) -> float | None:
    """从整幅截图自动反推单张牌宽度，用于校验布局是否仍成立。

    原理：检出的最大牌面块宽度 = CARD_W + (张数-1) * CARD_STACK_OFFSET。
    观测到的宽度只可能是 56（1 张）、72（2 张）、88（3 张）...
    因此取「不超过观测宽度的最大合法值」作为单张宽度估计：
        单张宽 = 观测宽 - k * CARD_STACK_OFFSET，k 取使结果最接近 56 的整数。

    返回 None 表示画面中没有牌（无法测量）。
    """
    widths: list[int] = []
    for name in C.REF_ZONE_CENTERS:
        x0, y0, x1, y1 = zone_rect(name)
        seg = img[y0:y1, x0:x1]
        blob = largest_card_blob(seg)
        if blob is not None:
            widths.append(blob["w"])
    if not widths:
        return None
    observed = float(np.median(widths))
    best_w, best_err = observed, 1e9
    for k in range(0, 6):
        cand = observed - k * C.CARD_STACK_OFFSET
        err = abs(cand - C.CARD_W)
        if err < best_err:
            best_w, best_err = cand, err
    return best_w
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_imaging.py -v`
Expected: PASS（11 项）

- [ ] **Step 5: 提交**

```bash
git add src/shengji/imaging.py tests/test_imaging.py
git commit -m "feat: 牌张宽度自动测量，作为布局校验常量"
```

---

### Task 6: 几何类型与四区推导

**Files:**
- Create: `src/shengji/geometry.py`
- Test: `tests/test_geometry.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_geometry.py`：

```python
import pytest

from shengji.geometry import Rect, layout_from_center


def test_rect_basics():
    r = Rect(10, 20, 30, 40)  # x0 y0 x1 y1
    assert r.w == 20
    assert r.h == 20
    assert r.center == (20.0, 30.0)


def test_rect_rejects_inverted():
    with pytest.raises(ValueError):
        Rect(30, 20, 10, 40)


def test_rect_clip_to_bounds():
    r = Rect(-5, -5, 50, 50)
    c = r.clip(0, 0, 100, 100)
    assert (c.x0, c.y0, c.x1, c.y1) == (0, 0, 50, 50)


def test_layout_from_center_produces_symmetric_cross():
    """给定中心与臂长，四区中心必须构成对称十字。"""
    zones = layout_from_center(cx=600.0, cy=300.0,
                               arm_x=107.5, arm_y_top=82.0, arm_y_bottom=82.0,
                               zone_w=73, zone_h=79)
    assert set(zones) == {"top", "left", "right", "bottom"}
    assert zones["top"].center == (600.0, 218.0)
    assert zones["bottom"].center == (600.0, 382.0)
    assert zones["left"].center == (492.5, 300.0)
    assert zones["right"].center == (707.5, 300.0)


def test_layout_from_center_keeps_zone_size():
    zones = layout_from_center(600.0, 300.0, 107.5, 82.0, 82.0, 73, 79)
    for r in zones.values():
        assert r.w == 73
        assert r.h == 79
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_geometry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.geometry'`

- [ ] **Step 3: 实现 geometry.py**

创建 `src/shengji/geometry.py`：

```python
"""矩形与四区几何。纯计算，无 IO、无状态。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Rect:
    """轴对齐矩形，(x0, y0) 左上、(x1, y1) 右下，x1/y1 为开区间。"""

    x0: int
    y0: int
    x1: int
    y1: int

    def __post_init__(self) -> None:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(f"矩形坐标反转: {(self.x0, self.y0, self.x1, self.y1)}")

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    @property
    def center(self) -> tuple[float, float]:
        return (self.x0 + self.w / 2.0, self.y0 + self.h / 2.0)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x0, self.y0, self.x1, self.y1)

    def clip(self, bx0: int, by0: int, bx1: int, by1: int) -> "Rect":
        """裁到给定边界内。"""
        return Rect(max(self.x0, bx0), max(self.y0, by0),
                    min(self.x1, bx1), min(self.y1, by1))

    def expand(self, dx: int, dy: int, bounds: tuple[int, int] | None = None) -> "Rect":
        """四周外扩。bounds=(W, H) 时同时裁到画面内。"""
        r = Rect(self.x0 - dx, self.y0 - dy, self.x1 + dx, self.y1 + dy)
        if bounds is not None:
            r = r.clip(0, 0, bounds[0], bounds[1])
        return r


def layout_from_center(
    cx: float,
    cy: float,
    arm_x: float,
    arm_y_top: float,
    arm_y_bottom: float,
    zone_w: int,
    zone_h: int,
) -> dict[str, Rect]:
    """由「中心 + 臂长」推导四个出牌区。

    实测表明四区构成规则十字（spec §11A），因此 3 个数即可描述四区，
    而非 4 个独立矩形。上下臂长分开传参，因为实测上下并不完全对称
    （top 79px / bottom 85px），保留这一差异以免把真实布局「拉正」。
    """
    arms = {
        "top": (0.0, -arm_y_top),
        "bottom": (0.0, +arm_y_bottom),
        "left": (-arm_x, 0.0),
        "right": (+arm_x, 0.0),
    }
    out: dict[str, Rect] = {}
    for name, (dx, dy) in arms.items():
        zx, zy = cx + dx, cy + dy
        x0 = int(round(zx - zone_w / 2))
        y0 = int(round(zy - zone_h / 2))
        out[name] = Rect(x0, y0, x0 + zone_w, y0 + zone_h)
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_geometry.py -v`
Expected: PASS（6 项）

- [ ] **Step 5: 提交**

```bash
git add src/shengji/geometry.py tests/test_geometry.py
git commit -m "feat: Rect 与四区几何推导（中心+臂长）"
```

---

### Task 7: 布局数据模型与三假设预测

**Files:**
- Create: `src/shengji/layout/__init__.py`
- Create: `src/shengji/layout/model.py`
- Test: `tests/test_layout_model.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_layout_model.py`：

```python
from shengji.layout.model import AnchorMode, LayoutModel


def _ref_model():
    return LayoutModel.from_reference()


def test_from_reference_matches_measured_centers():
    m = _ref_model()
    assert m.reference_size == (1280, 720)
    z = m.zones
    assert z["top"].center == (557.5, 246.5)
    assert z["right"].center == (663.0, 322.5)


def test_reference_anchor_is_identity():
    """参考尺寸 + H1(左上角) 锚定时，布局应等于实测坐标本身。"""
    m = _ref_model()
    assert m.anchor_mode is AnchorMode.TOP_LEFT
    shifted = m.for_client(0, 0, 1280, 720)
    assert shifted.zones["top"].center == (557.5, 246.5)


def test_top_left_anchor_shifts_by_origin():
    m = _ref_model()
    s = m.for_client(100, 50, 1280, 720)   # 窗口移到 (100,50)
    assert s.zones["top"].center == (657.5, 296.5)


def test_center_anchor_centers_layout_in_client():
    m = _ref_model().with_anchor(AnchorMode.CENTER)
    # 客户区变大 200x100 -> 布局整体右移 100、下移 50
    s = m.for_client(0, 0, 1480, 820)
    assert s.zones["top"].center == (657.5, 296.5)


def test_arm_lengths_preserved_across_anchors():
    m = _ref_model()
    for mode in (AnchorMode.TOP_LEFT, AnchorMode.CENTER):
        s = m.with_anchor(mode).for_client(0, 0, 1280, 720)
        cx = sum(r.center[0] for r in s.zones.values()) / 4
        assert abs(s.zones["left"].center[0] - (cx - 108.9)) < 0.51
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_layout_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.layout'`

- [ ] **Step 3: 实现 model.py**

创建 `src/shengji/layout/__init__.py`（空文件）。

创建 `src/shengji/layout/model.py`：

```python
"""布局模型：锚点模式 + 固定像素偏移 -> 实际四区矩形。

关键约束（用户实测确认）：窗口尺寸不影响牌的长宽，
因此布局是固定像素的，偏移量是常量，不做等比缩放。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum

from .. import constants as C
from ..geometry import Rect, layout_from_center


class AnchorMode(str, Enum):
    """牌桌相对客户区的锚定方式（spec §11A 的三假设）。"""

    TOP_LEFT = "top_left"   # H1：客户区左上角 + 固定偏移
    CENTER = "center"       # H2：客户区中心 + 固定偏移
    DETECTED = "detected"   # H3：由桌面颜色锚点定位


@dataclass(frozen=True)
class LayoutModel:
    """一套布局：四区几何 + 描述的锚定方式。"""

    reference_size: tuple[int, int]
    center: tuple[float, float]
    arm_x: float
    arm_y_top: float
    arm_y_bottom: float
    zone_w: int
    zone_h: int
    anchor_mode: AnchorMode = AnchorMode.TOP_LEFT

    @classmethod
    def from_reference(cls) -> "LayoutModel":
        """由 spec §11.2 的实测值构造参考布局。"""
        z = C.REF_ZONE_CENTERS
        cx = sum(p[0] for p in z.values()) / 4.0
        cy = sum(p[1] for p in z.values()) / 4.0
        return cls(
            reference_size=(C.REF_W, C.REF_H),
            center=(cx, cy),
            arm_x=(z["right"][0] - z["left"][0]) / 2.0,
            arm_y_top=cy - z["top"][1],
            arm_y_bottom=z["bottom"][1] - cy,
            zone_w=C.ZONE_W,
            zone_h=C.ZONE_H,
        )

    def with_anchor(self, mode: AnchorMode) -> "LayoutModel":
        return replace(self, anchor_mode=mode)

    @property
    def zones(self) -> dict[str, Rect]:
        return layout_from_center(self.center[0], self.center[1],
                                  self.arm_x, self.arm_y_top, self.arm_y_bottom,
                                  self.zone_w, self.zone_h)

    def _layout_origin_in_client(self, cl_x: int, cl_y: int,
                                 cl_w: int, cl_h: int) -> tuple[float, float]:
        """返回『参考坐标系原点』落在客户区里的位置（客户区局部坐标）。"""
        rw, rh = self.reference_size
        if self.anchor_mode is AnchorMode.CENTER:
            # 参考画面在客户区内居中
            return ((cl_w - rw) / 2.0, (cl_h - rh) / 2.0)
        # TOP_LEFT 与 DETECTED 都以上一层的锚点为原点
        return (0.0, 0.0)

    def for_client(self, cl_x: int, cl_y: int, cl_w: int, cl_h: int) -> "LayoutModel":
        """把布局换算到给定客户区（客户区在屏幕上的位置为 cl_x, cl_y）。

        返回的模型其 center 已是屏幕绝对坐标。
        """
        ox, oy = self._layout_origin_in_client(cl_x, cl_y, cl_w, cl_h)
        return replace(self, center=(cl_x + ox + self.center[0],
                                     cl_y + oy + self.center[1]))

    def with_center(self, cx: float, cy: float) -> "LayoutModel":
        return replace(self, center=(cx, cy))

    def to_dict(self) -> dict:
        return {
            "reference_size": list(self.reference_size),
            "center": list(self.center),
            "arm_x": self.arm_x,
            "arm_y_top": self.arm_y_top,
            "arm_y_bottom": self.arm_y_bottom,
            "zone_w": self.zone_w,
            "zone_h": self.zone_h,
            "anchor_mode": self.anchor_mode.value,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "LayoutModel":
        return cls(
            reference_size=tuple(d["reference_size"]),
            center=tuple(d["center"]),
            arm_x=float(d["arm_x"]),
            arm_y_top=float(d["arm_y_top"]),
            arm_y_bottom=float(d["arm_y_bottom"]),
            zone_w=int(d["zone_w"]),
            zone_h=int(d["zone_h"]),
            anchor_mode=AnchorMode(d.get("anchor_mode", "top_left")),
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_layout_model.py -v`
Expected: PASS（5 项）

- [ ] **Step 5: 提交**

```bash
git add src/shengji/layout tests/test_layout_model.py
git commit -m "feat: 布局模型（锚点模式 + 固定像素偏移）"
```

---

### Task 8: 布局不变量校验

**Files:**
- Create: `src/shengji/layout/validate.py`
- Test: `tests/test_layout_validate.py`

**为什么需要**：让定位失效**可观测**，而不是静默算错（spec §11A L4）。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_layout_validate.py`：

```python
from dataclasses import replace

from shengji.layout.model import LayoutModel
from shengji.layout.validate import (
    ValidationReport,
    Violation,
    validate_against_frame,
    validate_geometry,
)


# ---------- 几何校验：与画面无关，任何时候都能跑 ----------

def test_reference_layout_passes_geometry():
    rep = validate_geometry(LayoutModel.from_reference())
    assert rep.ok, [v.detail for v in rep.violations]


def test_detects_non_symmetric_cross():
    """把左右臂拉成明显不对称，应报 arm_asymmetry_x。"""
    bad = replace(LayoutModel.from_reference(), arm_x=200.0)
    rep = validate_geometry(bad)
    assert not rep.ok
    assert any(v.code == "arm_asymmetry_x" for v in rep.violations)


def test_detects_wrong_zone_size():
    bad = replace(LayoutModel.from_reference(), zone_h=200)
    rep = validate_geometry(bad)
    assert any(v.code == "zone_size_mismatch" for v in rep.violations)


def test_detects_zone_too_narrow():
    bad = replace(LayoutModel.from_reference(), zone_w=30)
    rep = validate_geometry(bad)
    assert any(v.code == "zone_too_narrow" for v in rep.violations)


def test_detects_card_width_drift():
    """牌宽偏离基准 —— 用于发现 DPI 缩放被改或客户端改版。"""
    rep = validate_geometry(LayoutModel.from_reference(), card_width=70.0)
    assert any(v.code == "card_width_drift" for v in rep.violations)


def test_violation_is_descriptive():
    v = Violation("arm_asymmetry_x", "左右臂不对称 40.0px")
    assert "arm_asymmetry_x" in repr(v)


# ---------- 牌面块一致性校验：这才是真正有区分力的判据 ----------
# 设计变更（实测驱动）：初稿用「区必须在桌面色上」校验，但实测发现
# 牌桌周围的背景与桌面同色（阈值 90 时空区覆盖 100% 但全图覆盖 74%），
# 该判据没有区分力。改用「牌尺寸块」这一强特征。

def test_against_frame_verifies_reference_on_all_two(shots):
    rep = validate_against_frame(LayoutModel.from_reference(), shots["all_two"])
    assert rep.verifiable
    assert rep.ok, [v.detail for v in rep.violations]


def test_against_frame_verifies_reference_on_others_one(shots):
    rep = validate_against_frame(LayoutModel.from_reference(), shots["others_one"])
    assert rep.verifiable
    assert rep.ok, [v.detail for v in rep.violations]


def test_against_frame_marks_empty_frame_unverifiable(shots):
    """空桌无法校验 —— 必须显式表达为 verifiable=False，而不是假装通过。

    实测：空桌帧里唯一的「牌尺寸块」是右侧静态 UI 元素 (1042,282) 68x74，
    其面积 1836 低于 CARD_BLOB_MIN_AREA=3000，因此应被滤掉，候选数为 0。
    """
    rep = validate_against_frame(LayoutModel.from_reference(), shots["empty"])
    assert not rep.verifiable
    assert rep.ok


def test_against_frame_rejects_shifted_layout(shots):
    """布局整体右移 200px 后，真实牌块全部落到预测区之外，必须被否决。"""
    m = LayoutModel.from_reference()
    cx, cy = m.center
    rep = validate_against_frame(m.with_center(cx + 200, cy), shots["all_two"])
    assert rep.verifiable
    assert not rep.ok
    assert any(v.code == "cards_outside_zones" for v in rep.violations)


def test_against_frame_rejects_shifted_layout_down(shots):
    m = LayoutModel.from_reference()
    cx, cy = m.center
    rep = validate_against_frame(m.with_center(cx, cy + 200), shots["all_two"])
    assert rep.verifiable
    assert not rep.ok


def test_report_default_is_ok_and_verifiable():
    rep = ValidationReport()
    assert rep.ok
    assert rep.verifiable
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_layout_validate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.layout.validate'`

- [ ] **Step 3: 实现 validate.py**

创建 `src/shengji/layout/validate.py`：

```python
"""布局校验：几何不变量 + 牌面块一致性。

失败即告警，绝不静默继续。

设计变更（实测驱动）：初稿用「区必须落在桌面色上」校验，
但实测发现牌桌周围的背景与桌面同色（阈值 90 时空区覆盖 100%，全图覆盖 74%），
该判据没有区分力。改用「牌尺寸块」这一强特征：
牌是 56x79、宽高比 0.709、面积 3700+ 的白色块，随机出现的概率极低。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from .. import constants as C
from .model import LayoutModel


@dataclass(frozen=True)
class Violation:
    code: str
    detail: str

    def __repr__(self) -> str:  # pragma: no cover - 便于日志阅读
        return f"Violation({self.code}: {self.detail})"


@dataclass
class ValidationReport:
    violations: list[Violation] = field(default_factory=list)
    verifiable: bool = True   # False = 当前帧信息不足（如空桌），不等于「通过」

    @property
    def ok(self) -> bool:
        return not self.violations


def validate_geometry(m: LayoutModel, card_width: float | None = None) -> ValidationReport:
    """与画面无关的几何校验。任何时候都能跑。"""
    rep = ValidationReport()

    if abs(m.zone_h - C.CARD_H) > 10:
        rep.violations.append(Violation(
            "zone_size_mismatch",
            f"区高 {m.zone_h} 与牌高 {C.CARD_H} 相差超过 10px"))
    if m.zone_w < C.CARD_W:
        rep.violations.append(Violation(
            "zone_too_narrow",
            f"区宽 {m.zone_w} 小于单张牌宽 {C.CARD_W}，无法容纳任何出牌"))

    if abs(m.arm_y_top - m.arm_y_bottom) > C.TOL_ARM_SYMMETRY_Y:
        rep.violations.append(Violation(
            "arm_asymmetry_y",
            f"上下臂不对称 {abs(m.arm_y_top - m.arm_y_bottom):.1f}px "
            f"(限 {C.TOL_ARM_SYMMETRY_Y}px)"))

    zones = m.zones
    zcx = sum(r.center[0] for r in zones.values()) / 4.0
    left_dx = zcx - zones["left"].center[0]
    right_dx = zones["right"].center[0] - zcx
    if abs(left_dx - right_dx) > C.TOL_ARM_SYMMETRY_X:
        rep.violations.append(Violation(
            "arm_asymmetry_x",
            f"左右臂不对称 {abs(left_dx - right_dx):.1f}px "
            f"(限 {C.TOL_ARM_SYMMETRY_X}px)"))

    if card_width is not None and abs(card_width - C.CARD_W) > C.TOL_CARD_W:
        rep.violations.append(Violation(
            "card_width_drift",
            f"牌宽 {card_width:.1f} 偏离基准 {C.CARD_W}±{C.TOL_CARD_W}，"
            f"可能是 DPI 缩放被改或客户端改版，需重新标定"))

    return rep


def find_card_sized_blobs(frame: np.ndarray) -> list[tuple[int, int, int, int]]:
    """全图扫出「牌尺寸」的白色块，返回 (x, y, w, h) 列表。

    面积下限是关键：实测 1 张牌块约 3736px，而右侧静态 UI 元素
    (1042,282) 68x74 面积仅 1836 且宽高比同为 0.92 —— 只有面积能区分。
    """
    from ..imaging import card_mask

    m = card_mask(frame).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(m, 8)
    out: list[tuple[int, int, int, int]] = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < C.CARD_BLOB_MIN_AREA:
            continue
        if not (C.CARD_BLOB_MIN_W <= w <= C.CARD_BLOB_MAX_W):
            continue
        if not (C.CARD_BLOB_MIN_H <= h <= C.CARD_BLOB_MAX_H):
            continue
        out.append((int(x), int(y), int(w), int(h)))
    return out


def _inside(rect, cx: float, cy: float) -> bool:
    return rect.x0 <= cx <= rect.x1 and rect.y0 <= cy <= rect.y1


def score_hypothesis(m: LayoutModel, blobs: list[tuple[int, int, int, int]],
                     frame_shape: tuple[int, int]) -> tuple[int, int]:
    """返回 (区内块数, 区外块数)。"""
    h, w = frame_shape
    search = [z.expand(C.ZONE_SEARCH_MARGIN_X, C.ZONE_SEARCH_MARGIN_Y).clip(0, 0, w, h)
              for z in m.zones.values()]
    inside = outside = 0
    for bx, by, bw, bh in blobs:
        bcx, bcy = bx + bw / 2.0, by + bh / 2.0
        if any(_inside(r, bcx, bcy) for r in search):
            inside += 1
        else:
            outside += 1
    return inside, outside


def validate_against_frame(m: LayoutModel, frame: np.ndarray) -> ValidationReport:
    """用牌面块校验布局。

    判据（经实测标定）：**区内块数必须多于区外块数**。
    不使用「区外必须为 0」——因为画面中存在静态 UI 元素可能与牌块同形，
    用多数判据可容忍少量杂散块，同时仍能否决明显错位的假设。

    画面中一个牌尺寸块都没有 -> verifiable=False（无法校验，不等于通过）。
    """
    rep = ValidationReport()
    blobs = find_card_sized_blobs(frame)
    if not blobs:
        rep.verifiable = False
        return rep

    inside, outside = score_hypothesis(m, blobs, frame.shape[:2])
    if inside <= outside:
        rep.violations.append(Violation(
            "cards_outside_zones",
            f"预测区内只有 {inside} 个牌块，区外却有 {outside} 个，"
            f"布局与实际牌位不符"))
    return rep
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_layout_validate.py -v`
Expected: PASS（12 项）

- [ ] **Step 5: 提交**

```bash
git add src/shengji/layout/validate.py tests/test_layout_validate.py
git commit -m "feat: 布局校验（几何不变量 + 牌面块一致性），弃用无区分力的桌面色判据"
```

---

### Task 9: 端到端四区定位（用真实截图验证）

**Files:**
- Create: `src/shengji/layout/detect.py`
- Test: `tests/test_layout_detect.py`

**这一步是本计划的核心验收**：用 4 张不同局面的真实截图，
断言「四区定位 + 占用检测」的结果与局面语义完全一致。

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_layout_detect.py`：

```python
from shengji.layout.detect import detect_occupied_zones
from shengji.layout.model import LayoutModel


def test_empty_state_has_no_occupied_zone(shots):
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["empty"], m)
    assert occ == {}


def test_others_one_occupies_all_but_bottom(shots):
    """其他三家各出一张：上/左/右有牌，自己(下)没出。"""
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["others_one"], m)
    assert set(occ) == {"top", "left", "right"}, occ


def test_next_two_occupies_only_right(shots):
    """下家出两张 —— 实测推得下家 = 右区。"""
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["next_two"], m)
    assert set(occ) == {"right"}, occ


def test_all_two_occupies_every_zone(shots):
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["all_two"], m)
    assert set(occ) == {"top", "left", "right", "bottom"}, occ


def test_card_count_estimates_from_blob_width(shots):
    """两张牌宽 72、单张 56 -> 应由宽度反推张数。"""
    m = LayoutModel.from_reference()
    occ = detect_occupied_zones(shots["all_two"], m)
    assert occ["top"]["count"] == 2
    occ1 = detect_occupied_zones(shots["others_one"], m)
    assert occ1["top"]["count"] == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_layout_detect.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.layout.detect'`

- [ ] **Step 3: 实现 detect.py**

创建 `src/shengji/layout/detect.py`：

```python
"""出牌区占用检测与锚定选择（spec §11A 的三假设）。"""

from __future__ import annotations

import numpy as np

from .. import constants as C
from ..imaging import card_mask, largest_card_blob
from .model import AnchorMode, LayoutModel
from .validate import ValidationReport, validate_against_frame, validate_geometry


def estimate_card_count(blob_w: int, card_w: float = C.CARD_W) -> int:
    """由外接框宽度反推牌张数。

    宽度模型：w = card_w + (n-1) * CARD_STACK_OFFSET
    """
    if blob_w < card_w * 0.6:
        return 1
    n = 1 + round((blob_w - card_w) / C.CARD_STACK_OFFSET)
    return max(1, int(n))


def zone_occupancy(frame: np.ndarray, rect) -> dict | None:
    """判断单区是否有牌；有则返回 {blob, width, height, count, card_frac}。"""
    h, w = frame.shape[:2]
    r = rect.clip(0, 0, w, h)
    if r.w <= 0 or r.h <= 0:
        return None
    seg = frame[r.y0:r.y1, r.x0:r.x1]
    frac = float(card_mask(seg).mean())
    if frac < 0.20:
        return None
    blob = largest_card_blob(seg)
    if blob is None:
        return None
    return {
        "blob": blob,
        "width": blob["w"],
        "height": blob["h"],
        "count": estimate_card_count(blob["w"]),
        "card_frac": frac,
        "rect": r,
    }


def detect_occupied_zones(frame: np.ndarray, model: LayoutModel,
                          margin_x: int | None = None) -> dict[str, dict]:
    """返回 {区名: 占用信息}，只含实际有牌的区。

    检索区在标定的区 bbox 基础上左右放宽，以容纳甩牌（一次出多张）。
    """
    mx = C.ZONE_SEARCH_MARGIN_X if margin_x is None else margin_x
    out: dict[str, dict] = {}
    for name, rect in model.zones.items():
        occ = zone_occupancy(frame, rect.expand(mx, C.ZONE_SEARCH_MARGIN_Y))
        if occ is not None:
            out[name] = occ
    return out


def select_anchor(frame: np.ndarray, client: tuple[int, int, int, int],
                  base: LayoutModel) -> tuple[LayoutModel, ValidationReport, AnchorMode]:
    """按 H1 -> H2 顺序选择锚定方式，返回第一个通过校验的。

    client = (x, y, w, h) 为窗口客户区在屏幕上的矩形。

    设计要点（实测驱动）：
    - H3「桌面颜色锚点」已**作废** —— 实测牌桌与背景同色，颜色无法定位牌桌
    - 空桌时无法校验（画面里没有牌尺寸块），此时**默认 H1 并标记未验证**，
      由调用方在首墩出牌后复核；这比胡乱切换假设更安全
    - 全部失败时返回校验未通过的结果与报告，交由调用方提示重新标定
    """
    cl_x, cl_y, cl_w, cl_h = client
    last_report: ValidationReport = ValidationReport()
    last_model = base

    for mode in (AnchorMode.TOP_LEFT, AnchorMode.CENTER):
        cand = base.with_anchor(mode).for_client(cl_x, cl_y, cl_w, cl_h)
        rep = validate_geometry(cand)
        if not rep.ok:
            last_report, last_model = rep, cand
            continue
        frep = validate_against_frame(cand, frame)
        if frep.ok:
            # 空桌时 frep.verifiable=False —— 仍然采用，但把「未验证」传递出去
            frep.violations.extend(rep.violations)
            return cand, frep, mode
        last_report, last_model = frep, cand

    return last_model, last_report, AnchorMode.TOP_LEFT
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_layout_detect.py -v`
Expected: PASS（5 项）

**若 `test_others_one_occupies_all_but_bottom` 或 `test_card_count_estimates_from_blob_width` 失败**：
不要调整断言迁就实现。先跑
`uv run python -c "..."` 或 `spike/analyze_screenshots4.py` 复核该局面下各区的实测白牌面占比与
外接框宽度，确认是常量还是实现有偏差，再改。

- [ ] **Step 5: 提交**

```bash
git add src/shengji/layout/detect.py tests/test_layout_detect.py
git commit -m "feat: 四区占用检测与三假设锚定选择，用 4 张真实局面截图验收"
```

---

### Task 10: win32 窗口定位与 DPI 感知

**Files:**
- Create: `src/shengji/window/__init__.py`
- Create: `src/shengji/window/win32.py`
- Test: `tests/test_window_win32.py`

- [ ] **Step 1: 写失败的测试（不需要游戏在跑）**

创建 `tests/test_window_win32.py`：

```python
import pytest

from shengji.window.win32 import (
    ClientRect,
    enable_per_monitor_dpi_awareness,
    find_windows_by_title,
)


def test_enable_dpi_awareness_is_idempotent():
    """重复调用不应抛异常（进程 DPI 感知只能设置一次）。"""
    assert enable_per_monitor_dpi_awareness() in (True, False)
    assert enable_per_monitor_dpi_awareness() in (True, False)


def test_find_windows_returns_list():
    res = find_windows_by_title("__不存在的窗口标题__")
    assert isinstance(res, list)
    assert res == []


def test_client_rect_dataclass():
    r = ClientRect(hwnd=1, x=10, y=20, w=800, h=600, title="t")
    assert r.origin == (10, 20)
    assert r.size == (800, 600)


def test_find_own_console_window_or_skip():
    """本进程至少应能找到自己相关的一个窗口；找不到就跳过（CI 无窗口环境）。"""
    res = find_windows_by_title("")
    if not res:
        pytest.skip("当前环境无可见窗口")
    assert all(isinstance(r, ClientRect) for r in res)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_window_win32.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.window'`

- [ ] **Step 3: 实现 win32.py**

创建 `src/shengji/window/__init__.py`（空文件）。

创建 `src/shengji/window/win32.py`：

```python
"""Windows 窗口定位。所有坐标一律用『客户区』，不用窗口外框。"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.windll.user32

_DPI_AWARE_SET = False


def enable_per_monitor_dpi_awareness() -> bool:
    """声明 Per-Monitor DPI Aware，避免逻辑/物理坐标错位。

    必须在创建任何窗口前调用。重复调用返回 False 而不抛异常。
    """
    global _DPI_AWARE_SET
    if _DPI_AWARE_SET:
        return False
    try:
        # 2 = PROCESS_PER_MONITOR_DPI_AWARE
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            return False
    _DPI_AWARE_SET = True
    return True


@dataclass(frozen=True)
class ClientRect:
    """窗口客户区在屏幕上的位置（物理像素）。"""

    hwnd: int
    x: int
    y: int
    w: int
    h: int
    title: str

    @property
    def origin(self) -> tuple[int, int]:
        return (self.x, self.y)

    @property
    def size(self) -> tuple[int, int]:
        return (self.w, self.h)


def _client_rect_of(hwnd: int) -> tuple[int, int, int, int] | None:
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    w, h = rect.right - rect.left, rect.bottom - rect.top
    if w <= 0 or h <= 0:
        return None
    pt = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(pt)):
        return None
    return (pt.x, pt.y, w, h)


def _title_of(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def find_windows_by_title(substr: str, visible_only: bool = True) -> list[ClientRect]:
    """枚举顶层窗口，返回标题包含 substr 的窗口客户区列表。

    substr 为空字符串时返回所有可见顶层窗口。
    """
    out: list[ClientRect] = []
    EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _cb(hwnd, _lparam):
        if visible_only and not user32.IsWindowVisible(hwnd):
            return True
        title = _title_of(hwnd)
        if substr and substr not in title:
            return True
        cr = _client_rect_of(hwnd)
        if cr is None:
            return True
        x, y, w, h = cr
        out.append(ClientRect(hwnd=int(hwnd), x=x, y=y, w=w, h=h, title=title))
        return True

    user32.EnumWindows(EnumProc(_cb), 0)
    return out


# 待 Phase 0 实测确认后填入真实类名/标题关键字。
# 不要凭猜测写死；用 tools/list_windows.py 现场枚举。
CANDIDATE_TITLE_KEYWORDS: tuple[str, ...] = ("QQ游戏", "升级", "拖拉机")


def find_game_window() -> ClientRect | None:
    """按候选关键字查找游戏窗口，返回面积最大的一个。"""
    found: list[ClientRect] = []
    for kw in CANDIDATE_TITLE_KEYWORDS:
        found.extend(find_windows_by_title(kw))
    if not found:
        return None
    return max(found, key=lambda r: r.w * r.h)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_window_win32.py -v`
Expected: PASS（4 项，或 1 项 skip）

- [ ] **Step 5: 提交**

```bash
git add src/shengji/window tests/test_window_win32.py
git commit -m "feat: win32 窗口客户区定位与 DPI 感知"
```

---

### Task 11: 采集后端与自动降级

**Files:**
- Create: `src/shengji/capture/__init__.py`
- Create: `src/shengji/capture/base.py`
- Create: `src/shengji/capture/dxcam_backend.py`
- Create: `src/shengji/capture/mss_backend.py`
- Create: `src/shengji/capture/factory.py`
- Test: `tests/test_capture_factory.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_capture_factory.py`：

```python
import numpy as np

from shengji.capture.base import Frame
from shengji.capture.factory import (
    DegradedMode,
    NullBackend,
    build_backend,
    degraded_poll_interval,
)


def test_frame_records_timestamp_and_region():
    f = Frame(image=np.zeros((4, 4, 3), dtype=np.uint8), ts=1.5, region=(0, 0, 4, 4))
    assert f.ts == 1.5
    assert f.region == (0, 0, 4, 4)


def test_null_backend_returns_none():
    b = NullBackend()
    assert b.grab((0, 0, 10, 10)) is None
    b.close()


def test_build_backend_falls_back_to_null_when_all_fail():
    """两种真实后端都不可用时应降级到 NullBackend，而不是抛异常。"""
    b, mode = build_backend(allow_dxcam=False, allow_mss=False)
    assert isinstance(b, NullBackend)
    assert mode is DegradedMode.NONE


def test_degraded_poll_interval_is_slower_for_mss():
    """mss 单帧约 17.7ms（约一个核心），必须降频，不能维持 60Hz。"""
    assert degraded_poll_interval(DegradedMode.DXCAM) == 1 / 60
    assert degraded_poll_interval(DegradedMode.MSS) == 1 / 15
    assert degraded_poll_interval(DegradedMode.NONE) == 1 / 5
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_capture_factory.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.capture'`

- [ ] **Step 3: 实现 base.py**

创建 `src/shengji/capture/__init__.py`（空文件）。

创建 `src/shengji/capture/base.py`：

```python
"""采集后端抽象。上层只依赖本模块的 Protocol。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class Frame:
    """一帧画面及其元数据。"""

    image: np.ndarray
    ts: float
    region: tuple[int, int, int, int]


@runtime_checkable
class CaptureBackend(Protocol):
    """采集后端。

    grab(region) 在 region 为 None 时抓整个输出；
    屏幕无变化时**应返回 None**（Desktop Duplication 的原生语义），
    这是零成本变化侦测的基础。
    """

    def grab(self, region: tuple[int, int, int, int] | None) -> Frame | None: ...

    def close(self) -> None: ...
```

- [ ] **Step 4: 实现 dxcam_backend.py**

创建 `src/shengji/capture/dxcam_backend.py`：

```python
"""Desktop Duplication（dxcam）后端 —— 主路径。

实测：单次 grab 无新帧 0.038ms、有变化 0.042ms；60FPS 下约 0.25% 单核；
屏幕无变化时返回 None。
"""

from __future__ import annotations

import time

import numpy as np

from .base import Frame


class DxcamBackend:
    def __init__(self, output_idx: int = 0, output_color: str = "BGR") -> None:
        import dxcam  # 延迟导入，便于在无显卡环境测试其它部分

        self._cam = dxcam.create(output_idx=output_idx, output_color=output_color)
        if self._cam is None:
            raise RuntimeError(f"dxcam 无法创建输出 {output_idx}")
        self._output_idx = output_idx

    def grab(self, region: tuple[int, int, int, int] | None = None) -> Frame | None:
        img = self._cam.grab(region=region)
        if img is None:
            return None
        ts = time.perf_counter()
        h, w = img.shape[:2]
        rg = region if region is not None else (0, 0, w, h)
        return Frame(image=img, ts=ts, region=rg)

    def close(self) -> None:
        try:
            self._cam.release()
        except Exception:
            pass
```

- [ ] **Step 5: 实现 mss_backend.py**

创建 `src/shengji/capture/mss_backend.py`：

```python
"""BitBlt（mss）后端 —— 降级路径。

实测：整屏单帧约 17.7ms（≈一个完整核心），比 dxcam 慢约 400 倍，
因此必须配合降频使用（见 factory.degraded_poll_interval）。
"""

from __future__ import annotations

import time

import numpy as np

from .base import Frame


class MssBackend:
    def __init__(self, monitor_index: int = 1) -> None:
        import mss  # 延迟导入

        self._sct = mss.MSS()
        mons = self._sct.monitors
        if monitor_index >= len(mons):
            monitor_index = 1 if len(mons) > 1 else 0
        self._mon = mons[monitor_index]
        self._monitor_index = monitor_index

    def grab(self, region: tuple[int, int, int, int] | None = None) -> Frame | None:
        if region is None:
            x, y, w, h = (self._mon["left"], self._mon["top"],
                          self._mon["width"], self._mon["height"])
        else:
            x, y, w, h = region
        shot = self._sct.grab({"left": x, "top": y, "width": w, "height": h})
        img = np.asarray(shot)[:, :, :3]  # 去掉 alpha，得到 BGR
        return Frame(image=img, ts=time.perf_counter(), region=(x, y, w, h))

    def close(self) -> None:
        try:
            self._sct.close()
        except Exception:
            pass
```

- [ ] **Step 6: 实现 factory.py**

创建 `src/shengji/capture/factory.py`：

```python
"""采集后端选择与降级。

注意：mss 与 dxcam 语义不同 —— mss 永远返回画面（无变化也返回），
dxcam 无变化时返回 None。上层必须按「None 即无变化」处理，
因此 mss 降级时需要上层补充差分（见事件层）。
"""

from __future__ import annotations

from enum import Enum

from .base import CaptureBackend, Frame


class DegradedMode(str, Enum):
    DXCAM = "dxcam"    # 主路径
    MSS = "mss"        # 降级：BitBlt
    NONE = "none"      # 无可用后端


class NullBackend:
    """占位后端：永远没有画面。用于让上层在无采集能力时仍可启动并提示。"""

    def grab(self, region: tuple[int, int, int, int] | None = None) -> Frame | None:
        return None

    def close(self) -> None:
        pass


# 实测依据（spec §4.2 / §9.2）
_POLL = {
    DegradedMode.DXCAM: 1 / 60,   # 60Hz，约 0.25% 单核
    DegradedMode.MSS: 1 / 15,     # 15Hz，因单帧约占一个核心
    DegradedMode.NONE: 1 / 5,     # 5Hz，仅维持存活探测
}


def degraded_poll_interval(mode: DegradedMode) -> float:
    return _POLL[mode]


def build_backend(
    output_idx: int = 0,
    allow_dxcam: bool = True,
    allow_mss: bool = True,
) -> tuple[CaptureBackend, DegradedMode]:
    """按 dxcam -> mss -> Null 顺序尝试，返回后端与所处降级等级。"""
    if allow_dxcam:
        try:
            from .dxcam_backend import DxcamBackend

            return DxcamBackend(output_idx=output_idx), DegradedMode.DXCAM
        except Exception:
            pass
    if allow_mss:
        try:
            from .mss_backend import MssBackend

            return MssBackend(), DegradedMode.MSS
        except Exception:
            pass
    return NullBackend(), DegradedMode.NONE
```

- [ ] **Step 7: 运行测试确认通过**

Run: `uv run pytest tests/test_capture_factory.py -v`
Expected: PASS（4 项）

- [ ] **Step 8: 提交**

```bash
git add src/shengji/capture tests/test_capture_factory.py
git commit -m "feat: 采集后端抽象、dxcam 主路径与 mss 降级"
```

---

### Task 12: 标定配置存储与布局可视化导出

**Files:**
- Create: `src/shengji/calib/__init__.py`
- Create: `src/shengji/calib/store.py`
- Create: `src/shengji/tools/__init__.py`
- Create: `src/shengji/tools/dump_layout.py`
- Test: `tests/test_calib_store.py`

- [ ] **Step 1: 写失败的测试**

创建 `tests/test_calib_store.py`：

```python
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


def test_load_returns_none_on_unknown_anchor_mode(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({
        "layout": {**LayoutModel.from_reference().to_dict(),
                   "anchor_mode": "bogus"},
        "output_idx": 0, "variant": [4, 2],
    }), encoding="utf-8")
    assert load_calibration(p) is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_calib_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.calib'`

- [ ] **Step 3: 实现 store.py**

创建 `src/shengji/calib/__init__.py`（空文件）。

创建 `src/shengji/calib/store.py`：

```python
"""标定配置的读写。配置为可人工查看与手改的 JSON。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..layout.model import AnchorMode, LayoutModel

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
        # 含 AnchorMode 值非法（ValueError）
        return None
```

- [ ] **Step 4: 实现 dump_layout.py**

创建 `src/shengji/tools/__init__.py`（空文件）。

创建 `src/shengji/tools/dump_layout.py`：

```python
"""把检测到的布局画成标注图，供人工核对（自动检测 + 用户确认流程的产出）。

用法：
    uv run python -m shengji.tools.dump_layout png/手牌+所有人出两张.jpg out.png
    uv run python -m shengji.tools.dump_layout --all-fixtures
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

from ..imaging import imread_unicode, imwrite_unicode
from ..layout.detect import detect_occupied_zones, select_anchor
from ..layout.model import LayoutModel
from ..layout.validate import validate_against_frame, validate_geometry

COLORS = {
    "top": (0, 255, 255),
    "left": (255, 128, 0),
    "right": (255, 0, 255),
    "bottom": (0, 255, 0),
}


def annotate(frame: np.ndarray, model: LayoutModel) -> tuple[np.ndarray, dict]:
    vis = frame.copy()
    occ = detect_occupied_zones(frame, model)
    for name, rect in model.zones.items():
        info = occ.get(name)
        color = COLORS[name]
        thick = 3 if info else 1
        cv2.rectangle(vis, (rect.x0, rect.y0), (rect.x1, rect.y1), color, thick)
        label = name if info is None else f"{name} x{info['count']}"
        cv2.putText(vis, label, (rect.x0, max(14, rect.y0 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
    return vis, occ


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="导出布局标注图")
    ap.add_argument("image", nargs="?", help="输入截图路径")
    ap.add_argument("out", nargs="?", help="输出图片路径")
    ap.add_argument("--all-fixtures", action="store_true",
                    help="对 tests/fixtures/screenshots 下全部夹具出图")
    args = ap.parse_args(argv)

    base = LayoutModel.from_reference()
    outdir = Path("spike/shot_analysis")
    outdir.mkdir(parents=True, exist_ok=True)

    if args.all_fixtures:
        fx = Path("tests/fixtures/screenshots")
        files = sorted(fx.glob("*.jpg"))
        if not files:
            print(f"未找到夹具: {fx}", file=sys.stderr)
            return 1
        for p in files:
            img = imread_unicode(p)
            if img is None:
                print(f"无法读取 {p}", file=sys.stderr)
                continue
            # 夹具本身就是参考分辨率下的裁剪，故客户区取全图
            h, w = img.shape[:2]
            model, rep, mode = select_anchor(img, (0, 0, w, h), base)
            vis, occ = annotate(img, model)
            dst = outdir / f"layout_{p.stem}.png"
            imwrite_unicode(dst, vis)
            print(f"{p.name}: 锚定={mode.value} 占用={sorted(occ)} "
                  f"校验={'通过' if rep.ok else '失败'}")
            for v in rep.violations:
                print(f"   ! {v.code}: {v.detail}")
            print(f"   -> {dst}")
        return 0

    if not args.image or not args.out:
        ap.print_help()
        return 1
    img = imread_unicode(args.image)
    if img is None:
        print(f"无法读取 {args.image}", file=sys.stderr)
        return 1
    h, w = img.shape[:2]
    model, rep, mode = select_anchor(img, (0, 0, w, h), base)
    vis, occ = annotate(img, model)
    imwrite_unicode(args.out, vis)
    print(f"锚定={mode.value} 占用={ {k: v['count'] for k, v in occ.items()} }")
    print(f"校验={'通过' if rep.ok else '失败'}")
    for v in rep.violations:
        print(f"  ! {v.code}: {v.detail}")
    print(f"-> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run pytest tests/test_calib_store.py -v`
Expected: PASS（5 项）

- [ ] **Step 6: 实跑可视化工具，人工核对产出**

Run: `uv run python -m shengji.tools.dump_layout --all-fixtures`
Expected: 4 行输出，每行锚定为 `top_left`，占用集合分别为
`[]` / `['left','right','top']` / `['right']` / `['bottom','left','right','top']`，
校验均「通过」，并在 `spike/shot_analysis/layout_*.png` 生成 4 张标注图。

打开这些图核对：红框应准确套在牌桌中央的十字四区上。

- [ ] **Step 7: 提交**

```bash
git add src/shengji/calib src/shengji/tools tests/test_calib_store.py
git commit -m "feat: 标定配置存储与布局可视化导出工具"
```

---

### Task 13: 采样录制工具（Phase 0 交付物）

**Files:**
- Create: `src/shengji/tools/record.py`
- Test: `tests/test_record_tool.py`

**为什么需要**：模板匹配需要真实牌面样本。本工具用「变化触发」自动切片存盘，
不需要人工抓时机（spec §11A / Phase 0 验收项 ①）。

- [ ] **Step 1: 写失败的测试（用假后端，不依赖游戏）**

创建 `tests/test_record_tool.py`：

```python
import numpy as np

from shengji.capture.base import Frame
from shengji.tools.record import SampleWriter, should_save


def test_should_save_on_occupancy_change():
    assert should_save(prev=(), now=("top",)) is True
    assert should_save(prev=("top",), now=("top",)) is False
    assert should_save(prev=("top",), now=("top", "left")) is True


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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_record_tool.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'shengji.tools.record'`

- [ ] **Step 3: 实现 record.py**

创建 `src/shengji/tools/record.py`：

```python
"""采样录制工具：自动把出牌区的牌面切片存盘，供剪模板与调阈值。

触发逻辑：只在「某区占用状态发生变化」且该区已判稳时存盘，
因此不需要人工抓那几秒的显示窗口。

用法：
    uv run python -m shengji.tools.record --out samples/ --seconds 600
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..capture.factory import DegradedMode, build_backend, degraded_poll_interval
from ..imaging import imwrite_unicode
from ..layout.detect import detect_occupied_zones, select_anchor
from ..layout.model import AnchorMode, LayoutModel
from ..window.win32 import enable_per_monitor_dpi_awareness, find_game_window

# 判稳参数（spec §4.3）：连续若干帧差异低于阈值才算「牌已摆定」
SETTLE_FRAMES = 8
SETTLE_DIFF_THRESHOLD = 2.0
VOTE_FRAMES = 3


def should_save(prev: tuple[str, ...], now: tuple[str, ...]) -> bool:
    """占用集合发生变化才存盘（去重，避免同一墩重复采样）。"""
    return tuple(sorted(prev)) != tuple(sorted(now))


def frame_diff_score(a: np.ndarray, b: np.ndarray) -> float:
    """两帧的平均绝对灰度差，用于判稳。"""
    import cv2

    ga = cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)
    gb = cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)
    if ga.shape != gb.shape:
        return 1e9
    return float(cv2.absdiff(ga, gb).mean())


@dataclass
class SampleWriter:
    """把牌面切片与元数据写入磁盘。"""

    root: Path
    deal_id: str
    trick_index: int
    seat: int

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._manifest = self.root / "manifest.jsonl"

    def write_zone(self, zone: str, img: np.ndarray, count: int, ts: float) -> Path:
        name = f"deal{self.deal_id}_trick{self.trick_index:03d}_seat{self.seat}_{zone}_{count}.png"
        p = self.root / name
        imwrite_unicode(p, img)
        rec = {
            "path": name,
            "deal_id": self.deal_id,
            "trick_index": self.trick_index,
            "seat": self.seat,
            "zone": zone,
            "count": count,
            "ts": ts,
        }
        with self._manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return p


def run(out_dir: Path, seconds: float, output_idx: int, max_bursts: int) -> int:
    if not enable_per_monitor_dpi_awareness():
        pass  # 已被设置过，正常
    win = find_game_window()
    if win is None:
        print("未找到游戏窗口。请先启动 QQ 游戏并进入升级牌局。", file=sys.stderr)
        print("可用以下命令查看当前所有窗口标题，以确定候选关键字：", file=sys.stderr)
        print("  uv run python -c \"from shengji.window.win32 import find_windows_by_title;"
              " [print(r.title, r.size) for r in find_windows_by_title('')]\"",
              file=sys.stderr)
        return 2

    backend, mode = build_backend(output_idx=output_idx)
    interval = degraded_poll_interval(mode)
    print(f"窗口: {win.title} 客户区={win.origin} {win.size}")
    print(f"后端: {mode.value}  轮询间隔: {interval*1000:.1f}ms")

    base = LayoutModel.from_reference()

    # 先抓一帧用于锚定选择；抓不到就退回 H1（左上角锚定）
    first = backend.grab(None)
    if first is not None:
        model, rep, mode = select_anchor(
            first.image, (win.x, win.y, win.w, win.h), base)
        if not rep.verifiable:
            print("提示：当前牌桌为空，锚定方式暂按 "
                  f"{mode.value} 假定，将在首墩出牌后自动复核。")
        if not rep.ok:
            for v in rep.violations:
                print(f"  ! {v.code}: {v.detail}", file=sys.stderr)
    else:
        model = base.with_anchor(AnchorMode.TOP_LEFT).for_client(
            win.x, win.y, win.w, win.h)
        mode = AnchorMode.TOP_LEFT
        print("提示：首帧抓取失败，暂按 H1（左上角锚定）运行。")

    prev_occ: tuple[str, ...] = ()
    stable = 0
    prev_gray = None
    saved = 0
    trick = 0
    t_end = time.perf_counter() + seconds

    while time.perf_counter() < t_end:
        f = backend.grab(None)
        if f is None:
            time.sleep(interval)
            continue

        full = f.image
        # 全局判稳：整幅画面稳定若干帧才认为动画结束
        if prev_gray is not None and prev_gray.shape == full.shape:
            if frame_diff_score(full, prev_gray) < SETTLE_DIFF_THRESHOLD:
                stable += 1
            else:
                stable = 0
        prev_gray = full

        if stable < SETTLE_FRAMES:
            time.sleep(interval)
            continue

        occ = detect_occupied_zones(full, model)
        now = tuple(sorted(occ))
        if should_save(prev_occ, now):
            if now == ():
                trick += 1
            elif saved < max_bursts:
                w = SampleWriter(out_dir, deal_id="live", trick_index=trick, seat=-1)
                h, ww = full.shape[:2]
                for zone, info in occ.items():
                    r = info["rect"].clip(0, 0, ww, h)
                    crop = full[r.y0:r.y1, r.x0:r.x1]
                    w.write_zone(zone, crop, info["count"], f.ts)
                    saved += 1
                print(f"  墩{trick} 占用={list(now)} -> 已存 {len(occ)} 张切片")
            prev_occ = now

        time.sleep(interval)

    backend.close()
    print(f"完成：共存 {saved} 张切片 -> {out_dir}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="采样录制（自动切片存盘）")
    ap.add_argument("--out", default="samples", help="输出目录")
    ap.add_argument("--seconds", type=float, default=600.0, help="录制时长（秒）")
    ap.add_argument("--output-idx", type=int, default=0, help="显示器序号")
    ap.add_argument("--max-bursts", type=int, default=400, help="切片数量上限")
    args = ap.parse_args(argv)
    return run(Path(args.out), args.seconds, args.output_idx, args.max_bursts)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_record_tool.py -v`
Expected: PASS（3 项）

- [ ] **Step 5: 全量测试**

Run: `uv run pytest -v`
Expected: 全部 PASS（约 50 项）

- [ ] **Step 6: 提交**

```bash
git add src/shengji/tools/record.py tests/test_record_tool.py
git commit -m "feat: 采样录制工具（变化触发自动切片）"
```

---

## 完成标准

- [x] `uv run pytest` 全部通过（**82 项**），其中包含用 4 张真实局面截图做的端到端四区定位断言
- [x] `uv run python -m shengji.tools.dump_layout --all-fixtures` 输出的 4 张标注图，
      四区检出结果与局面语义逐条吻合
- [ ] `uv run python -m shengji.tools.record --seconds 60` 在真实牌局中能自动存下牌面切片
      —— **待用户在真实牌局中验证**（无游戏运行环境，无法自动化）

## 实施结果

**状态：已完成并验证。** 提交 `34d6795`。82 项测试全部通过。

### 与计划的偏差

实施过程中发现计划本身有 **3 个真缺陷**，均已修正。这些缺陷说明「纸面评审」不足以替代实跑。

| # | 缺陷 | 后果 | 修正 |
|---|---|---|---|
| 1 | `zone_occupancy` 用 `card_frac < 0.20` 判空，但该值是在**外扩后**的检索区上算的 | 检索区从 73×79 外扩到 313×159 后，白色占比由 0.86 稀释到 0.09，**所有出牌区都会被判为空**，识别层拿不到任何数据 | 改为以「牌尺寸块」为占用判据，不再依赖占比阈值 |
| 2 | 检索区 `margin=120` 使相邻区互相覆盖 | 左右区中心仅隔 108px，外扩后重叠；「下家出两张」会把**上区也误判为有牌**——正是 `test_next_two_occupies_only_right` 要防的错误 | 放弃「区域内包含」判定，改为**把每个牌块就近分配给最近的区中心**，同时天然容纳甩牌溢出 |
| 3 | `arm_asymmetry_x` 是**永远为真的空检查** | 模型用单一 `arm_x` 推导左右区，左右臂**结构上恒对称**，无法构造出违反它的输入，该分支是死代码 | 移除，改为有意义的 `zones_too_close`（四区必须互相分得开） |

另修正：

- `pyproject.toml` 缺 `build-system`，导致 `uv run python -m shengji.tools.*` 报
  `No module named 'shengji'`。补 hatchling 配置并 `uv sync` 装为可编辑包
- 计划中 `record.py` 的 `info["rect"].clip(int(r_x := 0), ...)` 是无意义残留，已清理
- 计划中 `select_anchor` 保留的 H3（桌面颜色锚点）已作废，实现移除
- 计划中 `VOTE_FRAMES` 常量未使用，实现移除

### 实测标定出的常量

计划里有两处数值是**推断**的，实施时用实测数据替换：

| 常量 | 计划值 | 实测标定值 | 依据 |
|---|---|---|---|
| `CARD_BLOB_MIN_AREA` | 800 | **3000** | 真实牌块面积 3736~4954，而右侧静态 UI 元素 `(1042,282) 68×74` 面积仅 1836 且**宽高比同为 0.92**——只有面积能区分 |
| `ZONE_ASSIGN_MAX_DIST` | 无 | **60** | 实测归属距离最大约 11px；须小于相邻区中心间距（108px）的一半 |
| `felt_mask` 阈值用途 | 定位牌桌 | **仅辅助判空** | 实测牌桌与背景同色，无法定位 |

### 关于测试数量

计划预估约 50 项，实际 82 项。多出的部分主要是：

- 端到端四区定位按 4 种局面状态各写了独立断言（含"仅右区有牌"这条防跨区串味的回归）
- 锚定选择的多分支覆盖（空桌未验证、客户区偏移）
- 几何/配置的边界与容错（损坏 JSON、非法枚举值、形状不匹配）

## 已知未覆盖 / 后续计划

本计划只交付**采集、锚定与标定基础**。以下属于后续独立计划：

| 计划 | 内容 | 依赖 |
|---|---|---|
| Plan 2 | 识别层（点数 13 类 + 花色 4 类分类器、投票、置信度） | 本计划的采样工具产出 |
| Plan 3 | 事件层（区状态机、判稳、pHash 去重、环形缓冲、墩边界、待确认队列） | Plan 2 |
| Plan 4a | 牌局引擎：墩赢家规则（跟牌/对子/拖拉机/甩牌）+ 主牌次序（含级牌） | 无（纯逻辑，可并行） |
| Plan 4b | 牌局引擎：记账模型（`known` 互斥快照、`unseen` 多重集、`bottom_unknown`） | Plan 4a |
| Plan 5 | UI 悬浮窗（PySide6 穿透、全局热键、纠正面板） | Plan 3 |
| Plan 6 | 按家推断（功能 B）与 B1–B4 验收 | Plan 4b |

**尚未定位、属 Phase 0 现场标定任务**：底牌/扣底区、分数区、主牌指示区、各家剩余牌数区。
`src/shengji/tools/dump_layout.py` 与 `record.py` 已落地，可用它们在实际牌局的对应阶段
截图定位，再补进 `constants.py`。
