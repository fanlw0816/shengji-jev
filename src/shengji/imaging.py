"""图像 I/O 与颜色分割。

所有函数为纯函数，不持有状态，便于用真实截图做确定性测试。
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from . import constants as C


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


def felt_mask(img: np.ndarray, dist_max: int = C.FELT_DIST_MAX) -> np.ndarray:
    """桌面色掩码：到实测桌面色均值的欧氏距离小于阈值。

    ⚠️ 不能用于定位牌桌 —— 实测牌桌与背景同色（见 constants 注释）。
    仅用于辅助判断区域是否为空白桌面。

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


def card_sized_blobs(img: np.ndarray,
                     min_area: int | None = None) -> list[dict]:
    """扫出符合「牌尺寸窗口」的白色块，按面积降序返回。

    这是布局校验与出牌区占用判定的共同基础：牌是 56x79、宽高比 0.709、
    面积 3700+ 的白色块，与桌面/背景/手牌带的区分度都很高。

    面积下限是关键：实测 1 张牌块约 3736px，而右侧静态 UI 元素
    (1042,282) 68x74 面积仅 1836 且宽高比同为 0.92 —— 只有面积能区分。
    """
    ma = C.CARD_BLOB_MIN_AREA if min_area is None else min_area
    m = card_mask(img).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                         cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    n, _lab, stats, _cent = cv2.connectedComponentsWithStats(m, 8)
    out: list[dict] = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < ma:
            continue
        if not (C.CARD_BLOB_MIN_W <= w <= C.CARD_BLOB_MAX_W):
            continue
        if not (C.CARD_BLOB_MIN_H <= h <= C.CARD_BLOB_MAX_H):
            continue
        out.append({"x": int(x), "y": int(y), "w": int(w), "h": int(h),
                    "area": int(area)})
    out.sort(key=lambda b: -b["area"])
    return out


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
    因此取「观测宽度减去若干倍叠放偏移后最接近 CARD_W」的值作为单张宽度估计。

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
