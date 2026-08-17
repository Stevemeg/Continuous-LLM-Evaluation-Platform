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
from clep.security.grants import grant_for


def _invocation_outcome(result) -> str:
    """The evaluator's six resolutions, narrowed to the four a governance
    reader asks about: did it score, did it decline, was it unable, or was it
    refused permission before it ran."""
    if result.resolution == "scored":
        return "scored"
    if result.resolution == "abstained":
        return "abstained"
    if result.resolution == "unavailable" and "was not granted" in \
            (result.unavailable_reason or ""):
        return "refused"
    return "unavailable"
from clep.judges.panel import PanelOutcome
from clep.orchestration.repository import RunRepository, sample_key
from clep.providers.gateway import (CandidateInvocation, ProviderGateway)
from clep.providers.port import CompletionRequest
from clep.identity import new_ulid
from clep.telemetry import NULL_TELEMETRY, correlated, current_id


class RunCancelled(Exception):
    """Raised by the cancellation probe to unwind the loop cleanly."""


@dataclass(frozen=True)
class Example:
    id: str
    prompt: str
    expected: str | None = None
    content_digest: str | None = None
    retrieved_context: tuple[str, ...] = ()
    # Phase 9 inputs, carried by the example so the loop passes them through
    # rather than reconstructing them. `contexts` is the identified form of
    # `retrieved_context`; `required_context_ids` is what the dataset says
    # retrieval was supposed to find, which is the only thing that makes
    # REQ-F-03-6 attribution possible.
    contexts: tuple = ()
    citations: tuple = ()
    required_context_ids: tuple = ()
    trajectory: object | None = None
    tool_schemas: dict | None = None
    expected_tools: tuple = ()


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
    # Phase 10: what the run recorded beyond scores. Counted here so an
    # end-to-end run can be shown to have written its evidence rather than
    # merely computed it.
    retrieval_recorded: int = 0
    trajectory_steps_recorded: int = 0
    panel: "PanelOutcome" = field(default_factory=lambda: PanelOutcome())
    notes: list[str] = field(default_factory=list)


class RunExecutor:
    def __init__(self, repository: RunRepository, gateway: ProviderGateway,
                 registry: EvaluatorRegistry, *, evaluator_ids: dict[str, str] | None = None,
                 is_cancelled=None, evaluator_timeout_ms: int | None = None,
                 analysis_repository=None, judge_panel=None,
                 judge_timeout_ms: int | None = None,
                 evaluator_capabilities=(), telemetry=None):
        #: Injected like every other dependency, and defaulting to the recorder
        #: that records nothing (ADR-022 rules 3 and 7). A run must execute
        #: identically whether or not a deployment configured a backend.
        self._telemetry = telemetry or NULL_TELEMETRY
        #: What a deployment has granted custom evaluators (ADR-006 rule 3).
        #: Empty by default and empty everywhere in this repository: every
        #: built-in reaches for nothing, so deny-by-default costs them nothing
        #: and a widening is a deliberate act with a name attached.
        self._evaluator_capabilities = tuple(evaluator_capabilities)
        self._repo = repository
        self._gateway = gateway
        self._registry = registry
        #: Phase 9 evidence, written as the run executes. Optional, because a
        #: suite with no retrieval and no trajectory has nothing to record.
        self._analysis = analysis_repository
        #: A `JudgePanel` when the suite configures an ensemble. Optional for
        #: the same reason, and injected rather than constructed here so the
        #: loop cannot reach a provider the gateway did not give it.
        self._panel = judge_panel
        self._judge_timeout_ms = judge_timeout_ms
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
        """`REQ-N-OBS-1`'s crossing point, and the reason it is here.

        Execution happens in a worker, in a different process from the request
        that asked for it. The correlation could travel in the job payload; it
        travels in the **run row** instead, because that is the carrier that
        survives a redelivery, a restart, and a resume from checkpoint. A payload
        is gone once the job is retried under a new message; the run is still
        there, and so is its identifier.
        """
        correlation = self._repo.adopt_correlation(run_id,
                                                   current_id() or new_ulid())
        with correlated(correlation):
            return self._execute(run_id, examples, candidates,
                                 budget_limit=budget_limit,
                                 budget_currency=budget_currency,
                                 integration_tier=integration_tier)

    def _execute(self, run_id: str, examples: list[Example],
                 candidates: list[Candidate], *, budget_limit: Decimal | None,
                 budget_currency: str, integration_tier: str) -> RunOutcome:
        self._run_id = run_id
        resume_from = self._repo.checkpoint(run_id) + 1
        outcome = RunOutcome(completeness="complete", resumed_from_index=resume_from)
        if resume_from:
            outcome.notes.append(
                f"resumed at example index {resume_from}; the {resume_from} "
                f"already completed were not recomputed")
        self._repo.mark_running(run_id)
        self._telemetry.observe("clep_run_state_total", 1,
                                execution_state="running")

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
        # Metric class 9. Four of these five values are not success, and a
        # telemetry surface recording only "succeeded" and "failed" is one in
        # which `partial`, `exhausted`, `cancelled` and `rejected` are invisible
        # — which is `REQ-X-1` incompleteness propagation becoming unverifiable
        # in production, the exact thing observability-strategy.md §3 warns of.
        self._telemetry.observe("clep_run_state_total", 1,
                                execution_state="terminal")
        self._telemetry.observe("clep_run_terminal_total", 1,
                                completeness=outcome.completeness)
        return outcome

    # ------------------------------------------------------------------ inner
    def _process_example(self, run_id, index, example, candidates, tier,
                         outcome, currency) -> Decimal:
        invocations = [
            CandidateInvocation(
                candidate_label=c.label, endpoint_name=c.endpoint_name,
                request=CompletionRequest(model=c.model, prompt=example.prompt))
            for c in candidates]
        candidate_outcomes = self._gateway.invoke_all(invocations)
        by_label = {c.label: c for c in candidates}
        added = Decimal(0)

        for candidate_outcome in candidate_outcomes:
            candidate = by_label[candidate_outcome.candidate_label]
            key = sample_key(run_id, candidate.label, example.id)

            evaluations = []
            if candidate_outcome.succeeded:
                sample = self._sample_context(
                    example, candidate_outcome.result.text, tier)
                evaluations = self._evaluate(sample)
                resolution, score, failure_kind = self._score_from(
                    evaluations)
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
                example_content_digest=example.content_digest,
                trajectory_truncated=bool(example.trajectory is not None
                                          and example.trajectory.truncated),
                model_latency_ms=candidate_outcome.latency_ms)

            if inserted:
                outcome.samples_recorded += 1
                if resolution == "scored":
                    outcome.samples_scored += 1
                else:
                    outcome.samples_failed += 1
                self._record_evaluators(sample_id, evaluations, outcome)
                self._record_analysis(sample_id, example, outcome)
                self._judge(sample_id, example, candidate_outcome, tier, outcome)
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

    def _sample_context(self, example, output, tier) -> SampleContext:
        """One construction, used by scoring and by recording alike.

        There were two before Phase 9, and they had already drifted apart in
        which fields they passed. Every field a Phase 9 evaluator reads —
        identified contexts, citations, the required set, the typed trajectory
        and its schemas — travels through here, so an evaluator cannot see one
        thing while scoring and another while being recorded.
        """
        return SampleContext(
            example_id=example.id, prompt=example.prompt, output=output,
            expected=example.expected,
            retrieved_context=() if example.contexts else example.retrieved_context,
            integration_tier=tier, contexts=example.contexts,
            citations=example.citations,
            required_context_ids=example.required_context_ids,
            agent_trajectory=example.trajectory,
            tool_schemas=example.tool_schemas,
            expected_tools=example.expected_tools)

    def _evaluate(self, sample: SampleContext):
        """Run every registered evaluator once, under an explicit grant.

        The grant is deny-by-default and scoped to this tenant (ADR-006 rules 3
        and 5). Nothing here widens it: an evaluator that declares a capability
        is refused before it runs unless a deployment granted it deliberately.
        """
        grant = grant_for(self._repo.organization_id,
                          self._evaluator_capabilities)
        return [(key, run_evaluator(self._registry.get(key), sample,
                                    timeout_ms=self._evaluator_timeout_ms,
                                    grant=grant))
                for key in self._registry.keys()]

    def _score_from(self, results):
        """The sample's own resolution, taken from its evaluators.

        Scored only when at least one evaluator produced a number. An evaluator
        set that all abstained leaves the sample abstained, because averaging
        over nothing is not a score — and a truncated trajectory contributes
        nothing here for the same reason.
        """
        scores = [r.score for _, r in results if r.resolution == "scored"]
        if not scores:
            return "abstained", None, None
        return "scored", sum(scores) / Decimal(len(scores)), None

    def _record_evaluators(self, sample_id, results, outcome):
        grant = grant_for(self._repo.organization_id,
                          self._evaluator_capabilities)
        for key, result in results:
            evaluator_version_id = self._evaluator_ids.get(key)
            if evaluator_version_id is None:
                continue
            self._repo.record_evaluator_outcome(
                sample_id=sample_id, evaluator_version_id=evaluator_version_id,
                resolution=result.resolution, score=result.score,
                unavailable_reason=result.unavailable_reason,
                duration_ms=result.duration_ms)
            # The governance record (ADR-006 rule 6): which version ran, inside
            # which boundary, and how it ended. `outcome` here is the
            # invocation's fate, which is narrower than the evaluator's six
            # resolutions — a governance reader is asking whether the code was
            # allowed to run and whether it produced anything, not what it said.
            invocation_outcome = _invocation_outcome(result)
            self._repo.record_evaluator_invocation(
                sample_id=sample_id, evaluator_version_id=evaluator_version_id,
                granted_permissions=grant.recorded,
                outcome=invocation_outcome,
                # Was `f"{sample_id}:{key}"` — a value derivable from
                # `run_sample_id` and `evaluator_version_id` in the same row, so
                # it carried nothing the row did not already hold. The column is
                # named `correlation_id`; it now holds a correlation identifier.
                # The fallback keeps the old value for a run that predates the
                # chain, because the column is NOT NULL and an empty string
                # would satisfy the constraint while correlating nothing.
                correlation_id=current_id() or f"{sample_id}:{key}")
            self._telemetry.observe("clep_evaluator_invocation_total", 1,
                                    outcome=invocation_outcome)
            if result.duration_ms is not None:
                self._telemetry.observe("clep_evaluator_duration_ms",
                                        result.duration_ms,
                                        outcome=invocation_outcome)
            outcome.evaluator_outcomes += 1

    def _judge(self, sample_id, example, candidate_outcome, tier, outcome):
        """Run the ensemble over this sample, if the suite configured one.

        A judgement is worth having only about an answer that exists, so a
        candidate that failed is not judged — there is nothing to judge, and a
        judgement of nothing would be a score against a provider outage.
        """
        if self._panel is None or not candidate_outcome.succeeded:
            return
        sample = self._sample_context(example, candidate_outcome.result.text, tier)
        self._panel.judge(run_id=self._run_id, run_sample_id=sample_id,
                          sample=sample, outcome=outcome.panel)

    def _record_analysis(self, sample_id, example, outcome):
        """Persist the Phase 9 inputs this sample was evaluated against.

        Written here, by the run, rather than assembled afterwards from
        anything: the evidence behind a score has to be the evidence the score
        was computed from.
        """
        if self._analysis is None:
            return
        if example.contexts:
            self._analysis.record_retrieval(
                run_sample_id=sample_id, contexts=example.contexts,
                citations=example.citations)
            outcome.retrieval_recorded += len(example.contexts)
        if example.trajectory is not None:
            self._analysis.record_trajectory(run_sample_id=sample_id,
                                             trajectory=example.trajectory)
            outcome.trajectory_steps_recorded += len(example.trajectory.steps)
