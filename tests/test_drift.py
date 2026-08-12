"""Drift against baseline history — REQ-F-10-4.

The classification half runs without a database, because the decision is
arithmetic and a refusal. The history half needs one: what counts as "baseline
history" is a property of rows, including superseded baselines, and a fake would
answer whatever it was told.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.analytics import drift
from clep.analytics.drift import (ABOVE_RANGE, BELOW_RANGE, DRIFTED, DriftError,
                                  DriftRepository, HistoryPoint,
                                  INSUFFICIENT_CONFIGURATION,
                                  INSUFFICIENT_HISTORY, NOT_MEASURED, STABLE,
                                  WITHIN_RANGE)
from clep.db.session import tenant_session
from tests.conftest import requires_postgres
from tests.test_regression import build_run, examples, _slug  # noqa: F401


def history(*values):
    return [HistoryPoint(run_id=f"R{i}", baseline_id=f"B{i}",
                         baseline_state="superseded", mean_score=Decimal(v),
                         observations=10, approved_at=None)
            for i, v in enumerate(values)]


def classify(current, values, **kw):
    return drift.classify(metric_key="m", run_id="R", current_value=current,
                          current_observations=10, history=history(*values),
                          minimum_history=kw.get("minimum_history"),
                          tolerance=kw.get("tolerance"))


# ============================================== the refusals, without a database
def test_a_minimum_history_of_one_is_refused_outright():
    """A history of one is the single prior run the requirement forbids,
    whatever it is called."""
    with pytest.raises(DriftError, match="single prior run"):
        classify("0.8", ["0.8", "0.8"], minimum_history=1,
                 tolerance=Decimal("0.1"))
    with pytest.raises(DriftError):
        classify("0.8", ["0.8", "0.8"], minimum_history=0,
                 tolerance=Decimal("0.1"))


def test_one_baseline_is_not_a_history():
    analysis = classify(Decimal("0.5"), ["0.8"], minimum_history=2,
                        tolerance=Decimal("0.01"))
    assert analysis.verdict == INSUFFICIENT_HISTORY
    assert "not yet a history" in analysis.detail


def test_a_history_shorter_than_the_configured_minimum_abstains():
    analysis = classify(Decimal("0.5"), ["0.8", "0.81", "0.79"],
                        minimum_history=10, tolerance=Decimal("0.01"))
    assert analysis.verdict == INSUFFICIENT_HISTORY
    assert "below the configured minimum" in analysis.detail


def test_without_a_tolerance_the_platform_declines_to_invent_one():
    """ADR-007's rule. A threshold chosen here would become the product's
    definition of drift by default, and nobody would have calibrated it."""
    analysis = classify(Decimal("0.2"), ["0.8", "0.81", "0.79"])
    assert analysis.verdict == INSUFFICIENT_CONFIGURATION
    assert "declines" not in analysis.detail  # it says what is missing, not that
    assert "no drift tolerance" in analysis.detail
    # And it still reports the fact that costs no calibration.
    assert analysis.position == BELOW_RANGE


def test_a_run_that_produced_no_score_is_not_drift():
    analysis = classify(None, ["0.8", "0.81"], minimum_history=2,
                        tolerance=Decimal("0.01"))
    assert analysis.verdict == NOT_MEASURED
    assert analysis.position is None


# ================================================================ the judgements
def test_position_is_reported_against_the_range_the_history_spanned():
    assert classify(Decimal("0.70"), ["0.80", "0.90"]).position == BELOW_RANGE
    assert classify(Decimal("0.85"), ["0.80", "0.90"]).position == WITHIN_RANGE
    assert classify(Decimal("0.95"), ["0.80", "0.90"]).position == ABOVE_RANGE


def test_a_configured_tolerance_classifies_against_the_median_not_the_last_run():
    """The requirement in one assertion: the most recent baseline is 0.5, and a
    detector comparing against it alone would call 0.8 a large change. Against
    the history it is the 0.5 that is unusual."""
    analysis = classify(Decimal("0.80"), ["0.80", "0.81", "0.79", "0.50"],
                        minimum_history=3, tolerance=Decimal("0.05"))
    assert analysis.historical_median == Decimal("0.795000000")
    assert analysis.verdict == STABLE


def test_a_value_far_from_the_historical_median_is_drift():
    analysis = classify(Decimal("0.20"), ["0.80", "0.81", "0.79"],
                        minimum_history=3, tolerance=Decimal("0.05"))
    assert analysis.verdict == DRIFTED
    assert analysis.deviation_from_median == Decimal("0.60")
    assert "exceeds the configured tolerance" in analysis.detail


def test_the_analysis_serialises_with_its_history_intact():
    body = classify(Decimal("0.20"), ["0.80", "0.81"], minimum_history=2,
                    tolerance=Decimal("0.05")).as_dict()
    assert body["verdict"] == DRIFTED
    assert body["historySize"] == 2
    assert len(body["history"]) == 2
    assert body["history"][0]["meanScore"] == "0.80"
    assert body["tolerance"] == "0.05"


# ============================================================ the history itself
pytestmark_integration = [pytest.mark.integration, requires_postgres]


@pytest.mark.integration
@requires_postgres
def test_baseline_history_includes_superseded_baselines(
        migrated_database, seeded, examples):
    """A superseded baseline is history, not a mistake. Dropping them would
    shrink "history" to "the current baseline" — the single prior run again."""
    from clep.regression.repository import RegressionRepository
    scores = [[Decimal("0.80")] * 10, [Decimal("0.70")] * 10,
              [Decimal("0.60")] * 10]
    runs = [build_run(migrated_database, seeded, examples, s, key=f"dr-{i}")
            for i, s in enumerate(scores)]
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        for run_id in runs:
            baseline_id = repo.create_baseline(run_id=run_id, created_by="t")
            repo.approve_baseline(baseline_id, approved_by="t")

    candidate = build_run(migrated_database, seeded, examples,
                          [Decimal("0.10")] * 10, key="dr-candidate")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        points = DriftRepository(conn, seeded["organization"]).baseline_history(
            seeded["project"], suite_version_id=seeded["suite_version"],
            metric_key=_slug(seeded))
        analysis = DriftRepository(conn, seeded["organization"]).analyse(
            seeded["project"], run_id=candidate,
            suite_version_id=seeded["suite_version"], metric_key=_slug(seeded),
            minimum_history=3, tolerance=Decimal("0.05"))

    assert len(points) == 3, "superseded baselines were dropped from history"
    assert {p.baseline_state for p in points} == {"approved", "superseded"}
    assert analysis.verdict == DRIFTED
    assert analysis.position == BELOW_RANGE
    assert analysis.historical_median == Decimal("0.70000000000000000000")
    assert [p.run_id for p in analysis.history] == runs


@pytest.mark.integration
@requires_postgres
def test_the_run_being_assessed_is_never_part_of_its_own_history(
        migrated_database, seeded, examples):
    from clep.regression.repository import RegressionRepository
    run_id = build_run(migrated_database, seeded, examples,
                       [Decimal("0.80")] * 10, key="dr-self")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        baseline_id = repo.create_baseline(run_id=run_id, created_by="t")
        repo.approve_baseline(baseline_id, approved_by="t")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        analysis = DriftRepository(conn, seeded["organization"]).analyse(
            seeded["project"], run_id=run_id,
            suite_version_id=seeded["suite_version"], metric_key=_slug(seeded))
    assert analysis.history == ()
    assert analysis.verdict == INSUFFICIENT_HISTORY


@pytest.mark.integration
@requires_postgres
def test_drift_history_never_crosses_a_tenant_boundary(
        migrated_database, seeded, second_organization, examples):
    from clep.regression.repository import RegressionRepository
    run_id = build_run(migrated_database, seeded, examples,
                       [Decimal("0.80")] * 10, key="dr-iso")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        baseline_id = repo.create_baseline(run_id=run_id, created_by="t")
        repo.approve_baseline(baseline_id, approved_by="t")
    with tenant_session(migrated_database, second_organization) as conn:
        points = DriftRepository(conn, second_organization).baseline_history(
            seeded["project"], suite_version_id=seeded["suite_version"],
            metric_key=_slug(seeded))
    assert points == []
