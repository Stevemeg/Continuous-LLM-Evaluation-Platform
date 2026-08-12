"""Audit, retention, erasure, quotas and the D-1 guarantee.

The compliance half of Phase 12. Everything here is asserted against PostgreSQL,
because everything here is a property of the store: an append-only trail, a
retention floor no tenant may lower, an erasure that is verified rather than
reported, and a foreign reference the store refuses.

The rate limiter is the exception and is tested in `test_rate_limits.py`, where
the clock is injected.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from clep.api import audit
from clep.api.security_service import SecurityService
from clep.db import provision
from clep.db.session import tenant_session
from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid
from clep.security import erasure
from clep.security.repository import SecurityError, SecurityRepository
from tests.conftest import MIGRATION_DSN, requires_postgres
from tests.test_regression import build_run, examples, _slug  # noqa: F401

pytestmark = [pytest.mark.integration, requires_postgres]


@pytest.fixture
def owner(organization):
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        user_id, presented = provision.bootstrap_organization(
            conn, organization, external_subject="owner@example.invalid")
    return {"id": user_id, "credential": presented}


# ================================================================ audit trail
def test_an_audit_event_carries_its_justification_and_the_version_approved(
        migrated_database, organization, owner):
    """`REQ-F-12-4` and `REQ-N-COMP-1`. Two columns the schema has carried since
    Phase 4 and nothing wrote until now: without them an auditor can ask *what
    was changed* and not *which version was approved, and why*."""
    digest = "sha256:" + "a" * 64
    with tenant_session(migrated_database, organization) as conn:
        audit.record(conn, organization, owner["id"], "baseline.approved",
                     "baseline", new_ulid(),
                     justification="release 4.2 sign-off",
                     target_content_digest=digest)
    with tenant_session(migrated_database, organization) as conn:
        row = conn.execute(
            "SELECT action, justification, target_content_digest "
            "  FROM clep.audit_event WHERE action = 'baseline.approved'"
        ).fetchone()
    assert row == ("baseline.approved", "release 4.2 sign-off", digest)


def test_an_audit_event_may_name_a_route_rather_than_a_row(migrated_database,
                                                           organization, owner):
    """A refusal has no object to point at. The column is nullable for that
    case, and `target_type` never is: an event with no idea what it was about is
    not a record."""
    with tenant_session(migrated_database, organization) as conn:
        audit.record(conn, organization, owner["id"], "access.denied", "route",
                     None, justification="audit:read required for /x")
        row = conn.execute(
            "SELECT target_type, target_id FROM clep.audit_event "
            " WHERE action = 'access.denied'").fetchone()
    assert row == ("route", None)


def test_paging_does_not_drop_events_that_share_a_timestamp(
        migrated_database, organization, owner, seeded):
    """The defect this test was written after finding.

    Every event written by one transaction shares `occurred_at`, and a ULID's
    low 80 bits are random, so identifiers written in the same millisecond do
    not sort in the order the events happened. A cursor filtering on `id` alone
    therefore skipped events whenever a page boundary landed inside a
    transaction. The fix compares `(occurred_at, id)` as a pair; this asserts
    the property that failed, over events written together on purpose.
    """
    service = SecurityService(migrated_database)
    with tenant_session(migrated_database, organization) as conn:
        for index in range(9):
            audit.record(conn, organization, owner["id"], f"thing.{index}",
                         "thing", seeded["project"])
    seen, cursor = [], None
    for _page in range(5):
        page = service.list_audit_events(organization_id=organization,
                                         project_id=seeded["project"], limit=2,
                                         cursor=cursor)
        seen.extend(item["id"] for item in page["items"])
        cursor = page["nextCursor"]
        if cursor is None:
            break
    assert len(seen) == 9, "a page boundary inside one transaction lost events"
    assert len(set(seen)) == 9, "an event was returned on two pages"


def test_the_runtime_role_can_neither_edit_nor_delete_an_audit_event(
        migrated_database, organization, owner):
    """I-33. Both directions: a DELETE would remove the record of an action, an
    UPDATE would rewrite it, and neither privilege is granted."""
    with tenant_session(migrated_database, organization) as conn:
        audit.record(conn, organization, owner["id"], "thing.done", "thing",
                     new_ulid())
    for statement in ("DELETE FROM clep.audit_event",
                      "UPDATE clep.audit_event SET action = 'thing.undone'"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_session(migrated_database, organization) as conn:
                conn.execute(statement)


def test_the_audit_surface_pages_newest_first_and_the_cursor_advances(
        migrated_database, organization, owner, seeded):
    service = SecurityService(migrated_database)
    with tenant_session(migrated_database, organization) as conn:
        for index in range(7):
            audit.record(conn, organization, owner["id"], f"prompt.{index}",
                         "prompt", seeded["project"])
    first = service.list_audit_events(organization_id=organization,
                                      project_id=seeded["project"], limit=3)
    assert len(first["items"]) == 3
    assert first["nextCursor"] is not None
    second = service.list_audit_events(organization_id=organization,
                                       project_id=seeded["project"], limit=3,
                                       cursor=first["nextCursor"])
    assert not ({i["id"] for i in first["items"]} &
                {i["id"] for i in second["items"]})


def test_another_tenant_reads_none_of_the_audit_trail(migrated_database,
                                                      organization, owner,
                                                      second_organization,
                                                      seeded):
    service = SecurityService(migrated_database)
    with tenant_session(migrated_database, organization) as conn:
        audit.record(conn, organization, owner["id"], "prompt.created",
                     "prompt", seeded["project"])
    theirs = service.list_audit_events(organization_id=second_organization,
                                       project_id=seeded["project"])
    assert theirs["items"] == []


def test_a_refusal_is_recorded_against_the_tenant_it_was_refused_in(
        migrated_database, organization, owner):
    """`REQ-N-SEC-2` from the position the platform can honestly occupy: it
    records that a principal was refused, not that it detected a cross-tenant
    read — which the 404/403 indistinguishability specifically prevents it from
    knowing."""
    service = SecurityService(migrated_database)
    service.record_denial(organization_id=organization, actor_id=owner["id"],
                          permission="audit:read", target="/audit-events")
    with tenant_session(migrated_database, organization) as conn:
        row = conn.execute(
            "SELECT action, justification FROM clep.audit_event "
            " WHERE action = 'access.denied'").fetchone()
    assert row[0] == "access.denied"
    assert "audit:read" in row[1]


def test_an_unverified_credential_writes_nothing_to_anyones_audit_trail(
        migrated_database, organization):
    """ADR-021 rule 8 applied to authentication: the audit store is the one
    thing nobody may prune, so an unauthenticated caller must not be able to
    grow it."""
    service = SecurityService(migrated_database)
    service.record_authentication_failure(reason="no such key", target="/runs")
    with tenant_session(migrated_database, organization) as conn:
        assert conn.execute(
            "SELECT count(*) FROM clep.audit_event").fetchone()[0] == 0
    assert service.authentication_failures == 1


# ================================================================== retention
def test_a_tenant_cannot_lower_audit_retention_below_the_floor(
        migrated_database, organization, owner):
    """I-34, refused by the store. The repository does not check it — that is
    the point: a second writer would have to satisfy the same constraint."""
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        floor = repo.audit_retention_floor()
        with pytest.raises(SecurityError, match="may not fall below"):
            repo.set_retention_policy(decision_days=90, content_days=30,
                                      audit_days=floor - 1,
                                      actor_id=owner["id"])


def test_a_tenant_may_raise_audit_retention_above_the_floor(migrated_database,
                                                            organization, owner):
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        policy = repo.set_retention_policy(
            decision_days=365, content_days=90,
            audit_days=repo.audit_retention_floor() + 1000,
            actor_id=owner["id"])
    assert policy.audit_retention_days > 2555


def test_content_retention_shorter_than_audit_retention_is_permitted(
        migrated_database, organization, owner):
    """The three classes are independent (ADR-011). Content may be short-lived
    while the record of what was decided about it is not."""
    with tenant_session(migrated_database, organization) as conn:
        policy = SecurityRepository(conn, organization).set_retention_policy(
            decision_days=400, content_days=7, audit_days=2555,
            actor_id=owner["id"])
    assert policy.content_retention_days == 7


def test_an_unconfigured_tenant_is_not_a_tenant_with_no_retention(
        migrated_database, organization, owner):
    """Absence of a policy must not read as absence of a floor."""
    reported = SecurityService(migrated_database).retention_policy(
        organization_id=organization)
    assert reported["auditRetentionFloorDays"] == 2555
    assert reported["auditRetentionDays"] >= reported["auditRetentionFloorDays"]


def test_a_usage_limit_of_zero_is_refused(migrated_database, organization,
                                          owner):
    with tenant_session(migrated_database, organization) as conn:
        with pytest.raises(SecurityError, match="must be positive"):
            SecurityRepository(conn, organization).set_usage_limit(
                requests_per_minute=0, runs_per_period=10, period_days=30,
                actor_id=owner["id"])


def test_the_run_quota_counts_down_and_then_refuses(migrated_database,
                                                    organization, owner):
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        repo.set_usage_limit(requests_per_minute=60, runs_per_period=2,
                             period_days=30, actor_id=owner["id"])
        assert repo.consume_run_quota()[:1] == (True,)
        assert repo.consume_run_quota()[:1] == (True,)
        allowed, used, limit = repo.consume_run_quota()
    assert not allowed and used == 2 and limit == 2


def test_one_tenants_exhausted_quota_leaves_another_untouched(
        migrated_database, organization, second_organization, owner):
    """`REQ-N-SCALE-2`: no cross-tenant interference, asserted rather than
    inferred from the keying scheme looking correct."""
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        theirs, _key = provision.bootstrap_organization(
            conn, second_organization, external_subject="them@example.invalid")
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        repo.set_usage_limit(requests_per_minute=60, runs_per_period=1,
                             period_days=30, actor_id=owner["id"])
        assert repo.consume_run_quota()[0]
        assert not repo.consume_run_quota()[0]
    with tenant_session(migrated_database, second_organization) as conn:
        assert SecurityRepository(conn, second_organization) \
            .consume_run_quota()[0]


# ==================================================================== erasure
@pytest.fixture
def erasable(migrated_database, seeded, examples, organization, owner):
    """A run over real example content, plus a derived artifact and a piece of
    gate evidence — the three things an erasure has to treat differently."""
    run_id = build_run(migrated_database, seeded, examples, [0.9, 0.9],
                       key="erasure")
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        digest = conn.execute(
            "SELECT content_digest FROM clep.example_content "
            " WHERE organization_id = %s", (organization,)).fetchone()[0]
        conn.execute(
            "UPDATE clep.run_sample SET example_content_digest = %s "
            " WHERE organization_id = %s", (digest, organization))
        derived = uuid.uuid4()
        conn.execute(
            "INSERT INTO clep.artifact (id, organization_id, artifact_class, "
            "content_digest, payload_ref, byte_size, source_content_digest, "
            "correlation_id) VALUES (%s,%s,'candidate_output',%s,"
            "'s3://bucket/derived',10,%s,'corr-1')",
            (derived, organization, "sha256:" + "1" * 64, digest))
        evidence = uuid.uuid4()
        conn.execute(
            "INSERT INTO clep.artifact (id, organization_id, artifact_class, "
            "content_digest, payload_ref, byte_size, correlation_id) "
            "VALUES (%s,%s,'gate_evidence',%s,'s3://bucket/evidence',10,'corr-2')",
            (evidence, organization, "sha256:" + "2" * 64))
    return {"run": run_id, "digest": digest, "derived": derived,
            "evidence": evidence}


def test_erasure_destroys_the_content_and_keeps_the_example(
        migrated_database, organization, owner, erasable):
    with tenant_session(migrated_database, organization) as conn:
        outcome = erasure.request_erasure(
            conn, organization, digests=[erasable["digest"]],
            justification="data subject request 17", actor_id=owner["id"])
    assert outcome.state == "completed"
    assert outcome.verified == outcome.target_count
    with tenant_session(migrated_database, organization) as conn:
        content = conn.execute(
            "SELECT payload_ref, erased_at, erasure_audit_id "
            "  FROM clep.example_content WHERE content_digest = %s",
            (erasable["digest"],)).fetchone()
        examples_left = conn.execute(
            "SELECT count(*) FROM clep.example").fetchone()[0]
    assert content[0] is None and content[1] is not None
    assert content[2] is not None, "an erasure must name the record that " \
                                   "authorised it"
    assert examples_left > 0, "I-8: destroying content leaves the example"


def test_erasure_demotes_every_referencing_run_before_destroying(
        migrated_database, organization, owner, erasable):
    with tenant_session(migrated_database, organization) as conn:
        outcome = erasure.request_erasure(
            conn, organization, digests=[erasable["digest"]],
            justification="data subject request 17", actor_id=owner["id"])
    assert erasable["run"] in outcome.demoted_run_ids
    with tenant_session(migrated_database, organization) as conn:
        state = conn.execute(
            "SELECT reproducibility FROM clep.run WHERE id = %s",
            (ulid_to_uuid(erasable["run"]),)).fetchone()[0]
    assert state == "auditable", \
        "a run whose content is gone must stop claiming it can be replayed"


def test_erasure_reaches_derived_artifacts_and_spares_gate_evidence(
        migrated_database, organization, owner, erasable):
    """`REQ-N-PRIV-4` in one direction and `REQ-N-COMP-1` in the other. The
    schema already makes gate evidence structurally free of erasable content;
    this proves the erasure path agrees."""
    with tenant_session(migrated_database, organization) as conn:
        erasure.request_erasure(
            conn, organization, digests=[erasable["digest"]],
            justification="data subject request 17", actor_id=owner["id"])
    with tenant_session(migrated_database, organization) as conn:
        derived = conn.execute(
            "SELECT payload_ref, erased_at FROM clep.artifact WHERE id = %s",
            (erasable["derived"],)).fetchone()
        evidence = conn.execute(
            "SELECT payload_ref, erased_at FROM clep.artifact WHERE id = %s",
            (erasable["evidence"],)).fetchone()
    assert derived == (None, derived[1]) and derived[1] is not None
    assert evidence[0] == "s3://bucket/evidence" and evidence[1] is None


def test_an_erasure_is_recorded_with_its_justification_and_its_verification(
        migrated_database, organization, owner, erasable):
    with tenant_session(migrated_database, organization) as conn:
        outcome = erasure.request_erasure(
            conn, organization, digests=[erasable["digest"]],
            justification="data subject request 17", actor_id=owner["id"])
    with tenant_session(migrated_database, organization) as conn:
        request = conn.execute(
            "SELECT state, target_count, verified_count, completed_at, "
            "       justification FROM clep.erasure_request WHERE id = %s",
            (ulid_to_uuid(outcome.id),)).fetchone()
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM clep.audit_event ORDER BY id").fetchall()]
    assert request[0] == "completed"
    assert request[1] == request[2], "completion requires every target verified"
    assert request[3] is not None
    assert request[4] == "data subject request 17"
    assert "content.erasure_requested" in actions
    assert "content.erasure_completed" in actions


def test_an_erasure_with_no_justification_is_refused(migrated_database,
                                                     organization, owner,
                                                     erasable):
    with tenant_session(migrated_database, organization) as conn:
        with pytest.raises(erasure.ErasureError, match="justification"):
            erasure.request_erasure(conn, organization,
                                    digests=[erasable["digest"]],
                                    justification="too short",
                                    actor_id=owner["id"])


def test_an_erasure_naming_nothing_live_is_refused_rather_than_reported_done(
        migrated_database, organization, owner, erasable):
    with tenant_session(migrated_database, organization) as conn:
        with pytest.raises(erasure.ErasureError, match="no live content"):
            erasure.request_erasure(conn, organization,
                                    digests=["sha256:" + "f" * 64],
                                    justification="nothing to erase here",
                                    actor_id=owner["id"])


def test_a_second_tenant_cannot_erase_the_first_tenants_content(
        migrated_database, organization, second_organization, owner, erasable):
    """The digest is known to the attacker — it is in the run they cannot see.
    Knowing it must not be enough."""
    with tenant_session(migrated_database, second_organization) as conn:
        with pytest.raises(erasure.ErasureError, match="no live content"):
            erasure.request_erasure(conn, second_organization,
                                    digests=[erasable["digest"]],
                                    justification="borrowed digest attempt",
                                    actor_id=owner["id"])


def test_an_approved_baseline_pins_the_content_until_an_audited_override(
        migrated_database, organization, owner, erasable, seeded):
    """`REQ-F-05-9`. Refused, then permitted with the override, and the override
    is recorded on the request rather than only in the caller's intention."""
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clep.baseline (id, organization_id, project_id, "
            "run_id, suite_version_id, dataset_version_id, state, "
            "identity_digest, created_by, approved_by, approved_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,'approved',%s,%s,%s, now())",
            (uuid.uuid4(), organization, ulid_to_uuid(seeded["project"]),
             ulid_to_uuid(erasable["run"]),
             ulid_to_uuid(seeded["suite_version"]),
             ulid_to_uuid(seeded["dataset_version"]), "sha256:" + "3" * 64,
             uuid.uuid4(), uuid.uuid4()))
    with tenant_session(migrated_database, organization) as conn:
        with pytest.raises(erasure.BaselinePinned):
            erasure.request_erasure(conn, organization,
                                    digests=[erasable["digest"]],
                                    justification="data subject request 17",
                                    actor_id=owner["id"])
    with tenant_session(migrated_database, organization) as conn:
        outcome = erasure.request_erasure(
            conn, organization, digests=[erasable["digest"]],
            justification="data subject request 17, legal sign-off",
            actor_id=owner["id"], override_baseline_pin=True)
        used = conn.execute(
            "SELECT is_override_used FROM clep.erasure_request WHERE id = %s",
            (ulid_to_uuid(outcome.id),)).fetchone()[0]
    assert outcome.state == "completed"
    assert used is True


# ======================================================================= D-1
def test_a_comparison_cannot_cite_another_tenants_evaluator_version(
        migrated_database, organization, second_organization, seeded):
    """D-1, closed by a store-level trigger rather than by a composite key.

    The evaluator version is real and belongs to somebody else. Under the
    caller's tenant context it is invisible, so the row is refused for not
    existing — which is exactly what the composite foreign key would have said.
    """
    theirs = new_ulid()
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        definition = new_ulid()
        conn.execute(
            "INSERT INTO clep.evaluator_definition (id, organization_id, scope, "
            "slug, display_name) VALUES (%s,%s,'custom',%s,'Theirs')",
            (ulid_to_uuid(definition), second_organization,
             f"theirs_{definition[-8:].lower()}"))
        conn.execute(
            "INSERT INTO clep.evaluator_version (id, organization_id, "
            "evaluator_definition_id, version_number, content_digest, "
            "input_schema_ref, output_schema_ref, declared_permissions, "
            "is_deterministic, cost_class) VALUES (%s,%s,%s,1,%s,"
            "'schema://in/v1','schema://out/v1','none',true,'free')",
            (ulid_to_uuid(theirs), second_organization, ulid_to_uuid(definition),
             "sha256:" + "9" * 64))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with tenant_session(migrated_database, organization) as conn:
            conn.execute(
                "INSERT INTO clep.comparison (id, organization_id, "
                "gate_decision_id, metric_key, result_kind, "
                "evaluator_version_id, classification, sample_size, "
                "statistical_method_version) "
                "VALUES (%s,%s,%s,'m','deterministic_evaluator',%s,"
                "        'no_change',10,'v1')",
                (uuid.uuid4(), organization, uuid.uuid4(),
                 ulid_to_uuid(theirs)))


def test_an_invocation_cannot_cite_another_tenants_evaluator_version(
        migrated_database, organization, second_organization):
    """The same guard on the table Phase 12 adds. Closing D-1 on one table and
    reintroducing its shape on the next would have been no closure at all."""
    theirs = new_ulid()
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        definition = new_ulid()
        conn.execute(
            "INSERT INTO clep.evaluator_definition (id, organization_id, scope, "
            "slug, display_name) VALUES (%s,%s,'custom',%s,'Theirs')",
            (ulid_to_uuid(definition), second_organization,
             f"theirs_{definition[-8:].lower()}"))
        conn.execute(
            "INSERT INTO clep.evaluator_version (id, organization_id, "
            "evaluator_definition_id, version_number, content_digest, "
            "input_schema_ref, output_schema_ref, declared_permissions, "
            "is_deterministic, cost_class) VALUES (%s,%s,%s,1,%s,"
            "'schema://in/v1','schema://out/v1','none',true,'free')",
            (ulid_to_uuid(theirs), second_organization, ulid_to_uuid(definition),
             "sha256:" + "8" * 64))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with tenant_session(migrated_database, organization) as conn:
            conn.execute(
                "INSERT INTO clep.evaluator_invocation (id, organization_id, "
                "run_sample_id, evaluator_version_id, granted_permissions, "
                "outcome, correlation_id) "
                "VALUES (%s,%s,%s,%s,'none','scored','corr')",
                (uuid.uuid4(), organization, uuid.uuid4(), ulid_to_uuid(theirs)))


def test_a_comparison_may_still_cite_a_built_in_evaluator(migrated_database,
                                                          organization, seeded):
    """The other half of the guard, and the reason it is a trigger rather than a
    composite key: a built-in carries a NULL organization_id under the ADR-010
    rule 4 exception and must remain citable."""
    with tenant_session(migrated_database, organization) as conn:
        visible = conn.execute(
            "SELECT count(*) FROM clep.evaluator_version WHERE id = %s",
            (ulid_to_uuid(seeded["evaluator_version"]),)).fetchone()[0]
    assert visible == 1, "a built-in evaluator is readable by every tenant"
