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
    """USD per million tokens for one model.

    ``cache_write`` is the 5-minute cache write rate, ``cache_write_1h`` the
    1-hour one.
    """

    input: float
    cache_write: float
    cache_read: float
    output: float
    cache_write_1h: float


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
# Cache writes: ``Usage.cache_write`` is the total and ``Usage.cache_write_1h`` the
# part written to the 1-hour cache; the rest is priced at the 5-minute rate.

RATES: dict[str, Rates] = {
    "claude-opus-5-5": Rates(
        input=4.0, cache_write=5.0, cache_read=0.20, output=20.0, cache_write_1h=8.0
    ),
    "claude-sonnet-5-5": Rates(
        input=2.0, cache_write=2.5, cache_read=0.10, output=10.0, cache_write_1h=4.0
    ),
    "claude-sonnet-5": Rates(
        input=2.0, cache_write=2.5, cache_read=0.20, output=10.0, cache_write_1h=4.0
    ),
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
        + price_tokens(usage.cache_write - usage.cache_write_1h, rate.cache_write)
        + price_tokens(usage.cache_write_1h, rate.cache_write_1h)
        + price_tokens(usage.cache_read, rate.cache_read)
        + price_tokens(usage.output, rate.output)
    )
    return Money(total, measured=False)


def session_cost(session: Session, rates: dict[str, Rates] = RATES) -> Money | None:
    """Return the recorded session cost, else an estimate, else ``None``.

    The recorded figure is session-level (it can include resumed history and
    calls made outside this file), so use call-level :func:`usage_cost` for
    per-artifact dollars and this only for session totals.

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
