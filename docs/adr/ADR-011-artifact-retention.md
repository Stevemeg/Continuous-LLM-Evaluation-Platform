# ADR-011 — Artifact retention and reproducibility

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M2.3 |
| Canonical basis | §9, §10, §16, §19 |
| Requirements | `REQ-F-07-1`, `REQ-F-07-2`, `REQ-F-07-3`, `REQ-F-05-8`, `REQ-N-PRIV-3`, `REQ-N-PRIV-4`, `REQ-N-COMP-2`, `REQ-N-COMP-3`, `REQ-F-12-6` |

## Context

Reproducibility and erasure pull in opposite directions. `REQ-F-07-3` requires re-running a past evaluation from its captured identity. `REQ-N-PRIV-4` requires deletion to reach derived artifacts and traces, not merely the dataset record. `REQ-N-COMP-3` requires audit records retained independently and not deletable by the actors they record.

Three retention regimes are therefore in play, and treating them as one is how a platform ends up either unable to honour deletion or unable to explain a past decision.

## Decision

**Three independent retention classes, with reproducibility as queryable state rather than an assumption.**

| Class | Contents | Retention | Deletable |
|---|---|---|---|
| **Audit** | Governance events: approvals, gate decisions, policy exceptions, access attempts | Independent floor, per `REQ-N-COMP-3` | No. Append-only, not deletable by the actors it records |
| **Decision record** | Run identity, aggregate metrics, gate verdict, policy and method versions | Tenant retention policy, but not below the audit floor | Only by tenant retention policy |
| **Content artifact** | Per-sample inputs, outputs, traces, judge rationales | Tenant retention policy | Yes, and on an erasure request |

Consequences of the split, stated as rules:

1. **Erasure destroys content artifacts and leaves decision records intact.** The affected run is **demoted from reproducible to auditable** (`REQ-F-05-8`, and consistent with [ADR-005](ADR-005-dataset-immutability.md)).
2. **Reproducibility status is stored, queryable state** on every run — not inferred at replay time. `REQ-N-COMP-2` requires the reproducibility window to be explicit rather than incidental, and a status field is what makes it explicit.
3. **Artifacts are indexed by the example identity they derive from**, so `REQ-N-PRIV-4` deletion can reach every derived trace without scanning.
4. **Replay reports partial reconstruction as a first-class result** (`REQ-F-07-3`), naming what it could not reconstruct.
5. **Audit retention cannot be lowered by a tenant retention policy** (`REQ-F-12-6` is explicitly subordinate to `REQ-N-COMP-3`).
6. **Deletion is executable within a defined period and is itself audited** (`REQ-N-PRIV-3`). The period is a policy input, not an engineering choice, and remains unset.

## Open question, deliberately not closed

Whether a **content hash may be retained** after erasure — useful for verifying that a replay input matches the original — is not decided here. A hash of personal data may itself be regulated depending on content and jurisdiction, and that is a legal question rather than an architectural one. Recorded as requiring external input.

Deciding it either way now would be guessing on a compliance matter, and the guess would be invisible in the architecture until it mattered.

## Consequences

- Three retention mechanisms to implement, configure, and test, rather than one.
- Every run carries reproducibility status, which reporting must surface (`REQ-X-1` applies: a demoted run must not present as fully reproducible).
- The artifact index by example identity is a hard requirement on the Phase 3 data model, not an optimisation.
- The audit store's independence constrains deployment: it cannot share a delete path with tenant data.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Single retention policy for everything | Cannot satisfy `REQ-N-COMP-3` independence and erasure simultaneously. |
| Never delete artifacts, rely on access control | Does not honour an erasure obligation; access control is not deletion. |
| Delete decision records with content | Destroys the audit trail `REQ-N-COMP-1` requires and erases the record of a decision that really occurred. |
| Infer reproducibility at replay time | Makes the reproducibility window implicit, which `REQ-N-COMP-2` forbids, and turns a query into a full reconstruction attempt. |
