"""Alert rules and their firings — REQ-F-11-9.

The properties that matter are properties of the store and of the figures: that
one firing per rule per run is guaranteed by a constraint rather than by the
caller remembering, that a rule cannot be written against a figure the platform
does not compute, and that a firing carries the completeness of the evidence
behind it.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.analytics import alerts
from clep.analytics.alerts import (ALREADY_RECORDED, AlertError,
                                   AlertRepository, BELOW_MINIMUM_SAMPLE, FIRED,
                                   NOT_MEASURED, WITHIN_THRESHOLD, evaluate_run)
from clep.db.session import tenant_session
from tests.conftest import requires_postgres
from tests.test_regression import build_run, examples, _slug  # noqa: F401

pytestmark = [pytest.mark.integration, requires_postgres]

GOOD = [Decimal("0.90")] * 10
POOR = [Decimal("0.20")] * 10
LATENCIES = [10, 20, 30, 40, 50, 60, 70, 80, 90, 900]


def rule(conn, seeded, **overrides):
    body = dict(project_id=seeded["project"], slug="quality-floor",
                display_name="Quality floor", dimension="quality",
                metric_key=_slug(seeded), direction="higher_is_better",
                threshold=Decimal("0.50"), minimum_sample_size=5,
                created_by="tester")
    body.update(overrides)
    return AlertRepository(conn, seeded["organization"]).create_rule(**body)


# --------------------------------------------------------------------- rules
def test_a_rule_on_a_figure_the_platform_does_not_compute_is_refused(
        migrated_database, seeded):
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        with pytest.raises(AlertError, match="never fires and never says why"):
            rule(conn, seeded, dimension="cost", metric_key="cost_maybe",
                 slug="c1")
        with pytest.raises(AlertError):
            rule(conn, seeded, dimension="latency",
                 metric_key="latency_feels_slow", slug="l1")


def test_a_rule_must_state_a_dimension_a_direction_and_a_minimum(
        migrated_database, seeded):
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        with pytest.raises(AlertError, match="REQ-F-11-9 names"):
            rule(conn, seeded, dimension="vibes", slug="v1")
        with pytest.raises(AlertError, match="which way is bad"):
            rule(conn, seeded, direction="sideways", slug="d1")
        with pytest.raises(AlertError, match="fires on noise"):
            rule(conn, seeded, minimum_sample_size=0, slug="m1")


def test_a_rule_is_paused_rather_than_deleted(migrated_database, seeded):
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = AlertRepository(conn, seeded["organization"])
        rule_id = rule(conn, seeded)
        assert [r.id for r in repo.active_rules(seeded["project"])] == [rule_id]
        assert repo.pause_rule(rule_id) is True
        assert repo.active_rules(seeded["project"]) == []
        # Still listed, and still explaining why nothing fires.
        assert [r.state for r in repo.list_rules(seeded["project"])] == ["paused"]
        assert repo.pause_rule(rule_id) is False


# ------------------------------------------------------------------- firings
def test_a_quality_rule_fires_when_the_run_falls_below_its_floor(
        migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, POOR, key="al-1")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded)
        outcomes = evaluate_run(conn, seeded["organization"],
                                project_id=seeded["project"], run_id=run_id)
        events = AlertRepository(conn, seeded["organization"]).events_for_run(
            run_id)
    assert [o.outcome for o in outcomes] == [FIRED]
    assert outcomes[0].observed_value == Decimal("0.20000000000000000000")
    assert len(events) == 1
    assert events[0].sample_size == 10
    assert events[0].threshold == Decimal("0.500000000")
    # REQ-F-11-7: the alert carries the completeness of what it was computed on.
    assert events[0].evidence_completeness == "complete"


def test_a_run_within_the_threshold_writes_nothing(migrated_database, seeded,
                                                   examples):
    run_id = build_run(migrated_database, seeded, examples, GOOD, key="al-2")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded)
        outcomes = evaluate_run(conn, seeded["organization"],
                                project_id=seeded["project"], run_id=run_id)
        events = AlertRepository(conn, seeded["organization"]).events_for_run(
            run_id)
    assert [o.outcome for o in outcomes] == [WITHIN_THRESHOLD]
    assert events == []


def test_a_value_exactly_at_the_threshold_has_not_breached_it(
        migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples,
                       [Decimal("0.50")] * 10, key="al-3")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded)
        outcomes = evaluate_run(conn, seeded["organization"],
                                project_id=seeded["project"], run_id=run_id)
    assert outcomes[0].outcome == WITHIN_THRESHOLD


def test_a_rule_below_its_minimum_sample_size_does_not_fire(
        migrated_database, seeded, examples):
    """REQ-F-08-3 applied to alerting: firing here would be firing on noise."""
    run_id = build_run(migrated_database, seeded, examples,
                       POOR[:2] + [None] * 8, key="al-4")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded, minimum_sample_size=5)
        outcomes = evaluate_run(conn, seeded["organization"],
                                project_id=seeded["project"], run_id=run_id)
        events = AlertRepository(conn, seeded["organization"]).events_for_run(
            run_id)
    assert outcomes[0].outcome == BELOW_MINIMUM_SAMPLE
    assert "firing on noise" in outcomes[0].detail
    assert events == []


def test_a_rule_naming_a_metric_the_run_does_not_produce_says_so(
        migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, GOOD, key="al-5")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded, metric_key="a_metric_this_suite_does_not_produce",
             slug="absent")
        outcomes = evaluate_run(conn, seeded["organization"],
                                project_id=seeded["project"], run_id=run_id)
    assert outcomes[0].outcome == NOT_MEASURED
    assert "nothing to decide about" in outcomes[0].detail


def test_evaluating_the_same_run_twice_produces_one_alert(
        migrated_database, seeded, examples):
    """The store's constraint, not the caller's memory."""
    run_id = build_run(migrated_database, seeded, examples, POOR, key="al-6")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded)
        first = evaluate_run(conn, seeded["organization"],
                             project_id=seeded["project"], run_id=run_id)
        second = evaluate_run(conn, seeded["organization"],
                              project_id=seeded["project"], run_id=run_id)
        events = AlertRepository(conn, seeded["organization"]).events_for_run(
            run_id)
    assert first[0].outcome == FIRED
    assert second[0].outcome == ALREADY_RECORDED
    assert len(events) == 1


def test_a_firing_over_a_cancelled_run_records_that_the_evidence_was_partial(
        migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, POOR, key="al-7",
                       completeness="cancelled")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded)
        evaluate_run(conn, seeded["organization"],
                     project_id=seeded["project"], run_id=run_id)
        events = AlertRepository(conn, seeded["organization"]).events_for_run(
            run_id)
    assert events[0].evidence_completeness == "cancelled"
    assert "finished cancelled" in events[0].detail


def test_a_latency_rule_reads_the_tail_and_a_cost_rule_reads_the_total(
        migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, GOOD, key="al-8",
                       latencies=LATENCIES, costs=[Decimal("0.05")] * 10)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded, slug="tail", dimension="latency",
             metric_key="model_latency_p95_ms", direction="lower_is_better",
             threshold=Decimal("500"))
        rule(conn, seeded, slug="spend", dimension="cost",
             metric_key="cost_per_successful_task",
             direction="lower_is_better", threshold=Decimal("0.01"))
        outcomes = {o.slug: o for o in evaluate_run(
            conn, seeded["organization"], project_id=seeded["project"],
            run_id=run_id)}
    assert outcomes["tail"].outcome == FIRED
    assert outcomes["tail"].observed_value == Decimal("900")
    assert outcomes["spend"].outcome == FIRED
    assert outcomes["spend"].observed_value == Decimal("0.050000000")


def test_a_paused_rule_never_fires(migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, POOR, key="al-9")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = AlertRepository(conn, seeded["organization"])
        rule_id = rule(conn, seeded)
        repo.pause_rule(rule_id)
        outcomes = evaluate_run(conn, seeded["organization"],
                                project_id=seeded["project"], run_id=run_id)
    assert outcomes == []


def test_an_alert_event_cannot_be_edited_after_the_fact(migrated_database,
                                                        seeded, examples):
    """Audit-class, like a release observation. Advice or evidence rewritten
    once the outcome is known is a retelling."""
    import psycopg
    run_id = build_run(migrated_database, seeded, examples, POOR, key="al-10")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded)
        evaluate_run(conn, seeded["organization"],
                     project_id=seeded["project"], run_id=run_id)
    with pytest.raises(psycopg.errors.Error):
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            conn.execute("UPDATE clep.alert_event SET detail = 'rewritten'")
    with pytest.raises(psycopg.errors.Error):
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            conn.execute("DELETE FROM clep.alert_event")


def test_alerts_never_cross_a_tenant_boundary(migrated_database, seeded,
                                              second_organization, examples):
    run_id = build_run(migrated_database, seeded, examples, POOR, key="al-11")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rule(conn, seeded)
        evaluate_run(conn, seeded["organization"],
                     project_id=seeded["project"], run_id=run_id)
    with tenant_session(migrated_database, second_organization) as conn:
        repo = AlertRepository(conn, second_organization)
        assert repo.list_rules(seeded["project"]) == []
        assert repo.events_for_project(seeded["project"]) == []
        assert repo.events_for_run(run_id) == []


def test_the_schema_records_no_way_to_deliver_or_act_on_an_alert():
    """REQ-F-10-3 in the store. A column recording a notification sent is a
    column that expects the platform to send one."""
    from pathlib import Path
    import re
    root = Path(__file__).resolve().parents[1]
    sql = (root / "docs/data/schema/11-analytics-and-alerts.sql").read_text(
        encoding="utf-8")
    body = re.search(r"CREATE TABLE clep\.alert_event\s*\((.*?)\n\);", sql,
                     re.S).group(1)
    for actuation in ("webhook", "endpoint", "delivered", "notified",
                      "acknowledged", "target_url", "channel", "retry"):
        assert actuation not in body.lower()
    rule_body = re.search(r"CREATE TABLE clep\.alert_rule\s*\((.*?)\n\);", sql,
                          re.S).group(1)
    for actuation in ("webhook", "endpoint", "recipient", "channel", "email"):
        assert actuation not in rule_body.lower()
