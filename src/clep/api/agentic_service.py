"""Application service for judges, plans, escalations and memory.

Same rules as the other services: a tenant-bound session per call, the
organization from the ingress principal, responses in the contract's vocabulary,
and every governance write audited inside the same transaction as the thing it
records — through the one writer in `audit`, never a second copy of the insert.

The rule specific to this service: reasoning happens before persistence, never
during it. The planner runs in memory, bounded, and what is written is the plan
it settled on together with the full trace of how it got there. A drafting loop
that wrote as it went would leave rejected drafts in the store as though they
were candidates for execution.
"""
from __future__ import annotations

from decimal import Decimal

from clep.agents.planner import (EvaluationPlan, PlanError, PlanInputs, accept,
                                 amend, draft_plan, validate)
from clep.agents.repository import PlanRepository, PlanSettled
from clep.agents.sdk import Bounds
from clep.api import audit
from clep.db.session import tenant_session
from clep.judges.repository import (EscalationAlreadyReviewed,
                                    JudgeRepository, JudgeRepositoryError,
                                    JudgeVersionFrozen,
                                    JudgeVersionNotPublished)
from clep.memory.repository import MemoryRepository


class AgenticService:
    def __init__(self, runtime_dsn: str):
        self._dsn = runtime_dsn

    # ----------------------------------------------------------------- judges
    def create_judge(self, *, organization_id: str, project_id: str, slug: str,
                     display_name: str, actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = JudgeRepository(conn, organization_id)
            judge_id = repo.create_judge(project_id=project_id, slug=slug,
                                         display_name=display_name)
            audit.record(conn, organization_id, actor_id, "judge.created",
                         "judge", judge_id)
            return {"id": judge_id, "slug": slug, "displayName": display_name,
                    "scope": "custom"}

    def add_judge_version(self, *, organization_id: str, judge_id: str,
                          rubric: str, model_configuration_id: str,
                          actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = JudgeRepository(conn, organization_id)
            version = repo.add_version(
                judge_id=judge_id, rubric=rubric,
                model_configuration_id=model_configuration_id,
                created_by=actor_id)
            audit.record(conn, organization_id, actor_id, "judge_version.created",
                         "judge_version", version.id)
            return _present_version(version)

    def publish_judge_version(self, *, organization_id: str, version_id: str,
                              actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = JudgeRepository(conn, organization_id)
            if repo.get_version(version_id) is None:
                return None
            version = repo.publish_version(version_id)
            audit.record(conn, organization_id, actor_id,
                         "judge_version.published", "judge_version", version.id)
            return _present_version(version)

    def create_ensemble(self, *, organization_id: str, project_id: str,
                        slug: str, judge_version_ids, agreement_threshold,
                        minimum_scoring_votes, actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = JudgeRepository(conn, organization_id)
            ensemble = repo.create_ensemble(
                project_id=project_id, slug=slug,
                judge_version_ids=judge_version_ids,
                agreement_threshold=agreement_threshold,
                minimum_scoring_votes=minimum_scoring_votes,
                created_by=actor_id)
            audit.record(conn, organization_id, actor_id, "judge_ensemble.created",
                         "judge_ensemble", ensemble.id)
            return _present_ensemble(ensemble)

    def get_ensemble(self, *, organization_id: str, ensemble_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            ensemble = JudgeRepository(conn, organization_id).get_ensemble(
                ensemble_id)
            return _present_ensemble(ensemble) if ensemble else None

    # ------------------------------------------------------------------ plans
    def create_plan(self, *, organization_id: str, project_id: str, inputs:
                    PlanInputs, actor_id: str, bounds: Bounds) -> dict:
        """Draft, critique, redraft — then write whatever it settled on.

        A plan that did not validate is stored as a draft with its reasoning
        trace, not rejected outright: `REQ-F-AG-1` makes the plan reviewable,
        and a person amending a failed draft is the intended path.
        """
        from clep.agents.planner import plan_with_reflection
        reasoning = plan_with_reflection(inputs, bounds)
        plan = reasoning.value or draft_plan(inputs)
        with tenant_session(self._dsn, organization_id) as conn:
            repo = PlanRepository(conn, organization_id)
            plan_id = repo.create(project_id=project_id, plan=plan,
                                  created_by=actor_id, reasoning=reasoning)
            audit.record(conn, organization_id, actor_id,
                         "evaluation_plan.drafted", "evaluation_plan", plan_id)
            return self._present_plan(repo, plan_id)

    def get_plan(self, *, organization_id: str, plan_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = PlanRepository(conn, organization_id)
            if repo.get(plan_id) is None:
                return None
            return self._present_plan(repo, plan_id)

    def amend_plan(self, *, organization_id: str, plan_id: str, note: str,
                   actor_id: str, steps=None) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = PlanRepository(conn, organization_id)
            stored = repo.get(plan_id)
            if stored is None:
                return None
            plan = _rehydrate(repo, stored)
            amended = amend(plan, note=note, actor=actor_id,
                            steps=steps if steps is not None else plan.steps)
            repo.amend(plan_id, plan=amended, actor_id=actor_id, note=note)
            audit.record(conn, organization_id, actor_id,
                         "evaluation_plan.amended", "evaluation_plan", plan_id)
            return self._present_plan(repo, plan_id)

    def accept_plan(self, *, organization_id: str, plan_id: str,
                    justification: str, actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = PlanRepository(conn, organization_id)
            stored = repo.get(plan_id)
            if stored is None:
                return None
            plan = _rehydrate(repo, stored)
            accept(plan, actor_id)          # refuses one that does not validate
            repo.accept(plan_id, plan=plan, actor_id=actor_id)
            audit.record(conn, organization_id, actor_id,
                         "evaluation_plan.accepted", "evaluation_plan", plan_id)
            return self._present_plan(repo, plan_id)

    def _present_plan(self, repo: PlanRepository, plan_id: str) -> dict:
        row = repo.get(plan_id)
        return {"id": row.id, "state": row.state, "objective": row.objective,
                "suiteVersionId": row.suite_version_id,
                "baselineId": row.baseline_id,
                "gatePolicyVersionId": row.gate_policy_version_id,
                "judgeEnsembleId": row.judge_ensemble_id,
                "estimatedCost": str(row.estimated_cost),
                "steps": repo.steps(plan_id),
                "amendments": repo.amendments(plan_id),
                "reasoning": repo.reasoning(plan_id),
                "acceptedBy": row.accepted_by,
                "digest": row.content_digest}

    # ------------------------------------------------------------ escalations
    def list_escalations(self, *, organization_id: str, project_id: str,
                         state: str | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            items = JudgeRepository(conn, organization_id).list_escalations(
                project_id, state)
            return {"items": items, "nextCursor": None}

    def review_escalation(self, *, organization_id: str, escalation_id: str,
                          outcome: str, justification: str,
                          actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = JudgeRepository(conn, organization_id)
            reviewed = repo.review_escalation(
                escalation_id, actor_id=actor_id, outcome=outcome,
                justification=justification)
            if reviewed is None:
                return None
            audit.record(conn, organization_id, actor_id, "escalation.reviewed",
                         "escalation", escalation_id)
            return reviewed

    # ----------------------------------------------------------------- memory
    def evaluation_memory(self, *, organization_id: str, project_id: str,
                          window_days: int | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            memory = MemoryRepository(conn, organization_id).evaluation_memory(
                project_id, window_days=window_days)
        return {
            "windowDays": memory.window_days,
            "gateDecisions": memory.gate_decisions,
            "regressions": memory.regressions,
            "escalations": memory.escalations,
            "judgeCalibration": [
                {"judgeVersionId": c.judge_version_id,
                 "judgements": c.judgements,
                 "meanDeviation": _decimal(c.mean_deviation),
                 "deviationSpread": _decimal(c.deviation_spread),
                 "abstentionRate": _decimal(c.abstention_rate),
                 "failureRate": _decimal(c.failure_rate)}
                for c in memory.judge_calibration],
            "recurringFailures": [
                {"signature": f.signature, "occurrences": f.occurrences,
                 "firstSeen": f.first_seen, "lastSeen": f.last_seen}
                for f in memory.recurring_failures],
            "evaluatorInstability": [
                {"evaluatorVersionId": e.evaluator_version_id, "runs": e.runs,
                 "failureRate": _decimal(e.failure_rate), "unstable": e.unstable}
                for e in memory.evaluator_instability],
            "retentionFloorDays": memory.retention_floor_days,
        }


def _rehydrate(repo: PlanRepository, row) -> EvaluationPlan:
    """Rebuild the typed plan from what was stored.

    The stored form is the source of truth, so an amendment or an acceptance
    acts on what is actually there rather than on what the caller believes is
    there.
    """
    from clep.agents.planner import PlanStep
    steps = tuple(PlanStep(order=s["order"], kind=s["kind"], subject=s["subject"],
                           detail=s["detail"],
                           estimated_cost=Decimal(s["estimatedCost"]))
                  for s in repo.steps(row.id))
    candidates = tuple(s.subject for s in steps if s.kind == "score_candidate")
    evaluators = tuple(s.subject for s in steps if s.kind == "run_evaluator")
    ensembles = tuple(s.subject.split(",") for s in steps
                      if s.kind == "run_ensemble")
    inputs = PlanInputs(
        objective=row.objective, suite_version_id=row.suite_version_id,
        dataset_version_ids=(), candidate_labels=candidates or ("unknown",),
        evaluator_version_keys=evaluators,
        ensemble_judge_keys=tuple(ensembles[0]) if ensembles else (),
        baseline_id=row.baseline_id,
        gate_policy_version_id=row.gate_policy_version_id,
        judge_ensemble_id=row.judge_ensemble_id,
        budget=row.budget_limit, currency=row.budget_currency or "USD")
    return EvaluationPlan(inputs=inputs, steps=steps, state=row.state,
                          accepted_by=row.accepted_by)


def _present_version(version) -> dict:
    return {"id": version.id, "judgeId": version.judge_definition_id,
            "versionNumber": version.version_number, "state": version.state,
            "modelConfigurationId": version.model_configuration_id,
            "rubricDigest": version.rubric_digest,
            "contentDigest": version.content_digest}


def _present_ensemble(ensemble) -> dict:
    return {"id": ensemble.id, "slug": ensemble.slug,
            "judgeVersionIds": list(ensemble.judge_version_ids),
            "agreementThreshold": _decimal(ensemble.agreement_threshold),
            "minimumScoringVotes": ensemble.minimum_scoring_votes}


def _decimal(value) -> str | None:
    return None if value is None else str(value)
