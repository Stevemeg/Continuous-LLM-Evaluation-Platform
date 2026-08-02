"""Evaluator SDK: the contract, the tier boundary, and plugin containment."""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.evaluators.builtin import (ContainsExpected, ContextGrounding,
                                     ExactMatch, default_registry)
from clep.evaluators.sdk import (EvaluatorError, EvaluatorOutcome,
                                 EvaluatorRegistry, SampleContext, abstained,
                                 run_evaluator, scored, tier_permits)

SAMPLE = SampleContext(example_id="e1", prompt="p", output="Paris",
                       expected="Paris", integration_tier="output_only")


def _reg(evaluator):
    registry = EvaluatorRegistry()
    return registry.register(evaluator)


# ---------------------------------------------------------------- the contract
def test_only_a_scored_resolution_carries_a_number():
    assert scored(1).score == Decimal("1")
    with pytest.raises(EvaluatorError):
        EvaluatorOutcome("scored", score=None)
    with pytest.raises(EvaluatorError):
        EvaluatorOutcome("failed", score=Decimal("0"))


def test_an_unscored_sample_can_never_be_read_as_zero():
    """REQ-X-8. A zero drags an average down and looks like a regression; an
    abstention does not, and the difference must be unrepresentable rather than
    merely discouraged."""
    outcome = abstained("nothing to compare against")
    assert outcome.score is None
    assert outcome.resolution != "scored"


def test_scores_are_bounded_and_exact():
    assert scored("0.5").score == Decimal("0.5")
    with pytest.raises(EvaluatorError):
        scored(1.5)
    with pytest.raises(EvaluatorError):
        scored(-0.1)


def test_an_unavailable_evaluator_must_say_why():
    with pytest.raises(EvaluatorError):
        EvaluatorOutcome("unavailable")
    assert EvaluatorOutcome("unavailable", unavailable_reason="tier").unavailable_reason


# ------------------------------------------------------------------- registry
def test_the_same_name_and_version_cannot_be_registered_twice():
    """Canonical §25 rejects unversioned evaluators. Two behaviours under one
    identity would silently change what past runs measured."""
    registry = EvaluatorRegistry()
    registry.register(ExactMatch())
    with pytest.raises(EvaluatorError, match="already registered"):
        registry.register(ExactMatch())


def test_registration_requires_a_declared_version_and_tier():
    class Nameless:
        name = "x"
        version = ""
        requires_tier = "output_only"

        def evaluate(self, sample):
            return abstained()

    with pytest.raises(EvaluatorError, match="version"):
        EvaluatorRegistry().register(Nameless())


def test_the_default_registry_carries_the_builtins():
    registry = default_registry()
    assert registry.keys() == ["contains_expected@1.0.0", "context_grounding@1.0.0",
                               "exact_match@1.0.0"]


# ----------------------------------------------------------------- tier gating
def test_tier_ordering():
    assert tier_permits("output_only", "full")
    assert tier_permits("partial", "partial")
    assert not tier_permits("full", "partial")
    assert not tier_permits("partial", "output_only")


def test_an_evaluator_that_needs_more_context_is_unavailable_not_approximated():
    """REQ-F-03-4. The plugin is not called at all, so it cannot draw a
    conclusion from an empty context it was never given."""
    called = []

    class Nosy(ContextGrounding):
        def evaluate(self, sample):
            called.append(sample)
            return super().evaluate(sample)

    outcome = run_evaluator(_reg(Nosy()), SAMPLE)
    assert outcome.resolution == "unavailable"
    assert outcome.score is None
    assert "partial" in outcome.unavailable_reason
    assert called == [], "the evaluator must not run below its required tier"


def test_the_same_evaluator_runs_when_the_tier_permits():
    sample = SampleContext(example_id="e", prompt="p", output="o",
                           expected="alpha beta",
                           retrieved_context=("alpha only",),
                           integration_tier="partial")
    outcome = run_evaluator(_reg(ContextGrounding()), sample)
    assert outcome.resolution == "scored"
    assert outcome.score == Decimal("0.5")


# ------------------------------------------------------------- plugin failures
def test_a_plugin_that_raises_produces_a_failed_outcome_not_a_dead_run():
    class Exploding:
        name, version, requires_tier = "boom", "1.0.0", "output_only"

        def evaluate(self, sample):
            raise ZeroDivisionError("no")

    outcome = run_evaluator(_reg(Exploding()), SAMPLE)
    assert outcome.resolution == "failed"
    assert "ZeroDivisionError" in outcome.detail
    assert outcome.score is None


def test_a_plugin_returning_the_wrong_type_is_a_failure_not_a_crash():
    class Wrong:
        name, version, requires_tier = "wrong", "1.0.0", "output_only"

        def evaluate(self, sample):
            return 0.9

    outcome = run_evaluator(_reg(Wrong()), SAMPLE)
    assert outcome.resolution == "failed"
    assert "not an EvaluatorOutcome" in outcome.detail


def test_a_plugin_cannot_reach_anything_but_its_sample():
    """The isolation ADR-006 deferred to this phase. The context carries the
    sample and nothing else - no store, no gateway, no configuration."""
    seen = {}

    class Curious:
        name, version, requires_tier = "curious", "1.0.0", "output_only"

        def evaluate(self, sample):
            seen["fields"] = sorted(vars(sample))
            return abstained()

    run_evaluator(_reg(Curious()), SAMPLE)
    assert seen["fields"] == ["example_id", "expected", "integration_tier",
                              "output", "prompt", "retrieved_context", "trajectory"]


def test_an_evaluator_that_overruns_its_budget_is_timed_out_not_accepted_late():
    import time

    class Slow:
        name, version, requires_tier = "slow", "1.0.0", "output_only"

        def evaluate(self, sample):
            time.sleep(0.05)
            return scored(1)

    outcome = run_evaluator(_reg(Slow()), SAMPLE, timeout_ms=1)
    assert outcome.resolution == "timed_out"
    assert outcome.score is None


# ------------------------------------------------------------- determinism
def test_the_builtins_are_deterministic():
    """REQ-N-MAINT-2 needs deterministic fixtures, which is impossible if the
    evaluators underneath them are not."""
    for evaluator in (ExactMatch(), ContainsExpected()):
        registration = _reg(evaluator)
        outcomes = [run_evaluator(registration, SAMPLE) for _ in range(20)]
        assert len({(o.resolution, o.score) for o in outcomes}) == 1


def test_exact_match_abstains_rather_than_scoring_zero_without_an_expectation():
    sample = SampleContext(example_id="e", prompt="p", output="anything")
    outcome = run_evaluator(_reg(ExactMatch()), sample)
    assert outcome.resolution == "abstained"
    assert outcome.score is None
