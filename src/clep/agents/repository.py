"""Persistence for evaluation plans and the reasoning that produced them.

The plan's state machine lives in `planner`, which is pure and testable without
a database; this module writes what it decided and refuses what the store would
refuse. The reasoning trace is written once, with every attempt, including the
rejected ones — `REQ-F-AG-5` asks for the full history, and a history that
records only the accepted draft is a claim rather than a record.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from clep.agents.planner import EvaluationPlan, PlanError, validate
from clep.identity import actor_uuid, new_ulid, ulid_to_uuid, uuid_to_ulid


class PlanRepositoryError(RuntimeError):
    pass


class PlanSettled(PlanRepositoryError):
    """An accepted or rejected plan is the record of what was decided."""


@dataclass(frozen=True)
class PlanRow:
    id: str
    project_id: str
    state: str
    objective: str
    suite_version_id: str
    baseline_id: str | None
    gate_policy_version_id: str | None
    judge_ensemble_id: str | None
    budget_limit: Decimal | None
    budget_currency: str | None
    estimated_cost: Decimal
    content_digest: str
    accepted_by: str | None


class PlanRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    def create(self, *, project_id: str, plan: EvaluationPlan, created_by: str,
               reasoning=None) -> str:
        plan_id = new_ulid()
        inputs = plan.inputs
        self._conn.execute(
            "INSERT INTO clep.evaluation_plan (id, organization_id, project_id, "
            "state, objective, suite_version_id, baseline_id, "
            "gate_policy_version_id, judge_ensemble_id, budget_limit, "
            "budget_currency, estimated_cost, content_digest, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(plan_id), self._org, ulid_to_uuid(project_id),
             plan.state, inputs.objective, ulid_to_uuid(inputs.suite_version_id),
             _maybe(inputs.baseline_id), _maybe(inputs.gate_policy_version_id),
             _maybe(inputs.judge_ensemble_id),
             inputs.budget,
             inputs.currency if inputs.budget is not None else None,
             plan.estimated_cost, plan.digest, actor_uuid(created_by)))
        self._write_steps(plan_id, plan)
        if reasoning is not None:
            self._write_reasoning(plan_id, reasoning)
        return plan_id

    def _write_steps(self, plan_id: str, plan: EvaluationPlan) -> None:
        """Append this revision's steps under its digest.

        Nothing is replaced. An amended plan's earlier steps stay next to the
        amendment that superseded them, which is what an amendment record is
        for — and there is no DELETE grant on the table, deliberately.
        """
        for step in plan.steps:
            self._conn.execute(
                "INSERT INTO clep.plan_step (id, organization_id, "
                "evaluation_plan_id, plan_digest, step_order, kind, subject, "
                "detail, estimated_cost) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), self._org, ulid_to_uuid(plan_id), plan.digest,
                 step.order, step.kind, step.subject, step.detail or None,
                 step.estimated_cost))

    def _write_reasoning(self, plan_id: str, reasoning) -> None:
        trace_id = uuid.uuid4()
        bounds = reasoning.bounds
        self._conn.execute(
            "INSERT INTO clep.reasoning_trace (id, organization_id, "
            "evaluation_plan_id, state, max_iterations, budget, timeout_ms, "
            "cost, duration_ms, stopped_because) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (trace_id, self._org, ulid_to_uuid(plan_id), reasoning.state,
             bounds.max_iterations, bounds.budget, bounds.timeout_ms,
             reasoning.cost, reasoning.duration_ms, reasoning.stopped_because))
        for attempt in reasoning.attempts:
            self._conn.execute(
                "INSERT INTO clep.reasoning_attempt (id, organization_id, "
                "reasoning_trace_id, attempt_index, accepted, critique, cost, "
                "duration_ms, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), self._org, trace_id, attempt.index,
                 attempt.accepted, attempt.critique, attempt.cost,
                 attempt.duration_ms, attempt.error))

    def get(self, plan_id: str) -> PlanRow | None:
        row = self._conn.execute(
            "SELECT id, project_id, state, objective, suite_version_id, "
            "baseline_id, gate_policy_version_id, judge_ensemble_id, "
            "budget_limit, budget_currency, estimated_cost, content_digest, "
            "accepted_by FROM clep.evaluation_plan "
            "WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(plan_id))).fetchone()
        if row is None:
            return None
        return PlanRow(
            id=uuid_to_ulid(row[0]), project_id=uuid_to_ulid(row[1]),
            state=row[2], objective=row[3],
            suite_version_id=uuid_to_ulid(row[4]),
            baseline_id=uuid_to_ulid(row[5]) if row[5] else None,
            gate_policy_version_id=uuid_to_ulid(row[6]) if row[6] else None,
            judge_ensemble_id=uuid_to_ulid(row[7]) if row[7] else None,
            budget_limit=row[8], budget_currency=row[9], estimated_cost=row[10],
            content_digest=row[11],
            accepted_by=str(row[12]) if row[12] else None)

    def steps(self, plan_id: str) -> list[dict]:
        """The current revision's steps: the ones under the plan's own digest."""
        rows = self._conn.execute(
            "SELECT s.step_order, s.kind, s.subject, s.detail, s.estimated_cost "
            "FROM clep.plan_step s "
            "JOIN clep.evaluation_plan p "
            "  ON p.organization_id = s.organization_id "
            " AND p.id = s.evaluation_plan_id "
            " AND p.content_digest = s.plan_digest "
            "WHERE s.organization_id = %s AND s.evaluation_plan_id = %s "
            "ORDER BY s.step_order",
            (self._org, ulid_to_uuid(plan_id))).fetchall()
        return [{"order": r[0], "kind": r[1], "subject": r[2],
                 "detail": r[3] or "", "estimatedCost": str(r[4])} for r in rows]

    def amendments(self, plan_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT actor_id, note, prior_digest, amended_at "
            "FROM clep.plan_amendment WHERE organization_id = %s "
            "AND evaluation_plan_id = %s ORDER BY amended_at, id",
            (self._org, ulid_to_uuid(plan_id))).fetchall()
        return [{"actor": str(r[0]), "note": r[1], "priorDigest": r[2]}
                for r in rows]

    def reasoning(self, plan_id: str) -> dict | None:
        trace = self._conn.execute(
            "SELECT id, state, max_iterations, budget, timeout_ms, cost, "
            "duration_ms, stopped_because FROM clep.reasoning_trace "
            "WHERE organization_id = %s AND evaluation_plan_id = %s",
            (self._org, ulid_to_uuid(plan_id))).fetchone()
        if trace is None:
            return None
        attempts = self._conn.execute(
            "SELECT attempt_index, accepted, critique, cost, duration_ms, error "
            "FROM clep.reasoning_attempt WHERE organization_id = %s "
            "AND reasoning_trace_id = %s ORDER BY attempt_index",
            (self._org, trace[0])).fetchall()
        return {"state": trace[1], "maxIterations": trace[2],
                "budget": str(trace[3]), "timeoutMs": trace[4],
                "cost": str(trace[5]), "durationMs": trace[6],
                "stoppedBecause": trace[7],
                "attempts": [{"index": a[0], "accepted": a[1], "critique": a[2],
                              "cost": str(a[3]), "durationMs": a[4],
                              "error": a[5]} for a in attempts]}

    # ------------------------------------------------------------- transitions
    def amend(self, plan_id: str, *, plan: EvaluationPlan, actor_id: str,
              note: str) -> PlanRow:
        """Write an amended draft, keeping the digest it replaced.

        The prior digest is what makes the amendment reviewable: without it the
        record says something changed and not what it changed from.
        """
        current = self.get(plan_id)
        if current is None:
            return None
        if current.state != "draft":
            raise PlanSettled(
                f"evaluation plan {plan_id} is {current.state}; amending it "
                f"would change the record of what was approved")
        self._conn.execute(
            "INSERT INTO clep.plan_amendment (id, organization_id, "
            "evaluation_plan_id, actor_id, note, prior_digest) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (uuid.uuid4(), self._org, ulid_to_uuid(plan_id),
             actor_uuid(actor_id), note, current.content_digest))
        if plan.digest != current.content_digest:
            self._write_steps(plan_id, plan)
        self._conn.execute(
            "UPDATE clep.evaluation_plan SET content_digest = %s, "
            "estimated_cost = %s, objective = %s "
            "WHERE organization_id = %s AND id = %s",
            (plan.digest, plan.estimated_cost, plan.inputs.objective, self._org,
             ulid_to_uuid(plan_id)))
        return self.get(plan_id)

    def accept(self, plan_id: str, *, plan: EvaluationPlan,
               actor_id: str) -> PlanRow:
        current = self.get(plan_id)
        if current is None:
            return None
        if current.state != "draft":
            raise PlanSettled(f"evaluation plan {plan_id} is already "
                              f"{current.state}")
        problems = validate(plan)
        if problems:
            raise PlanError(f"this plan does not validate and cannot be "
                            f"accepted: {problems}")
        if plan.digest != current.content_digest:
            raise PlanError(
                "the plan being accepted is not the plan that is stored; the "
                "digest recorded at acceptance must be the one that was "
                "reviewed")
        self._conn.execute(
            "UPDATE clep.evaluation_plan SET state = 'accepted', "
            "accepted_by = %s, accepted_at = now() "
            "WHERE organization_id = %s AND id = %s",
            (actor_uuid(actor_id), self._org, ulid_to_uuid(plan_id)))
        return self.get(plan_id)


def _maybe(value):
    return ulid_to_uuid(value) if value else None
