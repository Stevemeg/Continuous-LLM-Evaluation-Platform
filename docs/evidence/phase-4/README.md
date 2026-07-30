# Validation Evidence — Phase 4

Phase: **Phase 4 — Golden Dataset & Benchmark Registry foundation**
Milestones: M4.1 through M4.5

## Contents

| File | What it is |
|---|---|
| `check_phase4.py` | Phase validator. `python docs/evidence/phase-4/check_phase4.py .` Exits non-zero on any FAIL |
| `check_schema_conformance.py` | Schema conformance checker. Parses the DDL and verifies it satisfies ADR-012, the tenancy rules and the naming standards |
| `validation-output.txt` | Verbatim output of the phase validator |
| `schema-conformance-output.txt` | Verbatim output of the conformance checker |

## Scope decision recorded up front

Canonical §23 names Phase 4 "Golden Dataset & Benchmark Registry **foundation**", and the phase brief asked for storage planning and implementation-ready specifications while instructing that implementation code wait for a phase that explicitly enters implementation. Canonical §23 places the Core Evaluation Harness at Phase 5.

Phase 4 therefore delivers **schema specification, not implementation**: DDL as a reviewable and machine-checkable artifact, with no migration chain, no migration tooling, no application source, no dependency manifest and no tests. `T-19` asserts their absence, and excludes `docs/data/schema/` from the migration pattern deliberately, because a schema definition is a specification and a versioned migration chain is not.

This is a judgement call on an ambiguous boundary and is flagged for review rather than buried.

## What the phase validator checks

| Check | What it establishes |
|---|---|
| `T-1` | Every Phase 1 milestone validator still passes |
| `T-2` | The Phase 3 phase-gate still passes **against the Phase 3 tree** |
| `T-3` | The schema satisfies ADR-012, the tenancy rules and the naming standards |
| `T-4` | Traceability enforcement passes, now including the schema layer |
| `T-5` | The committed traceability matrix is current |
| `T-6` | Schema and API contract agree on every shared vocabulary |
| `T-7` | All 13 ADRs present; the two gated ones are still undecided |
| `T-8` | The new ADRs name their constraint, alternatives, consequences and evidence |
| `T-9` `T-10` | Every requirement and invariant referenced is defined |
| `T-11`…`T-18` | Invented metrics, links, placeholders, secrets, attribution, identity, canonical document, hygiene |
| `T-19` | Phase 4 did not implement Phase 5+ scope |

## The check that carries this phase

**`T-6` — schema and contract must agree.** Phase 3 defined enumerations in the API contract. Phase 4 defined the same enumerations as check constraints. Two definitions of one vocabulary is exactly the duplication that drifts silently, and the drift would surface as an API accepting a value the database rejects — at runtime, in production, on a valid request.

Four vocabularies are compared mechanically: dataset version state, quality finding kind, quality finding severity, and artifact class. The check fails on any disagreement in either direction.

## The conformance checker, and why it exists

`check_schema_conformance.py` guards a failure mode that is **silent by construction**. A table added without `FORCE ROW LEVEL SECURITY`, or granted `UPDATE` on an audit table, produces no error and no failing query. It produces a schema that looks correct in review and does not isolate.

ADR-012 records the evidence: PostgreSQL table owners bypass row-level security by default, so `ENABLE` alone is insufficient. An application connecting as owner reads across tenants while every policy appears correctly defined — and isolation tests run as a non-owner would pass.

The checker verifies, across 22 tables:

| Rule | Assertion |
|---|---|
| ADR-012 D-1, D-2 | Every tenant-scoped table has both `ENABLE` **and** `FORCE` |
| ADR-012 D-3 | Both roles are `NOSUPERUSER` and `NOBYPASSRLS` |
| ADR-012 D-4 | The schema is owned by the migration role, not the runtime role |
| P-1 / N-4 | `organization_id` present and not nullable, except the enumerated global exception |
| P-2 | Every policy has `USING` **and** `WITH CHECK` — `USING` alone filters reads while permitting a cross-tenant write |
| P-5 | Foreign keys between tenant-scoped tables carry `organization_id`, so a cross-tenant *link* is rejected, not merely a cross-tenant *read* |
| I-33 | `UPDATE` and `DELETE` never granted to the runtime role on audit-class tables |
| N-1,2,6,7,8,9,10,11 | Naming, typing, enumerated states, and exact numerics for quantities |

## Self-test

The conformance checker passed on its first run, which is precisely when a checker most needs proving. Nine violations were planted one at a time and every one was caught:

| Planted | Caught by |
|---|---|
| `FORCE ROW LEVEL SECURITY` removed from one table | ADR-012 D-2 |
| `ENABLE ROW LEVEL SECURITY` removed from one table | ADR-012 D-1 |
| `NOBYPASSRLS` removed from the runtime role | ADR-012 D-3 |
| `UPDATE` granted on the audit table | I-33 |
| `organization_id` made nullable | P-1 / N-4 |
| `WITH CHECK` removed from a policy | P-2 |
| Composite foreign key reduced to a single column | P-5 |
| A quantity column changed to floating point | N-9 |
| A constraint renamed off the standard prefix | N-10 |

The schema files were confirmed byte-identical after restoration.

## Traceability

| | |
|---|---|
| Traced | **131** of 150 |
| — architecture | 110 |
| — data model | 71 |
| — schema | 16 |
| — API contract | 63 |
| Deferred with owner and reason | 19 |
| Untracked | 0 |

**The bidirectional check caught a stale deferral during this phase.** `REQ-N-OPS-1` (local development against real backing services) had been deferred to Phase 5; ADR-013 rule O-7 now requires exactly that, so the requirement became traced and the deferral became a lie. The generator failed, and the deferral was removed rather than the failure suppressed. `REQ-N-OPS-3` was also re-owned from Phase 4 to Phase 5, because Phase 4 specified the schema without creating a migration chain.

A deferral register that is never re-checked is where finished work goes to be forgotten. This is the second phase in which that check has changed the register.

## Manual inspection

- **The two ordering rules were checked against the schema**, because neither is expressible as a constraint: quality checks before approval, and demotion before destruction. Both are stated in the lifecycle document with the reason, and the second is reflected in the `erasure_request` state sequence.
- **`ck_artifact__gate_evidence_not_erasable` was reasoned through**, since gate evidence must be permanent and free of erasable content simultaneously; the constraint makes the contradiction impossible to introduce rather than relying on discipline.
- **The evaluator policy asymmetry was checked deliberately.** Built-in evaluators are globally readable but the `WITH CHECK` clause has no `NULL` branch, so the runtime role cannot create or modify one.

## Scope

Phase 4 produces specification and validation only. **No performance, cost or capacity figure is set anywhere in Phase 4.**
