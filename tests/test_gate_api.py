"""Baselines and quality gates through the HTTP surface, end to end.

Driven against a real database and the real contract: the properties worth
testing — that a decision cannot be edited, that an exception is audited, that
the human and machine reports describe the same decision — are properties of the
store and of the contract, and a fake would report whatever it was told to.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from clep.api.gate_service import GateService
from clep.api.registry_service import RegistryService
from clep.api.service import RunService
from clep.identity import is_ulid
from tests.conftest import api_app, requires_postgres
from tests.test_regression import (BASELINE_SCORES, approved_baseline, build_run,
                                   examples, _slug)  # noqa: F401

pytestmark = [pytest.mark.integration, requires_postgres]


@pytest.fixture
def client(migrated_database, seeded):
    run_service = RunService(
        migrated_database,
        dataset_version_resolver=lambda org, suite: seeded["dataset_version"])
    return TestClient(
        api_app(migrated_database, run_service, RegistryService(migrated_database),
                   GateService(migrated_database)),
        raise_server_exceptions=False)


@pytest.fixture
def auth(seeded, owner_headers):
    """A credential the store verified, not a token naming a tenant.

    Phase 12 replaced the string that used to be here. Every test in
    this file now passes through authentication and authorization on
    its way to whatever it was written to check.
    """
    return owner_headers


def published_policy(client, auth, seeded, **overrides):
    criterion = {"metricKey": _slug(seeded), "dimension": "quality",
                 "source": "evaluator", "direction": "higher_is_better",
                 "precisionThreshold": "0.05", "onRegression": "hard_fail",
                 "onInsufficientEvidence": "warning",
                 "onNotComparable": "hard_fail"}
    criterion.update(overrides)
    policy = client.post(f"/projects/{seeded['project']}/gate-policies", headers=auth,
                         json={"slug": "gate-" + seeded["project"][-6:].lower(),
                               "displayName": "Release gate"})
    assert policy.status_code == 201, policy.text
    version = client.post(f"/gate-policies/{policy.json()['id']}/versions",
                          headers=auth,
                          json={"confidenceLevel": 0.95, "resampleCount": 200,
                                "bootstrapSeed": 20260804, "criteria": [criterion]})
    assert version.status_code == 201, version.text
    published = client.post(
        f"/gate-policy-versions/{version.json()['id']}/publish", headers=auth)
    assert published.status_code == 200, published.text
    return published.json()


def approved(client, auth, seeded, run_id):
    created = client.post(f"/projects/{seeded['project']}/baselines", headers=auth,
                          json={"runId": run_id, "label": "release-1"})
    assert created.status_code == 201, created.text
    ok = client.post(f"/baselines/{created.json()['id']}/approval", headers=auth)
    assert ok.status_code == 200, ok.text
    return ok.json()


# ------------------------------------------------------------------ baselines
def test_a_baseline_is_created_and_approved(client, auth, migrated_database,
                                            seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="api-b1")
    baseline = approved(client, auth, seeded, run_id)
    assert is_ulid(baseline["id"])
    assert baseline["state"] == "approved"
    assert baseline["runId"] == run_id
    assert baseline["identityDigest"].startswith("sha256:")


def test_a_run_that_never_finished_is_refused_as_a_baseline(client, auth, seeded):
    created = client.post(f"/projects/{seeded['project']}/baselines", headers=auth,
                          json={"runId": "01ARZ3NDEKTSV4RRFFQ69G5FAV"})
    assert created.status_code == 422
    assert created.json()["category"] == "client_error"


# -------------------------------------------------------------------- policies
def test_a_policy_version_carries_the_parameters_adr_007_left_unset(
        client, auth, seeded):
    version = published_policy(client, auth, seeded)
    assert version["state"] == "published"
    assert version["confidenceLevel"] == 0.95
    assert version["resampleCount"] == 200
    assert version["bootstrapSeed"] == 20260804
    assert version["criteria"][0]["precisionThreshold"] == "0.050000000"


def test_a_criterion_cannot_map_an_abstention_to_a_pass(client, auth, seeded):
    """ADR-016: `pass` is not in the contract's CriterionAction vocabulary."""
    policy = client.post(f"/projects/{seeded['project']}/gate-policies", headers=auth,
                         json={"slug": "bad", "displayName": "Bad"})
    response = client.post(
        f"/gate-policies/{policy.json()['id']}/versions", headers=auth,
        json={"confidenceLevel": 0.95, "resampleCount": 100, "bootstrapSeed": 1,
              "criteria": [{"metricKey": "m", "dimension": "quality",
                            "source": "evaluator", "direction": "higher_is_better",
                            "onRegression": "hard_fail",
                            "onInsufficientEvidence": "pass",
                            "onNotComparable": "hard_fail"}]})
    assert response.status_code == 400
    assert "onInsufficientEvidence" in response.json()["detail"]


# ----------------------------------------------------------------- evaluation
def test_a_regression_is_a_successful_call_with_a_failing_outcome(
        client, auth, migrated_database, seeded, examples):
    """REQ-F-09-5. A bad score is a 200, not a 5xx."""
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="api-g1")
    worse = [s - Decimal("0.20") for s in BASELINE_SCORES]
    candidate_run = build_run(migrated_database, seeded, examples, worse,
                              key="api-g2")
    approved(client, auth, seeded, baseline_run)
    version = published_policy(client, auth, seeded)

    response = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": candidate_run,
                                 "gatePolicyVersionId": version["id"]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outcome"] == "hard_fail"
    assert body["evaluatedOutcome"] == "hard_fail"
    assert body["statisticalMethodVersion"] == "paired-bootstrap-percentile/1"
    assert body["gatePolicyVersionId"] == version["id"]
    assert body["comparisons"][0]["classification"] == "regression"
    assert body["comparisons"][0]["interval"]["confidenceLevel"] == "0.950000"
    assert body["criterionResults"][0]["ruleFired"] == "interval"


def test_the_approved_baseline_is_resolved_without_the_caller_naming_it(
        client, auth, migrated_database, seeded, examples):
    """REQ-F-09-7."""
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="api-r1")
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="api-r2")
    baseline = approved(client, auth, seeded, baseline_run)
    version = published_policy(client, auth, seeded)
    response = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": candidate_run,
                                 "gatePolicyVersionId": version["id"]})
    assert response.json()["baselineId"] == baseline["id"]
    assert response.json()["outcome"] == "pass"


def test_no_approved_baseline_is_recorded_as_not_comparable_not_as_an_error(
        client, auth, migrated_database, seeded, examples):
    candidate_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                              key="api-n1")
    version = published_policy(client, auth, seeded, onNotComparable="warning")
    response = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": candidate_run,
                                 "gatePolicyVersionId": version["id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "not_comparable"
    assert "no approved baseline" in body["criterionResults"][0]["detail"]


def test_a_draft_policy_version_cannot_decide_a_release(
        client, auth, migrated_database, seeded, examples):
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="api-d1")
    approved(client, auth, seeded, run_id)
    policy = client.post(f"/projects/{seeded['project']}/gate-policies", headers=auth,
                         json={"slug": "draft", "displayName": "Draft"})
    version = client.post(
        f"/gate-policies/{policy.json()['id']}/versions", headers=auth,
        json={"confidenceLevel": 0.95, "resampleCount": 100, "bootstrapSeed": 1,
              "criteria": [{"metricKey": _slug(seeded), "dimension": "quality",
                            "source": "evaluator", "direction": "higher_is_better",
                            "onRegression": "hard_fail",
                            "onInsufficientEvidence": "warning",
                            "onNotComparable": "hard_fail"}]})
    response = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": run_id,
                                 "gatePolicyVersionId": version.json()["id"]})
    assert response.status_code == 422, "a draft is refused rather than used"
    assert response.json()["category"] == "client_error"


# ------------------------------------------------------------------- reports
def test_the_decision_is_retrievable_as_json_and_as_prose(
        client, auth, migrated_database, seeded, examples):
    """REQ-F-09-4: both representations, one decision, the same evidence."""
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="api-rep1")
    worse = [s - Decimal("0.20") for s in BASELINE_SCORES]
    candidate_run = build_run(migrated_database, seeded, examples, worse,
                              key="api-rep2")
    approved(client, auth, seeded, baseline_run)
    version = published_policy(client, auth, seeded)
    decision = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": candidate_run,
                                 "gatePolicyVersionId": version["id"]}).json()

    machine = client.get(f"/gate-decisions/{decision['id']}", headers=auth)
    assert machine.status_code == 200
    assert machine.json()["id"] == decision["id"]

    human = client.get(f"/gate-decisions/{decision['id']}",
                       headers={**auth, "Accept": "text/markdown"})
    assert human.status_code == 200
    assert human.headers["content-type"].startswith("text/markdown")
    text = human.text
    assert decision["gateEvidenceDigest"] in text
    assert decision["comparisons"][0]["meanDifference"] in text
    assert str(decision["comparisons"][0]["sampleSize"]) in text
    assert "hard_fail" in text


# ---------------------------------------------------------------- exceptions
def test_an_exception_waives_the_block_and_leaves_the_decision_untouched(
        client, auth, migrated_database, seeded, examples):
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="api-e1")
    worse = [s - Decimal("0.20") for s in BASELINE_SCORES]
    candidate_run = build_run(migrated_database, seeded, examples, worse,
                              key="api-e2")
    approved(client, auth, seeded, baseline_run)
    version = published_policy(client, auth, seeded)
    decision = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": candidate_run,
                                 "gatePolicyVersionId": version["id"]}).json()
    assert decision["outcome"] == "hard_fail"

    expiry = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    created = client.post(f"/gate-decisions/{decision['id']}/exceptions",
                          headers=auth,
                          json={"justification": "known flaky provider, ticket "
                                                 "QA-1187, re-run scheduled",
                                "expiresAt": expiry})
    assert created.status_code == 201, created.text

    after = client.get(f"/gate-decisions/{decision['id']}", headers=auth).json()
    assert after["outcome"] == "exception_applied"
    assert after["evaluatedOutcome"] == "hard_fail", "the evidence is unchanged"
    assert after["exception"]["justification"].startswith("known flaky provider")


def test_a_thin_justification_is_refused(client, auth, migrated_database, seeded,
                                         examples):
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="api-t1")
    approved(client, auth, seeded, run_id)
    version = published_policy(client, auth, seeded)
    decision = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": run_id,
                                 "gatePolicyVersionId": version["id"]}).json()
    response = client.post(f"/gate-decisions/{decision['id']}/exceptions",
                           headers=auth,
                           json={"justification": "ok",
                                 "expiresAt": datetime.now(timezone.utc).isoformat()})
    assert response.status_code == 422


def test_an_expired_exception_stops_applying_without_anything_running(
        client, auth, migrated_database, seeded, examples):
    """The expiry is a query condition, not a scheduled job.

    An expiry that needs a sweeper to take effect is an expiry that silently does
    not when the sweeper stops.
    """
    baseline_run = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                             key="api-x1")
    worse = [s - Decimal("0.20") for s in BASELINE_SCORES]
    candidate_run = build_run(migrated_database, seeded, examples, worse,
                              key="api-x2")
    approved(client, auth, seeded, baseline_run)
    version = published_policy(client, auth, seeded)
    decision = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": candidate_run,
                                 "gatePolicyVersionId": version["id"]}).json()
    soon = (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()
    client.post(f"/gate-decisions/{decision['id']}/exceptions", headers=auth,
                json={"justification": "waived for one second only, deliberately",
                      "expiresAt": soon})
    import time
    time.sleep(1.2)
    after = client.get(f"/gate-decisions/{decision['id']}", headers=auth).json()
    assert after["outcome"] == "hard_fail"
    assert "exception" not in after


# ------------------------------------------------------------------- tenancy
def test_another_tenants_decision_is_not_found(client, auth, migrated_database,
                                               seeded, second_organization, intruder_headers,
                                               examples):
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="api-i1")
    approved(client, auth, seeded, run_id)
    version = published_policy(client, auth, seeded)
    decision = client.post(f"/projects/{seeded['project']}/gate-evaluations",
                           headers=auth,
                           json={"candidateRunId": run_id,
                                 "gatePolicyVersionId": version["id"]}).json()
    other = intruder_headers
    response = client.get(f"/gate-decisions/{decision['id']}", headers=other)
    assert response.status_code == 404, "indistinguishable from a decision that " \
                                        "never existed"
