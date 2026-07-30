# ADR-013 — Artifact store

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M4.1 |
| Canonical basis | §15 (artifacts: S3-compatible storage, local object store), §9, §10 |
| Constrained by | [ADR-011](ADR-011-artifact-retention.md), [ADR-012](ADR-012-primary-datastore.md) |
| Requirements | `REQ-F-07-2`, `REQ-N-PRIV-4`, `REQ-F-05-8`, `REQ-N-SCALE-1`, `REQ-N-PERF-3` |

## Context

Artifacts are per-sample objects: inputs as evaluated, outputs, judge rationales, traces. The Phase 3 volume model shows they grow as the product of five factors — runs, examples, candidates, evaluators and judges — which makes them the largest data in the system by a wide margin, and the reason analytics must never scan them.

The question is not which vendor but **where artifacts live relative to the relational store**, because that decision determines whether erasure and analytics both remain tractable.

## Decision

**An S3-compatible object store for artifact payloads; the relational store holds only artifact metadata and references.**

| # | Rule | Basis |
|---|---|---|
| O-1 | Payloads live in the object store; the database holds identity, class, references and retention state. | Volume model: putting payloads in rows makes every table scan pay for them |
| O-2 | Objects are addressed by content digest, **scoped per tenant**. Identical content in two tenants is stored twice. | `REQ-F-12-5`, artifact-model rule A-7 |
| O-3 | Every object carries a reference from the `example_content` digest it derives from. | ADR-011 constraint; makes `REQ-N-PRIV-4` erasure an indexed lookup rather than a scan |
| O-4 | Deletion is by digest with reference counting. An object is destroyed when its last reference is removed. | Content addressing deduplicates within a tenant, so delete-on-first-reference would destroy an artifact another run legitimately holds |
| O-5 | Object lifecycle policies are **not** the mechanism for erasure. Erasure is an explicit, audited, verified operation. | `REQ-N-PRIV-3` requires deletion within a defined period and auditable; a background lifecycle sweep is neither |
| O-6 | The store is treated as eventually consistent for listing, and the database is authoritative for existence. | A missing object with a live reference is a detectable defect; an orphan object is reclaimable |
| O-7 | Local development uses an API-compatible local object store, not a filesystem stub. | `REQ-N-OPS-1` requires the full validation suite to run locally against real backing services |

## Why not the database

Storing payloads in the relational store would satisfy consistency trivially and fail on everything else: table sizes dominated by opaque blobs, analytics queries paying for data they never read, backup and restore times driven by artifact volume, and `REQ-N-PERF-3` responsiveness degrading as history grows rather than as query complexity grows.

**The decisive argument is erasure, not size.** ADR-011 makes content-class artifacts erasable and decision-class records permanent. Keeping them in one store means one deletion path serving two retention regimes, and the regime that yields under pressure would be the compliance one.

## Why object lifecycle rules are not erasure (O-5)

An object-store lifecycle policy is a background process with no completion signal, no audit record, and no per-object confirmation. `REQ-N-PRIV-3` requires deletion executable within a defined period and auditable; `REQ-N-PRIV-4` requires it to reach every derivative. A lifecycle rule can support *retention expiry*, but an erasure request must be a foreground operation that verifies and records what it destroyed.

Conflating the two is how a platform ends up reporting that data was deleted when a policy merely scheduled it.

## Consequences

- Two stores to operate, back up and reason about, with the database authoritative for existence (O-6).
- Erasure spans both stores and must be ordered: demote, destroy, verify, record — as specified in the artifact model.
- Per-tenant scoping forgoes cross-tenant deduplication, a real storage cost accepted for an isolation guarantee.
- Reference counting must be transactional with respect to the database, or an object could be destroyed while a reference survives.
- Local development requires the object store running, not stubbed.

## What this ADR does not decide

Vendor, deployment topology, bucket layout, encryption-at-rest mechanism, replication and durability class. Those are Phase 14 infrastructure decisions. This ADR fixes the split and the erasure semantics, which are the parts that constrain the schema.
