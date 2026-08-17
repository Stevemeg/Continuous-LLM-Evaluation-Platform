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
from clep.experiments.capture import build_run_identity
from clep.experiments.identity import IDENTITY_KINDS
from clep.experiments.repository import IdentityRepository
from clep.identity import new_ulid
from clep.orchestration.repository import RunRepository
from clep.security.repository import SecurityRepository
from clep.telemetry.correlation import current_id


class QuotaExhausted(RuntimeError):
    """`REQ-N-SEC-9`. A refusal the caller can act on, never a platform failure:
    the tenant asked for more than their configured allowance, which is a
    successful decision about a request rather than the platform breaking."""


def identity_digest(*parts: str) -> str:
    """Digest over ordered parts. Retained for callers that already hold the
    material; `build_run_identity` is what a run should use, because it reads the
    stored content digests rather than trusting identifiers."""
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
        limit, currency = (budget if budget else (None, None))
        with tenant_session(self._dsn, organization_id) as conn:
            # Built before the run exists. An identity assembled afterwards could
            # not stop a run from being created against a component that is not
            # there, and REQ-F-07-1 requires the identity to be frozen *before*
            # execution rather than reconstructed once it is too late to refuse.
            identity = build_run_identity(
                conn, organization_id, suite_version_id=suite_version_id,
                dataset_version_id=dataset_version_id,
                integration_tier=integration_tier, candidates=candidates)
            digest = identity.digest()

            repo = RunRepository(conn, organization_id)
            # The evaluation quota, counted here rather than at the route, and
            # counted only for a submission that is actually new (`REQ-N-SEC-9`).
            # At the route it would have been charged before the idempotency key
            # was consulted, so a client retrying a timed-out POST — the case
            # idempotency exists for — would have spent quota twice for one run.
            if repo.find_run_by_idempotency_key(project_id,
                                                idempotency_key) is None:
                allowed, used, ceiling = SecurityRepository(
                    conn, organization_id).consume_run_quota()
                if not allowed:
                    raise QuotaExhausted(
                        f"evaluation quota reached: this tenant has started "
                        f"{used} run(s) against a quota of {ceiling} for the "
                        f"current period. The quota is configurable through "
                        f"PUT /usage-limit.")
            run_id = repo.create_run(
                project_id=project_id, suite_version_id=suite_version_id,
                dataset_version_id=dataset_version_id, identity_digest=digest,
                integration_tier=integration_tier, idempotency_key=idempotency_key,
                budget_limit=limit, budget_currency=currency,
                # The chain's root. `run.correlation_id` has been declared since
                # Phase 5 and written by nothing, which is why the contract's
                # `correlationId` has never appeared in a response. It is the
                # platform's identifier, not the caller's (ADR-022 rule 4).
                correlation_id=current_id())
            IdentityRepository(conn, organization_id).capture(run_id, identity)
            # `add_candidate` is idempotent on (run, label), so a resubmitted
            # request converges on the same candidates rather than needing the
            # caller to distinguish "already done" from "went wrong".
            for index, spec in enumerate(candidates):
                repo.add_candidate(
                    run_id, label=spec.get("label") or f"candidate-{index + 1}",
                    model_configuration_id=spec["modelConfigurationId"],
                    prompt_version_id=spec.get("promptVersionId"),
                    endpoint_kind=spec.get("endpointKind", "hosted"))
            return self._present(repo.get_run(run_id), repo,
                                 IdentityRepository(conn, organization_id))

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
            return self._present(repo.get_run(run_id), repo,
                                 IdentityRepository(conn, organization_id))

    # ------------------------------------------------------------------- read
    def get_run(self, organization_id: str, run_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RunRepository(conn, organization_id)
            run = repo.get_run(run_id)
            identity_repo = IdentityRepository(conn, organization_id)
            return self._present(run, repo, identity_repo) if run else None

    def list_samples(self, organization_id: str, run_id: str, limit: int,
                     offset: int) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RunRepository(conn, organization_id)
            if repo.get_run(run_id) is None:
                return None
            items = repo.list_samples(run_id, limit, offset)
            return {"items": items, "limit": limit, "offset": offset}

    def run_identity(self, organization_id: str, run_id: str) -> dict | None:
        """The captured identity, component by component (REQ-F-07-1).

        The summary on the run answers "are these two comparable". This answers
        "what exactly did it measure", which is what a reproduction needs and
        what a digest cannot provide.
        """
        with tenant_session(self._dsn, organization_id) as conn:
            if RunRepository(conn, organization_id).get_run(run_id) is None:
                return None
            identity = IdentityRepository(conn, organization_id).components_of(run_id)
            return {
                "runId": run_id,
                "digest": identity.digest(),
                "components": [
                    {"kind": c.kind, "ref": c.ref, "digest": c.digest,
                     "inDigest": c.kind in IDENTITY_KINDS}
                    for c in identity.components],
            }

    # ------------------------------------------------------------ presentation
    @staticmethod
    def _present(run, repo, identity_repo=None) -> dict:
        total, currency = repo.cost_total(run.id)
        counts = repo.sample_counts(run.id)
        body = {
            "id": run.id,
            "projectId": run.project_id,
            "identity": RunService._identity_summary(run, identity_repo),
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

    @staticmethod
    def _identity_summary(run, identity_repo) -> dict:
        """Every field the contract's `RunIdentity` requires.

        Phase 5 emitted `digest` alone, which did not satisfy the schema the
        contract had already declared — the contract was right and the
        implementation was short. The plural fields come from the captured
        components rather than from the request, so what is reported is what was
        recorded rather than what was asked for.
        """
        by_kind: dict[str, list[str]] = {}
        if identity_repo is not None:
            for component in identity_repo.components_of(run.id).components:
                by_kind.setdefault(component.kind, []).append(component.ref)
        return {
            "digest": run.identity_digest,
            "datasetVersionId": run.dataset_version_id,
            "suiteVersionId": run.suite_version_id,
            "promptVersionIds": sorted(by_kind.get("prompt_version", [])),
            "modelConfigurationIds": sorted(by_kind.get("model_configuration", [])),
            "evaluatorVersionIds": sorted(by_kind.get("evaluator_version", [])),
            # Judges arrive with the agentic evaluation layer in Phase 8. An
            # empty list is the honest answer for a run that used none; omitting
            # a required field, or inventing one, would not be.
            "judgeVersionIds": [],
            "seed": _single_seed(by_kind.get("seed", [])),
            "frozenAt": run.frozen_at.isoformat() if run.frozen_at else None,
        }


def _single_seed(seeds: list[str]) -> int | None:
    """The contract's `seed` is one nullable integer.

    Candidates may carry different seeds, and reporting one of several as *the*
    seed would be a claim the run does not support. Null is reported unless every
    candidate agreed; the per-candidate values are all present in the
    component-by-component identity.
    """
    distinct = set(seeds)
    if len(distinct) != 1:
        return None
    try:
        return int(distinct.pop())
    except ValueError:
        return None
