"""HTTP surface for the operations the phases so far own.

Runs and their identity (Phase 5, 6), the prompt registry and experiments
(Phase 6), and baselines, gate policies and gate decisions (Phase 7). The rest of
the contract belongs to phases that have not run — dataset management, erasure
and the audit surfaces — and is deliberately absent rather than stubbed. A stub
that returns 501 is still a route a client can find; a route that does not exist
is an honest 404.

Two rules the contract states and this module enforces:

  * Tenant context comes from the authenticated principal at ingress and is set
    once (ADR-010 rule 3). It is never read from a path, query or body, so a
    caller cannot ask for another tenant's data by asking politely.
  * A quality failure is never a Problem. `Problem.category` has three values and
    none of them is a quality outcome: a run that scored badly is a successful
    response describing a bad score. Only the platform failing produces a 503.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from clep.agents.planner import PlanError, PlanInputs
from clep.agents.repository import PlanSettled
from clep.agents.sdk import Bounds
from clep.api import contract
from clep.identity import is_ulid, new_ulid
from clep.judges.consensus import ConsensusError
from clep.judges.repository import (EscalationAlreadyReviewed,
                                    JudgeRepositoryError)
from clep.orchestration.releases import ReleaseObservationError
from clep.orchestration.schedules import CadenceError, ScheduleError
from clep.regression.repository import PolicyNotPublished

PROBLEM_TYPE = "https://clep.invalid/problems/"


class Problem(BaseModel):
    """RFC 9457, with the contract's category extension."""
    type: str
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None
    category: str
    correlationId: str | None = None


def problem_categories() -> list[str]:
    """Read from the contract, not restated here. Three values, and none of them
    is a quality outcome."""
    return list(contract.schema("Problem")["properties"]["category"]["enum"])


def problem(status: int, title: str, category: str, detail: str,
            correlation_id: str | None = None) -> JSONResponse:
    if category not in problem_categories():
        raise ValueError(
            f"{category!r} is not a Problem category the contract declares "
            f"({problem_categories()}); a quality failure is never a Problem")
    body = Problem(type=PROBLEM_TYPE + title.lower().replace(" ", "-"), title=title,
                   status=status, detail=detail, category=category,
                   correlationId=correlation_id)
    return JSONResponse(status_code=status, content=body.model_dump(exclude_none=True),
                        media_type="application/problem+json")


class CandidateSpecIn(BaseModel):
    label: str | None = None
    modelConfigurationId: str
    promptVersionId: str | None = None


class BudgetIn(BaseModel):
    limit: str
    currency: str = Field(min_length=3, max_length=3)


class RunRequestIn(BaseModel):
    suiteVersionId: str
    candidates: list[CandidateSpecIn] = Field(min_length=1)
    budget: BudgetIn | None = None
    integrationTier: str | None = None
    evaluationPlanId: str | None = None
    judgeEnsembleId: str | None = None


class JudgeIn(BaseModel):
    slug: str = Field(min_length=1)
    displayName: str = Field(min_length=1)


class JudgeVersionIn(BaseModel):
    rubric: str = Field(min_length=1)
    modelConfigurationId: str


class JudgeEnsembleIn(BaseModel):
    slug: str = Field(min_length=1)
    judgeVersionIds: list[str] = Field(min_length=2)
    agreementThreshold: str | None = None
    minimumScoringVotes: int | None = Field(default=None, ge=2)


class EvaluationPlanIn(BaseModel):
    objective: str = Field(min_length=1)
    suiteVersionId: str
    candidates: list[CandidateSpecIn] = Field(min_length=1)
    baselineId: str | None = None
    gatePolicyVersionId: str | None = None
    judgeEnsembleId: str | None = None
    budget: BudgetIn | None = None
    integrationTier: str | None = None


class PlanAmendmentIn(BaseModel):
    note: str = Field(min_length=1)


class PlanAcceptanceIn(BaseModel):
    justification: str = Field(min_length=1)


class EscalationReviewIn(BaseModel):
    outcome: str = Field(min_length=1)
    justification: str = Field(min_length=1)


class TenantPrincipal(BaseModel):
    organization_id: str
    subject: str


def principal_from_authorization(
    authorization: str | None = Header(default=None),
) -> TenantPrincipal:
    """Establish tenant context at the boundary, once.

    A bearer token is required by the contract's security scheme. Token issuance
    and verification are a Phase 12 concern; what Phase 5 fixes now is the
    *shape* — the organization is derived from the credential and from nothing
    the caller can vary per request.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="bearer credential required")
    token = authorization.split(" ", 1)[1].strip()
    org, _, subject = token.partition(":")
    try:
        uuid.UUID(org)
    except ValueError:
        raise HTTPException(status_code=401, detail="credential is not bound to a tenant")
    return TenantPrincipal(organization_id=org, subject=subject or "unknown")


class PromptIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    displayName: str = Field(min_length=1)


class PromptVersionIn(BaseModel):
    body: str = Field(min_length=1)


class ExperimentIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    displayName: str = Field(min_length=1)
    hypothesis: str | None = None


class BaselineIn(BaseModel):
    runId: str
    label: str | None = None


class GatePolicyIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    displayName: str = Field(min_length=1)


class GateCriterionIn(BaseModel):
    metricKey: str = Field(min_length=1)
    dimension: str
    source: str
    direction: str
    onRegression: str
    onInsufficientEvidence: str
    onNotComparable: str
    precisionThreshold: str | None = None
    minimumSampleSize: int | None = Field(default=None, ge=1)
    absoluteFloor: str | None = None
    relativeTolerance: str | None = None


class GatePolicyVersionIn(BaseModel):
    confidenceLevel: float = Field(gt=0, lt=1)
    resampleCount: int = Field(ge=1)
    bootstrapSeed: int
    criteria: list[GateCriterionIn] = Field(min_length=1)


class GateEvaluationIn(BaseModel):
    candidateRunId: str
    baselineId: str | None = None
    gatePolicyVersionId: str


class EvaluationScheduleIn(BaseModel):
    suiteVersionId: str
    cadence: str = Field(min_length=1)
    budget: BudgetIn
    candidates: list[CandidateSpecIn] = Field(min_length=1)
    trigger: str | None = None
    gatePolicyVersionId: str | None = None
    baselineId: str | None = None


class ReleaseObservationIn(BaseModel):
    trigger: str
    runId: str
    gateDecisionId: str | None = None


class PolicyExceptionIn(BaseModel):
    justification: str = Field(min_length=20)
    expiresAt: datetime


#: The bounds the planner runs under when the API drafts a plan. Stated here,
#: at the only place that constructs them, rather than defaulted inside the
#: harness: `Bounds` has no defaults on purpose, and this is the caller that
#: has to choose. Small on purpose — drafting is cheap and a planner that
#: iterates twenty times has not understood the objective.
PLANNING_BOUNDS = Bounds(max_iterations=4, budget=Decimal("0.50"),
                         timeout_ms=30_000)


def create_app(run_service, registry_service=None, gate_service=None,
               agentic_service=None, schedule_service=None) -> FastAPI:
    """`run_service` supplies persistence and execution.

    Injected rather than imported so the contract tests can drive every path
    without a database, and so nothing in this module can reach the store
    directly and forget the tenant context on the way.
    """
    app = FastAPI(title=contract.load()["info"]["title"],
                  version=contract.load()["info"]["version"],
                  openapi_url=None)

    # Fail at import time if any route drifts from the contract.
    for method, path in (("POST", "/projects/{projectId}/runs"),
                         ("GET", "/runs/{runId}"),
                         ("POST", "/runs/{runId}/cancel"),
                         ("GET", "/runs/{runId}/samples"),
                         ("GET", "/runs/{runId}/identity"),
                         ("POST", "/runs/{runId}/reproductions"),
                         ("POST", "/projects/{projectId}/prompts"),
                         ("POST", "/prompts/{promptId}/versions"),
                         ("GET", "/prompt-versions/{promptVersionId}"),
                         ("POST", "/prompt-versions/{promptVersionId}/publish"),
                         ("POST", "/projects/{projectId}/experiments"),
                         ("POST", "/projects/{projectId}/baselines"),
                         ("POST", "/baselines/{baselineId}/approval"),
                         ("POST", "/projects/{projectId}/gate-policies"),
                         ("POST", "/gate-policies/{gatePolicyId}/versions"),
                         ("POST", "/gate-policy-versions/{gatePolicyVersionId}/publish"),
                         ("POST", "/projects/{projectId}/gate-evaluations"),
                         ("GET", "/gate-decisions/{gateDecisionId}"),
                         ("POST", "/gate-decisions/{gateDecisionId}/exceptions"),
                         ("POST", "/projects/{projectId}/judges"),
                         ("POST", "/judges/{judgeId}/versions"),
                         ("POST", "/judge-versions/{judgeVersionId}/publish"),
                         ("POST", "/projects/{projectId}/judge-ensembles"),
                         ("GET", "/judge-ensembles/{judgeEnsembleId}"),
                         ("POST", "/projects/{projectId}/evaluation-plans"),
                         ("GET", "/evaluation-plans/{evaluationPlanId}"),
                         ("POST", "/evaluation-plans/{evaluationPlanId}/amendments"),
                         ("POST", "/evaluation-plans/{evaluationPlanId}/acceptance"),
                         ("GET", "/projects/{projectId}/escalations"),
                         ("POST", "/escalations/{escalationId}/review"),
                         ("GET", "/projects/{projectId}/evaluation-memory"),
                         ("POST", "/projects/{projectId}/evaluation-schedules"),
                         ("POST", "/evaluation-schedules/{scheduleId}/pause"),
                         ("POST", "/projects/{projectId}/release-observations")):
        contract.operation_for(method, path)

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException):
        category = "authorization" if exc.status_code in (401, 403) else "client_error"
        if exc.status_code >= 500:
            category = "platform_failure"
        return problem(exc.status_code, exc.detail or "request failed", category,
                       exc.detail or "", request.headers.get("x-correlation-id"))

    @app.post("/projects/{projectId}/runs", status_code=202)
    def create_run(body: RunRequestIn, projectId: str = Path(...),
                   principal: TenantPrincipal = Depends(principal_from_authorization),
                   idempotency_key: str | None = Header(default=None,
                                                        alias="Idempotency-Key")):
        _require_ulid(projectId, "projectId")
        _require_ulid(body.suiteVersionId, "suiteVersionId")
        if not idempotency_key:
            # REQ-N-REL-2 begins at submission. Without a caller-supplied key a
            # retried POST is a second run, and the platform cannot tell.
            raise HTTPException(status_code=400,
                                detail="Idempotency-Key header is required")
        tier = body.integrationTier or "output_only"
        if tier not in contract.enum_of("IntegrationTier"):
            raise HTTPException(status_code=400,
                                detail=f"integrationTier must be one of "
                                       f"{contract.enum_of('IntegrationTier')}")
        budget = None
        if body.budget:
            budget = (Decimal(body.budget.limit), body.budget.currency)
        run = run_service.create_run(
            organization_id=principal.organization_id, project_id=projectId,
            suite_version_id=body.suiteVersionId,
            candidates=[c.model_dump() for c in body.candidates],
            integration_tier=tier, budget=budget, idempotency_key=idempotency_key)
        return run

    @app.get("/runs/{runId}")
    def get_run(runId: str = Path(...),
                principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(runId, "runId")
        run = run_service.get_run(principal.organization_id, runId)
        if run is None:
            # Indistinguishable from another tenant's run, on purpose: a 404 that
            # differs from a 403 tells an attacker which identifiers exist.
            raise HTTPException(status_code=404, detail="no such run")
        return run

    @app.post("/runs/{runId}/cancel", status_code=202)
    def cancel_run(runId: str = Path(...),
                   principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(runId, "runId")
        cancelled = run_service.cancel_run(principal.organization_id, runId)
        if cancelled is None:
            raise HTTPException(status_code=404, detail="no such run")
        return cancelled

    @app.get("/runs/{runId}/samples")
    def list_run_samples(runId: str = Path(...), limit: int = Query(50, ge=1, le=200),
                         offset: int = Query(0, ge=0),
                         principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(runId, "runId")
        page = run_service.list_samples(principal.organization_id, runId, limit, offset)
        if page is None:
            raise HTTPException(status_code=404, detail="no such run")
        return page

    @app.get("/runs/{runId}/identity")
    def get_run_identity(runId: str = Path(...),
                         principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(runId, "runId")
        identity = run_service.run_identity(principal.organization_id, runId)
        if identity is None:
            raise HTTPException(status_code=404, detail="no such run")
        return identity

    # ---- registry and experiments -------------------------------------------
    # Registered only when the service is supplied. A route that exists and
    # cannot work is worse than one that does not exist: the first is a 500 a
    # client discovers in production, the second is a 404 it discovers at once.
    if registry_service is None:
        return app

    @app.post("/projects/{projectId}/prompts", status_code=201)
    def create_prompt(body: PromptIn, projectId: str = Path(...),
                      principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        return registry_service.create_prompt(
            organization_id=principal.organization_id, project_id=projectId,
            slug=body.slug, display_name=body.displayName,
            actor_id=principal.subject)

    @app.post("/prompts/{promptId}/versions", status_code=201)
    def add_prompt_version(body: PromptVersionIn, promptId: str = Path(...),
                           principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(promptId, "promptId")
        version = registry_service.add_prompt_version(
            organization_id=principal.organization_id, prompt_id=promptId,
            body=body.body, actor_id=principal.subject)
        if version is None:
            raise HTTPException(status_code=404, detail="no such prompt")
        return version

    @app.get("/prompt-versions/{promptVersionId}")
    def get_prompt_version(promptVersionId: str = Path(...),
                           principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(promptVersionId, "promptVersionId")
        version = registry_service.get_prompt_version(principal.organization_id,
                                                      promptVersionId)
        if version is None:
            raise HTTPException(status_code=404, detail="no such prompt version")
        return version

    @app.post("/prompt-versions/{promptVersionId}/publish")
    def publish_prompt_version(promptVersionId: str = Path(...),
                               principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(promptVersionId, "promptVersionId")
        version = registry_service.publish_prompt_version(
            organization_id=principal.organization_id,
            version_id=promptVersionId, actor_id=principal.subject)
        if version is None:
            raise HTTPException(status_code=404, detail="no such prompt version")
        return version

    @app.post("/projects/{projectId}/experiments", status_code=201)
    def create_experiment(body: ExperimentIn, projectId: str = Path(...),
                          principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        return registry_service.create_experiment(
            organization_id=principal.organization_id, project_id=projectId,
            slug=body.slug, display_name=body.displayName,
            hypothesis=body.hypothesis, actor_id=principal.subject)

    @app.post("/runs/{runId}/reproductions", status_code=201)
    def reproduce_run(runId: str = Path(...),
                      principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(runId, "runId")
        attempt = registry_service.reproduce_run(
            organization_id=principal.organization_id, run_id=runId,
            actor_id=principal.subject)
        if attempt is None:
            raise HTTPException(status_code=404, detail="no such run")
        return attempt

    # ---- baselines and quality gates ----------------------------------------
    # Registered on the same rule as the registry routes above.
    if gate_service is None:
        return app

    @app.post("/projects/{projectId}/baselines", status_code=201)
    def create_baseline(body: BaselineIn, projectId: str = Path(...),
                        principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        _require_ulid(body.runId, "runId")
        baseline = gate_service.create_baseline(
            organization_id=principal.organization_id, project_id=projectId,
            run_id=body.runId, label=body.label, actor_id=principal.subject)
        if baseline is None:
            # The run does not exist, or has not finished. Both are the caller
            # naming something that cannot be a baseline, and neither is a
            # platform failure.
            raise HTTPException(
                status_code=422,
                detail="the run does not exist or has not completed")
        return baseline

    @app.post("/baselines/{baselineId}/approval")
    def approve_baseline(baselineId: str = Path(...),
                         principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(baselineId, "baselineId")
        baseline = gate_service.approve_baseline(
            organization_id=principal.organization_id, baseline_id=baselineId,
            actor_id=principal.subject)
        if baseline is None:
            raise HTTPException(status_code=404, detail="no such baseline")
        return baseline

    @app.post("/projects/{projectId}/gate-policies", status_code=201)
    def create_gate_policy(body: GatePolicyIn, projectId: str = Path(...),
                           principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        return gate_service.create_gate_policy(
            organization_id=principal.organization_id, project_id=projectId,
            slug=body.slug, display_name=body.displayName,
            actor_id=principal.subject)

    @app.post("/gate-policies/{gatePolicyId}/versions", status_code=201)
    def add_gate_policy_version(body: GatePolicyVersionIn,
                                gatePolicyId: str = Path(...),
                                principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(gatePolicyId, "gatePolicyId")
        for criterion in body.criteria:
            _require_enum(criterion.dimension, "CriterionDimension", "dimension")
            _require_enum(criterion.source, "CriterionSource", "source")
            _require_enum(criterion.direction, "MetricDirection", "direction")
            for field, value in (("onRegression", criterion.onRegression),
                                 ("onInsufficientEvidence",
                                  criterion.onInsufficientEvidence),
                                 ("onNotComparable", criterion.onNotComparable)):
                _require_enum(value, "CriterionAction", field)
        version = gate_service.add_policy_version(
            organization_id=principal.organization_id, policy_id=gatePolicyId,
            confidence_level=Decimal(str(body.confidenceLevel)),
            resample_count=body.resampleCount, bootstrap_seed=body.bootstrapSeed,
            criteria=[c.model_dump() for c in body.criteria],
            actor_id=principal.subject)
        if version is None:
            raise HTTPException(status_code=404, detail="no such gate policy")
        return version

    @app.post("/gate-policy-versions/{gatePolicyVersionId}/publish")
    def publish_gate_policy_version(gatePolicyVersionId: str = Path(...),
                                    principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(gatePolicyVersionId, "gatePolicyVersionId")
        version = gate_service.publish_policy_version(
            organization_id=principal.organization_id,
            version_id=gatePolicyVersionId, actor_id=principal.subject)
        if version is None:
            raise HTTPException(status_code=404,
                                detail="no such gate policy version")
        return version

    @app.post("/projects/{projectId}/gate-evaluations")
    def evaluate_gate(body: GateEvaluationIn, projectId: str = Path(...),
                      principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        _require_ulid(body.candidateRunId, "candidateRunId")
        _require_ulid(body.gatePolicyVersionId, "gatePolicyVersionId")
        if body.baselineId:
            _require_ulid(body.baselineId, "baselineId")
        try:
            decision = gate_service.evaluate_gate(
                organization_id=principal.organization_id, project_id=projectId,
                candidate_run_id=body.candidateRunId,
                policy_version_id=body.gatePolicyVersionId,
                baseline_id=body.baselineId, actor_id=principal.subject)
        except PolicyNotPublished as exc:
            # The caller named a version that exists and is not fit to be cited.
            # A client error, never a platform failure (REQ-F-09-5).
            raise HTTPException(status_code=422, detail=str(exc))
        if decision is None:
            raise HTTPException(status_code=404,
                                detail="no such run or gate policy version")
        return decision

    @app.get("/gate-decisions/{gateDecisionId}")
    def get_gate_decision(gateDecisionId: str = Path(...),
                          accept: str = Header(default="application/json"),
                          principal: TenantPrincipal = Depends(principal_from_authorization)):
        """One decision, two representations (`REQ-F-09-4`).

        Negotiated rather than split across two operations: two operations would
        be two things to keep in step, and the requirement's point is that both
        describe the same decision with the same evidence.
        """
        _require_ulid(gateDecisionId, "gateDecisionId")
        if "text/markdown" in accept:
            body = gate_service.decision_report(principal.organization_id,
                                                gateDecisionId)
            if body is None:
                raise HTTPException(status_code=404, detail="no such gate decision")
            return PlainTextResponse(body, media_type="text/markdown")
        decision = gate_service.get_decision(principal.organization_id,
                                             gateDecisionId)
        if decision is None:
            raise HTTPException(status_code=404, detail="no such gate decision")
        return decision

    @app.post("/gate-decisions/{gateDecisionId}/exceptions", status_code=201)
    def create_policy_exception(body: PolicyExceptionIn,
                                gateDecisionId: str = Path(...),
                                principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(gateDecisionId, "gateDecisionId")
        exception = gate_service.create_exception(
            organization_id=principal.organization_id,
            decision_id=gateDecisionId, justification=body.justification,
            expires_at=body.expiresAt, actor_id=principal.subject)
        if exception is None:
            raise HTTPException(status_code=404, detail="no such gate decision")
        return exception

    # ================================================================ Phase 8
    @app.post("/projects/{projectId}/judges", status_code=201)
    def create_judge(body: JudgeIn, projectId: str = Path(...),
                     principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        return agentic_service.create_judge(
            organization_id=principal.organization_id, project_id=projectId,
            slug=body.slug, display_name=body.displayName,
            actor_id=principal.subject)

    @app.post("/judges/{judgeId}/versions", status_code=201)
    def add_judge_version(body: JudgeVersionIn, judgeId: str = Path(...),
                          principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(judgeId, "judgeId")
        _require_ulid(body.modelConfigurationId, "modelConfigurationId")
        return agentic_service.add_judge_version(
            organization_id=principal.organization_id, judge_id=judgeId,
            rubric=body.rubric, model_configuration_id=body.modelConfigurationId,
            actor_id=principal.subject)

    @app.post("/judge-versions/{judgeVersionId}/publish")
    def publish_judge_version(judgeVersionId: str = Path(...),
                              principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(judgeVersionId, "judgeVersionId")
        version = agentic_service.publish_judge_version(
            organization_id=principal.organization_id,
            version_id=judgeVersionId, actor_id=principal.subject)
        if version is None:
            raise HTTPException(status_code=404, detail="no such judge version")
        return version

    @app.post("/projects/{projectId}/judge-ensembles", status_code=201)
    def create_judge_ensemble(body: JudgeEnsembleIn, projectId: str = Path(...),
                              principal: TenantPrincipal = Depends(principal_from_authorization)):
        """A composition ADR-017 forbids is a caller error, not a platform one.

        An ensemble that cannot disagree with itself would agree on everything,
        which is a 422 about the request rather than a 503 about the service.
        """
        _require_ulid(projectId, "projectId")
        for version_id in body.judgeVersionIds:
            _require_ulid(version_id, "judgeVersionIds")
        try:
            return agentic_service.create_ensemble(
                organization_id=principal.organization_id, project_id=projectId,
                slug=body.slug, judge_version_ids=body.judgeVersionIds,
                agreement_threshold=(Decimal(body.agreementThreshold)
                                     if body.agreementThreshold else None),
                minimum_scoring_votes=body.minimumScoringVotes,
                actor_id=principal.subject)
        except (ConsensusError, JudgeRepositoryError) as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/judge-ensembles/{judgeEnsembleId}")
    def get_judge_ensemble(judgeEnsembleId: str = Path(...),
                           principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(judgeEnsembleId, "judgeEnsembleId")
        ensemble = agentic_service.get_ensemble(
            organization_id=principal.organization_id,
            ensemble_id=judgeEnsembleId)
        if ensemble is None:
            raise HTTPException(status_code=404, detail="no such ensemble")
        return ensemble

    @app.post("/projects/{projectId}/evaluation-plans", status_code=201)
    def create_evaluation_plan(body: EvaluationPlanIn, projectId: str = Path(...),
                               principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        _require_ulid(body.suiteVersionId, "suiteVersionId")
        if body.integrationTier:
            _require_enum(body.integrationTier, "IntegrationTier", "integrationTier")
        inputs = PlanInputs(
            objective=body.objective, suite_version_id=body.suiteVersionId,
            dataset_version_ids=(),
            candidate_labels=tuple(c.label or c.modelConfigurationId
                                   for c in body.candidates),
            baseline_id=body.baselineId,
            gate_policy_version_id=body.gatePolicyVersionId,
            judge_ensemble_id=body.judgeEnsembleId,
            budget=Decimal(body.budget.limit) if body.budget else None,
            currency=body.budget.currency if body.budget else "USD",
            integration_tier=body.integrationTier or "output_only")
        return agentic_service.create_plan(
            organization_id=principal.organization_id, project_id=projectId,
            inputs=inputs, actor_id=principal.subject,
            bounds=PLANNING_BOUNDS)

    @app.get("/evaluation-plans/{evaluationPlanId}")
    def get_evaluation_plan(evaluationPlanId: str = Path(...),
                            principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(evaluationPlanId, "evaluationPlanId")
        plan = agentic_service.get_plan(
            organization_id=principal.organization_id, plan_id=evaluationPlanId)
        if plan is None:
            raise HTTPException(status_code=404, detail="no such evaluation plan")
        return plan

    @app.post("/evaluation-plans/{evaluationPlanId}/amendments")
    def amend_evaluation_plan(body: PlanAmendmentIn,
                              evaluationPlanId: str = Path(...),
                              principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(evaluationPlanId, "evaluationPlanId")
        try:
            plan = agentic_service.amend_plan(
                organization_id=principal.organization_id,
                plan_id=evaluationPlanId, note=body.note,
                actor_id=principal.subject)
        except (PlanError, PlanSettled) as e:
            raise HTTPException(status_code=422, detail=str(e))
        if plan is None:
            raise HTTPException(status_code=404, detail="no such evaluation plan")
        return plan

    @app.post("/evaluation-plans/{evaluationPlanId}/acceptance")
    def accept_evaluation_plan(body: PlanAcceptanceIn,
                               evaluationPlanId: str = Path(...),
                               principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(evaluationPlanId, "evaluationPlanId")
        try:
            plan = agentic_service.accept_plan(
                organization_id=principal.organization_id,
                plan_id=evaluationPlanId, justification=body.justification,
                actor_id=principal.subject)
        except (PlanError, PlanSettled) as e:
            raise HTTPException(status_code=422, detail=str(e))
        if plan is None:
            raise HTTPException(status_code=404, detail="no such evaluation plan")
        return plan

    @app.get("/projects/{projectId}/escalations")
    def list_escalations(projectId: str = Path(...),
                         state: str | None = Query(default=None),
                         principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        if state:
            _require_enum(state, "EscalationState", "state")
        return agentic_service.list_escalations(
            organization_id=principal.organization_id, project_id=projectId,
            state=state)

    @app.post("/escalations/{escalationId}/review")
    def record_escalation_review(body: EscalationReviewIn,
                                 escalationId: str = Path(...),
                                 principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(escalationId, "escalationId")
        try:
            reviewed = agentic_service.review_escalation(
                organization_id=principal.organization_id,
                escalation_id=escalationId, outcome=body.outcome,
                justification=body.justification, actor_id=principal.subject)
        except EscalationAlreadyReviewed as e:
            raise HTTPException(status_code=422, detail=str(e))
        if reviewed is None:
            raise HTTPException(status_code=404, detail="no such escalation")
        return reviewed

    @app.get("/projects/{projectId}/evaluation-memory")
    def get_evaluation_memory(projectId: str = Path(...),
                              windowDays: int | None = Query(default=None, ge=1),
                              principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        return agentic_service.evaluation_memory(
            organization_id=principal.organization_id, project_id=projectId,
            window_days=windowDays)

    # ---- schedules and release observations ---------------------------------
    # Registered on the same rule as everything above it.
    if schedule_service is None:
        return app

    @app.post("/projects/{projectId}/evaluation-schedules", status_code=201)
    def create_evaluation_schedule(body: EvaluationScheduleIn,
                                   projectId: str = Path(...),
                                   principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        _require_ulid(body.suiteVersionId, "suiteVersionId")
        trigger = body.trigger or "schedule"
        _require_enum(trigger, "ScheduleTrigger", "trigger")
        try:
            return schedule_service.create_schedule(
                organization_id=principal.organization_id, project_id=projectId,
                suite_version_id=body.suiteVersionId, cadence=body.cadence,
                budget=(body.budget.limit, body.budget.currency),
                candidates=[c.model_dump() for c in body.candidates],
                trigger=trigger,
                gate_policy_version_id=body.gatePolicyVersionId,
                baseline_id=body.baselineId, actor_id=principal.subject)
        except (CadenceError, ScheduleError) as e:
            # 422 rather than 500: a cadence nothing can read is a request the
            # platform understood and refused, and refusing it here is what
            # stops a standing order that silently never fires.
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/evaluation-schedules/{scheduleId}/pause")
    def pause_evaluation_schedule(scheduleId: str = Path(...),
                                  principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(scheduleId, "scheduleId")
        paused = schedule_service.pause_schedule(
            organization_id=principal.organization_id, schedule_id=scheduleId,
            actor_id=principal.subject)
        if paused is None:
            raise HTTPException(status_code=404, detail="no such schedule")
        return paused

    @app.post("/projects/{projectId}/release-observations", status_code=201)
    def record_release_observation(body: ReleaseObservationIn,
                                   projectId: str = Path(...),
                                   principal: TenantPrincipal = Depends(principal_from_authorization)):
        _require_ulid(projectId, "projectId")
        _require_ulid(body.runId, "runId")
        # Only a trigger describing a system that is already live. A manual or
        # pull-request run is an evaluation before release, not an observation
        # of one after it, and the store refuses the row either way.
        if body.trigger not in ("post_deployment", "canary"):
            raise HTTPException(
                status_code=422,
                detail="trigger must be post_deployment or canary; an "
                       "observation describes a system that is already live")
        try:
            observation = schedule_service.record_observation(
                organization_id=principal.organization_id, project_id=projectId,
                run_id=body.runId, trigger=body.trigger,
                gate_decision_id=body.gateDecisionId, actor_id=principal.subject)
        except ReleaseObservationError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if observation is None:
            raise HTTPException(status_code=404,
                                detail="no such run or gate decision")
        return observation

    return app


def _require_enum(value: str, schema_name: str, field: str) -> None:
    """Validate against the contract's vocabulary, never a copy of it.

    A list restated here would be a second source of truth, and the first symptom
    of drift would be an accepted request the store then rejects.
    """
    permitted = contract.enum_of(schema_name)
    if value not in permitted:
        raise HTTPException(status_code=400,
                            detail=f"{field} must be one of {permitted}")


def _require_ulid(value: str, field: str) -> None:
    if not is_ulid(value):
        raise HTTPException(status_code=400,
                            detail=f"{field} is not a well-formed identifier")
