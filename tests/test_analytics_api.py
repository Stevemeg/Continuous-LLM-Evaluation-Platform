"""The analytics, scorecard and alerting surface over HTTP.

Driven against a real database and the real contract. The properties worth
testing at this layer are the ones the layer owns: that a leaderboard cannot be
asked for without a benchmark, that a tenant sees only its own figures, that the
markdown scorecard is the same report as the JSON one, and that a rule the
platform cannot evaluate is refused rather than stored.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from clep.api.analytics_service import AnalyticsService
from clep.api.app import create_app
from clep.api.gate_service import GateService
from clep.api.registry_service import RegistryService
from clep.api.service import RunService
from clep.identity import new_ulid
from tests.conftest import requires_postgres
from tests.test_regression import build_run, examples, _slug  # noqa: F401

pytestmark = [pytest.mark.integration, requires_postgres]

GOOD = [Decimal("0.90")] * 10
POOR = [Decimal("0.10")] * 10
LATENCIES = [10, 20, 30, 40, 50, 60, 70, 80, 90, 900]


@pytest.fixture
def client(migrated_database, seeded):
    run_service = RunService(
        migrated_database,
        dataset_version_resolver=lambda org, suite: seeded["dataset_version"])
    return TestClient(
        create_app(run_service, RegistryService(migrated_database),
                   GateService(migrated_database), None, None,
                   AnalyticsService(migrated_database)),
        raise_server_exceptions=False)


@pytest.fixture
def auth(seeded):
    return {"Authorization": f"Bearer {seeded['organization']}:tester"}


# ------------------------------------------------------------------- trends
def test_the_trend_endpoint_returns_points_with_their_evidence(
        client, auth, migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, GOOD, key="api-t1")
    response = client.get(
        f"/projects/{seeded['project']}/analytics/quality-trend", headers=auth)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [p["runId"] for p in body["items"]] == [run_id]
    assert body["items"][0]["observations"] == 10
    assert body["items"][0]["completeness"]["state"] == "complete"


def test_the_trend_endpoint_filters_by_metric_and_window(
        client, auth, migrated_database, seeded, examples):
    build_run(migrated_database, seeded, examples, GOOD, key="api-t2")
    matching = client.get(
        f"/projects/{seeded['project']}/analytics/quality-trend",
        headers=auth, params={"metricKey": _slug(seeded), "windowDays": 30})
    absent = client.get(
        f"/projects/{seeded['project']}/analytics/quality-trend",
        headers=auth, params={"metricKey": "no-such-metric"})
    assert len(matching.json()["items"]) == 1
    assert absent.json()["items"] == []


# -------------------------------------------------------------- leaderboard
def test_a_leaderboard_cannot_be_requested_without_a_benchmark(client, auth,
                                                               seeded):
    """REQ-F-11-2. The parameter is required by the signature, so the refusal
    happens before any query runs."""
    response = client.get(f"/projects/{seeded['project']}/analytics/leaderboard",
                          headers=auth)
    assert response.status_code == 422, response.text


def test_a_leaderboard_is_scoped_to_the_benchmark_it_names(
        client, auth, migrated_database, seeded, examples):
    build_run(migrated_database, seeded, examples, GOOD, key="api-l1")
    response = client.get(
        f"/projects/{seeded['project']}/analytics/leaderboard", headers=auth,
        params={"suiteVersionId": seeded["suite_version"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suiteVersionId"] == seeded["suite_version"]
    assert body["items"][0]["modelIdentifier"] == "m"
    elsewhere = client.get(
        f"/projects/{seeded['project']}/analytics/leaderboard", headers=auth,
        params={"suiteVersionId": new_ulid()})
    assert elsewhere.json()["items"] == []


# --------------------------------------------------------------- operational
def test_the_operational_endpoint_reports_the_tail_and_cost_per_task(
        client, auth, migrated_database, seeded, examples):
    build_run(migrated_database, seeded, examples, GOOD, key="api-o1",
              latencies=LATENCIES, costs=[Decimal("0.02")] * 10)
    body = client.get(f"/projects/{seeded['project']}/analytics/operational",
                      headers=auth).json()
    assert body["modelLatencyMs"]["measured"] == 10
    assert body["modelLatencyMs"]["quantiles"]["0.95"] == 900
    assert body["successfulTasks"] == 10
    assert body["costPerSuccessfulTask"] == "0.020000000"


# ---------------------------------------------------------- judges and agents
def test_the_judge_and_agent_endpoints_answer_for_an_empty_project(
        client, auth, seeded):
    """An empty answer, marked incomplete. Not a 404: the project exists and
    has produced nothing, which is a finding rather than a missing resource."""
    judges = client.get(f"/projects/{seeded['project']}/analytics/judges",
                        headers=auth).json()
    agents = client.get(f"/projects/{seeded['project']}/analytics/agents",
                        headers=auth).json()
    assert judges["judgements"] == 0
    assert judges["completeness"]["state"] == "incomplete"
    assert agents["toolCalls"] == 0
    assert agents["completeness"]["state"] == "incomplete"


# ---------------------------------------------------------------------- drift
def test_the_drift_endpoint_abstains_without_a_configured_tolerance(
        client, auth, migrated_database, seeded, examples):
    from clep.db.session import tenant_session
    from clep.regression.repository import RegressionRepository
    for index in range(2):
        run_id = build_run(migrated_database, seeded, examples,
                           [Decimal("0.80")] * 10, key=f"api-d{index}")
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            repo = RegressionRepository(conn, seeded["organization"])
            baseline_id = repo.create_baseline(run_id=run_id, created_by="t")
            repo.approve_baseline(baseline_id, approved_by="t")
    current = build_run(migrated_database, seeded, examples,
                        [Decimal("0.20")] * 10, key="api-d-now")

    params = {"suiteVersionId": seeded["suite_version"], "runId": current,
              "metricKey": _slug(seeded)}
    abstained = client.get(f"/projects/{seeded['project']}/analytics/drift",
                           headers=auth, params=params).json()
    assert abstained["verdict"] == "insufficient_configuration"
    assert abstained["position"] == "below_observed_range"

    classified = client.get(
        f"/projects/{seeded['project']}/analytics/drift", headers=auth,
        params={**params, "minimumHistory": 2, "tolerance": "0.05"}).json()
    assert classified["verdict"] == "drifted"
    assert classified["historySize"] == 2


def test_a_minimum_history_of_one_is_refused_by_the_surface(client, auth,
                                                            seeded):
    response = client.get(
        f"/projects/{seeded['project']}/analytics/drift", headers=auth,
        params={"suiteVersionId": seeded["suite_version"], "runId": new_ulid(),
                "metricKey": "m", "minimumHistory": 1})
    assert response.status_code == 422, response.text


def test_drift_about_a_run_that_does_not_exist_is_a_404(client, auth, seeded):
    response = client.get(
        f"/projects/{seeded['project']}/analytics/drift", headers=auth,
        params={"suiteVersionId": seeded["suite_version"], "runId": new_ulid(),
                "metricKey": "m"})
    assert response.status_code == 404


# ------------------------------------------------------------------ scorecard
def test_both_scorecard_representations_carry_the_same_qualifications(
        client, auth, migrated_database, seeded, examples):
    build_run(migrated_database, seeded, examples, GOOD[:6] + [None] * 4,
              key="api-sc", completeness="partial")
    body = client.get(f"/projects/{seeded['project']}/scorecard",
                      headers=auth).json()
    text = client.get(f"/projects/{seeded['project']}/scorecard", headers=auth,
                      params={"format": "markdown"})
    assert text.headers["content-type"].startswith("text/markdown")
    assert body["qualityTrend"][0]["completeness"]["state"] == "incomplete"
    reason = body["qualityTrend"][0]["completeness"]["reason"]
    assert reason.split(";")[0].strip() in text.text
    assert body["notEstablished"][0] in text.text


def test_an_unknown_scorecard_format_is_refused(client, auth, seeded):
    response = client.get(f"/projects/{seeded['project']}/scorecard",
                          headers=auth, params={"format": "pdf"})
    assert response.status_code == 400


# --------------------------------------------------------------------- alerts
def a_rule(client, auth, seeded, **overrides):
    body = {"slug": "floor", "displayName": "Quality floor",
            "dimension": "quality", "metricKey": _slug(seeded),
            "direction": "higher_is_better", "threshold": "0.5",
            "minimumSampleSize": 5}
    body.update(overrides)
    return client.post(f"/projects/{seeded['project']}/alert-rules",
                       headers=auth, json=body)


def test_an_alert_rule_is_created_paused_and_listed(client, auth, seeded):
    created = a_rule(client, auth, seeded)
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    assert created.json()["state"] == "active"

    paused = client.post(f"/alert-rules/{rule_id}/pause", headers=auth)
    assert paused.status_code == 200
    assert paused.json()["state"] == "paused"

    listed = client.get(f"/projects/{seeded['project']}/alert-rules",
                        headers=auth).json()
    assert [r["id"] for r in listed["items"]] == [rule_id]


def test_a_rule_on_a_figure_the_platform_does_not_compute_is_refused(
        client, auth, seeded):
    refused = a_rule(client, auth, seeded, slug="c", dimension="cost",
                     metricKey="cost_probably")
    assert refused.status_code == 422, refused.text
    assert "never fires" in refused.json()["detail"]


def test_a_rule_with_a_dimension_outside_the_contract_is_refused(client, auth,
                                                                 seeded):
    refused = a_rule(client, auth, seeded, slug="v", dimension="vibes")
    assert refused.status_code == 400, refused.text


def test_evaluating_a_run_reports_on_every_rule_and_records_the_firings(
        client, auth, migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, POOR, key="api-a1")
    a_rule(client, auth, seeded)
    a_rule(client, auth, seeded, slug="quiet", threshold="0.01")

    evaluated = client.post(f"/runs/{run_id}/alert-evaluations", headers=auth)
    assert evaluated.status_code == 201, evaluated.text
    outcomes = {o["slug"]: o for o in evaluated.json()["items"]}
    assert outcomes["floor"]["outcome"] == "fired"
    assert outcomes["quiet"]["outcome"] == "within_threshold"
    assert outcomes["quiet"]["detail"]

    events = client.get(f"/projects/{seeded['project']}/alert-events",
                        headers=auth).json()
    assert len(events["items"]) == 1
    assert events["items"][0]["runId"] == run_id
    assert events["items"][0]["evidenceCompleteness"] == "complete"

    # Evaluating twice does not produce a second alert about the same evidence.
    again = client.post(f"/runs/{run_id}/alert-evaluations", headers=auth)
    assert {o["slug"]: o["outcome"] for o in again.json()["items"]}["floor"] \
        == "already_recorded"
    assert len(client.get(f"/projects/{seeded['project']}/alert-events",
                          headers=auth).json()["items"]) == 1


def test_alerting_on_a_run_still_in_flight_is_refused(client, auth,
                                                      migrated_database, seeded):
    from clep.db.session import tenant_session
    from clep.orchestration.repository import RunRepository
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        run_id = RunRepository(conn, seeded["organization"]).create_run(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "0" * 64,
            integration_tier="output_only", idempotency_key="api-inflight")
    response = client.post(f"/runs/{run_id}/alert-evaluations", headers=auth)
    assert response.status_code == 422
    assert "still changing" in response.json()["detail"]


def test_alerting_about_a_run_that_does_not_exist_is_a_404(client, auth):
    response = client.post(f"/runs/{new_ulid()}/alert-evaluations",
                           headers={**{"Authorization": "Bearer "
                                       "00000000-0000-0000-0000-000000000000:t"}})
    assert response.status_code in (404, 401, 403)


def test_another_tenant_sees_none_of_it(client, migrated_database, seeded,
                                        second_organization, examples):
    auth = {"Authorization": f"Bearer {seeded['organization']}:tester"}
    intruder = {"Authorization": f"Bearer {second_organization}:tester"}
    run_id = build_run(migrated_database, seeded, examples, POOR, key="api-iso")
    a_rule(client, auth, seeded)
    client.post(f"/runs/{run_id}/alert-evaluations", headers=auth)

    assert client.get(f"/projects/{seeded['project']}/analytics/quality-trend",
                      headers=intruder).json()["items"] == []
    assert client.get(f"/projects/{seeded['project']}/alert-rules",
                      headers=intruder).json()["items"] == []
    assert client.get(f"/projects/{seeded['project']}/alert-events",
                      headers=intruder).json()["items"] == []
    assert client.post(f"/runs/{run_id}/alert-evaluations",
                       headers=intruder).status_code == 404
