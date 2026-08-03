"""Re-running a past evaluation from its captured identity.

`REQ-F-07-3`: "shall re-run a past evaluation from its captured identity and
shall report any element that could not be reconstructed."

The second half is the requirement. Re-running is easy; the failure mode this
guards against is a reproduction that quietly substitutes what it can still find
— the current version of a prompt, today's dataset, a different evaluator build —
and reports success. That produces a number that looks like a reproduction and is
not one, which is worse than reporting nothing at all.

So every component of the original identity is checked for presence AND for
content, and anything that has moved is named in a gap with a reason. A
reproduction with gaps is `partially_reproducible`; one where nothing survives is
`not_reproducible` and no replay run is created.
"""
from __future__ import annotations

from dataclasses import dataclass

from clep.experiments.identity import IDENTITY_KINDS, RunIdentity
from clep.experiments.repository import IdentityRepository
from clep.identity import ulid_to_uuid

#: How a component of the original identity can fail to be reconstructible.
#: Mirrors ck_reproduction_gap__reason; a value not in the schema's list would be
#: rejected on insert.
COMPONENT_ABSENT = "component_absent"
CONTENT_ERASED = "content_erased"
DIGEST_MISMATCH = "digest_mismatch"
ENVIRONMENT_CHANGED = "environment_changed"

#: Which tables hold the content each identity component names, and the column
#: carrying its digest. `seed` and `integration_tier` are their own content —
#: the value is the thing — so they are not resolvable and never absent.
RESOLVERS = {
    "dataset_version": ("dataset_version", "content_digest"),
    "prompt_version": ("prompt_version", "content_digest"),
    "model_configuration": ("model_configuration", "content_digest"),
    "system_version": ("system_version", "content_digest"),
    "suite_version": ("suite_version", "content_digest"),
    "evaluator_version": ("evaluator_version", "content_digest"),
}


@dataclass(frozen=True)
class ReproductionReport:
    outcome: str
    gaps: tuple[dict, ...]

    @property
    def is_faithful(self) -> bool:
        return self.outcome == "reproducible"


def assess(conn, organization_id: str, identity: RunIdentity, *,
           current_environment: RunIdentity | None = None) -> ReproductionReport:
    """Compare a captured identity against what the store holds today.

    Read-only. Deciding whether a reproduction is possible must not itself change
    anything, or an attempt would perturb the thing it is measuring.
    """
    org = str(organization_id)
    gaps: list[dict] = []

    for component in identity.components:
        if component.kind == "environment":
            if current_environment is None:
                continue
            current = {c.digest for c in current_environment.components
                       if c.kind == "environment"}
            if current and component.digest not in current:
                # Recorded, not fatal. ADR-014 keeps the environment out of the
                # identity digest precisely so that a different host is still the
                # same measurement; it is reported so a reviewer can weigh it.
                gaps.append(_gap(component, ENVIRONMENT_CHANGED))
            continue

        target = RESOLVERS.get(component.kind)
        if target is None:
            continue

        table, digest_column = target
        row = conn.execute(
            f"SELECT {digest_column} FROM clep.{table} "
            f"WHERE id = %s AND (organization_id = %s OR organization_id IS NULL)",
            (ulid_to_uuid(component.ref), org)).fetchone()
        if row is None:
            gaps.append(_gap(component, COMPONENT_ABSENT))
        elif row[0] != component.digest:
            gaps.append(_gap(component, DIGEST_MISMATCH))

    return ReproductionReport(outcome=_outcome(identity, gaps),
                              gaps=tuple(gaps))


def erased_content_gaps(conn, organization_id: str,
                        dataset_version_ref: str) -> list[dict]:
    """Examples whose content is no longer available (I-8).

    The example record survives erasure and its content does not, so a dataset
    version can be entirely present while the thing it pointed at is gone. A
    reproduction that did not look would re-run against a smaller dataset and
    report a clean result.

    Two conditions count, and the schema distinguishes them: a content row marked
    `erased_at`, which is the audited privacy case, and no content row at all,
    which means the content was never recorded. Either way the example cannot be
    re-evaluated, and either way silently proceeding would overstate the
    reproduction.
    """
    row = conn.execute(
        "SELECT count(*) FROM clep.example e "
        "LEFT JOIN clep.example_content c "
        "  ON c.organization_id = e.organization_id AND c.example_id = e.id "
        "WHERE e.organization_id = %s AND e.dataset_version_id = %s "
        "  AND (c.example_id IS NULL OR c.erased_at IS NOT NULL)",
        (str(organization_id), ulid_to_uuid(dataset_version_ref))).fetchone()
    missing = int(row[0]) if row else 0
    if not missing:
        return []
    return [{"componentKind": "dataset_version", "componentRef": dataset_version_ref,
             "reason": CONTENT_ERASED,
             "detail": f"{missing} example(s) have no content"}]


def reproduce(conn, organization_id: str, original_run_id: str, *,
              current_environment: RunIdentity | None = None,
              replay: bool = False) -> dict:
    """Assess a run's reproducibility and record the attempt.

    `replay` is False by default: Phase 6 delivers the identity model and the
    honest report. Executing the replay run is the orchestrator's job and is
    driven by the existing Phase 5 worker, so this records the assessment and the
    replay run identifier when one is supplied rather than starting execution
    from inside a read path.
    """
    repository = IdentityRepository(conn, organization_id)
    identity = repository.components_of(original_run_id)
    report = assess(conn, organization_id, identity,
                    current_environment=current_environment)

    gaps = list(report.gaps)
    for component in identity.components:
        if component.kind == "dataset_version":
            gaps.extend(erased_content_gaps(conn, organization_id, component.ref))

    outcome = _outcome(identity, gaps)
    replay_run_id = original_run_id if (replay and outcome != "not_reproducible") \
        else None
    attempt_id = repository.record_attempt(
        original_run_id=original_run_id, replay_run_id=replay_run_id,
        outcome=outcome, gaps=gaps)
    return {"id": attempt_id, "outcome": outcome,
            "gaps": [{k: v for k, v in g.items() if k != "detail"} for g in gaps]}


def _gap(component, reason: str) -> dict:
    return {"componentKind": component.kind, "componentRef": component.ref,
            "reason": reason}


def _outcome(identity: RunIdentity, gaps: list[dict]) -> str:
    if not gaps:
        return "reproducible"
    resolvable = [c for c in identity.components if c.kind in RESOLVERS]
    blocking = {(g["componentKind"], g["componentRef"]) for g in gaps
                if g["reason"] != ENVIRONMENT_CHANGED
                and g["componentKind"] in IDENTITY_KINDS}
    if resolvable and len(blocking) >= len(resolvable):
        # Nothing the identity names can still be resolved. There is no honest
        # replay to run, so no replay run is recorded — the schema's
        # ck_reproduction_attempt__replay_matches_outcome enforces that pairing.
        return "not_reproducible"
    return "partially_reproducible"
