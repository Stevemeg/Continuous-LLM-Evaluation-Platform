"""A schedule, fired by the queue's own cron, all the way to an observation.

This is the test M11.1 exists for, and what it deliberately does NOT do is the
point of it. Nothing here calls the sweep. Nothing here calls the run loop.
Nothing here enqueues a job. A real `arq.worker.Worker` is started against the
real broker with the real `sweep_cron()` from `clep.orchestration.worker` — the
same factory the deployment's `WorkerSettings` uses, with its cadence turned down
to a second so a test does not wait a minute — and everything after that happens
because the worker decided it should.

What the test then asserts is the whole chain:

    an active schedule whose cadence matches the minute
      -> arq's cron enqueues the sweep
      -> the sweep creates a run, with trigger_kind from the schedule
      -> the sweep enqueues the run
      -> the worker executes it through RunExecutor
      -> evaluators run and their outcomes are persisted
      -> samples, costs and model latency are persisted
      -> the gate is evaluated against the approved baseline
      -> a release observation records what the platform RECOMMENDS
      -> all of it is readable back out of the store

The only stub anywhere is the HTTP opener beneath the provider adapter, for the
same reason as in `test_end_to_end.py`: a deterministic test cannot depend on a
model being installed. Everything above that transport is production code.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import uuid
from decimal import Decimal

import psycopg
import pytest
from arq.worker import Worker

from clep.config import ProviderEndpoint
from clep.db.session import tenant_session
from clep.evaluators.sdk import EvaluatorRegistry
from clep.identity import new_ulid, ulid_to_uuid
from clep.orchestration.examples import (StoredExampleSource, digest_of,
                                         file_payload_reader)
from clep.orchestration.releases import ReleaseObservationRepository
from clep.orchestration.repository import RunRepository
from clep.orchestration.runner import Candidate, Example, RunExecutor
from clep.orchestration.schedules import ScheduleRepository
from clep.orchestration.worker import (execute_run, execute_scheduled_run,
                                       sweep_cron)
from clep.providers.gateway import Price, PriceBook, ProviderGateway
from clep.providers.openai_compatible import OpenAICompatibleAdapter
from clep.regression.repository import RegressionRepository
from tests.conftest import (MIGRATION_DSN, REDIS_URL, requires_postgres,
                            requires_redis)

pytestmark = [pytest.mark.integration, requires_postgres, requires_redis]

ANSWER = "Paris"
#: The model identifier the shared fixture seeds. The scheduler reads the model
#: from the run's own candidate rows rather than from anything a caller passes,
#: so the price book has to be declared for the identifier the registry holds —
#: a mismatch here produces an unpriced call and a run that costs nothing, which
#: is exactly the silent zero `REQ-F-07-6` refuses.
MODEL = "m"
#: Long enough for a one-second cron to fire, the sweep to run, the job to be
#: picked up and the run to execute; short enough that a broken chain fails the
#: test rather than hanging the suite.
DEADLINE_SECONDS = 40


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(request, timeout=None):
    body = json.loads(request.data.decode())
    return _Body(json.dumps({
        "choices": [{"message": {"content": ANSWER}}], "model": body["model"],
        "usage": {"prompt_tokens": 9, "completion_tokens": 3,
                  "total_tokens": 12}}).encode())


def gateway() -> ProviderGateway:
    endpoint = ProviderEndpoint(name="stub", base_url="http://endpoint.invalid/v1",
                                api_key="")
    return ProviderGateway(
        {"stub": OpenAICompatibleAdapter(endpoint, opener=_opener)},
        PriceBook({MODEL: Price(Decimal("0.001"), Decimal("0.002"))}))


@pytest.fixture
def dataset_on_disk(seeded, tmp_path):
    """Three examples whose payloads are real files with real digests.

    The record is in PostgreSQL and the payload is not — ADR-005's split, and
    ADR-013's object store, whose S3 adapter belongs to the deployment phase. A
    `file://` reference is what a local deployment has until then, and
    `StoredExampleSource` verifies the digest either way.
    """
    prompts = ["What is the capital of France?", "Name the capital of France.",
               "France's capital city?"]
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        seed = json.dumps({"prompt": prompts[0], "expected": ANSWER}).encode()
        seed_path = tmp_path / "seeded.json"
        seed_path.write_bytes(seed)
        conn.execute(
            "UPDATE clep.example_content SET payload_ref = %s,"
            " content_digest = %s, byte_size = %s"
            " WHERE organization_id = %s AND example_id IN ("
            "   SELECT id FROM clep.example WHERE organization_id = %s"
            "   AND dataset_version_id = %s)",
            (seed_path.as_uri(), digest_of(seed), len(seed),
             seeded["organization"], seeded["organization"],
             ulid_to_uuid(seeded["dataset_version"])))
        for ordinal, prompt in enumerate(prompts[1:], start=200):
            example_id = new_ulid()
            payload = json.dumps({"prompt": prompt, "expected": ANSWER}).encode()
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
                (uuid.uuid4(), seeded["organization"], ulid_to_uuid(example_id),
                 digest_of(payload), path.as_uri(), len(payload)))
    return prompts


def registry_and_ids(seeded):
    """One evaluator, bound to the one evaluator version the fixture seeded.

    One rather than the three built-ins on purpose. Phase 10 found that mapping
    several registry keys onto a single `evaluator_version_id` makes the gate's
    lookup by metric name match every evaluator's outcomes at once and compare a
    mixture; a metric has to resolve to exactly one evaluator or it is not that
    metric.
    """
    from clep.evaluators.builtin import ExactMatch
    registry = EvaluatorRegistry()
    registry.register(ExactMatch(), is_builtin=True)
    return registry, {key: seeded["evaluator_version"] for key in registry.keys()}


def approved_baseline(dsn, seeded, examples) -> str:
    """A baseline from a run this file executed, through the same loop.

    Its identity is built and captured the same way the scheduler builds the
    candidate's, because a gate decides comparability from the captured
    components before it computes anything. A baseline with a fabricated digest
    is not comparable with a real run, and the gate would report `hard_fail` for
    a reason that is about the fixture rather than about quality.
    """
    from clep.experiments.capture import build_run_identity
    from clep.experiments.repository import IdentityRepository

    registry, evaluator_ids = registry_and_ids(seeded)
    candidates = [{"label": "a",
                   "modelConfigurationId": seeded["model_configuration"]}]
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        identity = build_run_identity(
            conn, seeded["organization"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            integration_tier="output_only", candidates=candidates)
        run_id = repo.create_run(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest=identity.digest(), integration_tier="output_only",
            idempotency_key="scheduled-baseline")
        IdentityRepository(conn, seeded["organization"]).capture(run_id, identity)
        candidate_id = repo.add_candidate(
            run_id, label="a",
            model_configuration_id=seeded["model_configuration"],
            endpoint_kind="hosted")
        RunExecutor(repo, gateway(), registry, evaluator_ids=evaluator_ids
                    ).execute(
            run_id, [Example(id=e, prompt=p, expected=ANSWER)
                     for e, p in examples],
            [Candidate(id=candidate_id, label="a", model=MODEL,
                       endpoint_name="stub")])
    with tenant_session(dsn, seeded["organization"]) as conn:
        regression = RegressionRepository(conn, seeded["organization"])
        baseline_id = regression.create_baseline(run_id=run_id,
                                                 created_by="tester",
                                                 label="scheduled")
        regression.approve_baseline(baseline_id, approved_by="tester")
    return baseline_id


def published_policy(dsn, seeded) -> str:
    with tenant_session(dsn, seeded["organization"]) as conn:
        metric = conn.execute(
            "SELECT d.slug FROM clep.evaluator_version v "
            "JOIN clep.evaluator_definition d ON d.id = v.evaluator_definition_id "
            "WHERE v.id = %s",
            (ulid_to_uuid(seeded["evaluator_version"]),)).fetchone()[0]
        repo = RegressionRepository(conn, seeded["organization"])
        policy_id = repo.create_gate_policy(project_id=seeded["project"],
                                            slug="scheduled", display_name="S")
        version_id = repo.add_policy_version(
            policy_id, confidence_level=Decimal("0.95"), resample_count=100,
            bootstrap_seed=20260811, created_by="tester")
        repo.add_criterion(
            version_id, metric_key=metric, dimension="quality",
            source="evaluator", direction="higher_is_better",
            precision_threshold=Decimal("0.5"), on_regression="hard_fail",
            on_insufficient_evidence="warning", on_not_comparable="hard_fail")
        repo.publish_policy_version(version_id)
    return version_id


def stored_examples(dsn, seeded):
    with tenant_session(dsn, seeded["organization"]) as conn:
        rows = conn.execute(
            "SELECT id FROM clep.example WHERE organization_id = %s "
            "AND dataset_version_id = %s ORDER BY ordinal",
            (seeded["organization"],
             ulid_to_uuid(seeded["dataset_version"]))).fetchall()
    from clep.identity import uuid_to_ulid
    return [uuid_to_ulid(r[0]) for r in rows]


async def run_worker_until(dsn, organization_id, predicate, *, queue_name,
                           context, deadline=DEADLINE_SECONDS):
    """Start a real worker with the real cron, and wait for the store to change.

    The worker is the only thing that acts. This function creates no jobs and
    calls no sweep; it starts the worker, watches PostgreSQL, and stops.
    """
    from arq.connections import RedisSettings
    settings = RedisSettings.from_dsn(REDIS_URL)
    # The run loop is synchronous psycopg inside an async job, so the event loop
    # stops being serviced while a run executes. arq's one-second connect
    # timeout is fine against an idle broker and not fine behind ninety seconds
    # of other integration tests — the first version of this failed only when
    # the whole suite ran, which is the worst way for a test to be flaky.
    settings.conn_timeout = 15
    settings.conn_retries = 10
    settings.conn_retry_delay = 0.5
    worker = Worker(
        functions=[execute_run, execute_scheduled_run],
        cron_jobs=[sweep_cron(1)], redis_settings=settings,
        queue_name=queue_name, ctx=dict(context), poll_delay=0.1,
        max_jobs=2, job_timeout=120, keep_result=5, handle_signals=False,
        health_check_interval=5, retry_jobs=False)
    task = asyncio.create_task(worker.async_run())
    try:
        for _ in range(int(deadline / 0.25)):
            await asyncio.sleep(0.25)
            if task.done():
                task.result()  # re-raise whatever killed the worker

            def _look():
                with tenant_session(dsn, organization_id) as conn:
                    return predicate(conn)

            # Off the event loop: a synchronous query here would compete with
            # the worker it is watching.
            found = await asyncio.to_thread(_look)
            if found:
                return found
        raise AssertionError(
            f"nothing happened within {deadline}s; the cron trigger did not "
            f"reach an observable outcome "
            f"(jobs complete={worker.jobs_complete}, "
            f"failed={worker.jobs_failed}, retried={worker.jobs_retried})")
    finally:
        # Stopping a worker mid-poll interrupts whatever redis call it was in,
        # which surfaces as a connection error from the cancelled task. By this
        # point the outcome is already decided, so the shutdown is quiet on
        # purpose rather than adding a failure that is about teardown.
        logging.getLogger("arq.worker").setLevel(logging.CRITICAL)
        asyncio.get_running_loop().set_exception_handler(lambda loop, ctx: None)
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: B014 - see above
            pass
        # Not `worker.close()`: arq's close path raises SIGUSR1 at a worker it
        # did not install signal handlers for, and SIGUSR1 does not exist on
        # Windows. The pool is what actually needs releasing.
        pool = getattr(worker, "_pool", None)
        if pool is not None:
            try:
                await pool.aclose(close_connection_pool=True)
            except Exception:  # noqa: BLE001 - teardown of an interrupted pool
                pass


def worker_context(dsn, seeded):
    registry, evaluator_ids = registry_and_ids(seeded)
    return {"runtime_dsn": dsn, "gateway": gateway(), "registry": registry,
            "evaluator_ids": evaluator_ids,
            "example_source": StoredExampleSource(
                payload_reader=file_payload_reader)}


# ============================================================== the whole chain
async def test_a_cron_trigger_produces_a_run_a_gate_decision_and_an_observation(
        migrated_database, seeded, dataset_on_disk):
    dsn = migrated_database
    organization = seeded["organization"]
    example_ids = stored_examples(dsn, seeded)
    baseline_id = approved_baseline(
        dsn, seeded, list(zip(example_ids, dataset_on_disk)))
    policy_version_id = published_policy(dsn, seeded)

    with tenant_session(dsn, organization) as conn:
        schedule_id = ScheduleRepository(conn, organization).create_schedule(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            cadence="* * * * *", trigger_kind="post_deployment",
            budget_limit=Decimal("5"), budget_currency="USD",
            created_by="tester", baseline_id=baseline_id,
            gate_policy_version_id=policy_version_id,
            candidates=[{"label": "candidate",
                         "modelConfigurationId": seeded["model_configuration"]}])

    def an_observation_exists(conn):
        return ReleaseObservationRepository(conn, organization).list_for_project(
            seeded["project"])

    observations = await run_worker_until(
        dsn, organization, an_observation_exists,
        queue_name=f"arq:test:{uuid.uuid4().hex[:12]}",
        context=worker_context(dsn, seeded))

    assert len(observations) == 1
    observation = observations[0]
    assert observation.trigger_kind == "post_deployment"
    assert observation.recommendation in ("none", "investigate", "rollback",
                                          "hold_release")
    assert observation.gate_decision_id, "the observation cites no gate decision"
    assert "platform changes nothing itself" in observation.rationale

    with tenant_session(dsn, organization) as conn:
        run = conn.execute(
            "SELECT id, trigger_kind, execution_state, completeness, "
            "       idempotency_key FROM clep.run WHERE organization_id = %s "
            "AND id = %s", (organization, ulid_to_uuid(observation.run_id))
        ).fetchone()
        samples = conn.execute(
            "SELECT count(*), count(model_latency_ms), "
            "       count(*) FILTER (WHERE resolution = 'scored') "
            "FROM clep.run_sample WHERE run_id = %s",
            (ulid_to_uuid(observation.run_id),)).fetchone()
        evaluator_outcomes = conn.execute(
            "SELECT count(*) FROM clep.evaluator_outcome eo "
            "JOIN clep.run_sample s ON s.id = eo.run_sample_id "
            "WHERE s.run_id = %s", (ulid_to_uuid(observation.run_id),)
        ).fetchone()[0]
        costs = conn.execute(
            "SELECT count(*), sum(cost_amount) FROM clep.sample_cost "
            "WHERE run_id = %s", (ulid_to_uuid(observation.run_id),)).fetchone()
        decision = conn.execute(
            "SELECT evaluated_outcome, baseline_id FROM clep.gate_decision "
            "WHERE organization_id = %s AND id = %s",
            (organization, ulid_to_uuid(observation.gate_decision_id))).fetchone()
        schedule = ScheduleRepository(conn, organization).get_schedule(schedule_id)

    # The run happened, and it says why it happened.
    assert run[1] == "post_deployment"
    assert run[2] == "terminal"
    assert run[3] == "complete"
    assert run[4].startswith(f"schedule:{schedule_id}:")

    # The evaluators ran through the harness and their outcomes are persisted.
    assert samples[0] == len(example_ids) == 3
    assert samples[2] == 3, "the scheduled run scored nothing"
    assert evaluator_outcomes >= 3
    # REQ-F-11-3: latency was measured at the gateway and written with the sample.
    assert samples[1] == 3, "no model latency was recorded"
    assert costs[0] == 3 and costs[1] > 0

    # The gate ran against the approved baseline and the decision is persisted.
    assert decision[0] in ("pass", "warning", "insufficient_evidence")
    assert decision[1] is not None

    # And the schedule records what it last produced.
    assert schedule.last_run_id == observation.run_id


async def test_a_schedule_whose_cadence_does_not_match_produces_nothing(
        migrated_database, seeded, dataset_on_disk):
    """The negative half. The same worker, the same cron, a cadence that fires
    at 03:30 — and after the same wait, no run exists."""
    dsn = migrated_database
    organization = seeded["organization"]
    with tenant_session(dsn, organization) as conn:
        ScheduleRepository(conn, organization).create_schedule(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"], cadence="30 3 1 1 *",
            budget_limit=Decimal("5"), budget_currency="USD",
            created_by="tester",
            candidates=[{"label": "candidate",
                         "modelConfigurationId": seeded["model_configuration"]}])

    def a_run_exists(conn):
        return conn.execute(
            "SELECT count(*) FROM clep.run WHERE organization_id = %s",
            (organization,)).fetchone()[0] > 0

    with pytest.raises(AssertionError, match="did not reach an observable"):
        await run_worker_until(
            dsn, organization, a_run_exists,
            queue_name=f"arq:test:{uuid.uuid4().hex[:12]}",
            context=worker_context(dsn, seeded), deadline=6)


async def test_the_cron_sweeps_only_the_tenant_that_owns_the_schedule(
        migrated_database, seeded, second_organization, dataset_on_disk):
    """Tenant isolation under the real trigger. The sweep considers every
    tenant; row-level security is what stops it acting for the wrong one."""
    dsn = migrated_database
    organization = seeded["organization"]
    with tenant_session(dsn, organization) as conn:
        ScheduleRepository(conn, organization).create_schedule(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"], cadence="* * * * *",
            budget_limit=Decimal("5"), budget_currency="USD",
            created_by="tester",
            candidates=[{"label": "candidate",
                         "modelConfigurationId": seeded["model_configuration"]}])

    def a_terminal_run_exists(conn):
        return conn.execute(
            "SELECT count(*) FROM clep.run WHERE organization_id = %s "
            "AND execution_state = 'terminal'", (organization,)).fetchone()[0]

    assert await run_worker_until(
        dsn, organization, a_terminal_run_exists,
        queue_name=f"arq:test:{uuid.uuid4().hex[:12]}",
        context=worker_context(dsn, seeded))

    with tenant_session(dsn, second_organization) as conn:
        assert conn.execute("SELECT count(*) FROM clep.run").fetchone()[0] == 0
        assert conn.execute(
            "SELECT count(*) FROM clep.run_sample").fetchone()[0] == 0
