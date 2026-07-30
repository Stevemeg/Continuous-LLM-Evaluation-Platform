# Validation Evidence — Phase 1

Phase: **Phase 1 — Product Foundation**
Milestones: M1.1, M1.2, M1.3

This directory holds the comprehensive phase-level validation. Milestone evidence lives in [`../M1.1/`](../M1.1/), [`../M1.2/`](../M1.2/), and [`../M1.3/`](../M1.3/).

## Contents

| File | What it is |
|---|---|
| `check_phase1.py` | Phase validator, exactly as executed. Re-runnable: `python docs/evidence/phase-1/check_phase1.py .` Exits non-zero on any FAIL |
| `validation-output.txt` | Verbatim output of `check_phase1.py` |

## What it checks

It runs every milestone validator, then the phase-level checks that no single milestone can make.

| Check | What it establishes |
|---|---|
| `P-1` | Every Phase 1 milestone validator executes and passes — this is the regression check for earlier milestones |
| `P-2` | Every canonical Phase 1 requirement has a resulting artifact |
| `P-3` | Every identifier referenced in any Phase 1 document is defined by the document that owns it |
| `P-4` | Every relative link across product and evidence documents resolves |
| `P-5` | No placeholder or filler text |
| `P-6` | No outstanding unsupported claim |
| `P-7` | No outstanding invented metric |
| `P-8` | No secret in the working tree or in any git blob on any ref |
| `P-9` | No attribution in the governed scope |
| `P-10` | Sole-contributor git identity across all history |
| `P-11` | Repository hygiene: no stray artifacts tracked, nothing untracked-and-unignored, clean tree |
| `P-12` | Canonical document is local, ignored, unchanged, untracked, and absent from published history |
| `P-13` | No architecture or technology decision was silently introduced |
| `P-14` | Phase 1 did not implement Phase 2+ scope |

## The two boundary checks

`P-13` and `P-14` exist because the cheapest way to violate the canonical governance in §22 is not to announce an architecture change — it is to let one arrive quietly inside a requirement or a stray file.

**`P-13`** fails any line that asserts adoption of a named technology *for this system*. Naming a technology while describing a competitor is legitimate and expected in the competitive analysis; asserting that this product will use one is a decision, and a decision made outside an ADR is the failure canonical §19 exists to prevent.

**`P-14`** asserts the absence of whole artifact classes that belong to later phases: application source, architecture decision records, schemas and migrations, API contracts, dependency manifests, and container or infrastructure definitions. Phase 1 is documentation. The milestone validators under `docs/evidence/` are excluded, because they are Phase 1's own validation evidence rather than application code.

## On the scoping of `P-9`

The attribution check distinguishes two scopes deliberately, and the distinction is load-bearing.

The **governed scope** — the product documents, every blob reachable from the published ref and from the phase branch, and their commit messages — carries a zero-tolerance requirement.

Separately, superseded blobs may exist on **local-only recovery refs**. The granular M1.1 working history was deliberately retained locally after being squashed for publication, and three of its blobs predate corrections made during M1.1 finalization: an ignore rule that named a tool's configuration directory, a checker whose search term was assembled so as to leave one vendor name intact, and a disclosure that named the string it was disclosing.

Those three blobs are unreachable from anything published, and are reported by name at every run rather than being filtered out. Reporting them as a published-history failure would be inaccurate; hiding them would be worse. The published history itself is clean, which is the requirement that governs.

## Manual inspection performed in addition to these checks

Passing scripts is not sufficient, so the artifacts were also read directly. Recorded because the finding matters more than the pass:

- Requirement statements were read for the two failure modes a checker cannot detect: a requirement that states a mechanism instead of a behaviour, and a requirement that cannot be verified as written. `REQ-F-05-8` was rewritten during drafting for the first reason.
- The capability numbering was checked against the PRD by hand, which is how a heading referencing two capabilities that do not exist was found. Only twelve capabilities are defined; the reasoning-component requirements now carry an `AG` group rather than inventing a thirteenth and fourteenth.
- The competitive analysis was checked for the specific failure of asserting a competitor weakness from silent documentation, which is the failure its own integrity rules exist to prevent.
- Every `TARGET NOT YET SET` was checked to confirm it states the condition under which a target becomes derivable, rather than deferring indefinitely.
- A sample of traces was checked for correctness rather than mere presence, since a checker can only confirm that a trace exists. The gate requirements resolve to the CI/CD use case, the erasure requirement to the deletion use case, and the escalation requirement to the judge-disagreement use case.

### Prioritisation finding, unresolved

Requirement priorities distribute as **136 Must, 14 Should, 0 Could, 0 Won't** across 150 requirements.

A set in which nine requirements in ten are mandatory is barely prioritised. The MoSCoW scale is present but is not doing the work it exists to do, and no requirement is explicitly deferred out of the first release.

There is a defence: canonical §2 states a non-negotiable product definition, and most requirements here derive directly from canonical obligations rather than from preference. Cutting a canonical requirement is not this milestone's prerogative.

That defence is not obviously sufficient, and this is recorded as an open finding rather than resolved. If the distribution is correct, the first releasable product is very large and the delivery plan must say so. If it is not correct, prioritisation needs a deliberate pass that only the product owner can authorise, because it means declining canonical scope for a first release. The scripts pass either way, which is precisely why this is recorded here.

## Scope

Phase 1 produces documentation and its validation. It produces no application code, so there is no type checking, linting, architecture-boundary enforcement, test coverage, or performance measurement in this phase — those gates begin at the first implementation milestone.

**No performance, quality, cost, pricing, or adoption figure for this system or for any competitor appears anywhere in Phase 1**, because nothing has been built, nothing has been measured, and no competitor was installed or run.
