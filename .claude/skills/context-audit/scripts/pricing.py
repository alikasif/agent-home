"""Turn token usage into dollars, labelled ``measured`` or ``estimated``.

A cost is *measured* only when Claude Code recorded the session total itself
(``cost-state``). Anything computed here from per-token rates is *estimated*.
A model with no known rate yields ``None``, never zero: an unpriced call must
not silently shrink a total.
"""

from __future__ import annotations

from dataclasses import dataclass

from transcripts import Session, Usage

TOKENS_PER_MTOK = 1_000_000


@dataclass(frozen=True)
class Rates:
    """USD per million tokens for one model."""

    input: float
    cache_write: float
    cache_read: float
    output: float


@dataclass(frozen=True)
class Money:
    """A dollar amount and whether it was recorded (``True``) or computed."""

    value: float
    measured: bool


# Source: https://platform.claude.com/docs/en/about-claude/pricing (model pricing
# table), copied 2026-11-23. Only models whose rates were verified there and that
# appear in local transcripts (call models, or ``cost-state`` ``modelUsage``) are
# listed. Any other model prices as None, never zero. ``claude-haiku-5-5`` is
# deliberately absent: its price depends on prompt length (over 100k tokens), which
# ``Rates`` cannot express.
#
# Known gap: ``Usage.cache_write`` is a single figure, so the 5-minute cache write
# rate (1.25x input) is used. Writes into the 1-hour cache cost 2x input. In local
# transcripts nearly all writes are 1-hour, so an estimate here runs roughly a third
# below the recorded cost; repricing one such session's writes at the 1-hour rate
# reproduces its recorded cost exactly. Fixing it needs the 5m/1h split added to
# ``Usage`` (transcript field ``cache_creation.ephemeral_*_input_tokens``).
RATES: dict[str, Rates] = {
    "claude-sonnet-5-5": Rates(
        input=2.0, cache_write=2.5, cache_read=0.10, output=10.0
    ),
    "claude-sonnet-5": Rates(input=2.0, cache_write=2.5, cache_read=0.20, output=10.0),
}


def price_tokens(tokens: int, rate_per_mtok: float) -> float:
    """Return the USD cost of ``tokens`` at ``rate_per_mtok`` per million."""
    return tokens * rate_per_mtok / TOKENS_PER_MTOK


def usage_cost(
    usage: Usage, model: str, rates: dict[str, Rates] = RATES
) -> Money | None:
    """Estimate one call's cost; ``None`` when ``model`` has no rate."""
    rate = rates.get(model)
    if rate is None:
        return None
    total = (
        price_tokens(usage.input, rate.input)
        + price_tokens(usage.cache_write, rate.cache_write)
        + price_tokens(usage.cache_read, rate.cache_read)
        + price_tokens(usage.output, rate.output)
    )
    return Money(total, measured=False)


def session_cost(session: Session, rates: dict[str, Rates] = RATES) -> Money | None:
    """Return the recorded session cost, else an estimate, else ``None``.

    The estimate is ``None`` when the session has no calls or any call's model
    has no rate, so a partial sum is never presented as the total.
    """
    if session.cost_usd is not None:
        return Money(session.cost_usd, measured=True)
    if not session.calls:
        return None
    total = 0.0
    for call in session.calls:
        cost = usage_cost(call.usage, call.model, rates)
        if cost is None:
            return None
        total += cost.value
    return Money(total, measured=False)
