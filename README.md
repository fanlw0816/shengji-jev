# 升级记牌器（Shengji Card Counter）

在 Windows 上玩 QQ 游戏「升级」（拖拉机 / 双升 / 80 分）时，**自动记牌**：
实时统计剩余牌、按家推断手牌范围、追踪分数与墩次。

通过**屏幕截图 + 图像识别**获取牌局数据 —— 不读游戏内存、不注入进程、不修改游戏文件。

> ⚠️ **风险提示（未核实）**：读屏方案属最低风险等级，但**腾讯 ACE/TP 是否覆盖 QQ 游戏平台、
> 记牌器是否会被判定违规，本项目未能查到可靠资料**（调研时 `web_search` 工具不可用）。
> 已知同类工具（勇芳记牌器、我爱记牌器等）在中文互联网公开分发且持续更新。
> **投入使用前请自行评估风险。** 详见 [调研笔记](docs/research/2026-09-28-prior-art-and-risks.md)。

---

## 当前状态

| 阶段 | 内容 | 状态 |
|---|---|---|
| Plan 1 | 采集 · 锚定 · 标定基础 | ✅ **已实现并验证** |
| Plan 2 | 识别层（点数 13 类 + 花色 4 类、投票、置信度） | 🟡 **框架已实现并验证**；真实模板库待人工标注 |
| Plan 3 | 事件层（状态机 / 去重 / 墩边界 / 待确认队列） | ✅ **已实现并验证** |
| Plan 4a | 牌局引擎：主牌次序（含级牌）+ 墩赢家（单张/对子/拖拉机） | 🟡 **核心已实现并验证**；甩牌判定待补 |
| Plan 4b | 牌局引擎：记账模型（已知集合 / 未见牌池 / 未知底牌堆） | ✅ **已实现并验证** |
| Plan 5 | 悬浮窗 UI（PySide6） | 🟡 **显示/热键/穿透/推断行/异常行已完成**；交互式纠正面板未做 |
| Plan 6 | 按家推断（功能 B） | ✅ **已实现并验证**（引擎 31 项 + UI 接线断言全部执行通过） |

**当前测试：412 项**（`uv run pytest`）。

> 📌 **关于 numpy 版本上限**：numpy 自 **2.4.0** 起把官方 wheel 的编译基线抬到
> **x86-64-v2**（要求 SSE4.2 + POPCNT）。若在 KVM/VMware 等虚拟机里跑、且 hypervisor
> 暴露的是老 CPU 型号（如本机的 Core 2 Duo T7700），装 2.4+ 会 `import numpy` 直接
> `RuntimeError: ...baseline optimizations: (X86_V2) but your machine doesn't support`，
> 连带 `cv2` / `PySide6` 全部起不来。因此本项目锁 **`numpy>=2,<2.4`**（现为 2.3.5），
> opencv 5.0 只要求 `numpy>=2`，兼容。迁到较新 CPU 的机器后可放宽回 `numpy>=2`。

---

## 快速开始

需要 Python 3.13 与 [uv](https://docs.astral.sh/uv/)。

```bash
uv sync                 # 建虚拟环境并安装依赖（含可编辑安装本项目）
uv run pytest -q        # 跑测试（412 项，含 4 张真实截图的端到端验收）
```

### 运行记牌器

```bash
uv run python -m shengji.tools.run_counter --trump S2 --templates templates.json
```

热键：`Ctrl+Alt+L` 切换交互模式（关掉鼠标穿透）· `Ctrl+Alt+O` 强制重读 ·
`Ctrl+Alt+P` 暂停/恢复。

不给 `--trump` 时分牌无法判定，只显示张数；不给模板库时只报告张数不认牌 ——
两者都会在启动时明确提示，而不是装作能算。

### 工具

**导出布局标注图**（自动检测四区并画框，供人工核对）：

```bash
uv run python -m shengji.tools.dump_layout --all-fixtures
# 输出到 spike/shot_analysis/layout_*.png
```

**采样录制**（在真实牌局中自动切牌面切片存盘，供剪模板用）：

```bash
uv run python -m shengji.tools.record --seconds 600 --out samples
```

它用「变化触发」逻辑自动抓取，**不需要人工盯着那几秒的显示窗口**。

**建立识别模板库**（需要你给 17 个字形贴标签，约 5 分钟）：

```bash
uv run python -m shengji.tools.label_templates extract --samples samples --work templates_work
# 打开 templates_work/labels.json 给每个簇填 label，对照同目录的 cluster*.png
uv run python -m shengji.tools.label_templates build --work templates_work --out templates.json
```

模板库是识别层唯一需要人工介入的环节——本质上是"给 13 个点数 + 4 个花色字形起名字"，
无法自动化。

如果报「未找到游戏窗口」，列出当前窗口标题以确定关键字：

```bash
uv run python -c "from shengji.window.win32 import find_windows_by_title as f; [print(r.title, r.size) for r in f('')]"
```

然后改 `src/shengji/window/win32.py` 里的 `CANDIDATE_TITLE_KEYWORDS`。

---

## 工作原理

```
DXGI 抓屏 ──> 牌面块检测 ──> 就近归属到四区 ──> 状态机判稳
   (60Hz)                                        │ SETTLED
                                                 ▼
                                    切角标切片（每张牌左侧 16×28）
                                                 ▼
                              点数分类器(13) + 花色分类器(4)
                                                 ▼
                                  3 帧多数投票 + 置信度评估
                                                 ▼
                              PlayEvent(seat, cards, confidence)
                                                 ▼
                          牌局引擎：记账（未见池计数 / 不变式 I1·I2）
                                                 ▼
                       墩结束时回放出牌 → 各家的「空门」（哪个花色已经打光）
                                                 ▼
                    按家推断：可能持有（candidates）/ 必然持有（certain）
                                                 ▼
                                         悬浮窗刷新
```

### 识别层的关键事实

叠放时每张牌只露出左侧 **16px**，但**点数与花色的角标正好在这 16px 内完整可见**——
客户端把角标做得足够窄，正是为了叠放时仍可读。所以识别单元不是整张牌，
而是 `16×28` 的角标切片；角标内**点数在上、花色在下，中间有干净空隙**（中位 row=14），
可自动切分，因此只需 **13 + 4 = 17 次匹配**而非 54 次。

### 三个关键设计决定

**1. 变化驱动采样，不是定时截屏**

dxcam 底层是 Desktop Duplication API，屏幕无变化时 `grab()` **返回 `None`** 且仅耗
**0.038 ms**。操作系统免费提供了变化侦测，因此不需要"定时截图再比对"。
恒定 60 Hz 轮询（对齐面板刷新率）的成本仅 **0.25% 单核**。

**2. 布局是固定像素，不随窗口缩放**

实测确认**窗口尺寸不影响牌的长宽**，因此游戏按固定像素渲染。
定位采用 `ROI = 锚点 + 固定像素偏移`，偏移是常量，**不做等比缩放**。
牌宽（56×79）的角色是**校验常量**：一旦变化即说明 DPI 缩放被改或客户端改版，应告警。

**3. 用牌面块校验布局，不用颜色**

初稿曾把「桌面色」当作定位牌桌的锚点，**实测证明这是错的**：
牌桌周围的背景与桌面同色（阈值 90 时空区覆盖 100%，但全图覆盖 74%，
最大连通块 bbox 贴满画面）。颜色无法定位牌桌。

改用**牌尺寸块**（56×79、宽高比 0.709、面积 3700+ 的白色块）作为强特征，
判据为「**区内块数必须多于区外块数**」。

### 按家推断的关键取舍

读牌不是算概率，而是**排除**。三类约束参与求解：面值总数、各家容量、**空门**
（某家已打光某花色 —— 最强的一类信息，来自"有该花色必须跟"）。

- **只做逻辑排除，不做概率推断**：给范围不给概率（概率没有客观验收标准）
- **偏宽安全、偏窄危险**：少建模一点只会让范围偏宽，而偏宽仍不漏真值；
  所以不追求"最优解"，只保证"可靠且不平凡"
- **不平凡**：靠"把整池给每一家"蒙混是能被测出来的 ——
  验收要求同时满足「排除性」（可证在别家的牌不得出现）与「必然性」（被逼死的牌要认出来）
- **矛盾即报错**：面值无处可去、某家填不满、上下界互斥 —— 都是识别错误的强信号，
  直接抛错并在悬浮窗显示，绝不返回一个"看起来能用"的结果

---

## 实测数据

以下全部为本机实测（Windows 11 26200 / RTX 5060 Ti / 双 2560×1440 @60Hz），
测量脚本保留在 `spike/`。

| 项目 | 实测值 |
|---|---|
| dxcam 单次 `grab()`（无新帧） | **0.038 ms** |
| 热路径（抓帧 + N 区差分 + 判稳） | **0.042 ms/帧** |
| 跑满 60 FPS 的 CPU 成本 | ≈ **单核 0.25%** |
| dxcam 实际抓到的新帧率 | **46.7 FPS**（与 mss 地面真值 46.3 Hz 吻合，不丢帧） |
| mss 整屏 BitBlt（降级路径） | 17.7 ms ≈ **一个完整核心**（故降频到 15 Hz） |
| 单张牌外接框 | **56 × 79 px**，宽高比 **0.709**（标准扑克 0.714，误差 0.5%） |
| 两张牌叠放 | 宽 72 px → 叠放偏移 **16 px** |
| 模板匹配单次 | 0.145 ms |

### 四区布局（1280×720 参考坐标系）

出牌区是牌桌正中的**十字四宫格**（经 4 张不同局面截图交叉验证）：

| 区 | bbox | 归属 | 判定依据 |
|---|---|---|---|
| 上 | x522-594, y207-285 | 对家 | |
| 左 | x411-483, y283-361 | 上家 | |
| 右 | x627-699, y283-361 | **下家** | 「下家出两张」时只有右区有牌 |
| 下 | x521-593, y371-449 | **自己** | 「其他三家出一张」时下区为空 |

---

## 项目结构

```
src/shengji/
  constants.py      实测常量单一来源（每条数值都注明实测出处）
  cards.py          牌张模型（A=14、花色 0-3、王独立字段）
  imaging.py        中文路径安全 IO、颜色分割、牌尺寸块扫描、牌宽度量
  geometry.py       Rect 与「中心 + 臂长」四区推导
  layout/           model(锚点模式) · validate(几何+牌面块校验) · detect(占用检测/锚定选择)
  capture/          Protocol 抽象 + dxcam 主路径 + mss 降级 + 自动降级工厂
  window/win32.py   客户区定位 + Per-Monitor DPI 感知
  recognition/      patch(角标切片) · templates(字形模板库) · classify(投票+置信度)
  events/           phash(去重) · ringbuffer(回溯) · pending(待确认队列) · pipeline(状态机)
  engine/           trump(主牌次序) · trick(墩赢家) · accounting(记账) · inference(按家推断)
  session.py        会话状态（与 Qt 无关，纯单测覆盖）
  ui/               viewmodel(纯函数) · overlay(悬浮窗) · hotkeys · app(控制器)
  calib/store.py    标定 JSON 读写
  tools/            dump_layout(布局标注) · record(采样录制) · label_templates · run_counter

tests/              412 项测试
  fixtures/screenshots/   4 张真实截图（端到端验收的数据源）
spike/              实测脚本与性能证据（非产品代码）
docs/               设计文档 · 实现计划 · 调研笔记
```

---

## 已知限制

- **反作弊风险未核实**（见顶部提示）
- **遮挡**：Desktop Duplication 捕获整个显示器而非单个窗口，出牌区被别的窗口盖住会抓错。
  备选方案是 Windows Graphics Capture（可抓被遮挡窗口）
- **手牌区识别可行性未验证**：手牌 25 张紧贴重叠，每张仅露出约 13px 左边缘，
  该宽度内能否放下点数+花色角标尚未确认
- **以下 ROI 尚未定位**，属 Phase 0 现场标定任务：
  底牌/扣底区、分数区、主牌指示区、各家剩余牌数
- **甩牌最大张数未实测**，出牌区检索宽度上限待收紧
- **三副牌（6 人局）规则数据未验证**，引擎将对未验证变体拒绝启动

---

## 文档

| 文档 | 内容 |
|---|---|
| [设计文档](docs/superpowers/specs/2026-09-28-shengji-cardcounter-design.md) | 完整设计：架构、采样、牌局引擎、记账模型、校验、风险 |
| [Plan 1 实现计划](docs/superpowers/plans/2026-09-28-capture-and-layout.md) | 采集/锚定/标定的逐任务实现计划 |
| [Plan 3 事件层](docs/superpowers/plans/2026-09-28-events.md) | 状态机 / 去重 / 看门狗 / 待确认队列 |
| [Plan 5 UI 层](docs/superpowers/plans/2026-09-28-ui.md) | 悬浮窗 / 热键 / 鼠标穿透 |
| [Plan 6 按家推断](docs/superpowers/plans/2026-09-29-per-seat-inference.md) | 功能 B：上下界传播、空门推导、B1–B4 验收 |
| [调研笔记](docs/research/2026-09-28-prior-art-and-risks.md) | 同类工具、开源先例、规则依据、未核实项 |
| [文档索引](docs/README.md) | 全部文档与 spike 实测脚本对照表 |

---

## 开发约定

- **实测优先于推断**：设计中的每个常量都来自 `spike/` 下的实测脚本，
  并注明出处。改常量前先重跑脚本复核
- **失败要可见**：布局校验与记账不变式任一失败即告警，绝不静默继续算错
- **测试不需要游戏在跑**：端到端验收基于录制的截图/帧序列离线回放
