# Requirements Specification
## Continuous LLM Evaluation & Regression Testing Platform

| Field | Value |
|---|---|
| Document | Requirements Specification (functional, cross-cutting, non-functional) |
| Status | **Draft — pending external review** |
| Milestone | M1.3 — Functional and Non-Functional Requirements |
| Phase | Phase 1 — Product Foundation |
| Governing specification | `Continuous LLM Evaluation Platform - Canonical Master Prompt v3.docx` (immutable; held locally, not distributed in this repository) |
| Required by | Canonical §18 (SRS, functional/non-functional requirements) and §23 (Phase 1) |
| Related documents | [`prd.md`](prd.md) · [`personas.md`](personas.md) · [`use-cases.md`](use-cases.md) · [`success-criteria.md`](success-criteria.md) · [`non-goals.md`](non-goals.md) · [`competitive-analysis.md`](competitive-analysis.md) · [`positioning.md`](positioning.md) |

> **This document specifies *what* the product must do, never *how*.** No component, interface, schema, API contract, storage technology, framework, or deployment topology is named or implied as a decision. Architecture is owned by Phase 2 and data/contract design by Phase 3 `[CANON §23]`. Where a requirement has an obvious architectural consequence, that consequence is recorded as a note for Phase 2 rather than resolved here.

> **No performance, cost, or quality figure in this document is a measured result, and most are deliberately absent.** Canonical §20 forbids claiming a metric unless an executed test produced one, and nothing is built. Non-functional requirements therefore specify the **dimension**, the **measurement method**, and the **conditions under which a target may be set** — not invented numbers. Targets marked `TARGET NOT YET SET` are gaps to be closed by evidence, not oversights.

---

## How to read this document

### Requirement identifiers

| Form | Meaning |
|---|---|
| `REQ-F-nn-m` | Functional requirement *m* for capability `CAP-nn` |
| `REQ-F-AG-n` | Functional requirement *n* in the reasoning-component group, serving one or more existing capabilities |
| `REQ-X-n` | Cross-cutting requirement, realising cross-cutting behaviour `X-n` from [`use-cases.md`](use-cases.md) |
| `REQ-N-CAT-n` | Non-functional requirement *n* in category `CAT` |

Identifiers are stable and are never reused once published. A withdrawn requirement is marked withdrawn and retained.

### Priority

MoSCoW, scoped to the first releasable product.

| Code | Meaning |
|---|---|
| **M** | Must — the product is not viable without it |
| **S** | Should — significant value, but the product ships without it if forced |
| **C** | Could — desirable, first to be cut |
| **W** | Won't (this version) — explicitly deferred, recorded so it is not silently lost |

### Verification method

How the requirement will be shown to be met. Planned, not performed: nothing is built.

| Code | Method |
|---|---|
| **T** | Test — automated test or executed benchmark produces the evidence |
| **D** | Demonstration — an executed end-to-end scenario produces the evidence |
| **A** | Analysis — reasoning over design or data, recorded and reviewable |
| **I** | Inspection — inspection of an artifact against a stated rule |

### Traces

Every requirement row carries its own traces, in the order `capability · use cases · personas · canonical sections`. **The requirement rows are the single source of truth for traceability.** The coverage summary in §5 is derived from them and `check_m13.py` fails if the two disagree — the same discipline M1.1 adopted after summary tables drifted from their sources in sixteen places.

---

## 1. Functional requirements

### CAP-01 — Prompt regression testing

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-01-1` | The product shall represent a prompt as a versioned artifact, and a prompt version referenced by any completed run shall be immutable thereafter. | M | CAP-01 · UC-01 · U-1 · §6,§9 | T |
| `REQ-F-01-2` | The product shall allow a named baseline to be designated from a specific combination of prompt version, dataset version, and evaluator/judge version set. | M | CAP-01 · UC-01,UC-08 · U-1,U-4 · §6,§9 | T |
| `REQ-F-01-3` | The product shall evaluate a candidate prompt version against a designated baseline using the same dataset version and the same evaluator/judge versions. | M | CAP-01 · UC-01,UC-08 · U-1,U-4 · §6,§9 | T |
| `REQ-F-01-4` | The product shall refuse to report a baseline-versus-candidate comparison as valid when dataset or evaluator/judge versions differ between the two sides, and shall state which element differs. | M | CAP-01 · UC-01,UC-07,UC-08 · U-1,U-4 · §9 | T |
| `REQ-F-01-5` | The product shall expose per-sample outputs for both sides of a comparison so that an aggregate movement can be attributed to specific samples. | S | CAP-01 · UC-01,UC-12 · U-1 · §9 | D |
| `REQ-F-01-6` | The product shall record prompt version history with the actor and time of each change. | M | CAP-01 · UC-01,UC-14 · U-1,U-6 · §6,§16 | T |

### CAP-02 — Model migration and provider comparison

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-02-1` | The product shall evaluate one prompt version and dataset version across two or more model or provider configurations in a single comparison. | M | CAP-02 · UC-02 · U-1,U-5 · §6 | T |
| `REQ-F-02-2` | The product shall record, for every run, the model identity, provider identity, and the configuration parameters that affect output. | M | CAP-02 · UC-02,UC-07 · U-1,U-2 · §9 | T |
| `REQ-F-02-3` | The product shall report quality, token usage, cost, and latency for each candidate in a comparison. | M | CAP-02 · UC-02,UC-12 · U-5 · §6,§9,§12 | T |
| `REQ-F-02-4` | The product shall support both hosted and self-hosted model endpoints as candidates. | M | CAP-02 · UC-02 · U-2,U-3 · §6 | D |
| `REQ-F-02-5` | The product shall support comparison of more than two candidates for analytical purposes, while a gate decision remains a pairwise baseline-versus-candidate judgement (see PQ-4 in §4). | S | CAP-02 · UC-02,UC-12 · U-5 · §6,§12 | T |
| `REQ-F-02-6` | The product shall isolate provider failure so that the failure of one candidate does not invalidate the results of the others, and shall mark the affected candidate as incomplete. | M | CAP-02 · UC-02 · U-2 · §9,§21 | T |

### CAP-03 — RAG evaluation

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-03-1` | The product shall accept retrieved contexts, and where available citations, as evaluation inputs alongside the question and the generated answer. | M | CAP-03 · UC-03 · U-1 · §6 | T |
| `REQ-F-03-2` | The product shall provide evaluators for retrieval quality, context relevance, faithfulness, groundedness, citation quality, and answer relevance. | M | CAP-03 · UC-03 · U-1 · §6 | T |
| `REQ-F-03-3` | The product shall report hallucination analysis that distinguishes a claim unsupported by the provided context from a claim contradicted by it. | M | CAP-03 · UC-03 · U-1,U-6 · §6 | T |
| `REQ-F-03-4` | The product shall define integration tiers by the intermediate state a caller exposes, shall state which evaluators are available at each tier, and shall refuse rather than approximate an evaluator whose required input is absent (see PQ-1 in §4). | M | CAP-03 · UC-03 · U-1,U-3 · §6,§9 | T |
| `REQ-F-03-5` | The product shall treat retrieved context as untrusted input for the purposes of judge and evaluator execution. | M | CAP-03 · UC-03 · U-3,U-6 · §16 | T |
| `REQ-F-03-6` | The product shall attribute a retrieval-stage failure separately from a generation-stage failure when both are observable. | S | CAP-03 · UC-03 · U-1 · §6,§21 | A |

### CAP-04 — Agent evaluation

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-04-1` | The product shall accept an agent trajectory — the ordered sequence of tool calls with arguments and results — as an evaluation input. | M | CAP-04 · UC-04 · U-1 · §6 | T |
| `REQ-F-04-2` | The product shall evaluate task success, tool-selection correctness, and tool-call validity. | M | CAP-04 · UC-04 · U-1 · §6 | T |
| `REQ-F-04-3` | The product shall evaluate trajectory and planning quality, including detection of non-terminating loops and of recovery after a failed step. | M | CAP-04 · UC-04 · U-1 · §6,§21 | T |
| `REQ-F-04-4` | The product shall evaluate final-answer quality independently of trajectory quality, and shall report the two separately. | M | CAP-04 · UC-04 · U-1,U-5 · §6 | T |
| `REQ-F-04-5` | The product shall bound the trajectory length it will ingest and evaluate, and shall mark a truncated trajectory as truncated rather than evaluating it as complete. | M | CAP-04 · UC-04 · U-2 · §9,§21 | T |
| `REQ-F-04-6` | The product shall treat tool results as untrusted input. | M | CAP-04 · UC-04 · U-3,U-6 · §16 | T |

### CAP-05 — Golden Dataset Manager

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-05-1` | The product shall version datasets, and a dataset version released for benchmark use shall be an immutable snapshot. | M | CAP-05 · UC-05,UC-07 · U-2,U-6 · §6,§10 | T |
| `REQ-F-05-2` | The product shall record dataset lineage, provenance, source metadata, and licensing or usage metadata where applicable. | M | CAP-05 · UC-05,UC-14 · U-6 · §10 | T |
| `REQ-F-05-3` | The product shall enforce a declared schema for every dataset version and reject examples that violate it. | M | CAP-05 · UC-05 · U-2 · §10 | T |
| `REQ-F-05-4` | The product shall support labels, splits, tags, ownership, and an explicit approval state on dataset versions. | M | CAP-05 · UC-05,UC-06 · U-2,U-4 · §10 | T |
| `REQ-F-05-5` | The product shall require human review and recorded approval before a dataset version becomes eligible for baseline use. | M | CAP-05 · UC-05,UC-14 · U-4,U-6 · §10,§16,§25 | T |
| `REQ-F-05-6` | The product shall run dataset quality checks for duplicates, train/test leakage, malformed examples, staleness, and contamination risk, and shall report findings before approval. | M | CAP-05 · UC-05 · U-2,U-6 · §10 | T |
| `REQ-F-05-7` | The product shall classify sensitive data in datasets and support redaction, retention policy, and access policy per tenant. | M | CAP-05 · UC-05,UC-18 · U-6 · §10,§16 | T |
| `REQ-F-05-8` | The product shall honour a data deletion request by removing the designated example content while preserving the integrity of historical run records that referenced it, and shall mark affected runs as no longer fully reproducible rather than silently altering them. | M | CAP-05 · UC-18,UC-07,UC-14 · U-6 · §10,§16 | D |
| `REQ-F-05-9` | The product shall prevent a dataset version from being deleted while an active baseline references it, and shall require an explicit, audited override. | M | CAP-05 · UC-05,UC-18 · U-6 · §10,§16 | T |

> **Note for Phase 2.** `REQ-F-05-8` and `REQ-F-05-1` are in genuine tension: immutability of released snapshots versus an enforceable right to erasure. This specification resolves the *product* behaviour — content is removed, history is preserved, and affected runs are demoted from reproducible to auditable. The mechanism is an architecture question and is explicitly left to Phase 2. It is recorded here because discovering it during implementation would be expensive.

### CAP-06 — Benchmark Suite Registry

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-06-1` | The product shall represent a benchmark suite as a reusable, versioned definition referencing dataset versions, evaluator configurations, and thresholds. | M | CAP-06 · UC-06 · U-2,U-4 · §6 | T |
| `REQ-F-06-2` | The product shall record ownership for every benchmark suite and suite version. | M | CAP-06 · UC-06,UC-14 · U-3,U-6 · §6,§16 | T |
| `REQ-F-06-3` | The product shall pin the benchmark suite version into the identity of every run executed from it. | M | CAP-06 · UC-06,UC-07 · U-2 · §9 | T |
| `REQ-F-06-4` | The product shall scope a benchmark suite to a project by default and shall support explicit, audited sharing to other projects within the same tenant, never across tenants (see PQ-2 in §4). | M | CAP-06 · UC-06,UC-14 · U-3,U-6 · §16 | T |
| `REQ-F-06-5` | The product shall prevent modification of a suite version that has been used to produce an approved baseline. | M | CAP-06 · UC-06,UC-08 · U-4 · §6,§9 | T |

### CAP-07 — Reproducible experiment tracking

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-07-1` | The product shall capture, for every run, an immutable identity comprising dataset version, prompt or system version, model and provider configuration, evaluator and judge versions, seeds where relevant, environment metadata, and timestamps. | M | CAP-07 · UC-07,UC-14 · U-2,U-6 · §9 | T |
| `REQ-F-07-2` | The product shall retain per-sample traces and artifacts in addition to aggregate metrics. | M | CAP-07 · UC-07,UC-12 · U-1,U-2 · §9 | T |
| `REQ-F-07-3` | The product shall re-run a past evaluation from its captured identity and shall report any element that could not be reconstructed. | M | CAP-07 · UC-07 · U-2,U-6 · §9 | D |
| `REQ-F-07-4` | The product shall ensure that result caching never changes the outcome of an evaluation relative to an uncached execution, and shall record whether a result was served from cache. | M | CAP-07 · UC-07 · U-2 · §9 | T |
| `REQ-F-07-5` | The product shall define explicit partial-failure semantics, and shall support resumption, idempotent re-delivery, and checkpointing of long-running evaluations. | M | CAP-07 · UC-07,UC-10 · U-2 · §9,§21 | T |
| `REQ-F-07-6` | The product shall account for tokens and cost per candidate, evaluator, judge, sample, run, project, and tenant. | M | CAP-07 · UC-02,UC-12 · U-2,U-5 · §9 | T |
| `REQ-F-07-7` | The product shall support cancellation of an in-flight evaluation, leaving a consistent and clearly incomplete record. | S | CAP-07 · UC-10 · U-2 · §9 | T |

### CAP-08 — Regression detection

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-08-1` | The product shall classify a candidate against a baseline using absolute and relative thresholds defined per metric. | M | CAP-08 · UC-08 · U-4 · §6,§9 | T |
| `REQ-F-08-2` | The product shall report uncertainty or confidence for every comparison, and effect size where the metric makes it meaningful. | M | CAP-08 · UC-08,UC-09 · U-4,U-5 · §9,§25 | T |
| `REQ-F-08-3` | The product shall provide minimum-sample guidance per metric and shall decline to issue a regression classification when the available sample is below it. | M | CAP-08 · UC-08,UC-09 · U-4 · §9 | T |
| `REQ-F-08-4` | The product shall treat "insufficient evidence" as an outcome distinct from both "regression" and "no change", and shall never present the former as the latter. | M | CAP-08 · UC-01,UC-08,UC-09 · U-1,U-4 · §9,§21,§25 | T |
| `REQ-F-08-5` | The product shall never record a failed, errored, or abstained evaluation as a zero or worst-case score. | M | CAP-08 · UC-01,UC-03,UC-04,UC-17 · U-1,U-4 · §9,§21 | T |
| `REQ-F-08-6` | The product shall keep deterministic evaluator results and probabilistic judge results structurally separate in storage and in reporting. | M | CAP-08 · UC-08,UC-15 · U-4,U-6 · §9,§25 | T |
| `REQ-F-08-7` | The product shall version the statistical comparison method and record the version used in every gate decision. | M | CAP-08 · UC-08,UC-09,UC-14 · U-4,U-6 · §9 | T |
| `REQ-F-08-8` | The product shall invalidate comparability, rather than warn, when a judge or evaluator version changes between baseline and candidate, and shall offer re-scoring of the baseline as the remedy (see PQ-3 in §4). | M | CAP-08 · UC-07,UC-08 · U-4 · §9 | T |

> **Note for Phase 2.** `REQ-F-08-2`, `REQ-F-08-3`, and `REQ-F-08-7` require a defensible choice of statistical method. That choice is explicitly an ADR-and-spike matter under canonical §19, is not made here, and must not be made incidentally during implementation.

### CAP-09 — CI/CD quality gates

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-09-1` | The product shall expose evaluation and gate execution through both a command-line interface and an API suitable for use by continuous integration systems. | M | CAP-09 · UC-09 · U-1,U-4 · §11 | D |
| `REQ-F-09-2` | The product shall support gate outcomes of hard fail, warning, manual approval required, and policy exception. | M | CAP-09 · UC-09 · U-4 · §11 | T |
| `REQ-F-09-3` | The product shall support gate policies that combine quality, cost, latency, safety, judge-agreement, and task-specific criteria. | M | CAP-09 · UC-09 · U-4,U-5 · §11 | T |
| `REQ-F-09-4` | The product shall emit both a machine-readable and a human-readable report for every gate decision, each containing the exact evidence on which the decision rests. | M | CAP-09 · UC-09,UC-14 · U-4,U-6 · §11 | T |
| `REQ-F-09-5` | The product shall report a platform or infrastructure failure as a distinct outcome from a quality failure, and shall never present the former as a quality verdict. | M | CAP-09 · UC-01,UC-09,UC-10,UC-11 · U-1,U-4 · §11,§21 | T |
| `REQ-F-09-6` | The product shall require an actor, a justification, and an expiry for every policy exception, and shall audit it. | M | CAP-09 · UC-09,UC-14 · U-4,U-6 · §11,§16 | T |
| `REQ-F-09-7` | The product shall evaluate a pull request against an approved baseline without requiring the caller to restate the baseline definition. | S | CAP-09 · UC-09 · U-1,U-4 · §11 | D |
| `REQ-F-09-8` | The product shall make the gate policy version part of the recorded gate decision. | M | CAP-09 · UC-09,UC-14 · U-4,U-6 · §11,§16 | T |

### CAP-10 — Scheduled and post-deployment evaluation

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-10-1` | The product shall execute evaluations on a schedule without human initiation. | M | CAP-10 · UC-10 · U-2 · §11 | D |
| `REQ-F-10-2` | The product shall support post-deployment and canary evaluation of a released system. | M | CAP-10 · UC-11 · U-2,U-5 · §11 | D |
| `REQ-F-10-3` | The product shall issue rollback and remediation *recommendations* only, and shall never autonomously change a production system, a dataset, a policy, or a release decision. | M | CAP-10 · UC-11,UC-16,UC-18 · U-2,U-6 · §11,§25 | T |
| `REQ-F-10-4` | The product shall detect quality drift by comparing current results against baseline history rather than against a single prior run. | M | CAP-10 · UC-11 · U-2,U-5 · §12 | T |
| `REQ-F-10-5` | The product shall bound the cost of a scheduled evaluation before executing it and shall skip, rather than partially execute, a run whose estimate exceeds its budget. | M | CAP-10 · UC-10 · U-2,U-5 · §9,§21 | T |

### CAP-11 — Analytics and reporting

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-11-1` | The product shall present quality trends over time and baseline-versus-candidate comparisons. | M | CAP-11 · UC-12 · U-5 · §12 | D |
| `REQ-F-11-2` | The product shall present model and provider leaderboards scoped to a named benchmark, never as a global ranking. | S | CAP-11 · UC-12 · U-5 · §12 | T |
| `REQ-F-11-3` | The product shall report latency distributions including tail latency, and token and cost per successful task. | M | CAP-11 · UC-12 · U-2,U-5 · §12 | T |
| `REQ-F-11-4` | The product shall report judge agreement, disagreement, calibration, and failure rates. | M | CAP-11 · UC-12,UC-15 · U-4,U-6 · §12 | T |
| `REQ-F-11-5` | The product shall report agent tool success, trajectory failures, loops, retries, and task completion. | M | CAP-11 · UC-12 · U-1,U-5 · §12 | T |
| `REQ-F-11-6` | The product shall make every reported figure traceable to the run and the samples that produced it. | M | CAP-11 · UC-12,UC-13,UC-14 · U-5,U-6 · §12,§16 | T |
| `REQ-F-11-7` | The product shall mark any figure computed from incomplete data as incomplete, in every view and export in which it appears. | M | CAP-11 · UC-01,UC-08,UC-09,UC-10,UC-12 · U-4,U-5 · §12,§21 | T |
| `REQ-F-11-8` | The product shall produce an executive scorecard or report suitable for a non-specialist reader, without discarding the incompleteness and uncertainty qualifications. | S | CAP-11 · UC-13 · U-5 · §12 | D |
| `REQ-F-11-9` | The product shall support alerting on defined quality, cost, and latency conditions. | S | CAP-11 · UC-11,UC-12 · U-2,U-5 · §12,§14 | T |

### CAP-12 — Enterprise governance

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-12-1` | The product shall represent organizations, projects, and environments as first-class scopes for every record it creates. | M | CAP-12 · UC-14 · U-3,U-6 · §16,§17 | T |
| `REQ-F-12-2` | The product shall enforce role-based access control over datasets, prompts, suites, baselines, policies, approvals, runs, and reports. | M | CAP-12 · UC-14 · U-3,U-6 · §16 | T |
| `REQ-F-12-3` | The product shall issue scoped API and service credentials and shall support their rotation and revocation. | M | CAP-12 · UC-09,UC-14 · U-3 · §16 | T |
| `REQ-F-12-4` | The product shall audit every change to a dataset, prompt, model configuration, benchmark, evaluator, policy, approval, and release decision, recording actor, time, and justification where a justification is required. | M | CAP-12 · UC-05,UC-09,UC-14,UC-15,UC-18 · U-6 · §16 | T |
| `REQ-F-12-5` | The product shall isolate tenant data such that no request executing in one tenant's context can read or modify another tenant's records. | M | CAP-12 · UC-14 · U-3,U-6 · §16,§21 | T |
| `REQ-F-12-6` | The product shall support per-tenant retention and deletion policies for datasets, runs, artifacts, and audit records, subject to the audit-retention floor in `REQ-N-COMP-3`. | M | CAP-12 · UC-18 · U-6 · §16,§10 | T |
| `REQ-F-12-7` | The product shall record approvals with actor, time, and the artifact version approved. | M | CAP-12 · UC-05,UC-09,UC-14 · U-4,U-6 · §16 | T |
| `REQ-F-12-8` | The product shall make tenant isolation, access control, approvals, and audit available in every deployment configuration it offers, and shall not withhold them by tier. | M | CAP-12 · UC-14 · U-3,U-6 · §16 | I |
| `REQ-F-12-9` | The product shall permission-scope, schema-validate, and audit every tool or custom evaluator invocation, and shall isolate it from other tenants' data. | M | CAP-12 · UC-17 · U-3,U-6 · §13,§16 | T |

> **Note.** `REQ-F-12-8` is the requirement form of positioning pillar P-4 in [`positioning.md`](positioning.md), which rests on an unvalidated assumption (A-2) about buyers below an enterprise procurement threshold. The requirement is stated because the positioning depends on it; if A-2 is falsified, this requirement should be revisited rather than quietly retained.

### Reasoning components — canonical agentic requirements

`[CANON §8]` The canonical specification requires specific reasoning capabilities. These are requirements on product behaviour, not an architecture decision, and canonical §7 forbids inflating conventional services into agents. They carry the `AG` group rather than a capability number because each one serves an existing capability rather than adding a thirteenth.

| ID | Requirement | Pri | Traces | V |
|---|---|---|---|---|
| `REQ-F-AG-1` | The product shall produce a typed, reviewable evaluation plan from an objective, dataset, suite, candidate, budget, and policy inputs, and shall allow a human to inspect and amend it before execution. | M | CAP-07 · UC-16 · U-1,U-2 · §8 | T |
| `REQ-F-AG-2` | The product shall support a heterogeneous judge ensemble combining probabilistic judges with deterministic evaluators, under a configurable consensus rule. | M | CAP-08 · UC-15 · U-4,U-6 · §8,§25 | T |
| `REQ-F-AG-3` | The product shall expose, for every ensemble judgement, the disagreement between judges, a confidence signal, the version of each judge, and the cost and latency each incurred. | M | CAP-08 · UC-15,UC-12 · U-4,U-6 · §8 | T |
| `REQ-F-AG-4` | The product shall escalate a low-agreement judgement to human review rather than resolving it silently by averaging. | M | CAP-08 · UC-15 · U-4,U-6 · §8,§25 | T |
| `REQ-F-AG-5` | The product shall bound any self-critique or regeneration loop by a maximum iteration count, a budget, and a timeout, and shall retain the full history of each iteration. | M | CAP-07 · UC-16 · U-2 · §8,§21 | T |
| `REQ-F-AG-6` | The product shall retain structured historical evaluation memory covering regression history, judge disagreements, release decisions, recurring failures, and evaluator instability, subject to tenant-aware retention and deletion. | S | CAP-11 · UC-12,UC-14 · U-5,U-6 · §8,§16 | T |
| `REQ-F-AG-7` | The product shall expose a stable evaluator plugin interface carrying capability metadata, input and output schemas, version, dependencies, permissions, and cost characteristics. | M | CAP-06 · UC-17 · U-3 · §8 | T |
| `REQ-F-AG-8` | The product shall allow reasoning components to be exercised in tests without live model calls. | M | CAP-07 · UC-17 · U-3 · §8,§20 | T |
| `REQ-F-AG-9` | The product shall reject a custom evaluator whose declared schema does not match its observed behaviour, rather than recording its output as a score. | M | CAP-06 · UC-17 · U-3 · §8,§21 | T |

---

## 2. Cross-cutting requirements

`[CANON §22]` These realise the cross-cutting behaviours identified in [`use-cases.md`](use-cases.md). They are stated once and apply to every capability, rather than being restated per feature.

| ID | Realises | Requirement | Pri | Traces | V |
|---|---|---|---|---|---|
| `REQ-X-1` | X-1 | Every view, export, report, and API response that presents a figure derived from incomplete data shall mark it as incomplete, with the reason available. | M | CAP-11 · UC-01,UC-02,UC-08,UC-09,UC-10,UC-12 · U-1,U-4,U-5 · §12,§21 | T |
| `REQ-X-2` | X-2 | A failed, errored, timed-out, or abstained evaluation shall never be represented as a numeric score. | M | CAP-08 · UC-01,UC-03,UC-04,UC-17 · U-1,U-4 · §9,§21 | T |
| `REQ-X-3` | X-3 | Insufficient evidence shall be a first-class outcome throughout the product, distinct from both an observed change and an observed absence of change. | M | CAP-08 · UC-01,UC-08,UC-09 · U-1,U-4 · §9,§25 | T |
| `REQ-X-4` | X-4 | A change in the version of any element of run identity shall invalidate comparability by enforcement, and the product shall not permit an invalid comparison to be reported as valid. | M | CAP-07 · UC-06,UC-07,UC-08,UC-12 · U-2,U-4 · §9 | T |
| `REQ-X-5` | X-5 | Every governance-relevant action shall be audited with actor, time, and, where the action requires one, a justification. | M | CAP-12 · UC-05,UC-09,UC-14,UC-15,UC-18 · U-6 · §16 | T |
| `REQ-X-6` | X-6 | The product shall recommend and shall never autonomously modify a production system, a dataset, a policy, or a release decision. | M | CAP-10 · UC-11,UC-16,UC-18 · U-2,U-6 · §11,§25 | T |
| `REQ-X-7` | X-7 | Content originating outside the product — dataset examples, retrieved contexts, tool results, model outputs — shall be treated as untrusted when it reaches a judge, an evaluator, or a rendered report. | M | CAP-12 · UC-03,UC-04,UC-05,UC-15,UC-16 · U-3,U-6 · §16,§21 | T |
| `REQ-X-8` | X-8 | Every reported figure shall be traceable to the run and the specific samples that produced it. | M | CAP-11 · UC-12,UC-13,UC-14 · U-5,U-6 · §12,§16 | T |
| `REQ-X-9` | X-9 | Cost shall be estimated before an evaluation executes and enforced while it executes, with a defined behaviour on exhaustion. | M | CAP-07 · UC-02,UC-09,UC-10,UC-16 · U-2,U-5 · §9,§21 | T |
| `REQ-X-10` | X-10 | A platform failure shall be reported through a distinct channel and with distinct semantics from a quality failure, in every surface that reports outcomes. | M | CAP-09 · UC-01,UC-09,UC-10,UC-11 · U-1,U-4 · §11,§21 | T |

---

## 3. Non-functional requirements

**Every target below is either derived from a canonical requirement or marked `TARGET NOT YET SET`.** A number that cannot be justified today is recorded as a gap with the condition that would let it be set. Canonical §20 and §24 make an invented figure a defect, not a placeholder.

### Performance — `PERF`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-PERF-1` | Gate decision latency for a pull-request evaluation shall be low enough not to displace the product from the pull-request path. | Wall-clock from gate invocation to reported decision, measured per suite size | `TARGET NOT YET SET` — requires a spike measuring realistic suite sizes against provider latency | M | CAP-09 · UC-09 · U-1,U-4 · §11 | T |
| `REQ-N-PERF-2` | Evaluation throughput shall scale with concurrency controls rather than by unbounded parallel provider calls. | Samples completed per unit time at each configured concurrency level | `TARGET NOT YET SET` — depends on provider rate limits, which are external | M | CAP-07 · UC-10 · U-2 · §9 | T |
| `REQ-N-PERF-3` | Analytics queries over historical runs shall remain responsive as run history grows. | Query latency at defined history volumes | `TARGET NOT YET SET` — requires a data-volume model, owned by Phase 3 | S | CAP-11 · UC-12 · U-5 · §12 | T |
| `REQ-N-PERF-4` | Cost and token accounting shall not require a full recomputation over history to answer a per-run question. | Complexity analysis plus measured query latency | Analysis recorded at Phase 3 | S | CAP-07 · UC-12 · U-2,U-5 · §9 | A |

### Scalability — `SCALE`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-SCALE-1` | The product shall support dataset versions substantially larger than a hand-curated set without redesign. | Largest dataset version evaluated end to end | `TARGET NOT YET SET` — requires a representative dataset-size model | M | CAP-05 · UC-05 · U-2 · §10 | T |
| `REQ-N-SCALE-2` | The product shall support concurrent evaluation runs across multiple tenants without cross-tenant interference in throughput or cost accounting. | Concurrent multi-tenant run test with per-tenant accounting reconciliation | Interference: none permitted | M | CAP-12 · UC-14 · U-3 · §16 | T |
| `REQ-N-SCALE-3` | Adding a tenant or project shall not require a schema or deployment change. | Inspection plus a demonstrated tenant creation | No change permitted | M | CAP-12 · UC-14 · U-3 · §16,§17 | D |

### Reliability — `REL`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-REL-1` | A long-running evaluation shall survive worker loss without losing completed work. | Fault-injection test killing a worker mid-run, then resuming | No completed sample recomputed incorrectly; no sample silently lost | M | CAP-07 · UC-07,UC-10 · U-2 · §9,§21 | T |
| `REQ-N-REL-2` | Duplicate delivery of the same unit of work shall not double-count results or cost. | Idempotency test with deliberate duplicate submission | Exactly-once effect on results and accounting | M | CAP-07 · UC-10 · U-2 · §21 | T |
| `REQ-N-REL-3` | Transient failure of a dependency shall degrade the product explicitly rather than producing a wrong verdict. | Fault injection per dependency class | No quality verdict emitted from a degraded path | M | CAP-09 · UC-09,UC-10 · U-2,U-4 · §21 | T |
| `REQ-N-REL-4` | Provider outage, rate limiting, malformed response, and model deprecation shall each have a defined, tested behaviour. | Fault injection per named failure mode | All four defined and tested | M | CAP-02 · UC-02 · U-2 · §21 | T |
| `REQ-N-REL-5` | Platform availability shall be defined by an explicit service-level objective. | SLO definition and measurement | `TARGET NOT YET SET` — SLO definition is a Phase 13 deliverable per canonical §23 | S | CAP-09 · UC-09 · U-3 · §14 | A |

### Security — `SEC`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-SEC-1` | Every request shall be authenticated, and every authorization decision shall be enforced server-side. | Test suite asserting rejection of unauthenticated and unauthorized access | No bypass | M | CAP-12 · UC-14 · U-3,U-6 · §16 | T |
| `REQ-N-SEC-2` | Cross-tenant access attempts shall fail and shall be audited. | Negative test per record type, asserting failure and audit emission | No leakage; every attempt audited | M | CAP-12 · UC-14 · U-3,U-6 · §16,§21 | T |
| `REQ-N-SEC-3` | Judges and reasoning components shall resist prompt injection carried in benchmark content, retrieved context, and tool output. | Adversarial corpus of injection attempts, executed as tests | No injected instruction changes a score or a gate outcome | M | CAP-12 · UC-03,UC-04,UC-15 · U-3,U-6 · §16,§21 | T |
| `REQ-N-SEC-4` | Custom evaluators and tool integrations shall execute under an explicit permission boundary. | Inspection plus tests asserting denied capabilities are unavailable | No implicit capability | M | CAP-12 · UC-17 · U-3 · §13,§16 | T |
| `REQ-N-SEC-5` | Credentials shall never be persisted in plaintext, logged, or included in reports or artifacts. | Automated scan of logs, artifacts, reports, and repository | Zero occurrences | M | CAP-12 · UC-14 · U-3,U-6 · §15,§16 | T |
| `REQ-N-SEC-6` | Data shall be encrypted in transit, and at rest where the deployment makes it applicable. | Configuration inspection plus connection tests | In transit: required. At rest: required where applicable | M | CAP-12 · — · U-3,U-6 · §16 | I |
| `REQ-N-SEC-7` | Dependencies shall be scanned for known vulnerabilities as part of the build. | Build-integrated scan with a defined failure policy | Defined policy enforced | M | CAP-12 · — · U-3 · §16 | T |
| `REQ-N-SEC-8` | A formal threat model shall exist before production hardening. | Inspection of the threat-model artifact | Exists and is current | M | CAP-12 · — · U-3,U-6 · §16 | I |
| `REQ-N-SEC-9` | Rate limits and quotas shall be enforced per tenant. | Load test asserting enforcement per tenant | Enforced | M | CAP-12 · — · U-3 · §16 | T |

### Privacy and data handling — `PRIV`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-PRIV-1` | Sensitive data classes in datasets and traces shall be classified and handled according to their class. | Inspection of classification plus tests on handling paths | Every class handled | M | CAP-05 · UC-05,UC-18 · U-6 · §10,§16 | T |
| `REQ-N-PRIV-2` | Redaction shall be available where sensitive content must not reach a judge, a report, or a log. | Tests asserting redacted content does not appear downstream | No leakage of redacted content | M | CAP-05 · UC-05 · U-6 · §10 | T |
| `REQ-N-PRIV-3` | A deletion request shall be executable within a defined period and shall be auditable. | Executed deletion scenario with audit inspection | `TARGET NOT YET SET` — the period is a policy input, not an engineering choice | M | CAP-05 · UC-18 · U-6 · §10,§16 | D |
| `REQ-N-PRIV-4` | Deletion shall extend to derived artifacts and traces containing the deleted content, not only to the dataset record. | Deletion scenario asserting absence across derived stores | Complete within the defined scope | M | CAP-05 · UC-18 · U-6 · §10,§16 | T |

### Observability — `OBS`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-OBS-1` | A single request shall be correlatable through workflow, model call, evaluator, judge, artifact, and gate decision. | Trace inspection across the full chain in a demonstration | End-to-end correlation present | M | CAP-11 · UC-12,UC-14 · U-2,U-3 · §14 | D |
| `REQ-N-OBS-2` | The product shall emit metrics covering latency, errors, queue time, provider behaviour, tokens and cost, judge behaviour, evaluator failures, retries, and workflow transitions. | Inspection of emitted metric names against this list | All nine classes present | M | CAP-11 · UC-12 · U-2,U-3 · §14 | I |
| `REQ-N-OBS-3` | Observability shall not require a proprietary vendor for the product to function. | Build and run with every vendor adapter excluded | Core functions unchanged | M | CAP-11 · — · U-3 · §14,§19 | T |
| `REQ-N-OBS-4` | Metric label cardinality shall be bounded to prevent unbounded series growth. | Inspection plus a cardinality assertion test | Bounded | S | CAP-11 · — · U-3 · §14 | T |

### Usability and explainability — `USE`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-USE-1` | A gate decision shall be explainable to the engineer who triggered it without requiring access to the platform's internals. | Review of the decision report against a stated comprehension checklist | Checklist satisfied | M | CAP-09 · UC-01,UC-09 · U-1,U-4 · §11,§24 | I |
| `REQ-N-USE-2` | An uncertainty or incompleteness qualification shall survive summarisation into an executive report. | Inspection of generated reports for retained qualifications | Retained in every summary level | M | CAP-11 · UC-13 · U-5,U-6 · §12 | I |
| `REQ-N-USE-3` | A failure message shall state which stage failed and what the caller can do about it. | Inspection of failure paths against the rule | Every failure path compliant | S | CAP-09 · UC-01,UC-09 · U-1 · §21 | I |

### Maintainability — `MAINT`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-MAINT-1` | The codebase shall enforce strict typing, linting, and architecture-boundary checks in the build. | Build execution | Enforced; build fails otherwise | M | — · — · U-3 · §20,§22 | T |
| `REQ-N-MAINT-2` | Deterministic fixtures shall allow every model-dependent path to be tested without live calls. | Test suite execution with providers disabled | Full suite passes offline | M | — · UC-17 · U-3 · §20 | T |
| `REQ-N-MAINT-3` | Evaluation meta-tests shall demonstrate that evaluators distinguish known-good from known-bad cases. | Meta-test suite execution | Every shipped evaluator covered | M | CAP-06 · UC-17 · U-3,U-4 · §20 | T |
| `REQ-N-MAINT-4` | Test coverage and quality gates shall be defined and enforced rather than aspirational. | Build-integrated coverage gate | Values carried from the approved Gate 0 criteria in [`success-criteria.md`](success-criteria.md) | M | — · — · U-3 · §20 | T |
| `REQ-N-MAINT-5` | Every dependency shall be traceable to a documented requirement. | Inspection of dependency manifest against requirements | No unjustified dependency | M | — · — · U-3 · §22 | I |

### Compliance and auditability — `COMP`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-COMP-1` | An auditor shall be able to answer, for any past release decision, what evidence supported it, who approved it, and under which policy version. | Executed audit scenario against historical records | Answerable without engineering assistance | M | CAP-12 · UC-14 · U-6 · §16 | D |
| `REQ-N-COMP-2` | The reproducibility window — how far back a decision can be re-executed — shall be explicit rather than incidental. | Inspection of the stated window against retention configuration | Stated and consistent | M | CAP-07 · UC-07,UC-14 · U-6 · §9,§16 | I |
| `REQ-N-COMP-3` | Audit records shall be retained independently of dataset and artifact retention, and shall not be deletable by the actors they record. | Tests asserting audit immutability and independent retention | Append-only; independently retained | M | CAP-12 · UC-14,UC-18 · U-6 · §16 | T |
| `REQ-N-COMP-4` | Evaluation methodology and its limitations shall be documented for every shipped capability. | Inspection of capability documentation | Documented per capability | M | — · — · U-6 · §24 | I |

### Cost — `COST`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-COST-1` | Evaluation cost shall be attributable to tenant, project, run, and candidate. | Reconciliation of attributed cost against recorded provider usage | Attribution reconciles | M | CAP-07 · UC-12 · U-2,U-5 · §9 | T |
| `REQ-N-COST-2` | Budget exhaustion shall be a defined, tested outcome rather than an incident. | Fault injection driving a run to budget exhaustion | Defined behaviour; no partial result presented as complete | M | CAP-07 · UC-10,UC-16 · U-2,U-5 · §21 | T |
| `REQ-N-COST-3` | An unexpectedly expensive evaluation plan shall be detectable before execution. | Estimation accuracy measured against actual cost | `TARGET NOT YET SET` — estimation error bound requires measurement | M | CAP-07 · UC-16 · U-2,U-5 · §21 | T |

### Portability and operability — `OPS`

| ID | Requirement | Measurement method | Target | Pri | Traces | V |
|---|---|---|---|---|---|---|
| `REQ-N-OPS-1` | A developer shall be able to run the product locally with real backing services and execute the full validation suite. | Executed local setup from a clean checkout | Full suite runs locally | M | — · — · U-3 · §15,§20 | D |
| `REQ-N-OPS-2` | Configuration shall come from the environment, with no secret committed to the repository. | Repository scan plus configuration inspection | Zero committed secrets | M | — · — · U-3 · §15 | T |
| `REQ-N-OPS-3` | Schema and data migrations shall be versioned, reversible where feasible, and tested. | Migration test execution | Every migration tested | M | — · — · U-3 · §20 | T |

---

## 4. Resolution of open product questions

`[CANON §22]` M1.1 recorded four product questions assigned to this milestone. Each is resolved below as a **product** decision. None is an architecture decision, and none forecloses a Phase 2 ADR.

### PQ-1 — minimum intermediate state for RAG and agent evaluation

**Resolved.** The product defines three integration tiers, and states plainly what each can and cannot evaluate.

| Tier | Caller exposes | Evaluable |
|---|---|---|
| **Full** | Question, retrieved contexts, citations, generated answer; or full agent trajectory with tool arguments and results | Every `CAP-03` and `CAP-04` evaluator |
| **Partial** | Final output plus a subset of intermediate state | Only evaluators whose declared inputs are present; the remainder are reported unavailable, never approximated |
| **Output-only** | Final output alone | Answer-level evaluators only. Retrieval quality, groundedness attribution, tool-selection correctness, and trajectory quality are unavailable |

The residual value at output-only tier is real but narrow: the product can still detect answer-level regression against a baseline. It cannot explain the cause. Requirement: `REQ-F-03-4`. The product refuses rather than approximates, because an approximated groundedness score is worse than an absent one — it is a number a release decision might rest on.

### PQ-2 — benchmark suite sharing scope

**Resolved.** Project-scoped by default; explicitly shareable to other projects within the same tenant through an audited grant; never shareable across tenants. Requirement: `REQ-F-06-4`. Default-private is chosen because the failure mode of over-sharing a benchmark suite is silent: a suite carrying another team's thresholds produces confident, wrong gate outcomes.

### PQ-3 — judge version change against existing baselines

**Resolved.** A judge or evaluator version change invalidates comparability against baselines scored with the prior version. The product does not warn and proceed; it declines the comparison and offers re-scoring of the baseline under the new version as the remedy. Requirements: `REQ-F-08-8`, `REQ-X-4`. Warning-and-proceeding was rejected because a warning attached to a numeric verdict is routinely ignored, and the resulting comparison is not merely uncertain — it is measuring two different things.

### PQ-4 — N-way comparison versus pairwise

**Resolved.** Both, with a strict separation. A **gate decision** is always pairwise, baseline versus one candidate, because a gate answers one question: may this change ship. **Analytics and leaderboards** support N-way comparison for exploration. Requirements: `REQ-F-02-5`, `REQ-F-11-2`. Leaderboards are benchmark-scoped and never global, since a ranking without a stated benchmark invites exactly the decontextualised comparison canonical §25 rejects.

---

## 5. Coverage summary

Derived from the requirement rows in §1 and §2. `check_m13.py` recomputes this from those rows and fails if it disagrees.

| Capability | Requirements |
|---|---|
| CAP-01 | REQ-F-01-1, REQ-F-01-2, REQ-F-01-3, REQ-F-01-4, REQ-F-01-5, REQ-F-01-6 |
| CAP-02 | REQ-F-02-1, REQ-F-02-2, REQ-F-02-3, REQ-F-02-4, REQ-F-02-5, REQ-F-02-6 |
| CAP-03 | REQ-F-03-1, REQ-F-03-2, REQ-F-03-3, REQ-F-03-4, REQ-F-03-5, REQ-F-03-6 |
| CAP-04 | REQ-F-04-1, REQ-F-04-2, REQ-F-04-3, REQ-F-04-4, REQ-F-04-5, REQ-F-04-6 |
| CAP-05 | REQ-F-05-1, REQ-F-05-2, REQ-F-05-3, REQ-F-05-4, REQ-F-05-5, REQ-F-05-6, REQ-F-05-7, REQ-F-05-8, REQ-F-05-9 |
| CAP-06 | REQ-F-06-1, REQ-F-06-2, REQ-F-06-3, REQ-F-06-4, REQ-F-06-5, REQ-F-AG-7, REQ-F-AG-9 |
| CAP-07 | REQ-F-07-1, REQ-F-07-2, REQ-F-07-3, REQ-F-07-4, REQ-F-07-5, REQ-F-07-6, REQ-F-07-7, REQ-F-AG-1, REQ-F-AG-5, REQ-F-AG-8 |
| CAP-08 | REQ-F-08-1, REQ-F-08-2, REQ-F-08-3, REQ-F-08-4, REQ-F-08-5, REQ-F-08-6, REQ-F-08-7, REQ-F-08-8, REQ-F-AG-2, REQ-F-AG-3, REQ-F-AG-4 |
| CAP-09 | REQ-F-09-1, REQ-F-09-2, REQ-F-09-3, REQ-F-09-4, REQ-F-09-5, REQ-F-09-6, REQ-F-09-7, REQ-F-09-8 |
| CAP-10 | REQ-F-10-1, REQ-F-10-2, REQ-F-10-3, REQ-F-10-4, REQ-F-10-5 |
| CAP-11 | REQ-F-11-1, REQ-F-11-2, REQ-F-11-3, REQ-F-11-4, REQ-F-11-5, REQ-F-11-6, REQ-F-11-7, REQ-F-11-8, REQ-F-11-9, REQ-F-AG-6 |
| CAP-12 | REQ-F-12-1, REQ-F-12-2, REQ-F-12-3, REQ-F-12-4, REQ-F-12-5, REQ-F-12-6, REQ-F-12-7, REQ-F-12-8, REQ-F-12-9 |

| Cross-cutting behaviour | Requirement |
|---|---|
| X-1 | REQ-X-1 |
| X-2 | REQ-X-2 |
| X-3 | REQ-X-3 |
| X-4 | REQ-X-4 |
| X-5 | REQ-X-5 |
| X-6 | REQ-X-6 |
| X-7 | REQ-X-7 |
| X-8 | REQ-X-8 |
| X-9 | REQ-X-9 |
| X-10 | REQ-X-10 |

---

## 6. Effect on the M1.1 provisional success criteria

M1.1 marked several success criteria as provisional pending this requirement set. Each is now addressed, and the conclusion in every case is that **no change to** [`success-criteria.md`](success-criteria.md) **is required**:

| M1.1 item | Conclusion |
|---|---|
| `SC-F7`, `SC-F8` — latency, throughput, availability carry no numbers | **Confirmed as correct.** `REQ-N-PERF-1`, `REQ-N-PERF-2`, and `REQ-N-REL-5` specify the dimension and measurement method but deliberately set no target, because none is defensible before a spike. Writing a number now would be the invention M1.1 avoided. |
| `SC-G3` — coverage floors marked provisional | **Gate 0 values stand.** `REQ-N-MAINT-4` carries them forward unchanged. The requirement set gives no evidence for revising them; changing them on preference alone would be arbitrary. |
| Criteria depending on the formal requirement set | **Now traceable.** Every criterion in the affected categories has at least one requirement in §1 to §3 it can be verified against. |

`[CANON §22]` Not editing the M1.1 documents is a deliberate choice. They are published on `main`, they remain accurate, and their statement that these items are "subject to M1.3" is satisfied by this section rather than contradicted by it.

---

## 7. Deliberately deferred

| Deferred | Owner | Why |
|---|---|---|
| Statistical method for uncertainty, effect size, and minimum sample | Phase 2 ADR and spike | Canonical §19 requires an ADR. Choosing a method here would pre-empt it, and choosing one to keep moving is exactly what canonical §22 forbids. |
| Durable execution approach for long-running evaluations | Phase 2 ADR and spike | Canonical §15 requires an explicit comparison before locking. |
| Agent orchestration approach | Phase 2 ADR | Canonical §15 and §19. |
| Model and provider abstraction approach | Phase 2 ADR | Canonical §15 and §19. |
| Tool-integration protocol choice | Phase 2 ADR | Canonical §13 requires an ADR comparing the options. |
| Mechanism reconciling erasure with snapshot immutability | Phase 2 | Product behaviour is specified in `REQ-F-05-8`; the mechanism is architectural. |
| Data model, schemas, API contracts, retention implementation | Phase 3 | Canonical §23. |
| Mechanical requirement-to-code traceability | Phase 3 | Canonical §18. This document establishes requirement-to-product traceability only. |
| Numeric performance, scalability, and availability targets | Phase 2 spikes onward | Recorded as `TARGET NOT YET SET` rather than guessed. |
| Deletion-period policy input | External policy decision | Not an engineering choice. |

---

## 8. Requirement-level risks

| # | Risk | Consequence if unmanaged |
|---|---|---|
| RR-1 | The erasure-versus-immutability tension (`REQ-F-05-8` against `REQ-F-05-1`) is resolved at product level but not architecturally. | A late architectural discovery could force one requirement to be weakened under delivery pressure, and the one that yields would be the compliance requirement. |
| RR-2 | `REQ-F-08-2` and `REQ-F-08-3` depend on a statistical method not yet chosen. | An incidental choice made during implementation would become the product's statistical position without an ADR — the exact failure canonical §19 exists to prevent. |
| RR-3 | `REQ-F-03-4` makes evaluation quality dependent on caller integration depth. | Adoption friction concentrated precisely in the capabilities most differentiating, and a support burden explaining why an evaluator is unavailable. |
| RR-4 | `REQ-F-12-8` rests on positioning assumption A-2, which is unvalidated. | Governance is built for every tier at a cost that the assumed segment may not exist to justify. |
| RR-5 | Non-functional targets are largely unset. | Non-functional requirements cannot be verified until spikes close them, so nothing prevents a late discovery that a target is unreachable. |
| RR-6 | `REQ-N-SEC-3` requires an adversarial corpus that does not yet exist. | Injection resistance would be asserted rather than tested, which canonical §20 forbids. |

---

## 9. Document history

| Version | Milestone | Change |
|---|---|---|
| 0.1 | M1.3 | Initial requirement set. Pending external review. |
