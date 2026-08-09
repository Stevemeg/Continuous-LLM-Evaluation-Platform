"""Judges, plans, escalations and memory through the HTTP surface, end to end.

Against a real database and the real contract, for the reason the gate tests
give: the properties worth testing here — that a used ensemble cannot be
recomposed, that an accepted plan cannot be amended, that an escalation is
reviewed once — are properties of the store, and a fake would report whatever it
was told to.
"""
from __future__ import annotations

import uuid
from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

from clep.api.agentic_service import AgenticService
from clep.api.app import create_app
from clep.api.gate_service import GateService
from clep.api.registry_service import RegistryService
from clep.api.service import RunService
from clep.identity import is_ulid, new_ulid, ulid_to_uuid
from clep.judges.consensus import Ensemble, reach_consensus
from clep.judges.repository import JudgeRepository
from clep.judges.sdk import JudgeVersion, Vote
from tests.conftest import MIGRATION_DSN, requires_postgres
from tests.test_regression import (BASELINE_SCORES, approved_baseline,  # noqa: F401
                                   build_run, examples, _slug)

pytestmark = [pytest.mark.integration, requires_postgres]

DIGEST = "sha256:" + "c" * 64


@pytest.fixture
def client(migrated_database, seeded):
    run_service = RunService(
        migrated_database,
        dataset_version_resolver=lambda org, suite: seeded["dataset_version"])
    return TestClient(
        create_app(run_service, RegistryService(migrated_database),
                   GateService(migrated_database),
                   AgenticService(migrated_database)),
        raise_server_exceptions=False)


@pytest.fixture
def auth(seeded):
    return {"Authorization": f"Bearer {seeded['organization']}:tester"}


@pytest.fixture
def second_configuration(seeded):
    """A second model configuration, so an ensemble can be heterogeneous."""
    configuration_id = new_ulid()
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clep.model_configuration (id, organization_id, model_id, "
            "version_number, output_affecting_parameters, content_digest, "
            "is_deterministic, state, created_by, published_at) "
            "VALUES (%s,%s,%s,2,'{}',%s,true,'published',%s, now())",
            (ulid_to_uuid(configuration_id), seeded["organization"],
             ulid_to_uuid(seeded["model"]), "sha256:" + "d" * 64, uuid.uuid4()))
    return configuration_id


def published_judge(client, auth, seeded, slug, configuration_id):
    judge = client.post(f"/projects/{seeded['project']}/judges", headers=auth,
                        json={"slug": slug, "displayName": slug.title()})
    assert judge.status_code == 201, judge.text
    version = client.post(f"/judges/{judge.json()['id']}/versions", headers=auth,
                          json={"rubric": f"Score {slug}.",
                                "modelConfigurationId": configuration_id})
    assert version.status_code == 201, version.text
    published = client.post(f"/judge-versions/{version.json()['id']}/publish",
                            headers=auth)
    assert published.status_code == 200, published.text
    return published.json()


def ensemble_of(client, auth, seeded, configuration_id, **overrides):
    a = published_judge(client, auth, seeded, "helpfulness", seeded["model_configuration"])
    b = published_judge(client, auth, seeded, "faithfulness", configuration_id)
    body = {"slug": "release-panel", "judgeVersionIds": [a["id"], b["id"]],
            "agreementThreshold": "0.20", "minimumScoringVotes": 2}
    body.update(overrides)
    created = client.post(f"/projects/{seeded['project']}/judge-ensembles",
                          headers=auth, json=body)
    return created


# ------------------------------------------------------------------- judges
def test_a_judge_version_is_created_and_published(client, auth, seeded):
    version = published_judge(client, auth, seeded, "helpfulness",
                              seeded["model_configuration"])
    assert is_ulid(version["id"])
    assert version["state"] == "published"
    assert version["versionNumber"] == 1
    assert version["rubricDigest"].startswith("sha256:")


def test_a_second_version_of_a_judge_increments_rather_than_replacing(
        client, auth, seeded):
    judge = client.post(f"/projects/{seeded['project']}/judges", headers=auth,
                        json={"slug": "helpfulness", "displayName": "H"}).json()
    first = client.post(f"/judges/{judge['id']}/versions", headers=auth,
                        json={"rubric": "v1",
                              "modelConfigurationId": seeded["model_configuration"]})
    second = client.post(f"/judges/{judge['id']}/versions", headers=auth,
                         json={"rubric": "v2",
                               "modelConfigurationId": seeded["model_configuration"]})
    assert first.json()["versionNumber"] == 1
    assert second.json()["versionNumber"] == 2
    assert first.json()["contentDigest"] != second.json()["contentDigest"]


# ---------------------------------------------------------------- ensembles
def test_an_ensemble_is_created_from_published_judges(client, auth, seeded,
                                                      second_configuration):
    created = ensemble_of(client, auth, seeded, second_configuration)
    assert created.status_code == 201, created.text
    body = created.json()
    assert len(body["judgeVersionIds"]) == 2
    assert body["agreementThreshold"] == "0.200000000"

    read = client.get(f"/judge-ensembles/{body['id']}", headers=auth)
    assert read.status_code == 200
    assert read.json()["judgeVersionIds"] == body["judgeVersionIds"]


def test_an_ensemble_of_one_configuration_is_a_caller_error(client, auth, seeded):
    """ADR-004 D-1 through the HTTP surface: 422, not 503. The request is wrong,
    not the platform."""
    a = published_judge(client, auth, seeded, "helpfulness",
                        seeded["model_configuration"])
    b = published_judge(client, auth, seeded, "faithfulness",
                        seeded["model_configuration"])
    refused = client.post(f"/projects/{seeded['project']}/judge-ensembles",
                          headers=auth,
                          json={"slug": "twins",
                                "judgeVersionIds": [a["id"], b["id"]],
                                "agreementThreshold": "0.2"})
    assert refused.status_code == 422
    assert "correlated" in refused.json()["detail"]
    assert refused.json()["category"] == "client_error"


def test_an_ensemble_of_draft_judges_is_refused(client, auth, seeded,
                                                second_configuration):
    judge = client.post(f"/projects/{seeded['project']}/judges", headers=auth,
                        json={"slug": "draftonly", "displayName": "D"}).json()
    draft = client.post(f"/judges/{judge['id']}/versions", headers=auth,
                        json={"rubric": "v1",
                              "modelConfigurationId": second_configuration}).json()
    published = published_judge(client, auth, seeded, "helpfulness",
                                seeded["model_configuration"])
    refused = client.post(f"/projects/{seeded['project']}/judge-ensembles",
                          headers=auth,
                          json={"slug": "half-baked",
                                "judgeVersionIds": [draft["id"], published["id"]],
                                "agreementThreshold": "0.2"})
    assert refused.status_code == 422
    assert "draft" in refused.json()["detail"]


def test_an_ensemble_with_no_threshold_is_accepted_and_will_escalate(
        client, auth, seeded, second_configuration):
    """ADR-017 §3. Uncalibrated is a legitimate state; silently guessing is not."""
    created = ensemble_of(client, auth, seeded, second_configuration,
                          agreementThreshold=None)
    assert created.status_code == 201, created.text
    assert created.json()["agreementThreshold"] is None


# ------------------------------------------------------- judgements and memory
def judged_run(migrated_database, seeded, examples, ensemble_id, version_ids,
               scores, key):
    """Record one ensemble judgement per sample, then its consensus."""
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key=key)
    judges = [JudgeVersion(slug=v, version="1", model=f"m{i}",
                           endpoint_name=f"e{i}", rubric="r")
              for i, v in enumerate(version_ids)]
    ensemble = Ensemble(judges=tuple(judges), agreement_threshold=Decimal("0.20"),
                        minimum_scoring_votes=2)
    from clep.db.session import tenant_session
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = JudgeRepository(conn, seeded["organization"])
        samples = conn.execute(
            "SELECT id FROM clep.run_sample WHERE run_id = %s ORDER BY sample_index",
            (ulid_to_uuid(run_id),)).fetchall()
        from clep.identity import uuid_to_ulid
        for index, (sample,) in enumerate(samples):
            votes = []
            for judge, version_id, score in zip(judges, version_ids,
                                                scores[index % len(scores)]):
                vote = Vote(judge=judge, resolution="scored",
                            score=Decimal(str(score)), cost=Decimal("0.001"),
                            currency="USD", latency_ms=12)
                repo.record_judgement(
                    run_id=run_id, run_sample_id=uuid_to_ulid(sample),
                    judge_version_id=version_id, vote=vote, prompt_digest=DIGEST,
                    idempotency_key=f"{key}:{index}:{version_id}")
                votes.append(vote)
            repo.record_consensus(run_id=run_id,
                                  run_sample_id=uuid_to_ulid(sample),
                                  ensemble_id=ensemble_id,
                                  consensus=reach_consensus(ensemble, votes),
                                  project_id=seeded["project"])
    return run_id


def test_a_judgement_that_scored_has_a_vote_and_one_that_did_not_has_none(
        client, auth, migrated_database, seeded, examples, second_configuration):
    """REQ-X-8 in the store: an unscored judgement has nothing to read as zero."""
    ensemble = ensemble_of(client, auth, seeded, second_configuration).json()
    version_ids = ensemble["judgeVersionIds"]
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="jv1")
    from clep.db.session import tenant_session
    from clep.identity import uuid_to_ulid
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = JudgeRepository(conn, seeded["organization"])
        sample = conn.execute(
            "SELECT id FROM clep.run_sample WHERE run_id = %s LIMIT 1",
            (ulid_to_uuid(run_id),)).fetchone()[0]
        judge = JudgeVersion(slug="a", version="1", model="m", endpoint_name="e",
                             rubric="r")
        repo.record_judgement(
            run_id=run_id, run_sample_id=uuid_to_ulid(sample),
            judge_version_id=version_ids[0],
            vote=Vote(judge=judge, resolution="scored", score=Decimal("0.8")),
            prompt_digest=DIGEST, idempotency_key="jv1:scored")
        repo.record_judgement(
            run_id=run_id, run_sample_id=uuid_to_ulid(sample),
            judge_version_id=version_ids[1],
            vote=Vote(judge=judge, resolution="abstained"),
            prompt_digest=DIGEST, idempotency_key="jv1:abstained")
        rows = conn.execute(
            "SELECT count(*) FROM clep.judge_run WHERE run_id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0]
        votes = conn.execute(
            "SELECT count(*) FROM clep.judge_vote v JOIN clep.judge_run r "
            "ON r.id = v.judge_run_id WHERE r.run_id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0]
    assert rows == 2
    assert votes == 1


def test_a_judgement_is_recorded_once_under_duplicate_delivery(
        client, auth, migrated_database, seeded, examples, second_configuration):
    ensemble = ensemble_of(client, auth, seeded, second_configuration).json()
    run_id = build_run(migrated_database, seeded, examples, BASELINE_SCORES,
                       key="jv2")
    from clep.db.session import tenant_session
    from clep.identity import uuid_to_ulid
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = JudgeRepository(conn, seeded["organization"])
        sample = conn.execute(
            "SELECT id FROM clep.run_sample WHERE run_id = %s LIMIT 1",
            (ulid_to_uuid(run_id),)).fetchone()[0]
        judge = JudgeVersion(slug="a", version="1", model="m", endpoint_name="e",
                             rubric="r")
        vote = Vote(judge=judge, resolution="scored", score=Decimal("0.8"),
                    cost=Decimal("0.002"), currency="USD")
        for _ in range(2):
            repo.record_judgement(
                run_id=run_id, run_sample_id=uuid_to_ulid(sample),
                judge_version_id=ensemble["judgeVersionIds"][0], vote=vote,
                prompt_digest=DIGEST, idempotency_key="jv2:once")
        assert conn.execute(
            "SELECT count(*) FROM clep.judge_run WHERE run_id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0] == 1


def test_disagreement_raises_an_escalation_that_a_person_closes(
        client, auth, migrated_database, seeded, examples, second_configuration):
    ensemble = ensemble_of(client, auth, seeded, second_configuration).json()
    judged_run(migrated_database, seeded, examples, ensemble["id"],
               ensemble["judgeVersionIds"], scores=[(0.1, 0.9)], key="esc1")

    listed = client.get(f"/projects/{seeded['project']}/escalations",
                        headers=auth, params={"state": "open"})
    assert listed.status_code == 200, listed.text
    items = listed.json()["items"]
    assert items, "a spread of 0.8 against a threshold of 0.2 did not escalate"
    assert items[0]["reason"] == "disagreement_above_threshold"

    reviewed = client.post(f"/escalations/{items[0]['id']}/review", headers=auth,
                           json={"outcome": "candidate_is_correct",
                                 "justification": "judge b misread the rubric"})
    assert reviewed.status_code == 200, reviewed.text
    assert reviewed.json()["state"] == "reviewed"

    again = client.post(f"/escalations/{items[0]['id']}/review", headers=auth,
                        json={"outcome": "changed my mind",
                              "justification": "second thoughts"})
    assert again.status_code == 422
    assert "terminal" in again.json()["detail"]


def test_agreement_raises_no_escalation(client, auth, migrated_database, seeded,
                                        examples, second_configuration):
    ensemble = ensemble_of(client, auth, seeded, second_configuration).json()
    judged_run(migrated_database, seeded, examples, ensemble["id"],
               ensemble["judgeVersionIds"], scores=[(0.80, 0.85)], key="esc2")
    listed = client.get(f"/projects/{seeded['project']}/escalations", headers=auth)
    assert listed.json()["items"] == []


def test_memory_reports_calibration_escalations_and_the_audit_floor(
        client, auth, migrated_database, seeded, examples, second_configuration):
    ensemble = ensemble_of(client, auth, seeded, second_configuration).json()
    judged_run(migrated_database, seeded, examples, ensemble["id"],
               ensemble["judgeVersionIds"], scores=[(0.60, 0.95)], key="mem1")

    memory = client.get(f"/projects/{seeded['project']}/evaluation-memory",
                        headers=auth)
    assert memory.status_code == 200, memory.text
    body = memory.json()
    assert body["retentionFloorDays"] == 2555
    assert body["escalations"] >= 1
    calibration = {c["judgeVersionId"]: c for c in body["judgeCalibration"]}
    assert set(calibration) == set(ensemble["judgeVersionIds"])
    assert all(c["judgements"] > 0 for c in calibration.values())


def test_memory_is_derived_and_never_disagrees_with_the_records(
        client, auth, migrated_database, seeded, examples, second_configuration):
    """A summary that can disagree with what it summarises is worse than none."""
    ensemble = ensemble_of(client, auth, seeded, second_configuration).json()
    judged_run(migrated_database, seeded, examples, ensemble["id"],
               ensemble["judgeVersionIds"], scores=[(0.10, 0.95)], key="mem2")
    memory = client.get(f"/projects/{seeded['project']}/evaluation-memory",
                        headers=auth).json()
    listed = client.get(f"/projects/{seeded['project']}/escalations",
                        headers=auth).json()
    assert memory["escalations"] == len(listed["items"])


def test_a_narrow_window_narrows_memory_without_touching_the_floor(
        client, auth, migrated_database, seeded, examples, second_configuration):
    ensemble = ensemble_of(client, auth, seeded, second_configuration).json()
    judged_run(migrated_database, seeded, examples, ensemble["id"],
               ensemble["judgeVersionIds"], scores=[(0.10, 0.95)], key="mem3")
    narrow = client.get(f"/projects/{seeded['project']}/evaluation-memory",
                        headers=auth, params={"windowDays": 1}).json()
    assert narrow["windowDays"] == 1
    assert narrow["retentionFloorDays"] == 2555


# -------------------------------------------------------------------- plans
def plan_body(seeded, **over):
    body = {"objective": "does the new prompt refuse less often",
            "suiteVersionId": seeded["suite_version"],
            "candidates": [{"label": "candidate-a",
                            "modelConfigurationId": seeded["model_configuration"]}]}
    body.update(over)
    return body


def test_a_plan_is_drafted_with_the_reasoning_that_produced_it(client, auth,
                                                               seeded):
    created = client.post(f"/projects/{seeded['project']}/evaluation-plans",
                          headers=auth, json=plan_body(seeded))
    assert created.status_code == 201, created.text
    plan = created.json()
    assert plan["state"] == "draft"
    assert plan["steps"][0]["kind"] == "score_candidate"
    assert plan["reasoning"]["state"] == "accepted"
    assert plan["reasoning"]["attempts"][0]["accepted"] is True
    assert plan["reasoning"]["maxIterations"] >= 1
    assert plan["digest"].startswith("sha256:")


def test_a_plan_is_amended_then_accepted_and_then_frozen(client, auth, seeded):
    created = client.post(f"/projects/{seeded['project']}/evaluation-plans",
                          headers=auth, json=plan_body(seeded)).json()
    amended = client.post(f"/evaluation-plans/{created['id']}/amendments",
                          headers=auth, json={"note": "reviewed the candidate list"})
    assert amended.status_code == 200, amended.text
    assert amended.json()["amendments"][0]["note"] == "reviewed the candidate list"
    assert amended.json()["amendments"][0]["priorDigest"] == created["digest"]

    accepted = client.post(f"/evaluation-plans/{created['id']}/acceptance",
                           headers=auth, json={"justification": "looks right"})
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["state"] == "accepted"
    assert accepted.json()["acceptedBy"]

    refused = client.post(f"/evaluation-plans/{created['id']}/amendments",
                          headers=auth, json={"note": "one more thing"})
    assert refused.status_code == 422
    assert refused.json()["category"] == "client_error"


def test_a_plan_is_read_back_with_its_steps_and_trace(client, auth, seeded):
    created = client.post(f"/projects/{seeded['project']}/evaluation-plans",
                          headers=auth, json=plan_body(seeded)).json()
    read = client.get(f"/evaluation-plans/{created['id']}", headers=auth)
    assert read.status_code == 200
    assert read.json()["steps"] == created["steps"]
    assert read.json()["reasoning"]["stoppedBecause"]


def test_an_unknown_plan_is_a_404_not_a_500(client, auth):
    assert client.get(f"/evaluation-plans/{new_ulid()}",
                      headers=auth).status_code == 404
