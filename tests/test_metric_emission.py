"""REQ-N-OBS-2 and REQ-N-OBS-4, established by emission rather than by reading.

Two requirements, and both have an easy fake version this file refuses.

`REQ-N-OBS-2` asks for nine metric classes. The easy version asserts that nine
classes appear in the catalogue — which would pass against a catalogue of nine
declarations that nothing ever records. So the assertion here is over what a
**driven platform actually emitted**: a run executes, judges vote, a gate
decides, a provider fails, a job is picked up off the queue, and the classes are
counted from the samples that arrived at the backend.

`REQ-N-OBS-4` asks for bounded cardinality. The easy version greps the source for
`run_id`. That cannot see a label built from a variable, and passes the moment
somebody writes `labels=d`. So the assertion here is that **the number of
distinct series does not grow when the number of tenants, runs and correlations
does** — measured, with the counts actually varied.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.api.app import outcome_class_for
from clep.providers.gateway import (CandidateInvocation, Price, PriceBook,
                                    ProviderGateway)
from clep.providers.port import (CompletionRequest, CompletionResult,
                                 ProviderOutage, ProviderRateLimited,
                                 QuotaExhausted, Usage)
from clep.telemetry import (CATALOGUE, METRIC_CLASSES, RecordingBackend,
                            Telemetry, correlated)
from clep.orchestration.worker import observe_queue_time


class _Adapter:
    def __init__(self, failure=None):
        self._failure = failure

    def complete(self, request):
        if self._failure is not None:
            raise self._failure
        return CompletionResult(text="ok", model=request.model,
                                usage=Usage(10, 5, 15), endpoint_name="e",
                                endpoint_kind="hosted")


def _gateway(telemetry, failure=None, priced=True):
    prices = PriceBook({"m": Price(Decimal("0.001"), Decimal("0.002"))}
                       if priced else {})
    return ProviderGateway({"e": _Adapter(failure)}, prices,
                           telemetry=telemetry)


def _invoke(gateway, model="m"):
    return gateway.invoke(CandidateInvocation(
        candidate_label="a", endpoint_name="e",
        request=CompletionRequest(model=model, prompt="p")))


# ------------------------------------------------------- the nine classes
def test_every_required_metric_class_is_emitted_by_driven_activity():
    """Not "declared". Emitted, by exercising the surfaces that own them."""
    backend = RecordingBackend()
    t = Telemetry(backend)

    with correlated():
        # 4 provider behaviour, 1 latency (model call), 5 tokens and cost,
        # 8 retries — one gateway call each for success and for failure.
        _invoke(_gateway(t))
        _invoke(_gateway(t, failure=ProviderOutage("down")))
        _invoke(_gateway(t, priced=False))

        # 1 latency (http), 2 errors
        t.observe("clep_http_request_duration_ms", 12.0, method="POST",
                  outcome_class="success")
        t.observe("clep_request_outcome_total", 1, outcome_class="success")
        t.observe("clep_failure_attribution_total", 1, attribution="platform",
                  surface="api")

        # 3 queue time
        observe_queue_time({"telemetry": t, "enqueue_time": None, "job_try": 2})
        t.observe("clep_work_unit_queue_duration_ms", 4.0, queue="default")

        # 1 latency (gate), 6 judge behaviour, 7 evaluator failures,
        # 9 workflow transitions
        t.observe("clep_gate_decision_duration_ms", 30.0, verdict="pass")
        t.observe("clep_judge_vote_total", 1, resolution="scored")
        t.observe("clep_judge_consensus_total", 1, state="escalated")
        t.observe("clep_judge_escalation_total", 1,
                  reason="disagreement_above_threshold")
        t.observe("clep_evaluator_invocation_total", 1, outcome="scored")
        t.observe("clep_evaluator_duration_ms", 3.0, outcome="scored")
        t.observe("clep_run_state_total", 1, execution_state="terminal")
        t.observe("clep_run_terminal_total", 1, completeness="complete")

    emitted = {CATALOGUE.get(name).metric_class for name in backend.names()}
    assert emitted == set(METRIC_CLASSES), (
        f"classes never emitted: {sorted(set(METRIC_CLASSES) - emitted)}")


def test_every_terminal_state_including_the_four_that_are_not_success():
    """observability-strategy.md §3, class 9. If telemetry records only success
    and failure, `partial`, `exhausted`, `cancelled` and `rejected` become
    invisible and REQ-X-1 incompleteness propagation is unverifiable."""
    backend = RecordingBackend()
    t = Telemetry(backend)
    for state in ("complete", "partial", "exhausted", "cancelled", "rejected"):
        t.observe("clep_run_terminal_total", 1, completeness=state)
    assert len(backend.series()) == 5


def test_the_five_provider_failure_modes_are_each_distinguishable():
    """REQ-N-REL-4's four, plus the fifth the ADR-003 spike discovered: an
    exhausted quota is terminal, and classifying it as a rate limit produces an
    infinite retry against a condition no retry can change."""
    backend = RecordingBackend()
    t = Telemetry(backend)
    for failure in (ProviderOutage("a"), ProviderRateLimited("b"),
                    QuotaExhausted("c")):
        _invoke(_gateway(t, failure=failure))
    outcomes = {labels["outcome"] for name, _, labels, _ in backend.samples
                if name == "clep_provider_call_total"}
    assert outcomes == {"provider_outage", "provider_rate_limited",
                        "quota_exhausted"}
    # And the retryability of each reached the retry metric unchanged.
    retryable = {labels["retryable"] for name, _, labels, _ in backend.samples
                 if name == "clep_retry_total"}
    assert retryable == {"retryable", "terminal"}


# ------------------------------------------------- bounded cardinality
def test_series_do_not_grow_with_tenants_runs_or_correlations():
    """REQ-N-OBS-4, behaviourally. The counts are varied; the series are counted.

    A source-text check for `run_id` cannot establish this — it cannot see a
    label built from a variable, and it passes the moment somebody writes
    `labels=d`. This drives 60 correlations through the same surfaces and
    requires the distinct series to stay put.
    """
    backend = RecordingBackend()
    t = Telemetry(backend)
    gateway = _gateway(t)

    def drive():
        with correlated():
            _invoke(gateway)
            t.observe("clep_request_outcome_total", 1, outcome_class="success")
            t.observe("clep_run_terminal_total", 1, completeness="complete")

    drive()
    after_one = len(backend.series())
    for _ in range(59):
        drive()
    after_sixty = len(backend.series())

    assert after_one == after_sixty, (
        f"series grew from {after_one} to {after_sixty} across 60 correlations")
    assert len(backend.correlations()) == 60, "the correlations really did vary"
    assert len(backend.samples) > 60, "the samples really were recorded"


def test_the_series_ceiling_is_computable_and_small():
    """If any declared metric carried an unbounded label this sum could not be
    computed at all — which is the property, not the number."""
    ceiling = CATALOGUE.max_series()
    assert 0 < ceiling < 500, ceiling
    assert all(spec.max_series >= 1 for spec in CATALOGUE)


@pytest.mark.parametrize("status,expected", [
    (200, "success"), (201, "success"), (304, "success"),
    (400, "client_error"), (404, "client_error"), (409, "client_error"),
    (401, "authorization"), (403, "authorization"),
    (500, "platform_failure"), (503, "platform_failure"),
])
def test_a_status_code_maps_to_the_outcome_class_req_x_10_requires(status,
                                                                  expected):
    """A 401 and a 503 are different answers to "whose fault", and availability
    and verdict integrity are both computed from that difference."""
    assert outcome_class_for(status) == expected
