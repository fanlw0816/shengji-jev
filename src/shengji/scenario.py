"""离线事件语料：把「事件序列 → 期望终态」写成 JSON，逐个回放断言。

**为什么需要它。** 端到端验收目前只有 4 张真实截图，覆盖面被截图数量卡住。
而 Plan 5 之后状态已经是**事件序列的函数**（`replay.py`），
于是「造局面」不再需要截图：直接写事件序列就能覆盖真实截图里凑不出来的边界 ——
连续多墩、补录插到历史墩、缺一手的墩、甩牌墩、6 人局、手牌未标定……

语料同时是**将来挂真机样本的接口**：Phase 0 标定完成后，
把真实对局导成同一格式即可直接进来回归。

---

格式（文件见 `tests/fixtures/scenarios/*.json`）：

```json
{
  "name": "两墩记分",
  "description": "两墩都用红桃对子，验证赢家按座位号归属",
  "players": 4, "decks": 2, "trump": "S2",
  "own_hand": ["SA", "SA", "SK"],
  "zone_to_seat": {"bottom": 0, "right": 1, "top": 2, "left": 3},
  "events": [
    {"type": "play", "zone": "bottom", "cards": ["H5"], "trick": 0, "ts": 1.0},
    {"type": "play", "zone": "right", "cards": ["H10"], "trick": 0, "ts": 1.1},
    {"type": "trick_end", "trick": 0, "ts": 1.4, "plays": ["bottom", "right"]}
  ],
  "overrides": {"0": [{"zone": "bottom", "cards": ["H5"], "ts": 1.0}]},
  "expect": {"trick_index": 1, "points": {"right": 15}, "errors_empty": true}
}
```

约定：

- `cards` 用 `Card.code()` 的 ASCII 编码（`SA` / `H10` / `joker_small` / `joker_big`）
- `cards: null`（或省略 `cards` 只给 `count`）表示**未识别** —— 用来造低置信 / 缺一手的场面
- `trick_end.plays` 给出**出牌顺序**的区名列表；省略则按该墩 play 事件的先后推出
- `overrides` 是墩级补录（对应 `replay.py` 的 `trick_overrides`），键为墩号字符串
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .cards import Card, parse_code
from .engine.accounting import get_variant_rule
from .engine.trump import TrumpInfo, parse_trump
from .events.types import Event, PlayEvent, TrickEndEvent
from .replay import DEFAULT_SEATS, CounterState, ReplayContext, replay_events

EXPECT_KEYS = frozenset({
    "trick_index", "history_len", "history", "points", "total_points",
    "unscored_tricks", "errors", "errors_empty", "unseen", "unseen_total",
    "own_hand_remaining", "voids",
})
"""`expect` 允许的键。**未知键直接报错** —— 拼错的键会让断言恒真，那是静默失效。"""


class ScenarioError(Exception):
    """语料文件格式错误 —— 必须显式失败，不能跳过。"""


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    ctx: ReplayContext
    events: tuple[Event, ...]
    overrides: Mapping[int, tuple[PlayEvent, ...]]
    expect: Mapping[str, Any]
    path: Path | None = None

    def run(self) -> tuple[CounterState, list[str]]:
        """按事件序列回放，返回 (终态, 错误消息列表)。"""
        return replay_events(self.events, self.ctx, trick_overrides=self.overrides)


# ---------- 加载 ----------


def load_scenario(path: str | Path) -> Scenario:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScenarioError(f"语料 {path} 无法读取：{exc}") from exc

    players = int(data.get("players", 4))
    decks = int(data.get("decks", 2))
    rule = get_variant_rule(players, decks)

    trump_spec = data.get("trump")
    trump: TrumpInfo | None = parse_trump(trump_spec) if trump_spec else None

    own_hand_spec = data.get("own_hand")
    own_hand = tuple(_cards(own_hand_spec)) if own_hand_spec else None

    ctx = ReplayContext(
        rule=rule,
        seats=tuple(data.get("seats", DEFAULT_SEATS)),
        trump=trump,
        own_seat=int(data.get("own_seat", 0)),
        own_hand=own_hand,
        zone_to_seat={k: int(v) for k, v in data.get("zone_to_seat", {}).items()},
    )

    overrides: dict[int, tuple[PlayEvent, ...]] = {}
    for key, specs in data.get("overrides", {}).items():
        idx = int(key)
        overrides[idx] = tuple(
            _play_event(s, i, default_trick=idx) for i, s in enumerate(specs))

    expect = data.get("expect", {})
    unknown = set(expect) - EXPECT_KEYS
    if unknown:
        raise ScenarioError(
            f"语料 {path} 的 expect 含未知键 {sorted(unknown)}；"
            f"可用键：{sorted(EXPECT_KEYS)}")

    return Scenario(
        name=str(data.get("name", path.stem)),
        description=str(data.get("description", "")),
        ctx=ctx,
        events=tuple(_build_events(data.get("events", []))),
        overrides=overrides,
        expect=expect,
        path=path,
    )


def load_dir(directory: str | Path) -> list[Scenario]:
    """加载目录下全部语料，按文件名排序（保证顺序稳定）。"""
    paths = sorted(Path(directory).glob("*.json"))
    if not paths:
        raise ScenarioError(
            f"{directory} 下没有任何语料文件 —— 空语料库会被误当成「全过」")
    return [load_scenario(p) for p in paths]


def _build_events(specs: Sequence[Mapping[str, Any]]) -> list[Event]:
    events: list[Event] = []
    by_trick: dict[int, list[PlayEvent]] = {}

    for i, spec in enumerate(specs):
        kind = spec.get("type", "play")
        if kind == "play":
            ev = _play_event(spec, i)
            events.append(ev)
            by_trick.setdefault(ev.trick_index, []).append(ev)
        elif kind == "trick_end":
            idx = int(spec["trick"])
            zones = spec.get("plays")
            if zones is None:
                plays = tuple(by_trick.get(idx, ()))
            else:
                pool = {p.zone: p for p in by_trick.get(idx, ())}
                missing = [z for z in zones if z not in pool]
                if missing:
                    raise ScenarioError(
                        f"trick_end(trick={idx}) 引用了没有 play 事件的区 {missing}")
                plays = tuple(pool[z] for z in zones)
            events.append(TrickEndEvent(trick_index=idx,
                                        frame_ts=float(spec.get("ts", i)),
                                        plays=plays))
        else:
            raise ScenarioError(f"未知事件类型：{kind!r}")
    return events


def _play_event(spec: Mapping[str, Any], index: int, *,
                default_trick: int = 0) -> PlayEvent:
    zone = spec.get("zone")
    if zone is None:
        raise ScenarioError("play 事件缺少 zone")
    if spec.get("cards") is not None:
        parsed = tuple(_cards(spec["cards"]))
        cards: tuple[Card, ...] | None = parsed
        count = int(spec.get("count", len(parsed)))
    else:
        cards = None
        count = int(spec.get("count", 0))
        if count <= 0:
            raise ScenarioError(
                f"play 事件 zone={zone} 未给 cards 时必须给 count > 0（张数未知仍是信息）")
    return PlayEvent(
        zone=str(zone),
        cards=cards,
        count=count,
        confidence=float(spec.get("confidence", 1.0)),
        frame_agreement=float(spec.get("agreement", 1.0)),
        trick_index=int(spec.get("trick", default_trick)),
        frame_ts=float(spec.get("ts", index)),
        evidence_path=spec.get("evidence"),
    )


def _cards(specs: Sequence[str]) -> list[Card]:
    out: list[Card] = []
    for token in specs:
        card = parse_code(str(token))
        if card is None:
            raise ScenarioError(f"无法解析牌编码 {token!r}")
        out.append(card)
    return out


# ---------- 断言 ----------


def check(scenario: Scenario, state: CounterState,
          errors: Sequence[str]) -> list[str]:
    """按 `expect` 逐项核对终态，返回**不匹配的描述列表**（全对则返回空列表）。

    刻意返回描述而不是直接 `assert`：调用方能把一条语料里的**所有**偏差
    一次性打出来，而不是"修一个跑一次"。
    """
    exp = scenario.expect
    session = state.session
    problems: list[str] = []

    def want(key: str, got: Any, expected: Any) -> None:
        if got != expected:
            problems.append(f"{key}: 期望 {expected!r}，实际 {got!r}")

    if "trick_index" in exp:
        want("trick_index", session.trick_index, exp["trick_index"])
    if "history_len" in exp:
        want("history_len", len(session.history), exp["history_len"])
    if "total_points" in exp:
        want("total_points", session.total_points(), exp["total_points"])
    if "unscored_tricks" in exp:
        want("unscored_tricks", session.unscored_tricks, exp["unscored_tricks"])

    if "points" in exp:
        # 只比非零项：`_score` 会给赢家 +0 也记一条，那不是"有分"
        nonzero = {k: v for k, v in session.points.items() if v}
        want("points", nonzero, exp["points"])

    if "history" in exp:
        if len(session.history) != len(exp["history"]):
            problems.append(
                f"history 长度：期望 {len(exp['history'])}，实际 {len(session.history)}")
        else:
            for i, (rec, e) in enumerate(zip(session.history, exp["history"],
                                             strict=True)):
                for field in ("points", "winner_zone", "points_known", "confident"):
                    if field in e:
                        want(f"history[{i}].{field}", getattr(rec, field), e[field])
                if "plays" in e:
                    got = [[c.code() for c in (p.cards or ())] for p in rec.plays]
                    want(f"history[{i}].plays", got, e["plays"])

    if "unseen" in exp:
        if state.pool is None:
            problems.append("unseen: 期望核对未见池，但手牌未标定（pool 为 None）")
        else:
            counter = state.pool.as_counter()
            for code, expected in exp["unseen"].items():
                card = parse_code(code)
                if card is None:
                    problems.append(f"unseen[{code}]: 无法解析的牌编码")
                    continue
                actual = counter.get(card, 0)
                if actual != expected:
                    problems.append(f"unseen[{code}]: 期望 {expected}，实际 {actual}")
    if "unseen_total" in exp:
        if state.pool is None:
            problems.append("unseen_total: 期望核对未见池，但手牌未标定（pool 为 None）")
        else:
            want("unseen_total", state.pool.total(), exp["unseen_total"])

    if "own_hand_remaining" in exp:
        remaining = state.own_hand_remaining(scenario.ctx.own_hand)
        if remaining is None:
            problems.append("own_hand_remaining: 手牌未标定，无法核对")
        else:
            counts = Counter(c.code() for c in remaining)
            spec = exp["own_hand_remaining"]
            if isinstance(spec, dict):
                for code, expected in spec.items():
                    if counts.get(code, 0) != expected:
                        problems.append(
                            f"own_hand_remaining[{code}]: 期望 {expected}，"
                            f"实际 {counts.get(code, 0)}")
            else:
                want("own_hand_remaining", sorted(counts.elements()), sorted(spec))

    if "voids" in exp:
        if state.voids is None:
            problems.append("voids: 无空门追踪器")
        else:
            mapping = state.voids.as_mapping()
            for seat_key, groups in exp["voids"].items():
                voids_for_seat: frozenset[int] = mapping.get(int(seat_key), frozenset())
                want(f"voids[{seat_key}]", sorted(voids_for_seat), sorted(groups))

    if exp.get("errors_empty"):
        if errors:
            problems.append(f"errors: 期望无错误，实际 {list(errors)!r}")
    for i, needle in enumerate(exp.get("errors", ())):
        if not any(needle in e for e in errors):
            problems.append(f"errors[{i}]: 没有任何错误消息包含 {needle!r}（实际 {list(errors)!r}）")

    return problems


def run_scenario(path: str | Path) -> tuple[Scenario, CounterState, list[str]]:
    """加载并回放，返回 (语料, 终态, 回放错误)。"""
    scenario = load_scenario(path)
    state, errors = scenario.run()
    return scenario, state, errors
