"""Application service for standing orders and release observations.

Same rules as the other services: a tenant-bound session per call, the
organization from the ingress principal, responses in the contract's vocabulary,
and every governance write audited inside the same transaction as the thing it
records.

The rule specific to this service is what it will not do. `recordReleaseObservation`
derives its recommendation from a gate decision that already exists; it never
decides quality itself, and there is no path here that changes a production
system (`REQ-F-10-3`). An observation with no decision behind it records
`investigate` and says so — the honest answer for "we looked and reached no
verdict" is not "nothing needs doing".
"""
from __future__ import annotations

from decimal import Decimal

from clep.api import audit
from clep.db.session import tenant_session
from clep.orchestration.releases import (ReleaseObservationRepository,
                                         recommendation_for)
from clep.orchestration.schedules import (CadenceError, ScheduleError,
                                          ScheduleRepository, parse_cadence)
from clep.regression.repository import RegressionRepository


class ScheduleService:
    def __init__(self, runtime_dsn: str):
        self._dsn = runtime_dsn

    # ------------------------------------------------------------- schedules
    def create_schedule(self, *, organization_id: str, project_id: str,
                        suite_version_id: str, cadence: str, budget: tuple,
                        candidates: list[dict], actor_id: str,
                        trigger: str = "schedule",
                        gate_policy_version_id: str | None = None,
                        baseline_id: str | None = None) -> dict:
        limit, currency = budget
        with tenant_session(self._dsn, organization_id) as conn:
            repo = ScheduleRepository(conn, organization_id)
            schedule_id = repo.create_schedule(
                project_id=project_id, suite_version_id=suite_version_id,
                cadence=cadence, budget_limit=Decimal(str(limit)),
                budget_currency=currency, created_by=actor_id,
                candidates=candidates, trigger_kind=trigger,
                gate_policy_version_id=gate_policy_version_id,
                baseline_id=baseline_id)
            audit.record(conn, organization_id, actor_id,
                         "evaluation_schedule.created", "evaluation_schedule",
                         schedule_id)
            return _present_schedule(repo.get_schedule(schedule_id))

    def pause_schedule(self, *, organization_id: str, schedule_id: str,
                       actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = ScheduleRepository(conn, organization_id)
            if repo.get_schedule(schedule_id) is None:
                return None
            if repo.pause_schedule(schedule_id):
                audit.record(conn, organization_id, actor_id,
                             "evaluation_schedule.paused", "evaluation_schedule",
                             schedule_id)
            return _present_schedule(repo.get_schedule(schedule_id))

    def get_schedule(self, organization_id: str, schedule_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            row = ScheduleRepository(conn, organization_id).get_schedule(schedule_id)
            return _present_schedule(row) if row else None

    # --------------------------------------------------- release observations
    def record_observation(self, *, organization_id: str, project_id: str,
                           run_id: str, trigger: str, actor_id: str,
                           gate_decision_id: str | None = None) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            run = conn.execute(
                "SELECT completeness FROM clep.run WHERE organization_id = %s "
                "AND id = %s", (organization_id, _uuid(run_id))).fetchone()
            if run is None:
                return None
            decision = None
            if gate_decision_id:
                decision = RegressionRepository(
                    conn, organization_id).get_decision(gate_decision_id)
                if decision is None:
                    return None
            kind, rationale = _advice(trigger, decision, run[0])
            repo = ReleaseObservationRepository(conn, organization_id)
            observation_id = repo.record(
                project_id=project_id, run_id=run_id, trigger_kind=trigger,
                recommendation=kind, rationale=rationale, observed_by=actor_id,
                gate_decision_id=gate_decision_id)
            audit.record(conn, organization_id, actor_id,
                         "release_observation.recorded", "release_observation",
                         observation_id)
            return _present_observation(repo.get(observation_id))

    def get_observation(self, organization_id: str,
                        observation_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            row = ReleaseObservationRepository(conn, organization_id).get(
                observation_id)
            return _present_observation(row) if row else None


def _advice(trigger: str, decision, completeness) -> tuple[str, str]:
    if decision is None:
        return ("investigate",
                f"a {trigger} evaluation of a live system was recorded with no "
                f"gate decision behind it; the run finished {completeness}, and "
                f"nothing here says the release is safe")
    outcome = decision["evaluatedOutcome"]
    return (recommendation_for(outcome),
            f"a {trigger} evaluation of a live system reached {outcome} in gate "
            f"decision {decision['id']}; the run finished {completeness}. This "
            f"is advice for a person to act on — the platform changes nothing "
            f"itself")


def _present_schedule(row) -> dict:
    body = {"id": row.id, "suiteVersionId": row.suite_version_id,
            "cadence": row.cadence, "trigger": row.trigger_kind,
            "budget": {"limit": str(row.budget_limit),
                       "currency": row.budget_currency},
            "state": row.state,
            "gatePolicyVersionId": row.gate_policy_version_id,
            "baselineId": row.baseline_id, "lastRunId": row.last_run_id}
    return body


def _present_observation(row) -> dict:
    return {"id": row.id, "trigger": row.trigger_kind, "runId": row.run_id,
            "gateDecisionId": row.gate_decision_id,
            "recommendation": {"kind": row.recommendation,
                               "rationale": row.rationale,
                               "gateDecisionId": row.gate_decision_id},
            "observedAt": row.observed_at.isoformat() if row.observed_at else None}


def _uuid(value: str):
    from clep.identity import ulid_to_uuid
    return ulid_to_uuid(value)


__all__ = ["ScheduleService", "CadenceError", "ScheduleError", "parse_cadence"]
