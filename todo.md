# 任务进度

> **本文件是项目的进度看板，每完成一项就更新它。**
> 状态标记：✅ 已完成并验证 ｜ 🟡 部分完成 ｜ ⬜ 未开始 ｜ ⛔ 被阻塞
>
> 最后更新：2026-09-28

---

## 总体状态

| 阶段 | 内容 | 状态 | 验证方式 |
|---|---|---|---|
| Plan 1 | 采集 · 锚定 · 标定基础 | ✅ | 82 项测试，含 4 张真实截图端到端验收 |
| Plan 2 | 识别层（点数 13 + 花色 4 + 投票 + 置信度） | 🟡 | 框架 35 项测试通过；真实模板库待标注 |
| Plan 3 | 事件层（状态机 / 去重 / 墩边界 / 待确认队列） | ✅ | 54 项测试，含真实截图离线回放 |
| Plan 4a | 牌局引擎：主牌次序 + 墩赢家 | 🟡 | 51 项测试通过；甩牌判定未实现 |
| Plan 4b | 牌局引擎：记账模型 | ✅ | 25 项测试通过；三类重复扣减缺陷均有回归测试 |
| Plan 5 | 悬浮窗 UI（PySide6） | 🟡 | 显示/热键/穿透已完成；**交互式纠正面板未做** |
| Plan 6 | 按家推断（功能 B） | ⬜ | — |

**当前测试：364 项全部通过。**

```bash
uv sync
uv run pytest                       # 364 passed
```

---

## Plan 1 — 采集 · 锚定 · 标定基础 ✅

- [x] uv 项目骨架 + pytest 配置 + 可编辑安装（hatchling）
- [x] `constants.py` 实测常量单一来源（每条注明实测出处）
- [x] `imaging.py` 中文路径安全 IO、桌面色/牌面分割、牌尺寸块扫描、牌宽度量
- [x] `geometry.py` Rect 与「中心 + 臂长」四区推导
- [x] `layout/model.py` 布局模型（锚点模式 + 固定像素偏移）
- [x] `layout/validate.py` 几何不变量 + 牌面块一致性校验
- [x] `layout/detect.py` 四区占用检测（就近归属）+ 锚定选择
- [x] `capture/` Protocol 抽象 + dxcam 主路径 + mss 降级 + 自动降级工厂
- [x] `window/win32.py` 客户区定位 + Per-Monitor DPI 感知
- [x] `calib/store.py` 标定 JSON 读写（损坏返回 None 而非抛异常）
- [x] `tools/dump_layout.py` 布局标注导出
- [x] `tools/record.py` 采样录制（变化触发自动切片）
- [x] 测试夹具：4 张真实 1280×720 截图
- [x] 端到端验收：4 种局面状态的占用检测逐条吻合

**实施中发现并修正的计划缺陷**（详见 [计划文末「实施结果」](docs/superpowers/plans/2026-09-28-capture-and-layout.md)）：

1. `zone_occupancy` 的 `card_frac` 阈值被检索区膨胀稀释（0.86 → 0.09），所有区会被判为空
2. 检索区 margin=120 让相邻区互相覆盖（左右区中心仅隔 108px），"下家出两张"会误判上区
3. `arm_asymmetry_x` 是永远为真的空检查（模型结构上恒对称，无法构造违反输入）

---

## Plan 2 — 识别层 🟡

- [x] `cards.py` Card 模型与编码约定（A=14、花色 0-3、王独立字段）
- [x] `recognition/patch.py` 角标切片提取（每张牌左侧 16×28），切点数/花色片
- [x] `recognition/templates.py` 字形特征、模板库、分类、JSON 持久化
- [x] `recognition/classify.py` 单帧识别、多数投票、置信度、不确定性判定
- [x] `recognition/types.py` 载荷类型
- [x] `tools/label_templates.py` 聚类 → 人工标注 → 建库
- [x] 合成字形测机械部分（13 + 4 全类还原、平移抖动、实心形状）
- [x] 真实截图测结构部分（切片数量/尺寸/16px 偏移/切分空隙）
- [ ] **真实模板库**（需要人工标注，见下）

### ⛔ 阻塞项：真实模板库

识别框架已就绪，但真实模板库需要**人工给 17 个字形贴标签**（13 点数 + 4 花色），
无法自动化。现有 4 张截图只能抽出 13 张牌，覆盖不了 54 类。

```bash
# 1) 真实牌局中录样本
uv run python -m shengji.tools.record --seconds 600 --out samples
# 2) 提取角标并聚类，每簇输出 8 倍放大代表图
uv run python -m shengji.tools.label_templates extract --samples samples --work templates_work
# 3) 打开 templates_work/labels.json 给每个簇填 label（对着 cluster*.png 看）
# 4) 建库
uv run python -m shengji.tools.label_templates build --work templates_work --out templates.json
```

**实施中发现并修正的缺陷**（详见 [识别层文档](docs/superpowers/plans/2026-09-28-recognition.md)）：

1. **零均值归一化让实心字形塌成零向量**（真缺陷）→ 改为非负二值形状 + L2 归一化
2. 测试字形极性搞反（黑底白字 vs 真实的白底深字）→ 特征颠倒
3. `vote_frames` 用列表 `index()` 定位槽位会错位 → 改为 `enumerate`

---

## Plan 4a — 牌局引擎：主牌次序与墩赢家 🟡

- [x] `engine/trump.py` 主牌判定、牌力次序（**含正级/副级**）、比较、排序、主牌集合枚举
- [x] `engine/trick.py` 结构判定（单张/对子/拖拉机/甩牌）、墩赢家、分牌点数
- [x] 单张 / 对子 / 拖拉机 的赢家判定
- [x] 主牌压副牌；强弱相等先出者赢；非领出花色且非主牌者不能赢
- [x] 无主局（副牌只比点数不比花色）
- [ ] **甩牌（一次出多张杂牌）的赢家判定** —— 见下

### 🟡 未实现：甩牌的赢家判定

完整规则需判断甩牌合法性（是否均为该花色最大）与各家跟牌的结构匹配，
规则复杂且地区差异大。当前实现**保守地让领出方赢，但返回 `confident=False`**，
交上层走人工确认流程 —— 与设计原则「宁可让你点一下，也不静默算错」一致。

### ⛔ 待实测确认：副级三门是否可互压

设计文档 §7.6.3。默认实现为「同级、不可互压」。
该差异**直接影响墩赢家与分牌归属**，必须用实际客户端对局验证。

### 实施中修正的缺陷

`all_trumps` 只枚举了主花色的级牌，漏掉另外三门 —— 两副牌下**少算 6 张主牌**，
会直接导致记牌统计出错。已补两条回归测试。

---

## Plan 4b — 牌局引擎：记账模型 ✅

- [x] `engine/accounting.py` 变体规则表、已知集合、未见牌池、未知底牌堆
- [x] 不变式 I1（`unseen ≥ 0`）与 I2（总账平衡）作为识别错误探测器
- [x] 未验证变体拒绝启动（`(6,3)` 抛错而非猜测）
- [x] 三类重复扣减缺陷全部有回归测试

| 缺陷 | 后果 | 回归测试 |
|---|---|---|
| 快照时点混用（扣底后的 H + 扣底前的 P 有交集） | 重复扣减，未见池算错 | `test_declarer_known_set_rejects_overlap` |
| `played ∩ known`（自己打出的牌已在 known 中） | 未见池变负数 | `test_own_play_does_not_decrement_unseen` |
| 底牌不属于任何一家 | 计数约束把底牌派给某家 | `test_bottom_unknown_is_reserved_so_invariants_hold` |

---

## Plan 3 — 事件层 ✅

- [x] `events/types.py` `ZoneState` / `FrameSnapshot` / `PlayEvent` / `TrickEndEvent` / `PendingItem`
- [x] `events/pipeline.py` `ZoneTracker` 区域状态机（`EMPTY` / `ENTERING` / `SETTLED`）
- [x] 判稳：连续 8 帧差异 < 阈值；**首帧永不算稳定**（否则动画中间态会被当摆定）
- [x] `events/phash.py` dHash 去重，**清空时重置周期**（否则下一墩同样的牌会被漏计）
- [x] `events/ringbuffer.py` 环形缓冲 + 时间窗回溯，供低置信时重算
- [x] 墩边界：全部 N 区回到 `EMPTY` 且此前有过占用
- [x] 看门狗：一次占用周期内出现第二种内容 → `missed_play`
- [x] `events/pending.py` 待确认队列：按 `frame_ts` 有序、**入队即落盘证据**、缺图仍入队
- [x] 低置信两个阈值独立、合取式触发（`confidence` / `frame_agreement`）
- [x] 未确认的出牌**不进入本墩确定记录**，但事件本身必须流出
- [x] 离线回放：用 4 张真实截图断言事件序列与墩边界

详见 [事件层文档](docs/superpowers/plans/2026-09-28-events.md)。

**三道防线**（都对应设计文档 §9.1「绝不静默丢牌」）：

| 防线 | 防的是什么 |
|---|---|
| pHash 去重 | 同一手牌被反复计入 |
| 看门狗 `missed_play` | 一手牌被下一手覆盖、来不及读取 |
| 待确认队列 | 低置信事件因纠正耗时超过缓冲时长而消失 |

---

## Plan 5 — 悬浮窗 UI 🟡

- [x] `session.py` 会话状态（事件 → 可显示状态，与 Qt 无关，纯单测覆盖）
- [x] `ui/viewmodel.py` 视图模型（状态 → 渲染数据，纯函数）
- [x] `ui/overlay.py` PySide6 无边框置顶小窗，**默认鼠标穿透**
- [x] 剩余牌统计面板（4 花色 × 13 点数 + 大小王）
- [x] 墩次 / 分数面板（各家分数）
- [x] 状态行分三级：运行中 / 降级或待确认（warn）/ 无采集后端（error）
- [x] `ui/hotkeys.py` 全局热键：`Ctrl+Alt+L` 交互、`Ctrl+Alt+O` 强制重读、`Ctrl+Alt+P` 暂停
- [x] `ui/app.py` 应用控制器（`tick_once()` 可注入假后端做确定性测试）
- [x] `tools/run_counter.py` CLI 入口
- [x] 降级模式提示（mss → 15Hz、无后端 → error）
- [x] 待确认计数显示（低置信 / 漏抓分开计）
- [ ] **交互式纠正面板**：目前只显示"有 N 项待确认"，
      还不能在悬浮窗里点选改正某一张牌
- [ ] 各家推断范围面板（属 Plan 6 的功能 B，UI 侧未做）

详见 [UI 层文档](docs/superpowers/plans/2026-09-28-ui.md)。

**实施中修正的 Qt 缺陷**：

`setAttribute(WA_TransparentForMouseEvents, ...)` 会把
`WindowTransparentForInput` 标志**重新加回来**，
所以必须**先设属性、后设窗口标志**。原顺序反了会导致
**鼠标穿透永远关不掉**，纠正流程也就永远点不到。
已在 `overlay.apply_interactive` 注释中记录，并有测试覆盖。

---

## Plan 6 — 按家推断（功能 B）⬜

- [ ] `per_seat_candidates`（最紧可靠超集）
- [ ] `per_seat_certain`（必然持有）
- [ ] 验收断言 B1–B4（防平凡满足）：

| # | 断言 | 防的是什么 |
|---|---|---|
| B1 | `ground_truth[seat] ⊆ candidates[seat]` | soundness |
| B2 | `certain[seat] ⊆ ground_truth[seat]` | certain 的 soundness |
| B3 | 可证必然在别家的牌不得出现在本家 candidates | **平凡全集解** |
| B4 | 构造场景中存在 `certain[seat] ≠ ∅` | **恒返回空集解** |

- [ ] 紧致度回归（防止实现悄悄退化为返回全集）

---

## Phase 0 现场标定任务（需在真实牌局中完成）

这些无法离线完成，必须在真实对局中截图定位：

- [ ] 底牌/扣底区 ROI
- [ ] 分数区 ROI
- [ ] 主牌指示区 ROI（`level_rank` 识别）
- [ ] 各家剩余牌数区 ROI
- [ ] `CANDIDATE_TITLE_KEYWORDS` 实测确认（现为猜测值）
- [ ] 甩牌最大张数实测（决定出牌区检索宽度上限）
- [ ] 「看底牌」期间手牌区是否变形（若临时并入手牌，标定 ROI 会误读）
- [ ] 「全部 N 区同帧清空」假设验证（若为淡出/逐区清空，`trick_index` 会被污染）

---

## 已知风险与限制

| 项 | 状态 |
|---|---|
| **反作弊 / 封号风险** | ⚠️ **未能核实**。读屏方案不修改进程，风险等级最低，但腾讯 ACE/TP 覆盖情况未查到可靠资料。详见 [调研笔记](docs/research/2026-09-28-prior-art-and-risks.md) |
| ROI 被遮挡 | Desktop Duplication 捕获整个显示器；备选 WGC |
| 手牌区识别可行性 | 25 张紧贴重叠，每张仅露约 13px，角标能否容纳未验证 |
| 三副牌规则数据 | `(6,3)` 标注 `UNVERIFIED`，引擎拒绝启动 |
| 副级三门比较规则 | 待实测（§7.6.3） |

---

## 文档索引

| 文档 | 内容 |
|---|---|
| [README](README.md) | 项目总览、快速开始、工作原理、实测数据 |
| [设计文档](docs/superpowers/specs/2026-09-28-shengji-cardcounter-design.md) | 完整设计（940 行） |
| [Plan 1 计划 + 实施结果](docs/superpowers/plans/2026-09-28-capture-and-layout.md) | 采集/锚定/标定 |
| [Plan 2 识别层](docs/superpowers/plans/2026-09-28-recognition.md) | 识别层 + 模板标注流程 |
| [Plan 3 事件层](docs/superpowers/plans/2026-09-28-events.md) | 状态机 / 去重 / 看门狗 / 待确认队列 |
| [Plan 5 UI 层](docs/superpowers/plans/2026-09-28-ui.md) | 悬浮窗 / 热键 / 鼠标穿透 |
| [调研笔记](docs/research/2026-09-28-prior-art-and-risks.md) | 同类工具、开源先例、未核实项 |
| [文档索引](docs/README.md) | 全部文档与 spike 实测脚本对照表 |

---

## 进度日志

| 日期 | 内容 |
|---|---|
| 2026-09-28 | uv 项目初始化；抓屏实测（dxcam 46.7 FPS 不丢帧）；设计文档 5 稿、经 3 轮评审 |
| 2026-09-28 | 分析 4 张真实截图，修正 ROI 假设与定位模型（推翻「桌面色锚点」） |
| 2026-09-28 | Plan 1 落地，82 项测试 |
| 2026-09-28 | Plan 2 识别层框架，134 项测试 |
| 2026-09-28 | Plan 4a 引擎核心，185 项测试 |
| 2026-09-28 | Plan 4b 记账模型，210 项测试；关联远程仓库 |
| 2026-09-28 | Plan 3 事件层（状态机/去重/看门狗/待确认队列），264 项测试 |
| 2026-09-28 | Plan 5 UI 层（会话状态/视图模型/悬浮窗/全局热键/CLI），364 项测试 |
