"""Per-artifact statistics and verdicts for the context audit.

This is the stats half of the report: it turns sessions plus the repo's
artifacts into numbers and a verdict per artifact. Rendering is added separately.

Every dollar figure here is call-level and *estimated* (``measured=False``):
it is computed from each call's own model rate, never from a session's recorded
total, because a recorded total can cover more than one file's calls. A figure
is ``None`` when any call it needs has a model without a rate, never zero and
never a partial sum.

Only names, sizes and counts are produced; no transcript or file content.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from attribute import Artifact, Loaded, match_session, tokens_from_chars
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
                    "not auto-loaded here (CLAUDE.md only points to it); "
                    "it is read on demand via that pointer, so no session loaded it"
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
            f"{always_tokens} tokens paid every session, within {TRIM_ALWAYS_ON_TOKENS}",
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
