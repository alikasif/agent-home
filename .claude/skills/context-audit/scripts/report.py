"""Per-artifact statistics and verdicts for the context audit.

The first half turns sessions plus the repo's artifacts into numbers and a
verdict per artifact; the second half renders them as a terminal report or JSON.

Every dollar figure here is call-level and *estimated* (``measured=False``):
it is computed from each call's own model rate, never from a session's recorded
total, because a recorded total can cover more than one file's calls. A figure
is ``None`` when any call it needs has a model without a rate, never zero and
never a partial sum.

Only names, sizes and counts are produced; no transcript or file content.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Literal

from attribute import (
    CHARS_PER_TOKEN,
    Artifact,
    Loaded,
    Unmatched,
    match_session,
    tokens_from_chars,
)
from pricing import RATES, Money, Rates, price_tokens
from transcripts import Call, RequestShape, Session

Verdict = Literal["keep", "trim", "move-to-on-demand", "insufficient data"]

# Fewer loaded sessions than this is too little evidence to judge an artifact.
MIN_SESSIONS = 10
# An agent or skill listing line above this is worth shortening (about 1,200
# chars: a sentence or two plus a couple of examples is well under it).
TRIM_DESCRIPTION_TOKENS = 300
# Used in fewer than this share of the sessions that loaded it counts as rarely
# used: one session in ten.
LOW_USE_RATE = 0.10
# An always-on file (``AGENTS.md``, a rule) above this is worth splitting or
# cutting (about 6,000 chars, a few screens of text).
TRIM_ALWAYS_ON_TOKENS = 1500

MCP_PREFIX = "mcp__"
ALWAYS_ON_KINDS = frozenset({"agents_md", "rule"})
# Used for the listing-line size of an artifact that no session loaded: the
# ``- `` and ``: `` around ``name: description``.
LISTING_OVERHEAD_CHARS = 4


@dataclass(frozen=True)
class ArtifactStats:
    """What one repo artifact costs and how it is used.

    ``always_tokens`` is what every session that loads it pays (the recorded
    listing line or file, averaged over loaded sessions; the repo size when no
    session loaded it). ``on_use_tokens`` is the body paid on each invocation
    (0 for always-on files). ``cost_window`` is the estimated dollars over the
    sessions that loaded it (always-paid plus on-use) and ``cost_per_session``
    that divided by ``loaded_sessions``. Both are ``None`` when unpriced.
    """

    artifact: Artifact
    always_tokens: int
    on_use_tokens: int
    loaded_sessions: int
    used_sessions: int
    cost_window: Money | None
    cost_per_session: Money | None
    verdict: Verdict
    reason: str


@dataclass(frozen=True)
class OtherLoad:
    """Mean per-call context that is not one of this repo's artifacts.

    An estimate (``measured`` is ``False``): chars divided by
    ``CHARS_PER_TOKEN``. ``RequestShape`` omits each tool's ``input_schema``, so
    the tool figures are understated.
    """

    system_tokens: int
    builtin_tool_tokens: int
    mcp_tool_tokens: int
    measured: bool


@dataclass(frozen=True)
class ToolResultStat:
    """Totals for one tool's results; ``repeated`` results repeat an earlier hash."""

    tool_name: str
    total_chars: int
    count: int
    repeated: int


# -- artifact stats -----------------------------------------------------------


def build_stats(
    sessions: list[Session],
    artifacts: list[Artifact],
    rates: dict[str, Rates] = RATES,
) -> list[ArtifactStats]:
    """Return one :class:`ArtifactStats` per artifact, in the order given.

    ``loaded_sessions`` counts sessions where the artifact was loaded and
    ``used_sessions`` those where it was also invoked; sessions that never
    loaded it count for neither. Invocations only count when they name a repo
    skill or agent of that kind, so built-in slash commands are ignored.
    """
    matched = [
        (
            session,
            {
                _key(item.artifact): item
                for item in match_session(artifacts, session)[0]
            },
        )
        for session in sessions
    ]
    return [_artifact_stats(item, matched, rates) for item in artifacts]


def _key(artifact: Artifact) -> tuple[str, str]:
    return (artifact.kind, artifact.name)


def _artifact_stats(
    artifact: Artifact,
    matched: list[tuple[Session, dict[tuple[str, str], Loaded]]],
    rates: dict[str, Rates],
) -> ArtifactStats:
    body_tokens = _body_tokens(artifact)
    chars: list[int] = []
    used = 0
    cost: float | None = 0.0
    for session, loaded in matched:
        entry = loaded.get(_key(artifact))
        if entry is None:
            continue
        chars.append(entry.chars)
        invocations = _invocation_count(artifact, session)
        if invocations:
            used += 1
        always = _always_cost(session.calls, entry, rates)
        on_use = _on_use_cost(session.calls, body_tokens, invocations, rates)
        cost = _add(_add(cost, always), on_use)
    count = len(chars)
    mean_chars = round(sum(chars) / count) if count else _repo_always_chars(artifact)
    always_tokens = tokens_from_chars(mean_chars)
    verdict, reason = decide_verdict(artifact, count, used, always_tokens, body_tokens)
    return ArtifactStats(
        artifact=artifact,
        always_tokens=always_tokens,
        on_use_tokens=body_tokens,
        loaded_sessions=count,
        used_sessions=used,
        cost_window=None if cost is None else Money(cost, measured=False),
        cost_per_session=(
            Money(cost / count, measured=False) if cost is not None and count else None
        ),
        verdict=verdict,
        reason=reason,
    )


def _body_tokens(artifact: Artifact) -> int:
    """Tokens paid on each invocation; always-on files are never invoked."""
    if artifact.kind in ALWAYS_ON_KINDS:
        return 0
    return tokens_from_chars(artifact.body_chars)


def _repo_always_chars(artifact: Artifact) -> int:
    """Always-paid size from the repo, for an artifact no session loaded."""
    if artifact.kind in ALWAYS_ON_KINDS:
        return artifact.body_chars
    return artifact.description_chars + len(artifact.name) + LISTING_OVERHEAD_CHARS


def _invocation_count(artifact: Artifact, session: Session) -> int:
    if artifact.kind not in ("agent", "skill"):
        return 0
    return sum(
        1
        for call in session.invocations
        if call.kind == artifact.kind and call.name == artifact.name
    )


def _add(total: float | None, part: float | None) -> float | None:
    """Add costs; one unknown part makes the total unknown."""
    if total is None or part is None:
        return None
    return total + part


def _always_cost(
    calls: list[Call], loaded: Loaded, rates: dict[str, Rates]
) -> float | None:
    """Estimated dollars one session paid to carry ``loaded``.

    One cache write on the introducing call (the 1-hour rate when that call
    wrote to the 1-hour cache), then a cache read on each later call, each at
    its own model's rate. ``None`` if any of those calls' models is unpriced;
    ``0.0`` when no call followed the attachment.
    """
    tokens = tokens_from_chars(loaded.chars)
    total = 0.0
    for index in range(loaded.from_call, len(calls)):
        call = calls[index]
        rate = rates.get(call.model)
        if rate is None:
            return None
        if index == loaded.from_call:
            per_mtok = (
                rate.cache_write_1h
                if call.usage.cache_write_1h > 0
                else rate.cache_write
            )
        else:
            per_mtok = rate.cache_read
        total += price_tokens(tokens, per_mtok)
    return total


def _on_use_cost(
    calls: list[Call], body_tokens: int, invocations: int, rates: dict[str, Rates]
) -> float | None:
    """Body tokens times invocations at the first call's input rate."""
    if not invocations:
        return 0.0
    rate = rates.get(calls[0].model) if calls else None
    if rate is None:
        return None
    return price_tokens(body_tokens * invocations, rate.input)


# -- verdicts -----------------------------------------------------------------


def decide_verdict(
    a: Artifact, loaded: int, used: int, always_tokens: int, on_use_tokens: int
) -> tuple[Verdict, str]:
    """Pick a verdict for ``a`` and say in plain words why.

    ``loaded`` and ``used`` are session counts (``used`` only among loaded
    sessions). Always-on files (``AGENTS.md``, rules) get keep or trim by size
    only; agents and skills may also get move-to-on-demand when rarely used.
    """
    if loaded == 0:
        if a.kind == "agents_md":
            return (
                "insufficient data",
                (
                    "not loaded in any scanned session (it is not auto-loaded "
                    "when CLAUDE.md only points to it; it is read on demand)"
                ),
            )
        return "insufficient data", "no scanned session loaded it"
    if loaded < MIN_SESSIONS:
        return (
            "insufficient data",
            f"loaded in {loaded} sessions; needs at least {MIN_SESSIONS}",
        )
    if a.kind in ALWAYS_ON_KINDS:
        if always_tokens > TRIM_ALWAYS_ON_TOKENS:
            return (
                "trim",
                (
                    f"{always_tokens} tokens paid every session, "
                    f"over {TRIM_ALWAYS_ON_TOKENS}"
                ),
            )
        return (
            "keep",
            (
                f"{always_tokens} tokens paid every session, "
                f"within {TRIM_ALWAYS_ON_TOKENS}"
            ),
        )
    rate = used / loaded
    large = always_tokens > TRIM_DESCRIPTION_TOKENS
    use = f"used in {used} of {loaded} loaded sessions"
    if large and rate < LOW_USE_RATE:
        return (
            "move-to-on-demand",
            (
                f"{always_tokens} tokens paid every session but {use} "
                f"(under {LOW_USE_RATE:.0%}); its {on_use_tokens}-token body is "
                "only paid when used"
            ),
        )
    if large:
        return (
            "trim",
            (
                f"{always_tokens} tokens paid every session, "
                f"over {TRIM_DESCRIPTION_TOKENS}; {use}"
            ),
        )
    if rate < LOW_USE_RATE:
        return (
            "keep",
            f"{use}, but only {always_tokens} tokens paid every session",
        )
    return "keep", f"{use}; {always_tokens} tokens paid every session"


# -- context that is not ours -------------------------------------------------


def other_load(sessions: list[Session]) -> OtherLoad:
    """Mean per-call system prompt, built-in tool and MCP tool tokens.

    Taken from each call's request shape (calls without a recorded shape are
    skipped), as chars over ``CHARS_PER_TOKEN``, so it is an estimate.
    ``RequestShape`` omits each tool's ``input_schema``, so the tool figures
    are understated.
    """
    system = builtin = mcp = count = 0
    for session in sessions:
        for call in session.calls:
            shape = session.shapes.get(call.shape_hash or "")
            if shape is None:
                continue
            count += 1
            system += _system_chars(shape)
            builtin += _tool_chars(shape, lambda name: not name.startswith(MCP_PREFIX))
            mcp += _tool_chars(shape, lambda name: name.startswith(MCP_PREFIX))
    if count == 0:
        return OtherLoad(0, 0, 0, measured=False)
    return OtherLoad(
        tokens_from_chars(round(system / count)),
        tokens_from_chars(round(builtin / count)),
        tokens_from_chars(round(mcp / count)),
        measured=False,
    )


def _system_chars(shape: RequestShape) -> int:
    return sum(len(text) for text in shape.system)


def _tool_chars(shape: RequestShape, wanted: Callable[[str], bool]) -> int:
    return sum(len(name) + len(desc) for name, desc in shape.tools if wanted(name))


# -- tool results -------------------------------------------------------------


def top_tool_results(sessions: list[Session], n: int) -> list[ToolResultStat]:
    """The ``n`` tools whose results carry the most characters.

    ``repeated`` counts results whose content hash was already seen earlier in
    the same session (the same output returned again).
    """
    totals: dict[str, list[int]] = {}
    for session in sessions:
        seen: set[str] = set()
        for result in session.tool_results:
            row = totals.setdefault(result.tool_name, [0, 0, 0])
            row[0] += result.chars
            row[1] += 1
            if result.content_hash in seen:
                row[2] += 1
            seen.add(result.content_hash)
    ranked = sorted(totals.items(), key=lambda item: (-item[1][0], item[0]))
    return [ToolResultStat(name, *row) for name, row in ranked[:n]]


# -- rendering ----------------------------------------------------------------

NOT_AVAILABLE = "n/a"
EMPTY_CELL = "-"
UNMATCHED_LINES = 10
HEADERS = ("always", "on-use", "used", "$ window", "$/session", "verdict")


@dataclass(frozen=True)
class ReportMeta:
    """Facts about the scanned window that the report states but does not compute.

    ``first_day`` and ``last_day`` are the dates of the earliest and latest call;
    ``claude_versions`` are the Claude Code versions the sessions came from.
    """

    session_count: int
    first_day: date
    last_day: date
    claude_versions: list[str]
    tested_versions: tuple[str, ...]
    skipped_lines: int
    unmatched: list[Unmatched]


def render_report(
    stats: list[ArtifactStats],
    other: OtherLoad,
    results: list[ToolResultStat],
    meta: ReportMeta,
    top: int | None,
) -> str:
    """Return the terminal report: header, table, reasons, other context, footer.

    Rows are ordered by estimated dollars in the window (unpriced last) and cut
    to ``top`` when given. The output is ASCII only and holds names, sizes and
    counts, never message, attachment or file content.
    """
    ranked = sorted(stats, key=_row_order)
    shown = ranked if top is None else ranked[: max(top, 0)]
    lines = [_header(meta), *_warnings(meta), ""]
    lines += _table(shown)
    if len(shown) < len(ranked):
        lines.append(f"(showing top {len(shown)} of {len(ranked)} artifacts by $)")
    if shown:
        lines += ["", *_reasons(shown)]
    lines.append("")
    lines.append(_not_ours(other))
    lines += _unmatched_lines(meta.unmatched)
    lines += _result_lines(results)
    lines += ["", *_footer()]
    return "\n".join(lines) + "\n"


SUMMARY_ROWS = 15
SUMMARY_HEADERS = ("Name", "Tokens every session", "Used in", "Est. cost", "What to do")
SUMMARY_ALIGN = ("---", "---:", "---:", "---:", "---")
ACTIONS: dict[Verdict, str] = {
    "trim": "Shorten description",
    "move-to-on-demand": "Consider loading only on demand",
    "keep": "Fine",
    "insufficient data": "Not enough data",
}


def render_summary(
    stats: list[ArtifactStats],
    other: OtherLoad,
    results: list[ToolResultStat],
    meta: ReportMeta,
    top: int | None,
) -> str:
    """Return the compact default view: one plain-language table and two notes.

    Rows are ordered like :func:`render_report` and cut to ``top`` (default
    ``SUMMARY_ROWS``). Reasons, thresholds and caveats are left to
    :func:`render_report` (``--details``). ASCII only; names, sizes and counts
    only, never message, attachment or file content.
    """
    ranked = sorted(stats, key=_row_order)
    shown = ranked[: SUMMARY_ROWS if top is None else max(top, 0)]
    lines = [_summary_header(meta), *_warnings(meta), "", *_summary_table(shown)]
    if len(shown) < len(ranked):
        lines.append(
            f"{len(ranked) - len(shown)} more rows not shown (--top N or --details)"
        )
    lines += ["", *_summary_notes(other, results)]
    return "\n".join(lines) + "\n"


def _summary_header(meta: ReportMeta) -> str:
    noun = "session" if meta.session_count == 1 else "sessions"
    return (
        f"Context audit: {meta.session_count} {noun}, "
        f"{meta.first_day.isoformat()} to {meta.last_day.isoformat()}"
    )


def _summary_table(shown: list[ArtifactStats]) -> list[str]:
    def line(cells: tuple[str, ...] | list[str]) -> str:
        return "| " + " | ".join(cells) + " |"

    rows = [line(_summary_cells(item)) for item in shown]
    return [line(SUMMARY_HEADERS), "|" + "|".join(SUMMARY_ALIGN) + "|", *rows]


def _summary_cells(item: ArtifactStats) -> list[str]:
    loaded = item.loaded_sessions
    if not loaded:
        used = EMPTY_CELL
    elif item.artifact.kind in ALWAYS_ON_KINDS:
        used = "always on"
    else:
        used = f"{item.used_sessions} of {loaded}"
    cost = _money(item, item.cost_window)
    return [
        _ascii(item.artifact.name).replace("|", "/"),
        str(item.always_tokens),
        used,
        cost if cost == NOT_AVAILABLE else f"${cost}",
        _action(item),
    ]


def _action(item: ArtifactStats) -> str:
    if item.verdict == "insufficient data" and not item.loaded_sessions:
        return "Not loaded in these sessions"
    if item.verdict == "trim" and item.artifact.kind in ALWAYS_ON_KINDS:
        return "Shorten the file"
    return ACTIONS[item.verdict]


def _summary_notes(other: OtherLoad, results: list[ToolResultStat]) -> list[str]:
    lines = [
        (
            "Not counted above: built-in tools about "
            f"{_tokens(other.builtin_tool_tokens)} tokens per call, system prompt "
            f"about {_tokens(other.system_tokens)}."
        )
    ]
    if results:
        top = results[0]
        tokens = _tokens(tokens_from_chars(top.total_chars))
        lines.append(
            f"Largest tool output: {_ascii(top.tool_name)}, about {tokens} tokens "
            f"in {top.count} results."
        )
    lines.append(
        "All figures are estimates, not billing, and dollars run low. "
        "Use --details for the reasons and caveats."
    )
    return lines


def render_json(stats: list[ArtifactStats], meta: ReportMeta) -> str:
    """Return the report as JSON: ``{"meta": {...}, "artifacts": [...]}``.

    Each artifact entry has the keys ``artifact``, ``kind``, ``path``,
    ``always_tokens``, ``on_use_tokens``, ``loaded_sessions``, ``used_sessions``,
    ``cost_window``, ``cost_per_session`` (dollars, or ``null`` when unpriced or
    never loaded), ``verdict``, ``reason`` and ``measured`` (whether the dollar
    figures were recorded rather than estimated).
    """
    payload = {
        "meta": {
            "session_count": meta.session_count,
            "first_day": meta.first_day.isoformat(),
            "last_day": meta.last_day.isoformat(),
            "claude_versions": meta.claude_versions,
            "tested_versions": list(meta.tested_versions),
            "untested_versions": _untested(meta),
            "skipped_lines": meta.skipped_lines,
            "unmatched": [
                {"source": item.source, "chars": item.chars} for item in meta.unmatched
            ],
        },
        "artifacts": [_json_row(item) for item in stats],
    }
    return json.dumps(payload, indent=2)


def _json_row(item: ArtifactStats) -> dict[str, object]:
    return {
        "artifact": item.artifact.name,
        "kind": item.artifact.kind,
        "path": item.artifact.path.as_posix(),
        "always_tokens": item.always_tokens,
        "on_use_tokens": item.on_use_tokens,
        "loaded_sessions": item.loaded_sessions,
        "used_sessions": item.used_sessions,
        "cost_window": _dollars(item, item.cost_window),
        "cost_per_session": _dollars(item, item.cost_per_session),
        "verdict": item.verdict,
        "reason": item.reason,
        "measured": any(
            money.measured
            for money in (item.cost_window, item.cost_per_session)
            if money is not None
        ),
    }


def _dollars(item: ArtifactStats, money: Money | None) -> float | None:
    """Dollars, or ``None`` when unpriced or no session loaded the artifact."""
    if money is None or item.loaded_sessions == 0:
        return None
    return money.value


def _row_order(item: ArtifactStats) -> tuple[int, float, int, str]:
    value = _dollars(item, item.cost_window)
    priced = value is not None
    return (0 if priced else 1, -(value or 0.0), -item.always_tokens, _label(item))


def _ascii(text: str) -> str:
    """``text`` with any non-ASCII character replaced by ``?``."""
    return text.encode("ascii", "replace").decode("ascii")


def _label(item: ArtifactStats) -> str:
    artifact = item.artifact
    if artifact.kind == "agents_md":
        return _ascii(artifact.name)
    return _ascii(f"{artifact.kind}:{artifact.name}")


def _header(meta: ReportMeta) -> str:
    versions = ", ".join(meta.claude_versions) or "unknown"
    return (
        f"context-audit  ({meta.session_count} sessions, "
        f"{meta.first_day.isoformat()}..{meta.last_day.isoformat()}, "
        f"Claude Code {_ascii(versions)})"
    )


def _untested(meta: ReportMeta) -> list[str]:
    return [v for v in meta.claude_versions if v not in meta.tested_versions]


def _warnings(meta: ReportMeta) -> list[str]:
    lines: list[str] = []
    untested = _untested(meta)
    if untested:
        lines.append(
            f"Warning: Claude Code {_ascii(', '.join(untested))} not tested "
            f"(tested: {', '.join(meta.tested_versions)}); the transcript format "
            "may differ and figures may be wrong."
        )
    if meta.skipped_lines:
        lines.append(
            f"Skipped {meta.skipped_lines} transcript lines that did not parse."
        )
    return lines


def _tokens(count: int) -> str:
    return f"{count / 1000:.1f}k" if count >= 1000 else str(count)


def _money(item: ArtifactStats, money: Money | None) -> str:
    value = _dollars(item, money)
    if value is None:
        return NOT_AVAILABLE
    # Small per-session figures would all round to 0.00 at two places.
    return f"{value:.2f}" if value >= 0.01 or value == 0 else f"{value:.3f}"


def _cells(item: ArtifactStats) -> list[str]:
    always_on = item.artifact.kind in ALWAYS_ON_KINDS
    loaded = item.loaded_sessions
    return [
        str(item.always_tokens),
        EMPTY_CELL if always_on else str(item.on_use_tokens),
        EMPTY_CELL if always_on or not loaded else f"{item.used_sessions}/{loaded}",
        _money(item, item.cost_window),
        _money(item, item.cost_per_session),
        item.verdict,
    ]


def _table(shown: list[ArtifactStats]) -> list[str]:
    rows = [(_label(item), _cells(item)) for item in shown]
    name_width = max([len("artifact"), *(len(label) for label, _ in rows)])
    widths = [
        max([len(head), *(len(cells[i]) for _, cells in rows)])
        for i, head in enumerate(HEADERS)
    ]

    def line(label: str, cells: list[str]) -> str:
        body = "  ".join(
            cell.ljust(width) if i == len(cells) - 1 else cell.rjust(width)
            for i, (cell, width) in enumerate(zip(cells, widths))
        )
        return f"{label.ljust(name_width)}  {body}".rstrip()

    return [line("artifact", list(HEADERS)), *(line(*row) for row in rows)]


def _reasons(shown: list[ArtifactStats]) -> list[str]:
    return ["Why:", *(f"  {_label(item)}: {_ascii(item.reason)}" for item in shown)]


def _not_ours(other: OtherLoad) -> str:
    mark = "measured" if other.measured else "estimated"
    return (
        f"Not ours ({mark}), mean tokens per call: "
        f"system prompt {_tokens(other.system_tokens)}, "
        f"built-in tool defs {_tokens(other.builtin_tool_tokens)}, "
        f"MCP/plugin {_tokens(other.mcp_tool_tokens)}"
    )


def _unmatched_lines(unmatched: list[Unmatched]) -> list[str]:
    """Context recorded in sessions that no repo artifact accounts for."""
    sizes: dict[str, list[int]] = defaultdict(list)
    for item in unmatched:
        sizes[item.source].append(item.chars)
    if not sizes:
        return []
    ranked = sorted(sizes.items(), key=lambda pair: (-sum(pair[1]), pair[0]))
    lines = ["Recorded but not in this repo (estimated, mean tokens per session):"]
    for source, chars in ranked[:UNMATCHED_LINES]:
        mean = tokens_from_chars(round(sum(chars) / len(chars)))
        lines.append(f"  {_ascii(source)}: {_tokens(mean)} in {len(chars)} sessions")
    return lines


def _result_lines(results: list[ToolResultStat]) -> list[str]:
    if not results:
        return []
    lines = ["", "Largest tool results (measured chars, estimated tokens):"]
    for item in results:
        lines.append(
            f"  {_ascii(item.tool_name)}: {item.total_chars:,} chars "
            f"(~{_tokens(tokens_from_chars(item.total_chars))} tokens) "
            f"in {item.count} results, {item.repeated} repeated"
        )
    return lines


def _footer() -> list[str]:
    constants = (
        f"MIN_SESSIONS = {MIN_SESSIONS}, "
        f"TRIM_DESCRIPTION_TOKENS = {TRIM_DESCRIPTION_TOKENS}, "
        f"LOW_USE_RATE = {LOW_USE_RATE}, "
        f"TRIM_ALWAYS_ON_TOKENS = {TRIM_ALWAYS_ON_TOKENS}, "
        f"CHARS_PER_TOKEN = {CHARS_PER_TOKEN}"
    )
    dollars = (
        "$ columns are call-level dollar estimates (estimated): always-paid "
        "tokens at each call's cache rates plus on-use body at the input rate; "
        "n/a means an unpriced model or never loaded. Token columns are "
        f"estimated sizes (recorded chars / {CHARS_PER_TOKEN})."
    )
    basis = (
        f"All figures are estimated from recorded chars at {CHARS_PER_TOKEN} "
        "chars per token and list prices; not derived from billing totals."
    )
    cache = (
        "Dollar figures assume the cache stays warm; cache misses are not "
        "counted, so they are understated."
    )
    dispatch = (
        "On-use dollars cover the dispatch only, not the calls the subagent "
        "makes, so they are understated for agents that run long."
    )
    schemas = (
        "Tool input schemas are excluded from the Not ours figures (the "
        "transcript's request shape omits them), so tool tokens are understated. "
        "Deferred MCP tools are not in the recorded request shape, so "
        "MCP/plugin tokens are understated (the figure can read about 1)."
    )
    return [
        dollars,
        basis,
        cache,
        dispatch,
        f"Verdict rules (heuristic): {constants}",
        schemas,
    ]
