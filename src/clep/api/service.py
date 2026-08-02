"""Application service behind the HTTP surface.

Everything here opens a tenant-bound session and does its work inside it. The
organization identifier arrives from the ingress principal and is never a
parameter a caller can influence, which is what makes ADR-010 rule 3 true in
practice rather than only in the architecture document.

Responses are assembled in the contract's vocabulary — `completeness`,
`reproducibility`, ULID identifiers — because the contract is authoritative and
the store's column names are not part of it.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal

from clep.db.session import tenant_session
from clep.identity import new_ulid
from clep.orchestration.repository import RunRepository


def identity_digest(*parts: str) -> str:
    """`REQ-F-07-1`: run identity is frozen before execution and never updated.

    Derived from the inputs that determine what the run measures, so two runs
    with the same identity measured the same thing — which is the property
    reproducibility claims depend on.
    """
    material = "\x1f".join(str(p) for p in parts)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class RunService:
    def __init__(self, runtime_dsn: str, *, dataset_version_resolver=None):
        self._dsn = runtime_dsn
        #: Supplied by the caller; Phase 5 does not own dataset selection, which
        #: belongs to the registry phases. Injected rather than guessed.
        self._resolve_dataset_version = dataset_version_resolver or (lambda org, suite: suite)

    # ------------------------------------------------------------------ write
    def create_run(self, *, organization_id: str, project_id: str,
                   suite_version_id: str, candidates: list[dict],
                   integration_tier: str, budget, idempotency_key: str) -> dict:
        dataset_version_id = self._resolve_dataset_version(organization_id,
                                                           suite_version_id)
        digest = identity_digest(project_id, suite_version_id, dataset_version_id,
                                 integration_tier,
                                 *(c["modelConfigurationId"] for c in candidates))
        limit, currency = (budget if budget else (None, None))
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RunRepository(conn, organization_id)
            run_id = repo.create_run(
                project_id=project_id, suite_version_id=suite_version_id,
                dataset_version_id=dataset_version_id, identity_digest=digest,
                integration_tier=integration_tier, idempotency_key=idempotency_key,
                budget_limit=limit, budget_currency=currency)
            # `add_candidate` is idempotent on (run, label), so a resubmitted
            # request converges on the same candidates rather than needing the
            # caller to distinguish "already done" from "went wrong".
            for index, spec in enumerate(candidates):
                repo.add_candidate(
                    run_id, label=spec.get("label") or f"candidate-{index + 1}",
                    model_configuration_id=spec["modelConfigurationId"],
                    prompt_version_id=spec.get("promptVersionId"),
                    endpoint_kind=spec.get("endpointKind", "hosted"))
            return self._present(repo.get_run(run_id), repo)

    def cancel_run(self, organization_id: str, run_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RunRepository(conn, organization_id)
            run = repo.get_run(run_id)
            if run is None:
                return None
            if run.execution_state != "terminal":
                # REQ-F-07-7: cancellation leaves a consistent, clearly
                # incomplete record. It never rewrites completed samples.
                repo.finish_run(run_id, "cancelled",
                                "cancelled by request; samples already recorded "
                                "remain valid and no further work was started")
            return self._present(repo.get_run(run_id), repo)

    # ------------------------------------------------------------------- read
    def get_run(self, organization_id: str, run_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RunRepository(conn, organization_id)
            run = repo.get_run(run_id)
            return self._present(run, repo) if run else None

    def list_samples(self, organization_id: str, run_id: str, limit: int,
                     offset: int) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RunRepository(conn, organization_id)
            if repo.get_run(run_id) is None:
                return None
            items = repo.list_samples(run_id, limit, offset)
            return {"items": items, "limit": limit, "offset": offset}

    # ------------------------------------------------------------ presentation
    @staticmethod
    def _present(run, repo) -> dict:
        total, currency = repo.cost_total(run.id)
        counts = repo.sample_counts(run.id)
        body = {
            "id": run.id,
            "projectId": run.project_id,
            "identity": {"digest": run.identity_digest},
            # Null until terminal. A run still executing has not ended in any of
            # the five ways `completeness` enumerates, and calling it `partial`
            # would report the opposite of what it is.
            "completeness": run.completeness,
            "executionState": run.execution_state,
            "reproducibility": run.reproducibility,
            "createdAt": run.created_at.isoformat() if run.created_at else None,
            "sampleCounts": counts,
        }
        if run.incomplete_reason:
            body["incompleteReason"] = run.incomplete_reason
        if run.correlation_id:
            body["correlationId"] = run.correlation_id
        if total is not None:
            body["cost"] = {"total": str(Decimal(total)),
                            "currency": currency or run.budget_currency or "USD"}
        return body
