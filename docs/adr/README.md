# Architecture Decision Records

Canonical §19 fixes the required ADR topics; all eleven are present. Two more were added in Phase 4 for the datastore decisions the schema depends on, the two spike-gated ADRs were decided in the Technology Spike Sprint, two more were added in Phase 6 for decisions the reproducible experiment model turns on, and one in Phase 7 for the composition rule the quality gate turns on.

| ADR | Topic | Status |
|---|---|---|
| [ADR-001](ADR-001-durable-execution.md) | Durable execution for long-running evaluations | Accepted on executed spike evidence |
| [ADR-002](ADR-002-agent-orchestration.md) | Orchestration of reasoning components | Accepted |
| [ADR-003](ADR-003-provider-abstraction.md) | Model and provider abstraction | Accepted on executed spike evidence |
| [ADR-004](ADR-004-judge-ensemble.md) | Judge ensemble and consensus strategy | Accepted (strategy); parameters gated |
| [ADR-005](ADR-005-dataset-immutability.md) | Golden dataset immutability and versioning | Accepted |
| [ADR-006](ADR-006-evaluator-isolation.md) | Evaluator SDK and plugin isolation | Accepted (model); mechanism deferred to Phase 5 |
| [ADR-007](ADR-007-regression-statistics.md) | Regression statistics and quality-gate semantics | Accepted (method class) on spike evidence; parameters gated |
| [ADR-008](ADR-008-tool-protocol.md) | Tool integration protocol | Accepted |
| [ADR-009](ADR-009-observability-core.md) | Vendor-neutral observability core with optional adapters | Accepted |
| [ADR-010](ADR-010-multi-tenancy.md) | Multi-tenancy isolation | Accepted (enforcement location); store mechanism constrained |
| [ADR-011](ADR-011-artifact-retention.md) | Artifact retention and reproducibility | Accepted |
| [ADR-012](ADR-012-primary-datastore.md) | Primary datastore | Accepted, under four mandatory conditions |
| [ADR-013](ADR-013-artifact-store.md) | Artifact store | Accepted |
| [ADR-014](ADR-014-run-identity-scope.md) | What a run identity captures, and what enters its digest | Accepted |
| [ADR-015](ADR-015-cache-correctness.md) | Result caching that cannot change an outcome | Accepted |
| [ADR-016](ADR-016-gate-composition.md) | How statistical evidence and configured thresholds compose into a gate decision | Accepted |
| [ADR-017](ADR-017-judge-agreement.md) | What judge disagreement is measured as, and what an ensemble may be composed of | Accepted |

## The two ADRs that were held open, and are now closed

ADR-001 and ADR-003 were left **undecided** through Phases 2, 3 and 4, because both turn on properties only measurement can establish. Each specified its own spike in full — hypothesis, candidates, method, measurements, decision rule, and falsification condition — and declined to pre-empt it. Neither blocked the intervening phases, because the architecture treats both as container responsibilities behind project-owned ports and names no technology.

Both were decided in the [Technology Spike Sprint](../evidence/spike-sprint/), on infrastructure provisioned for the purpose: PostgreSQL, Redis, a durable workflow engine, a real self-hosted inference server, and a fault endpoint. Neither decision went the way the ADR's own framing suggested it would.

- **ADR-001** measured both candidates as passing every randomly-timed worker kill, then showed that result to be worthless: a deliberate crash inside the commit-to-completion window made **both** candidates over-bill by exactly one sample. Neither engine provides exactly-once effects, so the engine choice could not turn on them. One of the ADR's own zero-conditions proved stricter than the requirement it operationalised and was reclassified.
- **ADR-003** rejected the aggregation library on two mechanically-derived findings: it reports an identical exception class, status code and cause for an outage and for a malformed response, and it writes the API key to stdout when debug logging is enabled. Wrapping it in the project's own port fixed neither.

## Three ADRs decided on executed evidence

ADR-007 rests on a spike that was **run**, not described: [`../evidence/phase-2/spikes/`](../evidence/phase-2/spikes/), seed `20260730`, standard library only, deterministic.

That spike found a defect in its own methodology — a ceiling-clipping bias that made the null hypothesis non-null and inflated every method's regression rate — which was fixed and calibration-checked before any result was used. The recorded conclusion contradicts the intuition the spike began with: the naive threshold method's problem is not its false-positive rate, which is the lowest of the three at large samples, but that it cannot express uncertainty or abstain at all.

The spike-sprint runs did the same twice more. ADR-001's decisive experiment was built specifically to falsify the result its first experiment had produced. ADR-003's spike found a live bug in the approach it went on to recommend — an exhausted-quota response classified as retryable, which would have retried forever — and recorded it rather than quietly fixing it.

## Status vocabulary

| Status | Meaning |
|---|---|
| Accepted | Decided. Implementation must follow it or raise a change proposal. |
| Accepted (X); Y gated | The structure is decided; named parameters or mechanisms are not, and are listed in the ADR. |
| Proposed — gated on spike | **Not a decision.** The spike protocol is specified; no option is selected. No ADR currently holds this status. |
| Accepted on executed spike evidence | Decided, and the run that decided it is committed and re-runnable. |
