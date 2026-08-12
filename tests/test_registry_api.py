"""The registry and experiment operations, end to end through the HTTP surface.

Driven against a real database, because the properties worth testing here —
immutability, tenant isolation, an audit row per change — are properties of the
store and a fake would report whatever it was told to.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clep.api.registry_service import RegistryService, actor_uuid
from clep.api.service import RunService
from clep.identity import is_ulid, new_ulid
from tests.conftest import api_app, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]


@pytest.fixture
def client(migrated_database, seeded):
    run_service = RunService(
        migrated_database,
        dataset_version_resolver=lambda org, suite: seeded["dataset_version"])
    return TestClient(api_app(migrated_database, run_service, RegistryService(migrated_database)),
                      raise_server_exceptions=False)


@pytest.fixture
def auth(seeded, owner_headers):
    """A credential the store verified, not a token naming a tenant.

    Phase 12 replaced the string that used to be here. Every test in
    this file now passes through authentication and authorization on
    its way to whatever it was written to check.
    """
    return owner_headers


def create_version(client, auth, seeded, body="Answer briefly."):
    prompt = client.post(f"/projects/{seeded['project']}/prompts", headers=auth,
                         json={"slug": "api-" + new_ulid()[-8:].lower(),
                               "displayName": "API Prompt"})
    assert prompt.status_code == 201, prompt.text
    version = client.post(f"/prompts/{prompt.json()['id']}/versions",
                          headers=auth, json={"body": body})
    assert version.status_code == 201, version.text
    return prompt.json(), version.json()


# ------------------------------------------------------------------ registry
def test_a_prompt_and_its_first_version_are_created(client, auth, seeded):
    prompt, version = create_version(client, auth, seeded)
    assert is_ulid(prompt["id"]) and is_ulid(version["id"])
    assert version["versionNumber"] == 1
    assert version["state"] == "draft"
    assert version["contentDigest"].startswith("sha256:")


def test_the_digest_is_derived_here_not_accepted_from_the_caller(client, auth,
                                                                 seeded):
    """A caller-supplied digest is an assertion; the identity model needs a
    fact."""
    _, version = create_version(client, auth, seeded, body="exact text")
    from clep.registry.repository import content_digest
    assert version["contentDigest"] == content_digest("exact text")


def test_publishing_makes_the_version_immutable_and_is_idempotent(client, auth,
                                                                  seeded):
    _, version = create_version(client, auth, seeded)
    published = client.post(f"/prompt-versions/{version['id']}/publish", headers=auth)
    assert published.status_code == 200
    assert published.json()["state"] == "published"
    assert published.json()["publishedAt"] is not None
    again = client.post(f"/prompt-versions/{version['id']}/publish", headers=auth)
    assert again.status_code == 200, "a retried publish is not a failure"


def test_a_version_is_readable_and_carries_its_actor(client, auth, seeded,
                                                     owner_credential):
    """REQ-F-01-6: who changed the prompt, and when.

    The actor is now the principal the credential resolved to, rather than
    whatever subject the caller wrote after the colon. That is the difference
    Phase 12 makes to every audit column in the product: the name in it is one
    the store verified.
    """
    _, version = create_version(client, auth, seeded)
    fetched = client.get(f"/prompt-versions/{version['id']}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["createdBy"] == \
        str(actor_uuid(owner_credential["userId"]))
    assert fetched.json()["createdAt"] is not None


def test_the_change_is_recorded_in_the_audit_trail(client, auth, seeded,
                                                   migrated_database):
    from clep.db.session import tenant_session
    _, version = create_version(client, auth, seeded)
    client.post(f"/prompt-versions/{version['id']}/publish", headers=auth)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM clep.audit_event WHERE organization_id = %s "
            "ORDER BY occurred_at", (seeded["organization"],)).fetchall()]
    assert "prompt.created" in actions
    assert "prompt_version.created" in actions
    assert "prompt_version.published" in actions


def test_another_tenant_cannot_read_a_prompt_version(client, auth, seeded,
                                                     second_organization, intruder_headers):
    _, version = create_version(client, auth, seeded)
    intruder = intruder_headers
    assert client.get(f"/prompt-versions/{version['id']}",
                      headers=intruder).status_code == 404


def test_a_malformed_identifier_is_a_client_error_not_a_lookup(client, auth):
    assert client.get("/prompt-versions/not-a-ulid", headers=auth).status_code == 400


def test_the_registry_requires_a_credential(client, seeded):
    assert client.post(f"/projects/{seeded['project']}/prompts",
                       json={"slug": "x", "displayName": "X"}).status_code == 401


# --------------------------------------------------------------- experiments
def test_an_experiment_is_created_with_its_hypothesis(client, auth, seeded):
    response = client.post(f"/projects/{seeded['project']}/experiments",
                           headers=auth,
                           json={"slug": "exp-1", "displayName": "Experiment 1",
                                 "hypothesis": "shorter prompts score the same"})
    assert response.status_code == 201
    assert response.json()["hypothesis"] == "shorter prompts score the same"


# ------------------------------------------------------------- run identity
def _create_run(client, auth, seeded, key="api-identity"):
    return client.post(
        f"/projects/{seeded['project']}/runs",
        headers={**auth, "Idempotency-Key": key},
        json={"suiteVersionId": seeded["suite_version"],
              "candidates": [{"label": "a",
                              "modelConfigurationId": seeded["model_configuration"],
                              "promptVersionId": seeded["prompt_version"]}]})


def test_a_run_reports_every_field_the_contract_requires_of_its_identity(
        client, auth, seeded):
    """Phase 5 emitted `digest` alone while the contract had already declared
    seven required fields. The contract was right and the implementation was
    short."""
    from clep.api import contract
    run = _create_run(client, auth, seeded).json()
    required = contract.schema("RunIdentity")["required"]
    missing = [f for f in required if f not in run["identity"]]
    assert missing == [], f"RunIdentity is missing required field(s): {missing}"
    assert run["identity"]["modelConfigurationIds"] == [seeded["model_configuration"]]


def test_the_identity_is_readable_component_by_component(client, auth, seeded):
    run = _create_run(client, auth, seeded, key="components").json()
    identity = client.get(f"/runs/{run['id']}/identity", headers=auth)
    assert identity.status_code == 200
    body = identity.json()
    assert body["digest"] == run["identity"]["digest"]
    kinds = {c["kind"] for c in body["components"]}
    assert {"dataset_version", "suite_version", "model_configuration",
            "prompt_version", "evaluator_version", "environment"} <= kinds


def test_the_environment_component_is_marked_as_outside_the_digest(client, auth,
                                                                   seeded):
    run = _create_run(client, auth, seeded, key="in-digest").json()
    components = client.get(f"/runs/{run['id']}/identity",
                            headers=auth).json()["components"]
    by_kind = {c["kind"]: c["inDigest"] for c in components}
    assert by_kind["environment"] is False
    assert by_kind["dataset_version"] is True


def test_a_run_naming_a_configuration_that_does_not_exist_is_refused(client, auth,
                                                                     seeded):
    """The identity is built before the run, so this is refused rather than
    recorded as a run whose identity is partly unknown."""
    response = client.post(
        f"/projects/{seeded['project']}/runs",
        headers={**auth, "Idempotency-Key": "absent-config"},
        json={"suiteVersionId": seeded["suite_version"],
              "candidates": [{"modelConfigurationId": new_ulid()}]})
    assert response.status_code >= 400


def test_another_tenant_cannot_read_a_runs_identity(client, auth, seeded,
                                                    second_organization, intruder_headers):
    run = _create_run(client, auth, seeded, key="identity-isolation").json()
    intruder = intruder_headers
    assert client.get(f"/runs/{run['id']}/identity",
                      headers=intruder).status_code == 404


# ------------------------------------------------------------- reproduction
def test_reproducing_an_untouched_run_reports_no_gaps(client, auth, seeded):
    run = _create_run(client, auth, seeded, key="repro-api").json()
    response = client.post(f"/runs/{run['id']}/reproductions", headers=auth)
    assert response.status_code == 201
    body = response.json()
    assert body["outcome"] == "reproducible"
    assert body["gaps"] == []
    assert body["replayRunId"] is None


def test_reproducing_a_run_that_does_not_exist_is_a_404(client, auth):
    assert client.post(f"/runs/{new_ulid()}/reproductions",
                       headers=auth).status_code == 404
