# ADR-009 — Vendor-neutral observability core with optional adapters

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M2.3 |
| Canonical basis | §14, §19 |
| Requirements | `REQ-N-OBS-1`, `REQ-N-OBS-2`, `REQ-N-OBS-3`, `REQ-N-OBS-4` |
| Supporting evidence | [`../product/competitive-analysis.md`](../product/competitive-analysis.md) §3.6 (licence finding, source `S-08`) |

## Context

Canonical §14 is directive: a vendor-neutral standard is the foundation, specific vendor platforms are to be evaluated as *optional* integrations, and "the core platform must not depend on a proprietary tracing vendor." `REQ-N-OBS-3` restates this as a testable requirement: the core must function with every vendor adapter excluded.

## Decision

1. **The core emits telemetry through a vendor-neutral instrumentation standard.** No vendor-specific client appears in the domain or in any core container.
2. **Vendor integrations are adapters behind a port**, individually optional and individually removable.
3. **A build excluding every vendor adapter must pass the full validation suite.** This is how `REQ-N-OBS-3` is verified rather than asserted, and it is a build configuration, not a documentation claim.
4. **Correlation is a core responsibility, not a vendor feature.** `REQ-N-OBS-1` requires a single request to be correlatable through workflow, model call, evaluator, judge, artifact, and gate decision. Correlation identifiers are produced by the core so the chain survives the removal of any adapter.
5. **Label cardinality is bounded in the core** (`REQ-N-OBS-4`), not left to a backend to absorb.
6. **Any vendor integration passes licence review before adoption.**

## The licence finding that gives rule 6 force

M1.2 established from a vendor's own repository that a prominent candidate integration, which self-describes as open source, is licensed under terms that are not OSI-approved and that restrict providing the software as a managed service to third parties (`S-08`).

Canonical §5 includes AI platform teams "exposing evaluation as an internal service" among the target users. A licence restricting service provision therefore intersects the product's own intended use directly. This is not an abstract preference for neutrality: it is a concrete case where absorbing a candidate integration into the core could constrain what the product is permitted to be.

Rule 6 exists because that finding came from reading a licence, not from reading a feature list — and no amount of capability comparison would have surfaced it.

## Consequences

- Vendor-specific features unavailable through the neutral standard are unavailable in the core, reachable only through an optional adapter that some deployments will not have.
- Two telemetry paths must be tested: core-only, and core plus each adapter.
- Correlation identifier propagation becomes a core concern crossing every container boundary, which constrains Phase 3 contract design.
- Adding a vendor integration incurs a licence review as a gating step, not a later formality.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Adopt a vendor platform as the primary observability layer | Canonical §14 forbids core dependency on a proprietary tracing vendor. `REQ-N-OBS-3` would be unsatisfiable. |
| Neutral standard for metrics, vendor platform for traces | Splits correlation across two systems, so `REQ-N-OBS-1` end-to-end correlation would depend on a vendor. |
| Defer the decision to Phase 13 | The decision constrains instrumentation from the first implementation milestone; deferring guarantees retrofitting. |
