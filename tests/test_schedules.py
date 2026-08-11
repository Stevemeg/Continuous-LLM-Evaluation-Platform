"""Cadence, eligibility, and the two things a sweep must refuse.

The cadence half runs without a database: an expression either fires in a minute
or it does not, and that is arithmetic. The eligibility half needs a real
PostgreSQL, because the guarantee being tested is that a duplicate trigger is
refused by a unique constraint rather than by the sweep remembering.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import psycopg
import pytest

from clep.identity import new_ulid, ulid_to_uuid
from clep.orchestration import scheduler
from clep.orchestration.examples import (ExampleUnavailable, StoredExampleSource,
                                         digest_of, file_payload_reader)
from clep.orchestration.releases import (ReleaseObservationError,
                                         recommendation_for)
from clep.orchestration.schedules import (CadenceError, ScheduleError,
                                          ScheduleRepository, parse_cadence,
                                          trigger_key)
from tests.conftest import MIGRATION_DSN, requires_postgres

UTC = timezone.utc


def at(**kw) -> datetime:
    base = {"year": 2026, "month": 8, "day": 11, "hour": 10, "minute": 0}
    base.update(kw)
    return datetime(tzinfo=UTC, **base)


# ============================================================ cadence, no database
def test_every_minute_fires_in_every_minute():
    cadence = parse_cadence("* * * * *")
    assert cadence.matches(at(minute=0))
    assert cadence.matches(at(minute=37))


def test_a_step_fires_only_on_its_multiples():
    cadence = parse_cadence("*/15 * * * *")
    assert [m for m in range(60) if cadence.matches(at(minute=m))] == [0, 15, 30, 45]


def test_a_list_and_a_range_are_both_read():
    assert sorted(parse_cadence("1,2,5-7 * * * *").minutes) == [1, 2, 5, 6, 7]


def test_an_hour_constrains_as_well_as_a_minute():
    cadence = parse_cadence("30 3 * * *")
    assert cadence.matches(at(hour=3, minute=30))
    assert not cadence.matches(at(hour=4, minute=30))
    assert not cadence.matches(at(hour=3, minute=31))


def test_the_two_day_fields_are_a_union_when_both_are_restricted():
    """Vixie cron's rule, stated rather than inherited: "the 1st and every
    Monday" means either, and a reader of a release schedule is entitled to
    know which reading they got."""
    cadence = parse_cadence("0 0 1 * 1")
    assert cadence.matches(at(day=1, hour=0, minute=0))         # the 1st
    assert cadence.matches(at(day=10, hour=0, minute=0))        # a Monday
    assert not cadence.matches(at(day=11, hour=0, minute=0))    # neither


@pytest.mark.parametrize("expression", [
    "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *", "* * 0 * *",
    "* * * 13 *", "* * * * 7", "a * * * *", "*/0 * * * *", "5-1 * * * *",
    "", "@hourly",
])
def test_an_unreadable_cadence_is_refused_rather_than_defaulted(expression):
    """A standing order that silently never fires is indistinguishable from one
    that works, until someone checks."""
    with pytest.raises(CadenceError):
        parse_cadence(expression)


def test_a_naive_timestamp_is_refused():
    with pytest.raises(CadenceError):
        parse_cadence("* * * * *").matches(datetime(2026, 8, 11, 10, 0))


def test_the_trigger_key_is_the_schedule_and_the_minute():
    a = trigger_key("S1", at(minute=5))
    assert a == trigger_key("S1", at(minute=5, second=30))
    assert a != trigger_key("S1", at(minute=6))
    assert a != trigger_key("S2", at(minute=5))


def test_the_trigger_key_is_the_same_minute_in_any_zone():
    """A key that changed with the reporter's zone would let the same firing
    create two runs."""
    elsewhere = at(minute=5).astimezone(timezone(timedelta(hours=9)))
    assert trigger_key("S1", elsewhere) == trigger_key("S1", at(minute=5))


# ================================================== the recommendation mapping
def test_every_gate_outcome_maps_to_a_recommendation():
    from clep.cli.exit_codes import BY_OUTCOME as CODES
    from clep.orchestration.releases import BY_OUTCOME as ADVICE
    assert set(CODES) == set(ADVICE), (
        "a gate outcome that a pipeline can act on but an operator cannot")


def test_an_abstention_recommends_investigating_rather_than_nothing():
    assert recommendation_for("insufficient_evidence") == "investigate"
    assert recommendation_for("not_comparable") == "investigate"
    assert recommendation_for("hard_fail") == "rollback"
    assert recommendation_for("pass") == "none"


def test_an_unmapped_outcome_is_refused_rather_than_read_as_nothing_to_do():
    with pytest.raises(ReleaseObservationError):
        recommendation_for("an_outcome_added_later")


# ================================================================== the payload
def test_a_file_reference_is_read_and_anything_else_is_refused(tmp_path):
    path = tmp_path / "e.json"
    path.write_text(json.dumps({"prompt": "q"}), encoding="utf-8")
    assert file_payload_reader(path.as_uri()) == path.read_bytes()
    with pytest.raises(ExampleUnavailable):
        file_payload_reader("s3://bucket/key")
    with pytest.raises(ExampleUnavailable):
        file_payload_reader((tmp_path / "absent.json").as_uri())


def test_a_source_with_no_reader_refuses_rather_than_evaluating_nothing():
    with pytest.raises(ExampleUnavailable):
        StoredExampleSource().load(None, "org", "ds")


# ======================================================= eligibility, with a store


def write_examples(tmp_path, seeded, prompts) -> None:
    """Real files, with real digests, referenced by real rows.

    Not a stub: the object store's own adapter (ADR-013) belongs to the
    deployment phase, and until it exists a `file://` reference is what a local
    deployment has. The digest check in `StoredExampleSource` runs either way.
    """
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        # The shared fixture seeds one example whose payload lives at an
        # `s3://` reference the object store would serve. Nothing serves it
        # here, and a scheduled run refuses a dataset it cannot read whole —
        # correctly — so this test's own dataset version is made readable
        # rather than the refusal being weakened to tolerate a gap.
        seed_payload = json.dumps({"prompt": "seeded",
                                   "expected": "Paris"}).encode("utf-8")
        seed_path = tmp_path / "seeded.json"
        seed_path.write_bytes(seed_payload)
        conn.execute(
            "UPDATE clep.example_content SET payload_ref = %s,"
            " content_digest = %s, byte_size = %s"
            " WHERE organization_id = %s AND example_id IN ("
            "   SELECT id FROM clep.example WHERE organization_id = %s"
            "   AND dataset_version_id = %s)",
            (seed_path.as_uri(), digest_of(seed_payload), len(seed_payload),
             seeded["organization"], seeded["organization"],
             ulid_to_uuid(seeded["dataset_version"])))
        for ordinal, prompt in enumerate(prompts, start=100):
            example_id = new_ulid()
            payload = json.dumps({"prompt": prompt,
                                  "expected": "Paris"}).encode("utf-8")
            path = tmp_path / f"{example_id}.json"
            path.write_bytes(payload)
            conn.execute(
                "INSERT INTO clep.example (id, organization_id,"
                " dataset_version_id, ordinal, split) VALUES (%s,%s,%s,%s,'test')",
                (ulid_to_uuid(example_id), seeded["organization"],
                 ulid_to_uuid(seeded["dataset_version"]), ordinal))
            conn.execute(
                "INSERT INTO clep.example_content (id, organization_id,"
                " example_id, content_digest, payload_ref, byte_size)"
                " VALUES (%s,%s,%s,%s,%s,%s)",
                (ulid_to_uuid(new_ulid()), seeded["organization"],
                 ulid_to_uuid(example_id), digest_of(payload), path.as_uri(),
                 len(payload)))


def make_schedule(conn, seeded, *, cadence="* * * * *", budget="10",
                  trigger_kind="schedule", **kw) -> str:
    return ScheduleRepository(conn, seeded["organization"]).create_schedule(
        project_id=seeded["project"],
        suite_version_id=seeded["suite_version"], cadence=cadence,
        budget_limit=Decimal(budget), budget_currency="USD",
        created_by="tester", trigger_kind=trigger_kind,
        candidates=[{"label": "a",
                     "modelConfigurationId": seeded["model_configuration"]}],
        **kw)


@pytest.mark.integration
@requires_postgres
def test_a_schedule_stores_its_candidates_and_reads_them_back(
        migrated_database, seeded):
    from clep.db.session import tenant_session
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        schedule_id = make_schedule(conn, seeded)
        schedule = ScheduleRepository(conn, seeded["organization"]).get_schedule(
            schedule_id)
    assert schedule.state == "active"
    assert schedule.trigger_kind == "schedule"
    assert [c.label for c in schedule.candidates] == ["a"]
    assert schedule.candidates[0].model_configuration_id == \
        seeded["model_configuration"]


@pytest.mark.integration
@requires_postgres
def test_a_schedule_with_an_unreadable_cadence_is_never_written(
        migrated_database, seeded):
    from clep.db.session import tenant_session
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        with pytest.raises(CadenceError):
            make_schedule(conn, seeded, cadence="every 5 minutes")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        assert conn.execute(
            "SELECT count(*) FROM clep.evaluation_schedule WHERE cadence = %s",
            ("every 5 minutes",)).fetchone()[0] == 0


@pytest.mark.integration
@requires_postgres
def test_a_schedule_without_candidates_or_budget_is_refused(
        migrated_database, seeded):
    from clep.db.session import tenant_session
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = ScheduleRepository(conn, seeded["organization"])
        with pytest.raises(ScheduleError):
            repo.create_schedule(
                project_id=seeded["project"],
                suite_version_id=seeded["suite_version"], cadence="* * * * *",
                budget_limit=Decimal("1"), budget_currency="USD",
                created_by="t", candidates=[])
        with pytest.raises(ScheduleError):
            repo.create_schedule(
                project_id=seeded["project"],
                suite_version_id=seeded["suite_version"], cadence="* * * * *",
                budget_limit=Decimal("0"), budget_currency="USD",
                created_by="t",
                candidates=[{"modelConfigurationId":
                             seeded["model_configuration"]}])


@pytest.mark.integration
@requires_postgres
def test_only_a_schedule_due_this_minute_is_swept(migrated_database, seeded,
                                                  tmp_path):
    from clep.db.session import tenant_session
    write_examples(tmp_path, seeded, ["q1", "q2"])
    source = StoredExampleSource(payload_reader=file_payload_reader)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        due = make_schedule(conn, seeded, cadence="30 3 * * *")
        make_schedule(conn, seeded, cadence="0 4 * * *")

        matched = ScheduleRepository(conn, seeded["organization"]).due_schedules(
            at(hour=3, minute=30))
        assert [s.id for s in matched] == [due]

        outcome = scheduler.sweep_tenant(
            conn, seeded["organization"], moment=at(hour=3, minute=30),
            example_source=source)
    assert outcome.considered == 1
    assert len(outcome.fired) == 1
    assert outcome.fired[0].schedule_id == due


@pytest.mark.integration
@requires_postgres
def test_a_paused_schedule_is_not_swept(migrated_database, seeded, tmp_path):
    from clep.db.session import tenant_session
    write_examples(tmp_path, seeded, ["q1"])
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        schedule_id = make_schedule(conn, seeded)
        assert ScheduleRepository(conn, seeded["organization"]).pause_schedule(
            schedule_id)
        outcome = scheduler.sweep_tenant(
            conn, seeded["organization"], moment=at(),
            example_source=StoredExampleSource(payload_reader=file_payload_reader))
    assert outcome.considered == 0
    # Paused, not deleted: what was scheduled survives the decision to stop.
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        assert ScheduleRepository(conn, seeded["organization"]).get_schedule(
            schedule_id).state == "paused"


@pytest.mark.integration
@requires_postgres
def test_a_second_sweep_in_the_same_minute_creates_no_second_run(
        migrated_database, seeded, tmp_path):
    """The property that makes a duplicate trigger harmless. The key is derived
    from the schedule and the minute, so both sweeps compute the same one."""
    from clep.db.session import tenant_session
    write_examples(tmp_path, seeded, ["q1", "q2"])
    source = StoredExampleSource(payload_reader=file_payload_reader)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        schedule_id = make_schedule(conn, seeded)
        first = scheduler.sweep_tenant(conn, seeded["organization"], moment=at(),
                                       example_source=source)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        second = scheduler.sweep_tenant(conn, seeded["organization"], moment=at(),
                                        example_source=source)
        later = scheduler.sweep_tenant(conn, seeded["organization"],
                                       moment=at(minute=1),
                                       example_source=source)
        runs = conn.execute(
            "SELECT count(*) FROM clep.run WHERE trigger_kind = 'schedule'"
        ).fetchone()[0]
    assert first.fired and not second.fired
    assert second.triggers[0].outcome == scheduler.ALREADY_FIRED
    assert second.triggers[0].run_id == first.fired[0].run_id
    # The next minute is a different trigger, and does fire.
    assert later.fired and later.fired[0].run_id != first.fired[0].run_id
    assert runs == 2
    assert schedule_id == first.fired[0].schedule_id


@pytest.mark.integration
@requires_postgres
def test_an_over_budget_schedule_creates_no_run_at_all(
        migrated_database, seeded, tmp_path):
    """REQ-F-10-5. Skipped, not started and stopped: a run that halts halfway
    has spent money and left an incomplete record, which is the worse of both."""
    from clep.db.session import tenant_session
    write_examples(tmp_path, seeded, ["q1", "q2", "q3", "q4"])
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        make_schedule(conn, seeded, budget="0.000000001")
        outcome = scheduler.sweep_tenant(
            conn, seeded["organization"], moment=at(),
            example_source=StoredExampleSource(payload_reader=file_payload_reader))
        runs = conn.execute("SELECT count(*) FROM clep.run").fetchone()[0]
        samples = conn.execute("SELECT count(*) FROM clep.run_sample").fetchone()[0]
    assert outcome.triggers[0].outcome == scheduler.OVER_BUDGET
    assert "exceeds the budget" in outcome.triggers[0].detail
    assert (runs, samples) == (0, 0)


@pytest.mark.integration
@requires_postgres
def test_a_dataset_whose_content_was_erased_stops_the_run_rather_than_shrinking_it(
        migrated_database, seeded, tmp_path):
    from clep.db.session import tenant_session
    write_examples(tmp_path, seeded, ["q1", "q2"])
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        audit_id = ulid_to_uuid(new_ulid())
        conn.execute(
            "INSERT INTO clep.audit_event (id, organization_id, actor_id, action,"
            " target_type, target_id) VALUES (%s,%s,%s,'example.erased',"
            "'example_content',%s)",
            (audit_id, seeded["organization"], audit_id, audit_id))
        conn.execute(
            "UPDATE clep.example_content SET payload_ref = NULL, "
            "erased_at = now(), erasure_audit_id = %s "
            "WHERE organization_id = %s AND id = ("
            "  SELECT id FROM clep.example_content WHERE organization_id = %s "
            "  ORDER BY id DESC LIMIT 1)",
            (audit_id, seeded["organization"], seeded["organization"]))
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        make_schedule(conn, seeded)
        outcome = scheduler.sweep_tenant(
            conn, seeded["organization"], moment=at(),
            example_source=StoredExampleSource(payload_reader=file_payload_reader))
        runs = conn.execute("SELECT count(*) FROM clep.run").fetchone()[0]
    assert outcome.triggers[0].outcome == scheduler.NOT_EXECUTABLE
    assert "erased" in outcome.triggers[0].detail
    assert runs == 0


@pytest.mark.integration
@requires_postgres
def test_content_that_no_longer_matches_its_digest_stops_the_run(
        migrated_database, seeded, tmp_path):
    """Run identity is frozen over that digest (REQ-F-07-1). A run that
    evaluated different bytes under the same identity would be irreproducible in
    the one way nobody thinks to check."""
    from clep.db.session import tenant_session
    write_examples(tmp_path, seeded, ["q1"])
    edited = next(tmp_path.glob("*.json"))
    edited.write_bytes(json.dumps({"prompt": "something else"}).encode("utf-8"))
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        make_schedule(conn, seeded)
        outcome = scheduler.sweep_tenant(
            conn, seeded["organization"], moment=at(),
            example_source=StoredExampleSource(payload_reader=file_payload_reader))
        runs = conn.execute("SELECT count(*) FROM clep.run").fetchone()[0]
    assert outcome.triggers[0].outcome == scheduler.NOT_EXECUTABLE
    assert "does not match" in outcome.triggers[0].detail
    assert runs == 0


@pytest.mark.integration
@requires_postgres
def test_a_sweep_in_one_tenant_never_sees_another_tenants_schedule(
        migrated_database, seeded, second_organization, tmp_path):
    from clep.db.session import tenant_session
    write_examples(tmp_path, seeded, ["q1"])
    source = StoredExampleSource(payload_reader=file_payload_reader)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        make_schedule(conn, seeded)
    with tenant_session(migrated_database, second_organization) as conn:
        visible = ScheduleRepository(conn, second_organization).active_schedules()
        outcome = scheduler.sweep_tenant(conn, second_organization, moment=at(),
                                         example_source=source)
    assert visible == []
    assert outcome.considered == 0
    assert outcome.fired == []
