# ADR-001 — Durable execution for long-running evaluations

| Field | Value |
|---|---|
| Status | **Accepted — decided on executed spike evidence** |
| Milestone | M2.3 proposed; decided in the Technology Spike Sprint |
| Canonical basis | §15 (compare Temporal with Celery/ARQ before locking), §19 |
| Requirements | `REQ-F-07-5`, `REQ-N-REL-1`, `REQ-N-REL-2`, `REQ-F-07-7`, `REQ-X-9` |
| Evidence | [`../evidence/spike-sprint/`](../evidence/spike-sprint/) — `spike_durable_execution.py`, `spike_resume_latency.py` |

## Context

Evaluation runs are long-lived, resumable, and expensive. `REQ-N-REL-1` requires survival of worker loss without losing completed work; `REQ-N-REL-2` requires exactly-once effects on results and cost accounting under duplicate delivery; `REQ-F-07-5` requires explicit partial-failure semantics with checkpointing; `REQ-F-07-7` requires cancellation leaving a consistent, clearly incomplete record.

Canonical §15 is explicit that the candidates must be compared **before locking**. This ADR previously declined to decide. The spike has now run.

## Decision

**A task queue with explicit checkpointing.** ARQ over Redis for dispatch; run position, checkpoints and idempotency keys in PostgreSQL; all of it behind the project-owned Orchestrator port.

The durable workflow engine is **not** adopted.

## What the spike actually found

The full run is in [`../evidence/spike-sprint/s1-output.txt`](../evidence/spike-sprint/s1-output.txt). Two fault regimes were used, and the difference between them is the finding.

### Regime A — randomly timed worker loss

The fault this ADR originally specified: hard-kill the worker mid-run, restart it.

| Candidate | Trials | Samples lost | Completed samples recomputed | Cost entries double-counted |
|---|---|---|---|---|
| C1 durable workflow engine | 3 | 0 | 0 | 0 |
| C2 task queue + checkpointing | 3 | 0 | 0 | 0 |

Both candidates passed every trial. **This establishes nothing**, and saying so is the point. The window in which a process death can destroy durability is the few milliseconds between committing a side effect and telling the engine it happened — against a 120 ms unit of work. A randomly timed kill almost never lands there. Regime A did not show the zero-conditions hold; it showed they were not contradicted.

### Regime B — crash inside the commit-to-completion window

The worker exits hard immediately after the database commit and before the engine records completion.

| Candidate | Ledger | Samples lost | Recomputed | Cost double-counted | Cost recorded |
|---|---|---|---|---|---|
| C1 durable workflow engine | naive | 0 | 1 | **1** | 410 (expected 400) |
| C1 durable workflow engine | idempotent | 0 | 1 | 0 | 400 |
| C2 task queue + checkpointing | naive | 0 | 1 | **1** | 410 (expected 400) |
| C2 task queue + checkpointing | idempotent | 0 | 1 | 0 | 400 |

**Both candidates failed identically, and both were rescued identically.** Neither engine provides exactly-once side effects; both provide at-least-once delivery. Cost accounting was correct only when the ledger carried a unique key on run identity plus sample identity.

## The falsification condition fired, and what it forced

This ADR committed in advance: *"If neither satisfies the zero-conditions without substantial bespoke work, both are inadequate and the requirement set or the architecture must be revisited rather than the evidence reinterpreted."*

Neither satisfied all three zero-conditions. The revisit is therefore owed, and it lands on the measurement rather than the requirement:

**The zero-condition "completed samples recomputed must be zero" was mis-specified.** It is stricter than the requirement it claimed to operationalise. `REQ-N-REL-1` requires surviving worker loss *without losing completed work*; recomputing a sample does not lose it. No at-least-once system can satisfy the condition as written, and no engine on the market offers exactly-once execution of an arbitrary side effect. The condition is reclassified from a pass/fail gate to a **cost measurement** — recomputation is wasted provider spend, not lost work, and belongs in budget accounting.

The two zero-conditions that do map to requirements — **no sample lost** and **no cost entry double-counted** — are satisfied by both candidates, and only with an application-level idempotency key.

This is a correction that costs more than it saves: it makes idempotency keys mandatory across every externally-visible effect, which is a new binding obligation on the Phase 5 schema. It is recorded because it is what the evidence showed, not because it was convenient.

**The architecture had already assumed this before the spike ran.** [`../architecture/component-architectures.md`](../architecture/component-architectures.md) assigns exactly-once effect to a "per-sample idempotency key from run identity plus sample identity"; [`../data/domain-model.md`](../data/domain-model.md) states it as I-21; [`../architecture/failure-model.md`](../architecture/failure-model.md) handles duplicate delivery by resolving an idempotency key. The spike did not overturn those decisions — it showed they were load-bearing rather than defensive, and that removing them would have produced a system that silently over-bills.

## Why the task queue, given the engine needed no bespoke code

The engine choice cannot turn on exactly-once, because neither candidate delivers it. What remains:

| Criterion | C1 durable workflow engine | C2 task queue + checkpointing |
|---|---|---|
| Zero-conditions mapped to requirements | satisfied, with an idempotency key | satisfied, with an idempotency key |
| Bespoke state-management code | **0 tagged lines** | 22 tagged lines |
| Duplicate submission of the same unit | rejected (`WorkflowAlreadyStartedError`) | rejected (duplicate job id) |
| Added infrastructure | a workflow engine, which is itself a distributed system with its own persistence store | Redis, one process, 57.8 MB image |
| Where run state lives | the engine's own datastore | PostgreSQL, already required by [ADR-012](ADR-012-primary-datastore.md) |

The decision rule said: prefer the candidate satisfying the zero-conditions with less bespoke state management; **if both satisfy them, prefer the smaller operational footprint.** Both satisfy them, so the second clause governs.

Two arguments decide it beyond the rule's arithmetic:

1. **Tenant isolation.** [ADR-012](ADR-012-primary-datastore.md) established a row-level-security boundary in PostgreSQL under four mandatory conditions, and [ADR-010](ADR-010-multi-tenancy.md) requires isolation to be enforced at the store. Run state in PostgreSQL inherits that boundary. Run state in a workflow engine's own datastore sits **outside** it, and would need a second, independent tenant-isolation construction — a second place to get multi-tenancy wrong, protecting data that is already the most sensitive in the system.

2. **The bespoke surface is small and in the right vocabulary.** 22 lines of checkpoint read and monotonic checkpoint write, in SQL, against a database the project already operates. [`../data/domain-model.md`](../data/domain-model.md) already required run state, checkpointing and idempotency to be expressed in the domain rather than in an engine's vocabulary. The task queue satisfies that by construction; the workflow engine satisfies it by discipline.

**The honest counter-argument:** the workflow engine won the criterion this ADR named first, 0 lines against 22. If run orchestration acquires fan-out/fan-in over sub-runs, long timers, or human-in-the-loop signals, 22 lines will not stay 22, and hand-written coordination is where this decision would become expensive. That is a re-evaluation trigger, recorded below, not a reason to pre-buy a distributed system now.

## Resume latency — measured, and deliberately not used

| Detection timeout | C1 resume | C2 resume |
|---|---|---|
| 3 s | 3.11 s | 11.22 s |
| 6 s | 16.21 s | 13.87 s |
| 10 s | 9.23 s | 17.80 s |

At the matched 10 s timeout in the main run, C1 resumed in a median 9.67 s (3 trials, 9.59–9.68 s) and C2 in 17.36 s (16.90–17.37 s).

**These numbers do not support ranking the candidates.** Both engines detect worker loss by timeout, so latency tracks a setting rather than an engine property, and C1's response is not even monotonic in that setting — 6 s produced a slower resume than 10 s, which is retry backoff, not detection. Single trials per point cannot separate that from noise. The decision rule does not list resume latency as a criterion, and it is not used as one. It is recorded so that a later reader can see it was measured rather than skipped.

## Consequences

- **Binding, new:** every externally-visible effect of a work unit — result rows and cost entries — carries an idempotency key derived from run identity plus sample identity, enforced as a unique constraint, not as application discipline. Phase 5 schema work must implement this; it is the spike's principal output.
- Redis joins the deployment as a broker. PostgreSQL was already required.
- The 22 lines of checkpoint logic become project code, with tests, including a test for the monotonicity of the checkpoint advance under redelivery.
- `REQ-N-PERF-2` and `REQ-N-REL-5` targets remain `TARGET NOT YET SET`; this spike measured durability, not throughput.
- The Orchestrator port stays technology-neutral, so this decision remains reversible.

## Re-evaluation trigger

Reopen this ADR if run orchestration requires fan-out/fan-in across sub-runs, durable timers beyond a single job's lifetime, or human-in-the-loop signals mid-run. Those are the workloads the bespoke-line count would stop flattering.

## Alternatives considered

**Adopt the durable workflow engine on its zero bespoke lines.** Rejected: the count measures code not written, not risk not taken, and it omits an entire distributed system and a second datastore outside the tenant-isolation boundary.

**Declare exactly-once satisfied by the engine and skip idempotency keys.** Rejected by direct measurement — Regime B, naive ledger, both candidates over-billed by exactly one sample.

**Treat Regime A's clean sweep as sufficient.** Rejected: it is the result a broken experiment would also produce.
