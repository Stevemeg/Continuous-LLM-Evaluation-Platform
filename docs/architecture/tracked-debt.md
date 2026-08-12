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
| Status | **Closed in Phase 12** — by a store-level trigger, not by a composite key |

> **Closed, and how.** `12-identity-and-access.sql` adds
> `trg_comparison__evaluator_version_is_reachable`, which refuses an
> `evaluator_version` that is neither global nor the row's own tenant's. The
> lookup runs under the caller's tenant context and under FORCE row-level
> security, so another tenant's evaluator version is not merely rejected — it is
> invisible, and the row is refused for not existing. A built-in, whose
> `organization_id` is NULL, stays visible through the policy's NULL branch and
> is accepted. That is precisely the rule a composite foreign key would have
> expressed, and the protection now lives in the store rather than in the
> derivation path, which is what this entry said was missing.
>
> **What was deliberately not done.** The composite key remains unformable while
> built-in evaluators are global rows, and making it formable means amending
> ADR-010 rule 4 — an accepted decision. That is a change proposal, not a
> milestone, so it was not made. The same trigger guards
> `evaluator_invocation`, which Phase 12 adds with the identical shape; without
> it the phase would have closed one instance of this debt and created another.

The original entry is kept below as raised.

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

## D-3 — the object store has no adapter, so example payloads arrive over a port

| Field | Value |
|---|---|
| Raised | Phase 11, M11.1 |
| Decision | ADR-013: content lives in S3-compatible object storage, MinIO locally |
| Owning phase | Deployment and infrastructure (Phase 14) |
| Status | **Open** |

`example_content` holds a `payload_ref` and a digest; the bytes live in the
object store. Nothing in the repository stands that store up, and no adapter
speaks to it.

**Why it surfaced now.** Every run before Phase 11 was started by someone who
already held the examples and passed them in. A schedule has no such caller
(`REQ-F-10-1`), so something has to read the dataset — and reading it means
reading the payload.

**What holds instead.** `StoredExampleSource` reads the record from PostgreSQL
and resolves the payload through a `payload_reader` port. A source constructed
without one raises rather than returning examples with empty prompts, and the
built-in reader accepts `file://` references only, refusing anything else rather
than guessing. The digest the dataset version recorded is verified on every read,
so the protection `REQ-F-07-1` depends on holds regardless of which
implementation supplies the bytes.

**What must not happen.** A default that silently falls back to an empty prompt.
An evaluation of the empty string scores perfectly consistently and means
nothing, which is the failure mode this port exists to make impossible.

**Phase 12 widened this, and did not close it.** Erasure (`REQ-N-PRIV-4`)
destroys every record the platform holds — the `payload_ref`, the derived
artifacts, the reproducibility state — and verifies their absence by looking
rather than by trusting an update count. It cannot confirm that the object store
destroyed the object, because nothing speaks to the object store. So an erasure
reports `completed` on the evidence the platform can observe, and the remaining
confirmation arrives with the adapter this entry owns. Recorded here rather than
in the erasure evidence, because the gap belongs to the missing adapter and not
to the erasure path.

## D-4 — the gate's latency criterion still measures evaluation, not the model

| Field | Value |
|---|---|
| Raised | Phase 11, M11.3 |
| Requirement | `REQ-F-11-3` (reporting) and `REQ-F-08-1` (gate criteria) |
| Owning phase | Whichever phase revisits gate policy semantics |
| Status | **Accepted, and deliberately not fixed here** |

Phase 11 added `run_sample.model_latency_ms`, measured at the gateway. The
regression engine's `latency` criterion continues to read
`evaluator_outcome.duration_ms`, which is evaluation latency.

**Why it is not fixed here.** Changing what an existing gate criterion measures
would change the verdict of every published policy that uses it, in a phase whose
scope is reporting. That is a material change to CI semantics, and it belongs to
a change proposal rather than to a milestone that was asked for dashboards.

**What holds instead.** The two are named apart everywhere they appear. The
analytics report `modelLatencyMs` and `evaluatorLatencyMs` as separate
distributions; the engine's own docstring states which one it measures; and an
alert rule names the figure it watches from a closed vocabulary, so a rule on
model latency cannot be mistaken for a gate on it.

## D-5 — `REQ-F-12-1` names three scopes and the platform has two

| Field | Value |
|---|---|
| Raised | Phase 12, M12.2 |
| Requirement | `REQ-F-12-1` — organizations, projects and **environments** as first-class scopes |
| Owning phase | Deployment and infrastructure (Phase 14) |
| Status | **Open** |

Organization and project are first-class: every record carries
`organization_id NOT NULL`, every project-scoped record carries `project_id`, the
store enforces both, and Phase 12 makes an authorization scope out of each.
`Environment` is in the domain model (section 2) and has no table, no column and
no scope.

**Why it is not fixed here.** Nothing consumes it. No other requirement mentions
an environment, no contract operation carries one, no gate decision is scoped to
one, and no run records which environment it evaluated. Adding an `environment`
table that no record references would satisfy the word *first-class* and none of
its meaning — a scope nothing is scoped by is a table, not a boundary.

**What holds instead.** Nothing does, and that is the honest position. The two
scopes that exist are enforced structurally; the third is absent, and this entry
is where it is recorded rather than quietly counted as delivered by
`REQ-F-12-1`'s other two thirds.

**What must not happen.** An `environment` column added to `run` in a later phase
without deciding whether it participates in run identity. If it does not, two
runs in different environments share an identity and a comparison between them
looks valid — the silent-substitution failure I-11 exists to prevent.
