"""Tenant isolation, tested against the database that enforces it.

ADR-010 rule 6 requires a negative test per tenant-scoped table. ADR-012 records
why a positive test is not enough: PostgreSQL table owners bypass row-level
security by default, so a schema can look correct, pass every static check, and
isolate nothing. Only a query issued by the role the application actually uses
can tell the difference.

Every test here connects as `clep_app`, which owns nothing, is not a superuser,
and cannot bypass row security.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from clep.db.session import admin_session, tenant_session, TenantContextError
from clep.identity import new_ulid, ulid_to_uuid
from clep.orchestration.repository import RunRepository
from tests.conftest import MIGRATION_DSN, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]

TENANT_SCOPED = [
    "project", "dataset", "dataset_version", "example", "example_content",
    "dataset_approval", "dataset_lineage", "quality_check_result",
    "dataset_label",
    "benchmark_suite", "suite_version", "suite_member", "suite_grant",
    "suite_evaluator", "threshold", "artifact", "artifact_reference",
    # Dual-scoped: a NULL organization_id is the enumerated global exception
    # under ADR-010 rule 4, and the policy has a NULL branch in USING but not
    # in WITH CHECK. ENABLE and FORCE are required of them all the same.
    "evaluator_definition", "evaluator_version",
    "audit_event", "erasure_request", "run", "run_candidate", "run_sample",
    "sample_cost", "run_checkpoint", "evaluator_outcome",
    # Phase 6. The registry is where a self-hosted endpoint and a proprietary
    # prompt live, so it is the last place isolation may be assumed rather than
    # tested.
    "prompt", "prompt_version", "provider", "model", "model_configuration",
    "system_definition", "system_version", "experiment",
    "run_identity_component", "reproduction_attempt", "reproduction_gap",
    "result_cache",
    # Phase 7. A gate decision is the record of why a release shipped, and a
    # policy is one tenant's statement of what it will accept; neither may be
    # visible to, or writable by, anyone else.
    "baseline", "gate_policy", "gate_policy_version", "gate_criterion",
    "gate_decision", "comparison", "gate_criterion_result", "policy_exception",
    # Phase 8. A judge's rubric is one tenant's statement of what good looks
    # like, a judgement is the evidence behind a score, and an escalation names
    # a person who was asked to look. None of it may cross a tenant boundary.
    "judge_definition", "judge_version", "judge_ensemble",
    "judge_ensemble_member", "judge_run", "judge_vote", "consensus_result",
    "escalation", "evaluation_plan", "plan_step", "plan_amendment",
    "reasoning_trace", "reasoning_attempt",
    # Phase 9. A retrieved passage and a tool result are one tenant's data
    # arriving from one tenant's systems, and a hallucination finding is the
    # evidence behind a score.
    "required_context", "retrieved_context", "sample_citation",
    "trajectory_step", "hallucination_finding", "stage_attribution",
]


def test_the_list_above_is_every_tenant_scoped_table_in_the_live_catalogue():
    """The list is parametrised, so a table missing from it is a table nobody
    tests. Derived from the catalogue rather than trusted."""
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    sql = "\n".join(p.read_text(encoding="utf-8")
                    for p in sorted((root / "docs/data/schema").glob("*.sql")))
    declared = set(re.findall(r"CREATE TABLE clep\.(\w+)\s*\(", sql))
    assert declared - {"organization"} == set(TENANT_SCOPED)


def test_the_four_adr_012_conditions_hold_for_the_connected_role(migrated_database):
    """Not for the schema in the abstract — for the role actually connected.

    A deployment can satisfy every static check and still grant the application
    a superuser, which disables all of this while leaving the policies visible
    and apparently correct.
    """
    from clep.db.provision import assert_isolation_preconditions
    with psycopg.connect(migrated_database) as conn:
        problems = assert_isolation_preconditions(conn)
    assert problems == [], f"isolation preconditions violated: {problems}"


@pytest.mark.parametrize("table", TENANT_SCOPED)
def test_every_tenant_scoped_table_has_both_enable_and_force(migrated_database, table):
    with psycopg.connect(migrated_database) as conn:
        row = conn.execute(
            "SELECT relrowsecurity, relforcerowsecurity FROM pg_class c "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = 'clep' AND c.relname = %s", (table,)).fetchone()
    assert row is not None, f"{table} does not exist"
    enabled, forced = row
    assert enabled, f"{table} has no row-level security"
    assert forced, (f"{table} has ENABLE but not FORCE; the owner would bypass "
                    f"every policy on it")


def test_a_second_tenant_cannot_read_the_first_tenants_run(
        migrated_database, seeded, second_organization):
    repo_ids = _create_run(migrated_database, seeded)
    with tenant_session(migrated_database, second_organization) as conn:
        intruder = RunRepository(conn, second_organization)
        assert intruder.get_run(repo_ids["run"]) is None
        rows = conn.execute("SELECT count(*) FROM clep.run").fetchone()[0]
    assert rows == 0, "row-level security let a foreign tenant see a run"


def test_a_second_tenant_cannot_write_into_the_first_tenants_run(
        migrated_database, seeded, second_organization):
    """`USING` alone would filter the read and still permit this write.
    `WITH CHECK` is what refuses it."""
    ids = _create_run(migrated_database, seeded)
    with pytest.raises(psycopg.errors.Error):
        with tenant_session(migrated_database, second_organization) as conn:
            conn.execute(
                "INSERT INTO clep.run_checkpoint (id, organization_id, run_id) "
                "VALUES (%s, %s, %s)",
                (ulid_to_uuid(new_ulid()), seeded["organization"],
                 ulid_to_uuid(ids["run"])))


def test_a_cross_tenant_link_is_rejected_by_the_store_not_only_a_cross_tenant_read(
        migrated_database, seeded, second_organization):
    """P-5. Per-table policies stop a cross-tenant read; a row that LINKS two
    tenants would be refused by no single-table policy, which is why the foreign
    keys carry organization_id."""
    ids = _create_run(migrated_database, seeded)
    with admin_session(MIGRATION_DSN) as conn:
        project = conn.execute(
            "INSERT INTO clep.project (id, organization_id, slug, display_name) "
            "VALUES (%s, %s, 'other', 'Other') RETURNING id",
            (uuid.uuid4(), second_organization)).fetchone()[0]
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            conn.execute(
                "INSERT INTO clep.run (id, organization_id, project_id, "
                "suite_version_id, dataset_version_id, identity_digest, "
                "integration_tier, execution_state, idempotency_key) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'output_only', 'queued', 'x')",
                (uuid.uuid4(), second_organization, project,
                 ulid_to_uuid(seeded["suite_version"]),
                 ulid_to_uuid(seeded["dataset_version"]), "sha256:" + "0" * 64))


def test_a_session_with_no_tenant_sees_nothing_rather_than_everything(
        migrated_database, seeded):
    _create_run(migrated_database, seeded)
    with psycopg.connect(migrated_database) as conn:
        visible = conn.execute("SELECT count(*) FROM clep.run").fetchone()[0]
    assert visible == 0, ("an unset tenant context matched rows; it must fail "
                          "closed, not open")


def test_opening_a_session_without_a_tenant_is_refused(migrated_database):
    with pytest.raises(TenantContextError):
        with tenant_session(migrated_database, None):
            pass


def test_the_tenant_context_does_not_survive_the_session(migrated_database, seeded):
    """Set with is_local, so it dies with the transaction. A context that
    outlived it would let the next user of a pooled connection read the previous
    tenant's rows, with no error anywhere."""
    from clep.db.session import current_tenant
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        assert current_tenant(conn) == seeded["organization"]
    with psycopg.connect(migrated_database) as conn:
        assert current_tenant(conn) is None


def test_the_runtime_role_cannot_delete_an_audit_event(migrated_database, seeded):
    """I-33. An actor must not be able to remove the record of their own action."""
    with admin_session(MIGRATION_DSN) as conn:
        conn.execute(
            "INSERT INTO clep.audit_event (id, organization_id, actor_id, "
            "action, target_type, target_id) "
            "VALUES (%s, %s, %s, 'run.created', 'run', %s)",
            (uuid.uuid4(), seeded["organization"], uuid.uuid4(), uuid.uuid4()))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            conn.execute("DELETE FROM clep.audit_event")


def _create_run(dsn, seeded) -> dict:
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        run_id = repo.create_run(
            project_id=seeded["project"], suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "0" * 64, integration_tier="output_only",
            idempotency_key="isolation-test")
    return {"run": run_id}
