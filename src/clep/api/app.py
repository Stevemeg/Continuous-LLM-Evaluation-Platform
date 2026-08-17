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

import time
import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from clep.agents.planner import PlanError, PlanInputs
from clep.agents.repository import PlanSettled
from clep.agents.sdk import Bounds
from clep.analytics.alerts import AlertError
from clep.analytics.drift import DriftError
from clep.analytics.repository import AnalyticsError
from clep.api import contract
from clep.identity import is_ulid, new_ulid
from clep.judges.consensus import ConsensusError
from clep.judges.repository import (EscalationAlreadyReviewed,
                                    JudgeRepositoryError)
from clep.orchestration.releases import ReleaseObservationError
from clep.orchestration.schedules import CadenceError, ScheduleError
from clep.api.service import QuotaExhausted
from clep.regression.repository import PolicyNotPublished
from clep.security.erasure import BaselinePinned, ErasureError
from clep.security.repository import SecurityError
from clep.telemetry import NULL_TELEMETRY, correlated, current_id
from clep.telemetry.catalog import HTTP_METHODS

PROBLEM_TYPE = "https://clep.invalid/problems/"


def outcome_class_for(status: int) -> str:
    """A status code as one of the four declared outcome classes.

    The same split `Problem.category` makes, because `REQ-X-10` requires platform
    failure to be distinguishable from everything else *in every surface that
    reports outcomes* — and a metric is one of those surfaces. Availability and
    verdict integrity are both computed from this distinction, so collapsing a
    401 into "error" alongside a 503 would make both figures meaningless.
    """
    if status >= 500:
        return "platform_failure"
    if status in (401, 403):
        return "authorization"
    if status >= 400:
        return "client_error"
    return "success"


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
    """What a handler sees. Constructed only from a verified credential.

    Phase 5 fixed the *shape* of this — the organization derived from the
    credential and from nothing the caller can vary per request — and left
    issuance and verification to Phase 12. Until Phase 12 the derivation was a
    string split, so the shape was right and the tenant was whatever the caller
    typed. `authenticator` is what closed that.
    """
    organization_id: str
    subject: str
    kind: str = "user"
    api_key_id: str = ""


class ServiceAccountIn(BaseModel):
    slug: str = Field(min_length=1, max_length=64)
    displayName: str = Field(min_length=1)


class ApiKeyIn(BaseModel):
    principalKind: str
    subjectId: str
    displayName: str = Field(min_length=1)
    expiresAt: datetime | None = None


class RoleBindingIn(BaseModel):
    role: str = Field(min_length=1)
    principalKind: str
    subjectId: str
    scope: str
    projectId: str | None = None


class RetentionPolicyIn(BaseModel):
    decisionRetentionDays: int = Field(ge=1)
    contentRetentionDays: int = Field(ge=1)
    auditRetentionDays: int = Field(ge=1)


class UsageLimitIn(BaseModel):
    requestsPerMinute: int = Field(ge=1)
    runsPerPeriod: int = Field(ge=1)
    periodDays: int = Field(ge=1)


class ErasureRequestIn(BaseModel):
    exampleContentDigests: list[str] = Field(min_length=1)
    justification: str = Field(min_length=10)
    overrideBaselinePin: bool = False


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


class AlertRuleIn(BaseModel):
    slug: str = Field(min_length=1)
    displayName: str = Field(min_length=1)
    dimension: str
    metricKey: str = Field(min_length=1)
    direction: str
    threshold: str
    minimumSampleSize: int = Field(ge=1)


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
               agentic_service=None, schedule_service=None,
               analytics_service=None, *, authenticator=None,
               security_service=None, limiter=None, telemetry=None) -> FastAPI:
    """`run_service` supplies persistence and execution.

    Injected rather than imported so the contract tests can drive every path
    without a database, and so nothing in this module can reach the store
    directly and forget the tenant context on the way.

    `authenticator` turns a presented credential into
    `(Principal, Authorization)` and is **required**: an application built
    without one would authenticate nothing, and a default that accepted anything
    is the shape of every authentication bypass. It is a callable rather than a
    module import for the same reason the services are — so that the contract
    tests can drive every path, and so that nothing in this module can reach the
    credential store directly.
    """
    if authenticator is None:
        raise ValueError(
            "create_app requires an authenticator; an application that cannot "
            "verify a credential must not start, because the alternative is one "
            "that serves every request as whoever it claims to be")
    app = FastAPI(title=contract.load()["info"]["title"],
                  version=contract.load()["info"]["version"],
                  openapi_url=None)
    app.state.limiter = limiter
    telemetry = telemetry or NULL_TELEMETRY
    app.state.telemetry = telemetry

    @app.middleware("http")
    async def _correlate(request: Request, call_next):
        """Ingress: where the chain starts, and the only place it can start.

        Middleware rather than a dependency, because a dependency is per-route
        and `REQ-N-OBS-1` is about every request — including the ones that never
        reach a route, which are exactly the requests worth correlating when
        somebody asks what happened.

        This adds no route, so ADR-020 rule 6 and `_assert_every_route_is_guarded`
        are untouched.
        """
        method = request.method if request.method in HTTP_METHODS else "other"
        with correlated(inbound_reference=request.headers.get("x-correlation-id")) as c:
            started = time.monotonic()
            try:
                response = await call_next(request)
            except Exception:
                # An exception escaping the stack is the platform failing, and it
                # is recorded as such before it is re-raised. Recording it after
                # the handler would mean recording only the failures that were
                # caught, which are the ones already visible.
                telemetry.observe("clep_request_outcome_total", 1,
                                  outcome_class="platform_failure")
                telemetry.observe("clep_failure_attribution_total", 1,
                                  attribution="platform", surface="api")
                raise
            outcome = outcome_class_for(response.status_code)
            telemetry.observe("clep_http_request_duration_ms",
                              (time.monotonic() - started) * 1000.0,
                              method=method, outcome_class=outcome)
            telemetry.observe("clep_request_outcome_total", 1,
                              outcome_class=outcome)
            if outcome == "platform_failure":
                telemetry.observe("clep_failure_attribution_total", 1,
                                  attribution="platform", surface="api")
            # Returned so a client can join its own logs to ours. Safe to
            # return because the identifier carries no tenant identity
            # (ADR-022 rule 4) — and it is ours, not the one they sent.
            response.headers["x-correlation-id"] = c.correlation_id
            return response

    def _guard(method: str, path: str):
        """The one enforcement point (ADR-020 rules 6 and 7).

        Authenticate, then meter, then authorize — in that order, because a
        request that cannot be attributed to a tenant cannot be metered against
        one, and a request refused by the meter must not have been evaluated for
        authority it may not have.

        The permission is read from the contract rather than passed in.
        `operation_for` already refuses a route the contract does not declare;
        this refuses one the contract declares without a permission. Neither is
        expressible as an oversight: both fail at import.
        """
        operation = contract.operation_for(method, path)
        permission = operation.get("x-permission")
        if not permission:
            raise contract.ContractError(
                f"{operation['operationId']} declares no x-permission. Every "
                f"operation states the authority it requires in the contract; "
                f"a route with none would be a surface nobody attached a rule "
                f"to, which is the failure ADR-020 exists to make impossible.")

        def dependency(request: Request,
                       authorization: str | None = Header(default=None)):
            principal, granted = _authenticate(request, authorization)
            _meter(request, principal)
            project_id = request.path_params.get("projectId")
            if not granted.allows(permission, project_id):
                _record_denial(principal, permission, request)
                # Indistinguishable from "the object is not yours", for the
                # reason the run surface already returns an indistinguishable
                # 404: a response that separated them would tell a caller which
                # objects exist (ADR-020 rule 8).
                raise HTTPException(status_code=403, detail="not permitted")
            return principal

        #: What the import-time closure below looks for. A route registered
        #: without this dependency is a route with no authorization, and naming
        #: the attribute is how that is detected structurally rather than by
        #: reading the source.
        dependency.__clep_permission__ = permission
        dependency.__clep_operation__ = operation["operationId"]
        return dependency

    def _authenticate(request, authorization: str | None):
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status_code=401,
                                detail="bearer credential required")
        try:
            principal, granted = authenticator(
                authorization.split(" ", 1)[1].strip())
        except Exception as exc:  # noqa: BLE001 - see below
            # Every rejection reason collapses to one response (ADR-019 rule
            # 11). The specific reason is the operator's, through the audit
            # trail; distinguishing them here would be an oracle for
            # enumerating valid identifiers. The broad except is deliberate and
            # bounded: `authenticator` is injected, so its exception types are
            # not knowable here, and any failure to verify is a failure to
            # authenticate.
            _record_authentication_failure(request, exc)
            raise HTTPException(status_code=401, detail="credential rejected")
        return principal, granted

    def _meter(request, principal):
        if app.state.limiter is None:
            return
        verdict = app.state.limiter.check(principal.organization_id)
        if not verdict.allowed:
            raise HTTPException(status_code=429, detail=verdict.detail)

    def _record_denial(principal, permission, request):
        if security_service is None:
            return
        security_service.record_denial(
            organization_id=principal.organization_id,
            actor_id=principal.subject, permission=permission,
            target=request.url.path)

    def _record_authentication_failure(request, exc):
        if security_service is None:
            return
        security_service.record_authentication_failure(
            reason=getattr(exc, "reason", type(exc).__name__),
            target=request.url.path)

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
                         ("POST", "/projects/{projectId}/release-observations"),
                         ("GET", "/projects/{projectId}/analytics/quality-trend"),
                         ("GET", "/projects/{projectId}/analytics/leaderboard"),
                         ("GET", "/projects/{projectId}/analytics/operational"),
                         ("GET", "/projects/{projectId}/analytics/judges"),
                         ("GET", "/projects/{projectId}/analytics/agents"),
                         ("GET", "/projects/{projectId}/analytics/rag"),
                         ("GET", "/projects/{projectId}/analytics/drift"),
                         ("GET", "/projects/{projectId}/scorecard"),
                         ("POST", "/projects/{projectId}/alert-rules"),
                         ("GET", "/projects/{projectId}/alert-rules"),
                         ("POST", "/alert-rules/{alertRuleId}/pause"),
                         ("POST", "/runs/{runId}/alert-evaluations"),
                         ("GET", "/projects/{projectId}/alert-events"),
                         # Phase 12.
                         ("POST", "/service-accounts"),
                         ("POST", "/api-keys"),
                         ("GET", "/api-keys"),
                         ("POST", "/api-keys/{apiKeyId}/rotation"),
                         ("POST", "/api-keys/{apiKeyId}/revocation"),
                         ("GET", "/roles"),
                         ("POST", "/role-bindings"),
                         ("GET", "/role-bindings"),
                         ("POST", "/role-bindings/{roleBindingId}/revocation"),
                         ("GET", "/retention-policy"),
                         ("PUT", "/retention-policy"),
                         ("GET", "/usage-limit"),
                         ("PUT", "/usage-limit"),
                         ("GET", "/projects/{projectId}/audit-events"),
                         ("POST", "/erasure-requests")):
        contract.operation_for(method, path)

    @app.exception_handler(HTTPException)
    async def _http_exception(request: Request, exc: HTTPException):
        category = "authorization" if exc.status_code in (401, 403) else "client_error"
        if exc.status_code >= 500:
            category = "platform_failure"
        # Was `request.headers.get("x-correlation-id")` — the caller's own claim
        # echoed back, which correlates a Problem to nothing the platform
        # recorded. `current_id()` is the identifier this request actually ran
        # under and the one every other hop was written with, so a Problem is now
        # joinable to the run, the audit event and the gate decision behind it.
        return problem(exc.status_code, exc.detail or "request failed", category,
                       exc.detail or "", current_id())

    @app.post("/projects/{projectId}/runs", status_code=202)
    def create_run(body: RunRequestIn, projectId: str = Path(...),
                   principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/runs")),
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
        try:
            run = run_service.create_run(
                organization_id=principal.organization_id, project_id=projectId,
                suite_version_id=body.suiteVersionId,
                candidates=[c.model_dump() for c in body.candidates],
                integration_tier=tier, budget=budget,
                idempotency_key=idempotency_key)
        except QuotaExhausted as e:
            # 429 as the contract declares, and `client_error` rather than
            # `platform_failure`: the platform decided, correctly, that this
            # tenant has spent their allowance.
            raise HTTPException(status_code=429, detail=str(e))
        return run

    @app.get("/runs/{runId}")
    def get_run(runId: str = Path(...),
                principal: TenantPrincipal = Depends(_guard("GET", "/runs/{runId}"))):
        _require_ulid(runId, "runId")
        run = run_service.get_run(principal.organization_id, runId)
        if run is None:
            # Indistinguishable from another tenant's run, on purpose: a 404 that
            # differs from a 403 tells an attacker which identifiers exist.
            raise HTTPException(status_code=404, detail="no such run")
        return run

    @app.post("/runs/{runId}/cancel", status_code=202)
    def cancel_run(runId: str = Path(...),
                   principal: TenantPrincipal = Depends(_guard("POST", "/runs/{runId}/cancel"))):
        _require_ulid(runId, "runId")
        cancelled = run_service.cancel_run(principal.organization_id, runId)
        if cancelled is None:
            raise HTTPException(status_code=404, detail="no such run")
        return cancelled

    @app.get("/runs/{runId}/samples")
    def list_run_samples(runId: str = Path(...), limit: int = Query(50, ge=1, le=200),
                         offset: int = Query(0, ge=0),
                         principal: TenantPrincipal = Depends(_guard("GET", "/runs/{runId}/samples"))):
        _require_ulid(runId, "runId")
        page = run_service.list_samples(principal.organization_id, runId, limit, offset)
        if page is None:
            raise HTTPException(status_code=404, detail="no such run")
        return page

    @app.get("/runs/{runId}/identity")
    def get_run_identity(runId: str = Path(...),
                         principal: TenantPrincipal = Depends(_guard("GET", "/runs/{runId}/identity"))):
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
                      principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/prompts"))):
        _require_ulid(projectId, "projectId")
        return registry_service.create_prompt(
            organization_id=principal.organization_id, project_id=projectId,
            slug=body.slug, display_name=body.displayName,
            actor_id=principal.subject)

    @app.post("/prompts/{promptId}/versions", status_code=201)
    def add_prompt_version(body: PromptVersionIn, promptId: str = Path(...),
                           principal: TenantPrincipal = Depends(_guard("POST", "/prompts/{promptId}/versions"))):
        _require_ulid(promptId, "promptId")
        version = registry_service.add_prompt_version(
            organization_id=principal.organization_id, prompt_id=promptId,
            body=body.body, actor_id=principal.subject)
        if version is None:
            raise HTTPException(status_code=404, detail="no such prompt")
        return version

    @app.get("/prompt-versions/{promptVersionId}")
    def get_prompt_version(promptVersionId: str = Path(...),
                           principal: TenantPrincipal = Depends(_guard("GET", "/prompt-versions/{promptVersionId}"))):
        _require_ulid(promptVersionId, "promptVersionId")
        version = registry_service.get_prompt_version(principal.organization_id,
                                                      promptVersionId)
        if version is None:
            raise HTTPException(status_code=404, detail="no such prompt version")
        return version

    @app.post("/prompt-versions/{promptVersionId}/publish")
    def publish_prompt_version(promptVersionId: str = Path(...),
                               principal: TenantPrincipal = Depends(_guard("POST", "/prompt-versions/{promptVersionId}/publish"))):
        _require_ulid(promptVersionId, "promptVersionId")
        version = registry_service.publish_prompt_version(
            organization_id=principal.organization_id,
            version_id=promptVersionId, actor_id=principal.subject)
        if version is None:
            raise HTTPException(status_code=404, detail="no such prompt version")
        return version

    @app.post("/projects/{projectId}/experiments", status_code=201)
    def create_experiment(body: ExperimentIn, projectId: str = Path(...),
                          principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/experiments"))):
        _require_ulid(projectId, "projectId")
        return registry_service.create_experiment(
            organization_id=principal.organization_id, project_id=projectId,
            slug=body.slug, display_name=body.displayName,
            hypothesis=body.hypothesis, actor_id=principal.subject)

    @app.post("/runs/{runId}/reproductions", status_code=201)
    def reproduce_run(runId: str = Path(...),
                      principal: TenantPrincipal = Depends(_guard("POST", "/runs/{runId}/reproductions"))):
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
                        principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/baselines"))):
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
                         principal: TenantPrincipal = Depends(_guard("POST", "/baselines/{baselineId}/approval"))):
        _require_ulid(baselineId, "baselineId")
        baseline = gate_service.approve_baseline(
            organization_id=principal.organization_id, baseline_id=baselineId,
            actor_id=principal.subject)
        if baseline is None:
            raise HTTPException(status_code=404, detail="no such baseline")
        return baseline

    @app.post("/projects/{projectId}/gate-policies", status_code=201)
    def create_gate_policy(body: GatePolicyIn, projectId: str = Path(...),
                           principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/gate-policies"))):
        _require_ulid(projectId, "projectId")
        return gate_service.create_gate_policy(
            organization_id=principal.organization_id, project_id=projectId,
            slug=body.slug, display_name=body.displayName,
            actor_id=principal.subject)

    @app.post("/gate-policies/{gatePolicyId}/versions", status_code=201)
    def add_gate_policy_version(body: GatePolicyVersionIn,
                                gatePolicyId: str = Path(...),
                                principal: TenantPrincipal = Depends(_guard("POST", "/gate-policies/{gatePolicyId}/versions"))):
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
                                    principal: TenantPrincipal = Depends(_guard("POST", "/gate-policy-versions/{gatePolicyVersionId}/publish"))):
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
                      principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/gate-evaluations"))):
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
                          principal: TenantPrincipal = Depends(_guard("GET", "/gate-decisions/{gateDecisionId}"))):
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
                                principal: TenantPrincipal = Depends(_guard("POST", "/gate-decisions/{gateDecisionId}/exceptions"))):
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
                     principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/judges"))):
        _require_ulid(projectId, "projectId")
        return agentic_service.create_judge(
            organization_id=principal.organization_id, project_id=projectId,
            slug=body.slug, display_name=body.displayName,
            actor_id=principal.subject)

    @app.post("/judges/{judgeId}/versions", status_code=201)
    def add_judge_version(body: JudgeVersionIn, judgeId: str = Path(...),
                          principal: TenantPrincipal = Depends(_guard("POST", "/judges/{judgeId}/versions"))):
        _require_ulid(judgeId, "judgeId")
        _require_ulid(body.modelConfigurationId, "modelConfigurationId")
        return agentic_service.add_judge_version(
            organization_id=principal.organization_id, judge_id=judgeId,
            rubric=body.rubric, model_configuration_id=body.modelConfigurationId,
            actor_id=principal.subject)

    @app.post("/judge-versions/{judgeVersionId}/publish")
    def publish_judge_version(judgeVersionId: str = Path(...),
                              principal: TenantPrincipal = Depends(_guard("POST", "/judge-versions/{judgeVersionId}/publish"))):
        _require_ulid(judgeVersionId, "judgeVersionId")
        version = agentic_service.publish_judge_version(
            organization_id=principal.organization_id,
            version_id=judgeVersionId, actor_id=principal.subject)
        if version is None:
            raise HTTPException(status_code=404, detail="no such judge version")
        return version

    @app.post("/projects/{projectId}/judge-ensembles", status_code=201)
    def create_judge_ensemble(body: JudgeEnsembleIn, projectId: str = Path(...),
                              principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/judge-ensembles"))):
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
                           principal: TenantPrincipal = Depends(_guard("GET", "/judge-ensembles/{judgeEnsembleId}"))):
        _require_ulid(judgeEnsembleId, "judgeEnsembleId")
        ensemble = agentic_service.get_ensemble(
            organization_id=principal.organization_id,
            ensemble_id=judgeEnsembleId)
        if ensemble is None:
            raise HTTPException(status_code=404, detail="no such ensemble")
        return ensemble

    @app.post("/projects/{projectId}/evaluation-plans", status_code=201)
    def create_evaluation_plan(body: EvaluationPlanIn, projectId: str = Path(...),
                               principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/evaluation-plans"))):
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
                            principal: TenantPrincipal = Depends(_guard("GET", "/evaluation-plans/{evaluationPlanId}"))):
        _require_ulid(evaluationPlanId, "evaluationPlanId")
        plan = agentic_service.get_plan(
            organization_id=principal.organization_id, plan_id=evaluationPlanId)
        if plan is None:
            raise HTTPException(status_code=404, detail="no such evaluation plan")
        return plan

    @app.post("/evaluation-plans/{evaluationPlanId}/amendments")
    def amend_evaluation_plan(body: PlanAmendmentIn,
                              evaluationPlanId: str = Path(...),
                              principal: TenantPrincipal = Depends(_guard("POST", "/evaluation-plans/{evaluationPlanId}/amendments"))):
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
                               principal: TenantPrincipal = Depends(_guard("POST", "/evaluation-plans/{evaluationPlanId}/acceptance"))):
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
                         principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/escalations"))):
        _require_ulid(projectId, "projectId")
        if state:
            _require_enum(state, "EscalationState", "state")
        return agentic_service.list_escalations(
            organization_id=principal.organization_id, project_id=projectId,
            state=state)

    @app.post("/escalations/{escalationId}/review")
    def record_escalation_review(body: EscalationReviewIn,
                                 escalationId: str = Path(...),
                                 principal: TenantPrincipal = Depends(_guard("POST", "/escalations/{escalationId}/review"))):
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
                              principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/evaluation-memory"))):
        _require_ulid(projectId, "projectId")
        return agentic_service.evaluation_memory(
            organization_id=principal.organization_id, project_id=projectId,
            window_days=windowDays)

    # Each block registers only when its own service is supplied. Chained
    # early returns did this before, which meant a deployment wanting
    # analytics but not schedules silently lost the analytics routes.
    _register_schedule_routes(app, schedule_service, _guard)
    _register_analytics_routes(app, analytics_service, _guard)
    _register_governance_routes(app, security_service, _guard)
    _assert_every_route_is_guarded(app)
    return app


def _assert_every_route_is_guarded(app) -> None:
    """ADR-020 rule 6, checked structurally at import.

    Not by reading the source — nine checks in this project have been lost to
    text matching. This walks the routes FastAPI actually registered and looks
    for the guard's own marker on one of each route's dependencies. A route
    added without `Depends(_guard(...))` has no marker, no permission, and no
    authorization, and it does not start.
    """
    unguarded = []
    for route in app.routes:
        dependant = getattr(route, "dependant", None)
        if dependant is None or not getattr(route, "methods", None):
            continue  # the default docs and openapi routes, which carry none
        marked = any(
            hasattr(sub.call, "__clep_permission__")
            for sub in dependant.dependencies if sub.call is not None)
        if not marked:
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    if unguarded:
        raise contract.ContractError(
            f"{len(unguarded)} route(s) carry no authorization guard: "
            f"{unguarded}. Every route declares the permission it requires "
            f"through Depends(_guard(...)); a route that declares none is a "
            f"surface nobody attached a rule to.")


def _register_schedule_routes(app, schedule_service, _guard) -> None:
    """Registered only when the service is supplied. A route that exists and
    cannot work is worse than one that does not exist: the first is a 500 a
    client discovers in production, the second is a 404 it discovers at once."""
    if schedule_service is None:
        return
    # ---- schedules and release observations ---------------------------------
    @app.post("/projects/{projectId}/evaluation-schedules", status_code=201)
    def create_evaluation_schedule(body: EvaluationScheduleIn,
                                   projectId: str = Path(...),
                                   principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/evaluation-schedules"))):
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
                                  principal: TenantPrincipal = Depends(_guard("POST", "/evaluation-schedules/{scheduleId}/pause"))):
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
                                   principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/release-observations"))):
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


def _register_analytics_routes(app, analytics_service, _guard) -> None:
    """Registered only when the service is supplied. A route that exists and
    cannot work is worse than one that does not exist: the first is a 500 a
    client discovers in production, the second is a 404 it discovers at once."""
    if analytics_service is None:
        return
    # ---- analytics, the scorecard and alerting ------------------------------
    @app.get("/projects/{projectId}/analytics/quality-trend")
    def get_quality_trend(projectId: str = Path(...),
                          suiteVersionId: str | None = Query(default=None),
                          metricKey: str | None = Query(default=None),
                          windowDays: int | None = Query(default=None, ge=1),
                          limit: int = Query(100, ge=1, le=500),
                          principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/analytics/quality-trend"))):
        _require_ulid(projectId, "projectId")
        if suiteVersionId:
            _require_ulid(suiteVersionId, "suiteVersionId")
        return analytics_service.quality_trend(
            organization_id=principal.organization_id, project_id=projectId,
            suite_version_id=suiteVersionId, metric_key=metricKey,
            window_days=windowDays, limit=limit)

    @app.get("/projects/{projectId}/analytics/leaderboard")
    def get_benchmark_leaderboard(projectId: str = Path(...),
                                  suiteVersionId: str = Query(...),
                                  windowDays: int | None = Query(default=None, ge=1),
                                  principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/analytics/leaderboard"))):
        # Required by the signature, not merely by convention. REQ-F-11-2
        # forbids a global ranking, and an optional benchmark is a global
        # ranking with extra steps.
        _require_ulid(projectId, "projectId")
        _require_ulid(suiteVersionId, "suiteVersionId")
        try:
            return analytics_service.leaderboard(
                organization_id=principal.organization_id, project_id=projectId,
                suite_version_id=suiteVersionId, window_days=windowDays)
        except AnalyticsError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/projects/{projectId}/analytics/operational")
    def get_operational_analytics(projectId: str = Path(...),
                                  suiteVersionId: str | None = Query(default=None),
                                  windowDays: int | None = Query(default=None, ge=1),
                                  principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/analytics/operational"))):
        _require_ulid(projectId, "projectId")
        if suiteVersionId:
            _require_ulid(suiteVersionId, "suiteVersionId")
        return analytics_service.operational(
            organization_id=principal.organization_id, project_id=projectId,
            suite_version_id=suiteVersionId, window_days=windowDays)

    @app.get("/projects/{projectId}/analytics/judges")
    def get_judge_analytics(projectId: str = Path(...),
                            windowDays: int | None = Query(default=None, ge=1),
                            principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/analytics/judges"))):
        _require_ulid(projectId, "projectId")
        return analytics_service.judges(
            organization_id=principal.organization_id, project_id=projectId,
            window_days=windowDays)

    @app.get("/projects/{projectId}/analytics/agents")
    def get_agent_analytics(projectId: str = Path(...),
                            suiteVersionId: str | None = Query(default=None),
                            windowDays: int | None = Query(default=None, ge=1),
                            principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/analytics/agents"))):
        _require_ulid(projectId, "projectId")
        if suiteVersionId:
            _require_ulid(suiteVersionId, "suiteVersionId")
        return analytics_service.agents(
            organization_id=principal.organization_id, project_id=projectId,
            suite_version_id=suiteVersionId, window_days=windowDays)

    @app.get("/projects/{projectId}/analytics/rag")
    def get_rag_analytics(projectId: str = Path(...),
                          suiteVersionId: str | None = Query(default=None),
                          windowDays: int | None = Query(default=None, ge=1),
                          principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/analytics/rag"))):
        _require_ulid(projectId, "projectId")
        if suiteVersionId:
            _require_ulid(suiteVersionId, "suiteVersionId")
        return analytics_service.rag(
            organization_id=principal.organization_id, project_id=projectId,
            suite_version_id=suiteVersionId, window_days=windowDays)

    @app.get("/projects/{projectId}/analytics/drift")
    def get_quality_drift(projectId: str = Path(...),
                          suiteVersionId: str = Query(...),
                          runId: str = Query(...), metricKey: str = Query(...),
                          minimumHistory: int | None = Query(default=None, ge=2),
                          tolerance: str | None = Query(default=None),
                          principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/analytics/drift"))):
        for name, value in (("projectId", projectId),
                            ("suiteVersionId", suiteVersionId),
                            ("runId", runId)):
            _require_ulid(value, name)
        try:
            analysis = analytics_service.drift(
                organization_id=principal.organization_id, project_id=projectId,
                run_id=runId, suite_version_id=suiteVersionId,
                metric_key=metricKey, minimum_history=minimumHistory,
                tolerance=Decimal(tolerance) if tolerance else None)
        except DriftError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if analysis is None:
            raise HTTPException(status_code=404, detail="no such run")
        return analysis

    @app.get("/projects/{projectId}/scorecard")
    def get_project_scorecard(projectId: str = Path(...),
                              suiteVersionId: str | None = Query(default=None),
                              windowDays: int | None = Query(default=None, ge=1),
                              format: str = Query("json"),
                              minimumHistory: int | None = Query(default=None, ge=2),
                              tolerance: str | None = Query(default=None),
                              principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/scorecard"))):
        _require_ulid(projectId, "projectId")
        if suiteVersionId:
            _require_ulid(suiteVersionId, "suiteVersionId")
        if format not in ("json", "markdown"):
            raise HTTPException(status_code=400,
                                detail="format must be json or markdown")
        card = analytics_service.scorecard(
            organization_id=principal.organization_id, project_id=projectId,
            suite_version_id=suiteVersionId, window_days=windowDays,
            minimum_history=minimumHistory,
            tolerance=Decimal(tolerance) if tolerance else None,
            representation=format)
        if format == "markdown":
            return PlainTextResponse(card, media_type="text/markdown")
        return card

    @app.post("/projects/{projectId}/alert-rules", status_code=201)
    def create_alert_rule(body: AlertRuleIn, projectId: str = Path(...),
                          principal: TenantPrincipal = Depends(_guard("POST", "/projects/{projectId}/alert-rules"))):
        _require_ulid(projectId, "projectId")
        _require_enum(body.dimension, "AlertDimension", "dimension")
        _require_enum(body.direction, "MetricDirection", "direction")
        try:
            return analytics_service.create_alert_rule(
                organization_id=principal.organization_id, project_id=projectId,
                actor_id=principal.subject, slug=body.slug,
                display_name=body.displayName, dimension=body.dimension,
                metric_key=body.metricKey, direction=body.direction,
                threshold=Decimal(body.threshold),
                minimum_sample_size=body.minimumSampleSize)
        except AlertError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/projects/{projectId}/alert-rules")
    def list_alert_rules(projectId: str = Path(...),
                         principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/alert-rules"))):
        _require_ulid(projectId, "projectId")
        return analytics_service.list_alert_rules(
            organization_id=principal.organization_id, project_id=projectId)

    @app.post("/alert-rules/{alertRuleId}/pause")
    def pause_alert_rule(alertRuleId: str = Path(...),
                         principal: TenantPrincipal = Depends(_guard("POST", "/alert-rules/{alertRuleId}/pause"))):
        _require_ulid(alertRuleId, "alertRuleId")
        paused = analytics_service.pause_alert_rule(
            organization_id=principal.organization_id, rule_id=alertRuleId,
            actor_id=principal.subject)
        if paused is None:
            raise HTTPException(status_code=404, detail="no such alert rule")
        return paused

    @app.post("/runs/{runId}/alert-evaluations", status_code=201)
    def evaluate_alerts(runId: str = Path(...),
                        principal: TenantPrincipal = Depends(_guard("POST", "/runs/{runId}/alert-evaluations"))):
        _require_ulid(runId, "runId")
        try:
            evaluated = analytics_service.evaluate_alerts(
                organization_id=principal.organization_id, run_id=runId,
                actor_id=principal.subject)
        except AlertError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if evaluated is None:
            raise HTTPException(status_code=404, detail="no such run")
        return evaluated

    @app.get("/projects/{projectId}/alert-events")
    def list_alert_events(projectId: str = Path(...),
                          limit: int = Query(50, ge=1, le=200),
                          principal: TenantPrincipal = Depends(_guard("GET", "/projects/{projectId}/alert-events"))):
        _require_ulid(projectId, "projectId")
        return analytics_service.list_alert_events(
            organization_id=principal.organization_id, project_id=projectId,
            limit=limit)


def _register_governance_routes(app, security_service, _guard) -> None:
    """The Phase 12 surface: credentials, bindings, policy, audit and erasure.

    No operation here names an organization in its path. The tenant comes from
    the verified credential (ADR-010 rule 3), so a caller cannot ask for another
    tenant's credentials by asking politely — which is the same rule every
    earlier route follows and the one that matters most on these.
    """
    if security_service is None:
        return

    @app.post("/service-accounts", status_code=201)
    def create_service_account(
            body: ServiceAccountIn,
            principal: TenantPrincipal = Depends(
                _guard("POST", "/service-accounts"))):
        try:
            return security_service.create_service_account(
                organization_id=principal.organization_id, slug=body.slug,
                display_name=body.displayName, actor_id=principal.subject)
        except SecurityError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.post("/api-keys", status_code=201)
    def issue_api_key(
            body: ApiKeyIn,
            principal: TenantPrincipal = Depends(_guard("POST", "/api-keys"))):
        _require_enum(body.principalKind, "PrincipalKind", "principalKind")
        _require_ulid(body.subjectId, "subjectId")
        try:
            return security_service.issue_api_key(
                organization_id=principal.organization_id,
                principal_kind=body.principalKind, subject_id=body.subjectId,
                display_name=body.displayName, expires_at=body.expiresAt,
                actor_id=principal.subject)
        except SecurityError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/api-keys")
    def list_api_keys(
            principal: TenantPrincipal = Depends(_guard("GET", "/api-keys"))):
        return security_service.list_api_keys(
            organization_id=principal.organization_id)

    @app.post("/api-keys/{apiKeyId}/rotation", status_code=201)
    def rotate_api_key(
            apiKeyId: str = Path(...),
            principal: TenantPrincipal = Depends(
                _guard("POST", "/api-keys/{apiKeyId}/rotation"))):
        _require_ulid(apiKeyId, "apiKeyId")
        try:
            rotated = security_service.rotate_api_key(
                organization_id=principal.organization_id, key_id=apiKeyId,
                actor_id=principal.subject)
        except SecurityError as e:
            raise HTTPException(status_code=422, detail=str(e))
        if rotated is None:
            raise HTTPException(status_code=404, detail="no such api key")
        return rotated

    @app.post("/api-keys/{apiKeyId}/revocation")
    def revoke_api_key(
            apiKeyId: str = Path(...),
            principal: TenantPrincipal = Depends(
                _guard("POST", "/api-keys/{apiKeyId}/revocation"))):
        _require_ulid(apiKeyId, "apiKeyId")
        revoked = security_service.revoke_api_key(
            organization_id=principal.organization_id, key_id=apiKeyId,
            actor_id=principal.subject)
        if revoked is None:
            raise HTTPException(status_code=404, detail="no such api key")
        return revoked

    @app.get("/roles")
    def list_roles(
            principal: TenantPrincipal = Depends(_guard("GET", "/roles"))):
        return security_service.list_roles(
            organization_id=principal.organization_id)

    @app.post("/role-bindings", status_code=201)
    def create_role_binding(
            body: RoleBindingIn,
            principal: TenantPrincipal = Depends(
                _guard("POST", "/role-bindings"))):
        _require_enum(body.principalKind, "PrincipalKind", "principalKind")
        _require_enum(body.scope, "BindingScope", "scope")
        _require_ulid(body.subjectId, "subjectId")
        if body.projectId:
            _require_ulid(body.projectId, "projectId")
        try:
            return security_service.create_role_binding(
                organization_id=principal.organization_id, role=body.role,
                principal_kind=body.principalKind, subject_id=body.subjectId,
                scope=body.scope, project_id=body.projectId,
                actor_id=principal.subject)
        except SecurityError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/role-bindings")
    def list_role_bindings(
            principal: TenantPrincipal = Depends(
                _guard("GET", "/role-bindings"))):
        return security_service.list_role_bindings(
            organization_id=principal.organization_id)

    @app.post("/role-bindings/{roleBindingId}/revocation")
    def revoke_role_binding(
            roleBindingId: str = Path(...),
            principal: TenantPrincipal = Depends(
                _guard("POST", "/role-bindings/{roleBindingId}/revocation"))):
        _require_ulid(roleBindingId, "roleBindingId")
        try:
            revoked = security_service.revoke_role_binding(
                organization_id=principal.organization_id,
                binding_id=roleBindingId, actor_id=principal.subject)
        except SecurityError as e:
            # I-4 speaking through the store. A 422 rather than a 500: the
            # request was understood and refused, and the reason is something
            # the caller can act on by granting someone else first.
            raise HTTPException(status_code=422, detail=str(e))
        if revoked is None:
            raise HTTPException(status_code=404, detail="no such role binding")
        return revoked

    @app.get("/retention-policy")
    def get_retention_policy(
            principal: TenantPrincipal = Depends(
                _guard("GET", "/retention-policy"))):
        return security_service.retention_policy(
            organization_id=principal.organization_id)

    @app.put("/retention-policy")
    def set_retention_policy(
            body: RetentionPolicyIn,
            principal: TenantPrincipal = Depends(
                _guard("PUT", "/retention-policy"))):
        try:
            return security_service.set_retention_policy(
                organization_id=principal.organization_id,
                decision_days=body.decisionRetentionDays,
                content_days=body.contentRetentionDays,
                audit_days=body.auditRetentionDays,
                actor_id=principal.subject)
        except SecurityError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/usage-limit")
    def get_usage_limit(
            principal: TenantPrincipal = Depends(
                _guard("GET", "/usage-limit"))):
        return security_service.usage_limit(
            organization_id=principal.organization_id)

    @app.put("/usage-limit")
    def set_usage_limit(
            body: UsageLimitIn,
            principal: TenantPrincipal = Depends(
                _guard("PUT", "/usage-limit"))):
        try:
            return security_service.set_usage_limit(
                organization_id=principal.organization_id,
                requests_per_minute=body.requestsPerMinute,
                runs_per_period=body.runsPerPeriod,
                period_days=body.periodDays, actor_id=principal.subject)
        except SecurityError as e:
            raise HTTPException(status_code=422, detail=str(e))

    @app.get("/projects/{projectId}/audit-events")
    def list_audit_events(
            projectId: str = Path(...),
            cursor: str | None = Query(default=None),
            limit: int = Query(50, ge=1, le=200),
            principal: TenantPrincipal = Depends(
                _guard("GET", "/projects/{projectId}/audit-events"))):
        """`REQ-N-COMP-1`: what evidence supported a decision, and who approved.

        Project-addressed although the audit trail is tenant-scoped. That is
        deliberate: an audit event names a target rather than a project, so the
        filter is over the targets reachable from this project. An event whose
        target belongs to no project — a credential, a role binding — is
        tenant-wide and is returned for every project in the tenant, because
        hiding it would make the trail incomplete exactly where it matters.
        """
        _require_ulid(projectId, "projectId")
        return security_service.list_audit_events(
            organization_id=principal.organization_id, project_id=projectId,
            cursor=cursor, limit=limit)

    @app.post("/erasure-requests", status_code=202)
    def create_erasure_request(
            body: ErasureRequestIn,
            principal: TenantPrincipal = Depends(
                _guard("POST", "/erasure-requests"))):
        try:
            accepted = security_service.request_erasure(
                organization_id=principal.organization_id,
                digests=body.exampleContentDigests,
                justification=body.justification,
                override_baseline_pin=body.overrideBaselinePin,
                actor_id=principal.subject)
        except BaselinePinned as e:
            # 409 as the contract declares: an active baseline pins the target
            # and an audited override is required. Not a 422, because the
            # request is well formed and would be accepted with the override.
            raise HTTPException(status_code=409, detail=str(e))
        except ErasureError as e:
            raise HTTPException(status_code=422, detail=str(e))
        return accepted


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
