"""Command line entry point for ``/context-audit``.

Reads Claude Code transcripts, matches what they recorded as loaded against this
repo's agents, skills, rules and ``AGENTS.md``, and prints a cost report (or JSON
for ``/context-trim``). Only names, sizes and counts are printed.

Run as ``uv run python .claude/skills/context-audit/scripts/audit.py``; the sibling
modules are imported by bare name, so this directory must be the script's own
directory (it is, when run this way).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TextIO

from attribute import Artifact, Unmatched, match_session, scan_repo
from report import (
    ReportMeta,
    build_stats,
    other_load,
    render_json,
    render_report,
    top_tool_results,
)
from transcripts import (
    TESTED_VERSIONS,
    Session,
    TranscriptFormatError,
    load_sessions,
)

EXIT_OK = 0
EXIT_FORMAT_ERROR = 2
TOOL_RESULT_ROWS = 5
DEFAULT_ROOT = Path.home() / ".claude" / "projects"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the audit and return the process exit code.

    ``0`` for a report or for "no sessions found"; ``2`` when a transcript is in
    a format this tool does not recognise, including sessions with API calls of
    which none has a readable timestamp. Then stderr says "unrecognised
    transcript format" and nothing is printed to stdout, in JSON mode too.
    (argparse also exits ``2`` on a usage error, with its own message.)
    """
    _use_utf8(sys.stdout)
    _use_utf8(sys.stderr)
    args = _parser().parse_args(argv)
    try:
        loaded = list(load_sessions(args.root, args.project, args.since))
    except TranscriptFormatError as error:
        print(
            f"context-audit: unrecognised transcript format: {error}", file=sys.stderr
        )
        return EXIT_FORMAT_ERROR
    # A session that never called the API paid for nothing and would only dilute
    # the used-in-a-of-b counts.
    sessions = [session for session in loaded if session.calls]
    days = _call_days(sessions)
    if days is None and sessions:
        versions = ", ".join(sorted({session.version for session in sessions}))
        print(
            "context-audit: unrecognised transcript format: no API call has a "
            f"timestamp this tool can read (Claude Code version {versions})",
            file=sys.stderr,
        )
        return EXIT_FORMAT_ERROR
    if days is None:
        print(_no_sessions(args, json_mode=args.json))
        return EXIT_OK
    repo: Path = args.repo
    artifacts = [_relative_to(item, repo) for item in scan_repo(repo)]
    stats = build_stats(sessions, artifacts)
    meta = _meta(sessions, artifacts, days)
    if args.json:
        print(render_json(stats, meta))
    else:
        text = render_report(
            stats,
            other_load(sessions),
            top_tool_results(sessions, TOOL_RESULT_ROWS),
            meta,
            args.top,
        )
        print(text, end="")
    return EXIT_OK


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="audit.py",
        description="Attribute Claude Code context cost to this repo's files.",
    )
    parser.add_argument(
        "--project",
        metavar="NAME",
        help="only project directories whose name contains NAME (case-insensitive)",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        type=date.fromisoformat,
        help="only sessions that started on or after this UTC date",
    )
    parser.add_argument(
        "--top", metavar="N", type=int, help="show only the N costliest artifacts"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print JSON (all artifacts) instead of the terminal report",
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        type=Path,
        default=DEFAULT_ROOT,
        help="transcripts root (default: ~/.claude/projects)",
    )
    parser.add_argument(
        "--repo",
        metavar="PATH",
        type=Path,
        default=Path.cwd(),
        help="repo to audit (default: current directory)",
    )
    return parser


def _use_utf8(stream: TextIO) -> None:
    """Write UTF-8, replacing anything unencodable, where the stream allows."""
    reconfigure = getattr(stream, "reconfigure", None)
    if reconfigure is None:
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass  # already written to, or not a real text stream: keep its settings


def _relative_to(artifact: Artifact, repo: Path) -> Artifact:
    """``artifact`` with its path relative to ``repo``, so output is stable."""
    return dataclasses.replace(artifact, path=artifact.path.relative_to(repo))


def _call_days(sessions: list[Session]) -> tuple[date, date] | None:
    """First and last call date across ``sessions``, or ``None`` without calls."""
    days = [
        day
        for session in sessions
        for call in session.calls
        if (day := _day(call.timestamp)) is not None
    ]
    return (min(days), max(days)) if days else None


def _day(stamp: str) -> date | None:
    try:
        moment = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.date()


def _meta(
    sessions: list[Session], artifacts: list[Artifact], days: tuple[date, date]
) -> ReportMeta:
    unmatched: list[Unmatched] = []
    for session in sessions:
        unmatched.extend(match_session(artifacts, session)[1])
    return ReportMeta(
        session_count=len(sessions),
        first_day=days[0],
        last_day=days[1],
        claude_versions=sorted({session.version for session in sessions}),
        tested_versions=TESTED_VERSIONS,
        skipped_lines=sum(session.skipped_lines for session in sessions),
        unmatched=unmatched,
    )


def _no_sessions(args: argparse.Namespace, json_mode: bool) -> str:
    searched = [] if json_mode else [f"transcripts root: {args.root}"]
    if args.project:
        searched.append(f"project filter: {args.project}")
    if args.since:
        searched.append(f"since: {args.since.isoformat()}")
    message = "No sessions found (sessions with no API calls are ignored)."
    if json_mode:
        payload = {
            "meta": {"session_count": 0, "message": message, "searched": searched},
            "artifacts": [],
        }
        return json.dumps(payload, indent=2)
    return "\n".join([message, "Searched:", *(f"  {line}" for line in searched)])


if __name__ == "__main__":
    raise SystemExit(main())
