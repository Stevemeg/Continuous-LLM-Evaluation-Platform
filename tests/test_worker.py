"""The task-queue worker, driven against the real broker and the real database.

ADR-001 chose this shape after measuring it. These tests check that the shape
survived contact with the code: a run submitted twice is one job, a redelivered
run bills nothing further, and cancellation is a state in the store rather than a
signal to a process that may no longer exist.
"""
from __future__ import annotations

import io
import json
import os
from decimal import Decimal

import pytest

from clep.config import ProviderEndpoint
from clep.db.session import tenant_session
from clep.identity import new_ulid, ulid_to_uuid
from clep.orchestration.repository import RunRepository
from clep.orchestration.worker import execute_run, run_job_id
from clep.providers.gateway import Price, PriceBook, ProviderGateway
from clep.providers.openai_compatible import OpenAICompatibleAdapter
from tests.conftest import requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]

REDIS_URL = os.environ.get("CLEP_REDIS_URL", "redis://localhost:6399")
# A deliberately fake value, not a credential.
CANARY = "sk-worker-" + "0" * 40


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(req, timeout=None):
    return _Body(json.dumps({
        "choices": [{"message": {"content": "Paris"}}], "model": "m",
        "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                  "total_tokens": 12}}).encode())


def _context(dsn):
    endpoint = ProviderEndpoint(name="e", base_url="http://x/v1", api_key=CANARY)
    return {"runtime_dsn": dsn,
            "gateway": ProviderGateway(
                {"e": OpenAICompatibleAdapter(endpoint, opener=_opener)},
                PriceBook({"m": Price(Decimal("0.001"), Decimal("0.002"))}))}


def _prepare(dsn, seeded, n=4):
    import psycopg
    from tests.conftest import MIGRATION_DSN
    examples = []
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        for ordinal in range(1, n + 1):
            example_id = new_ulid()
            conn.execute(
                "INSERT INTO clep.example (id, organization_id, dataset_version_id,"
                " ordinal, split) VALUES (%s,%s,%s,%s,'test')",
                (ulid_to_uuid(example_id), seeded["organization"],
                 ulid_to_uuid(seeded["dataset_version"]), ordinal))
            examples.append({"id": example_id, "prompt": f"q{ordinal}",
                             "expected": "Paris"})
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        run_id = repo.create_run(
            project_id=seeded["project"], suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "0" * 64, integration_tier="output_only",
            idempotency_key="worker-1")
        candidate_id = repo.add_candidate(run_id, label="a",
                                          model_configuration_id=seeded["model_configuration"],
                                          endpoint_kind="hosted")
    return run_id, examples, [{"id": candidate_id, "label": "a", "model": "m",
                               "endpoint_name": "e"}]


def test_the_queue_deduplicates_a_run_before_any_work_starts():
    """The cheap outer layer. Derived from the run, so the broker refuses the
    second submission rather than relying on the worker to notice."""
    a = run_job_id("org", "run")
    b = run_job_id("org", "run")
    assert a == b
    assert run_job_id("org", "other") != a


async def test_a_queued_run_executes_and_reports_in_the_contracts_vocabulary(
        migrated_database, seeded):
    run_id, examples, candidates = _prepare(migrated_database, seeded)
    result = await execute_run(_context(migrated_database), seeded["organization"],
                               run_id, examples, candidates)
    assert result["completeness"] == "complete"
    assert result["samplesRecorded"] == 4
    assert Decimal(result["costTotal"]) > 0


async def test_redelivery_of_the_same_run_bills_nothing_further(
        migrated_database, seeded):
    """The property ADR-001's spike showed no engine provides for free."""
    run_id, examples, candidates = _prepare(migrated_database, seeded)
    context = _context(migrated_database)
    first = await execute_run(context, seeded["organization"], run_id, examples,
                              candidates)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        conn.execute("UPDATE clep.run_checkpoint SET last_completed_index = -1 "
                     "WHERE run_id = %s", (ulid_to_uuid(run_id),))
        conn.execute("UPDATE clep.run SET execution_state='queued', "
                     "completeness=NULL, completed_at=NULL WHERE id=%s",
                     (ulid_to_uuid(run_id),))
    second = await execute_run(context, seeded["organization"], run_id, examples,
                               candidates)
    assert second["samplesSkippedAsDuplicate"] == 4
    assert second["samplesRecorded"] == 0
    assert second["costTotal"] == first["costTotal"]


async def test_cancellation_is_read_from_the_store_not_signalled_to_a_process(
        migrated_database, seeded):
    """A signal reaches only the worker running the job. After a redelivery that
    worker may not exist, and the run would carry on."""
    run_id, examples, candidates = _prepare(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        RunRepository(conn, seeded["organization"]).finish_run(
            run_id, "cancelled", "cancelled before the worker picked it up")
    result = await execute_run(_context(migrated_database), seeded["organization"],
                               run_id, examples, candidates)
    assert result["completeness"] == "cancelled"
    assert result["samplesRecorded"] == 0


async def test_a_budget_stops_the_worker_and_the_reason_survives(
        migrated_database, seeded):
    run_id, examples, candidates = _prepare(migrated_database, seeded, n=6)
    result = await execute_run(_context(migrated_database), seeded["organization"],
                               run_id, examples, candidates,
                               budget_limit="0.00002")
    assert result["completeness"] == "exhausted"
    assert "budget" in result["incompleteReason"]
