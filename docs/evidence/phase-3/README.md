# Validation Evidence — Phase 3

Phase: **Phase 3 — Data and Contracts**
Milestones: M3.1 through M3.5

## Contents

| File | What it is |
|---|---|
| `check_phase3.py` | Phase validator, exactly as executed. `python docs/evidence/phase-3/check_phase3.py .` Exits non-zero on any FAIL |
| `validation-output.txt` | Verbatim output |
| `generate_traceability.py` | Traceability generator and enforcement check. `--write` regenerates the matrix |
| `traceability-matrix.md` | **Generated.** Requirements to architecture, data model, and API contract |

## What the validator checks

| Check | What it establishes |
|---|---|
| `S-1` | Every Phase 1 milestone validator still passes |
| `S-2` | The Phase 2 phase-gate still passes **against the Phase 2 tree** |
| `S-3` | Traceability enforcement passes: nothing untracked, no stale deferral |
| `S-4` | The committed matrix regenerates byte-identically — it is not stale |
| `S-5` | Every canonical §17 entity appears in the domain model |
| `S-6` | ERD, naming standards, tenancy rules, retention standards, volume model and complexity analysis all present |
| `S-7` | Every invariant referenced is defined |
| `S-8` | OpenAPI 3.1 is structurally valid: refs resolve, operationIds unique, auth declared |
| `S-9` | Eleven structural guarantees hold in the contract itself |
| `S-10` | Every requirement referenced by a Phase 3 artifact or by the contract is defined |
| `S-11` | No invented figure |
| `S-12` | Links resolve |
| `S-13` | No placeholder text |
| `S-14` | No secret in the working tree or any git blob |
| `S-15` | No attribution in the governed scope |
| `S-16` | Sole-contributor identity |
| `S-17` | Canonical document local, unchanged, ignored, untracked, absent from published history |
| `S-18` | Repository hygiene |
| `S-19` | Phase 3 did not implement Phase 4+ scope |

## The checks that carry this phase

**`S-9` — structural contract guarantees.** Prose describing a property can drift from the schema that implements it. An assertion about enum membership cannot. Eleven guarantees are asserted directly against `openapi.json`:

The most important is that **`GateOutcome` contains no `platform_failure` member.** `REQ-F-09-5` and `REQ-X-10` require platform failure to be distinguishable from a quality verdict, and the contract enforces this by making the confusion *unrepresentable* rather than by asking implementers to remember. The check fails if that member is ever added.

The others: `Completeness` has exactly five states so `REQ-X-1` cannot be reduced to a boolean; `Classification` carries `insufficient_evidence` and `not_comparable` as first-class outcomes; `Problem.category` can express `platform_failure`; `Reproducibility` has exactly two states; `SampleResolution` separates scored from non-scored; `UncertaintyInterval` requires both bounds and a confidence level; `ComparisonResult` requires the statistical method version; `PolicyException` requires actor, justification **and** expiry; `Decimal` is a string so money is never a JSON number; and `Idempotency-Key` is required on both mutating entry points.

**`S-3` and `S-4` — traceability that cannot rot.** The matrix is generated from the artifacts, never hand-written, because M1.1 found sixteen drifted entries in a hand-maintained summary table. `S-4` regenerates it and fails if the committed copy differs, so a stale matrix is a build failure rather than a discovery.

The generator enforces two directions. A requirement that is neither traced nor deferred fails. A requirement that is **both** traced and deferred also fails — a deferral register that is never re-checked is where completed work goes to be forgotten.

**`S-2` — the Phase 2 gate at its own tree.** `check_phase2.py` asserts Phase 2 contained no API contract or schema work. Phase 3 legitimately adds both, so that assertion is historical and is evaluated in a throwaway worktree at the Phase 2 commit, with the canonical document copied in for the same reason as before.

## Traceability result

| | |
|---|---|
| Requirements defined | 150 |
| Traced to an artifact | **130** |
| — architecture layer | 108 |
| — data model layer | 71 |
| — API contract layer | 63 |
| Deferred with an owning phase and reason | 20 |
| **Untracked** | **0** |
| Stale deferrals | 0 |
| Implementation layer | 0 — no implementation exists at Phase 3 |
| Test layer | 0 — no tests exist at Phase 3 |

`SC-G7` requires the count of requirements with no owning implementation or test to be exactly zero. That is not yet measurable, because neither exists. This generator is the mechanism that will measure it, and it is in place before the code it will judge.

## Two scanner defects found during this milestone

Both were in my own tooling, and both would have produced a false result.

**The traceability scanner missed nested declarations.** `REQ-F-02-1` was declared on a nested schema *property* rather than on the schema object, and the first scanner only looked one level deep. It reported the requirement as untracked when the contract did declare it. Had the deferral register been filled in from that output, a traced requirement would have been recorded as deferred to a later phase. The walk is now recursive.

**Seven requirements were covered in substance but uncited.** Counting coverage rather than assuming it found `REQ-F-01-3`, `REQ-F-01-5`, `REQ-F-02-3`, `REQ-F-06-2`, `REQ-F-08-5`, `REQ-F-09-3`, `REQ-N-SCALE-1` and `REQ-F-11-7` realised by artifacts that never named them. Citations were added rather than deferrals invented.

## Manual inspection performed in addition to these checks

- **Every deferral was justified individually**, not in bulk. Each of the 20 names an owning phase from canonical §23 and a reason. The RAG and agent evaluator requirements are suite content owned by Phase 9; analytics surfaces are Phase 11; fixtures, coverage gates and dependency justification need code to act on, so they are Phase 5.
- **The erasure ordering was checked for observable consistency.** Demotion precedes destruction, because destroying first would leave a window in which a run claims to be reproducible while its content is already gone.
- **The retention classification of judge rationales was checked specifically.** They read like system output but quote customer content verbatim, so classifying them as decision-class would have silently defeated `REQ-N-PRIV-4`. They are content class.
- **The `GateOutcome` enum was read against the failure model** to confirm both express the same taxonomy.

## Scope

Phase 3 produces specifications: a domain model, a data model, an artifact model, a machine-readable API contract, and a generated traceability matrix. **No implementation, no migrations, no dependency manifest, no tests** — `S-19` asserts their absence.

**No performance, cost, or capacity target is set anywhere in Phase 3.** The data-volume model deliberately expresses growth *relationships* rather than absolute figures, because no measurement exists and any absolute number would be invented.
