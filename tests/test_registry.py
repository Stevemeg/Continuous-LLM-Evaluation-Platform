"""The prompt, model and system registry, and the immutability it exists for.

`REQ-F-01-1` is the reason this phase has a registry at all: a run identity is
only worth anything if the things it names cannot change afterwards. These tests
attack that from the store's side, connecting as the runtime role, because a rule
the application enforces is a rule that holds only while every caller remembers
it.
"""
from __future__ import annotations

import psycopg
import pytest

from clep.db.session import admin_session, tenant_session
from clep.identity import is_ulid, new_ulid, ulid_to_uuid
from clep.registry.repository import (RegistryRepository, canonical_parameters,
                                      content_digest, _looks_deterministic)
from tests.conftest import MIGRATION_DSN, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]


def repo(dsn, seeded):
    return tenant_session(dsn, seeded["organization"])


# ------------------------------------------------------------------- prompts
def test_a_prompt_version_gets_the_next_number_and_a_derived_digest(
        migrated_database, seeded):
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="greeting", display_name="Greeting")
        first = registry.add_prompt_version(prompt_id, body="Answer briefly.",
                                            created_by=seeded["organization"])
        second = registry.add_prompt_version(prompt_id, body="Answer at length.",
                                             created_by=seeded["organization"])
        rows = [registry.get_prompt_version(v) for v in (first, second)]
    assert [r.version_number for r in rows] == [1, 2]
    assert rows[0].content_digest == content_digest("Answer briefly.")
    assert rows[0].content_digest != rows[1].content_digest
    assert all(is_ulid(r.id) for r in rows)


def test_creating_the_same_prompt_slug_twice_converges_rather_than_failing(
        migrated_database, seeded):
    """Registration is retryable. A second POST after a lost response must not
    be an error, or every client needs to distinguish "already done" from
    "went wrong"."""
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        a = registry.create_prompt(project_id=seeded["project"], slug="same",
                                   display_name="Same")
        b = registry.create_prompt(project_id=seeded["project"], slug="same",
                                   display_name="Same")
    assert a == b


# -------------------------------------------------------------- immutability
def test_a_published_prompt_version_cannot_be_modified(migrated_database, seeded):
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="frozen", display_name="Frozen")
        version_id = registry.add_prompt_version(prompt_id, body="original",
                                                 created_by=seeded["organization"])
        registry.publish_prompt_version(version_id)

    with pytest.raises(psycopg.errors.Error):
        with repo(migrated_database, seeded) as conn:
            conn.execute("UPDATE clep.prompt_version SET body = 'replaced' "
                         "WHERE id = %s", (ulid_to_uuid(version_id),))

    with repo(migrated_database, seeded) as conn:
        body = conn.execute("SELECT body FROM clep.prompt_version WHERE id = %s",
                            (ulid_to_uuid(version_id),)).fetchone()[0]
    assert body == "original"


def test_changing_the_body_without_the_digest_is_refused(migrated_database, seeded):
    """The defect the first version of this trigger had.

    It enumerated content_digest, version_number and published_at, so `body` was
    not covered: a published version's text could be replaced while its digest
    stayed the same, and the digest would quietly stop describing the content.
    A digest that does not describe the content is worse than no digest.
    """
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="digest", display_name="Digest")
        version_id = registry.add_prompt_version(prompt_id, body="truth",
                                                 created_by=seeded["organization"])
        registry.publish_prompt_version(version_id)
    with pytest.raises(psycopg.errors.Error, match="immutable"):
        with repo(migrated_database, seeded) as conn:
            conn.execute("UPDATE clep.prompt_version SET body = 'lie' WHERE id = %s",
                         (ulid_to_uuid(version_id),))


def test_publication_is_one_way(migrated_database, seeded):
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="oneway", display_name="One Way")
        version_id = registry.add_prompt_version(prompt_id, body="x",
                                                 created_by=seeded["organization"])
        registry.publish_prompt_version(version_id)
    with pytest.raises(psycopg.errors.Error, match="immutable"):
        with repo(migrated_database, seeded) as conn:
            conn.execute("UPDATE clep.prompt_version SET state = 'draft', "
                         "published_at = NULL WHERE id = %s",
                         (ulid_to_uuid(version_id),))


def test_a_published_version_cannot_be_deleted(migrated_database, seeded):
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="nodelete", display_name="No Delete")
        version_id = registry.add_prompt_version(prompt_id, body="x",
                                                 created_by=seeded["organization"])
        registry.publish_prompt_version(version_id)
    # DELETE is not granted to the runtime role at all, so this is attempted
    # through the migration role — the strongest actor that is not a superuser.
    with pytest.raises(psycopg.errors.Error, match="immutable"):
        with admin_session(MIGRATION_DSN) as conn:
            conn.execute("DELETE FROM clep.prompt_version WHERE id = %s",
                         (ulid_to_uuid(version_id),))


def test_a_draft_is_still_mutable(migrated_database, seeded):
    """Immutability that started before publication would make drafts useless."""
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="draft", display_name="Draft")
        version_id = registry.add_prompt_version(prompt_id, body="first pass",
                                                 created_by=seeded["organization"])
        conn.execute("UPDATE clep.prompt_version SET body = 'second pass' "
                     "WHERE id = %s", (ulid_to_uuid(version_id),))
        body = conn.execute("SELECT body FROM clep.prompt_version WHERE id = %s",
                            (ulid_to_uuid(version_id),)).fetchone()[0]
    assert body == "second pass"


def test_a_version_referenced_by_a_completed_run_is_frozen_even_as_a_draft(
        migrated_database, seeded):
    """REQ-F-01-1 verbatim: "referenced by any completed run ... immutable
    thereafter". The run is the evidence, and evidence whose inputs can still
    move is not evidence."""
    from clep.orchestration.repository import RunRepository
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="evidence", display_name="Evidence")
        version_id = registry.add_prompt_version(prompt_id, body="used",
                                                 created_by=seeded["organization"])
        runs = RunRepository(conn, seeded["organization"])
        run_id = runs.create_run(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "0" * 64, integration_tier="output_only",
            idempotency_key="frozen-by-run")
        runs.add_candidate(run_id, label="a",
                           model_configuration_id=seeded["model_configuration"],
                           prompt_version_id=version_id, endpoint_kind="hosted")
        runs.finish_run(run_id, "complete", None)
        state = registry.get_prompt_version(version_id).state
    assert state == "draft", "the point of this test is that state is irrelevant"

    with pytest.raises(psycopg.errors.Error, match="REQ-F-01-1"):
        with repo(migrated_database, seeded) as conn:
            conn.execute("UPDATE clep.prompt_version SET body = 'rewritten' "
                         "WHERE id = %s", (ulid_to_uuid(version_id),))


def test_a_version_used_only_by_an_unfinished_run_is_not_yet_frozen(
        migrated_database, seeded):
    """The requirement says *completed*. Freezing on submission would stop a
    draft being corrected while its first run is still queued."""
    from clep.orchestration.repository import RunRepository
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="inflight", display_name="In Flight")
        version_id = registry.add_prompt_version(prompt_id, body="draft",
                                                 created_by=seeded["organization"])
        runs = RunRepository(conn, seeded["organization"])
        run_id = runs.create_run(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "1" * 64, integration_tier="output_only",
            idempotency_key="not-frozen-yet")
        runs.add_candidate(run_id, label="a",
                           model_configuration_id=seeded["model_configuration"],
                           prompt_version_id=version_id, endpoint_kind="hosted")
        conn.execute("UPDATE clep.prompt_version SET body = 'corrected' "
                     "WHERE id = %s", (ulid_to_uuid(version_id),))
        body = conn.execute("SELECT body FROM clep.prompt_version WHERE id = %s",
                            (ulid_to_uuid(version_id),)).fetchone()[0]
    assert body == "corrected"


def test_publishing_an_already_published_version_is_not_an_error(
        migrated_database, seeded):
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        prompt_id = registry.create_prompt(project_id=seeded["project"],
                                           slug="twice", display_name="Twice")
        version_id = registry.add_prompt_version(prompt_id, body="x",
                                                 created_by=seeded["organization"])
        registry.publish_prompt_version(version_id)
        registry.publish_prompt_version(version_id)
        assert registry.get_prompt_version(version_id).is_published


# ------------------------------------------------- model configuration policy
@pytest.mark.parametrize("parameters,seed,expected", [
    ({"temperature": 0}, None, True),
    ({"temperature": 0.0, "top_p": 1.0}, None, True),
    ({"temperature": 0.7}, None, False),
    ({"temperature": 0.7}, 42, True),
    ({"top_p": 0.9}, None, False),
    ({}, None, False),
    ({}, 42, True),
])
def test_determinism_is_inferred_conservatively(parameters, seed, expected):
    """Defaulting to "deterministic" would let a sampled configuration into the
    cache, and REQ-F-07-4 would then be false in the direction nobody notices —
    the results still look entirely plausible."""
    assert _looks_deterministic(parameters, seed) is expected


def test_parameters_are_encoded_so_the_digest_does_not_depend_on_key_order():
    a = canonical_parameters({"temperature": 0, "top_p": 1})
    b = canonical_parameters({"top_p": 1, "temperature": 0})
    assert a == b
    assert content_digest(a) == content_digest(b)


def test_a_model_configuration_records_its_parameters_and_seed(
        migrated_database, seeded):
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        provider_id = registry.create_provider(slug="local", display_name="Local",
                                               endpoint_kind="self_hosted")
        model_id = registry.create_model(provider_id, model_identifier="qwen",
                                         display_name="Qwen")
        configuration_id = registry.add_model_configuration(
            model_id, parameters={"temperature": 0.7}, seed=11,
            created_by=seeded["organization"])
        stored = registry.get_model_configuration(configuration_id)
    assert stored["seed"] == 11
    assert stored["isDeterministic"] is True
    assert stored["parameters"] == {"temperature": 0.7}


def test_a_self_hosted_endpoint_is_a_first_class_provider(migrated_database, seeded):
    """REQ-F-02-4. Not a special case, not a flag on a hosted provider."""
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        provider_id = registry.create_provider(slug="onprem", display_name="On Prem",
                                               endpoint_kind="self_hosted")
        kind = conn.execute(
            "SELECT endpoint_kind FROM clep.provider WHERE id = %s",
            (ulid_to_uuid(provider_id),)).fetchone()[0]
    assert kind == "self_hosted"


def test_an_unknown_endpoint_kind_is_refused_before_it_reaches_the_store(
        migrated_database, seeded):
    from clep.registry.repository import RegistryError
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        with pytest.raises(RegistryError, match="self_hosted"):
            registry.create_provider(slug="x", display_name="X",
                                     endpoint_kind="serverless")


def test_the_registry_stores_no_credential_column(migrated_database):
    """A registry row is a thing many people can read. Endpoints and keys are
    deployment configuration and are read from the environment."""
    with psycopg.connect(MIGRATION_DSN) as conn:
        columns = [r[0] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'clep' AND table_name IN "
            "('provider', 'model', 'model_configuration')").fetchall()]
    forbidden = [c for c in columns
                 if any(w in c for w in ("key", "secret", "token", "password",
                                         "credential", "base_url", "url"))]
    assert forbidden == [], f"registry exposes credential-shaped column(s): {forbidden}"


# ------------------------------------------------------------------- systems
def test_a_system_version_digest_is_derived_from_its_parts(migrated_database, seeded):
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        system_id = registry.create_system(project_id=seeded["project"],
                                           slug="under-test", display_name="Under Test")
        version_id = registry.add_system_version(
            system_id, model_configuration_id=seeded["model_configuration"],
            prompt_version_id=seeded["prompt_version"],
            created_by=seeded["organization"])
        stored = registry.get_system_version(version_id)
        configuration = registry.get_model_configuration(seeded["model_configuration"])
        prompt_version = registry.get_prompt_version(seeded["prompt_version"])
    assert stored["contentDigest"] == content_digest(
        configuration["contentDigest"], prompt_version.content_digest)


def test_a_system_version_cannot_name_a_configuration_that_does_not_exist(
        migrated_database, seeded):
    from clep.registry.repository import RegistryError
    with repo(migrated_database, seeded) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        system_id = registry.create_system(project_id=seeded["project"],
                                           slug="absent", display_name="Absent")
        with pytest.raises(RegistryError, match="does not exist"):
            registry.add_system_version(
                system_id, model_configuration_id=new_ulid(),
                prompt_version_id=None, created_by=seeded["organization"])
