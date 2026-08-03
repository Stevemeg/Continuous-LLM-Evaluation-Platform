"""HTTP surface for the run operations the core harness owns.

Phase 5 implements four of the contract's thirteen operations: the four that
belong to the evaluation harness. The other nine belong to phases that have not
run — gate evaluation to Phase 7, dataset and baseline management to Phase 6,
erasure and audit surfaces to Phase 12 — and are deliberately absent rather than
stubbed. A stub that returns 501 is still a route a client can find; a route that
does not exist is an honest 404.

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
from decimal import Decimal

from fastapi import Depends, FastAPI, Header, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from clep.api import contract
from clep.identity import is_ulid, new_ulid

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


def create_app(run_service, registry_service=None) -> FastAPI:
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
                         ("POST", "/projects/{projectId}/experiments")):
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

    return app


def _require_ulid(value: str, field: str) -> None:
    if not is_ulid(value):
        raise HTTPException(status_code=400,
                            detail=f"{field} is not a well-formed identifier")
