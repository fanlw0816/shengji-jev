# 调研笔记：现成工具、规则依据与风险

- **日期**：2026-09-28
- **状态**：**不完整**。本会话的 `web_search` 工具因 API key 失效不可用（HTTP 401），
  以下内容是调研子代理改用 `web_fetch` 直接抓取 Bing / GitHub API / 官方文档得到的**部分结果**。
  未能覆盖的问题已明确标注。

---

## 1. 同类商业工具（公开分发）

搜索结果中出现的现成 QQ 游戏记牌器：

| 工具 | 说明（来自搜索摘要） | 来源 |
|---|---|---|
| 勇芳记牌器 | 支持 24 种常见牌类游戏的"全自动记牌辅助软件"，集记牌、算牌、剩牌显示于一体；页面日期 2025-09-16 | soft.china.com |
| QQ升级记牌器（草垫） | "由玩家亲自针对 QQ 升级而编写，极适于 QQ 多副牌升级（80 分拖拉机）"；页面日期为抓取时 6 天前 | crsky.com |
| 我爱记牌器（QQ游戏记牌器） | "专为 QQ 游戏大厅内的棋牌游戏编写" | crsky.com |

**意义**：这类工具在中文软件下载站是**公开、成熟、且持续更新**的品类。
这不构成"不会被判违规"的证据，但说明该技术路线在现实中长期存在。

**未核实**：这些工具是否曾导致封号、是否被腾讯主动清理 —— 未找到可靠资料。

---

## 2. 开源项目（建议实现前阅读）

| 项目 | 线索 | 备注 |
|---|---|---|
| `gooooloo/QQCardRecorder` | 2015 年创建，master 分支，7 forks / 24 watchers | QQ 记牌器。README 抓取失败（返回 48 字节），内容待查 |
| `evanjliu/chinese-tractor-card-game-tracker-` | 2024 年创建（repo id 776280379） | 中文拖拉机牌局追踪，与本项目目标最接近 |
| `rbtying/shengji` | 开源升级实现，规则站 `shengji.us/rules` | **本设计的规则依据来源** |
| `mxw/zpy` | 2021 年创建（repo id 269389941） | 中文拖拉机牌类项目，细节待查 |

GitHub API 检索结果规模：`shengji tractor` → 8 个仓库；`tractor card game chinese` → 10 个仓库。

**未核实**：以上项目的具体实现方式（是读屏、读内存还是纯规则引擎），需 clone 后阅读。

---

## 3. 升级规则要点（用于引擎参数化）

来源：[shengji.us/rules](http://shengji.us/rules)（`rbtying/shengji` 的规则文档）

- 每副牌 **54 张** = 4 花色 × 13 点数 + 小王 + 大王
- **A 大于 K**
- **每两名玩家增加一副牌**：4 人用 2 副（108 张），5 人用 2 副，6 人用 3 副（162 张）
- **分牌**：K、10、5 为有分值的牌
- 别名：拖拉机、四十分、八十分、打百分、找朋友

**对设计的影响**：引擎必须由 `(decks, players)` 参数化驱动。
用户要求支持"两副 + 三副"，对应 **4 人局与 6 人局**，不是同一人数下的两种牌数。

**未核实**：三副牌（6 人）局中每人手牌数与底牌张数 —— 各地规则有差异，
必须接入真实游戏后按实际 UI 校准确认，**不可凭推断写死**。

---

## 4. 抓屏技术参考

- [Desktop Duplication API](https://learn.microsoft.com/en-us/windows/win32/direct3ddxgi/desktop-dup-api) — dxcam 底层机制，本项目主采集路径
- [Screen capture (Windows Graphics Capture)](https://learn.microsoft.com/en-us/windows/uwp/audio-video-camera/screen-capture) — 可抓被遮挡窗口，备选后端
- [PrintWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow) — 传统窗口抓取，对 GPU 渲染窗口可能黑屏
- [GraphicsCaptureSession.IsBorderRequired](https://learn.microsoft.com/en-us/windows/uwp/api/windows.graphics.capture.graphicscapturesession.isborderrequired) — WGC 边框行为

Bing 检索确认存在"BitBlt 黑屏 / PrintWindow + PW_RENDERFULLCONTENT 对 DirectX 窗口黑屏"的
大量讨论，与 §4 选型（用 Desktop Duplication 而非 BitBlt）一致。

---

## 5. 未解决的问题

以下是本次调研**未能回答**的问题，因 web_search 不可用：

1. **QQ 游戏「升级」客户端的当前技术形态** —— 是否仍基于 Flash？Flash 2020 年停服后是否迁移到自研/H5 引擎？
   检索到 Flash 停服与迁移相关的搜索页，但未提取到权威结论。
2. **腾讯 ACE / TP 是否覆盖 QQ 游戏平台**，以及其对读屏类工具的态度与检测手段。
3. 上述开源项目的具体实现方式。

**建议**：在进入 Phase 1 前，用可用的搜索工具补齐第 1、2 项；
第 3 项直接 clone 阅读代码即可。
