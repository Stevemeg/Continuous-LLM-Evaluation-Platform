# ADR-010 — Multi-tenancy isolation

| Field | Value |
|---|---|
| Status | **Accepted** (enforcement location and model). Concrete datastore mechanism confirmed when the store is confirmed. |
| Milestone | M2.3 |
| Canonical basis | §16, §17, §19, §21 |
| Requirements | `REQ-F-12-1`, `REQ-F-12-5`, `REQ-F-12-7`, `REQ-F-12-8`, `REQ-F-06-4`, `REQ-N-SEC-2`, `REQ-N-SCALE-2`, `REQ-N-SCALE-3` |

## Context

`REQ-F-12-5` requires that no request executing in one tenant's context can read or modify another tenant's records. `REQ-N-SEC-2` requires cross-tenant attempts to fail **and** be audited. Canonical §21 lists "Cross-tenant leakage attempts" as a failure mode to design for.

The design question is not whether to isolate but **where isolation is enforced**. Every record the platform creates belongs to a tenant, so a single missed predicate anywhere in the application is a silent cross-tenant read — a class of bug that produces no error and may be discovered only by an auditor.

## Decision

1. **Shared schema with isolation enforced by the datastore, not by application query construction.** Tenant scoping is a property the store enforces on every access; application code cannot opt out by omission.
2. **The application connects under a runtime principal that cannot bypass the enforcement**, distinct from the principal used for schema migration. A runtime principal able to bypass isolation makes the enforcement advisory.
3. **Tenant context is established once per request at the ingress boundary** and carried, never re-derived from caller-supplied data deeper in the stack.
4. **A small, explicitly enumerated set of records is genuinely global** — for example role definitions and feature flags. The set is enumerated in the design so that "no tenant column" is a decision rather than an oversight, and any addition to it is a reviewable change.
5. **Cross-tenant attempts fail closed and emit an audit event** (`REQ-N-SEC-2`).
6. **Isolation is verified by negative tests per record type**, asserting both failure and audit emission. Isolation claimed but untested is not isolation.
7. **Adding a tenant requires no schema or deployment change** (`REQ-N-SCALE-3`).
8. **Isolation is available in every deployment configuration**, never gated by tier (`REQ-F-12-8`).
9. **Sharing is an explicit, audited grant, never an implicit default.** A benchmark suite is project-scoped and shareable only within its tenant (`REQ-F-06-4`); approvals are recorded with actor, time and artifact version (`REQ-F-12-7`). Cross-tenant sharing is not expressible.

## Rationale

Enforcement at the persistence boundary is chosen because it is the only location where the failure mode is *structurally* prevented rather than prevented by discipline. Application-layer filtering requires every query, now and in future, to remember the predicate; the failure is silent and the blast radius is a confidentiality breach across tenants.

Rule 2 is what makes rule 1 real. Datastore-enforced isolation that the application's own principal can bypass provides defence against accidental omission but not against a compromised or careless code path, and `REQ-N-SEC-2` asks for the attempt to *fail*, not to be discouraged.

## Deferred

The concrete enforcement mechanism depends on the datastore, whose selection canonical §15 makes ADR-backed. Rules 1 and 2 constrain that selection: **a candidate store that cannot enforce per-tenant access below the application layer, under a non-bypassing runtime principal, is disqualified.** This ADR therefore constrains a later decision rather than waiting on it.

## Consequences

- The runtime and migration principals differ, which constrains deployment and local development setup.
- Negative isolation tests become a standing requirement per record type, growing with the data model.
- The global-record set is a reviewable artifact; additions require justification.
- Analytics and reporting must operate within tenant scope, which bears on `REQ-N-PERF-3`.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Application-layer tenant filtering | A missed predicate is a silent cross-tenant read. Relies on discipline where a structural guarantee is available. |
| Database per tenant | Satisfies isolation strongly but violates `REQ-N-SCALE-3` — adding a tenant becomes a provisioning operation — and multiplies migration and operational cost. |
| Schema per tenant | Same provisioning objection, plus migration complexity scaling with tenant count. |
| Isolation as an enterprise-tier feature | Directly violates `REQ-F-12-8`, and contradicts the positioning pillar that governance is not tier-gated. |
