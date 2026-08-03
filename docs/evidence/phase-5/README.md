# Validation Evidence — Phase 5

Phase: **Phase 5 — Core Evaluation Harness + evaluator/plugin SDK**
Milestones: M5.1 through M5.7

## Contents

| File | What it is |
|---|---|
| `check_phase5.py` | Phase validator, 21 checks. `python docs/evidence/phase-5/check_phase5.py .` |
| `validation-output.txt` | Verbatim output of the validator |
| `test-output.txt` | Verbatim output of the test suite with coverage |

## What changed about validation in this phase

Phase 5 is the first phase whose output **executes**. Every earlier gate read
artifacts and reasoned about them; this one runs the test suite, applies the
schema to a real PostgreSQL, and believes the exit codes.

That produced the phase's most useful result immediately.

## The defect that only execution could find

`04-artifacts-and-audit.sql` would not apply. `artifact` is the target of
composite foreign keys carrying `organization_id`, and PostgreSQL requires a
matching unique constraint on the referenced side — which it did not have.

Phase 4's conformance checker verified the *referencing* side of every composite
key (rule P-5) and passed. It could not see the other half, because a parser
checks the shape of what is written and a database checks whether it works. The
schema was approved, reviewed, and unable to run.

`P-14` now checks both sides statically, and the migration tests check it by
applying the DDL. Phase 4's stated risk — *"the schema is verified by parsing,
not by executing"* — was correct, and this is what it was pointing at.

## Tenant isolation, finally tested rather than specified

Phase 4 closed with the risk that isolation was *specified but untested*: ADR-010
rule 6 requires a negative test per tenant-scoped table, and that needs a running
database. There is one now.

| Test | What it establishes |
|---|---|
| 24 parametrised checks | Every tenant-scoped table has `ENABLE` **and** `FORCE` in the live catalogue, not just in the DDL text |
| Second tenant reads | Returns nothing — not an error, nothing |
| Second tenant writes | Refused by `WITH CHECK`, which `USING` alone would have permitted |
| Cross-tenant link | Refused by the composite foreign key, which no single-table policy would catch |
| No tenant context | Sees zero rows — fails closed, not open |
| Context after session | Gone; a pooled connection cannot inherit the previous tenant's context |
| Audit deletion | Refused: an actor cannot remove the record of their own action |
| ADR-012 preconditions | Checked against the **connected role**, not the schema: not a superuser, no `BYPASSRLS`, owns no table |

That last one matters most. A deployment can satisfy every static check and still
hand the application a superuser, which disables all of this while leaving every
policy visibly correct.

## The spike's binding output, enforced

ADR-001 concluded that neither durable-execution candidate provides exactly-once
effects, and that `REQ-N-REL-2` holds only because of an application-level
idempotency key. `P-12` checks that key exists on all three effect tables, and
the orchestration tests demonstrate it:

- A full replay of a completed run records **0** new samples, skips **10** as
  duplicates, and the cost total is **identical**.
- Bypassing the application entirely and inserting a duplicate sample row by hand
  is refused by the database.
- The checkpoint refuses to move backwards, so a redelivered job that has been
  overtaken cannot cause completed work to be redone.

## Earlier gates, and why they now run in a clone

Each earlier gate is re-run against the commit it was written for. That was
already true in Phase 4, using a throwaway worktree. It stopped being sufficient
here.

A git worktree **shares the object database** with the repository that created
it. The Phase 4 gate scans every blob on every ref, so run in a worktree it saw
blobs from commits made *after* it and reported failures that were really just
the future arriving. Each gate now runs in an isolated clone, reset to its commit
and pruned of everything unreachable from it.

## Disclosed, and not fixable

Two blobs on published history carry credential-*shaped* strings:

| Blob | What it is |
|---|---|
| `0ec5878` `spike-sprint/common.py` | A throwaway local password inside a DSN, for a spike container that no longer exists |
| `42de37b` `spike-sprint/spike_provider_abstraction.py` | A variable named for a secret, holding a deliberately fake canary used to prove the leak detector worked |

Neither grants access to anything. The first is a genuine hygiene defect — *"it
is only the local one"* is exactly how a real credential gets committed — and the
working tree no longer contains it. Neither can be removed: both are on `main` at
`origin`, and rewriting published history is forbidden.

They are disclosed **by content hash**, so the exception cannot silently widen: a
changed file gets a new hash and fails again. The strengthened scan also revealed
that the earlier gates skipped every dotfile, so a credential in the committed
`.env.example` would never have been scanned at all. It scans hidden files now.

Local development uses trust authentication and password-free connection strings,
so there is no longer a local credential anywhere to commit.

## The canonical specification, and a check that was not checking it

Finalization re-ran `P-19` and found the check itself too narrow. It asked
`git ls-tree main` — the *current tree* of one branch. That passes a document
committed to `main` and deleted one commit later, and it never looked at the
other seven local branches at all. The property that matters is reachability, so
reachability is now what is measured, across every ref.

Measuring it turned something up. The canonical `.docx` is reachable from
`refs/heads/milestone/M1.1-product-definition` at `405424f` — a superseded chain
of `wip(M1.1)` commits that was squashed into the grandfathered `6adfbab` before
anything was pushed.

The reviewer's requirement holds: `origin` carries `main` and nothing else, and
the document is **absent from published history**. But `git push --all` would
publish the canonical specification, and nothing in the repository said so.

| | |
|---|---|
| Reachable from a published ref | 0 — the condition that is never permissible |
| Reachable from a local ref, undisclosed | 0 |
| Disclosed, local-only | 1 (`milestone/M1.1-product-definition`, blob `af23db3`) |

Disclosed by **ref and blob hash together**, so the exception cannot widen: the
same blob on any other ref fails, and a published ref fails before the disclosure
list is consulted at all. Both were verified by planting them.

The branch is left in place. It is unpublished history and removing it is the
repository owner's decision, not this phase's; `git branch -D
milestone/M1.1-product-definition` closes the exposure whenever that decision is
made. Until then the hazard is named, gated, and cannot grow.

## Traceability

| | |
|---|---|
| Traced | **136** of 150 |
| — implementation layer | 17 |
| — test layer | 10 |
| Deferred with owner and reason | 14 |
| Untracked | 0 |

Five deferrals were removed because Phase 5 satisfied them: deterministic
fixtures, the coverage gate, dependency justification, environment configuration,
and migrations. `SC-G7` is fully measurable from here — until now a requirement
could count as traced because a document mentioned it.

## Scope boundary

Phase 5 implements **four** of the contract's thirteen operations: the run
operations the harness owns. The other nine belong to phases that have not run —
gate evaluation to Phase 7, dataset and baseline management to Phase 6, erasure
and audit surfaces to Phase 12 — and are **absent rather than stubbed**. A stub
returning 501 is still a route a client can find and build against.

## One contract change, made in the required order

`Run.completeness` enumerates five ways a run can have *ended*, and was required.
A queued run has ended in none of them, so the contract forced callers to be told
a run was `partial` while it was still executing — the value meaning "finished,
and not entirely".

The contract was amended first and the implementation followed: `completeness` is
now null until terminal, and `ExecutionState` reports the lifecycle separately.
`P-9` checks that the schema, the contract and the code agree on all five shared
vocabularies.
