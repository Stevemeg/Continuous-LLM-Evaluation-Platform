"""Bounded reasoning — the only control flow a reasoning component may use.

ADR-002 declined a general agent framework and accepted the obligation that
comes with it: the bounds `REQ-F-AG-5` requires are project code, and they must
be provable. This module is that code. It holds no domain knowledge — it does
not know what a plan is or what a judge is — because a bound that only applies
to the planner is a bound the next reasoning component will not have.

Four properties are the whole point.

**A bound that is not supplied is not a bound.** `Bounds` has no defaults. A
default iteration limit is a number nobody chose governing every loop in the
product, which is the arbitrary constant canonical §25 rejects. A loop
constructed without saying where it stops is an error at construction rather
than a surprise in production.

**Every iteration is retained, including the rejected ones.** `REQ-F-AG-5` asks
for the full history of each iteration, and the rejected iterations are the
history worth having: they record what the component tried and why the critique
refused it. A loop that keeps only its final answer cannot be audited, only
believed.

**Exhausting a bound is an outcome, never an answer.** A `Reasoning` that did not
accept carries no value at all. `REQ-X-2` forbids representing a failed
evaluation as a number, and a plan that ran out of budget half-drafted is the
same class of thing: usable-looking, and not a result.

**Nothing here calls a model.** The step is a callable the caller supplies, which
is what makes `REQ-F-AG-8` — reasoning exercised in tests without live model
calls — a property of the design rather than a convention the tests observe.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

#: Why a bounded loop stopped. Exactly one of these is a success.
ACCEPTED = "accepted"
ITERATIONS_EXHAUSTED = "iterations_exhausted"
BUDGET_EXHAUSTED = "budget_exhausted"
DEADLINE_EXCEEDED = "deadline_exceeded"
NO_PROGRESS = "no_progress"
FAILED = "failed"
STATES = (ACCEPTED, ITERATIONS_EXHAUSTED, BUDGET_EXHAUSTED, DEADLINE_EXCEEDED,
          NO_PROGRESS, FAILED)


class ReasoningError(Exception):
    """Raised by the harness itself, never by a step's own failure."""


@dataclass(frozen=True)
class Bounds:
    """Where a reasoning loop stops. Every field is required.

    `REQ-F-AG-5` names three bounds — a maximum iteration count, a budget and a
    timeout — and this is the only way to obtain them. There is no default: a
    loop whose caller did not say where it ends is a programming error the
    constructor refuses, not a loop that runs until something else notices.
    """
    max_iterations: int
    budget: Decimal
    timeout_ms: int

    def __post_init__(self):
        if self.max_iterations < 1:
            raise ReasoningError(
                "a loop that may not iterate once cannot produce anything; "
                "state a maximum of at least one iteration")
        if self.budget < 0:
            raise ReasoningError("a negative budget is not a bound")
        if self.timeout_ms < 1:
            raise ReasoningError("a timeout must leave room for one attempt")


@dataclass(frozen=True)
class Proposal(Generic[T]):
    """What one iteration produced, and what it cost to produce."""
    value: T
    cost: Decimal = Decimal(0)


@dataclass(frozen=True)
class Attempt(Generic[T]):
    """One iteration, retained whether or not it was accepted."""
    index: int
    value: T | None
    accepted: bool
    critique: str
    cost: Decimal
    duration_ms: int
    error: str | None = None


@dataclass(frozen=True)
class Reasoning(Generic[T]):
    state: str
    value: T | None
    attempts: tuple
    cost: Decimal
    duration_ms: int
    stopped_because: str
    #: The bounds this ran under. Carried in the result rather than looked up
    #: later, because "it stayed inside its budget" is only checkable against
    #: the budget that actually applied.
    bounds: Bounds | None = None

    def __post_init__(self):
        if self.state not in STATES:
            raise ReasoningError(f"unknown reasoning state {self.state!r}")
        if (self.state == ACCEPTED) != (self.value is not None):
            raise ReasoningError(
                "a value is carried exactly when the loop accepted one; an "
                "exhausted bound is an outcome, not a half-finished answer")
        if not self.stopped_because:
            raise ReasoningError("a loop that stopped must say why")

    @property
    def iterations(self) -> int:
        return len(self.attempts)


def run_bounded(step: Callable[[int, str | None], Proposal],
                critic: Callable[[object], str],
                bounds: Bounds,
                *,
                fingerprint: Callable[[object], str] = repr,
                clock: Callable[[], float] = time.monotonic) -> Reasoning:
    """Iterate `step` until the critic accepts or a bound stops it.

    `step(index, previous_critique)` returns a `Proposal`. `critic(value)`
    returns the reason it is unacceptable, or an empty string to accept — the
    empty string means acceptance rather than a separate boolean so that a
    rejection is never recorded without the reason for it.

    The bounds are tested **before** each attempt, not after. Testing afterwards
    would let a loop start an iteration it had no budget for and then discard the
    work, which spends real money to produce nothing.

    `no_progress` is a fourth termination rule beyond the three `REQ-F-AG-5`
    names: a step that proposes the same thing twice running will keep proposing
    it, and paying for the remaining iterations to confirm that is not diligence.
    It can only stop a loop earlier than the required bounds, never later.
    """
    started = clock()
    spent = Decimal(0)
    attempts: list[Attempt] = []
    critique: str | None = None
    previous_fingerprint: str | None = None

    def elapsed_ms() -> int:
        return int((clock() - started) * 1000)

    def finish(state: str, value, why: str) -> Reasoning:
        return Reasoning(state=state, value=value, attempts=tuple(attempts),
                         cost=spent, duration_ms=elapsed_ms(),
                         stopped_because=why, bounds=bounds)

    for index in range(bounds.max_iterations):
        if elapsed_ms() >= bounds.timeout_ms:
            return finish(DEADLINE_EXCEEDED, None,
                          f"the {bounds.timeout_ms} ms timeout elapsed after "
                          f"{len(attempts)} iteration(s)")
        if spent >= bounds.budget:
            return finish(BUDGET_EXHAUSTED, None,
                          f"the budget of {bounds.budget} was spent after "
                          f"{len(attempts)} iteration(s)")

        attempt_started = clock()
        try:
            proposal = step(index, critique)
        except Exception as e:  # a step's failure is history, not a crash
            attempts.append(Attempt(index=index, value=None, accepted=False,
                                    critique="", cost=Decimal(0),
                                    duration_ms=int((clock() - attempt_started) * 1000),
                                    error=f"{type(e).__name__}: {e}"[:500]))
            critique = (f"the previous attempt raised {type(e).__name__}; "
                        f"produce a result rather than an exception")
            continue
        if not isinstance(proposal, Proposal):
            raise ReasoningError(
                f"a step must return a Proposal, not {type(proposal).__name__}")

        spent += proposal.cost
        reason = critic(proposal.value)
        accepted = reason == ""
        attempts.append(Attempt(index=index, value=proposal.value, accepted=accepted,
                                critique=reason, cost=proposal.cost,
                                duration_ms=int((clock() - attempt_started) * 1000)))
        if accepted:
            return finish(ACCEPTED, proposal.value,
                          f"accepted at iteration {index}")

        current = fingerprint(proposal.value)
        if current == previous_fingerprint:
            return finish(NO_PROGRESS, None,
                          f"iteration {index} proposed what iteration {index - 1} "
                          f"proposed; a further attempt cannot differ")
        previous_fingerprint = current
        critique = reason

    if attempts and all(a.value is None for a in attempts):
        return finish(FAILED, None,
                      f"every one of the {len(attempts)} attempts raised rather "
                      f"than proposing anything")
    return finish(ITERATIONS_EXHAUSTED, None,
                  f"the maximum of {bounds.max_iterations} iteration(s) was "
                  f"reached without an acceptable result")
