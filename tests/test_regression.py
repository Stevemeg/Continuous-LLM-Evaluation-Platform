"""Baselines, comparability and the gate, against a real database.

The properties that matter here are properties of data the store holds — which
examples were scored, which versions were pinned, what a policy said at the time
— so a fake would be testing the fake.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from clep.db.session import admin_session, tenant_session
from clep.experiments.identity import IdentityBuilder, digest_of
from clep.experiments.repository import IdentityRepository
from clep.identity import new_ulid, ulid_to_uuid
from clep.orchestration.repository import RunRepository
from clep.regression import comparability, engine, report
from clep.regression.repository import (BaselineNotEligible, PolicyFrozen,
                                        RegressionError, RegressionRepository)
from tests.conftest import MIGRATION_DSN, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]

BASELINE_SCORES = [Decimal("0.80"), Decimal("0.82"), Decimal("0.79"),
                   Decimal("0.81"), Decimal("0.83"), Decimal("0.80"),
                   Decimal("0.78"), Decimal("0.82"), Decimal("0.81"),
                   Decimal("0.79")]


@pytest.fixture
def examples(seeded):
    made = []
    with admin_session(MIGRATION_DSN) as conn:
        for ordinal in range(1, 11):
            example_id = new_ulid()
            conn.execute(
                "INSERT INTO clep.example (id, organization_id, dataset_version_id,"
                " ordinal, split) VALUES (%s,%s,%s,%s,'test')",
                (ulid_to_uuid(example_id), seeded["organization"],
                 ulid_to_uuid(seeded["dataset_version"]), ordinal))
            made.append(example_id)
    return made


def build_run(dsn, seeded, examples, scores, *, key, durations=None,
              costs=None, capture_identity=True, evaluator_version=None,
              extra_candidate=False):
    """A finished run with one evaluator outcome per example.

    `scores` may contain `None`, which records the sample as `failed` and writes
    no number anywhere — the shape REQ-F-08-5 is about.
    """
    evaluator_version = evaluator_version or seeded["evaluator_version"]
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        run_id = repo.create_run(
            project_id=seeded["project"], suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "0" * 64, integration_tier="output_only",
            idempotency_key=key)
        candidate_id = repo.add_candidate(
            run_id, label="a",
            model_configuration_id=seeded["model_configuration"],
            endpoint_kind="hosted")
        if extra_candidate:
            repo.add_candidate(
                run_id, label="b",
                model_configuration_id=seeded["model_configuration"],
                endpoint_kind="hosted")
        for index, (example_id, score) in enumerate(zip(examples, scores)):
            resolution = "scored" if score is not None else "failed"
            sample_id, _ = repo.record_sample(
                run_id=run_id, candidate_id=candidate_id, candidate_label="a",
                example_id=example_id, sample_index=index, resolution=resolution,
                score=score,
                failure_kind=None if score is not None else "provider_outage")
            repo.record_evaluator_outcome(
                sample_id=sample_id,
                evaluator_version_id=_ulid_of(evaluator_version),
                resolution=resolution, score=score, unavailable_reason=None,
                duration_ms=(durations or [12] * len(examples))[index])
            if costs is not None:
                repo.record_cost(run_id=run_id, sample_id=sample_id,
                                 sample_key_value=f"{key}-{index}",
                                 prompt_tokens=10, completion_tokens=5,
                                 amount=costs[index], currency="USD")
        repo.finish_run(run_id, "complete")
        if capture_identity:
            _capture(conn, seeded, run_id, evaluator_version)
    return run_id


def _ulid_of(value: str) -> str:
    """The orchestration repository takes ULIDs; the seeds are already ULIDs."""
    return value


def _capture(conn, seeded, run_id, evaluator_version, *, tier="output_only",
             dataset_version=None):
    identity = (IdentityBuilder()
                .add("dataset_version", dataset_version or seeded["dataset_version"],
                     digest_of("dsv"))
                .add("suite_version", seeded["suite_version"], digest_of("sv"))
                .add("evaluator_version", evaluator_version, digest_of("ev"))
                .add("model_configuration", seeded["model_configuration"],
                     digest_of("mc"))
                .add_literal("integration_tier", tier)
                .build())
    IdentityRepository(conn, seeded["organization"]).capture(run_id, identity)


def policy(dsn, seeded, **criterion):
    """A published single-criterion policy over the seeded evaluator's slug."""
    defaults = dict(metric_key=_slug(seeded), dimension="quality",
                    source="evaluator", direction="higher_is_better",
                    precision_threshold=Decimal("0.05"),
                    on_regression="hard_fail",
                    on_insufficient_evidence="warning",
                    on_not_comparable="hard_fail")
    defaults.update(criterion)
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        policy_id = repo.create_gate_policy(project_id=seeded["project"],
                                            slug="gp-" + new_ulid()[-8:].lower(),
                                            display_name="Release gate")
        version_id = repo.add_policy_version(
            policy_id, confidence_level=Decimal("0.95"), resample_count=200,
            bootstrap_seed=20260804, created_by="tester")
        repo.add_criterion(version_id, **defaults)
        repo.publish_policy_version(version_id)
    return version_id


def _slug(seeded) -> str:
    with psycopg.connect(MIGRATION_DSN) as conn:
        return conn.execute(
            "SELECT ed.slug FROM clep.evaluator_definition ed "
            "JOIN clep.evaluator_version ev ON ev.evaluator_definition_id = ed.id "
            "WHERE ev.id = %s",
            (ulid_to_uuid(seeded["evaluator_version"]),)).fetchone()[0]


def approved_baseline(dsn, seeded, run_id):
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        baseline_id = repo.create_baseline(run_id=run_id, created_by="tester")
        repo.approve_baseline(baseline_id, approved_by="approver")
        return repo.get_baseline(baseline_id)


def evaluate(dsn, seeded, baseline, candidate_run_id, version_id):
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        identities = IdentityRepository(conn, seeded["organization"])
        return engine.evaluate(
            conn, seeded["organization"], baseline=baseline,
            candidate_run_id=candidate_run_id,
            policy_version=repo.get_policy_version(version_id),
            criteria=repo.criteria_of(version_id),
            baseline_identity=identities.components_of(baseline.run_id),
            candidate_identity=identities.components_of(candidate_run_id))


# --------------------------------------------------------------------- baselines
def test_a_baseline_derives_its_scope_from_the_run(migrated_database, seeded,
                                                   examples):
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="b1")
    baseline = approved_baseline(migrated_database, seeded, run_id)
    assert baseline.suite_version_id == seeded["suite_version"]
    assert baseline.dataset_version_id == seeded["dataset_version"]
    assert baseline.state == "approved"


def test_an_unfinished_run_cannot_be_a_baseline(migrated_database, seeded):
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        run_id = RunRepository(conn, seeded["organization"]).create_run(
            project_id=seeded["project"], suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "0" * 64, integration_tier="output_only",
            idempotency_key="unfinished")
        with pytest.raises(BaselineNotEligible):
            RegressionRepository(conn, seeded["organization"]).create_baseline(
                run_id=run_id, created_by="tester")


def test_approving_a_baseline_supersedes_the_one_it_replaces(
        migrated_database, seeded, examples):
    first = build_run(migrated_database, seeded, examples, BASELINE_SCORES, key="s1")
    second = build_run(migrated_database, seeded, examples, BASELINE_SCORES, key="s2")
    old = approved_baseline(migrated_database, seeded, first)
    new = approved_baseline(migrated_database, seeded, second)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        assert repo.get_baseline(old.id).state == "superseded"
        assert repo.approved_baseline_for(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"]).id == new.id


# ---------------------------------------------------------------- comparability
def test_a_changed_evaluator_version_invalidates_comparability(
        migrated_database, seeded, examples):
    """REQ-F-08-8: invalidate, do not warn."""
    other = _second_evaluator_version(seeded)
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="c1")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="c2", capture_identity=False,
                              evaluator_version=other)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        _capture(conn, seeded, candidate_run, other)
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    version_id = policy(migrated_database, seeded)
    result = evaluate(migrated_database, seeded, baseline, candidate_run, version_id)
    outcome = result.criterion_outcomes[0]
    assert outcome.comparison["classification"] == "not_comparable"
    assert outcome.rule_fired == "comparability"
    assert "evaluator_version" in result.not_comparable_reason
    assert "Re-score the baseline" in result.not_comparable_reason
    # The policy said incomparability blocks, so it blocks. The reason is
    # recorded on the criterion; the action is the policy's to choose.
    assert result.outcome == "hard_fail"


def test_the_policy_decides_what_incomparability_costs(migrated_database, seeded,
                                                       examples):
    """The same finding, under a policy that treats it as advisory.

    An earlier version of the engine returned `not_comparable` from the run-level
    check without consulting the policy at all, so a team that had configured
    incomparability to block would have received an outcome their CI might treat
    as a note.
    """
    other = _second_evaluator_version(seeded)
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="c5")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="c6", capture_identity=False,
                              evaluator_version=other)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        _capture(conn, seeded, candidate_run, other)
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, on_not_comparable="warning"))
    assert result.outcome == "not_comparable", \
        "below a blocking threshold, the truer description wins"


def test_a_changed_model_configuration_is_the_experiment_not_a_defect():
    """The kinds a comparison varies are listed, not implied."""
    assert "model_configuration" in comparability.VARYING_KINDS
    assert "prompt_version" in comparability.VARYING_KINDS
    assert "evaluator_version" in comparability.PINNED_KINDS
    assert "environment" in comparability.IGNORED_KINDS


def test_every_identity_kind_has_a_comparability_opinion(migrated_database, seeded,
                                                         examples):
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="k1")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        identity = IdentityRepository(conn, seeded["organization"]).components_of(run_id)
    assert comparability.unknown_kinds(identity) == ()


# --------------------------------------------------------------------- the gate
def test_a_real_regression_hard_fails_with_the_evidence_attached(
        migrated_database, seeded, examples):
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="g1")
    worse = [s - Decimal("0.20") for s in BASELINE_SCORES]
    candidate_run = build_run(migrated_database, seeded, examples, worse, key="g2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded))
    assert result.outcome == "hard_fail"
    only = result.criterion_outcomes[0]
    assert only.rule_fired == "interval"
    assert only.comparison["classification"] == "regression"
    assert only.comparison["sample_size"] == 10
    assert only.comparison["baseline_mean"] > only.comparison["candidate_mean"]


def test_an_unchanged_candidate_passes(migrated_database, seeded, examples):
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="p1")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="p2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded))
    assert result.outcome == "pass"
    assert result.criterion_outcomes[0].comparison["classification"] == "no_change"


def test_a_failed_sample_is_excluded_rather_than_scored_as_zero(
        migrated_database, seeded, examples):
    """REQ-F-08-5, and the reason the sample size is on every report.

    Five of the candidate's ten examples failed outright. Were they read as
    zeroes the mean would halve and the gate would report a catastrophic
    regression; what actually happened is that half the evidence is missing, and
    the pair count says so.
    """
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="f1")
    partial = list(BASELINE_SCORES)
    for i in range(5):
        partial[i] = None
    candidate_run = build_run(migrated_database, seeded, examples, partial, key="f2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded))
    comparison = result.criterion_outcomes[0].comparison
    assert comparison["sample_size"] == 5
    assert comparison["candidate_mean"] > Decimal("0.5")
    assert comparison["classification"] == "no_change"


def test_a_below_minimum_sample_abstains_and_the_policy_decides_what_that_means(
        migrated_database, seeded, examples):
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="m1")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="m2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, minimum_sample_size=50))
    outcome = result.criterion_outcomes[0]
    assert outcome.comparison["classification"] == "insufficient_evidence"
    assert outcome.rule_fired == "minimum_sample"
    assert outcome.verdict == "warning"
    assert result.outcome == "insufficient_evidence"


def test_an_abstention_that_the_policy_blocks_on_blocks(
        migrated_database, seeded, examples):
    """ADR-016: the worst action wins over the most informative description."""
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="m3")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="m4")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, minimum_sample_size=50,
                             on_insufficient_evidence="hard_fail"))
    assert result.outcome == "hard_fail"


def test_an_absolute_floor_fails_independently_of_the_baseline(
        migrated_database, seeded, examples):
    """The rule a baseline-only gate cannot express.

    Both runs sit at the same level, so the comparison finds no change. The floor
    is what stops a product from drifting downwards one acceptable step at a time.
    """
    low = [Decimal("0.30")] * 10
    baseline_run = build_run(migrated_database, seeded, examples, low, key="a1")
    candidate_run = build_run(migrated_database, seeded, examples, low, key="a2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded,
                             absolute_floor=Decimal("0.60")))
    assert result.outcome == "hard_fail"
    assert result.criterion_outcomes[0].rule_fired == "absolute_floor"


def test_a_relative_tolerance_forgives_a_detected_regression_and_says_so(
        migrated_database, seeded, examples):
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="r1")
    slightly_worse = [s - Decimal("0.02") for s in BASELINE_SCORES]
    candidate_run = build_run(migrated_database, seeded, examples, slightly_worse,
                              key="r2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded,
                             relative_tolerance=Decimal("0.10")))
    outcome = result.criterion_outcomes[0]
    assert result.outcome == "pass"
    assert outcome.rule_fired == "relative_tolerance"
    assert outcome.comparison["classification"] == "regression", \
        "forgiven, not undetected"


def test_a_tolerance_cannot_manufacture_a_regression(migrated_database, seeded,
                                                     examples):
    """ADR-016: the tolerance can only forgive.

    A zero tolerance with an unchanged candidate must still pass. If the
    tolerance were applied before the interval, any non-zero difference would
    fail — the fixed-threshold method ADR-007 rejected, smuggled in through the
    policy.
    """
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="r3")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="r4")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded,
                             relative_tolerance=Decimal("0")))
    assert result.outcome == "pass"
    assert result.criterion_outcomes[0].rule_fired == "interval"


def test_a_criterion_with_no_precision_threshold_abstains_rather_than_guessing(
        migrated_database, seeded, examples):
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="n1")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="n2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, precision_threshold=None))
    outcome = result.criterion_outcomes[0]
    assert outcome.rule_fired == "precision_unset"
    assert outcome.comparison["classification"] == "insufficient_evidence"


def test_a_judge_agreement_criterion_abstains_loudly_instead_of_passing(
        migrated_database, seeded, examples):
    """A gate for a capability that does not exist yet must not report success."""
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="j1")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="j2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, source="judge_agreement",
                             dimension="judge_agreement",
                             metric_key="judge_agreement"))
    outcome = result.criterion_outcomes[0]
    assert outcome.rule_fired == "no_signal"
    assert outcome.verdict == "warning"
    assert outcome.comparison["classification"] == "insufficient_evidence"


def test_a_multi_candidate_run_is_not_comparable_rather_than_averaged(
        migrated_database, seeded, examples):
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="x1")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="x2", extra_candidate=True)
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, on_not_comparable="warning"))
    assert result.outcome == "not_comparable"
    assert "2 candidate configuration(s)" in result.not_comparable_reason
    assert result.criterion_outcomes[0].comparison["sample_size"] == 0, \
        "nothing was paired, rather than each example paired twice"


def test_a_metric_the_suite_does_not_produce_is_not_comparable(
        migrated_database, seeded, examples):
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="u1")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="u2")
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, metric_key="nonexistent",
                             on_not_comparable="warning"))
    assert result.criterion_outcomes[0].rule_fired == "comparability"
    assert result.outcome == "not_comparable"


def test_cost_and_latency_are_compared_the_same_way_and_the_direction_flips(
        migrated_database, seeded, examples):
    cheap = [Decimal("0.001")] * 10
    dear = [Decimal("0.010")] * 10
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="c3", costs=cheap)
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="c4", costs=dear)
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, source="cost",
                             dimension="cost", metric_key="cost_per_example",
                             direction="lower_is_better",
                             precision_threshold=Decimal("0.05")))
    outcome = result.criterion_outcomes[0]
    assert outcome.comparison["classification"] == "regression", "cost went up"
    assert outcome.comparison["result_kind"] == "operational"
    assert result.outcome == "hard_fail"


def test_latency_is_compared_from_the_only_timing_the_platform_records(
        migrated_database, seeded, examples):
    """Evaluator duration, and the report must not imply it is model latency.

    Per-model-call timing arrives with the observability phase. Until then a
    latency criterion measures how long evaluation took, which is a real signal
    and not the one a reader might assume, so the source is named rather than
    inferred.
    """
    quick = [10] * 10
    slow = [400] * 10
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="l1", durations=quick)
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="l2", durations=slow)
    baseline = approved_baseline(migrated_database, seeded, baseline_run)
    result = evaluate(migrated_database, seeded, baseline, candidate_run,
                      policy(migrated_database, seeded, source="latency",
                             dimension="latency", metric_key="evaluation_latency_ms",
                             direction="lower_is_better",
                             precision_threshold=Decimal("50")))
    outcome = result.criterion_outcomes[0]
    assert outcome.comparison["classification"] == "regression", "it got slower"
    assert outcome.comparison["baseline_mean"] == Decimal("10.000000000")
    assert outcome.comparison["candidate_mean"] == Decimal("400.000000000")
    assert outcome.comparison["result_kind"] == "operational"


# ------------------------------------------------------------------- policies
def test_a_policy_version_with_no_criteria_cannot_be_published(migrated_database,
                                                               seeded):
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        policy_id = repo.create_gate_policy(project_id=seeded["project"],
                                            slug="empty-" + new_ulid()[-6:].lower(),
                                            display_name="Empty")
        version_id = repo.add_policy_version(
            policy_id, confidence_level=Decimal("0.95"), resample_count=100,
            bootstrap_seed=1, created_by="tester")
        with pytest.raises(RegressionError):
            repo.publish_policy_version(version_id)


def test_a_published_policy_version_refuses_a_new_criterion(migrated_database,
                                                            seeded):
    version_id = policy(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        with pytest.raises(PolicyFrozen):
            repo.add_criterion(version_id, metric_key="late", dimension="quality",
                               source="evaluator", direction="higher_is_better",
                               on_regression="hard_fail",
                               on_insufficient_evidence="warning",
                               on_not_comparable="hard_fail")


# -------------------------------------------------------------------- reports
def test_an_exception_waives_a_block_without_editing_the_decision():
    decision = {"id": "01J", "projectId": "p", "candidateRunId": "r",
                "baselineId": "b", "gatePolicyVersionId": "v",
                "statisticalMethodVersion": "paired-bootstrap-percentile/1",
                "gateEvidenceDigest": "sha256:" + "0" * 64,
                "evaluatedOutcome": "hard_fail", "decidedAt": "now"}
    live = {"id": "e", "actorId": "a", "justification": "known flaky judge",
            "expiresAt": "later", "createdAt": "now"}
    body = report.machine_readable(decision, [], [], live)
    assert body["evaluatedOutcome"] == "hard_fail"
    assert body["outcome"] == "exception_applied"
    assert report.machine_readable(decision, [], [], None)["outcome"] == "hard_fail"


def test_an_exception_cannot_turn_a_pass_into_anything_else():
    assert report.effective_outcome("pass", {"id": "e"}) == "pass"
    assert report.effective_outcome("warning", {"id": "e"}) == "warning"


def test_the_human_report_carries_the_same_evidence_as_the_machine_one():
    """REQ-F-09-4. The readable one is the one that gets believed."""
    decision = {"id": "01J", "projectId": "p", "candidateRunId": "run-1",
                "baselineId": "base-1", "gatePolicyVersionId": "ver-1",
                "statisticalMethodVersion": "paired-bootstrap-percentile/1",
                "gateEvidenceDigest": "sha256:" + "0" * 64,
                "evaluatedOutcome": "hard_fail", "decidedAt": "2026-08-04"}
    comparisons = [{"metric": "exact_match", "resultKind": "deterministic_evaluator",
                    "classification": "regression", "sampleSize": 10,
                    "baselineMean": Decimal("0.80"),
                    "candidateMean": Decimal("0.60"),
                    "meanDifference": Decimal("-0.20"),
                    "intervalLower": Decimal("-0.25"),
                    "intervalUpper": Decimal("-0.15"),
                    "confidenceLevel": Decimal("0.95"),
                    "effectSize": Decimal("-2.5"), "minimumSampleSize": None,
                    "statisticalMethodVersion": "paired-bootstrap-percentile/1",
                    "abstentionReason": None, "notComparableReason": None,
                    "evaluatorVersionId": None, "id": "cmp-1"}]
    results = [{"metric": "exact_match", "dimension": "quality",
                "verdict": "hard_fail", "ruleFired": "interval",
                "detail": "exact_match regressed"}]
    text = report.human_readable(decision, comparisons, results, None)
    for fragment in ("hard_fail", "exact_match", "-0.20", "-0.25", "0.95", "10",
                     "interval", "sha256:"):
        assert fragment in text, fragment


def test_the_report_never_shows_judges_and_evaluators_in_one_table():
    """REQ-F-08-6 in reporting, not only in storage."""
    def comparison(kind, metric):
        return {"metric": metric, "resultKind": kind, "classification": "no_change",
                "sampleSize": 4, "baselineMean": None, "candidateMean": None,
                "meanDifference": None, "intervalLower": None,
                "intervalUpper": None, "confidenceLevel": None, "effectSize": None,
                "minimumSampleSize": None, "abstentionReason": None,
                "notComparableReason": None, "statisticalMethodVersion": "m/1",
                "evaluatorVersionId": None, "id": metric}
    decision = {"id": "d", "projectId": "p", "candidateRunId": "r",
                "baselineId": "b", "gatePolicyVersionId": "v",
                "statisticalMethodVersion": "m/1",
                "gateEvidenceDigest": "sha256:" + "0" * 64,
                "evaluatedOutcome": "pass", "decidedAt": "now"}
    text = report.human_readable(
        decision, [comparison("deterministic_evaluator", "exact_match"),
                   comparison("probabilistic_judge", "helpfulness")], [], None)
    deterministic_section = text.split("## Probabilistic judges")[0]
    assert "exact_match" in deterministic_section
    assert "helpfulness" not in deterministic_section


def _second_evaluator_version(seeded):
    """A second builtin evaluator version, sharing the first one's definition."""
    version_id = new_ulid()
    with admin_session(MIGRATION_DSN) as conn:
        definition = conn.execute(
            "SELECT evaluator_definition_id FROM clep.evaluator_version WHERE id = %s",
            (ulid_to_uuid(seeded["evaluator_version"]),)).fetchone()[0]
        conn.execute(
            "INSERT INTO clep.evaluator_version (id, organization_id,"
            " evaluator_definition_id, version_number, content_digest,"
            " input_schema_ref, output_schema_ref, declared_permissions,"
            " is_deterministic, cost_class) VALUES (%s,NULL,%s,2,%s,"
            "'schema://in/v1','schema://out/v1','none',true,'free')",
            (ulid_to_uuid(version_id), definition, "sha256:" + "9" * 64))
        conn.execute(
            "INSERT INTO clep.suite_evaluator (id, organization_id,"
            " suite_version_id, evaluator_version_id) VALUES (%s,%s,%s,%s)",
            (ulid_to_uuid(new_ulid()), seeded["organization"],
             ulid_to_uuid(seeded["suite_version"]), ulid_to_uuid(version_id)))
    return version_id
