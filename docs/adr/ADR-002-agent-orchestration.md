# ADR-002 — Orchestration of reasoning components

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M2.3 |
| Canonical basis | §7, §8, §15, §19, §25 |
| Requirements | `REQ-F-AG-1`, `REQ-F-AG-5`, `REQ-F-AG-8`, `REQ-N-MAINT-1` |

## Context

Canonical §15 requires comparing a general agent-orchestration framework with a minimal project-owned layer. Canonical §25 lists as an anti-pattern "Framework-driven architecture where LangGraph/RAGAS/etc. dictate domain boundaries."

The reasoning surface is small and fixed by the requirements: exactly three reasoning components exist — the planner (`REQ-F-AG-1`), the judge ensemble (`REQ-F-AG-2`/`3`/`4`), and bounded self-critique (`REQ-F-AG-5`). None involves open-ended multi-agent negotiation, dynamic tool discovery, or emergent control flow. Each has a fixed, auditable shape: bounded iteration, typed output, full history retention.

## Decision

**A minimal project-owned orchestration layer.** A general agent framework is not adopted for the domain.

The layer provides only what the requirements name: typed inputs and outputs, bounded iteration with a maximum count, budget, and timeout, complete iteration history, and injection points allowing every reasoning component to be exercised without live model calls (`REQ-F-AG-8`).

## Rationale

1. **The requirements describe bounded, typed pipelines, not emergent agent behaviour.** A framework designed for the latter would contribute abstraction the domain does not need while constraining the domain model — the §25 anti-pattern by construction.
2. **`REQ-F-AG-8` requires testability without live model calls.** An owned layer makes the model boundary a port the tests substitute directly.
3. **`REQ-F-AG-5` requires bounded iteration with full audit history.** These are properties the domain must own and be able to prove; delegating control flow to a framework makes them properties of a dependency.
4. **`REQ-N-MAINT-1` requires enforced architecture boundaries.** Dependencies must point inward; a framework that owns control flow inverts that for the reasoning components.

## Consequences

- The orchestration layer is project code and must be tested as thoroughly as the domain.
- Framework features not reimplemented — visual graph tooling, prebuilt agent patterns, ecosystem integrations — are unavailable, accepted as the cost of an uncoupled domain.
- Reasoning components remain substitutable and offline-testable.

## Revisit trigger

**Explicit and falsifiable:** if the reasoning surface grows beyond bounded, typed pipelines — for example if a capability requires dynamic multi-agent delegation or runtime tool discovery — this decision must be reopened rather than stretched. The trigger is a change in the *shape* of the reasoning requirement, not a change in the number of reasoning components.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Adopt a general agent framework | Contributes unneeded abstraction, couples the domain to framework control flow, and complicates `REQ-F-AG-8`. Canonical §25 names this failure directly. |
| Adopt a framework only inside the planner | Splits the reasoning execution model in two, producing two audit and testing paths for `REQ-F-AG-5`'s bounded-iteration guarantee. |
