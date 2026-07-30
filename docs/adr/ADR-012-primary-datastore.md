# ADR-012 — Primary datastore

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M4.1 |
| Canonical basis | §15 (technology baseline, ADR-backed not a blind mandate), §16, §17 |
| Constrained by | [ADR-010](ADR-010-multi-tenancy.md) — a store that cannot enforce per-tenant access below the application layer under a non-bypassing runtime principal is **disqualified** |
| Requirements | `REQ-F-12-5`, `REQ-N-SEC-2`, `REQ-N-SCALE-2`, `REQ-N-SCALE-3`, `REQ-N-COST-1` |
| Evidence | PostgreSQL documentation, row security, retrieved 2026-07-30 |

## Context

Canonical §15 names PostgreSQL as the primary database baseline but is explicit that the baseline is ADR-backed rather than mandated. ADR-010 then imposed a hard disqualifier that no store passes automatically: tenancy must be enforced **by the store**, and the runtime principal must not be able to bypass that enforcement.

Phase 4 needs this settled, because the Golden Dataset and Benchmark Registry schema cannot be specified without knowing whether the store can carry the isolation guarantee.

## Decision

**PostgreSQL, subject to four conditions that are part of this decision and not optional configuration.**

| # | Condition | Why it is mandatory |
|---|---|---|
| D-1 | `ENABLE ROW LEVEL SECURITY` on every tenant-scoped table | Without it no policy is consulted at all |
| D-2 | `FORCE ROW LEVEL SECURITY` on every tenant-scoped table | Without it the **table owner bypasses policies** |
| D-3 | Runtime role is `NOSUPERUSER` **and** `NOBYPASSRLS`, and does not own the tables | Superusers and `BYPASSRLS` roles always bypass |
| D-4 | Migration role is a distinct role that owns the schema | Separating ownership from runtime is what makes D-2 and D-3 meaningful |

## Evidence, and the trap it exposes

From the PostgreSQL documentation:

> "When row security is enabled on a table (with `ALTER TABLE ... ENABLE ROW LEVEL SECURITY`), all normal access to the table for selecting rows or modifying rows must be allowed by a row security policy."

> "Superusers and roles with the `BYPASSRLS` attribute always bypass the row security system when accessing a table. **Table owners normally bypass row security as well, though a table owner can choose to be subject to row security with `ALTER TABLE ... FORCE ROW LEVEL SECURITY`.**"

**`ENABLE` alone does not satisfy ADR-010.** A table owner bypasses policies by default, so an application connecting as the owner of its own tables would read across tenants while every policy appears correctly defined. Worse, the defect is invisible in the obvious test: isolation tests executed as a non-owner role pass, and production connecting as owner does not isolate.

This is precisely the failure mode ADR-010 rule 1 exists to prevent — isolation that depends on a condition nobody restated — and it is the reason D-2 and D-4 are conditions of the decision rather than deployment advice.

`REQ-N-SEC-2` requires cross-tenant attempts to **fail**. Under D-1 through D-4 they fail in the database, on every statement, regardless of what the application forgot.

## Why PostgreSQL satisfies the rest

| Need | Basis |
|---|---|
| Store-enforced tenancy under a non-bypassing principal | Row-level security with `FORCE`, per the evidence above |
| Exact decimal arithmetic for cost reconciliation (`REQ-N-COST-1`, N-9) | Exact numeric type; cost must not be binary floating point |
| Constraint-expressible invariants | Check, unique, foreign key and exclusion constraints carry the M3.1 invariants into the store |
| Tenant addition without schema change (`REQ-N-SCALE-3`) | Shared schema with a tenant discriminator; adding a tenant inserts a row |
| Append-only audit (`REQ-N-COMP-3`) | Privilege separation plus revoked update and delete on audit tables |
| Content-addressed identity | Deterministic digest columns with unique constraints |

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| A store without row-level access control, isolation in application code | Disqualified by ADR-010 rule 1. Reintroduces the silent missed-predicate failure. |
| Document store | Tenancy would be application-enforced, and the invariants in M3.1 are relational constraints; expressing them in application code makes them advisory. |
| Database per tenant | Violates `REQ-N-SCALE-3`: adding a tenant becomes a provisioning operation. Already rejected in ADR-010. |
| Defer the decision further | Phase 4 cannot specify a schema without it, and the disqualifier is decidable from documented capability without a spike. |

## Consequences

- Two database roles must exist from the first migration, and local development must reproduce both. A single-role setup would silently disable the guarantee.
- Every tenant-scoped table requires its own policy; a table added without one is a leak. This is a standing conformance check, not a review item.
- The runtime role cannot run migrations, so schema change is a separate operational path.
- Isolation tests must execute as the **runtime** role. A test running as owner or superuser proves nothing, which is the trap above in test form.

## What this ADR does not decide

Physical types, indexes, partitioning, connection pooling, and version. Extensions are out of scope: none is required by any current requirement, and adding one would be unearned complexity under `PR-7`.
