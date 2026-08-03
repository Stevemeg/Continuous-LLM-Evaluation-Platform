"""The run loop, against a real database.

These are the tests the ADR-001 spike bought. It established that neither
candidate engine provides exactly-once effects, and that the unique idempotency
key is what makes `REQ-N-REL-2` true. A test suite that only checked the happy
path would leave that entirely unverified.
"""
from __future__ import annotations

import io
import json
import urllib.error
import uuid
from decimal import Decimal

import psycopg
import pytest

from clep.config import ProviderEndpoint
from clep.db.session import admin_session, tenant_session
from clep.evaluators.builtin import default_registry
from clep.identity import new_ulid, ulid_to_uuid
from clep.orchestration.repository import RunRepository, sample_key
from clep.orchestration.runner import Candidate, Example, RunExecutor
from clep.providers.gateway import Price, PriceBook, ProviderGateway
from clep.providers.openai_compatible import OpenAICompatibleAdapter
from tests.conftest import MIGRATION_DSN, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]

CANARY = "sk-test-" + "0" * 40
ENDPOINT = ProviderEndpoint(name="e", base_url="http://endpoint.invalid/v1",
                            api_key=CANARY)
PRICES = PriceBook({"m": Price(Decimal("0.001"), Decimal("0.002"))})


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def ok_opener(text="Paris"):
    def _open(req, timeout=None):
        return _Body(json.dumps({
            "choices": [{"message": {"content": text}}], "model": "m",
            "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                      "total_tokens": 12}}).encode())
    return _open


def failing_opener():
    def _open(req, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))
    return _open


def gateway(opener):
    return ProviderGateway({"e": OpenAICompatibleAdapter(ENDPOINT, opener=opener)},
                           PRICES)


@pytest.fixture
def examples(seeded):
    """Ten examples, seeded through the migration role."""
    made = []
    with admin_session(MIGRATION_DSN) as conn:
        for ordinal in range(1, 11):
            example_id = new_ulid()
            conn.execute(
                "INSERT INTO clep.example (id, organization_id, dataset_version_id,"
                " ordinal, split) VALUES (%s,%s,%s,%s,'test')",
                (ulid_to_uuid(example_id), seeded["organization"],
                 ulid_to_uuid(seeded["dataset_version"]), ordinal))
            made.append(Example(id=example_id, prompt=f"q{ordinal}", expected="Paris"))
    return made


def make_run(dsn, seeded, key="run-1", budget=None):
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        run_id = repo.create_run(
            project_id=seeded["project"], suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "0" * 64, integration_tier="output_only",
            idempotency_key=key,
            budget_limit=budget[0] if budget else None,
            budget_currency=budget[1] if budget else None)
        candidate_id = repo.add_candidate(
            run_id, label="a",
            model_configuration_id=seeded["model_configuration"],
            endpoint_kind="hosted")
    return run_id, Candidate(id=candidate_id, label="a", model="m", endpoint_name="e")


def execute(dsn, seeded, run_id, candidate, examples, **kw):
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        executor = RunExecutor(repo, kw.pop("gw", gateway(ok_opener())),
                               default_registry(), **kw.pop("executor", {}))
        return executor.execute(run_id, examples, [candidate], **kw)


# ------------------------------------------------------------------ happy path
def test_a_complete_run_records_every_sample_and_its_cost(
        migrated_database, seeded, examples):
    run_id, candidate = make_run(migrated_database, seeded)
    outcome = execute(migrated_database, seeded, run_id, candidate, examples)
    assert outcome.completeness == "complete"
    assert outcome.samples_recorded == 10
    assert outcome.samples_scored == 10
    # 10 prompt tokens at 0.001/1k plus 2 completion at 0.002/1k, ten times.
    expected = (Decimal("0.001") * 10 + Decimal("0.002") * 2) / 1000 * 10
    assert outcome.cost_total == expected


def test_the_run_is_readable_afterwards_in_the_contracts_vocabulary(
        migrated_database, seeded, examples):
    run_id, candidate = make_run(migrated_database, seeded)
    execute(migrated_database, seeded, run_id, candidate, examples)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        row = RunRepository(conn, seeded["organization"]).get_run(run_id)
    assert row.execution_state == "terminal"
    assert row.completeness == "complete"
    assert row.reproducibility == "reproducible"


# ------------------------------------------------------- idempotency, the point
def test_replaying_the_same_run_bills_nothing_further(
        migrated_database, seeded, examples):
    """The ADR-001 spike's finding, enforced. A redelivered work unit is
    recorded once; a second execution of the whole run adds no cost at all."""
    run_id, candidate = make_run(migrated_database, seeded)
    first = execute(migrated_database, seeded, run_id, candidate, examples)

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        # Reset by statement, not through advance_checkpoint: that method is
        # monotonic by design and refuses to move backwards, which is exactly
        # what test_the_checkpoint_never_moves_backwards asserts.
        conn.execute("UPDATE clep.run_checkpoint SET last_completed_index = -1 "
                     "WHERE run_id = %s", (ulid_to_uuid(run_id),))
        conn.execute("UPDATE clep.run SET execution_state='queued', "
                     "completeness=NULL, completed_at=NULL WHERE id=%s",
                     (ulid_to_uuid(run_id),))
    second = execute(migrated_database, seeded, run_id, candidate, examples)

    assert second.samples_skipped_as_duplicate == 10
    assert second.samples_recorded == 0
    assert second.cost_total == first.cost_total, "the replay billed the tenant twice"

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rows = conn.execute(
            "SELECT count(*) FROM clep.sample_cost WHERE run_id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0]
    assert rows == 10


def test_the_unique_constraint_refuses_a_duplicate_even_if_the_code_forgets(
        migrated_database, seeded, examples):
    """Belt and braces, and the braces are the database. Application-level
    de-duplication that is bypassed by a bug is no protection at all."""
    run_id, candidate = make_run(migrated_database, seeded)
    execute(migrated_database, seeded, run_id, candidate, examples)
    key = sample_key(run_id, "a", examples[0].id)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            conn.execute(
                "INSERT INTO clep.run_sample (id, organization_id, run_id, "
                "run_candidate_id, example_id, sample_index, resolution, score, "
                "idempotency_key) SELECT %s, organization_id, run_id, "
                "run_candidate_id, example_id, sample_index, resolution, score, "
                "idempotency_key FROM clep.run_sample WHERE idempotency_key = %s",
                (ulid_to_uuid(new_ulid()), key))


def test_the_idempotency_key_depends_only_on_what_the_work_is():
    """A key containing an attempt number or a timestamp would differ between
    the first attempt and the redelivery, and the constraint would protect
    nothing."""
    a = sample_key("run", "cand", "example")
    b = sample_key("run", "cand", "example")
    assert a == b and a.startswith("sha256:")
    assert sample_key("run", "cand", "other") != a


# ------------------------------------------------------------------- resumption
def test_a_run_resumes_from_its_checkpoint_without_recomputing(
        migrated_database, seeded, examples):
    run_id, candidate = make_run(migrated_database, seeded)
    execute(migrated_database, seeded, run_id, candidate, examples[:4])

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        assert repo.checkpoint(run_id) == 3
        conn.execute("UPDATE clep.run SET execution_state='running', "
                     "completeness=NULL, completed_at=NULL WHERE id=%s",
                     (ulid_to_uuid(run_id),))

    resumed = execute(migrated_database, seeded, run_id, candidate, examples)
    assert resumed.resumed_from_index == 4
    assert resumed.samples_recorded == 6
    assert resumed.samples_skipped_as_duplicate == 0
    assert "resumed at example index 4" in resumed.notes[0]


def test_the_checkpoint_never_moves_backwards(migrated_database, seeded, examples):
    """A redelivered job that has already been overtaken must not drag the
    marker back and cause completed work to be redone."""
    run_id, _ = make_run(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        repo.advance_checkpoint(run_id, 7)
        repo.advance_checkpoint(run_id, 2)
        assert repo.checkpoint(run_id) == 7


# ------------------------------------------------------- incomplete outcomes
def test_a_provider_failure_produces_no_score_rather_than_a_zero(
        migrated_database, seeded, examples):
    """REQ-X-8. A zero would drag the average down and read as a regression."""
    run_id, candidate = make_run(migrated_database, seeded)
    outcome = execute(migrated_database, seeded, run_id, candidate, examples,
                      gw=gateway(failing_opener()))
    assert outcome.samples_scored == 0
    assert outcome.samples_failed == 10
    assert outcome.completeness == "partial"
    assert "did not produce a score" in outcome.incomplete_reason
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        scores = conn.execute(
            "SELECT count(*) FROM clep.run_sample WHERE run_id = %s AND "
            "score IS NOT NULL", (ulid_to_uuid(run_id),)).fetchone()[0]
        kinds = conn.execute(
            "SELECT DISTINCT failure_kind FROM clep.run_sample WHERE run_id = %s",
            (ulid_to_uuid(run_id),)).fetchall()
    assert scores == 0
    assert kinds == [("provider_outage",)]


def test_a_partial_run_is_never_reported_as_complete(migrated_database, seeded,
                                                     examples):
    run_id, candidate = make_run(migrated_database, seeded)
    outcome = execute(migrated_database, seeded, run_id, candidate, examples,
                      gw=gateway(failing_opener()))
    assert outcome.completeness != "complete"
    assert outcome.incomplete_reason


def test_an_exhausted_budget_stops_the_run_and_says_so(
        migrated_database, seeded, examples):
    limit = Decimal("0.00002")           # enough for roughly one sample
    run_id, candidate = make_run(migrated_database, seeded, budget=(limit, "USD"))
    outcome = execute(migrated_database, seeded, run_id, candidate, examples,
                      budget_limit=limit, budget_currency="USD")
    assert outcome.completeness == "exhausted"
    assert "budget" in outcome.incomplete_reason
    assert outcome.samples_recorded < 10


def test_cancellation_leaves_a_consistent_clearly_incomplete_record(
        migrated_database, seeded, examples):
    """REQ-F-07-7. Samples already recorded stay valid; the run says plainly
    that it did not finish."""
    seen = {"n": 0}

    def cancel_after_three():
        seen["n"] += 1
        return seen["n"] > 3

    run_id, candidate = make_run(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        executor = RunExecutor(repo, gateway(ok_opener()), default_registry(),
                               is_cancelled=cancel_after_three)
        outcome = executor.execute(run_id, examples, [candidate])
    assert outcome.completeness == "cancelled"
    assert "cancelled after 3" in outcome.incomplete_reason
    assert outcome.samples_recorded == 3

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        assert repo.sample_counts(run_id) == {"scored": 3}
        assert repo.get_run(run_id).completeness == "cancelled"


# ------------------------------------------------------------------- run reuse
def test_resubmitting_the_same_idempotency_key_returns_the_same_run(
        migrated_database, seeded):
    first, _ = make_run(migrated_database, seeded, key="same")
    second, _ = make_run(migrated_database, seeded, key="same")
    assert first == second


def test_a_different_key_creates_a_different_run(migrated_database, seeded):
    first, _ = make_run(migrated_database, seeded, key="one")
    second, _ = make_run(migrated_database, seeded, key="two")
    assert first != second


# ------------------------------------------------------------------ evaluators
def test_evaluator_outcomes_are_recorded_per_sample(
        migrated_database, seeded, examples):
    run_id, candidate = make_run(migrated_database, seeded)
    registry = default_registry()
    ids = {key: seeded["evaluator_version"] for key in registry.keys()[:1]}
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        executor = RunExecutor(repo, gateway(ok_opener()), registry,
                               evaluator_ids=ids)
        outcome = executor.execute(run_id, examples[:3], [candidate])
    assert outcome.evaluator_outcomes == 3
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        rows = conn.execute(
            "SELECT resolution, count(*) FROM clep.evaluator_outcome "
            "GROUP BY resolution").fetchall()
    assert dict(rows) == {"scored": 3}
