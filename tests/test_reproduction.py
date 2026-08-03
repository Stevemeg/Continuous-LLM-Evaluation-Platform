"""Reproduction from a captured identity — REQ-F-07-3.

The requirement's weight is in its second half: "and shall report any element
that could not be reconstructed". Re-running is the easy part. What these tests
attack is the failure mode where a reproduction quietly substitutes whatever it
can still find — the current version of a prompt, today's dataset — and reports
success, producing a number that looks like a reproduction and is not one.
"""
from __future__ import annotations

import uuid

import psycopg
import pytest

from clep.db.session import admin_session, tenant_session
from clep.experiments import reproduction
from clep.experiments.capture import build_run_identity
from clep.experiments.identity import IdentityBuilder, digest_of
from clep.experiments.repository import IdentityRepository
from clep.identity import new_ulid, ulid_to_uuid
from clep.orchestration.repository import RunRepository
from tests.conftest import MIGRATION_DSN, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]


def make_run(dsn, seeded, key="repro-1", with_prompt=True):
    candidates = [{"modelConfigurationId": seeded["model_configuration"]}]
    if with_prompt:
        candidates[0]["promptVersionId"] = seeded["prompt_version"]
    with tenant_session(dsn, seeded["organization"]) as conn:
        identity = build_run_identity(
            conn, seeded["organization"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            integration_tier="output_only", candidates=candidates)
        runs = RunRepository(conn, seeded["organization"])
        run_id = runs.create_run(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest=identity.digest(), integration_tier="output_only",
            idempotency_key=key)
        IdentityRepository(conn, seeded["organization"]).capture(run_id, identity)
    return run_id


def test_an_untouched_run_is_reproducible(migrated_database, seeded):
    run_id = make_run(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reproduction.reproduce(conn, seeded["organization"], run_id)
    assert result["outcome"] == "reproducible"
    assert result["gaps"] == []


def test_the_captured_identity_survives_a_round_trip(migrated_database, seeded):
    run_id = make_run(migrated_database, seeded, key="round-trip")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        stored = IdentityRepository(conn, seeded["organization"]).components_of(run_id)
        digest = conn.execute(
            "SELECT identity_digest FROM clep.run WHERE id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0]
    assert stored.digest() == digest, \
        "the digest on the run must be derivable from the components stored beside it"
    assert {"dataset_version", "suite_version", "model_configuration",
            "prompt_version", "evaluator_version", "integration_tier",
            "environment"} <= stored.kinds()


def test_a_component_whose_content_moved_is_reported_not_substituted(
        migrated_database, seeded):
    """The whole point. A draft prompt version can still be edited, and a
    reproduction that re-ran against the edited text without saying so would
    report a comparison that never happened."""
    run_id = make_run(migrated_database, seeded, key="digest-moved")
    with admin_session(MIGRATION_DSN) as conn:
        conn.execute("UPDATE clep.prompt_version SET content_digest = %s "
                     "WHERE id = %s",
                     (digest_of("edited after the run"),
                      ulid_to_uuid(seeded["prompt_version"])))
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reproduction.reproduce(conn, seeded["organization"], run_id)
    assert result["outcome"] == "partially_reproducible"
    assert {"componentKind": "prompt_version",
            "componentRef": seeded["prompt_version"],
            "reason": "digest_mismatch"} in result["gaps"]


def test_an_absent_component_is_named(migrated_database, seeded):
    run_id = make_run(migrated_database, seeded, key="absent")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        # A component the identity names that the store no longer holds. Written
        # directly because the registry deliberately offers no way to remove one.
        conn.execute(
            "INSERT INTO clep.run_identity_component (id, organization_id, "
            "run_id, component_kind, component_ref, component_digest) "
            "VALUES (%s, %s, %s, 'system_version', %s, %s)",
            (uuid.uuid4(), seeded["organization"], ulid_to_uuid(run_id),
             new_ulid(), digest_of("gone")))
        result = reproduction.reproduce(conn, seeded["organization"], run_id)
    assert result["outcome"] == "partially_reproducible"
    assert any(g["reason"] == "component_absent" for g in result["gaps"])


def test_a_changed_environment_is_reported_without_invalidating_the_run(
        migrated_database, seeded):
    """ADR-014: a different host is still the same measurement. It is recorded so
    a reviewer can weigh it, not used to reject the reproduction."""
    run_id = make_run(migrated_database, seeded, key="env")
    elsewhere = IdentityBuilder()
    elsewhere.add("environment", '{"python":"3.4.0"}', digest_of("elsewhere"))
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reproduction.reproduce(conn, seeded["organization"], run_id,
                                        current_environment=elsewhere.build())
    assert result["outcome"] == "partially_reproducible"
    reasons = {g["reason"] for g in result["gaps"]}
    assert reasons == {"environment_changed"}


def test_erased_example_content_is_a_gap_not_a_smaller_dataset(
        migrated_database, seeded):
    """I-8: the example record survives erasure and its content does not. A
    reproduction that did not look would re-run against fewer examples and report
    a clean result."""
    run_id = make_run(migrated_database, seeded, key="erased")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        clean = reproduction.reproduce(conn, seeded["organization"], run_id)
    assert clean["outcome"] == "reproducible"

    # Erasure as the schema represents it: the record survives, the payload does
    # not, and the audit record that authorised it is named.
    with admin_session(MIGRATION_DSN) as conn:
        audit_id = uuid.uuid4()
        conn.execute(
            "INSERT INTO clep.audit_event (id, organization_id, actor_id, action,"
            " target_type, target_id) VALUES (%s,%s,%s,'example.erased','example',%s)",
            (audit_id, seeded["organization"], uuid.uuid4(),
             ulid_to_uuid(seeded["example"])))
        conn.execute(
            "UPDATE clep.example_content SET payload_ref = NULL, erased_at = now(),"
            " erasure_audit_id = %s WHERE example_id = %s",
            (audit_id, ulid_to_uuid(seeded["example"])))

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        gaps = reproduction.erased_content_gaps(conn, seeded["organization"],
                                                seeded["dataset_version"])
        after = reproduction.reproduce(conn, seeded["organization"], run_id)
    assert gaps and gaps[0]["reason"] == "content_erased"
    assert after["outcome"] == "partially_reproducible", \
        "erased content must not be reported as a smaller but clean dataset"


def test_an_attempt_is_recorded_with_its_gaps(migrated_database, seeded):
    run_id = make_run(migrated_database, seeded, key="recorded")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reproduction.reproduce(conn, seeded["organization"], run_id)
        stored = IdentityRepository(conn, seeded["organization"]).attempt(result["id"])
    assert stored["originalRunId"] == run_id
    assert stored["outcome"] == result["outcome"]
    assert len(stored["gaps"]) == len(result["gaps"])


def test_a_not_reproducible_attempt_cannot_carry_a_replay_run(
        migrated_database, seeded):
    """ck_reproduction_attempt__replay_matches_outcome. Recording a replay for a
    run that could not be replayed at all would be a claim the attempt does not
    support."""
    run_id = make_run(migrated_database, seeded, key="pairing")
    with pytest.raises(psycopg.errors.CheckViolation):
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            conn.execute(
                "INSERT INTO clep.reproduction_attempt (id, organization_id, "
                "original_run_id, replay_run_id, outcome) "
                "VALUES (%s, %s, %s, %s, 'not_reproducible')",
                (uuid.uuid4(), seeded["organization"], ulid_to_uuid(run_id),
                 ulid_to_uuid(run_id)))


def test_an_assessment_without_a_replay_is_permitted(migrated_database, seeded):
    """The direction the first constraint got wrong. Deciding that a run *can*
    be reproduced is not the same act as re-running it, and requiring a replay
    run to record the conclusion would make the honest assessment impossible."""
    run_id = make_run(migrated_database, seeded, key="assessment-only")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reproduction.reproduce(conn, seeded["organization"], run_id)
        stored = IdentityRepository(conn, seeded["organization"]).attempt(result["id"])
    assert stored["outcome"] == "reproducible"
    assert stored["replayRunId"] is None


def test_a_reproduction_is_read_only_until_it_is_recorded(migrated_database, seeded):
    """Assessing reproducibility must not perturb what it measures."""
    run_id = make_run(migrated_database, seeded, key="readonly")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        before = IdentityRepository(conn, seeded["organization"]).components_of(run_id)
        reproduction.assess(conn, seeded["organization"], before)
        after = IdentityRepository(conn, seeded["organization"]).components_of(run_id)
    assert before == after
