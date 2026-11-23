"""Tests for the stats half of report.py, using synthetic sessions only."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from attribute import Artifact
from pricing import Rates
from report import (
    LOW_USE_RATE,
    MIN_SESSIONS,
    TRIM_ALWAYS_ON_TOKENS,
    TRIM_DESCRIPTION_TOKENS,
    ArtifactStats,
    ToolResultStat,
    build_stats,
    decide_verdict,
    other_load,
    top_tool_results,
)
from transcript_factory import TranscriptBuilder
from transcripts import Session, load_sessions

MODEL = "claude-test-model"
# Easy arithmetic: dollars per million tokens.
RATES = {
    MODEL: Rates(
        input=2.0, cache_write=4.0, cache_read=1.0, output=0.0, cache_write_1h=8.0
    )
}
SMALL_CHARS = 40  # 10 tokens
BIG_DESCRIPTION_CHARS = (TRIM_DESCRIPTION_TOKENS + 100) * 4
BIG_ALWAYS_ON_CHARS = (TRIM_ALWAYS_ON_TOKENS + 100) * 4


def agent(name: str = "alpha", description_chars: int = 40, body_chars: int = 4000):
    return Artifact("agent", name, Path(f"{name}.md"), description_chars, body_chars)


def skill(name: str = "beta", description_chars: int = 40, body_chars: int = 4000):
    return Artifact("skill", name, Path(f"{name}.md"), description_chars, body_chars)


def always_on(kind: Literal["agents_md", "rule"], name: str, chars: int) -> Artifact:
    path = Path("AGENTS.md") if kind == "agents_md" else Path("rules") / f"{name}.md"
    return Artifact(kind, name, path, 0, chars)


def listing_line(artifact: Artifact, chars: int) -> str:
    """A listing line of exactly ``chars`` characters for ``artifact``."""
    prefix = f"- {artifact.name}: "
    return prefix + "d" * (chars - len(prefix))


def build(
    tmp_path: Path,
    sid: str,
    loaded: list[tuple[Artifact, int]] | None = None,
    invoke: list[tuple[str, str]] | None = None,
    calls: int = 2,
    model: str = MODEL,
    write_1h: int | None = None,
) -> Session:
    """One synthetic session: loads ``loaded`` before call 0, then ``calls`` calls.

    ``invoke`` holds ``(how, name)`` with ``how`` one of ``skill``, ``agent``,
    ``slash``.
    """
    builder = TranscriptBuilder(session_id=sid).shape()
    for item, chars in loaded or []:
        line = listing_line(item, chars)
        if item.kind == "agent":
            builder.agent_delta({item.name: line})
        elif item.kind == "skill":
            builder.skill_listing(line, (item.name,))
        else:
            builder.instructions({f"/repo/{item.path.as_posix()}": "x" * chars})
    for index in range(calls):
        builder.request(model=model).assistant(
            input=1,
            cache_write=100 if index == 0 else 0,
            cache_read=0 if index == 0 else 100,
            model=model,
            cache_write_1h=write_1h if index == 0 else None,
        )
    for how, name in invoke or []:
        if how == "slash":
            builder.slash_command(name)
        else:
            builder.tool_use("Skill" if how == "skill" else "Agent", {_key(how): name})
    builder.write(tmp_path / "projects" / "proj" / f"{sid}.jsonl")
    return next(s for s in load_sessions(tmp_path / "projects") if s.session_id == sid)


def _key(how: str) -> str:
    return "skill" if how == "skill" else "subagent_type"


def many(
    tmp_path: Path,
    item: Artifact,
    chars: int,
    total: int,
    used: int,
    how: str = "agent",
    **kwargs,
) -> list[Session]:
    """``total`` sessions loading ``item``; the first ``used`` also invoke it."""
    return [
        build(
            tmp_path,
            f"s{index}",
            loaded=[(item, chars)],
            invoke=[(how, item.name)] if index < used else None,
            **kwargs,
        )
        for index in range(total)
    ]


def stat(stats: list[ArtifactStats], name: str) -> ArtifactStats:
    return next(s for s in stats if s.artifact.name == name)


# -- loaded versus used -------------------------------------------------------


def test_loaded_vs_used_counts_only_loaded_sessions(tmp_path: Path) -> None:
    item = agent()
    sessions = [
        build(tmp_path, "a", loaded=[(item, 40)], invoke=[("agent", "alpha")]),
        build(tmp_path, "b", loaded=[(item, 40)]),
        build(tmp_path, "c", loaded=[(item, 40)]),
        # Not loaded: invoking it here is not evidence either way.
        build(tmp_path, "d"),
        build(tmp_path, "e", invoke=[("agent", "alpha")]),
    ]
    result = stat(build_stats(sessions, [item], RATES), "alpha")
    assert (result.loaded_sessions, result.used_sessions) == (3, 1)


def test_never_loaded_artifact_is_insufficient_data(tmp_path: Path) -> None:
    item = agent()
    sessions = [build(tmp_path, f"s{i}") for i in range(3)]
    result = stat(build_stats(sessions, [item], RATES), "alpha")
    assert result.verdict == "insufficient data"
    assert result.loaded_sessions == 0
    assert result.cost_per_session is None
    # Never loaded: falls back to the repo size, 49 chars ("- alpha: " + 40).
    assert result.always_tokens == 12


def test_no_sessions_at_all_does_not_raise() -> None:
    result = build_stats([], [agent()], RATES)
    assert result[0].verdict == "insufficient data"


def test_session_with_no_calls_does_not_raise(tmp_path: Path) -> None:
    item = agent()
    session = build(tmp_path, "z", loaded=[(item, 40)], calls=0)
    result = stat(build_stats([session], [item], RATES), "alpha")
    assert result.loaded_sessions == 1
    assert result.cost_window is not None
    assert result.cost_window.value == 0.0


# -- verdicts -----------------------------------------------------------------


def test_fewer_than_min_sessions_is_insufficient_data(tmp_path: Path) -> None:
    item = agent()
    sessions = many(tmp_path, item, BIG_DESCRIPTION_CHARS, MIN_SESSIONS - 1, 0)
    result = stat(build_stats(sessions, [item], RATES), "alpha")
    assert result.verdict == "insufficient data"
    assert str(MIN_SESSIONS) in result.reason


def test_always_on_file_never_gets_move_to_on_demand(tmp_path: Path) -> None:
    item = always_on("agents_md", "AGENTS.md", BIG_ALWAYS_ON_CHARS)
    sessions = [
        build(tmp_path, f"s{i}", loaded=[(item, BIG_ALWAYS_ON_CHARS)])
        for i in range(MIN_SESSIONS + 2)
    ]
    result = stat(build_stats(sessions, [item], RATES), "AGENTS.md")
    assert result.verdict == "trim"
    assert result.used_sessions == 0


def test_small_always_on_file_is_keep(tmp_path: Path) -> None:
    item = always_on("rule", "beta", 400)
    sessions = [
        build(tmp_path, f"s{i}", loaded=[(item, 400)]) for i in range(MIN_SESSIONS)
    ]
    assert stat(build_stats(sessions, [item], RATES), "beta").verdict == "keep"


def test_decide_verdict_rule_never_moves() -> None:
    item = always_on("rule", "beta", 4)
    for loaded in (0, 5, MIN_SESSIONS, 100):
        verdict, _ = decide_verdict(item, loaded, 0, 100_000, 0)
        assert verdict != "move-to-on-demand"


def test_large_description_gets_trim(tmp_path: Path) -> None:
    item = agent()
    sessions = many(tmp_path, item, BIG_DESCRIPTION_CHARS, MIN_SESSIONS + 2, 12)
    result = stat(build_stats(sessions, [item], RATES), "alpha")
    assert result.verdict == "trim"
    assert result.always_tokens > TRIM_DESCRIPTION_TOKENS


def test_low_use_high_cost_agent_gets_move_to_on_demand(tmp_path: Path) -> None:
    item = agent()
    total = MIN_SESSIONS + 10
    sessions = many(tmp_path, item, BIG_DESCRIPTION_CHARS, total, 0)
    result = stat(build_stats(sessions, [item], RATES), "alpha")
    assert result.verdict == "move-to-on-demand"
    assert result.used_sessions / result.loaded_sessions < LOW_USE_RATE


def test_low_use_but_small_agent_is_keep(tmp_path: Path) -> None:
    item = agent()
    sessions = many(tmp_path, item, SMALL_CHARS, MIN_SESSIONS + 2, 0)
    assert stat(build_stats(sessions, [item], RATES), "alpha").verdict == "keep"


def test_heavily_used_small_agent_is_keep(tmp_path: Path) -> None:
    item = agent()
    sessions = many(tmp_path, item, SMALL_CHARS, MIN_SESSIONS + 2, MIN_SESSIONS + 2)
    assert stat(build_stats(sessions, [item], RATES), "alpha").verdict == "keep"


def test_agents_md_not_loaded_reason_mentions_on_demand_pointer(tmp_path: Path) -> None:
    item = always_on("agents_md", "AGENTS.md", 4000)
    sessions = [build(tmp_path, f"s{i}") for i in range(MIN_SESSIONS + 1)]
    result = stat(build_stats(sessions, [item], RATES), "AGENTS.md")
    assert result.verdict == "insufficient data"
    assert "not auto-loaded" in result.reason
    assert "CLAUDE.md" in result.reason
    assert "on demand" in result.reason


# -- invocations --------------------------------------------------------------


def test_slash_command_counts_as_skill_use(tmp_path: Path) -> None:
    item = skill()
    sessions = [
        build(tmp_path, "a", loaded=[(item, 40)], invoke=[("slash", "beta")]),
        build(tmp_path, "b", loaded=[(item, 40)]),
    ]
    result = stat(build_stats(sessions, [item], RATES), "beta")
    assert (result.loaded_sessions, result.used_sessions) == (2, 1)


def test_builtin_slash_command_is_ignored(tmp_path: Path) -> None:
    item = skill()
    sessions = [
        build(tmp_path, "a", loaded=[(item, 40)], invoke=[("slash", "clear")]),
    ]
    result = stat(build_stats(sessions, [item], RATES), "beta")
    assert result.used_sessions == 0
    assert result.cost_window is not None
    # Only the always-paid part: no on-use cost for /clear.
    assert result.cost_window.value == pytest.approx(10 * (4.0 + 1.0) / 1_000_000)


def test_invocation_matches_only_its_own_kind(tmp_path: Path) -> None:
    item = agent(name="same")
    other = skill(name="same")
    session = build(
        tmp_path,
        "a",
        loaded=[(item, 40), (other, 40)],
        invoke=[("skill", "same")],
    )
    stats = build_stats([session], [item, other], RATES)
    by_kind = {s.artifact.kind: s for s in stats}
    assert by_kind["skill"].used_sessions == 1
    assert by_kind["agent"].used_sessions == 0


# -- costs --------------------------------------------------------------------


def test_always_paid_cost_uses_write_then_read_rates(tmp_path: Path) -> None:
    item = agent()
    # 400 chars = 100 tokens. Call 0 writes (5m), calls 1 and 2 read.
    session = build(tmp_path, "a", loaded=[(item, 400)], calls=3)
    result = stat(build_stats([session], [item], RATES), "alpha")
    expected = 100 * (4.0 + 1.0 + 1.0) / 1_000_000
    assert result.cost_window is not None
    assert result.cost_window.value == pytest.approx(expected)
    assert result.cost_window.measured is False
    assert result.cost_per_session is not None
    assert result.cost_per_session.value == pytest.approx(expected)


def test_one_hour_write_uses_one_hour_rate(tmp_path: Path) -> None:
    item = agent()
    session = build(tmp_path, "a", loaded=[(item, 400)], calls=2, write_1h=100)
    result = stat(build_stats([session], [item], RATES), "alpha")
    assert result.cost_window is not None
    assert result.cost_window.value == pytest.approx(100 * (8.0 + 1.0) / 1_000_000)


def test_cost_per_session_divides_by_loaded_sessions(tmp_path: Path) -> None:
    item = agent()
    sessions = [
        build(tmp_path, "a", loaded=[(item, 400)]),
        build(tmp_path, "b", loaded=[(item, 400)]),
        build(tmp_path, "c"),
    ]
    result = stat(build_stats(sessions, [item], RATES), "alpha")
    assert result.cost_window is not None and result.cost_per_session is not None
    assert result.cost_per_session.value == pytest.approx(result.cost_window.value / 2)


def test_on_use_cost_priced_at_input_rate_of_first_call(tmp_path: Path) -> None:
    item = skill(body_chars=4000)  # 1000 tokens
    session = build(
        tmp_path,
        "a",
        loaded=[(item, 400)],
        invoke=[("skill", "beta"), ("slash", "beta")],
    )
    result = stat(build_stats([session], [item], RATES), "beta")
    assert result.on_use_tokens == 1000
    always = 100 * (4.0 + 1.0) / 1_000_000
    on_use = 1000 * 2 * 2.0 / 1_000_000
    assert result.cost_window is not None
    assert result.cost_window.value == pytest.approx(always + on_use)


def test_unknown_model_cost_is_none_not_zero(tmp_path: Path) -> None:
    item = agent()
    session = build(tmp_path, "a", loaded=[(item, 400)], model="claude-unknown-9")
    result = stat(build_stats([session], [item], RATES), "alpha")
    assert result.cost_window is None
    assert result.cost_per_session is None
    # Tokens are still reported; only the dollars are unknown.
    assert result.always_tokens == 100


def test_one_unpriced_session_makes_the_whole_cost_none(tmp_path: Path) -> None:
    item = agent()
    sessions = [
        build(tmp_path, "a", loaded=[(item, 400)]),
        build(tmp_path, "b", loaded=[(item, 400)], model="claude-unknown-9"),
    ]
    result = stat(build_stats(sessions, [item], RATES), "alpha")
    assert result.cost_window is None


# -- other load and tool results ----------------------------------------------


def test_other_load_splits_mcp_tools(tmp_path: Path) -> None:
    builder = (
        TranscriptBuilder(session_id="o")
        .shape(
            "shape-a",
            system=("s" * 400,),
            tools=(("Read", "d" * 396), ("mcp__srv__tool", "d" * 386)),
        )
        .shape("shape-b", system=("s" * 800,), tools=(("Read", "d" * 396),))
        .request("shape-a")
        .assistant()
        .request("shape-b")
        .assistant()
    )
    builder.write(tmp_path / "projects" / "proj" / "o.jsonl")
    sessions = list(load_sessions(tmp_path / "projects"))
    result = other_load(sessions)
    assert result.system_tokens == 150  # mean of 100 and 200
    assert result.builtin_tool_tokens == 100  # Read: 400 chars in both shapes
    assert result.mcp_tool_tokens == 50  # 100 in one call of two
    assert result.measured is False


def test_other_load_without_shapes_is_zero(tmp_path: Path) -> None:
    session = build(tmp_path, "a")
    empty = replace(session, shapes={})
    result = other_load([empty])
    assert (result.system_tokens, result.builtin_tool_tokens) == (0, 0)
    assert result.mcp_tool_tokens == 0
    assert other_load([]).system_tokens == 0


def test_top_tool_results_counts_repeats(tmp_path: Path) -> None:
    builder = TranscriptBuilder(session_id="t").shape().request().assistant()
    builder.tool_use("Read", {})
    builder.tool_result("a" * 100)
    builder.tool_use("Read", {})
    builder.tool_result("a" * 100)  # same content again: repeated
    builder.tool_use("Read", {})
    builder.tool_result("b" * 50)
    builder.tool_use("Grep", {})
    builder.tool_result("c" * 10)
    other = TranscriptBuilder(session_id="u").shape().request().assistant()
    other.tool_use("Read", {})
    other.tool_result("a" * 100)  # a new session: not a repeat
    builder.write(tmp_path / "projects" / "proj" / "t.jsonl")
    other.write(tmp_path / "projects" / "proj" / "u.jsonl")
    sessions = list(load_sessions(tmp_path / "projects"))

    top = top_tool_results(sessions, 5)
    assert top[0] == ToolResultStat("Read", 350, 4, 1)
    assert top[1] == ToolResultStat("Grep", 10, 1, 0)
    assert len(top_tool_results(sessions, 1)) == 1
