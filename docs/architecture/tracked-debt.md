# Tracked architectural debt

Known structural compromises that are **accepted, not forgotten**. Each names the
requirement it falls short of, what stops it being fixed now, what currently
holds the line instead, and the phase that owns the fix.

A debt leaves this register by being fixed, or by an ADR that explains why it
should not be. It does not leave by being quietly dropped: the Phase 8 validator
reads this file, and a `D-` entry that disappears without the corresponding
structure changing is a failure.

## D-1 — `comparison.evaluator_version_id` does not carry the tenant

| Field | Value |
|---|---|
| Raised | Phase 7, in the Phase 7 review package |
| Requirement | Schema rule P-5: a foreign key into tenant data carries `organization_id` |
| Owning phase | The tenancy and authentication phase (Phase 12) |
| Status | **Open** |

`clep.comparison` names the evaluator version whose scores it compared, as a
plain `uuid` rather than as a composite `(organization_id, evaluator_version_id)`
foreign key. Every other cross-table link in the schema carries the tenant, so a
row cannot cite another tenant's row even if application code asks it to.

**Why it is not fixed here.** `evaluator_version` is dual-scoped: a built-in
evaluator carries a NULL `organization_id` under the ADR-010 rule 4 exception. A
composite foreign key from a row whose `organization_id` is NOT NULL cannot
reference a row whose `organization_id` is NULL, so the constraint cannot be
formed while built-in evaluators exist as global rows.

**What holds instead.** The engine derives the evaluator version from the
candidate run under the caller's tenant session, so row-level security has
already filtered it before it reaches the comparison. The protection is in the
derivation path rather than in the store — which is exactly the arrangement the
rest of the schema exists to avoid relying on.

**What must not happen.** An ad hoc fix that conflicts with the roadmap: adding a
tenant column to `evaluator_version` and back-filling it would break the global
built-in exception ADR-010 decided; dropping the reference would remove the
evidence a comparison rests on.

## D-2 — judges are tenant-scoped, narrowing the domain model

| Field | Value |
|---|---|
| Raised | Phase 8, M8.4 |
| Domain model | `JudgeDefinition` is recorded as "P or G" |
| Owning phase | Revisit if a global judge library is ever specified |
| Status | **Accepted narrowing** |

Schema 08 makes `judge_definition` and `judge_version` tenant-scoped, with no
global variant. The domain model allows either.

**Why.** A judge version binds a rubric to a **model configuration**, and model
configurations are tenant data. A global judge version would have to reference
tenant data across the boundary, which is D-1 again — and D-1 is a debt to carry,
not one to multiply. Every Phase 8 foreign key therefore carries
`organization_id`.

This narrows the model and strengthens isolation, so it is recorded here rather
than raised as a change to an accepted decision. If a shared rubric library is
specified later, the shareable part is the rubric, not the version — which is a
new table rather than a change to these.
