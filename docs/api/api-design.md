# API Design Conventions

| Field | Value |
|---|---|
| Status | **Draft — pending external review** |
| Milestone | M3.4 — API Contract |
| Phase | Phase 3 — Data and Contracts |
| Contract | [`openapi.json`](openapi.json) — 13 operations, 40 schemas |
| Required by | Canonical §18 (OpenAPI 3.1 specification) |

Only the conventions and rationale the contract cannot carry itself. Operation and schema semantics live in the spec.

## Serialization: JSON, not YAML

The contract is JSON so it can be parsed and validated with the Python standard library. YAML would require a parser dependency, and Phase 3 deliberately creates no dependency manifest — that belongs to an implementation phase. OpenAPI 3.1 treats JSON as a first-class serialization, so nothing is lost but readability, and the spec is machine-checked on every validation run instead.

## The distinction that shapes the whole contract

**A quality failure is a successful API call. A platform failure is not.**

| Situation | HTTP | Body |
|---|---|---|
| Candidate regressed | `200` | `GateDecision` with `outcome: hard_fail` |
| Sample too small to judge | `200` | `GateDecision` with `outcome: insufficient_evidence` |
| Version drift invalidated the comparison | `200` | `GateDecision` with `outcome: not_comparable` |
| The platform itself failed | `503` | `Problem` with `category: platform_failure` |

`GateOutcome` deliberately has **no** `platform_failure` member. It is unrepresentable as a gate outcome, so no client can mistake one for the other and no server can accidentally emit one. `REQ-F-09-5` and `REQ-X-10` are enforced by the shape of the type rather than by convention.

This is the contract-level expression of the trust property: a developer told their change regressed when in fact the service was unavailable stops believing every later verdict.

## Errors

RFC 9457 problem details (`application/problem+json`), extended with `category` ∈ {`client_error`, `authorization`, `platform_failure`}. `detail` states which stage failed and what the caller can do about it (`REQ-N-USE-3`).

`404` rather than `403` is returned for resources outside the caller's tenant scope, so existence is not disclosed across a tenant boundary. Genuine authorization failures within scope return `403` and are audited (`REQ-N-SEC-2`).

## Idempotency

`Idempotency-Key` is **required** on run creation and gate evaluation, not optional. `REQ-N-REL-2` requires duplicate delivery to leave results and cost accounting unchanged, and CI systems retry. Making the header optional would make the guarantee optional.

## Correlation

`X-Correlation-Id` is accepted and generated when absent. It is the root of the chain `REQ-N-OBS-1` requires through workflow, model call, evaluator, judge, artifact, and gate decision, and it is echoed on `Problem` responses so a failed call is traceable.

## Versioning

The version is in the path (`/v1`). Additive changes — new optional fields, new enum members on response-only enums, new operations — are minor. Any change that could break a client is a new path version.

**Enum members on `Completeness`, `Classification`, and `GateOutcome` are treated as breaking to remove and additive to add.** Clients switch on these values to decide whether to block a release, so silently removing one changes release behaviour in a caller's pipeline.

## Pagination

Cursor-based, not offset. The largest collections grow as the product of five factors (see the data-volume model), and offset pagination over such tables degrades and can skip or repeat rows under concurrent insertion.

## Exact numerics

Money and token quantities are strings matching a decimal pattern, never JSON numbers. `REQ-N-COST-1` requires attributed cost to reconcile against provider-reported usage, and IEEE 754 doubles cannot represent decimal money exactly. Making them strings prevents a client's JSON parser from silently introducing error.

## Deliberately absent from this contract

| Absent | Owner |
|---|---|
| Analytics, trends, leaderboards, scorecards | Phase 11 |
| Canary and post-deployment evaluation | Phase 10 |
| Custom evaluator registration and upload | Phase 5, behind the evaluator SDK |
| Webhooks and alert delivery | Phase 11 |
| Authentication issuance and rotation flows | Phase 12 |
| Bulk export | Not required by any Phase 1 requirement |

Each is absent because no Phase 3 requirement needs it, not because it was overlooked. Adding a speculative endpoint would be the unearned complexity canonical §22 and `PR-7` both reject.
