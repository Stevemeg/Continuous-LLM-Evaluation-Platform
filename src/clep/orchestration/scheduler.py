"""The sweep that turns a standing order into a run.

`REQ-F-10-1` requires evaluations to execute on a schedule without human
initiation, and the word doing the work is *execute*. A stored schedule and a
CRUD surface over it are not that; neither is a callback someone invokes by hand.
So the trigger here is the task queue's own cron, registered on the worker
ADR-001 selected, and the sweep is the function it calls. Nothing in this module
decides when to run — it is told, by a scheduler it does not own.

The sweep is deliberately thin and deliberately refuses in four places.

**Before anything, the budget.** `REQ-F-10-5` requires a run whose estimate
exceeds its budget to be *skipped*, not started and stopped. The estimate comes
from the planner — the same `draft_plan`/`validate` pair a human-reviewed plan
goes through — rather than from a second cost model that would eventually
disagree with the first. A schedule that does not validate creates no run at all,
so there is no half-run to explain later.

**A trigger has an identity.** The idempotency key is the schedule and the UTC
minute its cadence matched. Two sweeps in the same minute — an overlapping
worker, a redelivery, a second process — derive the same key, find the run that
already exists, and enqueue nothing. This is checked before the run is created
rather than relied on afterwards, because `create_run` returning an identifier
cannot distinguish the first firing from the second.

**One tenant at a time.** The sweep asks which tenants exist and then opens a
real tenant session for each. Every schedule, run, sample and observation is
written under row-level security with that tenant's context, so a schedule in one
tenant cannot produce a run in another — and that is enforced by the store rather
than by this loop being careful.

**A release observation only where there is a release.** A `schedule` trigger
evaluates something before it ships; only `post_deployment` and `canary` describe
a system that is already live, and only those record an observation. What the
observation recommends comes from `releases.BY_OUTCOME`, and the platform acts on
none of it (`REQ-F-10-3`).
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from clep.agents.planner import PlanInputs, draft_plan, validate
from clep.db.session import known_organizations, tenant_session
from clep.experiments.capture import build_run_identity
from clep.experiments.repository import IdentityRepository
from clep.orchestration.examples import ExampleUnavailable
from clep.orchestration.releases import (ReleaseObservationRepository,
                                         recommendation_for)
from clep.orchestration.repository import RunRepository
from clep.orchestration.schedules import ScheduleRepository, trigger_key

#: How often the cron fires. A sweep costs one query per tenant and creates
#: nothing unless a cadence matches the minute, so the default is every minute —
#: the finest granularity a five-field cron expression can ask for.
SWEEP_SECONDS = int(os.environ.get("CLEP_SWEEP_SECONDS", "60"))

FIRED = "fired"
ALREADY_FIRED = "already_fired"
OVER_BUDGET = "over_budget"
NOT_EXECUTABLE = "not_executable"


@dataclass(frozen=True)
class Trigger:
    """What the sweep did about one due schedule, and why."""
    schedule_id: str
    outcome: str
    run_id: str | None = None
    detail: str = ""

    @property
    def created_a_run(self) -> bool:
        return self.outcome == FIRED


@dataclass
class SweepOutcome:
    considered: int = 0
    triggers: list = field(default_factory=list)

    @property
    def fired(self) -> list:
        return [t for t in self.triggers if t.created_a_run]

    def summary(self) -> dict:
        counts: dict[str, int] = {}
        for trigger in self.triggers:
            counts[trigger.outcome] = counts.get(trigger.outcome, 0) + 1
        return {"considered": self.considered, "outcomes": counts,
                "runs": [t.run_id for t in self.fired]}


def sweep_tenant(conn, organization_id: str, *, moment: datetime,
                 example_source, unit_cost: Decimal = Decimal("0.001")
                 ) -> SweepOutcome:
    """Create a run for every schedule this minute is due, and no others.

    Pure with respect to the queue: it writes runs and returns what it created,
    and the caller enqueues. Separating the two is what lets the whole eligibility
    and budget rule be tested against a real database without a broker.
    """
    schedules = ScheduleRepository(conn, organization_id)
    runs = RunRepository(conn, organization_id)
    outcome = SweepOutcome()

    for schedule in schedules.due_schedules(moment):
        outcome.considered += 1
        key = trigger_key(schedule.id, moment)
        existing = runs.find_run_by_idempotency_key(schedule.project_id, key)
        if existing:
            outcome.triggers.append(Trigger(
                schedule.id, ALREADY_FIRED, existing,
                f"this schedule already fired for "
                f"{moment.astimezone(timezone.utc):%Y-%m-%dT%H:%MZ}"))
            continue

        problem = _cannot_execute(conn, organization_id, schedules, schedule,
                                  example_source, unit_cost)
        if problem:
            reason, detail = problem
            outcome.triggers.append(Trigger(schedule.id, reason, None, detail))
            continue

        dataset_version_id = schedules.dataset_version_for(schedule.suite_version_id)
        candidates = [{"label": c.label,
                       "modelConfigurationId": c.model_configuration_id,
                       "promptVersionId": c.prompt_version_id,
                       "endpointKind": c.endpoint_kind}
                      for c in schedule.candidates]
        identity = build_run_identity(
            conn, organization_id, suite_version_id=schedule.suite_version_id,
            dataset_version_id=dataset_version_id,
            integration_tier="output_only", candidates=candidates)
        run_id = runs.create_run(
            project_id=schedule.project_id,
            suite_version_id=schedule.suite_version_id,
            dataset_version_id=dataset_version_id,
            identity_digest=identity.digest(), integration_tier="output_only",
            idempotency_key=key, budget_limit=schedule.budget_limit,
            budget_currency=schedule.budget_currency,
            trigger_kind=schedule.trigger_kind)
        IdentityRepository(conn, organization_id).capture(run_id, identity)
        for spec in candidates:
            runs.add_candidate(run_id, label=spec["label"],
                               model_configuration_id=spec["modelConfigurationId"],
                               prompt_version_id=spec["promptVersionId"],
                               endpoint_kind=spec["endpointKind"])
        schedules.record_run(schedule.id, run_id)
        outcome.triggers.append(Trigger(
            schedule.id, FIRED, run_id,
            f"cadence {schedule.cadence!r} matched; trigger {key}"))
    return outcome


def _cannot_execute(conn, organization_id, schedules, schedule, example_source,
                    unit_cost):
    """Every reason this schedule must not produce a run, before one exists."""
    dataset_version_id = schedules.dataset_version_for(schedule.suite_version_id)
    if dataset_version_id is None:
        return (NOT_EXECUTABLE,
                f"suite version {schedule.suite_version_id} has no dataset "
                f"version; there is nothing to evaluate")
    try:
        examples = example_source.load(conn, organization_id, dataset_version_id)
    except ExampleUnavailable as e:
        return NOT_EXECUTABLE, str(e)
    if not examples:
        return NOT_EXECUTABLE, "the dataset version holds no examples"

    plan = draft_plan(
        PlanInputs(
            objective=f"scheduled evaluation of suite version "
                      f"{schedule.suite_version_id}",
            suite_version_id=schedule.suite_version_id,
            dataset_version_ids=(dataset_version_id,),
            candidate_labels=tuple(c.label for c in schedule.candidates),
            baseline_id=schedule.baseline_id,
            gate_policy_version_id=schedule.gate_policy_version_id,
            budget=schedule.budget_limit, currency=schedule.budget_currency),
        unit_cost=unit_cost, sample_count=len(examples))
    problems = validate(plan)
    if problems:
        # REQ-F-10-5 is the one that matters most here and is reported as its
        # own outcome, because "we did not run because it would have cost too
        # much" and "we did not run because the schedule is wrong" call for
        # different people.
        reason = OVER_BUDGET if "exceeds the budget of" in problems else NOT_EXECUTABLE
        return reason, problems
    return None


# ---------------------------------------------------------------- the worker end
async def sweep_schedules(ctx) -> dict:
    """The cron entry point. Sweeps every tenant and enqueues what it created.

    The gateway, the evaluator registry and the example source all come from the
    worker context, so a deployment configures them once and a scheduled job
    cannot quietly invent a different endpoint or a different dataset reader.
    """
    dsn = ctx["runtime_dsn"]
    moment = ctx.get("clock", _now)()
    example_source = ctx["example_source"]
    summaries = {}
    for organization_id in known_organizations(dsn):
        with tenant_session(dsn, organization_id) as conn:
            outcome = sweep_tenant(conn, organization_id, moment=moment,
                                   example_source=example_source)
        for trigger in outcome.fired:
            await ctx["redis"].enqueue_job(
                "execute_scheduled_run", organization_id, trigger.schedule_id,
                trigger.run_id,
                _job_id=scheduled_job_id(organization_id, trigger.run_id))
        if outcome.considered:
            summaries[organization_id] = outcome.summary()
    return summaries


def scheduled_job_id(organization_id: str, run_id: str) -> str:
    """The broker's own de-duplication key, derived from the run."""
    return f"clep-scheduled-run:{organization_id}:{run_id}"


async def execute_scheduled_run(ctx, organization_id: str, schedule_id: str,
                                run_id: str) -> dict:
    """Execute a scheduled run, then observe the release if it is one.

    The execution itself is `worker.execute_run` — the same loop every other run
    goes through. A second execution path would eventually disagree with the
    first about what a run is, and the disagreement would surface as two runs
    that measured the same thing differently.
    """
    from clep.api.gate_service import GateService
    from clep.orchestration.worker import execute_run

    dsn = ctx["runtime_dsn"]
    example_source = ctx["example_source"]

    with tenant_session(dsn, organization_id) as conn:
        schedules = ScheduleRepository(conn, organization_id)
        schedule = schedules.get_schedule(schedule_id)
        if schedule is None:
            return {"skipped": "the schedule no longer exists"}
        runs = RunRepository(conn, organization_id)
        run = runs.get_run(run_id)
        if run is None:
            return {"skipped": "the run no longer exists"}
        dataset_version_id = schedules.dataset_version_for(schedule.suite_version_id)
        examples = example_source.load(conn, organization_id, dataset_version_id)
        candidates = _run_candidates(conn, organization_id, run_id)

    result = await execute_run(
        ctx, organization_id, run_id,
        [{"id": e.id, "prompt": e.prompt, "expected": e.expected,
          "content_digest": e.content_digest} for e in examples],
        candidates, budget_limit=str(schedule.budget_limit),
        budget_currency=schedule.budget_currency)

    decision = None
    if schedule.gate_policy_version_id and schedule.baseline_id:
        decision = GateService(dsn).evaluate_gate(
            organization_id=organization_id, project_id=schedule.project_id,
            candidate_run_id=run_id,
            policy_version_id=schedule.gate_policy_version_id,
            baseline_id=schedule.baseline_id, actor_id="scheduler")
    result["gateDecisionId"] = decision["id"] if decision else None
    result["evaluatedOutcome"] = decision["evaluatedOutcome"] if decision else None

    if schedule.observes_a_release:
        result["releaseObservationId"] = _observe(
            dsn, organization_id, schedule, run_id, decision, result)
    return result


def _observe(dsn, organization_id, schedule, run_id, decision, result) -> str:
    """Record what the platform advises. It does not act, and cannot."""
    if decision is None:
        kind = "investigate"
        rationale = (
            f"a {schedule.trigger_kind} evaluation ran and finished "
            f"{result.get('completeness')}, but the schedule names no baseline "
            f"and gate policy version, so no release decision was reached; "
            f"nothing here says the release is safe")
    else:
        kind = recommendation_for(decision["evaluatedOutcome"])
        rationale = (
            f"a {schedule.trigger_kind} evaluation of a live system reached "
            f"{decision['evaluatedOutcome']} in gate decision {decision['id']} "
            f"(evidence {decision['gateEvidenceDigest']}); the run finished "
            f"{result.get('completeness')}. This is advice for a person to act "
            f"on — the platform changes nothing itself")
    with tenant_session(dsn, organization_id) as conn:
        return ReleaseObservationRepository(conn, organization_id).record(
            project_id=schedule.project_id, run_id=run_id,
            trigger_kind=schedule.trigger_kind, recommendation=kind,
            rationale=rationale, observed_by="scheduler",
            gate_decision_id=decision["id"] if decision else None)


def _run_candidates(conn, organization_id: str, run_id: str) -> list[dict]:
    """The candidates the run was created with, read back from the run itself.

    Read from the run rather than from the schedule on purpose: a schedule
    amended between the sweep and the execution must not change what an already
    created run measures.
    """
    from clep.identity import ulid_to_uuid, uuid_to_ulid
    rows = conn.execute(
        "SELECT c.id, c.label, m.model_identifier, p.slug "
        "FROM clep.run_candidate c "
        "JOIN clep.model_configuration mc "
        "  ON mc.organization_id = c.organization_id "
        " AND mc.id = c.model_configuration_id "
        "JOIN clep.model m ON m.organization_id = mc.organization_id "
        " AND m.id = mc.model_id "
        "JOIN clep.provider p ON p.organization_id = m.organization_id "
        " AND p.id = m.provider_id "
        "WHERE c.organization_id = %s AND c.run_id = %s ORDER BY c.label",
        (str(organization_id), ulid_to_uuid(run_id))).fetchall()
    return [{"id": uuid_to_ulid(r[0]), "label": r[1], "model": r[2],
             "endpoint_name": r[3]} for r in rows]


def _now() -> datetime:
    return datetime.now(timezone.utc)
