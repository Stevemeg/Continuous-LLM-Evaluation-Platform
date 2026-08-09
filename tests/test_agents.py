"""The bounds are the product. These tests try to get past them."""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.agents.sdk import (ACCEPTED, BUDGET_EXHAUSTED, DEADLINE_EXCEEDED,
                             FAILED, ITERATIONS_EXHAUSTED, NO_PROGRESS, Bounds,
                             Proposal, Reasoning, ReasoningError, run_bounded)


def bounds(**over):
    base = dict(max_iterations=5, budget=Decimal("1.00"), timeout_ms=10_000)
    base.update(over)
    return Bounds(**base)


class Clock:
    """A clock the test advances, so a timeout test does not take a timeout."""

    def __init__(self):
        self.seconds = 0.0

    def __call__(self):
        return self.seconds


def accept_always(_value):
    return ""


def reject_always(_value):
    return "not good enough"


# ------------------------------------------------------------------ the bounds

def test_a_loop_without_a_stated_limit_cannot_be_constructed():
    with pytest.raises(ReasoningError):
        Bounds(max_iterations=0, budget=Decimal("1"), timeout_ms=1000)
    with pytest.raises(ReasoningError):
        Bounds(max_iterations=1, budget=Decimal("-1"), timeout_ms=1000)
    with pytest.raises(ReasoningError):
        Bounds(max_iterations=1, budget=Decimal("1"), timeout_ms=0)


def test_bounds_has_no_defaults():
    """ADR-002 and REQ-F-AG-5. A default bound is a bound nobody chose."""
    import inspect
    signature = inspect.signature(Bounds.__init__)
    for name in ("max_iterations", "budget", "timeout_ms"):
        assert signature.parameters[name].default is inspect.Parameter.empty


def test_iterations_are_exhausted_rather_than_exceeded():
    calls = []

    def step(index, critique):
        calls.append(index)
        return Proposal(value=f"draft-{index}", cost=Decimal(0))

    result = run_bounded(step, reject_always, bounds(max_iterations=3))
    assert result.state == ITERATIONS_EXHAUSTED
    assert calls == [0, 1, 2]
    assert result.iterations == 3


def test_the_budget_stops_the_loop_before_it_spends_what_it_has_not_got():
    def step(index, critique):
        return Proposal(value=f"draft-{index}", cost=Decimal("0.40"))

    result = run_bounded(step, reject_always,
                         bounds(max_iterations=10, budget=Decimal("1.00")))
    assert result.state == BUDGET_EXHAUSTED
    # Three attempts spend 1.20 against a budget of 1.00; the fourth is refused
    # before it is started rather than after it has been paid for.
    assert result.iterations == 3
    assert result.cost == Decimal("1.20")


def test_the_deadline_stops_the_loop():
    clock = Clock()

    def step(index, critique):
        clock.seconds += 2.0
        return Proposal(value=f"draft-{index}")

    result = run_bounded(step, reject_always, bounds(max_iterations=10,
                                                     timeout_ms=5000),
                         clock=clock)
    assert result.state == DEADLINE_EXCEEDED
    assert result.iterations == 3


def test_a_bound_that_stops_the_loop_produces_no_value():
    """REQ-X-2 applied to reasoning: an exhausted bound is not a half-answer."""
    for state_bounds in (bounds(max_iterations=1),
                         bounds(budget=Decimal(0)),
                         bounds(timeout_ms=1)):
        result = run_bounded(lambda i, c: Proposal(value="x", cost=Decimal("5")),
                             reject_always, state_bounds)
        assert result.value is None
        assert result.state != ACCEPTED
        assert result.stopped_because


def test_a_reasoning_result_cannot_claim_acceptance_without_a_value():
    with pytest.raises(ReasoningError):
        Reasoning(state=ACCEPTED, value=None, attempts=(), cost=Decimal(0),
                  duration_ms=0, stopped_because="x")
    with pytest.raises(ReasoningError):
        Reasoning(state=ITERATIONS_EXHAUSTED, value="something", attempts=(),
                  cost=Decimal(0), duration_ms=0, stopped_because="x")


# ------------------------------------------------------------------- acceptance

def test_an_accepted_result_stops_immediately_and_carries_the_value():
    calls = []

    def step(index, critique):
        calls.append((index, critique))
        return Proposal(value=index, cost=Decimal("0.01"))

    result = run_bounded(step, lambda v: "" if v == 2 else "keep going",
                         bounds(max_iterations=9))
    assert result.state == ACCEPTED
    assert result.value == 2
    assert len(calls) == 3
    assert result.cost == Decimal("0.03")


def test_the_critique_reaches_the_next_attempt():
    seen = []

    def step(index, critique):
        seen.append(critique)
        return Proposal(value=index)

    run_bounded(step, lambda v: "" if v == 1 else f"{v} is wrong",
                bounds(max_iterations=4))
    assert seen == [None, "0 is wrong"]


# --------------------------------------------------------------------- history

def test_every_iteration_is_retained_including_the_rejected_ones():
    """REQ-F-AG-5. The rejected drafts are the history worth having."""
    result = run_bounded(lambda i, c: Proposal(value=f"draft-{i}"),
                         lambda v: "" if v == "draft-2" else "no",
                         bounds(max_iterations=5))
    assert [a.index for a in result.attempts] == [0, 1, 2]
    assert [a.accepted for a in result.attempts] == [False, False, True]
    assert [a.value for a in result.attempts] == ["draft-0", "draft-1", "draft-2"]
    assert result.attempts[0].critique == "no"


def test_a_rejection_is_never_recorded_without_its_reason():
    result = run_bounded(lambda i, c: Proposal(value=i), reject_always,
                         bounds(max_iterations=3))
    for attempt in result.attempts:
        assert attempt.accepted is False
        assert attempt.critique


# ------------------------------------------------------------------- failures

def test_a_step_that_raises_becomes_history_rather_than_a_crash():
    received = []

    def step(index, critique):
        received.append(critique)
        if index == 0:
            raise RuntimeError("the model hung up")
        return Proposal(value="recovered")

    result = run_bounded(step, accept_always, bounds(max_iterations=3))
    assert result.state == ACCEPTED
    assert result.attempts[0].error.startswith("RuntimeError")
    assert result.attempts[0].value is None
    # The next attempt is told what went wrong rather than being asked again
    # into the same silence.
    assert "RuntimeError" in received[1]


def test_a_loop_that_never_produced_anything_says_so():
    def step(index, critique):
        raise ValueError("nope")

    result = run_bounded(step, accept_always, bounds(max_iterations=3))
    assert result.state == FAILED
    assert result.iterations == 3


def test_a_step_returning_the_wrong_type_is_a_programming_error():
    with pytest.raises(ReasoningError):
        run_bounded(lambda i, c: "just a string", accept_always, bounds())


# --------------------------------------------------------- termination rules

def test_repeating_a_proposal_terminates_rather_than_paying_to_confirm_it():
    calls = []

    def step(index, critique):
        calls.append(index)
        return Proposal(value="the same thing", cost=Decimal("0.10"))

    result = run_bounded(step, reject_always, bounds(max_iterations=10))
    assert result.state == NO_PROGRESS
    assert len(calls) == 2
    assert result.cost == Decimal("0.20")


def test_no_progress_can_only_stop_a_loop_earlier_than_the_required_bounds():
    """It must never let a loop run past max_iterations."""
    result = run_bounded(lambda i, c: Proposal(value=i), reject_always,
                         bounds(max_iterations=2))
    assert result.state == ITERATIONS_EXHAUSTED
    assert result.iterations == 2


def test_a_custom_fingerprint_decides_what_counts_as_the_same_proposal():
    class Draft:
        def __init__(self, n):
            self.n = n

    result = run_bounded(lambda i, c: Proposal(value=Draft(i)), reject_always,
                         bounds(max_iterations=4), fingerprint=lambda d: "constant")
    assert result.state == NO_PROGRESS


# ------------------------------------------------------------- offline by design

def test_nothing_in_the_module_reaches_a_provider():
    """REQ-F-AG-8. The model is the caller's callable, so there is nothing to stub."""
    import clep.agents.sdk as module
    source = open(module.__file__, encoding="utf-8").read()
    for forbidden in ("import requests", "httpx", "ProviderGateway", "urllib"):
        assert forbidden not in source
