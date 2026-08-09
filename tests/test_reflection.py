"""Regeneration retries an unreadable reply and nothing else."""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.agents.sdk import Bounds
from clep.evaluators.sdk import SampleContext
from clep.judges.reflection import is_unreadable, regenerate_unreadable
from clep.judges.sdk import JudgeVersion, Vote
from clep.providers.gateway import Price, PriceBook, ProviderGateway
from clep.providers.port import CompletionResult, ProviderOutage, Usage

PRICES = PriceBook({"judge-a": Price(Decimal("0.001"), Decimal("0.002"))})
JUDGE = JudgeVersion(slug="helpfulness", version="1", model="judge-a",
                     endpoint_name="e", rubric="Score it.")
SAMPLE = SampleContext(example_id="x", prompt="p", output="o")


class Scripted:
    def __init__(self, *replies, failure=None):
        self.replies = list(replies)
        self.failure = failure
        self.calls = 0

    def complete(self, request):
        self.calls += 1
        if self.failure:
            raise self.failure
        reply = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        return CompletionResult(text=reply, model=request.model,
                                usage=Usage(10, 5, 15), endpoint_name="e",
                                endpoint_kind="hosted")


def gateway(adapter):
    return ProviderGateway({"e": adapter}, PRICES)


def bounds(**over):
    base = dict(max_iterations=3, budget=Decimal("1"), timeout_ms=5000)
    base.update(over)
    return Bounds(**base)


def test_an_unreadable_reply_is_regenerated():
    adapter = Scripted("I think it's pretty good", "SCORE: 0.8")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter), bounds())
    assert result.state == "accepted"
    assert result.value.resolution == "scored"
    assert result.value.score == Decimal("0.8")
    assert adapter.calls == 2


def test_the_critique_tells_the_judge_what_was_wrong():
    adapter = Scripted("GATE: pass", "SCORE: 0.5")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter), bounds())
    assert "could not be read" in result.attempts[0].critique


def test_a_scored_reply_is_never_re_asked():
    """ADR-004 D-4. A judge that answered has answered."""
    adapter = Scripted("SCORE: 0.1")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter), bounds())
    assert adapter.calls == 1
    assert result.value.score == Decimal("0.1")


def test_an_abstention_is_an_answer_and_is_not_re_asked():
    adapter = Scripted("ABSTAIN: the output is empty")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter), bounds())
    assert adapter.calls == 1
    assert result.value.resolution == "abstained"


def test_a_low_score_is_not_a_reason_to_ask_again():
    """The failure this module exists to make impossible: regenerating until
    the number is one somebody likes."""
    adapter = Scripted("SCORE: 0.0")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter), bounds())
    assert adapter.calls == 1
    assert result.value.score == Decimal("0")


def test_a_provider_failure_is_not_regenerated_here():
    """The run loop owns provider retries. Two retry policies multiply."""
    adapter = Scripted(failure=ProviderOutage("down"))
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter), bounds())
    assert adapter.calls == 1
    assert result.value.resolution == "failed"
    assert not is_unreadable(result.value)


def test_regeneration_is_bounded_and_produces_no_vote_when_exhausted():
    adapter = Scripted("nonsense one", "nonsense two", "nonsense three",
                       "nonsense four")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter),
                                   bounds(max_iterations=3))
    assert result.state == "iterations_exhausted"
    assert result.value is None
    assert adapter.calls == 3
    assert len(result.attempts) == 3


def test_an_identical_unreadable_reply_stops_the_loop_early():
    adapter = Scripted("the same nonsense")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter),
                                   bounds(max_iterations=5))
    assert result.state == "no_progress"
    assert adapter.calls == 2


def test_every_attempt_costs_the_budget_whether_readable_or_not():
    adapter = Scripted("nonsense", "nonsense two", "SCORE: 0.5")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter),
                                   bounds(max_iterations=5))
    assert result.state == "accepted"
    assert result.cost == Decimal("0.00002") * 3


def test_the_budget_stops_regeneration():
    adapter = Scripted("nonsense one", "nonsense two", "nonsense three")
    result = regenerate_unreadable(JUDGE, SAMPLE, gateway(adapter),
                                   bounds(max_iterations=9,
                                          budget=Decimal("0.00003")))
    assert result.state == "budget_exhausted"
    assert result.value is None


def test_the_unreadable_test_is_about_the_reply_not_the_score():
    readable = Vote(judge=JUDGE, resolution="scored", score=Decimal("0"))
    outage = Vote(judge=JUDGE, resolution="failed",
                  detail="provider_outage: endpoint down")
    unreadable = Vote(judge=JUDGE, resolution="failed",
                      detail="unreadable reply: 'GATE: pass'")
    assert is_unreadable(readable) is False
    assert is_unreadable(outage) is False
    assert is_unreadable(unreadable) is True


def test_nothing_here_can_see_the_other_judges():
    """Agreement is a property of the ensemble, and is not visible from inside
    one judge — so this module cannot regenerate on it even by mistake."""
    import inspect
    parameters = set(inspect.signature(regenerate_unreadable).parameters)
    assert parameters == {"judge", "sample", "gateway", "bounds", "timeout_ms",
                          "clock"}
    import clep.judges.reflection as module
    assert not hasattr(module, "Ensemble")
    assert not hasattr(module, "reach_consensus")
