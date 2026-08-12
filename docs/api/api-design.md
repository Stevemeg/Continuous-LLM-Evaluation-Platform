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
| ~~Analytics, trends, leaderboards, scorecards~~ | **Delivered in Phase 11** (`REQ-F-11-1` … `REQ-F-11-8`, `REQ-F-10-4`) |
| ~~Canary and post-deployment evaluation~~ | **Delivered in Phase 10**, executed in Phase 11 |
| Custom evaluator registration and upload | Phase 5, behind the evaluator SDK |
| Alert **delivery** — webhooks, email, paging | Not owned by any phase yet. `REQ-F-11-9` asks the product to *alert on defined conditions*, and Phase 11 delivers that: rules, evaluation, and an audited firing record readable through `listAlertEvents`. Outbound delivery is an egress capability with its own threat surface, and `REQ-F-10-3` keeps the platform from acting on a production system. Nothing in `CAP-11` requires it, so it is absent rather than invented. |
| Authentication issuance and rotation flows | Phase 12 |
| Bulk export | Not required by any Phase 1 requirement |

Each is absent because no requirement needs it, not because it was overlooked. Adding a speculative endpoint would be the unearned complexity canonical §22 and `PR-7` both reject.

## Analytics are derived, never stored

Phase 11 adds twelve operations and no aggregate table. Every figure — a trend point, a leaderboard row, a latency quantile, a judge agreement rate — is computed on read from `run_sample`, `evaluator_outcome`, `sample_cost`, `consensus_result` and `trajectory_step`.

That is `REQ-F-11-6` made structural. A stored aggregate is a figure whose provenance is a previous computation; asked *which samples produced this*, it can only answer *the ones that were there when the job ran*. Every analytics response therefore carries the runs it was computed from and the observation count behind it, because it has just read them.

`EvidenceCompleteness` travels with each figure for `REQ-F-11-7`: a mean over a run that was cancelled halfway is not a mean of that run, and the qualification is part of the figure rather than a property some views happen to render.
