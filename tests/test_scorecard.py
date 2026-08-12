"""The executive scorecard — REQ-F-11-8.

Half the requirement is that a non-specialist can read it. The other half, and
the one that can silently fail, is that the qualifications survive: a summary
that drops "computed over a run that was cancelled halfway" is not a
simplification, it is a different claim. Most of what follows tests that the
caveats are still there.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.analytics import scorecard as scorecards
from clep.analytics.alerts import AlertRepository, evaluate_run
from clep.db.session import tenant_session
from tests.conftest import requires_postgres
from tests.test_regression import build_run, examples, _slug  # noqa: F401

pytestmark = [pytest.mark.integration, requires_postgres]

GOOD = [Decimal("0.90")] * 10
LATENCIES = [10, 20, 30, 40, 50, 60, 70, 80, 90, 900]


def card(dsn, seeded, **kw):
    with tenant_session(dsn, seeded["organization"]) as conn:
        return scorecards.build(conn, seeded["organization"], seeded["project"],
                                **kw)


def test_a_scorecard_over_a_whole_run_reports_its_figures_and_their_evidence(
        migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, GOOD, key="sc-1",
                       latencies=LATENCIES, costs=[Decimal("0.01")] * 10)
    body = scorecards.machine_readable(
        card(migrated_database, seeded, suite_version_id=seeded["suite_version"]))

    assert body["projectId"] == seeded["project"]
    assert [p["runId"] for p in body["qualityTrend"]] == [run_id]
    assert body["qualityTrend"][0]["observations"] == 10
    assert body["qualityTrend"][0]["completeness"]["state"] == "complete"
    assert body["operational"]["successfulTasks"] == 10
    assert body["operational"]["runIds"] == [run_id]
    assert body["leaderboard"][0]["runIds"] == [run_id]
    # REQ-F-11-8's second half is not optional, and is never empty.
    assert body["notEstablished"]
    assert any("Judge accuracy" in line for line in body["notEstablished"])


def test_an_incomplete_figure_keeps_its_qualification_in_the_human_report(
        migrated_database, seeded, examples):
    """The export most likely to be read by someone who will not go looking for
    the footnote."""
    build_run(migrated_database, seeded, examples, GOOD[:6] + [None] * 4,
              key="sc-2", completeness="partial", latencies=LATENCIES)
    text = scorecards.human_readable(
        card(migrated_database, seeded, suite_version_id=seeded["suite_version"]))
    assert "# AI quality scorecard" in text
    assert "Read quality with these qualifications." in text
    assert "did not finish complete" in text
    assert "excluded rather than counted as zero" in text
    assert "incomplete" in text


def test_the_human_report_states_what_the_platform_has_not_established(
        migrated_database, seeded, examples):
    build_run(migrated_database, seeded, examples, GOOD, key="sc-3")
    text = scorecards.human_readable(card(migrated_database, seeded))
    assert "## What this does not tell you" in text
    for limitation in scorecards.UNCALIBRATED:
        assert limitation in text


def test_a_scorecard_with_no_benchmark_produces_no_ranking_and_says_why(
        migrated_database, seeded, examples):
    build_run(migrated_database, seeded, examples, GOOD, key="sc-4")
    built = card(migrated_database, seeded)
    assert built.leaderboard == ()
    assert built.drift == ()
    text = scorecards.human_readable(built)
    assert "No benchmark was named" in text
    assert "refuses to make" in text


def test_an_empty_project_reports_an_absence_of_evidence_not_a_clean_bill(
        migrated_database, seeded):
    text = scorecards.human_readable(card(migrated_database, seeded))
    assert "an absence of evidence" in text
    assert "No alert rule is configured" in text
    assert "not a clean bill of" in text


def test_the_scorecard_reports_drift_and_its_abstention(migrated_database,
                                                        seeded, examples):
    from clep.regression.repository import RegressionRepository
    for index, score in enumerate((Decimal("0.80"), Decimal("0.82"))):
        run_id = build_run(migrated_database, seeded, examples, [score] * 10,
                           key=f"sc-drift-{index}")
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            repo = RegressionRepository(conn, seeded["organization"])
            baseline_id = repo.create_baseline(run_id=run_id, created_by="t")
            repo.approve_baseline(baseline_id, approved_by="t")
    build_run(migrated_database, seeded, examples, [Decimal("0.20")] * 10,
              key="sc-drift-now")

    unconfigured = scorecards.machine_readable(
        card(migrated_database, seeded, suite_version_id=seeded["suite_version"]))
    assert unconfigured["drift"], "no drift analysis was produced"
    assert unconfigured["drift"][0]["verdict"] == "insufficient_configuration"
    assert unconfigured["drift"][0]["position"] == "below_observed_range"

    configured = scorecards.machine_readable(
        card(migrated_database, seeded, suite_version_id=seeded["suite_version"],
             minimum_history=2, drift_tolerance=Decimal("0.05")))
    assert configured["drift"][0]["verdict"] == "drifted"
    text = scorecards.human_readable(
        card(migrated_database, seeded, suite_version_id=seeded["suite_version"],
             minimum_history=2, drift_tolerance=Decimal("0.05")))
    assert "## Drift against baseline history" in text
    assert "drifted" in text


def test_a_fired_alert_appears_on_the_scorecard_with_its_evidence(
        migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples,
                       [Decimal("0.10")] * 10, key="sc-alert")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        AlertRepository(conn, seeded["organization"]).create_rule(
            project_id=seeded["project"], slug="floor", display_name="Floor",
            dimension="quality", metric_key=_slug(seeded),
            direction="higher_is_better", threshold=Decimal("0.5"),
            minimum_sample_size=5, created_by="tester")
        evaluate_run(conn, seeded["organization"],
                     project_id=seeded["project"], run_id=run_id)
    body = scorecards.machine_readable(card(migrated_database, seeded))
    assert len(body["alerts"]) == 1
    assert body["alerts"][0]["runId"] == run_id
    assert body["alerts"][0]["evidenceCompleteness"] == "complete"
    assert body["alertRules"] == 1
    text = scorecards.human_readable(card(migrated_database, seeded))
    assert run_id in text


def test_the_scorecard_never_crosses_a_tenant_boundary(
        migrated_database, seeded, second_organization, examples):
    build_run(migrated_database, seeded, examples, GOOD, key="sc-iso")
    with tenant_session(migrated_database, second_organization) as conn:
        built = scorecards.build(conn, second_organization, seeded["project"])
    body = scorecards.machine_readable(built)
    assert body["qualityTrend"] == []
    assert body["operational"]["runIds"] == []
    assert body["operational"]["completeness"]["state"] == "incomplete"
