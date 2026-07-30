# ADR-005 — Golden dataset immutability and versioning

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M2.3 |
| Canonical basis | §10, §19, §25 |
| Requirements | `REQ-F-05-1`, `REQ-F-05-3`, `REQ-F-05-5`, `REQ-F-05-8`, `REQ-F-05-9`, `REQ-F-07-1`, `REQ-X-4` |

## Context

A gate decision is only as reproducible as the dataset it ran against. Canonical §25 names "Unversioned datasets/prompts/models/rubrics/evaluators" as an anti-pattern, and §10 requires immutable snapshots for released benchmarks.

Two requirements collide. `REQ-F-05-1` makes a released snapshot immutable. `REQ-F-05-8` requires deletion requests to be honourable. A design that satisfies only one of these either breaks reproducibility or breaks an enforceable obligation.

## Decision

**Content and record are separate, and only content is destructible.**

| Element | Mutability | Contains |
|---|---|---|
| Dataset version record | Immutable once released | Version identity, schema, ownership, approval state, lineage, provenance, per-example identity and schema position |
| Example content | Destructible on an audited deletion request | The payload — text, structured input, expected output |

Consequences of the split, stated as rules:

1. **A released version's record never changes.** Adding, removing, or editing examples produces a new version (`REQ-F-05-1`).
2. **Draft versions are freely mutable**; the transition to released is the immutability boundary, and it requires recorded human approval (`REQ-F-05-5`).
3. **Version identity is content-derived**, so two versions with identical content and schema are identifiable as such, and a claim that a run used version *v* is verifiable rather than asserted (`REQ-F-07-1`).
4. **Erasure destroys content and demotes, never rewrites.** Runs referencing erased content move from *reproducible* to *auditable*: the decision, its evidence, and its audit trail survive intact, and replay reports that it cannot reconstruct the input (`REQ-F-05-8`, `REQ-F-07-3`).
5. **A referenced version cannot be deleted while an active baseline pins it**; override is explicit and audited (`REQ-F-05-9`).
6. **Schema is enforced per version**, and violating examples are rejected rather than coerced (`REQ-F-05-3`).

## Why demotion rather than the alternatives

| Alternative | Why rejected |
|---|---|
| Refuse erasure to preserve immutability | Makes an enforceable obligation unmeetable. Not a design choice the platform is entitled to make. |
| Delete the run records too | Destroys the audit trail `REQ-N-COMP-1` requires, and erases the record of a release decision that really happened. |
| Silently substitute a placeholder and keep claiming reproducibility | Reproducibility would become a false claim. The worst option: it preserves the appearance of the property while removing the property. |
| Tombstone content but keep a hash | Retained as an open question for [ADR-011](ADR-011-artifact-retention.md); a hash may itself be regulated data depending on content and jurisdiction. |

Demotion is chosen because it makes the loss **visible**. A reviewer can see that a past decision is no longer replayable, and why. Every other option either denies the obligation or hides the consequence.

## Consequences

- Two lifecycles to implement and test: version records and example content.
- Replay (`REQ-F-07-3`) must report partial reconstruction as a first-class result, not an error.
- Artifacts derived from an example must be locatable from the example identity, since `REQ-N-PRIV-4` extends deletion to derived traces.
- A run's reproducibility status becomes queryable state rather than an assumption.
