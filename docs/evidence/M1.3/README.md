# Validation Evidence — M1.3

Milestone: **M1.3 — Functional and Non-Functional Requirements**
Phase: 1 — Product Foundation

This directory holds the evidence produced by executing M1.3's validation. Nothing here is asserted; every result was produced by a command that can be re-run.

## Contents

| File | What it is |
|---|---|
| `check_m13.py` | Validation checker, exactly as executed. Re-runnable: `python docs/evidence/M1.3/check_m13.py .` from the repository root. Exits non-zero on any FAIL |
| `validation-output.txt` | Verbatim output of `check_m13.py` against [`../../product/requirements.md`](../../product/requirements.md) |

## What the checker verifies

| Check | Acceptance criterion |
|---|---|
| `AC-1` | The requirements document exists and parses into requirement rows |
| `AC-2` | Every capability `CAP-01`…`CAP-12` has at least one functional requirement |
| `AC-3` | Every cross-cutting behaviour `X-1`…`X-10` is realised by a cross-cutting requirement |
| `AC-4` | Every requirement carries a statement, a valid priority code, traces, and a valid verification method |
| `AC-5` | Requirement identifiers are unique |
| `AC-6` | Every use case `UC-01`…`UC-18` is traced by at least one requirement |
| `AC-7` | Every user group `U-1`…`U-6` is traced |
| `AC-8` | Every canonical citation falls within the specification's section range |
| `AC-9` | The coverage summary agrees with the requirement rows |
| `AC-10` | No invented performance, cost, or capacity figure |
| `AC-11` | No implementation technology or competitor product named |
| `AC-12` | No assistant-tool attribution |
| `AC-13` | No secrets |
| `AC-14` | Every relative link resolves |
| `AC-15` | Every open product question carried into this milestone is resolved |
| `AC-16` | Every non-functional requirement carries a measurement method and a target cell |
| `AC-17` | No reference to an undefined requirement identifier |
| `PLACEHOLDER` | No `TODO`, `FIXME`, `TBD`, or filler text |

## The three checks that carry this milestone

**`AC-9` — coverage summary agrees with the requirement rows.** The requirement rows are the single source of truth for traceability, and the coverage summary is derived from them. The checker recomputes the summary from the rows and fails on any disagreement.

This exists because of a measured failure, not a hypothetical one. M1.1 found **sixteen** mismatches between its summary coverage tables and the use-case headers those tables restated. Summary tables that duplicate information held elsewhere drift silently, and a requirements document is the worst place for that to happen: a capability can appear covered while having lost its only requirement.

**`AC-10` — no invented figures.** Canonical §20 forbids claiming a metric unless an executed test produced it, and §24 forbids inventing metrics outright. Nothing is built here, so any concrete latency, throughput, percentage, or capacity number would be fabricated. Seven non-functional requirements therefore carry `TARGET NOT YET SET` together with the condition that would let a target be set. That is recorded as a gap, and `AC-16` ensures the measurement method is present even when the target is not.

**`AC-11` — no implementation technology named.** Requirements state *what*, not *how*. Canonical §22 forbids silently changing frozen architecture, and the cheapest way to violate it is to smuggle a technology choice into a requirement, where it never receives an ADR. The check fails on any named storage engine, framework, orchestration tool, or competitor product — including the technologies the canonical specification itself lists as candidates, because a candidate named in a requirement reads as a decision.

## Requirement set as validated

| Group | Count |
|---|---|
| Functional (`REQ-F-*`) | 93 |
| Cross-cutting (`REQ-X-*`) | 10 |
| Non-functional (`REQ-N-*`) | 47 |
| **Total** | **150** |

Coverage confirmed mechanically: 12 of 12 capabilities, 10 of 10 cross-cutting behaviours, 18 of 18 use cases, 6 of 6 user groups, 4 of 4 open product questions resolved.

## Self-tests

A check that has never failed has not been shown to work. Six violations were planted one at a time, each targeting a different check, and each was confirmed to fail the build before being removed:

| Planted violation | Caught by |
|---|---|
| A real requirement the coverage summary does not list | `AC-9` |
| A concrete latency target | `AC-10` |
| A named storage technology | `AC-11` |
| A reference to an undefined requirement identifier | `AC-17` |
| A duplicated requirement identifier | `AC-5` |
| An invalid priority code | `AC-4` |

After each restoration the checker returned to `PASS`, and the document was confirmed byte-identical to its pre-test state.

### One checker defect found and fixed during validation

The first run reported ten attribute defects: every cross-cutting requirement appeared to have a statement that was too short. The requirements were correct; the parser was wrong. Cross-cutting rows carry an extra column naming the behaviour they realise, which shifts the statement one cell to the right, and the parser was reading the wrong cell.

The parser now selects the statement column by requirement group. This is recorded because a checker that misreads its input can fail correct work as easily as it can pass incorrect work, and because the ten reported defects were the checker's, not the document's.

## Scope of this evidence

This evidence covers M1.3 only. M1.3 produces documentation; it produces no application code, so there is no type checking, linting, architecture-boundary checking, test coverage, or performance measurement here.

**No performance, quality, cost, or capacity figure for this system appears in the requirements, because nothing has been built and nothing has been measured.** Verification methods recorded against each requirement are *planned*, not performed.
