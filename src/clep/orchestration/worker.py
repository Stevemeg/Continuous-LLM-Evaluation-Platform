"""The task-queue worker ADR-001 selected.

One job per run, not one per sample. The ADR's spike compared exactly this shape
against a durable workflow engine: the queue delivers the run, the worker resumes
from the checkpoint, and redelivery after worker loss is safe because the effects
are keyed rather than because the engine promises anything.

`job_timeout` is the detection window for worker loss and therefore the dominant
term in resume latency. The spike measured it as a setting rather than an engine
property, which is why it is configuration here and not a constant.

`max_tries` is deliberately generous: a redelivered run costs nothing to replay,
because every effect it would repeat is refused by a unique constraint.
"""
from __future__ import annotations

import os
from decimal import Decimal

from arq.connections import RedisSettings

from clep.db.session import tenant_session
from clep.evaluators.builtin import default_registry
from clep.orchestration.repository import RunRepository
from clep.orchestration.runner import RunExecutor

JOB_TIMEOUT = int(os.environ.get("CLEP_JOB_TIMEOUT", "300"))
POLL_DELAY = float(os.environ.get("CLEP_POLL_DELAY", "0.5"))


def redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(
        os.environ.get("CLEP_REDIS_URL", "redis://localhost:6399"))


def run_job_id(organization_id: str, run_id: str) -> str:
    """The queue's own de-duplication key.

    Derived from the run rather than generated, so that submitting the same run
    twice is refused by the broker before any work starts. This is the cheap
    outer layer; the unique constraints in the store are the one that must hold
    when this layer is bypassed by a worker crash.
    """
    return f"clep-run:{organization_id}:{run_id}"


async def execute_run(ctx, organization_id: str, run_id: str,
                      examples: list, candidates: list,
                      budget_limit: str | None = None,
                      budget_currency: str = "USD",
                      integration_tier: str = "output_only") -> dict:
    """Entry point for a queued run.

    The gateway is taken from the worker context rather than constructed here, so
    that a deployment configures its endpoints once and a job cannot quietly
    invent a different one.
    """
    from clep.orchestration.runner import Candidate, Example

    dsn = ctx["runtime_dsn"]
    gateway = ctx["gateway"]
    registry = ctx.get("registry") or default_registry()

    with tenant_session(dsn, organization_id) as conn:
        repository = RunRepository(conn, organization_id)
        executor = RunExecutor(
            repository, gateway, registry,
            evaluator_ids=ctx.get("evaluator_ids"),
            is_cancelled=lambda: _is_cancelled(repository, run_id))
        outcome = executor.execute(
            run_id,
            [Example(**e) for e in examples],
            [Candidate(**c) for c in candidates],
            budget_limit=Decimal(budget_limit) if budget_limit else None,
            budget_currency=budget_currency,
            integration_tier=integration_tier)

    return {"completeness": outcome.completeness,
            "incompleteReason": outcome.incomplete_reason,
            "samplesRecorded": outcome.samples_recorded,
            "samplesSkippedAsDuplicate": outcome.samples_skipped_as_duplicate,
            "costTotal": str(outcome.cost_total)}


def _is_cancelled(repository: RunRepository, run_id: str) -> bool:
    """Cancellation is a state in the store, not a signal to a process.

    A signal only reaches the worker that happens to be running the job; a state
    is seen by whichever worker picks it up after a redelivery, which is the
    case that matters.
    """
    run = repository.get_run(run_id)
    return bool(run and run.completeness == "cancelled")


class WorkerSettings:
    functions = [execute_run]
    job_timeout = JOB_TIMEOUT
    poll_delay = POLL_DELAY
    max_tries = 10
    health_check_interval = 15

    @staticmethod
    def redis_settings():
        return redis_settings()
