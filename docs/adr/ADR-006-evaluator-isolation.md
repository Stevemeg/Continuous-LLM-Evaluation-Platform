# ADR-006 — Evaluator SDK and plugin isolation

| Field | Value |
|---|---|
| Status | **Accepted** (isolation model). Concrete isolation technology deferred to Phase 5. |
| Milestone | M2.3 |
| Canonical basis | §8, §13, §16, §19 |
| Requirements | `REQ-F-AG-7`, `REQ-F-AG-9`, `REQ-F-12-9`, `REQ-N-SEC-4`, `REQ-N-MAINT-3`, `REQ-X-7` |

## Context

Canonical §8 requires a stable Evaluator SDK with capability metadata, schemas, versions, dependencies, permissions, and cost characteristics, and states that third-party evaluation libraries are "adapters — not the architecture". Custom evaluators are, by definition, code the platform did not write, running against tenant data.

## Decision

**Evaluators are declarative plugins behind a stable port, executed under an explicitly granted permission set, and never trusted to describe their own behaviour truthfully.**

1. **Declared contract.** Every evaluator declares capability metadata, input and output schemas, version, dependencies, required permissions, and cost characteristics (`REQ-F-AG-7`).
2. **Declaration is verified, not believed.** Output is validated against the declared schema at runtime; an evaluator whose behaviour contradicts its declaration is rejected rather than having its output recorded as a score (`REQ-F-AG-9`). This is the difference between a contract and a promise.
3. **Deny by default.** An evaluator receives only explicitly granted capabilities. Absent a grant, no network egress, no filesystem access, no access to data outside the sample it is scoring (`REQ-N-SEC-4`, `REQ-F-12-9`).
4. **Isolation is enforced outside the evaluator's own process boundary**, not by convention or in-language sandboxing. In-language restriction of untrusted code is not a security boundary.
5. **Tenant scoping is part of the grant.** An evaluator invocation is scoped to one tenant's data; cross-tenant reach is not expressible in the interface (`REQ-F-12-9`).
6. **Every invocation is audited** with evaluator identity, version, and permissions used (`REQ-F-12-9`, `REQ-X-5`).
7. **Third-party libraries are adapters implementing the port**, never the port itself. The port's shape is derived from the requirements, not from any library's interface (canonical §8).
8. **Meta-tests are a shipping condition.** Every shipped evaluator must demonstrate it distinguishes known-good from known-bad cases (`REQ-N-MAINT-3`); an evaluator that cannot fail on a bad case is not evidence.
9. **Failure is not a score.** A crashed, timed-out, or schema-violating evaluator yields an explicit unavailable result, never a zero (`REQ-X-2`).

## Deferred

The concrete isolation mechanism — process, container, or a stronger runtime boundary — is a Phase 5 implementation decision constrained by rule 4. It is deferred because the choice depends on the deployment model (Phase 14) and because rule 4 is the property that matters; the mechanism is how it is met.

## Consequences

- Evaluator invocation is more expensive than an in-process call. Accepted: `REQ-N-SEC-4` is a security requirement, and untrusted code sharing a trust domain with the gate engine is not an acceptable trade.
- The SDK becomes a published contract with its own compatibility obligations (`REQ-F-AG-7`).
- Schema validation on every invocation adds overhead, retained because `REQ-F-AG-9` exists to stop an evaluator's output being trusted merely because it was produced.
- Adapters for third-party libraries must be written and maintained rather than adopted directly.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| In-process plugins with a review process | Review does not bound behaviour at runtime; `REQ-N-SEC-4` requires an enforced boundary. |
| Trust declared schemas without runtime validation | `REQ-F-AG-9` exists precisely because a declaration can be wrong; the failure would be a wrong score, not an error. |
| Adopt a third-party evaluator interface as the port | Lets a dependency define the domain, which canonical §8 and §25 both forbid. |
