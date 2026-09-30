"""离线事件语料库的回放测试。

一条语料 = 一段事件序列 + 一份期望终态。这里只做两件事：
把每条语料回放一遍，核对终态；以及确认**语料本身**不会因为格式问题静默失效。
"""

import json
from pathlib import Path

import pytest

from shengji.scenario import ScenarioError, check, load_dir, load_scenario, run_scenario

SCENARIO_DIR = Path(__file__).parent / "fixtures" / "scenarios"


@pytest.fixture(scope="module")
def scenarios():
    return load_dir(SCENARIO_DIR)


def test_corpus_is_not_empty(scenarios):
    """空语料库会被误当成「全过」—— 所以文件数本身要断言。"""
    assert len(scenarios) >= 7


def test_every_scenario_asserts_something(scenarios):
    for sc in scenarios:
        assert sc.expect, f"{sc.name} 没有 expect：只回放不断言，等于没跑"
        assert sc.events, f"{sc.name} 没有事件"


def test_all_scenarios_match_expect(scenarios):
    """逐条核对；把**所有**偏差一次性报出来，而不是修一条跑一次。"""
    failures = []
    for sc in scenarios:
        state, errors = sc.run()
        problems = check(sc, state, errors)
        if problems:
            failures.append(f"[{sc.name}] " + "；".join(problems))
    assert not failures, "\n".join(failures)


def test_replay_is_deterministic(scenarios):
    """同一段事件回放两次必须得到同一终态（重放层是纯函数）。"""
    for sc in scenarios:
        a, _ = sc.run()
        b, _ = sc.run()
        assert _fingerprint(a) == _fingerprint(b), sc.name


def _fingerprint(state):
    pool = state.pool
    return (
        state.session.trick_index,
        len(state.session.history),
        tuple(sorted(state.session.points.items())),
        tuple(sorted((c.code(), n) for c, n in (pool.as_counter().items() if pool else []))),
    )


# ---------- 语料格式：坏输入必须显式失败 ----------


def _write(tmp_path: Path, payload: dict, name: str = "s.json") -> Path:
    p = tmp_path / name
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return p


def test_unknown_expect_key_is_rejected(tmp_path):
    """拼错的 expect 键会让断言恒真 —— 那是静默失效，必须报错。"""
    p = _write(tmp_path, {"events": [], "expect": {"trick_indxe": 1}})
    with pytest.raises(ScenarioError) as ei:
        load_scenario(p)
    assert "未知键" in str(ei.value)


def test_trick_end_referencing_missing_zone_is_rejected(tmp_path):
    p = _write(tmp_path, {
        "events": [
            {"type": "play", "zone": "bottom", "cards": ["H5"], "trick": 0},
            {"type": "trick_end", "trick": 0, "plays": ["bottom", "right"]},
        ],
        "expect": {},
    })
    with pytest.raises(ScenarioError) as ei:
        load_scenario(p)
    assert "没有 play 事件" in str(ei.value)


def test_unparsable_card_code_is_rejected(tmp_path):
    p = _write(tmp_path, {
        "events": [{"type": "play", "zone": "bottom", "cards": ["HZ9"]}],
        "expect": {},
    })
    with pytest.raises(ScenarioError) as ei:
        load_scenario(p)
    assert "无法解析牌编码" in str(ei.value)


def test_unrecognized_play_requires_positive_count(tmp_path):
    """cards 为 null 时张数仍是信息；不给就等于这手牌什么都不知道。"""
    p = _write(tmp_path, {
        "events": [{"type": "play", "zone": "bottom", "cards": None}],
        "expect": {},
    })
    with pytest.raises(ScenarioError) as ei:
        load_scenario(p)
    assert "count > 0" in str(ei.value)


def test_unknown_event_type_is_rejected(tmp_path):
    p = _write(tmp_path, {"events": [{"type": "teleport"}], "expect": {}})
    with pytest.raises(ScenarioError):
        load_scenario(p)


def test_unverified_variant_is_rejected(tmp_path):
    """6 人 3 副的规则数据标着 UNVERIFIED —— 语料也不能绕过这道闸。"""
    p = _write(tmp_path, {"players": 6, "decks": 3, "events": [], "expect": {}})
    with pytest.raises(Exception) as ei:
        load_scenario(p)
    assert "UNVERIFIED" in str(ei.value) or "尚未实测确认" in str(ei.value)


def test_empty_directory_is_rejected(tmp_path):
    with pytest.raises(ScenarioError) as ei:
        load_dir(tmp_path)
    assert "没有任何语料文件" in str(ei.value)


def test_run_scenario_helper_returns_final_state():
    sc, state, errors = run_scenario(SCENARIO_DIR / "01-two-tricks-scoring.json")
    assert sc.name.startswith("两墩记分")
    assert state.session.trick_index == 2
    assert errors == []
