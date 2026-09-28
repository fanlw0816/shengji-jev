"""从采样目录提取角标、聚类、生成标注表，并由标注结果构建模板库。

用法：

    # 1) 从样本目录提取所有角标并聚类（每簇一个代表图）
    uv run python -m shengji.tools.label_templates extract --samples samples --out templates_work

    # 2) 打开 templates_work/sheet.png 与 labels.json，给每个簇填 rank / suit
    #    rank 取值：2..10 / J / Q / K / A / joker_small / joker_big
    #    suit 取值：S / H / D / C

    # 3) 由标注构建模板库
    uv run python -m shengji.tools.label_templates build --work templates_work --out templates.npz.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

from .. import constants as C
from ..imaging import imread_unicode, imwrite_unicode
from ..recognition.patch import split_patches
from ..recognition.templates import TemplateLibrary, glyph_feature, ink_mask

RANK_CHOICES = ["2", "3", "4", "5", "6", "7", "8", "9", "10",
                "J", "Q", "K", "A", "joker_small", "joker_big"]
SUIT_CHOICES = ["S", "H", "D", "C"]


def _iter_patch_files(samples: Path) -> list[Path]:
    exts = ("*.png", "*.jpg")
    files: list[Path] = []
    for e in exts:
        files.extend(sorted(samples.rglob(e)))
    return files


def _rank_and_suit_patches_from_crop(crop: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """把一张牌面切片切成点数片与花色片。

    采样工具存下的是牌周围带 5px 余量的裁切，因此这里先做「牌面白块」定位，
    再按角标几何切分。
    """
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    white = ((hsv[:, :, 2] > C.CARD_V_MIN) & (hsv[:, :, 1] < C.CARD_S_MAX)).astype(np.uint8)
    white = cv2.morphologyEx(white, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3)))
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(white, 8)
    if n <= 1:
        return np.zeros((1, 1, 3), np.uint8), np.zeros((1, 1, 3), np.uint8)
    big = max(range(1, n), key=lambda i: stats[i][4])
    x, y, w, h, _ = stats[big]
    corner = crop[y:y + C.CORNER_H, x:x + C.CARD_SLIVER_W]
    if corner.shape[:2] != (C.CORNER_H, C.CARD_SLIVER_W):
        return np.zeros((1, 1, 3), np.uint8), np.zeros((1, 1, 3), np.uint8)
    return split_patches(corner)


def _cluster(feats: list[np.ndarray], threshold: float) -> list[list[int]]:
    """单链接聚类。feats 已归一化，内积即相似度。"""
    n = len(feats)
    parent = list(range(n))

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for i in range(n):
        for j in range(i + 1, n):
            if float(np.dot(feats[i], feats[j])) > threshold:
                ri, rj = find(i), find(j)
                if ri != rj:
                    parent[rj] = ri
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return list(groups.values())


def cmd_extract(args: argparse.Namespace) -> int:
    samples = Path(args.samples)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)

    files = _iter_patch_files(samples)
    if not files:
        print(f"未找到样本图: {samples}", file=sys.stderr)
        return 2

    rank_patches: list[np.ndarray] = []
    suit_patches: list[np.ndarray] = []
    for p in files:
        img = imread_unicode(p)
        if img is None:
            continue
        rp, sp = _rank_and_suit_patches_from_crop(img)
        if rp.size > 3:
            rank_patches.append(rp)
        if sp.size > 3:
            suit_patches.append(sp)

    def build(patches: list[np.ndarray], prefix: str, threshold: float) -> dict:
        feats = []
        keep = []
        for i, pat in enumerate(patches):
            f = glyph_feature(pat)
            if f is None:
                continue
            feats.append(f)
            keep.append(i)
        groups = _cluster(feats, threshold)
        entries = []
        for gi, members in enumerate(sorted(groups, key=lambda g: -len(g))):
            # 取该簇中与簇内其它成员平均相似度最高的样本作代表
            best, best_s = members[0], -2.0
            for m in members:
                s = float(np.mean([np.dot(feats[m], feats[o]) for o in members]))
                if s > best_s:
                    best, best_s = m, s
            pat = patches[keep[best]]
            big = cv2.resize(pat, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
            fn = f"{prefix}_cluster{gi:02d}_n{len(members)}.png"
            imwrite_unicode(work / fn, big)
            entries.append({"cluster": gi, "size": len(members), "file": fn,
                            "label": ""})
        return {"entries": entries, "total_patches": len(keep)}

    print(f"样本图 {len(files)} 张；点数片 {len(rank_patches)}，花色片 {len(suit_patches)}")
    rank_res = build(rank_patches, "rank", args.rank_threshold)
    suit_res = build(suit_patches, "suit", args.suit_threshold)

    sheet = {
        "rank": {"choices": RANK_CHOICES, "clusters": rank_res["entries"],
                 "total_patches": rank_res["total_patches"]},
        "suit": {"choices": SUIT_CHOICES, "clusters": suit_res["entries"],
                 "total_patches": suit_res["total_patches"]},
    }
    (work / "labels.json").write_text(
        json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n点数簇 {len(rank_res['entries'])} 个，花色簇 {len(suit_res['entries'])} 个")
    print(f"  -> {work}/labels.json  请给每个簇填 label 字段")
    print(f"  -> {work}/rank_cluster*.png / suit_cluster*.png  为各簇代表图（8 倍放大）")
    print(f"\n  rank 可选值: {', '.join(RANK_CHOICES)}")
    print(f"  suit 可选值: {', '.join(SUIT_CHOICES)}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    work = Path(args.work)
    labels_path = work / "labels.json"
    if not labels_path.exists():
        print(f"缺少标注文件: {labels_path}，请先运行 extract", file=sys.stderr)
        return 2
    data = json.loads(labels_path.read_text(encoding="utf-8"))

    lib = TemplateLibrary()
    problems: list[str] = []
    for kind, adder, choices in (("rank", lib.add_rank, RANK_CHOICES),
                                 ("suit", lib.add_suit, SUIT_CHOICES)):
        for c in data.get(kind, {}).get("clusters", []):
            label = (c.get("label") or "").strip()
            if not label:
                continue
            if label not in choices:
                problems.append(f"{kind} 簇{c['cluster']} 标签非法: {label!r}")
                continue
            img = imread_unicode(work / c["file"])
            if img is None:
                problems.append(f"{kind} 簇{c['cluster']} 读不到代表图")
                continue
            # 代表图是 8 倍放大的，缩回原尺寸再提特征
            small = cv2.resize(img, None, fx=1 / 8.0, fy=1 / 8.0,
                               interpolation=cv2.INTER_AREA)
            if not adder(label, small):
                problems.append(f"{kind} 簇{c['cluster']} 提不出特征（可能无墨迹）")

    if problems:
        print("存在问题，未写出模板库：", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        return 3
    if not lib.ready:
        print("模板过少（点数与花色各需 ≥2 个），请补充标注", file=sys.stderr)
        return 4

    lib.save(args.out)
    print(f"模板库已写出: {args.out}")
    print(f"  点数模板 {len(lib.ranks)} 个: {sorted(lib.rank_labels())}")
    print(f"  花色模板 {len(lib.suits)} 个: {sorted(lib.suit_labels())}")
    missing_r = [r for r in RANK_CHOICES if r not in lib.rank_labels()]
    missing_s = [s for s in SUIT_CHOICES if s not in lib.suit_labels()]
    if missing_r:
        print(f"  ⚠ 尚缺点数: {missing_r}")
    if missing_s:
        print(f"  ⚠ 尚缺花色: {missing_s}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="模板库标注与构建")
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="提取角标并聚类，生成标注表")
    e.add_argument("--samples", default="samples")
    e.add_argument("--work", default="templates_work")
    e.add_argument("--rank-threshold", type=float, default=0.80)
    e.add_argument("--suit-threshold", type=float, default=0.90,
                   help="花色差异小，阈值应更高以免混簇")
    e.set_defaults(func=cmd_extract)

    b = sub.add_parser("build", help="由标注构建模板库")
    b.add_argument("--work", default="templates_work")
    b.add_argument("--out", default="templates.json")
    b.set_defaults(func=cmd_build)

    args = ap.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
