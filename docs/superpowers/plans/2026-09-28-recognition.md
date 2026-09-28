# 识别层 Implementation Plan（含实施记录）

> **状态：框架已实现并验证（134 项测试全绿）；真实模板库待人工标注。**
>
> 代码位置：`src/shengji/recognition/`、`src/shengji/tools/label_templates.py`

**Goal:** 从出牌区的画面切出每张牌的角标，识别出点数（13 类）与花色（4 类），
并对同一墩的多帧结果做投票与置信度评估。

**Architecture:** 四步流水线 —— `patch`（切角标）→ `templates`（字形特征与模板库）→
`classify`（分类/投票/置信度）→ `types`（载荷）。全部为纯函数，可用合成字形离线验证，
不需要游戏运行。

**Tech Stack:** Python 3.13, numpy, opencv-python

---

## 实测发现（决定整个识别方案的前提）

来自 `spike/analyze_cards.py` 与 `spike/analyze_cards2.py`。

### 1. 识别单元不是整张牌，而是「角标切片」

叠放时每张牌只露出左侧 **16px**（= `CARD_STACK_OFFSET`），但这 16px 里
**点数与花色的角标是完整的** —— 客户端把角标做得足够窄，正是为了叠放时仍可读。

由此：多张牌的外接框宽度 = `CARD_W + (n-1) × 16`，
第 i 张牌左边缘 = 外接框左边缘 + `i × 16`，识别切片 = 该位置起 `16 × 28`。

### 2. 点数与花色在角标内上下排列，中间有干净空隙

逐行墨迹剖面（13 张样本，11 张有干净空隙，中位切分线 **row=14**）：

```
第 0-13 行   3,1,2,3,4,5,4,5,4,7,8,4,4,4   ← 点数
第 14-16 行  0,0,0                          ← 空隙（切分线）
第 17-27 行  2,4,7,8,10,11,11,11,9,3,4      ← 花色
第 28-39 行  0,0,0,...                      ← 空白
```

因此可以自动切成 `CORNER_RANK_ROWS=(0,15)` 与 `CORNER_SUIT_ROWS=(16,28)`，
**13 + 4 = 17 次匹配**而非 54 次（成本模型见设计文档 §3.1）。

### 3. 未裁紧致外接框时特征无判别力

初版直接用原始角标像素做相关：**簇内最小相似度 0.756 < 簇间最大 0.828**
（区分间隔 −0.072，不可区分）。原因是字形在 16px 切片内的 ±1px 位置抖动
严重拉低相关性。

修正：先二值化墨迹 → 裁到**紧致外接框** → 缩放到 12×12 → L2 归一化。

### 4. 用整张牌面聚类也是无效的

角标墨迹只占整张牌的 3~6%，白底主导，任何两张牌的整牌相似度都 > 0.90。
必须用角标切片作为特征。

---

## 实施中发现并修正的设计缺陷

### ① 零均值归一化会让实心字形塌成零向量（真缺陷）

初版 `glyph_feature` 做零均值（Pearson 相关）。但**常值图像减掉均值就是全零向量**，
范数为 0，特征直接丢失。

触发场景：实心或近似实心的字形（例如一个实心方块，或模糊到只剩一团的小字形）
缩放到 12×12 后接近常值。

修正：改为**非负二值形状 + L2 归一化**，不做零均值。
代价是对亮度差异的稳健性略降，但二值化已经把亮度归一化掉了，实际无损失。

回归测试：`test_glyph_feature_works_for_solid_shape`。

### ② 测试字形的极性错误

初版测试辅助函数生成的是**黑底白字**，而真实扑克牌是**白底深字**。
于是 `ink_mask` 把背景当成墨迹，特征变成"带洞的矩形"——
精确副本还能分类，一加入位置抖动就崩（`2` 被识别成 `10`）。

修正测试辅助函数为白底深字，并在 docstring 中注明这一约束。
`ink_mask` 也顺手改成对单通道灰度输入容错。

### ③ `vote_frames` 用列表 `index()` 定位槽位

`per_slot.index(votes)` 会匹配到第一个相等的列表，槽位错位。
改为 `enumerate`。

---

## 模块职责

| 模块 | 做什么 | 依赖 |
|---|---|---|
| `cards.py` | `Card` 模型、编码约定（A=14、花色 0-3、王独立字段）、`code()`/`parse_code()` | 无 |
| `recognition/patch.py` | 由画面切出各区每张牌的角标切片，切成点数片与花色片 | layout, constants |
| `recognition/templates.py` | 字形特征、模板库、分类、JSON 持久化 | constants |
| `recognition/classify.py` | 单帧识别、逐张多数投票、置信度、不确定性判定 | patch, templates, cards |
| `recognition/types.py` | `CardRead` / `ZoneRead` / `RecognitionResult` | cards |
| `tools/label_templates.py` | 从采样目录提取角标、聚类、生成标注表、由标注建库 | patch, templates |

## 置信度语义

```
confidence      = 每张牌「最佳与次佳模板分之差」的最小值
frame_agreement = 跨帧投票一致率
两者独立，任一低于阈值即触发人工纠正（设计文档 §5.1 的合取式触发）
```

投票平票时返回 `None`（宁可不认，也不猜）。

---

## 尚未完成：真实模板库

**识别框架已全部就绪，但真实模板库需要人工给角标字形起名。**
本质上这是"给 13 个点数字形 + 4 个花色图形贴标签"，无法自动化。

### 使用流程

```bash
# 1) 先在真实牌局中录样本（Plan 1 的采样工具）
uv run python -m shengji.tools.record --seconds 600 --out samples

# 2) 提取角标并聚类，每簇输出一张 8 倍放大的代表图
uv run python -m shengji.tools.label_templates extract --samples samples --work templates_work

# 3) 打开 templates_work/labels.json，给每个簇填 label 字段
#    rank 取值：2..10 / J / Q / K / A / joker_small / joker_big
#    suit 取值：S / H / D / C   （对着同目录的 rank_cluster*.png / suit_cluster*.png 看）

# 4) 由标注构建模板库
uv run python -m shengji.tools.label_templates build --work templates_work --out templates.json
```

第 4 步会报告还缺哪些点数/花色，便于分批补齐。

### 当前只有部分字形可用

现有 4 张截图只能抽出 13 张牌，覆盖不了 54 类。
必须靠真实对局录制样本补齐。

---

## 测试覆盖（35 项）

| 文件 | 项数 | 覆盖 |
|---|---|---|
| `test_cards.py` | 9 | 牌模型、A>K 次序、编解码往返、非法值拒绝、可哈希可排序 |
| `test_patch.py` | 9 | 真实截图上 13 张切片的结构、尺寸、16px 偏移、不跨区、切分空隙 |
| `test_templates.py` | 16 | 墨迹判定、特征归一化、平移不变性、实心形状、13+4 全类还原、抖动鲁棒性、持久化容错 |
| `test_classify.py` | 11 | 单张识别、多数投票（含平票）、跨帧合并、两阈值不确定性判定、真实截图的槽位数 |

**合成字形测机械，真实截图测结构** —— 前者验证"给定已知字形能否正确还原"，
后者验证"切片位置与尺寸是否与实测几何一致"。两者都不需要游戏在跑。

---

## 后续依赖

- Plan 3（事件层）：依赖本层的 `RecognitionResult` 与 `is_uncertain`
- Plan 6（功能 B）：依赖模板库就绪后的稳定识别
