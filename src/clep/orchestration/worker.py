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
from datetime import datetime, timezone
from decimal import Decimal

from arq import cron
from arq.connections import RedisSettings

from clep.db.session import tenant_session
from clep.evaluators.builtin import default_registry
from clep.orchestration.repository import RunRepository
from clep.orchestration.runner import RunExecutor
from clep.orchestration.scheduler import (SWEEP_SECONDS, execute_scheduled_run,
                                          sweep_schedules)
from clep.telemetry import NULL_TELEMETRY

JOB_TIMEOUT = int(os.environ.get("CLEP_JOB_TIMEOUT", "300"))
POLL_DELAY = float(os.environ.get("CLEP_POLL_DELAY", "0.5"))


def observe_queue_time(ctx, queue: str = "default") -> None:
    """Metric class 3, and the only place it can honestly be measured.

    Queue time is enqueue to pick-up, and the worker is the first component that
    knows both ends: the queue holds the enqueue instant, and this function runs
    at the pick-up instant. Measured anywhere else it would be a guess.

    It answers the question a single latency figure cannot — whether slowness is
    contention or execution — and `REQ-N-PERF-2` turns on exactly that
    distinction, since throughput scaling with concurrency controls looks
    identical to throughput not scaling at all if you only measure the work.
    """
    telemetry = ctx.get("telemetry") or NULL_TELEMETRY
    enqueued = ctx.get("enqueue_time")
    if enqueued is not None:
        # arq hands this back as UTC; a naive value is treated as UTC rather
        # than as local time, because assuming local would make queue time
        # jump by the offset on any machine that is not on UTC.
        if enqueued.tzinfo is None:
            enqueued = enqueued.replace(tzinfo=timezone.utc)
        waited = (datetime.now(timezone.utc) - enqueued).total_seconds() * 1000.0
        telemetry.observe("clep_work_unit_queue_duration_ms",
                          max(0.0, waited), queue=queue)
    # A second or later attempt is a redelivery. Retryable, because the queue
    # would not have redelivered it otherwise — and stability degrading beneath
    # successful outcomes is exactly what metric class 8 exists to show.
    if int(ctx.get("job_try") or 1) > 1:
        telemetry.observe("clep_retry_total", 1, surface="worker",
                          retryable="retryable")


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
    observe_queue_time(ctx, ctx.get("queue_label") or "default")

    with tenant_session(dsn, organization_id) as conn:
        repository = RunRepository(conn, organization_id)
        executor = RunExecutor(
            repository, gateway, registry,
            evaluator_ids=ctx.get("evaluator_ids"),
            telemetry=ctx.get("telemetry"),
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


def sweep_cron(every_seconds: int = SWEEP_SECONDS):
    """The trigger `REQ-F-10-1` needs: the queue's own scheduler, not a caller.

    A factory rather than a constant so that the cadence is configuration in a
    deployment and can be a second in a test, without the test inventing a
    trigger of its own. `run_at_startup` is False deliberately — a worker
    restarting would otherwise fire every schedule whose minute it happened to
    land in, which is a redeploy that looks like a cadence.
    """
    every = max(1, min(60, int(every_seconds)))
    return cron(sweep_schedules, second=set(range(0, 60, every)),
                run_at_startup=False, unique=True, max_tries=1)


class WorkerSettings:
    functions = [execute_run, execute_scheduled_run]
    cron_jobs = [sweep_cron()]
    job_timeout = JOB_TIMEOUT
    poll_delay = POLL_DELAY
    max_tries = 10
    health_check_interval = 15

    @staticmethod
    def redis_settings():
        return redis_settings()
