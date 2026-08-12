"""The API over the real service, over the real database.

The contract tests use a fake service so they can exercise every path cheaply.
That leaves the wiring — tenant context, ULID conversion, presentation in the
contract's vocabulary — untested, and wiring is where this kind of system
actually breaks. These tests close that gap by driving HTTP requests all the way
to PostgreSQL.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from clep.api.service import RunService, identity_digest
from clep.identity import new_ulid
from tests.conftest import api_app, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]


@pytest.fixture
def client(migrated_database, seeded):
    service = RunService(
        migrated_database,
        dataset_version_resolver=lambda org, suite: seeded["dataset_version"])
    return TestClient(api_app(migrated_database, service), raise_server_exceptions=False)


@pytest.fixture
def auth(seeded, owner_headers):
    """A credential the store verified, not a token naming a tenant.

    Phase 12 replaced the string that used to be here. Every test in
    this file now passes through authentication and authorization on
    its way to whatever it was written to check.
    """
    return owner_headers


def create(client, auth, seeded, key="e2e-1", **extra):
    body = {"suiteVersionId": seeded["suite_version"],
            "candidates": [{"label": "a",
                            "modelConfigurationId": seeded["model_configuration"]}]}
    body.update(extra)
    return client.post(f"/projects/{seeded['project']}/runs",
                       headers={**auth, "Idempotency-Key": key}, json=body)


def test_a_run_is_created_and_readable(client, auth, seeded):
    created = create(client, auth, seeded)
    assert created.status_code == 202
    body = created.json()
    assert body["executionState"] == "queued"
    assert body["completeness"] is None
    assert body["projectId"] == seeded["project"]

    fetched = client.get(f"/runs/{body['id']}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]


def test_identifiers_returned_are_the_contracts_form(client, auth, seeded):
    from clep.identity import is_ulid
    body = create(client, auth, seeded, key="e2e-ulid").json()
    assert is_ulid(body["id"])
    assert is_ulid(body["projectId"])


def test_resubmitting_the_same_key_does_not_create_a_second_run(
        client, auth, seeded):
    first = create(client, auth, seeded, key="e2e-same").json()
    second = create(client, auth, seeded, key="e2e-same").json()
    assert first["id"] == second["id"]


def test_a_different_key_creates_a_second_run(client, auth, seeded):
    first = create(client, auth, seeded, key="e2e-a").json()
    second = create(client, auth, seeded, key="e2e-b").json()
    assert first["id"] != second["id"]


def test_run_identity_is_derived_and_stable():
    """REQ-F-07-1: frozen before execution and never updated. Two runs over the
    same inputs measure the same thing, which is what reproducibility rests on."""
    a = identity_digest("p", "s", "d", "full", "m1")
    b = identity_digest("p", "s", "d", "full", "m1")
    assert a == b and a.startswith("sha256:")
    assert identity_digest("p", "s", "d", "full", "m2") != a


def test_another_tenant_cannot_read_the_run_through_the_api(
        client, auth, seeded, intruder_headers):
    """The end-to-end version of the isolation tests: the tenant comes from the
    credential, reaches the session, and row-level security does the rest.

    Phase 12 made this test mean what it always claimed. The intruder used to
    present a token naming a tenant, which proved the store filtered rows and
    nothing about an attacker. This intruder holds a credential the store
    verified, and a genuine `owner` role in their own organization — the
    strongest position an outsider can occupy — and still sees a 404.
    """
    body = create(client, auth, seeded, key="e2e-iso").json()
    assert client.get(f"/runs/{body['id']}",
                      headers=intruder_headers).status_code == 404
    assert client.get(f"/runs/{body['id']}/samples",
                      headers=intruder_headers).status_code == 404


def test_cancelling_through_the_api_persists_the_reason(client, auth, seeded):
    body = create(client, auth, seeded, key="e2e-cancel").json()
    cancelled = client.post(f"/runs/{body['id']}/cancel", headers=auth).json()
    assert cancelled["completeness"] == "cancelled"
    assert cancelled["incompleteReason"]
    again = client.get(f"/runs/{body['id']}", headers=auth).json()
    assert again["completeness"] == "cancelled"
    assert again["executionState"] == "terminal"


def test_cancelling_an_already_terminal_run_does_not_rewrite_it(
        client, auth, seeded):
    body = create(client, auth, seeded, key="e2e-cancel2").json()
    first = client.post(f"/runs/{body['id']}/cancel", headers=auth).json()
    second = client.post(f"/runs/{body['id']}/cancel", headers=auth).json()
    assert first["incompleteReason"] == second["incompleteReason"]


def test_listing_samples_of_a_new_run_is_an_empty_page_not_an_error(
        client, auth, seeded):
    body = create(client, auth, seeded, key="e2e-samples").json()
    page = client.get(f"/runs/{body['id']}/samples", headers=auth)
    assert page.status_code == 200
    assert page.json() == {"items": [], "limit": 50, "offset": 0}


def test_a_budget_is_recorded_with_its_currency(client, auth, seeded):
    created = create(client, auth, seeded, key="e2e-budget",
                     budget={"limit": "2.50", "currency": "USD"})
    assert created.status_code == 202
    assert created.json()["cost"]["currency"] == "USD"


def test_an_unknown_run_is_a_404_for_its_own_tenant_too(client, auth):
    assert client.get(f"/runs/{new_ulid()}", headers=auth).status_code == 404
    assert client.post(f"/runs/{new_ulid()}/cancel", headers=auth).status_code == 404
