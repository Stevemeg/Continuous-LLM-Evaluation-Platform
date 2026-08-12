"""Standing orders and release observations through the HTTP surface.

Driven against a real database and the real contract, for the same reason as the
gate surface: the properties worth testing — that an unreadable cadence is
refused rather than stored, that an observation cannot be recorded about a run
that is not live, that the response carries advice and no actuation — belong to
the store and the contract, and a fake would report whatever it was told to.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clep.api.gate_service import GateService
from clep.api.registry_service import RegistryService
from clep.api.schedule_service import ScheduleService
from clep.api.service import RunService
from clep.identity import is_ulid, new_ulid
from tests.conftest import api_app, requires_postgres
from tests.test_regression import (BASELINE_SCORES, build_run,  # noqa: F401
                                   examples)

pytestmark = [pytest.mark.integration, requires_postgres]


@pytest.fixture
def client(migrated_database, seeded):
    run_service = RunService(
        migrated_database,
        dataset_version_resolver=lambda org, suite: seeded["dataset_version"])
    return TestClient(
        api_app(migrated_database, run_service, RegistryService(migrated_database),
                   GateService(migrated_database), None,
                   ScheduleService(migrated_database)),
        raise_server_exceptions=False)


@pytest.fixture
def auth(seeded, owner_headers):
    """A credential the store verified, not a token naming a tenant.

    Phase 12 replaced the string that used to be here. Every test in
    this file now passes through authentication and authorization on
    its way to whatever it was written to check.
    """
    return owner_headers


def a_schedule(client, auth, seeded, **overrides):
    body = {"suiteVersionId": seeded["suite_version"], "cadence": "*/15 * * * *",
            "budget": {"limit": "5.00", "currency": "USD"},
            "candidates": [{"modelConfigurationId":
                            seeded["model_configuration"]}]}
    body.update(overrides)
    return client.post(f"/projects/{seeded['project']}/evaluation-schedules",
                       headers=auth, json=body)


# ------------------------------------------------------------------ schedules
def test_a_schedule_is_created_and_reads_back_in_the_contracts_vocabulary(
        client, auth, seeded):
    created = a_schedule(client, auth, seeded)
    assert created.status_code == 201, created.text
    body = created.json()
    assert is_ulid(body["id"])
    assert body["cadence"] == "*/15 * * * *"
    assert body["state"] == "active"
    assert body["trigger"] == "schedule"
    assert body["budget"] == {"limit": "5.000000000", "currency": "USD"}
    assert body["lastRunId"] is None


def test_a_cadence_nothing_can_read_is_refused_rather_than_stored(
        client, auth, seeded):
    refused = a_schedule(client, auth, seeded, cadence="every 15 minutes")
    assert refused.status_code == 422, refused.text
    assert "five fields" in refused.json()["detail"]


def test_a_schedule_declares_why_it_runs_and_refuses_a_reason_it_cannot_be(
        client, auth, seeded):
    canary = a_schedule(client, auth, seeded, trigger="canary")
    assert canary.status_code == 201, canary.text
    assert canary.json()["trigger"] == "canary"
    for impossible in ("manual", "pull_request", "whenever"):
        refused = a_schedule(client, auth, seeded, trigger=impossible)
        assert refused.status_code == 400, refused.text


def test_a_schedule_is_paused_rather_than_deleted(client, auth, seeded):
    schedule_id = a_schedule(client, auth, seeded).json()["id"]
    paused = client.post(f"/evaluation-schedules/{schedule_id}/pause", headers=auth)
    assert paused.status_code == 200, paused.text
    assert paused.json()["state"] == "paused"
    # Still there, and still describing what was scheduled.
    assert paused.json()["cadence"] == "*/15 * * * *"
    # Pausing twice is not an error; the schedule is already stopped.
    again = client.post(f"/evaluation-schedules/{schedule_id}/pause", headers=auth)
    assert again.status_code == 200
    assert again.json()["state"] == "paused"


def test_pausing_a_schedule_that_does_not_exist_is_a_404(client, auth):
    absent = client.post(f"/evaluation-schedules/{new_ulid()}/pause", headers=auth)
    assert absent.status_code == 404


def test_another_tenant_cannot_see_or_pause_a_schedule(client, seeded, auth,
                                                       second_organization, intruder_headers):
    schedule_id = a_schedule(client, auth, seeded).json()["id"]
    intruder = intruder_headers
    denied = client.post(f"/evaluation-schedules/{schedule_id}/pause",
                         headers=intruder)
    # Indistinguishable from a schedule that does not exist, on purpose.
    assert denied.status_code == 404


# ------------------------------------------------------- release observations
def test_an_observation_carries_advice_and_no_way_to_act(
        client, auth, migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="obs-1")
    recorded = client.post(
        f"/projects/{seeded['project']}/release-observations", headers=auth,
        json={"trigger": "post_deployment", "runId": run_id})
    assert recorded.status_code == 201, recorded.text
    body = recorded.json()
    assert body["trigger"] == "post_deployment"
    assert body["runId"] == run_id
    # No gate decision behind it: the honest answer is not "nothing to do".
    assert body["recommendation"]["kind"] == "investigate"
    assert body["recommendation"]["rationale"]
    # REQ-F-10-3, in the response shape: nothing names an action or a target.
    serialised = recorded.text.lower()
    for actuation in ("endpoint", "webhook", "applied", "rolledback",
                      "rollback_at", "callback"):
        assert actuation not in serialised


def test_an_observation_about_a_run_that_is_not_live_is_refused(
        client, auth, migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="obs-2")
    for before_release in ("manual", "pull_request", "schedule"):
        refused = client.post(
            f"/projects/{seeded['project']}/release-observations", headers=auth,
            json={"trigger": before_release, "runId": run_id})
        assert refused.status_code == 422, refused.text
        assert "already live" in refused.json()["detail"]


def test_an_observation_about_a_run_that_does_not_exist_is_a_404(
        client, auth, seeded):
    absent = client.post(
        f"/projects/{seeded['project']}/release-observations", headers=auth,
        json={"trigger": "canary", "runId": new_ulid()})
    assert absent.status_code == 404


def test_one_observation_per_run(client, auth, migrated_database, seeded,
                                 examples):
    """The store's own constraint. A second observation about the same run
    would let advice be restated after the outcome is known."""
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="obs-3")
    first = client.post(
        f"/projects/{seeded['project']}/release-observations", headers=auth,
        json={"trigger": "canary", "runId": run_id})
    assert first.status_code == 201
    second = client.post(
        f"/projects/{seeded['project']}/release-observations", headers=auth,
        json={"trigger": "canary", "runId": run_id})
    assert second.status_code >= 400


def test_an_observation_citing_a_gate_decision_takes_its_recommendation_from_it(
        client, auth, migrated_database, seeded, examples):
    from tests.test_regression import approved_baseline
    from tests.test_gate_api import published_policy

    baseline_run = build_run(migrated_database, seeded, examples,
                             BASELINE_SCORES, key="obs-base")
    approved_baseline(migrated_database, seeded, baseline_run)
    candidate_run = build_run(migrated_database, seeded, examples,
                              BASELINE_SCORES, key="obs-cand")
    policy = published_policy(client, auth, seeded)
    decision = client.post(
        f"/projects/{seeded['project']}/gate-evaluations", headers=auth,
        json={"candidateRunId": candidate_run, "gatePolicyVersionId": policy["id"]})
    assert decision.status_code == 200, decision.text
    outcome = decision.json()["evaluatedOutcome"]

    recorded = client.post(
        f"/projects/{seeded['project']}/release-observations", headers=auth,
        json={"trigger": "post_deployment", "runId": candidate_run,
              "gateDecisionId": decision.json()["id"]})
    assert recorded.status_code == 201, recorded.text
    from clep.orchestration.releases import recommendation_for
    assert recorded.json()["recommendation"]["kind"] == recommendation_for(outcome)
    assert recorded.json()["gateDecisionId"] == decision.json()["id"]
    assert outcome in recorded.json()["recommendation"]["rationale"]
