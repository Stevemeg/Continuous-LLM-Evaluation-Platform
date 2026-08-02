"""Built-in evaluators.

Three, deliberately unglamorous. They exist to prove the SDK's contract holds
under real registration and execution, and to give the harness something
deterministic to run. Canonical §25 rejects a thin wrapper around third-party
evaluation libraries as an anti-pattern, so nothing here wraps one.

Every one is deterministic: the same sample yields the same outcome, always.
`REQ-N-MAINT-2` requires deterministic fixtures, and an evaluator that is not
itself deterministic makes that impossible to achieve downstream.
"""
from __future__ import annotations

from decimal import Decimal

from clep.evaluators.sdk import (EvaluatorOutcome, EvaluatorRegistry,
                                 SampleContext, abstained, scored)


class ExactMatch:
    """Scores 1 when the output equals the expectation after trivial cleanup.

    Abstains when there is no expectation. It does not score 0 — an example with
    nothing to compare against has not been failed, and `REQ-X-8` requires that
    distinction to survive into the average.
    """
    name = "exact_match"
    version = "1.0.0"
    requires_tier = "output_only"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        if sample.expected is None:
            return abstained("no expected output on this example")
        return scored(1 if sample.output.strip() == sample.expected.strip() else 0)


class ContainsExpected:
    """Substring containment, case-insensitive. A weak signal, honestly named."""
    name = "contains_expected"
    version = "1.0.0"
    requires_tier = "output_only"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        if sample.expected is None:
            return abstained("no expected output on this example")
        return scored(1 if sample.expected.strip().lower()
                      in sample.output.lower() else 0)


class ContextGrounding:
    """Fraction of expected tokens that appear in the retrieved context.

    Requires the `partial` tier, because it needs retrieved context. Asked to run
    without one it is reported `unavailable` by the SDK before it is called at
    all — which is the behaviour `REQ-F-03-4` demands, and the reason this
    evaluator is here rather than a fourth output-only one.
    """
    name = "context_grounding"
    version = "1.0.0"
    requires_tier = "partial"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        if sample.expected is None:
            return abstained("no expected output on this example")
        wanted = {w for w in sample.expected.lower().split() if w}
        if not wanted:
            return abstained("expected output contains no comparable tokens")
        haystack = " ".join(sample.retrieved_context).lower()
        found = sum(1 for w in wanted if w in haystack)
        return scored(Decimal(found) / Decimal(len(wanted)))


def default_registry() -> EvaluatorRegistry:
    registry = EvaluatorRegistry()
    for evaluator in (ExactMatch(), ContainsExpected(), ContextGrounding()):
        registry.register(evaluator, is_builtin=True)
    return registry
