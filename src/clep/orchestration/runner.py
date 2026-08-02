"""The evaluation loop.

ADR-001 selected a task queue with explicit checkpointing, which means the engine
does not know where a run got to and this module must. That is the twenty-two
lines of bespoke state management the ADR counted and accepted, and they are
here rather than scattered: reading the checkpoint, resuming from it, and
advancing it monotonically.

Four things this loop refuses to do, each because the requirement says so:

  * It never scores a sample it could not obtain. A provider failure produces a
    resolution that is not `scored` and carries no number (`REQ-X-8`).
  * It never lets one candidate's failure end another's (`REQ-F-02-6`).
  * It never bills the same work unit twice, even when the same index is
    processed twice after a redelivery (`REQ-N-REL-2`).
  * It never ends a run silently. Every terminal state that is not `complete`
    carries a reason (`REQ-X-1`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from clep.evaluators.sdk import (EvaluatorRegistry, SampleContext, run_evaluator)
from clep.orchestration.repository import RunRepository, sample_key
from clep.providers.gateway import (CandidateInvocation, ProviderGateway)
from clep.providers.port import CompletionRequest


class RunCancelled(Exception):
    """Raised by the cancellation probe to unwind the loop cleanly."""


@dataclass(frozen=True)
class Example:
    id: str
    prompt: str
    expected: str | None = None
    content_digest: str | None = None
    retrieved_context: tuple[str, ...] = ()


@dataclass(frozen=True)
class Candidate:
    id: str
    label: str
    model: str
    endpoint_name: str


@dataclass
class RunOutcome:
    completeness: str
    incomplete_reason: str | None = None
    samples_scored: int = 0
    samples_failed: int = 0
    samples_recorded: int = 0
    samples_skipped_as_duplicate: int = 0
    cost_total: Decimal = Decimal(0)
    unpriced_calls: int = 0
    evaluator_outcomes: int = 0
    resumed_from_index: int = -1
    notes: list[str] = field(default_factory=list)


class RunExecutor:
    def __init__(self, repository: RunRepository, gateway: ProviderGateway,
                 registry: EvaluatorRegistry, *, evaluator_ids: dict[str, str] | None = None,
                 is_cancelled=None, evaluator_timeout_ms: int | None = None):
        self._repo = repository
        self._gateway = gateway
        self._registry = registry
        #: Maps an evaluator's version key to the evaluator_version_id recorded
        #: against each outcome. Supplied by the caller because evaluator
        #: identity is registry data, not something this loop may invent.
        self._evaluator_ids = dict(evaluator_ids or {})
        self._is_cancelled = is_cancelled or (lambda: False)
        self._evaluator_timeout_ms = evaluator_timeout_ms

    def execute(self, run_id: str, examples: list[Example], candidates: list[Candidate],
                *, budget_limit: Decimal | None = None,
                budget_currency: str = "USD",
                integration_tier: str = "output_only") -> RunOutcome:
        resume_from = self._repo.checkpoint(run_id) + 1
        outcome = RunOutcome(completeness="complete", resumed_from_index=resume_from)
        if resume_from:
            outcome.notes.append(
                f"resumed at example index {resume_from}; the {resume_from} "
                f"already completed were not recomputed")
        self._repo.mark_running(run_id)

        spent = self._repo.cost_total(run_id)[0] or Decimal(0)

        for index in range(resume_from, len(examples)):
            if self._is_cancelled():
                outcome.completeness = "cancelled"
                outcome.incomplete_reason = (
                    f"cancelled after {index} of {len(examples)} examples; the "
                    f"record is deliberately incomplete rather than partial-looking")
                break
            if budget_limit is not None and spent >= budget_limit:
                outcome.completeness = "exhausted"
                outcome.incomplete_reason = (
                    f"budget of {budget_limit} {budget_currency} reached after "
                    f"{index} of {len(examples)} examples")
                break

            example = examples[index]
            spent += self._process_example(run_id, index, example, candidates,
                                           integration_tier, outcome, budget_currency)
            # Advance only after every candidate for this index is durable.
            # Advancing earlier would let a crash skip work that never happened.
            self._repo.advance_checkpoint(run_id, index)

        if outcome.completeness == "complete" and outcome.samples_failed:
            outcome.completeness = "partial"
            outcome.incomplete_reason = (
                f"{outcome.samples_failed} sample(s) did not produce a score; "
                f"a partial run is never reported as complete")

        outcome.cost_total = self._repo.cost_total(run_id)[0] or Decimal(0)
        self._repo.finish_run(run_id, outcome.completeness, outcome.incomplete_reason)
        return outcome

    # ------------------------------------------------------------------ inner
    def _process_example(self, run_id, index, example, candidates, tier,
                         outcome, currency) -> Decimal:
        invocations = [
            CandidateInvocation(
                candidate_label=c.label, endpoint_name=c.endpoint_name,
                request=CompletionRequest(model=c.model, prompt=example.prompt))
            for c in candidates]
        results = self._gateway.invoke_all(invocations)
        by_label = {c.label: c for c in candidates}
        added = Decimal(0)

        for candidate_outcome in results:
            candidate = by_label[candidate_outcome.candidate_label]
            key = sample_key(run_id, candidate.label, example.id)

            if candidate_outcome.succeeded:
                resolution, score, failure_kind = self._score(
                    example, candidate_outcome.result.text, tier, outcome)
            else:
                # No score. Not zero. The distinction is the requirement.
                resolution = "failed"
                score = None
                failure_kind = candidate_outcome.failure_kind

            sample_id, inserted = self._repo.record_sample(
                run_id=run_id, candidate_id=candidate.id,
                candidate_label=candidate.label, example_id=example.id,
                sample_index=index, resolution=resolution, score=score,
                failure_kind=failure_kind,
                example_content_digest=example.content_digest)

            if inserted:
                outcome.samples_recorded += 1
                if resolution == "scored":
                    outcome.samples_scored += 1
                else:
                    outcome.samples_failed += 1
                self._record_evaluators(sample_id, example, candidate_outcome, tier,
                                        outcome)
            else:
                # Redelivery. The work was already recorded; recording it again
                # is what would double-bill, so nothing further happens here.
                outcome.samples_skipped_as_duplicate += 1
                continue

            if candidate_outcome.unpriced:
                outcome.unpriced_calls += 1
            elif candidate_outcome.cost is not None:
                created = self._repo.record_cost(
                    run_id=run_id, sample_id=sample_id, sample_key_value=key,
                    prompt_tokens=candidate_outcome.result.usage.prompt_tokens,
                    completion_tokens=candidate_outcome.result.usage.completion_tokens,
                    amount=candidate_outcome.cost.amount,
                    currency=candidate_outcome.cost.currency)
                if created:
                    added += candidate_outcome.cost.amount
        return added

    def _score(self, example, output, tier, outcome):
        """The sample's own resolution, taken from its evaluators.

        Scored only when at least one evaluator produced a number. An evaluator
        set that all abstained leaves the sample abstained, because averaging
        over nothing is not a score.
        """
        scores = []
        for key in self._registry.keys():
            registration = self._registry.get(key)
            result = run_evaluator(
                registration,
                SampleContext(example_id=example.id, prompt=example.prompt,
                              output=output, expected=example.expected,
                              retrieved_context=example.retrieved_context,
                              integration_tier=tier),
                timeout_ms=self._evaluator_timeout_ms)
            if result.resolution == "scored":
                scores.append(result.score)
        if not scores:
            return "abstained", None, None
        return "scored", sum(scores) / Decimal(len(scores)), None

    def _record_evaluators(self, sample_id, example, candidate_outcome, tier, outcome):
        if not candidate_outcome.succeeded:
            return
        for key in self._registry.keys():
            registration = self._registry.get(key)
            evaluator_version_id = self._evaluator_ids.get(key)
            if evaluator_version_id is None:
                continue
            result = run_evaluator(
                registration,
                SampleContext(example_id=example.id, prompt=example.prompt,
                              output=candidate_outcome.result.text,
                              expected=example.expected,
                              retrieved_context=example.retrieved_context,
                              integration_tier=tier),
                timeout_ms=self._evaluator_timeout_ms)
            self._repo.record_evaluator_outcome(
                sample_id=sample_id, evaluator_version_id=evaluator_version_id,
                resolution=result.resolution, score=result.score,
                unavailable_reason=result.unavailable_reason,
                duration_ms=result.duration_ms)
            outcome.evaluator_outcomes += 1
