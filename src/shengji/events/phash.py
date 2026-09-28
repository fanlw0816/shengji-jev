"""感知哈希：用于同墩去重（设计文档 §4.3）。

用途：同一墩牌在屏幕上停留数秒，期间可能多次达到"判稳"。
若不按内容去重，同一手牌会被反复计入，从机制上破坏统计。

采用 dHash（差分哈希）：对缩放后的灰度图比较相邻像素大小，
比 aHash（均值哈希）对亮度渐变更稳健，且实现简单、无依赖。
"""

from __future__ import annotations

import cv2
import numpy as np

DEFAULT_HASH_SIZE = 8       # 8x8 -> 64 bit


def dhash(gray: np.ndarray, hash_size: int = DEFAULT_HASH_SIZE) -> int:
    """差分哈希。输入灰度图，输出整数（hash_size^2 位）。"""
    if gray.ndim != 2:
        raise ValueError("dhash 需要单通道灰度图")
    if gray.size == 0:
        raise ValueError("dhash 不接受空图")
    # 缩放到 (hash_size+1) x hash_size，再横向比较相邻像素
    small = cv2.resize(gray, (hash_size + 1, hash_size),
                       interpolation=cv2.INTER_AREA)
    diff = small[:, 1:] > small[:, :-1]
    bits = 0
    for v in diff.flatten():
        bits = (bits << 1) | int(v)
    return bits


def hamming(a: int, b: int) -> int:
    """两个哈希的汉明距离。"""
    return bin(a ^ b).count("1")


def same_content(a: int, b: int, tolerance: int = 0) -> bool:
    """判断两个哈希是否代表同一内容。

    tolerance 用于吸收 JPEG 压缩与抗锯齿带来的极小差异。
    默认 0（严格相等）—— 因为"宁可当成新的一手去识别"，也不要漏掉一手。
    """
    return hamming(a, b) <= tolerance
