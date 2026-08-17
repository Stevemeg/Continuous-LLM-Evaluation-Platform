"""The SLO measurements, executed. ADR-023 rules 1 to 8.

Every number this file prints came out of a run against a real PostgreSQL. None
is chosen, and the ones that cannot be measured are not filled in — they are
reported as `TARGET NOT YET SET` with a named blocker, which is a completed piece
of work under ADR-023 rule 3 rather than an admission.

Run with `-s` to see the measurement table; the Phase 13 validator does exactly
that and commits the output.
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from clep.analytics.cost import PROVIDER_RECONCILIATION_BLOCKER
from clep.analytics.slo import (QUALITY_VERDICTS, Indicator, percentile,
                                run_completion, verdict_integrity)
from clep.api.gate_service import GateService
from clep.db.session import tenant_session
from clep.identity import ulid_to_uuid
from tests.conftest import requires_postgres
from tests.test_end_to_end import (  # noqa: F401 - fixtures used by name
    approved_baseline_from, build_examples, examples_with_evidence,
    execute_run, published_policy, second_configuration, _metric_key_of)

pytestmark = [pytest.mark.integration, requires_postgres]

#: Enough repetitions for a p95 by nearest rank to name a real observation
#: rather than the maximum. Twenty is small, and the reported observation count
#: says so — which is the point of carrying it alongside every value.
REPETITIONS = 20

UNSET = "TARGET NOT YET SET"


def _row(name, target, value, observations, basis):
    print(f"  {name:<26} {str(target):<22} "
          f"observed={value}  n={observations}\n      basis: {basis}")


def test_gate_latency_is_measured_and_a_target_derived(
        migrated_database, seeded, examples_with_evidence):
    """The one empirical target this phase can honestly derive.

    Gate latency is invocation to reported decision, and by the time a gate runs
    the candidate run has already finished — so no provider time is inside it.
    That is what makes this measurable here when `REQ-N-PERF-1`'s end-to-end
    target is not: the measurement covers the platform's own contribution, which
    is exactly the quantity ADR-023 rule 5 requires be reported separately.
    """
    examples = build_examples(examples_with_evidence)
    baseline_run, _ = execute_run(migrated_database, seeded, examples,
                                  key="slo-baseline")
    candidate_run, _ = execute_run(migrated_database, seeded, examples,
                                   key="slo-candidate")
    baseline_id = approved_baseline_from(migrated_database, seeded, baseline_run)
    metric_key = _metric_key_of(migrated_database, seeded)
    policy_version_id = published_policy(migrated_database, seeded, metric_key)
    service = GateService(migrated_database)

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        sample_count = conn.execute(
            "SELECT count(*) FROM clep.run_sample WHERE organization_id = %s "
            "AND run_id = %s",
            (seeded["organization"], ulid_to_uuid(candidate_run))).fetchone()[0]

    durations = []
    for _ in range(REPETITIONS):
        started = time.perf_counter()
        decision = service.evaluate_gate(
            organization_id=seeded["organization"],
            project_id=seeded["project"], candidate_run_id=candidate_run,
            policy_version_id=policy_version_id, baseline_id=baseline_id,
            actor_id="tester")
        durations.append((time.perf_counter() - started) * 1000.0)
        assert decision is not None

    p50 = percentile(durations, Decimal("0.50"))
    p95 = percentile(durations, Decimal("0.95"))
    print(f"\n=== gate latency, suite-size band {sample_count} sample(s) ===")
    print(f"  observations : {len(durations)}")
    print(f"  p50          : {p50:.1f} ms")
    print(f"  p95          : {p95:.1f} ms")
    print(f"  max          : {max(durations):.1f} ms")
    print(f"  method       : nearest-rank percentile over {REPETITIONS} "
          f"executed gate evaluations against PostgreSQL, platform "
          f"contribution only (the candidate run had already terminated, so no "
          f"provider time is included)")

    # The assertion is that a measurement happened and is usable, not that it
    # hit a number chosen in advance. The target is derived from this output in
    # docs/evidence/phase-13/slo-targets.md, which cites this run.
    assert len(durations) == REPETITIONS
    assert p95 is not None and p95 > 0
    assert p95 in [Decimal(str(d)) for d in durations], (
        "the reported p95 is not one of the observations; nearest-rank must "
        "name a latency that actually happened")


def test_verdict_integrity_is_measured_and_its_objective_is_the_requirement(
        migrated_database, seeded, examples_with_evidence):
    """ADR-023 rule 8. The objective is 100% because REQ-X-10 admits no other
    value, and the measurement establishes conformance rather than the target."""
    examples = build_examples(examples_with_evidence)
    baseline_run, _ = execute_run(migrated_database, seeded, examples,
                                  key="slo-vi-baseline")
    candidate_run, _ = execute_run(migrated_database, seeded, examples,
                                   key="slo-vi-candidate")
    baseline_id = approved_baseline_from(migrated_database, seeded, baseline_run)
    metric_key = _metric_key_of(migrated_database, seeded)
    policy_version_id = published_policy(migrated_database, seeded, metric_key)
    GateService(migrated_database).evaluate_gate(
        organization_id=seeded["organization"], project_id=seeded["project"],
        candidate_run_id=candidate_run, policy_version_id=policy_version_id,
        baseline_id=baseline_id, actor_id="tester")

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        indicator = verdict_integrity(conn, seeded["organization"])

    print(f"\n=== verdict integrity ===")
    print(f"  objective    : 1 (REQ-X-10, ADR-023 rule 8 — derived from the "
          f"requirement, not from measurement)")
    print(f"  observed     : {indicator.value}")
    print(f"  observations : {indicator.observations}")
    print(f"  detail       : {indicator.detail}")

    assert indicator.measured, "no gate decisions to measure"
    assert indicator.value == Decimal(1), (
        "a gate returned a quality verdict over a run carrying platform-caused "
        "incompleteness; REQ-X-10 admits no such decision")


def test_run_completion_is_measured_and_its_target_stays_unset(
        migrated_database, seeded, examples_with_evidence):
    """Measured, and deliberately not promoted to a target.

    A proportion over a few dozen runs in a test harness cannot distinguish 99%
    from 99.99%, and publishing either from this evidence would be the invented
    figure wearing a measurement's clothes.
    """
    examples = build_examples(examples_with_evidence)
    execute_run(migrated_database, seeded, examples, key="slo-rc-1")
    execute_run(migrated_database, seeded, examples, key="slo-rc-2")

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        indicator = run_completion(conn, seeded["organization"])

    print(f"\n=== run completion ===")
    print(f"  target       : {UNSET}")
    print(f"  blocker      : an availability-shaped proportion requires "
          f"observation over production traffic and time; {indicator.observations} "
          f"runs in a test harness cannot distinguish 99% from 99.99%")
    print(f"  observed     : {indicator.value}")
    print(f"  observations : {indicator.observations}")
    print(f"  detail       : {indicator.detail}")

    assert indicator.measured
    assert 0 <= indicator.value <= 1


def test_cost_attribution_accuracy_has_no_target_and_names_its_blocker():
    """The indicator this phase cannot measure at all, recorded under ADR-023
    rule 3 rather than approximated."""
    print(f"\n=== cost attribution accuracy ===")
    print(f"  target       : {UNSET}")
    print(f"  blocker      : {PROVIDER_RECONCILIATION_BLOCKER}")
    print(f"  observed     : None")
    assert "hosted provider" in PROVIDER_RECONCILIATION_BLOCKER
    assert "no credential" in PROVIDER_RECONCILIATION_BLOCKER


def test_gate_availability_has_no_target_and_names_its_blocker():
    print(f"\n=== gate availability ===")
    print(f"  target       : {UNSET}")
    print(f"  blocker      : availability is a proportion over production "
          f"traffic and elapsed time. A test harness that executes N gate "
          f"evaluations and observes N successes establishes that the gate "
          f"works, not that it is available 99.9% of the time; the two differ "
          f"by every failure mode a test does not induce.")
    print(f"  observed     : None")


def test_an_indicator_over_zero_observations_is_not_a_measurement():
    """The guard on every proportion above. A value with no denominator is how
    "100% available" gets published on the strength of four requests."""
    empty = Indicator("x", None, 0, "nothing recorded")
    assert not empty.measured
    assert QUALITY_VERDICTS  # the vocabulary is non-empty
