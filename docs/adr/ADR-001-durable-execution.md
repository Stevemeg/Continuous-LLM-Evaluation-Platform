# ADR-001 — Durable execution for long-running evaluations

| Field | Value |
|---|---|
| Status | **Proposed — decision gated on spike. NOT DECIDED.** |
| Milestone | M2.3 |
| Canonical basis | §15 (compare Temporal with Celery/ARQ before locking), §19 |
| Requirements | `REQ-F-07-5`, `REQ-N-REL-1`, `REQ-N-REL-2`, `REQ-F-07-7`, `REQ-X-9` |

## Context

Evaluation runs are long-lived, resumable, and expensive. `REQ-N-REL-1` requires survival of worker loss without losing completed work; `REQ-N-REL-2` requires exactly-once effects on results and cost accounting under duplicate delivery; `REQ-F-07-5` requires explicit partial-failure semantics with checkpointing; `REQ-F-07-7` requires cancellation leaving a consistent, clearly incomplete record.

Canonical §15 names the candidates and is explicit that they must be compared **before locking**: "Durable/background execution: explicitly compare Temporal with Celery/ARQ before locking. Long-running resumable evaluations make Temporal a serious candidate."

## Decision

**None. This ADR does not select a technology.**

The candidates differ precisely on the properties the requirements care about — durable state, replay semantics, and exactly-once effects — and those properties cannot be assessed honestly from documentation. Choosing here would be choosing to keep the phase moving, which canonical §22 forbids and the governing execution model explicitly prohibits.

## Spike required before this ADR can be decided

| Element | Specification |
|---|---|
| Hypothesis | A durable-execution engine satisfies `REQ-N-REL-1` and `REQ-N-REL-2` with materially less bespoke state management than a task queue plus hand-written checkpointing. |
| Candidates | Durable workflow engine; task queue with explicit checkpointing (two variants). |
| Workload | A synthetic evaluation run of N samples with per-sample scoring, deliberate worker kill at a mid-run checkpoint, and deliberate duplicate submission of the same work unit. |
| Measurements | Completed samples recomputed after resume (must be zero); samples lost (must be zero); double-counted cost entries under duplicate delivery (must be zero); lines of bespoke state-management code required; observed resume latency. |
| Decision rule | Prefer the option satisfying all three zero-conditions with less bespoke state management. If both satisfy them, prefer the option with the smaller operational footprint. |
| Falsification | If neither satisfies the zero-conditions without substantial bespoke work, both are inadequate and the requirement set or the architecture must be revisited rather than the evidence reinterpreted. |
| Environment | Requires container infrastructure for the workflow engine and broker. Not available in the current environment. |

## Consequences of deferral

- Blocks implementation of the Run Orchestrator (Phase 5 onward). It does **not** block the remainder of Phase 2, because the architecture in [`../architecture/system-architecture.md`](../architecture/system-architecture.md) treats durable execution as a container responsibility behind a port and names no technology.
- `REQ-N-PERF-2` and `REQ-N-REL-5` targets remain `TARGET NOT YET SET` until this spike runs.
- The Orchestrator's port must be defined so either candidate can satisfy it, which is a constraint on Phase 3 contract design.

## Alternatives considered

Selecting the durable workflow engine now on the strength of canonical §15's remark that it is "a serious candidate" was rejected: a remark that something is a serious candidate is an instruction to evaluate it, not a decision.
