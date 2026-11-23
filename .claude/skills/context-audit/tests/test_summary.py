"""Tests for the compact default view (render_summary), synthetic data only."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from attribute import Artifact, Kind, match_session
from report import (
    SUMMARY_ROWS,
    ArtifactStats,
    OtherLoad,
    ToolResultStat,
    Verdict,
    build_stats,
    other_load,
    render_summary,
    top_tool_results,
)
from test_render import (
    OTHER,
    RESULTS,
    SENTINEL,
    make_meta,
    make_stat,
    secret_sessions,
)
from transcripts import TESTED_VERSIONS

HEADER = "| Name | Tokens every session | Used in | Est. cost | What to do |"


def summary(stats: list[ArtifactStats], top: int | None = None, **meta: object) -> str:
    return render_summary(stats, OTHER, RESULTS, make_meta(**meta), top)


def row(text: str, name: str) -> list[str]:
    line = next(item for item in text.splitlines() if item.startswith(f"| {name}"))
    return [cell.strip() for cell in line.strip("|").split("|")]


def with_verdict(stat: ArtifactStats, verdict: Verdict) -> ArtifactStats:
    return replace(stat, verdict=verdict)


def test_summary_is_a_markdown_table_with_plain_headers() -> None:
    text = summary([make_stat("alpha")])
    lines = text.splitlines()
    assert HEADER in lines
    assert lines[lines.index(HEADER) + 1] == "|---|---:|---:|---:|---|"


def test_summary_row_has_name_tokens_usage_cost_and_action() -> None:
    cells = row(summary([make_stat("alpha")]), "alpha")
    assert cells == ["alpha", "640", "3 of 12", "$1.90", "Shorten description"]


def test_actions_use_plain_words() -> None:
    stats = [
        with_verdict(make_stat("a"), "trim"),
        with_verdict(make_stat("b"), "move-to-on-demand"),
        with_verdict(make_stat("c"), "keep"),
        with_verdict(make_stat("d"), "insufficient data"),
    ]
    text = summary(stats)
    assert row(text, "a")[-1] == "Shorten description"
    assert row(text, "b")[-1] == "Consider loading only on demand"
    assert row(text, "c")[-1] == "Fine"
    assert row(text, "d")[-1] == "Not enough data"


def test_always_on_files_say_always_on_and_shorten_the_file() -> None:
    rule = with_verdict(make_stat("context7", kind="rule"), "trim")
    cells = row(summary([rule]), "context7")
    assert cells[2] == "always on"
    assert cells[4] == "Shorten the file"


def test_never_loaded_artifact_shows_dashes_not_zero_dollars() -> None:
    stat = replace(
        make_stat("AGENTS.md", kind="agents_md", loaded=0, used=0, window=0.0),
        verdict="insufficient data",
    )
    cells = row(summary([stat]), "AGENTS.md")
    assert cells[2] == "-"
    assert cells[3] == "n/a"
    assert cells[4] == "Not loaded in these sessions"


def test_unpriced_cost_is_n_a() -> None:
    assert row(summary([make_stat("alpha", window=None)]), "alpha")[3] == "n/a"


def test_tiny_costs_keep_three_decimals() -> None:
    assert row(summary([make_stat("alpha", window=0.004)]), "alpha")[3] == "$0.004"


def test_rows_are_ordered_by_cost_highest_first() -> None:
    stats = [make_stat("cheap", window=0.1), make_stat("dear", window=5.0)]
    names = [line for line in summary(stats).splitlines() if line.startswith("| ")]
    assert names[1].startswith("| dear")
    assert names[2].startswith("| cheap")


def test_default_cuts_to_summary_rows_and_says_how_to_see_more() -> None:
    stats = [make_stat(f"n{i:02d}", window=10.0 - i / 10) for i in range(20)]
    text = summary(stats)
    shown = [line for line in text.splitlines() if line.startswith("| n")]
    assert len(shown) == SUMMARY_ROWS
    assert "5 more rows not shown (--top N or --details)" in text


def test_top_overrides_the_default_cut() -> None:
    stats = [make_stat(f"n{i:02d}", window=10.0 - i / 10) for i in range(20)]
    text = summary(stats, top=20)
    assert len([line for line in text.splitlines() if line.startswith("| n")]) == 20
    assert "more rows not shown" not in text


def test_no_rows_still_renders() -> None:
    text = summary([])
    assert HEADER in text.splitlines()
    assert "Not counted above" in text


def test_summary_leaves_out_reasons_thresholds_and_long_caveats() -> None:
    text = summary([make_stat("alpha")])
    assert "Why:" not in text
    assert "MIN_SESSIONS" not in text
    assert "Verdict rules" not in text
    assert "cache" not in text
    assert len(text.splitlines()) <= 14


def test_summary_points_to_details_and_says_figures_are_estimates() -> None:
    text = summary([make_stat("alpha")])
    assert "--details" in text
    assert "estimates" in text


def test_summary_says_what_is_not_counted_in_plain_words() -> None:
    other = OtherLoad(2300, 13000, 1, measured=False)
    results = [ToolResultStat("Bash", 368_000, 383, 16)]
    text = render_summary([make_stat("alpha")], other, results, make_meta(), None)
    not_counted = next(ln for ln in text.splitlines() if ln.startswith("Not counted"))
    assert "built-in tools about 13.0k tokens per call" in not_counted
    assert "system prompt about 2.3k" in not_counted
    largest = next(ln for ln in text.splitlines() if ln.startswith("Largest"))
    assert "Bash" in largest
    assert "92.0k tokens" in largest


def test_untested_version_warning_is_kept_in_the_summary() -> None:
    text = summary([make_stat("alpha")], claude_versions=["9.9.9"])
    assert "Warning: Claude Code 9.9.9 not tested" in text
    assert "Warning" not in summary(
        [make_stat("alpha")], tested_versions=TESTED_VERSIONS
    )


def test_header_has_session_count_and_dates_in_words() -> None:
    first = summary([make_stat("alpha")]).splitlines()[0]
    assert first == "Context audit: 47 sessions, 2026-10-01 to 2026-11-23"


def test_one_session_is_not_pluralised() -> None:
    first = summary([make_stat("alpha")], session_count=1).splitlines()[0]
    assert first.startswith("Context audit: 1 session, ")


def test_summary_is_ascii_only() -> None:
    summary([make_stat("café")]).encode("ascii")


def test_pipe_in_a_name_cannot_break_the_table() -> None:
    cells = row(summary([make_stat("a|b")]), "a")
    assert len(cells) == 5


def test_summary_never_contains_message_or_attachment_text(tmp_path: Path) -> None:
    sessions = secret_sessions(tmp_path)
    artifacts = [Artifact("agent", "alpha", Path("alpha.md"), 10, 100)]
    unmatched = [u for s in sessions for u in match_session(artifacts, s)[1]]
    meta = make_meta(unmatched=unmatched, session_count=len(sessions))
    text = render_summary(
        build_stats(sessions, artifacts),
        other_load(sessions),
        top_tool_results(sessions, 5),
        meta,
        None,
    )
    assert SENTINEL not in text
    assert "alpha" in text


def test_kind_is_not_needed_to_read_the_name() -> None:
    kinds: list[Kind] = ["agent", "skill", "rule", "agents_md"]
    text = summary([make_stat(f"x{i}", kind=k) for i, k in enumerate(kinds)])
    assert [row(text, f"x{i}")[0] for i in range(4)] == ["x0", "x1", "x2", "x3"]
