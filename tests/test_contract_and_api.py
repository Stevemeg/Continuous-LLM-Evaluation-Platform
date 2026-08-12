"""The contract leads; the application follows.

Every assertion here runs in that direction. Nothing generates the contract from
the application, because a contract that cannot disagree with an implementation
is not a specification.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from clep.api import contract
from clep.api.app import create_app, problem_categories
from clep.identity import new_ulid

ORG = str(uuid.uuid4())
TOKEN = f"Bearer {ORG}:tester"
PROJECT = new_ulid()
SUITE = new_ulid()


class FakeRunService:
    """Stands in for persistence so contract behaviour is testable without a
    database. It records calls; it does not reimplement the service's rules."""

    def __init__(self):
        self.runs = {}
        self.calls = []

    def create_run(self, **kw):
        self.calls.append(("create_run", kw))
        run_id = new_ulid()
        body = {"id": run_id, "projectId": kw["project_id"],
                "identity": {"digest": "sha256:" + "0" * 64},
                "completeness": None, "executionState": "queued",
                "reproducibility": "reproducible",
                "createdAt": "2026-08-02T00:00:00Z", "sampleCounts": {}}
        self.runs[(kw["organization_id"], run_id)] = body
        return body

    def get_run(self, org, run_id):
        return self.runs.get((org, run_id))

    def cancel_run(self, org, run_id):
        run = self.runs.get((org, run_id))
        if run is None:
            return None
        run = dict(run, executionState="terminal", completeness="cancelled",
                   incompleteReason="cancelled by request")
        self.runs[(org, run_id)] = run
        return run

    def list_samples(self, org, run_id, limit, offset):
        if (org, run_id) not in self.runs:
            return None
        return {"items": [], "limit": limit, "offset": offset}


@pytest.fixture
def client():
    return TestClient(create_app(FakeRunService()), raise_server_exceptions=False)


# ------------------------------------------------------------------- contract
def test_every_implemented_route_is_declared_in_the_contract(client):
    declared = contract.operations()
    implemented = {(m, r.path) for r in client.app.routes
                   for m in getattr(r, "methods", set())
                   if m in ("GET", "POST", "PUT", "PATCH", "DELETE")}
    undeclared = sorted(implemented - set(declared))
    assert not undeclared, f"routes absent from the contract: {undeclared}"


def test_the_phase_implements_its_operations_and_says_so_by_omission():
    """Phase 11 adds the analytics, scorecard and alerting surface.
    The rest belong to phases that have not run and are absent, not stubbed: a
    501 is still a route a client can find and build against."""
    ids = {contract.operation_id(m, p) for m, p in contract.operations()}
    assert {"createRun", "getRun", "cancelRun", "listRunSamples",
            "createPrompt", "addPromptVersion", "getPromptVersion",
            "publishPromptVersion", "createExperiment", "getRunIdentity",
            "reproduceRun", "createBaseline", "approveBaseline",
            "createGatePolicy", "addGatePolicyVersion",
            "publishGatePolicyVersion", "evaluateGate", "getGateDecision",
            "createPolicyException",
            "createJudge", "addJudgeVersion", "publishJudgeVersion",
            "createJudgeEnsemble", "getJudgeEnsemble",
            "createEvaluationPlan", "getEvaluationPlan", "amendEvaluationPlan",
            "acceptEvaluationPlan", "listEscalations",
            "recordEscalationReview", "getEvaluationMemory",
            "getSampleAnalysis", "createEvaluationSchedule",
            "pauseEvaluationSchedule", "recordReleaseObservation",
            "getQualityTrend", "getBenchmarkLeaderboard",
            "getOperationalAnalytics", "getJudgeAnalytics", "getAgentAnalytics",
            "getQualityDrift", "getProjectScorecard", "createAlertRule",
            "listAlertRules", "pauseAlertRule", "evaluateAlerts",
            "listAlertEvents"} <= ids
    assert len(ids) == 52


def test_every_identifier_a_request_accepts_can_be_created_through_the_contract():
    """The defect class Phases 6 and 7 each found: a request field naming
    something no operation produces. Derived from the request schemas rather
    than listed, so a new one cannot be added without being creatable."""
    ids = {contract.operation_id(m, p) for m, p in contract.operations()}
    creators = {
        "judgeVersionId": "addJudgeVersion",
        "judgeEnsembleId": "createJudgeEnsemble",
        "evaluationPlanId": "createEvaluationPlan",
        "gatePolicyVersionId": "addGatePolicyVersion",
        "baselineId": "createBaseline",
        "modelConfigurationId": None,   # Phase 6 registry, created out of band
        "promptVersionId": "addPromptVersion",
        "suiteVersionId": None,         # Phase 4 registry
        "datasetVersionId": "createDatasetVersion",
        "runId": "createRun",
        "candidateRunId": "createRun",
        "projectId": None,
        "gateDecisionId": "evaluateGate",
        "escalationId": None,           # raised by execution, not by a caller
        "judgeId": "createJudge",
        "datasetId": None,
        "gatePolicyId": "createGatePolicy",
        "promptId": "createPrompt",
        "runSampleId": None,
        "scheduleId": None,
    }
    schemas = contract.load()["components"]["schemas"]
    cited = set()
    for name, schema in schemas.items():
        if not name.endswith("Request"):
            continue
        for field in schema.get("properties", {}):
            if field.endswith("Id") or field.endswith("Ids"):
                cited.add(field.removesuffix("s") if field.endswith("Ids") else field)
    unknown = sorted(cited - set(creators))
    assert not unknown, f"no creation route recorded for: {unknown}"
    missing = sorted(f for f in cited
                     if creators[f] is not None and creators[f] not in ids)
    assert not missing, f"cited but not creatable: {missing}"


def test_asking_for_an_operation_the_contract_lacks_is_an_error():
    with pytest.raises(contract.ContractError, match="contract leads"):
        contract.operation_for("POST", "/runs/{runId}/invented")


def test_vocabularies_are_read_from_the_contract_not_restated():
    assert contract.enum_of("IntegrationTier") == ["full", "partial", "output_only"]
    assert contract.enum_of("SampleResolution")[0] == "scored"
    assert problem_categories() == ["client_error", "authorization", "platform_failure"]


def test_a_quality_outcome_is_not_a_problem_category():
    """The contract is explicit: a run that scored badly is a successful
    response describing a bad score, never an error."""
    assert "quality_failure" not in problem_categories()
    assert "hard_fail" not in problem_categories()


# ----------------------------------------------------------------- behaviour
def test_creating_a_run_requires_an_idempotency_key(client):
    response = client.post(f"/projects/{PROJECT}/runs",
                           headers={"Authorization": TOKEN},
                           json={"suiteVersionId": SUITE,
                                 "candidates": [{"modelConfigurationId": new_ulid()}]})
    assert response.status_code == 400
    assert "Idempotency-Key" in response.json()["detail"]


def test_creating_a_run_returns_202_and_a_queued_run(client):
    response = client.post(
        f"/projects/{PROJECT}/runs",
        headers={"Authorization": TOKEN, "Idempotency-Key": "k1"},
        json={"suiteVersionId": SUITE,
              "candidates": [{"modelConfigurationId": new_ulid()}]})
    assert response.status_code == 202
    body = response.json()
    assert body["executionState"] == "queued"
    assert body["completeness"] is None, \
        "a running run has not ended in any of the five ways"


def test_an_absent_credential_is_rejected_before_anything_else(client):
    response = client.get(f"/runs/{new_ulid()}")
    assert response.status_code == 401
    assert response.json()["category"] == "authorization"


def test_the_tenant_comes_from_the_credential_and_not_from_the_request(client):
    """ADR-010 rule 3. There is no request field that names an organization, so
    a caller cannot ask for another tenant's data by asking politely."""
    created = client.post(
        f"/projects/{PROJECT}/runs",
        headers={"Authorization": TOKEN, "Idempotency-Key": "k2"},
        json={"suiteVersionId": SUITE,
              "candidates": [{"modelConfigurationId": new_ulid()}]}).json()
    other = f"Bearer {uuid.uuid4()}:intruder"
    response = client.get(f"/runs/{created['id']}", headers={"Authorization": other})
    assert response.status_code == 404


def test_another_tenants_run_is_indistinguishable_from_one_that_does_not_exist(client):
    absent = client.get(f"/runs/{new_ulid()}", headers={"Authorization": TOKEN})
    created = client.post(
        f"/projects/{PROJECT}/runs",
        headers={"Authorization": TOKEN, "Idempotency-Key": "k3"},
        json={"suiteVersionId": SUITE,
              "candidates": [{"modelConfigurationId": new_ulid()}]}).json()
    other = f"Bearer {uuid.uuid4()}:intruder"
    foreign = client.get(f"/runs/{created['id']}", headers={"Authorization": other})
    assert absent.status_code == foreign.status_code == 404
    assert absent.json()["title"] == foreign.json()["title"]


def test_malformed_identifiers_are_rejected_as_client_errors(client):
    response = client.get("/runs/not-a-ulid", headers={"Authorization": TOKEN})
    assert response.status_code == 400
    assert response.json()["category"] == "client_error"


def test_cancelling_leaves_a_clearly_incomplete_record(client):
    created = client.post(
        f"/projects/{PROJECT}/runs",
        headers={"Authorization": TOKEN, "Idempotency-Key": "k4"},
        json={"suiteVersionId": SUITE,
              "candidates": [{"modelConfigurationId": new_ulid()}]}).json()
    response = client.post(f"/runs/{created['id']}/cancel",
                           headers={"Authorization": TOKEN})
    assert response.status_code == 202
    body = response.json()
    assert body["completeness"] == "cancelled"
    assert body["incompleteReason"]


def test_an_unknown_integration_tier_is_refused(client):
    response = client.post(
        f"/projects/{PROJECT}/runs",
        headers={"Authorization": TOKEN, "Idempotency-Key": "k5"},
        json={"suiteVersionId": SUITE, "integrationTier": "everything",
              "candidates": [{"modelConfigurationId": new_ulid()}]})
    assert response.status_code == 400


def test_a_run_with_no_candidates_is_refused_by_the_contract_shape(client):
    response = client.post(
        f"/projects/{PROJECT}/runs",
        headers={"Authorization": TOKEN, "Idempotency-Key": "k6"},
        json={"suiteVersionId": SUITE, "candidates": []})
    assert response.status_code == 422
