# Architecture Decision Records

Phase 2 — Architecture. Canonical §19 fixes the required ADR topics; all eleven are present.

| ADR | Topic | Status |
|---|---|---|
| [ADR-001](ADR-001-durable-execution.md) | Durable execution for long-running evaluations | **Proposed — gated on spike. NOT DECIDED** |
| [ADR-002](ADR-002-agent-orchestration.md) | Orchestration of reasoning components | Accepted |
| [ADR-003](ADR-003-provider-abstraction.md) | Model and provider abstraction | **Proposed — gated on spike. NOT DECIDED** |
| [ADR-004](ADR-004-judge-ensemble.md) | Judge ensemble and consensus strategy | Accepted (strategy); parameters gated |
| [ADR-005](ADR-005-dataset-immutability.md) | Golden dataset immutability and versioning | Accepted |
| [ADR-006](ADR-006-evaluator-isolation.md) | Evaluator SDK and plugin isolation | Accepted (model); mechanism deferred to Phase 5 |
| [ADR-007](ADR-007-regression-statistics.md) | Regression statistics and quality-gate semantics | Accepted (method class) on spike evidence; parameters gated |
| [ADR-008](ADR-008-tool-protocol.md) | Tool integration protocol | Accepted |
| [ADR-009](ADR-009-observability-core.md) | Vendor-neutral observability core with optional adapters | Accepted |
| [ADR-010](ADR-010-multi-tenancy.md) | Multi-tenancy isolation | Accepted (enforcement location); store mechanism constrained |
| [ADR-011](ADR-011-artifact-retention.md) | Artifact retention and reproducibility | Accepted |

## The two undecided ADRs

ADR-001 and ADR-003 are **not decided**, deliberately.

Both turn on properties that only measurement can establish — durable-execution replay and exactly-once semantics under induced worker loss, and provider-abstraction error granularity and per-call usage detail under induced failure. Both spikes require infrastructure absent from the current environment: container infrastructure and a message broker for one, provider credentials and at least two endpoint types for the other.

The governing execution model is explicit that technology must not be chosen merely to keep a phase moving. Each ADR therefore specifies its own spike in full — hypothesis, candidates, method, measurements, decision rule, and falsification condition — and declines to pre-empt it.

**Neither blocks the rest of Phase 2.** The architecture treats both as container responsibilities behind project-owned ports and names no technology, which is what makes the deferral safe rather than merely convenient. Both block implementation from Phase 5 onward.

## One ADR decided on executed evidence

ADR-007 rests on a spike that was **run**, not described: [`../evidence/phase-2/spikes/`](../evidence/phase-2/spikes/), seed `20260730`, standard library only, deterministic.

The spike also found a defect in its own methodology — a ceiling-clipping bias that made the null hypothesis non-null and inflated every method's regression rate — which was fixed and calibration-checked before any result was used. The recorded conclusion contradicts the intuition the spike began with: the naive threshold method's problem is not its false-positive rate, which is the lowest of the three at large samples, but that it cannot express uncertainty or abstain at all.

## Status vocabulary

| Status | Meaning |
|---|---|
| Accepted | Decided. Implementation must follow it or raise a change proposal. |
| Accepted (X); Y gated | The structure is decided; named parameters or mechanisms are not, and are listed in the ADR. |
| Proposed — gated on spike | **Not a decision.** The spike protocol is specified; no option is selected. |
