# Domain Model

| Field | Value |
|---|---|
| Status | **Draft — pending external review** |
| Milestone | M3.1 — Domain Model |
| Phase | Phase 3 — Data and Contracts |
| Required by | Canonical §17 (data model domains), §23 |
| Constrained by | [ADR-005](../adr/ADR-005-dataset-immutability.md), [ADR-010](../adr/ADR-010-multi-tenancy.md), [ADR-011](../adr/ADR-011-artifact-retention.md) |

Entities, their identity and versioning rules, and the invariants that must hold. **Specification only** — no storage technology, no implementation. Physical design is [`data-model.md`](data-model.md).

## Conventions used below

| Column | Meaning |
|---|---|
| **Tenancy** | `T` tenant-scoped · `P` project-scoped within a tenant · `G` global (enumerated exception per ADR-010 rule 4) |
| **Identity** | `srv` server-assigned surrogate · `content` derived from content, so equal content yields equal identity |
| **Mutability** | `mut` mutable · `imm` immutable once created · `imm@release` mutable until released, immutable after |
| **Retention** | `audit` · `decision` · `content` — the three ADR-011 classes |

---

## 1. Organizations, identity, access — canonical §17

| Entity | Tenancy | Identity | Mutability | Notes |
|---|---|---|---|---|
| `Organization` | T (is the tenant) | srv | mut | Root of every scope. `REQ-F-12-1` |
| `User` | G | srv | mut | A user may hold memberships in several organizations |
| `Membership` | T | srv | mut | Binds user to organization with roles. Revocation is an update, never a delete |
| `Role` | G | srv | mut | Global by ADR-010 rule 4 exception — role *definitions* are not tenant data |
| `RoleBinding` | T | srv | mut | Binds principal to role within a scope. `REQ-F-12-2` |
| `ServiceAccount` | T | srv | mut | Non-human principal |
| `ApiKey` | T | srv | imm | Only the verifier is stored, never the secret (`REQ-N-SEC-5`). Rotation creates a new key; revocation sets state. `REQ-F-12-3` |

**Invariants**

| # | Invariant | Requirement |
|---|---|---|
| I-1 | Every tenant-scoped entity resolves to exactly one `Organization`. | `REQ-F-12-5` |
| I-2 | An `ApiKey` secret is never persisted or retrievable after issue. | `REQ-N-SEC-5` |
| I-3 | Revocation is expressed as state, never as deletion, so an audit trail survives. | `REQ-X-5` |
| I-4 | At least one principal retains administrative capability in every organization. | `REQ-F-12-2` |

## 2. Projects and environments — canonical §17

| Entity | Tenancy | Identity | Mutability | Notes |
|---|---|---|---|---|
| `Project` | T | srv | mut | Ownership boundary for suites, baselines, policies. `REQ-F-12-1` |
| `Environment` | P | srv | mut | Named target context, e.g. staging or production |
| `Application` | P | srv | mut | The system under evaluation |

**Invariant I-5** — a `Project` belongs to exactly one `Organization` and cannot be moved between organizations; moving would silently re-tenant every dependent record.

## 3. Datasets — canonical §17, ADR-005

| Entity | Tenancy | Identity | Mutability | Retention | Notes |
|---|---|---|---|---|---|
| `Dataset` | P | srv | mut | decision | Logical container |
| `DatasetVersion` | P | content | `imm@release` | decision | Content-derived identity makes "this run used version v" verifiable, not asserted. `REQ-F-05-1` |
| `Example` | P | srv | imm | decision | The **record**: identity, schema position, labels, lineage |
| `ExampleContent` | P | content | imm | **content** | The **payload**. Separately destructible — the ADR-005 split |
| `Label` | P | srv | mut until release | decision | `REQ-F-05-4` |
| `DatasetApproval` | P | srv | imm | audit | Actor, time, version approved. `REQ-F-05-5` |
| `Lineage` | P | srv | imm | decision | Provenance, source metadata, licensing metadata. `REQ-F-05-2` |
| `QualityCheckResult` | P | srv | imm | decision | Duplicates, leakage, malformed, staleness, contamination. `REQ-F-05-6` |

**Invariants**

| # | Invariant | Requirement |
|---|---|---|
| I-6 | A released `DatasetVersion` never changes membership or schema. Any change produces a new version. | `REQ-F-05-1` |
| I-7 | A `DatasetVersion` becomes baseline-eligible only with a recorded `DatasetApproval`. | `REQ-F-05-5` |
| I-8 | Destroying `ExampleContent` leaves `Example` intact and demotes every referencing `Run` to *auditable*. | `REQ-F-05-8` |
| I-9 | A `DatasetVersion` referenced by an active `Baseline` cannot be deleted without an audited override. | `REQ-F-05-9` |
| I-10 | Every `Example` conforms to its version's schema; violations are rejected, never coerced. | `REQ-F-05-3` |

**Why `ExampleContent` is a separate entity.** It is the only way to satisfy immutability and erasure simultaneously (ADR-005). It also gives `REQ-N-PRIV-4` a join path: every derived artifact references the content identity, so deletion can find all derivatives without scanning.

## 4. Prompts, models, providers — canonical §17

| Entity | Tenancy | Identity | Mutability | Notes |
|---|---|---|---|---|
| `Prompt` | P | srv | mut | Logical container |
| `PromptVersion` | P | content | imm once referenced | `REQ-F-01-1`, `REQ-F-01-6` |
| `Provider` | G | srv | mut | Provider catalogue entry, not credentials |
| `Model` | G | srv | mut | Model catalogue entry |
| `ModelConfiguration` | P | content | imm | **Every output-affecting parameter**. `REQ-F-02-2` |
| `ProviderCredential` | T | srv | mut | `DS-7`. Verifier or reference only; secret never stored in plaintext. `REQ-N-SEC-5` |

**Invariant I-11** — `ModelConfiguration` identity covers every parameter that can affect output. If a parameter is excluded, two different systems could share an identity and a comparison between them would look valid — the silent-substitution failure in the failure model.

## 5. Benchmark suites and evaluators — canonical §17

| Entity | Tenancy | Identity | Mutability | Notes |
|---|---|---|---|---|
| `BenchmarkSuite` | P | srv | mut | Ownership recorded on suite and every version. `REQ-F-06-1`, `REQ-F-06-2` |
| `SuiteVersion` | P | content | imm once used for an approved baseline | `REQ-F-06-3`, `REQ-F-06-5` |
| `SuiteGrant` | T | srv | imm | Audited share to another project **within the tenant only**. `REQ-F-06-4` |
| `EvaluatorDefinition` | P or G | srv | mut | Built-in evaluators global; custom evaluators project-scoped |
| `EvaluatorVersion` | P or G | content | imm | Declares schemas, permissions, cost characteristics. `REQ-F-AG-7` |
| `Threshold` | P | srv | mut | Per-metric absolute and relative thresholds. `REQ-F-08-1` |

**Invariants**

| # | Invariant | Requirement |
|---|---|---|
| I-12 | A `SuiteVersion` used by an approved `Baseline` is frozen. | `REQ-F-06-5` |
| I-13 | A `SuiteGrant` never crosses an `Organization` boundary. Cross-tenant sharing is not representable. | `REQ-F-06-4` |
| I-14 | An `EvaluatorVersion` declaring a schema its output violates is rejected, and its output is not recorded as a score. | `REQ-F-AG-9` |

## 6. Plans, runs, samples — canonical §17, the harness core

| Entity | Tenancy | Identity | Mutability | Retention | Notes |
|---|---|---|---|---|---|
| `EvaluationPlan` | P | srv | imm once accepted | decision | Typed, human-amendable before acceptance. `REQ-F-AG-1` |
| `RunIdentity` | P | **content** | imm | decision | Dataset, prompt, model config, evaluator and judge versions, suite version, seeds, environment, timestamps. `REQ-F-07-1` |
| `Run` | P | srv | state transitions only | decision | Carries `completeness` and `reproducibility` state |
| `RunSample` | P | srv | imm once resolved | decision | Per-sample outcome and resolution state |
| `Metric` | P | srv | imm | decision | Aggregate values, each attributable to samples. `REQ-X-8` |
| `CostRecord` | P | srv | imm | decision | Per candidate, evaluator, judge, sample, run, project, tenant. `REQ-F-07-6` |

**Invariants**

| # | Invariant | Requirement |
|---|---|---|
| I-15 | `RunIdentity` is frozen **before** execution begins and never updated. | `REQ-F-07-1` |
| I-16 | `Run.completeness` is one of `complete`, `partial`, `exhausted`, `cancelled`, `rejected` — five states, of which four are not success. | `REQ-X-1` |
| I-17 | `Run.reproducibility` is stored state (`reproducible` or `auditable`), never inferred at replay time. | `REQ-N-COMP-2`, ADR-011 |
| I-18 | A `RunSample` that failed, timed out, or abstained carries a resolution state and **no numeric score**. | `REQ-X-2` |
| I-19 | Every `Metric` resolves to the `RunSample` set that produced it. | `REQ-X-8` |
| I-20 | A cached `RunSample` result is marked as cache-served and is identical to an uncached computation. | `REQ-F-07-4` |
| I-21 | Sample resolution is idempotent under duplicate delivery: cost is counted once. | `REQ-N-REL-2` |

**I-16 and I-18 are the two invariants that carry `REQ-X-1` and `REQ-X-2` into storage.** If `completeness` were a boolean or a failed sample could hold a zero, no amount of application logic would restore the distinction.

## 7. Judges and consensus — canonical §17, ADR-004

| Entity | Tenancy | Identity | Mutability | Notes |
|---|---|---|---|---|
| `JudgeDefinition` | P or G | srv | mut | |
| `JudgeVersion` | P or G | content | imm | Participates in `RunIdentity`. `REQ-F-08-8` |
| `JudgeRun` | P | srv | imm | One judge's execution over one sample |
| `JudgeVote` | P | srv | imm | Score or abstention, plus cost and latency. `REQ-F-AG-3` |
| `ConsensusResult` | P | srv | imm | Verdict **and** disagreement measure, plus escalation state. `REQ-F-AG-2` |

**Invariants**

| # | Invariant | Requirement |
|---|---|---|
| I-22 | A `ConsensusResult` always carries a disagreement measure. A verdict without one is not representable. | `REQ-F-AG-3` |
| I-23 | Deterministic evaluator results are a distinct entity from `JudgeVote` — not a discriminator column. | `REQ-F-08-6` |
| I-24 | `escalated` is a terminal `ConsensusResult` state, not a retry marker. | `REQ-F-AG-4` |

## 8. Baselines, regressions, gates — canonical §17

| Entity | Tenancy | Identity | Mutability | Retention | Notes |
|---|---|---|---|---|---|
| `Baseline` | P | srv | imm once approved | decision | Pins dataset, prompt, model config, suite, evaluator and judge versions. `REQ-F-01-2` |
| `BaselineApproval` | P | srv | imm | audit | `REQ-F-05-5`, `REQ-F-12-7` |
| `ComparisonResult` | P | srv | imm | decision | Interval, effect size, classification, method version. ADR-007 |
| `GatePolicy` | P | srv | mut | decision | Versioned; the version is recorded in each decision. Criteria may combine quality, cost, latency, safety, judge agreement and task-specific measures. `REQ-F-09-3` |
| `GatePolicyVersion` | P | content | imm | decision | `REQ-F-09-8` |
| `GateDecision` | P | srv | imm | **audit** | Outcome, evidence reference, policy version, method version. `REQ-F-09-4` |
| `PolicyException` | P | srv | imm | audit | Actor, justification, **expiry**. `REQ-F-09-6` |

**Invariants**

| # | Invariant | Requirement |
|---|---|---|
| I-25 | `ComparisonResult.classification` ∈ {`regression`, `improvement`, `no_change`, `insufficient_evidence`, `not_comparable`}. The last two are first-class, not error states. | `REQ-F-08-4`, `REQ-X-3`, `REQ-X-4` |
| I-26 | Every `ComparisonResult` carries an uncertainty interval and the statistical method version. | `REQ-F-08-2`, `REQ-F-08-7` |
| I-27 | Every `GateDecision` references the exact evidence, the policy version, and the method version. | `REQ-F-09-4`, `REQ-F-09-8` |
| I-28 | `GateDecision.outcome` distinguishes `platform_failure` from every quality outcome. | `REQ-F-09-5`, `REQ-X-10` |
| I-29 | A `PolicyException` without actor, justification and expiry is not representable. | `REQ-F-09-6` |
| I-30 | A `Baseline` is comparable to a candidate only when every pinned version matches; otherwise the comparison is `not_comparable`. | `REQ-X-4`, `REQ-F-08-8` |

**I-28 is the storage-level expression of the trust property** in the component architecture: platform failure must never be recordable as a quality verdict.

## 9. Experiments and schedules — canonical §17

| Entity | Tenancy | Identity | Mutability | Notes |
|---|---|---|---|---|
| `Experiment` | P | srv | mut | Groups runs for comparison. `REQ-F-02-5` |
| `Comparison` | P | srv | imm | N-way for analytics; a gate decision remains pairwise |
| `ScheduledWorkflow` | P | srv | mut | `REQ-F-10-1` |
| `ScheduleExecution` | P | srv | imm | Links a schedule firing to its run |

**Invariant I-31** — a `GateDecision` references exactly one baseline and one candidate. N-way comparison is analytics only (PQ-4).

## 10. Traces, calls, costs — canonical §17

| Entity | Tenancy | Identity | Mutability | Retention | Notes |
|---|---|---|---|---|---|
| `Trace` | P | srv | imm | content | Correlation root. `REQ-N-OBS-1` |
| `ModelCall` | P | srv | imm | content | Provider, model config, tokens, cost, latency, outcome |
| `ToolCall` | P | srv | imm | content | `DS-4`. Permission-scoped, audited. `REQ-F-12-9` |
| `EvaluatorInvocation` | P | srv | imm | content | Evaluator version, permissions used, outcome |

**Invariant I-32** — every `ModelCall`, `ToolCall` and `EvaluatorInvocation` carries the correlation identifier chaining it to its `Run`, `RunSample` and, where applicable, `GateDecision`. This is the ADR-009 constraint on Phase 3 contract design, discharged here.

## 11. Alerts, reports, audit — canonical §17

| Entity | Tenancy | Identity | Mutability | Retention | Notes |
|---|---|---|---|---|---|
| `AlertRule` | P | srv | mut | decision | `REQ-F-11-9` |
| `AlertEvent` | P | srv | imm | decision | |
| `Report` | P | srv | imm | decision | Retains incompleteness qualifications. `REQ-N-USE-2` |
| `AuditEvent` | T | srv | **append-only** | **audit** | Actor, time, action, target version, justification. `REQ-F-12-4` |

**Invariants**

| # | Invariant | Requirement |
|---|---|---|
| I-33 | `AuditEvent` is append-only and not deletable by the actors it records. | `REQ-N-COMP-3` |
| I-34 | Audit retention is independent of, and not lowerable by, tenant retention policy. | `REQ-F-12-6` subordinate to `REQ-N-COMP-3` |
| I-35 | A governed action that cannot emit its `AuditEvent` does not complete. | `REQ-X-5`, failure model |

## Ports constrained by Phase 2 ADRs

Discharged here so the two undecided ADRs stay decidable later:

| Port | Constraint | Source |
|---|---|---|
| Orchestrator | Run state, checkpointing and idempotency are expressed in the domain, not in an engine's vocabulary, so either durable-execution candidate can satisfy it. | ADR-001 |
| Provider gateway | `ModelCall` carries **per-call** tokens and cost, and a discriminated outcome distinguishing outage, rate limit, malformed response and deprecation. | ADR-003 |
| Observability | Correlation identifier is a domain field on every call entity (I-32), not adapter metadata. | ADR-009 |
| Artifact index | Every artifact references the `ExampleContent` identity it derives from. | ADR-011 |

## Deliberately not specified here

Physical types, column names, indexes, partitioning, and constraint syntax are [`data-model.md`](data-model.md). Storage technology remains undecided and is out of scope for Phase 3 `[CANON §15]`. No entity above names a storage engine.
