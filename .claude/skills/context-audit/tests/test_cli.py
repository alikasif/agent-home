"""Tests for the audit.py command line, using synthetic transcripts only."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest
from audit import main
from transcript_factory import TranscriptBuilder, standard_session, subagent_path


def make_repo(tmp_path: Path, agent_name: str = "alpha") -> Path:
    """A repo with one agent and an AGENTS.md."""
    repo = tmp_path / "repo"
    agents = repo / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / f"{agent_name}.md").write_text(
        f"---\nname: {agent_name}\ndescription: does things\n---\nbody\n",
        encoding="utf-8",
    )
    (repo / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    return repo


def write_session(
    root: Path, project: str, session_id: str, start: str, agent: str = "alpha"
) -> None:
    """One session that loads ``agent`` and makes two calls."""
    (
        TranscriptBuilder(session_id=session_id, start=start)
        .shape()
        .agent_delta({agent: f"- {agent}: does things"})
        .request()
        .assistant(input=10, cache_write=100)
        .request()
        .assistant(input=2, cache_read=100)
        .write(root / project / f"{session_id}.jsonl")
    )


def run(
    capsys: pytest.CaptureFixture[str], tmp_path: Path, *args: str
) -> tuple[int, str, str]:
    repo = (
        make_repo(tmp_path) if not (tmp_path / "repo").exists() else tmp_path / "repo"
    )
    code = main(["--repo", str(repo), *args])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_no_matching_sessions_prints_message_and_exits_0(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    missing = tmp_path / "nowhere"
    code, out, _ = run(capsys, tmp_path, "--root", str(missing))
    assert code == 0
    assert "No sessions found" in out
    assert str(missing) in out
    assert "artifact" not in out  # no table

    root = tmp_path / "projects"
    write_session(root, "proj-a", "s1", "2026-11-01T10:00:00+00:00")
    code, out, _ = run(
        capsys, tmp_path, "--root", str(root), "--project", "no-such-project"
    )
    assert code == 0
    assert "No sessions found" in out
    assert "no-such-project" in out
    assert "artifact" not in out

    code, out, _ = run(capsys, tmp_path, "--root", str(root), "--since", "2027-01-01")
    assert code == 0
    assert "No sessions found" in out
    assert "2027-01-01" in out


def test_sessions_without_calls_count_as_no_sessions(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    TranscriptBuilder().shape().instructions().write(root / "p" / "s.jsonl")
    code, out, _ = run(capsys, tmp_path, "--root", str(root))
    assert code == 0
    assert "No sessions found" in out


def test_unknown_format_exits_2_without_report(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    path = root / "p" / "bad.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        '{"type": "mystery", "version": "9.9.9"}\n{"type": "other"}\n',
        encoding="utf-8",
    )
    code, out, err = run(capsys, tmp_path, "--root", str(root))
    assert code == 2
    assert out == ""
    assert "9.9.9" in err


def test_json_flag_outputs_valid_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    write_session(root, "proj-a", "s1", "2026-11-01T10:00:00+00:00")
    code, out, _ = run(capsys, tmp_path, "--root", str(root), "--json")
    assert code == 0
    data = json.loads(out)
    assert set(data) == {"meta", "artifacts"}
    assert data["meta"]["session_count"] == 1
    names = {item["artifact"] for item in data["artifacts"]}
    assert names == {"alpha", "AGENTS.md"}


def test_json_with_no_sessions_is_still_valid_json(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = run(capsys, tmp_path, "--root", str(tmp_path / "x"), "--json")
    assert code == 0
    data = json.loads(out)
    assert data["artifacts"] == []
    assert data["meta"]["session_count"] == 0


def test_json_paths_are_repo_relative_with_forward_slashes(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    write_session(root, "proj-a", "s1", "2026-11-01T10:00:00+00:00")
    _, out, _ = run(capsys, tmp_path, "--root", str(root), "--json")
    paths = {item["artifact"]: item["path"] for item in json.loads(out)["artifacts"]}
    assert paths == {"alpha": ".claude/agents/alpha.md", "AGENTS.md": "AGENTS.md"}
    assert str(tmp_path) not in out
    assert "\\\\" not in out


def test_report_survives_cp1252_stdout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path, agent_name="café-日本")
    root = tmp_path / "projects"
    write_session(root, "proj-a", "s1", "2026-11-01T10:00:00+00:00", "café")
    raw = io.BytesIO()
    wrapper = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", wrapper)
    for flags in ([], ["--json"]):
        code = main(["--repo", str(repo), "--root", str(root), *flags])
        assert code == 0
    wrapper.flush()
    assert b"context-audit" in raw.getvalue()


def test_report_works_when_stdout_cannot_reconfigure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = make_repo(tmp_path)
    sink = io.StringIO()  # no reconfigure method
    monkeypatch.setattr(sys, "stdout", sink)
    assert main(["--repo", str(repo), "--root", str(tmp_path / "none")]) == 0
    assert "No sessions found" in sink.getvalue()


def test_project_filter_and_since(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    write_session(root, "agent-home-x", "s1", "2026-11-01T10:00:00+00:00")
    write_session(root, "Agent-Home-y", "s2", "2026-11-10T10:00:00+00:00")
    write_session(root, "other", "s3", "2026-11-20T10:00:00+00:00")

    _, out, _ = run(capsys, tmp_path, "--root", str(root))
    assert "(3 sessions," in out

    _, out, _ = run(capsys, tmp_path, "--root", str(root), "--project", "agent-home")
    assert "(2 sessions," in out

    _, out, _ = run(
        capsys,
        tmp_path,
        "--root",
        str(root),
        "--project",
        "agent-home",
        "--since",
        "2026-11-05",
    )
    assert "(1 sessions," in out


def test_header_dates_come_from_call_timestamps(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    write_session(root, "p", "s1", "2026-11-03T10:00:00+00:00")
    write_session(root, "p", "s2", "2026-11-09T10:00:00+00:00")
    _, out, _ = run(capsys, tmp_path, "--root", str(root))
    assert "2026-11-03..2026-11-09" in out
    _, out, _ = run(capsys, tmp_path, "--root", str(root), "--json")
    meta = json.loads(out)["meta"]
    assert (meta["first_day"], meta["last_day"]) == ("2026-11-03", "2026-11-09")


def test_meta_reports_versions_skipped_lines_and_unmatched(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    write_session(root, "p", "s1", "2026-11-01T10:00:00+00:00", agent="foreign")
    (
        standard_session("s2", "2026-11-02T10:00:00+00:00")
        .raw("{truncated")
        .write(root / "p" / "s2.jsonl")
    )
    _, out, _ = run(capsys, tmp_path, "--root", str(root), "--json")
    meta = json.loads(out)["meta"]
    assert meta["claude_versions"] == ["2.1.330"]
    assert meta["skipped_lines"] == 1
    assert {item["source"] for item in meta["unmatched"]} >= {"agent_listing"}


def test_top_limits_report_rows(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    write_session(root, "p", "s1", "2026-11-01T10:00:00+00:00")
    _, out, _ = run(capsys, tmp_path, "--root", str(root), "--top", "1")
    assert "(showing top 1 of 2 artifacts by $)" in out


def test_bad_since_is_a_usage_error(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    with pytest.raises(SystemExit) as raised:
        run(capsys, tmp_path, "--since", "yesterday")
    assert raised.value.code == 2


def test_agent_dispatched_inside_a_subagent_counts_as_used(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    root = tmp_path / "projects"
    write_session(root, "p", "s1", "2026-11-01T10:00:00+00:00")
    (
        TranscriptBuilder(session_id="s1")
        .request()
        .assistant()
        .tool_use("Agent", {"subagent_type": "alpha", "prompt": "synthetic"})
        .write(subagent_path(root / "p" / "s1.jsonl"))
    )
    code, out, _ = run(capsys, tmp_path, "--root", str(root), "--json")
    data = json.loads(out)
    row = next(item for item in data["artifacts"] if item["artifact"] == "alpha")
    assert code == 0
    assert data["meta"]["session_count"] == 1
    assert (row["loaded_sessions"], row["used_sessions"]) == (1, 1)
