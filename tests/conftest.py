"""Test fixtures.

Integration tests run against real PostgreSQL, not a fake. `REQ-N-OPS-1` asks for
it and row-level security makes it non-negotiable: a fake cannot enforce a
policy, so tenant isolation tested against one is not tested at all.

Tests that need the database are marked `integration` and skip cleanly when it is
absent, so the unit suite stays runnable anywhere. A skipped isolation test is
reported as skipped, never as passed.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# No password in any default. The local compose stack uses trust authentication,
# so there is no local credential to commit, and CI or a shared environment
# supplies real ones through CLEP_MIGRATION_DSN and CLEP_RUNTIME_DSN.
MIGRATION_DSN = os.environ.get(
    "CLEP_MIGRATION_DSN", "postgresql://postgres@localhost:5439/clep")
RUNTIME_DSN = os.environ.get(
    "CLEP_RUNTIME_DSN", "postgresql://clep_app@localhost:5439/clep")
RUNTIME_PASSWORD = os.environ.get("CLEP_RUNTIME_PASSWORD", "")


def _postgres_available() -> bool:
    try:
        import psycopg
        with psycopg.connect(MIGRATION_DSN, connect_timeout=3):
            return True
    except Exception:
        return False


requires_postgres = pytest.mark.skipif(
    not _postgres_available(),
    reason="PostgreSQL is not reachable; run `docker compose up -d`")

REDIS_URL = os.environ.get("CLEP_REDIS_URL", "redis://localhost:6399")


def _redis_available() -> bool:
    try:
        import redis
        redis.Redis.from_url(REDIS_URL, socket_connect_timeout=3).ping()
        return True
    except Exception:
        return False


#: The scheduled-execution test drives a real arq worker against a real broker.
#: There is no fake: the thing under test is that the queue's own cron fires,
#: and a fake cron proves only that the fake was called.
requires_redis = pytest.mark.skipif(
    not _redis_available(),
    reason="Redis is not reachable; run `docker compose up -d`")


@pytest.fixture(scope="session")
def migrated_database():
    """Apply the schema specification, then hand back a runtime DSN.

    The migration role owns the schema; the returned DSN belongs to a role that
    owns nothing and cannot bypass row-level security (ADR-012 D-3, D-4). Tests
    that connect with anything else are not testing what is deployed.
    """
    import psycopg

    from clep.db import migrations, provision

    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS clep CASCADE")
        conn.execute("DROP TABLE IF EXISTS clep_schema_history")
        for role in ("clep_app", "clep_runtime", "clep_migration"):
            conn.execute(
                f"DO $$BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname='{role}') "
                f"THEN EXECUTE 'DROP OWNED BY {role}'; EXECUTE 'DROP ROLE {role}'; "
                f"END IF; END$$;")
        applied = migrations.apply(
            conn, migrations.discover(migrations.schema_dir(ROOT)))
        assert applied, "no schema files were applied"
        provision.ensure_login_roles(conn, RUNTIME_PASSWORD)
    return RUNTIME_DSN


@pytest.fixture
def organization(migrated_database):
    """A tenant, created through the migration role because creating the tenant
    root is provisioning rather than tenant-scoped work."""
    import psycopg
    org_id = uuid.uuid4()
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clep.organization (id, slug, display_name) "
            "VALUES (%s, %s, %s)",
            (org_id, f"org-{org_id.hex[:10]}", "Test Organization"))
    return str(org_id)


@pytest.fixture
def second_organization(migrated_database):
    import psycopg
    org_id = uuid.uuid4()
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clep.organization (id, slug, display_name) "
            "VALUES (%s, %s, %s)",
            (org_id, f"org-{org_id.hex[:10]}", "Other Organization"))
    return str(org_id)


@pytest.fixture
def owner_credential(migrated_database, organization):
    """A real credential for a real principal holding the `owner` role.

    Phase 12 deliberately did not give the API tests a fake authenticator. They
    already run against PostgreSQL, so they can present a credential the store
    verifies — which means every one of them now exercises the authentication
    and authorization path as well as whatever it was written for. A fake here
    would have made 96 tests pass while proving nothing about the boundary that
    phase exists to build.
    """
    import psycopg

    from clep.db import provision
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        user_id, presented = provision.bootstrap_organization(
            conn, organization, external_subject=f"owner-{organization}")
    return {"organization": organization, "userId": user_id,
            "credential": presented}


@pytest.fixture
def owner_headers(owner_credential):
    return {"Authorization": f"Bearer {owner_credential['credential']}"}


@pytest.fixture
def intruder_headers(migrated_database, second_organization):
    """A verified principal of ANOTHER tenant.

    The important word is *verified*. Before Phase 12 a cross-tenant test
    presented a token naming someone else's organization, which proved that the
    store filtered rows; it could not prove anything about a real attacker,
    because the token was not a credential. This one is: the intruder holds a
    genuine `owner` role, in their own organization, and still sees nothing.
    """
    import psycopg

    from clep.db import provision
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        _user_id, presented = provision.bootstrap_organization(
            conn, second_organization,
            external_subject=f"intruder-{second_organization}")
    return {"Authorization": f"Bearer {presented}"}


def api_app(runtime_dsn, *services, **kwargs):
    """Build the application the way a deployment does.

    One helper so that a change to how the application is wired is one edit
    rather than seven, and so no test can accidentally build an application
    whose authenticator is not the real one.
    """
    from clep.api.app import create_app
    from clep.api.security_service import SecurityService
    security = SecurityService(runtime_dsn)
    return create_app(*services, authenticator=security.authenticator(),
                      security_service=security, **kwargs)


@pytest.fixture
def seeded(migrated_database, organization):
    """A project, dataset version and suite version for one tenant.

    Seeded through the migration role: these belong to registry phases that have
    not run, and inventing an API for them here would be inventing scope.
    """
    import psycopg

    from clep.identity import new_ulid, ulid_to_uuid
    ids = {k: new_ulid() for k in
           ("project", "dataset", "dataset_version", "suite", "suite_version",
            "example", "provider", "model", "model_configuration",
            "evaluator_definition", "evaluator_version", "prompt",
            "prompt_version")}
    org = organization
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        u = ulid_to_uuid
        conn.execute("INSERT INTO clep.project (id, organization_id, slug, display_name)"
                     " VALUES (%s,%s,%s,%s)",
                     (u(ids["project"]), org, "proj", "Project"))
        conn.execute("INSERT INTO clep.dataset (id, organization_id, project_id, slug,"
                     " display_name) VALUES (%s,%s,%s,%s,%s)",
                     (u(ids["dataset"]), org, u(ids["project"]), "ds", "Dataset"))
        conn.execute("INSERT INTO clep.dataset_version (id, organization_id, dataset_id,"
                     " version_number, content_digest, schema_ref, state)"
                     " VALUES (%s,%s,%s,1,%s,'schema://example/v1','draft')",
                     (u(ids["dataset_version"]), org, u(ids["dataset"]),
                      "sha256:" + "a" * 64))
        conn.execute("INSERT INTO clep.example (id, organization_id,"
                     " dataset_version_id, ordinal, split) VALUES (%s,%s,%s,0,'test')",
                     (u(ids["example"]), org, u(ids["dataset_version"])))
        # Content, not just the record. An example with no content row cannot be
        # re-evaluated, so a fixture without one makes every reproduction report
        # a gap that is an artefact of the fixture rather than of the data.
        conn.execute("INSERT INTO clep.example_content (id, organization_id,"
                     " example_id, content_digest, payload_ref, byte_size)"
                     " VALUES (%s,%s,%s,%s,'s3://bucket/example-0',128)",
                     (uuid.uuid4(), org, u(ids["example"]), "sha256:" + "b" * 64))
        conn.execute("INSERT INTO clep.benchmark_suite (id, organization_id, project_id,"
                     " slug, display_name, owner_actor_id) VALUES (%s,%s,%s,%s,%s,%s)",
                     (u(ids["suite"]), org, u(ids["project"]), "suite", "Suite",
                      uuid.uuid4()))
        conn.execute("INSERT INTO clep.suite_version (id, organization_id,"
                     " benchmark_suite_id, version_number, content_digest,"
                     " owner_actor_id) VALUES (%s,%s,%s,1,%s,%s)",
                     (u(ids["suite_version"]), org, u(ids["suite"]),
                      "sha256:" + "d" * 64, uuid.uuid4()))
        # Built-in evaluator slugs are globally unique, so each test seeds its
        # own rather than colliding with the previous one's.
        conn.execute("INSERT INTO clep.evaluator_definition (id, organization_id, scope,"
                     " slug, display_name) VALUES (%s,NULL,'builtin',%s,%s)",
                     (u(ids["evaluator_definition"]),
                      f"exact_match_{ids['evaluator_definition'][-8:].lower()}",
                      "Exact Match"))
        conn.execute("INSERT INTO clep.evaluator_version (id, organization_id,"
                     " evaluator_definition_id, version_number, content_digest,"
                     " input_schema_ref, output_schema_ref, declared_permissions,"
                     " is_deterministic, cost_class) VALUES (%s,NULL,%s,1,%s,"
                     "'schema://in/v1','schema://out/v1','none',true,'free')",
                     (u(ids["evaluator_version"]), u(ids["evaluator_definition"]),
                      "sha256:" + "c" * 64))
        # What the suite version measures. Phase 11 reads this: a scheduled run
        # has no caller to name a dataset, so it resolves one through the suite.
        # A suite version with no member is a fixture artefact — it would make
        # every schedule unexecutable for a reason that is not about schedules.
        conn.execute("INSERT INTO clep.suite_member (id, organization_id,"
                     " suite_version_id, dataset_version_id) VALUES (%s,%s,%s,%s)",
                     (uuid.uuid4(), org, u(ids["suite_version"]),
                      u(ids["dataset_version"])))
        # The suite's evaluators are part of run identity (REQ-F-07-1) and are
        # read from the suite rather than from the request, so the fixture has to
        # attach one or every captured identity would be missing that component.
        conn.execute("INSERT INTO clep.suite_evaluator (id, organization_id,"
                     " suite_version_id, evaluator_version_id) VALUES (%s,%s,%s,%s)",
                     (uuid.uuid4(), org, u(ids["suite_version"]),
                      u(ids["evaluator_version"])))
        # Phase 6 gives run_candidate real foreign keys, so a run can no longer
        # name a model configuration that does not exist. The registry rows are
        # seeded here for the same reason the dataset is: they belong to a
        # capability under test, not to the test.
        conn.execute("INSERT INTO clep.provider (id, organization_id, slug,"
                     " display_name, endpoint_kind)"
                     " VALUES (%s,%s,'stub','Stub Provider','hosted')",
                     (u(ids["provider"]), org))
        conn.execute("INSERT INTO clep.model (id, organization_id, provider_id,"
                     " model_identifier, display_name) VALUES (%s,%s,%s,'m','Model')",
                     (u(ids["model"]), org, u(ids["provider"])))
        conn.execute("INSERT INTO clep.model_configuration (id, organization_id,"
                     " model_id, version_number, output_affecting_parameters,"
                     " content_digest, seed, is_deterministic, created_by)"
                     " VALUES (%s,%s,%s,1,'{\"temperature\": 0}',%s,7,true,%s)",
                     (u(ids["model_configuration"]), org, u(ids["model"]),
                      "sha256:" + "e" * 64, uuid.uuid4()))
        conn.execute("INSERT INTO clep.prompt (id, organization_id, project_id,"
                     " slug, display_name) VALUES (%s,%s,%s,'p','Prompt')",
                     (u(ids["prompt"]), org, u(ids["project"])))
        conn.execute("INSERT INTO clep.prompt_version (id, organization_id,"
                     " prompt_id, version_number, content_digest, body, created_by)"
                     " VALUES (%s,%s,%s,1,%s,'Answer briefly.',%s)",
                     (u(ids["prompt_version"]), org, u(ids["prompt"]),
                      "sha256:" + "f" * 64, uuid.uuid4()))
    ids["organization"] = org
    return ids
