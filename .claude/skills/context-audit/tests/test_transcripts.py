"""Tests for transcripts.py, using only synthetic fixtures."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from transcript_factory import TranscriptBuilder, standard_session
from transcripts import (
    Call,
    InstructionFile,
    Invocation,
    ToolResult,
    TranscriptFormatError,
    Usage,
    load_sessions,
)


def load_one(root: Path):
    sessions = list(load_sessions(root))
    assert len(sessions) == 1
    return sessions[0]


def write_standard(root: Path, project: str = "proj-a", **kwargs: str) -> Path:
    return standard_session(**kwargs).write(root / project / "sess-1.jsonl")


def test_load_sessions_parses_calls_and_usage(tmp_path: Path) -> None:
    write_standard(tmp_path)
    session = load_one(tmp_path)

    assert session.session_id == "sess-1"
    assert session.project == "proj-a"
    assert session.version == "2.1.330"
    assert [c.usage for c in session.calls] == [
        Usage(input=10, cache_write=100, cache_read=0, output=5),
        Usage(input=2, cache_write=0, cache_read=100, output=7),
        Usage(input=3, cache_write=0, cache_read=110, output=9),
    ]
    assert all(isinstance(c, Call) for c in session.calls)
    assert {c.model for c in session.calls} == {"claude-test-model"}
    assert {c.shape_hash for c in session.calls} == {"shape-a"}
    assert session.cost_usd == 1.25
    assert session.skipped_lines == 0
    assert session.shapes["shape-a"].system == ("synthetic system block",)
    assert session.shapes["shape-a"].tools == (("Read", "synthetic read tool"),)


def test_call_links_to_request_by_ref_not_adjacency(tmp_path: Path) -> None:
    builder = (
        TranscriptBuilder()
        .shape("shape-a")
        .shape("shape-b")
        .request("shape-a", model="model-a")
        .request("shape-b", model="model-b")
    )
    # req-2 is the nearest preceding request, but the entry names req-1.
    builder.link_to("req-1").assistant(model="ignored-for-link")
    builder.write(tmp_path / "p" / "s.jsonl")

    session = load_one(tmp_path)

    assert session.calls[0].shape_hash == "shape-a"
    assert session.calls[0].model == "model-a"


def test_call_without_ref_falls_back_to_nearest_preceding_request(
    tmp_path: Path,
) -> None:
    builder = TranscriptBuilder().shape().request(model="model-a")
    builder.link_to(None).assistant()
    builder.write(tmp_path / "p" / "s.jsonl")

    session = load_one(tmp_path)

    assert session.calls[0].shape_hash == "shape-a"
    assert session.calls[0].model == "model-a"


def test_synthetic_assistant_messages_are_not_calls(tmp_path: Path) -> None:
    (
        TranscriptBuilder()
        .request()
        .assistant()
        .assistant(model="<synthetic>", input=0, output=0)
        .write(tmp_path / "p" / "s.jsonl")
    )

    assert len(load_one(tmp_path).calls) == 1


def test_attachments_are_indexed_to_next_call(tmp_path: Path) -> None:
    write_standard(tmp_path)
    session = load_one(tmp_path)

    assert [i for i, _ in session.skill_listings] == [1]
    assert session.skill_listings[0][1].startswith("- synthetic-skill")
    assert session.agent_added == [
        (0, {"synthetic-agent": "- synthetic-agent: helper"})
    ]
    assert session.agent_removed == []
    assert session.instructions == [
        InstructionFile("/repo/AGENTS.md", "synthetic instructions", 0)
    ]


def test_agent_delta_removed_types_are_recorded(tmp_path: Path) -> None:
    (
        TranscriptBuilder()
        .request()
        .assistant()
        .agent_delta(added={}, removed=("gone",), is_initial=False)
        .request()
        .assistant()
        .write(tmp_path / "p" / "s.jsonl")
    )

    session = load_one(tmp_path)

    assert session.agent_removed == [(1, ["gone"])]
    assert session.agent_added == []


def test_invocations_from_tool_and_slash(tmp_path: Path) -> None:
    write_standard(tmp_path)

    assert load_one(tmp_path).invocations == [
        Invocation("skill", "x", "tool"),
        Invocation("skill", "y", "slash"),
    ]


def test_agent_tool_use_is_an_agent_invocation(tmp_path: Path) -> None:
    (
        TranscriptBuilder()
        .request()
        .assistant()
        .tool_use("Agent", {"subagent_type": "helper", "prompt": "synthetic"})
        .tool_use("Bash", {"command": "synthetic"})
        .write(tmp_path / "p" / "s.jsonl")
    )

    assert load_one(tmp_path).invocations == [Invocation("agent", "helper", "tool")]


def test_tool_results_record_name_size_and_hash(tmp_path: Path) -> None:
    (
        TranscriptBuilder()
        .request()
        .assistant()
        .tool_use("Read", {"file_path": "synthetic"})
        .tool_result("abcdef")
        .request()
        .assistant()
        .tool_use("Grep", {"pattern": "synthetic"})
        .tool_result(
            [
                {"type": "text", "text": "abc"},
                {"type": "tool_reference", "tool_name": "x"},
                {"type": "text", "text": "de"},
            ]
        )
        .write(tmp_path / "p" / "s.jsonl")
    )

    first, second = load_one(tmp_path).tool_results

    assert (first.tool_name, first.chars) == ("Read", 6)
    assert (second.tool_name, second.chars) == ("Grep", 5)
    assert all(isinstance(r, ToolResult) for r in (first, second))
    assert first.content_hash != second.content_hash
    assert len(first.content_hash) == 16


def test_identical_tool_results_share_a_hash(tmp_path: Path) -> None:
    builder = TranscriptBuilder().request().assistant()
    for _ in range(2):
        builder.tool_use("Read", {"file_path": "synthetic"}).tool_result("same")
    builder.write(tmp_path / "p" / "s.jsonl")

    first, second = load_one(tmp_path).tool_results

    assert first.content_hash == second.content_hash


def test_unknown_format_raises(tmp_path: Path) -> None:
    (
        TranscriptBuilder(version="9.9.9")
        .entry("brand-new-type")
        .entry("another-new-type")
        .write(tmp_path / "p" / "s.jsonl")
    )

    with pytest.raises(TranscriptFormatError, match=r"9\.9\.9"):
        list(load_sessions(tmp_path))


def test_unknown_format_without_version_still_raises(tmp_path: Path) -> None:
    (
        TranscriptBuilder(version=None)
        .entry("brand-new-type")
        .write(tmp_path / "p" / "s.jsonl")
    )

    with pytest.raises(TranscriptFormatError, match="unknown"):
        list(load_sessions(tmp_path))


def test_file_with_no_parseable_entries_is_skipped_not_fatal(tmp_path: Path) -> None:
    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "empty.jsonl").write_bytes(b"")
    (tmp_path / "p" / "cut.jsonl").write_bytes(b'{"type": "assist')

    assert list(load_sessions(tmp_path)) == []


def test_truncated_last_line_is_skipped(tmp_path: Path) -> None:
    path = write_standard(tmp_path)
    data = path.read_bytes().rstrip(b"\n")
    path.write_bytes(data[: -len(data.rsplit(b"\n", 1)[1]) // 2])

    session = load_one(tmp_path)

    assert len(session.calls) == 3
    assert session.cost_usd is None
    assert session.skipped_lines == 1


def test_truncated_inside_a_multibyte_character_is_skipped(tmp_path: Path) -> None:
    path = (
        TranscriptBuilder()
        .request()
        .assistant()
        .entry("user", message={"role": "user", "content": "café"})
        .write(tmp_path / "p" / "s.jsonl")
    )
    path.write_bytes(path.read_bytes().rstrip(b"\n")[:-3])

    session = load_one(tmp_path)

    assert len(session.calls) == 1
    assert session.skipped_lines == 1


def test_project_and_since_filters(tmp_path: Path) -> None:
    standard_session("a-old", "2026-10-01T00:00:00+00:00").write(
        tmp_path / "proj-a" / "a-old.jsonl"
    )
    standard_session("a-new", "2026-11-10T00:00:00+00:00").write(
        tmp_path / "proj-a" / "a-new.jsonl"
    )
    standard_session("b-new", "2026-11-12T00:00:00+00:00").write(
        tmp_path / "proj-b" / "b-new.jsonl"
    )

    def ids(project: str | None = None, since: date | None = None) -> set[str]:
        return {s.session_id for s in load_sessions(tmp_path, project, since)}

    assert ids() == {"a-old", "a-new", "b-new"}
    assert ids(project="proj-a") == {"a-old", "a-new"}
    assert ids(project="PROJ-B") == {"b-new"}
    assert ids(project="nope") == set()
    assert ids(since=date(2026, 11, 1)) == {"a-new", "b-new"}
    assert ids(since=date(2026, 11, 12)) == {"b-new"}
    assert ids(project="proj-a", since=date(2026, 11, 1)) == {"a-new"}


def test_missing_root_yields_nothing(tmp_path: Path) -> None:
    assert list(load_sessions(tmp_path / "does-not-exist")) == []


def test_skipped_lines_are_counted(tmp_path: Path) -> None:
    (
        TranscriptBuilder()
        .request()
        .raw("{not json")
        .assistant()
        .raw("")
        .raw("[1, 2]")
        .raw("not json either")
        .write(tmp_path / "p" / "s.jsonl")
    )

    session = load_one(tmp_path)

    assert session.skipped_lines == 3
    assert len(session.calls) == 1


def test_last_cost_state_wins_and_absent_means_none(tmp_path: Path) -> None:
    TranscriptBuilder().request().assistant().cost_state(1.0).cost_state(2.5).write(
        tmp_path / "p" / "with.jsonl"
    )
    TranscriptBuilder().request().assistant().write(tmp_path / "p" / "without.jsonl")

    costs = {s.session_id: s.cost_usd for s in load_sessions(tmp_path)}

    assert costs == {"with": 2.5, "without": None}
