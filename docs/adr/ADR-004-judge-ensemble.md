# ADR-004 — Judge ensemble and consensus strategy

| Field | Value |
|---|---|
| Status | **Accepted** (strategy). Parameter values gated on a future spike. |
| Milestone | M2.3 |
| Canonical basis | §8, §9, §25 |
| Requirements | `REQ-F-AG-2`, `REQ-F-AG-3`, `REQ-F-AG-4`, `REQ-F-08-6`, `REQ-F-11-4`, `REQ-X-7` |

## Context

Canonical §25 forbids treating a single judge as ground truth. Canonical §8 requires a heterogeneous ensemble with configurable consensus that exposes disagreement, confidence, judge version, bias and variance signals, cost and latency, and escalates low-agreement cases to human review.

The design question is what consensus *means* when judges disagree. Any rule that always produces a single number destroys the information `REQ-F-AG-3` requires the ensemble to surface.

## Decision

1. **Heterogeneous judges.** An ensemble must not consist of one model configuration repeated. Identical judges produce correlated errors and the appearance of consensus without its substance.
2. **Deterministic evaluators never vote.** They are computed, not judged. `REQ-F-08-6` requires structural separation, so they travel in a separate result channel and are never averaged with judge votes.
3. **Consensus is a configurable rule producing a verdict *and* a disagreement measure** — never a verdict alone. The disagreement measure is a first-class output (`REQ-F-AG-3`), not diagnostics.
4. **Low agreement escalates and terminates.** Below the configured agreement threshold the ensemble returns `escalated` and routes to human review (`REQ-F-AG-4`, `UC-15`). It does **not** retry until the judges agree; retrying until agreement manufactures consensus and is the §25 anti-pattern wearing a loop.
5. **Judge version participates in run identity.** A judge version change invalidates comparability rather than warning (`REQ-F-08-8`, `REQ-X-4`).
6. **Judge input is untrusted.** Sample content, retrieved context, tool results, and model output reach judges through legitimate paths and are the primary injection vector (`REQ-X-7`, `REQ-N-SEC-3`).
7. **Per-judge cost, latency, and version are recorded per judgement** (`REQ-F-AG-3`), enabling the agreement and calibration reporting in `REQ-F-11-4`.

## What is deliberately not decided

| Open | Why |
|---|---|
| The agreement metric | Choosing between candidate inter-rater measures requires real judge outputs. Asserting one now would be inventing a methodology, and canonical §19 makes it ADR-backed. |
| The escalation threshold | A threshold without calibration data is the arbitrary constant §25 rejects. |
| Ensemble size and composition | Cost and marginal value trade off; both need measurement. |

These are recorded as `TARGET NOT YET SET` in the same sense as the non-functional targets: the structure is decided, the constants are not.

## Consequences

- Every ensemble judgement carries a disagreement measure, so the gate engine can treat disagreement as a blocking condition.
- `escalated` becomes a terminal evaluation outcome that the gate flow and reporting must both represent.
- Ensemble cost is inherently higher than a single judge. Accepted: `REQ-X-9` requires cost estimation before execution, so the cost is visible and budgeted rather than hidden.
- Human review becomes a required product surface, not an optional one.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Single judge with high-quality prompt | Canonical §25 forbids it. It also makes judge failure indistinguishable from candidate failure. |
| Majority vote returning only the winner | Discards the disagreement `REQ-F-AG-3` requires to be exposed. |
| Average scores across judges | Averaging is the mechanism by which disagreement disappears; `REQ-F-AG-4` exists to prevent exactly this. |
| Escalate by retrying until agreement | Manufactures consensus and destroys the signal. |
