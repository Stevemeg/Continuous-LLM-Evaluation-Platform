"""Bounded self-critique for judgements — and the one thing it must never do.

Canonical §8 asks for critique and regeneration of "invalid or low-confidence
plans/judgments". ADR-004 D-4 forbids retrying until judges agree, because that
manufactures consensus and is the §25 anti-pattern wearing a loop. Both are
requirements on this module, and they are not in conflict once the difference is
named:

  * A judgement that could not be **read** is not a judgement. The model
    answered with prose, or a verdict, or nothing at all, and the narrow parse
    in `sdk` rejected it. Asking again — bounded, with the critique attached —
    is retrying a *malformed response*, and it cannot change what the judge
    thinks because the judge has not yet said anything.

  * A judgement that **was** read is final. Scored, abstained, timed out or
    failed at the provider: each is an answer, and re-asking an answered judge
    is the thing ADR-004 rejected. `regenerate_unreadable` stops the moment a
    reply parses, whatever it parses to.

The rule this module enforces, therefore, is narrower than "retry on failure":
it retries exactly one condition, and the condition is a property of the reply
rather than of the score. `LOW AGREEMENT IS NOT A CONDITION HERE` — agreement is
a property of the ensemble, which is not visible from inside one judge, and the
ensemble escalates rather than regenerating.
"""
from __future__ import annotations

from decimal import Decimal

from clep.agents.sdk import Bounds, Proposal, run_bounded
from clep.judges.sdk import Vote, run_judge

#: The detail prefix the SDK's parse produces for a reply it could not read. A
#: judgement is regenerated on this and on nothing else.
UNREADABLE = "unreadable reply:"


def is_unreadable(vote: Vote) -> bool:
    """Whether the judge said something that was not an answer.

    A provider outage produces `failed` too, and is *not* regenerated here: the
    run loop already owns provider retries and their backoff, and a second retry
    policy layered on top would multiply the attempts nobody budgeted for.
    """
    return vote.resolution == "failed" and vote.detail.startswith(UNREADABLE)


def regenerate_unreadable(judge, sample, gateway, bounds: Bounds, *,
                          timeout_ms: int | None = None, clock=None):
    """Ask one judge, re-asking only while the reply cannot be read.

    Returns the bounded `Reasoning`. The accepted value is a `Vote`; when the
    bounds run out the result carries no vote at all, and the caller records the
    last attempt's resolution rather than inventing one — an exhausted loop is
    not a judgement.
    """
    def step(index, critique):
        vote = run_judge(judge, sample, gateway, timeout_ms=timeout_ms)
        # Cost is real whether or not the reply was readable, and the budget
        # must feel it. A regeneration loop that only counted successful calls
        # would be unbounded in the only currency that matters.
        return Proposal(value=vote, cost=vote.cost or Decimal(0))

    def critic(vote):
        if is_unreadable(vote):
            return (f"the reply could not be read as a score or an abstention "
                    f"({vote.detail}); answer with exactly one line")
        return ""

    kwargs = {"fingerprint": lambda vote: f"{vote.resolution}:{vote.detail}"}
    if clock is not None:
        kwargs["clock"] = clock
    return run_bounded(step, critic, bounds, **kwargs)
