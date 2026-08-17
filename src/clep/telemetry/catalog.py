"""The declared metrics: all nine classes `REQ-N-OBS-2` requires, and no more.

Every label enumeration here mirrors a vocabulary that already exists in the
schema or in a domain module. They are restated as literals rather than imported
because the domain emits through this package, and importing the domain here to
describe the domain's own metrics is a cycle waiting for the first module that
needs both.

Restating creates the drift risk this project checks for everywhere else, so it
is checked here too: the Phase 13 validator imports both sides and compares them
as sets. A vocabulary that gains a member in the schema and not here fails a
check rather than producing a metric that silently refuses the new value.

What is deliberately absent is a money metric. Cost belongs in the store, as an
exact `numeric(18,9)` with its currency, which is where `clep.sample_cost`
already puts it; `REQ-N-COST-1` attribution is answered from those records under
ADR-023 rule 6. A counter of money would be a float, would need a currency label,
and a deployment pricing in an undeclared currency would then meet ADR-022 rule
5's refusal in the middle of recording a real cost. Tokens and pricing *outcome*
are observable here; the amounts are observable where they are exact.
"""
from __future__ import annotations

from clep.telemetry.metrics import MetricCatalogue, MetricSpec

# --------------------------------------------------------------- vocabularies
#: `clep.run.execution_state` (05-run-and-execution.sql)
EXECUTION_STATES = ("queued", "running", "terminal")

#: `clep.run.completeness`. Class 9 exists for these: four of the five are not
#: success, and telemetry that records only success and failure makes
#: `REQ-X-1` incompleteness propagation unverifiable in production.
COMPLETENESS = ("complete", "partial", "exhausted", "cancelled", "rejected")

#: `clep.run_sample.resolution`, `clep.evaluator_outcome.resolution`,
#: `clep.judge_run.resolution` — one vocabulary in three tables.
RESOLUTIONS = ("scored", "failed", "timed_out", "abstained", "unavailable",
               "truncated")

#: `clep.evaluator_invocation.outcome` (12-identity-and-access.sql). Narrower
#: than `RESOLUTIONS` on purpose: a governance reader asks whether the code was
#: allowed to run and whether it produced anything.
INVOCATION_OUTCOMES = ("scored", "abstained", "unavailable", "refused")

#: `clep.provider_endpoint.endpoint_kind`
ENDPOINT_KINDS = ("hosted", "self_hosted")

#: `clep.providers.port.TAXONOMY`, plus the success case. The fifth member came
#: out of the ADR-003 spike: an exhausted quota is terminal, not a rate limit.
PROVIDER_OUTCOMES = ("ok", "provider_outage", "provider_rate_limited",
                     "provider_malformed", "model_unavailable", "quota_exhausted")

#: `clep.gate_criterion_result.verdict` (07-regression-and-gates.sql)
GATE_VERDICTS = ("pass", "hard_fail", "warning", "approval_required",
                 "insufficient_evidence", "not_comparable")

#: `clep.consensus_result.state` — `clep.judges.consensus.STATES`
CONSENSUS_STATES = ("agreed", "escalated")

#: `clep.escalation.reason` — `clep.judges.consensus.ESCALATION_REASONS`
ESCALATION_REASONS = ("disagreement_above_threshold", "no_threshold_configured",
                      "insufficient_scoring_votes")

#: `Problem.category` in the contract, plus success. `REQ-X-10` lives here: a
#: platform failure and a client error are different answers to "whose fault".
OUTCOME_CLASSES = ("success", "client_error", "authorization", "platform_failure")

#: `REQ-X-10` again, as the thing it is actually about.
ATTRIBUTIONS = ("platform", "candidate")

HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")

#: Where a signal came from. A bounded list of the platform's own surfaces.
SURFACES = ("api", "worker", "scheduler", "gate", "evaluator", "judge",
            "provider")

QUEUES = ("default", "scheduled")


def build_catalogue() -> MetricCatalogue:
    """Constructed rather than module-level so a test can build a fresh one."""
    return MetricCatalogue((
        # ------------------------------------------------------- 1. latency
        MetricSpec(
            name="clep_http_request_duration_ms", kind="histogram",
            metric_class="latency", unit="ms",
            description="Time to serve one HTTP request on the tenant API.",
            labels={"method": HTTP_METHODS, "outcome_class": OUTCOME_CLASSES}),
        MetricSpec(
            name="clep_gate_decision_duration_ms", kind="histogram",
            metric_class="latency", unit="ms",
            description="Invocation to reported gate decision. The REQ-N-PERF-1 "
                        "surface, and the platform's own contribution only.",
            labels={"verdict": GATE_VERDICTS}),
        MetricSpec(
            name="clep_model_call_duration_ms", kind="histogram",
            metric_class="latency", unit="ms",
            description="One provider call, measured at the gateway because it "
                        "is the sole egress (ADR-003). Reported separately from "
                        "gate latency so provider time is never attributed to "
                        "the platform (ADR-023 rule 5).",
            labels={"endpoint_kind": ENDPOINT_KINDS,
                    "outcome": PROVIDER_OUTCOMES}),
        MetricSpec(
            name="clep_evaluator_duration_ms", kind="histogram",
            metric_class="latency", unit="ms",
            description="One evaluator invocation, inside its boundary.",
            labels={"outcome": INVOCATION_OUTCOMES}),

        # -------------------------------------------------------- 2. errors
        MetricSpec(
            name="clep_request_outcome_total", kind="counter",
            metric_class="errors", unit="1",
            description="Requests by outcome class. Availability's numerator "
                        "and denominator both come from here.",
            labels={"outcome_class": OUTCOME_CLASSES}),
        MetricSpec(
            name="clep_failure_attribution_total", kind="counter",
            metric_class="errors", unit="1",
            description="REQ-X-10 as a measurement: was this failure ours or the "
                        "candidate's. The verdict-integrity SLI is computed from "
                        "this distinction.",
            labels={"attribution": ATTRIBUTIONS, "surface": SURFACES}),

        # ---------------------------------------------------- 3. queue time
        MetricSpec(
            name="clep_work_unit_queue_duration_ms", kind="histogram",
            metric_class="queue_time", unit="ms",
            description="Enqueue to pick-up. Separates contention from "
                        "execution, which a single latency figure cannot.",
            labels={"queue": QUEUES}),

        # -------------------------------------------- 4. provider behaviour
        MetricSpec(
            name="clep_provider_call_total", kind="counter",
            metric_class="provider_behaviour", unit="1",
            description="Provider calls by endpoint kind and outcome, including "
                        "the five REQ-N-REL-4 failure modes.",
            labels={"endpoint_kind": ENDPOINT_KINDS,
                    "outcome": PROVIDER_OUTCOMES}),

        # ---------------------------------------------- 5. tokens and cost
        MetricSpec(
            name="clep_model_tokens_total", kind="counter",
            metric_class="tokens_and_cost", unit="1",
            description="Tokens as the provider reported them, never estimated.",
            labels={"direction": ("prompt", "completion"),
                    "endpoint_kind": ENDPOINT_KINDS}),
        MetricSpec(
            name="clep_model_call_priced_total", kind="counter",
            metric_class="tokens_and_cost", unit="1",
            description="Whether a call could be costed. An unpriced call is "
                        "reported as unpriced, never costed at zero, because a "
                        "silent zero is a budget that never trips.",
            labels={"priced": ("priced", "unpriced")}),

        # ------------------------------------------------ 6. judge behaviour
        MetricSpec(
            name="clep_judge_vote_total", kind="counter",
            metric_class="judge_behaviour", unit="1",
            description="Individual judge votes by resolution.",
            labels={"resolution": RESOLUTIONS}),
        MetricSpec(
            name="clep_judge_consensus_total", kind="counter",
            metric_class="judge_behaviour", unit="1",
            description="Whether an ensemble agreed or escalated. This measures "
                        "judge behaviour; it does not calibrate a judge, and no "
                        "threshold is set by anything here.",
            labels={"state": CONSENSUS_STATES}),
        MetricSpec(
            name="clep_judge_escalation_total", kind="counter",
            metric_class="judge_behaviour", unit="1",
            description="Escalations by the reason recorded against them.",
            labels={"reason": ESCALATION_REASONS}),

        # ------------------------------------------- 7. evaluator failures
        MetricSpec(
            name="clep_evaluator_invocation_total", kind="counter",
            metric_class="evaluator_failures", unit="1",
            description="Every invocation of evaluator code by how it ended, "
                        "including refusal before it ran (ADR-006 rule 6).",
            labels={"outcome": INVOCATION_OUTCOMES}),

        # ------------------------------------------------------- 8. retries
        MetricSpec(
            name="clep_retry_total", kind="counter",
            metric_class="retries", unit="1",
            description="Retries by surface and whether the condition was "
                        "retryable at all. Answers whether stability is "
                        "degrading beneath successful outcomes.",
            labels={"surface": SURFACES,
                    "retryable": ("retryable", "terminal")}),

        # ------------------------------------------ 9. workflow transitions
        MetricSpec(
            name="clep_run_state_total", kind="counter",
            metric_class="workflow_transitions", unit="1",
            description="Transitions into each execution state.",
            labels={"execution_state": EXECUTION_STATES}),
        MetricSpec(
            name="clep_run_terminal_total", kind="counter",
            metric_class="workflow_transitions", unit="1",
            description="Where runs actually terminate. Four of these five are "
                        "not success, and a metric that recorded only success "
                        "and failure would make them invisible.",
            labels={"completeness": COMPLETENESS}),
    ))


CATALOGUE = build_catalogue()
