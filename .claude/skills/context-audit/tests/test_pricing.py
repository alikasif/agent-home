"""Tests for pricing.py, using injected rates so no real price is asserted."""

from __future__ import annotations

import pytest
from pricing import Money, Rates, price_tokens, session_cost, usage_cost
from transcripts import Call, Session, Usage

MILLION = 1_000_000
TEST_RATES = {
    "m": Rates(input=1, cache_write=2, cache_read=0.5, output=4, cache_write_1h=3)
}


def make_session(calls: list[Call], cost_usd: float | None) -> Session:
    return Session(
        session_id="s",
        project="p",
        version="2.1.330",
        shapes={},
        calls=calls,
        skill_listings=[],
        agent_added=[],
        agent_removed=[],
        instructions=[],
        invocations=[],
        tool_results=[],
        cost_usd=cost_usd,
    )


def make_call(model: str, usage: Usage) -> Call:
    return Call(timestamp="t", model=model, shape_hash=None, usage=usage)


def test_usage_cost_applies_each_rate() -> None:
    usage = Usage(MILLION, MILLION, MILLION, MILLION)
    assert usage_cost(usage, "m", TEST_RATES) == Money(7.5, measured=False)


def test_usage_cost_prices_one_hour_writes_at_their_own_rate() -> None:
    # 1M cache-write tokens, 400k of them 1-hour: 600k x $2 + 400k x $3 = $2.40.
    usage = Usage(0, MILLION, 0, 0, cache_write_1h=400_000)
    cost = usage_cost(usage, "m", TEST_RATES)
    assert cost is not None
    assert cost.value == pytest.approx(2.4)
    assert cost.measured is False


def test_unknown_model_returns_none() -> None:
    assert usage_cost(Usage(1, 1, 1, 1), "nope", TEST_RATES) is None


def test_price_tokens() -> None:
    assert price_tokens(500_000, 4.0) == pytest.approx(2.0)


def test_session_cost_prefers_recorded_cost_state() -> None:
    calls = [make_call("m", Usage(MILLION, 0, 0, 0))]
    assert session_cost(make_session(calls, 9.0), TEST_RATES) == Money(9.0, True)


def test_session_cost_falls_back_to_estimate() -> None:
    calls = [
        make_call("m", Usage(MILLION, 0, 0, 0)),
        make_call("m", Usage(0, 0, 0, MILLION)),
    ]
    assert session_cost(make_session(calls, None), TEST_RATES) == Money(5.0, False)


def test_session_cost_none_when_any_model_unpriced() -> None:
    calls = [make_call("m", Usage(1, 0, 0, 0)), make_call("nope", Usage(1, 0, 0, 0))]
    assert session_cost(make_session(calls, None), TEST_RATES) is None


def test_session_cost_zero_calls_is_none() -> None:
    assert session_cost(make_session([], None), TEST_RATES) is None


def test_shipped_rates_are_ordered_sanely() -> None:
    from pricing import RATES

    assert RATES
    for rate in RATES.values():
        assert 0 < rate.cache_read < rate.input < rate.cache_write < rate.cache_write_1h
        assert rate.cache_write_1h < rate.output
