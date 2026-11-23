"""Tests for the render half of report.py, using synthetic sessions only."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from attribute import Artifact, Kind, Unmatched, match_session
from pricing import Money
from report import (
    LOW_USE_RATE,
    MIN_SESSIONS,
    TRIM_ALWAYS_ON_TOKENS,
    TRIM_DESCRIPTION_TOKENS,
    ArtifactStats,
    OtherLoad,
    ReportMeta,
    ToolResultStat,
    build_stats,
    other_load,
    render_json,
    render_report,
    top_tool_results,
)
from transcript_factory import TranscriptBuilder
from transcripts import TESTED_VERSIONS, Session, load_sessions

SENTINEL = "SECRET-SENTINEL-123"
JSON_KEYS = {
    "artifact",
    "kind",
    "path",
    "always_tokens",
    "on_use_tokens",
    "loaded_sessions",
    "used_sessions",
    "cost_window",
    "cost_per_session",
    "verdict",
    "reason",
    "measured",
}
OTHER = OtherLoad(9100, 12100, 3400, measured=False)
RESULTS = [ToolResultStat("Bash", 40_000, 20, 3)]


def art(name: str, kind: Kind = "agent") -> Artifact:
    return Artifact(kind, name, Path(f"{name}.md"), 40, 4000)


def make_stat(
    name: str = "alpha",
    kind: Kind = "agent",
    loaded: int = 12,
    used: int = 3,
    window: float | None = 1.9,
) -> ArtifactStats:
    cost = None if window is None else Money(window, measured=False)
    per = None if window is None or not loaded else Money(window / loaded, False)
    return ArtifactStats(
        artifact=art(name, kind),
        always_tokens=640,
        on_use_tokens=7000,
        loaded_sessions=loaded,
        used_sessions=used,
        cost_window=cost,
        cost_per_session=per,
        verdict="trim",
        reason="a reason",
    )


def make_meta(**overrides: Any) -> ReportMeta:
    fields: dict[str, Any] = {
        "session_count": 47,
        "first_day": date(2026, 10, 1),
        "last_day": date(2026, 11, 23),
        "claude_versions": ["2.1.330"],
        "tested_versions": TESTED_VERSIONS,
        "skipped_lines": 0,
        "unmatched": [],
    }
    fields.update(overrides)
    return ReportMeta(**fields)


def render(stats: list[ArtifactStats], top: int | None = None, **meta: Any) -> str:
    return render_report(stats, OTHER, RESULTS, make_meta(**meta), top)


def row(text: str, prefix: str) -> str:
    return next(line for line in text.splitlines() if line.startswith(prefix))


def secret_sessions(tmp_path: Path) -> list[Session]:
    builder = (
        TranscriptBuilder(session_id="s1")
        .shape(system=(f"system {SENTINEL}",), tools=(("Read", f"tool {SENTINEL}"),))
        .instructions({"/repo/AGENTS.md": f"rules {SENTINEL}"})
        .agent_delta({"alpha": f"- alpha: desc {SENTINEL}"})
        .skill_listing(f"- other: listing {SENTINEL}", ("other",))
        .request()
        .assistant(input=1, cache_write=100, cache_read=0)
        .entry("user", message={"role": "user", "content": f"hello {SENTINEL}"})
        .tool_use("Bash", {"command": f"echo {SENTINEL}"})
        .tool_result(f"output {SENTINEL}")
    )
    builder.write(tmp_path / "projects" / "p" / "s1.jsonl")
    return list(load_sessions(tmp_path / "projects"))


def test_render_never_contains_message_or_attachment_text(tmp_path: Path) -> None:
    sessions = secret_sessions(tmp_path)
    artifacts = [
        Artifact("agent", "alpha", Path("alpha.md"), 10, 100),
        Artifact("agents_md", "AGENTS.md", Path("AGENTS.md"), 0, 100),
    ]
    stats = build_stats(sessions, artifacts)
    unmatched = [u for s in sessions for u in match_session(artifacts, s)[1]]
    assert unmatched
    meta = make_meta(unmatched=unmatched, session_count=len(sessions))
    text = render_report(
        stats, other_load(sessions), top_tool_results(sessions, 5), meta, None
    )
    assert SENTINEL not in text
    assert SENTINEL not in render_json(stats, meta)
    assert "alpha" in text


def test_estimated_figures_are_marked() -> None:
    text = render([make_stat()])
    assert "(estimated)" in text
    not_ours = next(line for line in text.splitlines() if "Not ours" in line)
    assert "(estimated)" in not_ours


def test_footer_lists_verdict_constants() -> None:
    text = render([make_stat()])
    assert f"MIN_SESSIONS = {MIN_SESSIONS}" in text
    assert f"TRIM_DESCRIPTION_TOKENS = {TRIM_DESCRIPTION_TOKENS}" in text
    assert f"LOW_USE_RATE = {LOW_USE_RATE}" in text
    assert f"TRIM_ALWAYS_ON_TOKENS = {TRIM_ALWAYS_ON_TOKENS}" in text
    assert "CHARS_PER_TOKEN = 4.0" in text
    assert "input schemas" in text


def test_untested_version_warning_shown() -> None:
    assert "not tested" not in render([make_stat()])
    loud = render([make_stat()], claude_versions=["2.1.330", "9.9.9"])
    assert "9.9.9" in loud
    assert "not tested" in loud


def test_render_json_has_stable_keys() -> None:
    stats = [make_stat(), make_stat("b", window=None)]
    data = json.loads(render_json(stats, make_meta()))
    rows = data["artifacts"]
    assert len(rows) == 2
    for item in rows:
        assert set(item) == JSON_KEYS
    assert rows[0]["artifact"] == "alpha"
    assert rows[0]["cost_window"] == 1.9
    assert rows[0]["measured"] is False
    assert rows[1]["cost_window"] is None
    assert rows[1]["cost_per_session"] is None
    assert data["meta"]["session_count"] == 47


def test_top_limits_rows() -> None:
    stats = [make_stat(f"agent{i}", window=float(i)) for i in range(5)]
    text = render(stats, top=2)
    assert "agent4" in text and "agent3" in text
    assert "agent2" not in text and "agent0" not in text
    assert "top 2 of 5" in text
    full = render(stats, top=None)
    assert all(f"agent{i}" in full for i in range(5))


def test_never_loaded_artifact_prints_na_not_zero_dollars() -> None:
    stat = ArtifactStats(
        art("ghost"), 30, 1000, 0, 0, Money(0.0, False), None, "insufficient data", "x"
    )
    line = row(render([stat]), "agent:ghost")
    assert "$0.00" not in line and "0.00" not in line
    assert line.count("n/a") == 2
    assert line.split()[3] == "-"


def test_unpriced_artifact_prints_na() -> None:
    line = row(render([make_stat("pricey", window=None)]), "agent:pricey")
    assert line.count("n/a") == 2


def test_always_on_file_prints_dash_for_on_use() -> None:
    stat = replace(make_stat("rule1", kind="rule"), on_use_tokens=0)
    cells = row(render([stat]), "rule:rule1").split()
    assert cells[2] == "-"


def test_table_is_ascii_safe_for_cp1252() -> None:
    stats = [make_stat(), make_stat("café-→", window=None)]
    meta = make_meta(unmatched=[Unmatched("instructions:ü.md", 400)])
    text = render_report(stats, OTHER, RESULTS, meta, None)
    text.encode("cp1252")
    text.encode("ascii")
    render_json(stats, meta).encode("cp1252")


def test_unmatched_and_skipped_lines_reported() -> None:
    text = render(
        [make_stat()],
        skipped_lines=3,
        unmatched=[Unmatched("skill_listing", 4000), Unmatched("skill_listing", 2000)],
    )
    assert "3 transcript lines" in text
    assert "skill_listing" in text


def test_small_dollar_figures_keep_three_places() -> None:
    line = row(render([make_stat("tiny", window=0.036)]), "agent:tiny")
    assert "0.04" in line and "0.003" in line
