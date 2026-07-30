# Use Cases
## Continuous LLM Evaluation & Regression Testing Platform

| Field | Value |
|---|---|
| Status | **Draft — pending external review** |
| Milestone | M1.1 — Product Definition, Personas, and Use Cases |
| Phase | Phase 1 — Product Foundation |
| Source | `[CANON §5]`, `[CANON §6]` |
| Related | [`prd.md`](prd.md) · [`personas.md`](personas.md) · [`success-criteria.md`](success-criteria.md) · [`non-goals.md`](non-goals.md) |

---

## How to read this document

Eighteen use cases describe what the product does from the user's point of view. Together they cover all twelve canonical capabilities (`CAP-01` … `CAP-12` from [`prd.md`](prd.md) §5) and all six canonical user groups (`U-1` … `U-6` from [`personas.md`](personas.md)).

Each use case records:

- **Primary / secondary persona**, **trigger**, **preconditions**
- **Main flow** — the successful path
- **Alternate and failure flows** — because a use-case document that describes only success produces a requirement set that describes only success, and the resulting product breaks the first time reality intervenes
- **Postconditions**, **capability coverage**, **priority**

**Priority scale.** `P0` = the product has no value without it. `P1` = required for a credible complete product. `P2` = valuable, not foundational.

**These are product behaviours, not designs.** No use case specifies a technology, a component, an API shape, or a data structure. Where a behaviour has an obvious implementation, it is deliberately left unstated — Phase 2 owns that.

**Notation.** `[CANON §n]` cites the canonical specification. `[DERIVED]` marks behaviour that follows from canonical requirements without being literally stated. `[GATE-0]` marks behaviour whose enabling decision was approved at Architecture Gate 0.

---

## Use case index

| ID | Use case | Primary persona | Priority | Capabilities |
|---|---|---|---|---|
| [UC-01](#uc-01--evaluate-a-prompt-change-before-merge) | Evaluate a prompt change before merge | U-1 Maya | **P0** | CAP-01, CAP-07, CAP-08 |
| [UC-02](#uc-02--compare-models-or-providers-for-a-migration-decision) | Compare models or providers for a migration decision | U-1, U-5 | **P0** | CAP-02, CAP-07, CAP-11 |
| [UC-03](#uc-03--evaluate-a-rag-system) | Evaluate a RAG system | U-1 Maya | **P0** | CAP-03 |
| [UC-04](#uc-04--evaluate-an-agent) | Evaluate an agent | U-1 Maya | **P1** | CAP-04 |
| [UC-05](#uc-05--curate-and-approve-a-golden-dataset-version) | Curate and approve a golden dataset version | U-3, U-6 | **P0** | CAP-05, CAP-12 |
| [UC-06](#uc-06--define-and-reuse-a-benchmark-suite) | Define and reuse a benchmark suite | U-3 Priya | **P0** | CAP-06 |
| [UC-07](#uc-07--reproduce-a-past-evaluation) | Reproduce a past evaluation | U-1, U-6 | **P0** | CAP-07 |
| [UC-08](#uc-08--detect-a-regression-against-a-baseline) | Detect a regression against a baseline | U-4 Tomas | **P0** | CAP-08 |
| [UC-09](#uc-09--enforce-a-release-gate-in-cicd) | Enforce a release gate in CI/CD | U-4, U-1 | **P0** | CAP-08, CAP-09, CAP-12 |
| [UC-10](#uc-10--run-a-scheduled-benchmark) | Run a scheduled benchmark | U-2 Devin | **P1** | CAP-10 |
| [UC-11](#uc-11--evaluate-after-deployment-canary-and-drift) | Evaluate after deployment (canary and drift) | U-2 Devin | **P1** | CAP-10, CAP-08 |
| [UC-12](#uc-12--review-quality-cost-and-latency-analytics) | Review quality, cost, and latency analytics | U-5, U-1, U-2 | **P1** | CAP-11 |
| [UC-13](#uc-13--produce-an-executive-scorecard-or-report) | Produce an executive scorecard or report | U-5 Elena | **P2** | CAP-11 |
| [UC-14](#uc-14--answer-a-governance-or-audit-question) | Answer a governance or audit question | U-6 Rachel | **P0** | CAP-12, CAP-07 |
| [UC-15](#uc-15--resolve-judge-disagreement-through-human-review) | Resolve judge disagreement through human review | U-4, U-1 | **P1** | CAP-03, CAP-08, CAP-12 |
| [UC-16](#uc-16--plan-an-evaluation-with-assistance) | Plan an evaluation with assistance | U-1, U-3 | **P2** | CAP-06, CAP-07 |
| [UC-17](#uc-17--add-a-custom-evaluator) | Add a custom evaluator | U-3 Priya | **P1** | CAP-06, CAP-03, CAP-04 |
| [UC-18](#uc-18--honour-a-data-deletion-request) | Honour a data deletion request | U-6 Rachel | **P1** | CAP-05, CAP-12 |

---

## UC-01 — Evaluate a prompt change before merge

**Primary persona:** U-1 Maya · **Secondary:** U-4 Tomas
**Capabilities:** CAP-01, CAP-07, CAP-08 · **Priority: P0**
**Canonical basis:** `[CANON §6]` — *"Prompt regression testing with approved baselines and version history."*

**Trigger.** Maya modifies a system prompt and opens a pull request.

**Preconditions.** A project exists. A golden dataset version is approved. A benchmark suite is defined. An approved baseline exists for this suite.

### Main flow
1. Maya opens a pull request containing the prompt change.
2. Continuous integration invokes the platform, identifying the project, the benchmark suite, and the candidate — the modified prompt version.
3. The platform records an immutable run configuration capturing every input that can affect the result: dataset version, prompt version, model configuration, evaluator and judge versions, and sampling parameters.
4. The platform produces outputs for each dataset example using the candidate configuration.
5. The platform scores those outputs using the suite's deterministic evaluators and judges.
6. The platform compares the candidate against the approved baseline and classifies the outcome as an improvement, no detectable change, a regression, or insufficient evidence to decide.
7. The platform reports the outcome back to the pull request with a link to full evidence.
8. Maya sees which specific examples changed and how, and either proceeds or revises.

### Alternate flows
- **A1 — No baseline exists.** The platform reports that no comparison is possible and offers to establish this run as a candidate baseline, subject to approval. It does **not** silently treat the first run as a baseline.
- **A2 — Result is inconclusive.** `[DERIVED]` The platform reports that the evidence does not support a conclusion, states why (for example, insufficient samples relative to the observed variability), and indicates what would be required to decide. This is a distinct outcome from "no change."
- **A3 — Change improves some metrics and regresses others.** The platform reports per-metric outcomes rather than collapsing to a single verdict, and the release policy determines the overall gate result (see UC-09).
- **A4 — Baseline is stale or incomparable.** If the baseline was produced with a different dataset version or different evaluator versions, the platform refuses the comparison and explains why, rather than producing a misleading number.

### Failure flows
- **F1 — Provider unavailable or rate-limited.** The run pauses and retries within configured bounds. If it cannot complete, it terminates with partial results clearly marked incomplete. **It does not report an aggregate as though the run finished.**
- **F2 — Run exceeds its budget.** The run halts, partial results are retained and marked, and the reason is reported.
- **F3 — Some evaluations fail.** Failed evaluations are recorded as failures, never as zero scores. Every aggregate states how much of the dataset it actually covers.
- **F4 — Completeness below the policy minimum.** The gate returns inconclusive, never pass. `[CANON §11]`

**Postconditions.** An immutable run record, per-sample evidence, aggregate metrics with completeness metadata, a comparison result, and an audit entry.

---

## UC-02 — Compare models or providers for a migration decision

**Primary persona:** U-1 Maya, U-5 Elena · **Secondary:** U-2 Devin
**Capabilities:** CAP-02, CAP-07, CAP-11 · **Priority: P0**
**Canonical basis:** `[CANON §6]` — *"Model migration/provider comparison across hosted and self-hosted models."*

**Trigger.** The team is considering moving to a different model or provider — typically for cost, latency, capability, or because the current model is being deprecated.

**Preconditions.** Approved dataset version and benchmark suite. Credentials for each provider under consideration. `[GATE-0]` Credentials are customer-supplied.

### Main flow
1. Elena or Maya defines a comparison across two or more candidate model configurations, holding every other input constant.
2. The platform executes each candidate against the same dataset version with the same evaluators and judges.
3. The platform reports, per candidate: quality metrics with uncertainty, latency including tail behaviour, and token and cost consumption.
4. The platform presents the three axes together so the trade-off is visible in one place.
5. The platform indicates which quality differences are meaningful and which are not distinguishable from noise.
6. Elena makes a decision with the evidence attached to it.

### Alternate flows
- **A1 — Candidates are indistinguishable on quality.** The platform says so explicitly. `[DERIVED]` This is a genuinely useful answer, because it means the decision can be made on cost and latency alone. It must not be presented as a tie caused by measurement failure.
- **A2 — More than two candidates.** The platform reports all candidates and controls for the fact that comparing many candidates raises the chance of a spurious apparent winner.
- **A3 — Candidates differ in capability, not only quality.** For example, one supports structured output and another does not. The platform reports capability-driven failures distinctly from quality differences, because they are different decisions.
- **A4 — Self-hosted candidate.** `[CANON §6]` Hosted and self-hosted models are compared on the same footing.

### Failure flows
- **F1 — One provider fails while others succeed.** The failure is isolated to that candidate. Other candidates complete. The affected candidate's results are marked incomplete rather than silently under-reported.
- **F2 — A candidate refuses to answer some inputs.** Refusals are recorded as a distinct outcome, not as quality failures, because they measure something different.
- **F3 — Pricing information for a model is unavailable or stale.** Cost figures are marked as such rather than being estimated silently.

**Postconditions.** A comparison record, per-candidate evidence, and a decision that can be reconstructed later.

---

## UC-03 — Evaluate a RAG system

**Primary persona:** U-1 Maya
**Capabilities:** CAP-03 · **Priority: P0**
**Canonical basis:** `[CANON §6]` — *"RAG evaluation: retrieval quality, context relevance, faithfulness, groundedness, citation quality, answer relevance, hallucination analysis."*

**Trigger.** Maya changes retrieval configuration, the knowledge base, the chunking strategy, or the generation prompt of a retrieval-augmented system.

**Preconditions.** A dataset version containing questions and, where required by the chosen metrics, reference answers or relevance annotations. The system under evaluation exposes its retrieved contexts.

### Main flow
1. Maya runs a RAG benchmark suite against her system.
2. For each example, the platform obtains the generated answer **and the contexts the system retrieved**.
3. The platform evaluates retrieval quality against relevance annotations where available.
4. The platform evaluates context relevance, faithfulness of the answer to the retrieved contexts, groundedness, citation quality, and answer relevance.
5. The platform reports hallucination analysis — content asserted in the answer that is not supported by the retrieved contexts.
6. Maya sees which examples failed on which dimension and inspects the retrieved contexts for those examples.

### Alternate flows
- **A1 — The system does not expose retrieved contexts.** `[DERIVED]` Retrieval quality, faithfulness, and groundedness are not computable. The platform states which metrics are unavailable and why, and runs the metrics that remain valid. **It does not substitute a weaker proxy metric under the same name.** *(Open product question PQ-1 in [`prd.md`](prd.md) §10 concerns exactly how much value remains in this case.)*
- **A2 — No reference answers.** Reference-dependent metrics are unavailable; reference-free metrics still run. The distinction is reported.
- **A3 — Retrieval succeeds but generation ignores the context.** This is a distinguishable failure mode and is reported as such rather than as a general quality drop.

### Failure flows
- **F1 — A judge cannot produce a valid judgment.** The judgment is recorded as a failure or an abstention, never as a low score.
- **F2 — Retrieved contexts exceed size limits.** Handled within declared bounds; truncation, if it occurs, is recorded as part of the evidence, because a truncated context changes what faithfulness means.
- **F3 — A dataset example contains content attempting to manipulate the judge.** `[CANON §16]` The example is flagged and routed to human review rather than being silently scored.

**Postconditions.** Per-example retrieval and generation evidence, per-dimension metrics with completeness, and a record of which metrics were unavailable and why.

---

## UC-04 — Evaluate an agent

**Primary persona:** U-1 Maya
**Capabilities:** CAP-04 · **Priority: P1**
**Canonical basis:** `[CANON §6]` — *"Agent evaluation: task success, tool-selection correctness, tool-call validity, trajectory/planning quality, loop detection, recovery, final-answer quality."*

**Trigger.** Maya changes an agent's tools, planning prompt, or graph structure.

**Preconditions.** A dataset of tasks with success criteria. The agent under evaluation exposes its trajectory — the sequence of tool calls, their inputs, and their results.

### Main flow
1. Maya runs an agent benchmark suite.
2. For each task the platform obtains the final answer **and the full trajectory**.
3. The platform evaluates task success against the task's success criteria.
4. The platform evaluates tool-selection correctness and tool-call validity — whether calls conformed to their declared schemas.
5. The platform evaluates trajectory and planning quality.
6. The platform detects loops and redundant repeated actions.
7. The platform evaluates recovery — whether the agent recovered after a tool failure.
8. The platform evaluates final-answer quality.
9. Maya inspects failing trajectories step by step.

### Alternate flows
- **A1 — The agent succeeds by an inefficient path.** Task success and trajectory quality are reported separately, because "got there eventually after eleven redundant calls" is a different result from "got there directly," and collapsing them hides a real cost and latency problem.
- **A2 — The agent fails because a tool was unavailable.** Distinguished from a planning failure. These require different fixes.
- **A3 — Trajectory is not exposed.** Only final-answer quality and task success are computable. The platform states which metrics are unavailable rather than degrading them silently.

### Failure flows
- **F1 — The agent does not terminate.** Bounded by a declared limit; recorded as a non-termination outcome, which is a specific and meaningful agent failure mode rather than a run error.
- **F2 — Trajectory is malformed.** Recorded as an evaluation failure with the reason; not scored as zero.
- **F3 — A tool result contains content attempting to manipulate an evaluating judge.** `[CANON §16]` Flagged and escalated (see UC-15).

**Postconditions.** Per-task trajectory evidence, per-dimension metrics, and inspectable failure detail.

---

## UC-05 — Curate and approve a golden dataset version

**Primary persona:** U-3 Priya, U-6 Rachel · **Secondary:** U-1 Maya
**Capabilities:** CAP-05, CAP-12 · **Priority: P0**
**Canonical basis:** `[CANON §6]`, `[CANON §10]` — versioning, lineage, provenance, schemas, splits, approvals, access control, immutable release snapshots.

**Trigger.** A team needs a new evaluation dataset, or needs to extend or correct an existing one.

**Preconditions.** A project exists. The user holds the necessary permission.

### Main flow
1. Maya creates a draft dataset version, either new or derived from an existing version.
2. She adds examples with inputs, expected outputs or references, labels, and provenance metadata recording where each example came from.
3. She assigns examples to named splits.
4. The platform runs quality checks and reports findings: schema violations, duplicates and near-duplicates, overlap with other versions, contamination risk against known public benchmarks, personal data, malformed examples, and content that appears designed to manipulate a judge.
5. Maya resolves the findings, or records a justification for accepting one.
6. She submits the version for review.
7. Rachel or a designated approver reviews the changes — **as a diff against the parent version, not as an opaque blob** — and approves.
8. On approval the version becomes released and **immutable**. It can never be modified again.
9. Released versions can be referenced by benchmark suites and runs, with the guarantee that the content is exactly what was approved.

### Alternate flows
- **A1 — Quality checks find high-severity issues.** Promotion is blocked until they are resolved or explicitly overridden with a recorded justification and an identified approver.
- **A2 — The version is derived from an existing one.** Lineage to the parent is recorded, and the review presents only what changed.
- **A3 — Personal data is detected.** Redaction is required before promotion, or explicit acceptance is recorded with justification.
- **A4 — Approver rejects.** The version stays in draft with the rejection reason recorded.
- **A5 — A released version needs correction.** `[DERIVED]` It cannot be edited. A new version is created from it. The flawed version remains, is marked deprecated, and remains referenceable — because past decisions were made using it and must stay reconstructable.

### Failure flows
- **F1 — Ingestion partially fails.** Nothing is partially committed. The draft either accepts the batch or reports precisely which examples failed and why.
- **F2 — Two users edit the same draft concurrently.** Detected and reported; one does not silently overwrite the other.

**Postconditions.** An immutable, approved, access-controlled dataset version with lineage, provenance, quality-check results, and an approval record.

---

## UC-06 — Define and reuse a benchmark suite

**Primary persona:** U-3 Priya · **Secondary:** U-4 Tomas
**Capabilities:** CAP-06 · **Priority: P0**
**Canonical basis:** `[CANON §6]` — *"Benchmark Suite Registry with reusable suites, evaluator configurations, thresholds, versions, and ownership."*

**Trigger.** Priya wants a standard, reusable definition of "how we evaluate this class of system," so that results are comparable across teams.

**Preconditions.** At least one approved dataset version. Available evaluators and judges.

### Main flow
1. Priya creates a benchmark suite and names it.
2. She binds it to specific dataset versions, specific evaluator versions, and specific judge versions.
3. She configures thresholds and the consensus approach for judged metrics.
4. She assigns ownership.
5. She releases a suite version, which becomes immutable.
6. Teams reference the released suite version. Every team using it measures the same thing in the same way.
7. When the suite needs to change, a new suite version is created. Existing results remain attributed to the version that produced them.

### Alternate flows
- **A1 — Suite references a draft dataset version.** Release is blocked. A released suite may only reference released, immutable inputs — otherwise its results are not reproducible.
- **A2 — An evaluator used by the suite is superseded.** A new suite version is required to adopt it. The existing suite version continues to work unchanged, so historical comparisons remain valid.
- **A3 — A suite is needed for a system type it was not designed for.** A new suite is created rather than the existing one being loosened, so that the meaning of the original does not drift.

### Failure flows
- **F1 — Configuration is incompatible.** For example, an evaluator requiring reference answers is bound to a dataset without them. Detected at definition time, not at run time.
- **F2 — A referenced version no longer exists.** Cannot occur for released suites, because released inputs are immutable and are not deletable. This invariant is what makes the suite meaningful.

**Postconditions.** A released, immutable, owned suite version that fully determines what will be measured.

---

## UC-07 — Reproduce a past evaluation

**Primary persona:** U-1 Maya, U-6 Rachel · **Secondary:** U-2 Devin
**Capabilities:** CAP-07 · **Priority: P0**
**Canonical basis:** `[CANON §6]`, `[CANON §9]` — reproducible experiment tracking; replay from captured configuration and artifacts.

**Trigger.** Someone needs to re-examine a past evaluation — to verify a decision, to investigate a discrepancy, or to answer an audit question.

**Preconditions.** The original run exists with its configuration and artifacts retained.

### Main flow
1. The user locates the past run.
2. The platform displays the complete configuration that produced it.
3. The user chooses one of two reproduction modes:
   - **Re-scoring** — re-evaluate the stored outputs, optionally with different or newer evaluator and judge versions, without regenerating anything.
   - **Full re-execution** — regenerate outputs from the captured configuration and score them again.
4. The platform produces the new result alongside the original.
5. The platform reports what was reused and what was recomputed.

### Alternate flows
- **A1 — Re-scoring with a newer judge version.** `[DERIVED]` Answers "what would our current judge have said about last month's outputs?" The result is attributed to the new judge version and is never pooled with results from the old one.
- **A2 — Full re-execution produces different outputs.** `[GATE-0]` Expected. Language model providers do not guarantee identical outputs across time even for identical inputs. **The platform's reproducibility guarantee is that the configuration is captured exactly and the artifacts are retained — not that generated text will be byte-identical.** The platform states this plainly rather than implying a guarantee it cannot make.
- **A3 — The model used originally is deprecated or withdrawn.** Full re-execution is impossible. Re-scoring of stored outputs remains possible. The platform explains the distinction.

### Failure flows
- **F1 — Artifacts were removed under a retention policy.** Re-scoring is no longer possible. The platform states this explicitly rather than silently producing a partial reproduction.
- **F2 — Content was removed under a deletion request.** `[GATE-0]` The run's aggregate results and its record remain valid and immutable; per-example drill-down for the removed content is unavailable, and the run is visibly marked as having degraded evidence. See UC-18.

**Postconditions.** A reproduction result, and a clear statement of what could and could not be reproduced.

---

## UC-08 — Detect a regression against a baseline

**Primary persona:** U-4 Tomas · **Secondary:** U-1, U-2
**Capabilities:** CAP-08 · **Priority: P0**
**Canonical basis:** `[CANON §6]`, `[CANON §9]` — regression detection using baselines, absolute/relative thresholds, and statistically appropriate comparisons; uncertainty, effect size, minimum-sample guidance, protection against misleading tiny deltas.

**Trigger.** A candidate run completes and a comparison against an approved baseline is requested.

**Preconditions.** An approved baseline exists and is comparable with the candidate.

### Main flow
1. The platform verifies comparability — same dataset version, same evaluator and judge versions, same consensus configuration. **An incomparable pair is refused, not approximated.**
2. The platform compares candidate against baseline per metric.
3. For each metric the platform reports the observed difference, the uncertainty around it, and whether the difference is large enough to matter.
4. The platform classifies each metric: improvement, no detectable change, regression, insufficient evidence, or limited by the measuring instrument itself.
5. The platform reports the evidence behind each classification.

### Alternate flows
- **A1 — Difference is statistically detectable but trivially small.** `[CANON §9]` Reported as such. A detectable difference that is too small to matter is not a regression, and treating it as one is how gates lose credibility.
- **A2 — Many metrics compared at once.** `[DERIVED]` The platform accounts for the fact that checking many metrics simultaneously increases the chance that at least one appears to have regressed by chance. Without this, gates raise false alarms and get disabled. *(The specific method is an architecture decision, not a product decision, and is deferred.)*
- **A3 — Judge variability exceeds the observed difference.** `[GATE-0]` The platform reports that the measuring instrument cannot resolve a difference of this size. This is distinct from "no change" — one says the systems are alike, the other says the measurement is not precise enough to tell. Conflating them is a serious evaluation error.
- **A4 — Sample size is inadequate.** The platform reports insufficient evidence and indicates what sample size would be needed.
- **A5 — Baseline is stale.** Flagged, with the age and the reason it may no longer be representative.

### Failure flows
- **F1 — Baseline and candidate used different evaluator versions.** Comparison refused with an explanation. Comparing scores produced by different evaluator versions produces a number with no meaning.
- **F2 — Candidate run is incomplete.** The comparison reports on what was measured and states the completeness. If completeness is below the configured minimum, the result is inconclusive.

**Postconditions.** A per-metric comparison result with classification, uncertainty, and evidence.

---

## UC-09 — Enforce a release gate in CI/CD

**Primary persona:** U-4 Tomas, U-1 Maya
**Capabilities:** CAP-09, CAP-08, CAP-12 · **Priority: P0**
**Canonical basis:** `[CANON §11]` — CLI and API for CI; PR evaluation against approved baseline; hard fail, warning, manual approval, and policy exception modes; policies combining quality, cost, latency, safety, judge agreement, and task-specific metrics; machine-readable and human-readable reports with exact evidence.

**Trigger.** A pull request, a release candidate, or a deployment.

**Preconditions.** A release policy is defined and versioned. A comparison result is available (UC-08).

### Main flow
1. Continuous integration invokes the platform's gate evaluation.
2. The platform evaluates the release policy against the comparison result. A policy may combine quality metrics, cost, latency, safety outcomes, judge agreement, and run completeness.
3. Each policy clause produces an outcome with the exact evidence that produced it.
4. The platform produces an overall decision in one of the policy's modes: pass, hard fail, warning, or manual approval required.
5. The platform returns both a machine-readable result and a human-readable report.
6. Continuous integration acts on the result.
7. The decision is recorded permanently with its evidence.

### Alternate flows
- **A1 — Manual approval mode.** The release is held. A designated approver reviews the evidence and approves or rejects. The decision, the approver, and the justification are recorded.
- **A2 — Policy exception.** `[CANON §11]` An exception is granted with a recorded justification, an identified approver, and an expiry. An expired exception stops applying automatically. **An exception is a governed record, never a silent bypass.**
- **A3 — Warning mode.** The release proceeds and the concern is recorded and surfaced.
- **A4 — Policy references an unavailable metric.** The clause reports as unevaluable rather than passing by default. `[DERIVED]` A clause that cannot be evaluated must never count as satisfied — that would be a gate that silently stops gating.

### Failure flows
- **F1 — Evaluation run failed entirely.** The gate cannot decide. It returns a failure state distinct from "fail," because "we could not measure" is not the same as "it regressed," and the two require different responses from the developer.
- **F2 — Run completeness below the policy minimum.** `[GATE-0]` Inconclusive. Never pass. `[CANON §11]`
- **F3 — Platform unreachable from CI.** The CI integration reports a platform failure distinctly from a quality failure, so that a developer is never told their code regressed when in fact the evaluation service was down.

**Postconditions.** A permanent gate decision record with per-clause evidence, mode, and any approval or exception.

---

## UC-10 — Run a scheduled benchmark

**Primary persona:** U-2 Devin
**Capabilities:** CAP-10 · **Priority: P1**
**Canonical basis:** `[CANON §6]`, `[CANON §11]` — scheduled evaluations.

**Trigger.** A configured schedule fires.

**Preconditions.** A schedule is configured against a project, a suite, and a target system.

### Main flow
1. The schedule fires and the platform starts a run.
2. The run executes with the same guarantees as any other run.
3. Results are recorded and compared against the trend history.
4. Configured alerts fire on threshold breach or on drift.
5. Devin reviews results without having initiated anything.

### Alternate flows
- **A1 — A previous scheduled run is still executing.** The overlap policy governs: skip, queue, or run concurrently. **This is configured explicitly, never defaulted silently**, because each choice produces materially different behaviour under sustained slowness.
- **A2 — A scheduled run was missed** because the platform was unavailable. The missed-run policy governs whether it is executed late or skipped, and the miss is recorded either way.
- **A3 — A quality trend degrades without any deliberate change.** `[DERIVED]` Surfaced as drift. This is one of the most valuable signals the platform produces, because it detects changes originating outside the customer's control.

### Failure flows
- **F1 — Repeated scheduled failures.** Alerted as an operational problem, distinct from a quality problem.
- **F2 — Scheduled runs exhaust a budget.** Halted; alerted; partial results retained. Subsequent runs do not silently continue spending.

**Postconditions.** A run record indistinguishable in structure from a manually triggered run, plus trend data and any alerts.

---

## UC-11 — Evaluate after deployment (canary and drift)

**Primary persona:** U-2 Devin · **Secondary:** U-4 Tomas
**Capabilities:** CAP-10, CAP-08 · **Priority: P1**
**Canonical basis:** `[CANON §11]` — *"Canary/post-deployment evaluation and rollback recommendations; do not make unsafe autonomous production changes."*

**Trigger.** A deployment completes, or a canary is in progress.

**Preconditions.** The deployed system is reachable for evaluation. A baseline exists representing the pre-deployment state.

### Main flow
1. Deployment triggers a post-deployment evaluation.
2. The platform evaluates the deployed system against the benchmark suite.
3. Results are compared against the pre-deployment baseline.
4. If a regression is detected, the platform produces a **rollback recommendation** with the supporting evidence.
5. Devin or an authorized person decides whether to act.

### Alternate flows
- **A1 — Canary comparison.** Canary and stable are evaluated and compared directly.
- **A2 — Regression detected.** A recommendation is produced and an alert is raised. **The platform never performs the rollback.** `[CANON §11]`, `[CANON §25]` This boundary is absolute: the platform has no capability to modify a customer's production deployment, and this is a deliberate product decision rather than a missing feature.
- **A3 — Post-deployment quality differs from pre-deployment prediction.** Surfaced explicitly, because it indicates the evaluation dataset may not represent production traffic — which is important information about the evaluation itself, not only about the system.

### Failure flows
- **F1 — Deployed system unreachable.** Reported as an evaluation failure, not as a quality regression.
- **F2 — Evaluating production affects production.** Bounded by rate and concurrency limits declared for the target.

**Postconditions.** A post-deployment run record, a comparison, any recommendation, and an audit trail. **No production change is made by the platform.**

---

## UC-12 — Review quality, cost, and latency analytics

**Primary persona:** U-5 Elena, U-1 Maya, U-2 Devin
**Capabilities:** CAP-11 · **Priority: P1**
**Canonical basis:** `[CANON §12]` — quality trends, leaderboards, latency distributions and tail latency, token and cost per successful task, hallucination and faithfulness trends, judge agreement and calibration, agent tool success and trajectory failures, regression and gate history, dataset and benchmark health.

**Trigger.** A user wants to understand quality, cost, or latency over time or across candidates.

**Preconditions.** Completed runs exist.

### Main flow
1. The user opens analytics scoped to a project, a suite, or a comparison.
2. The platform presents quality trends over time, baseline-versus-candidate comparisons, leaderboards scoped to a specific benchmark, latency distributions including tail behaviour, and token and cost per successful task.
3. The platform presents evaluation-integrity views: judge agreement, judge calibration, judge failure rates, evaluator failures, and dataset and benchmark health.
4. The user drills from any aggregate down to the individual examples that produced it.

### Alternate flows
- **A1 — Trend spans a judge version change.** `[DERIVED]` The change is marked on the trend, and results from different judge versions are not silently joined into one line. A trend that quietly splices two different instruments together is a misleading chart.
- **A2 — Trend spans a dataset version change.** Same treatment.
- **A3 — Leaderboard requested across benchmarks.** Refused or heavily qualified — a leaderboard is only meaningful within a single benchmark, and `[CANON §12]` scopes leaderboards to a benchmark for exactly this reason.

### Failure flows
- **F1 — Insufficient history for a trend.** Stated, rather than drawing a trend line through too few points.
- **F2 — Aggregates would span incomplete runs.** Completeness is shown alongside every aggregate.

**Postconditions.** No state change. Every displayed figure is traceable to the runs that produced it.

---

## UC-13 — Produce an executive scorecard or report

**Primary persona:** U-5 Elena
**Capabilities:** CAP-11 · **Priority: P2**
**Canonical basis:** `[CANON §12]` — scorecards and executive reports.

**Trigger.** A periodic review, or a decision requiring a summary.

**Preconditions.** Sufficient completed runs.

### Main flow
1. Elena requests a report for a project and a period.
2. The platform produces a summary of quality status, notable changes, cost and latency, and release-gate history.
3. Differences are presented with an indication of whether they are meaningful.
4. The report is available in human-readable and machine-readable form and can be scheduled.

### Alternate flows
- **A1 — Nothing meaningful changed.** The report says so. `[DERIVED]` A report that manufactures a narrative from noise is worse than no report, because it will be repeated to leadership.
- **A2 — Data is insufficient for a period.** Stated plainly rather than extrapolated.

### Failure flows
- **F1 — Report would include sensitive dataset content.** `[CANON §16]` Redaction applies by default. Reports leave the platform, and the default must be safe rather than convenient.

**Postconditions.** A report artifact; no change to underlying data.

---

## UC-14 — Answer a governance or audit question

**Primary persona:** U-6 Rachel · **Secondary:** U-3, U-4
**Capabilities:** CAP-12, CAP-07 · **Priority: P0**
**Canonical basis:** `[CANON §4]`, `[CANON §16]` — who changed what, what evidence supported release, who approved it, whether quality later drifted; audit of dataset, prompt, model, benchmark, evaluator, policy, approval, and release changes.

**Trigger.** An internal or external audit, an incident review, or a periodic governance check.

**Preconditions.** The platform has been in use. Rachel holds the necessary permission.

### Main flow
1. Rachel identifies a release decision.
2. The platform shows: what changed, the evidence that supported the decision, the policy applied, the decision and its mode, who approved it, when, and any exception.
3. Rachel traces from the decision to the exact dataset version, evaluator versions, and judge versions used, and verifies the dataset version is unchanged since.
4. Rachel examines quality after the release to determine whether it drifted.
5. Rachel exports the evidence.

### Alternate flows
- **A1 — Question concerns a dataset.** Provenance, approvals, quality-check results, access history, and every run that used it are available.
- **A2 — Question concerns an exception.** Justification, approver, and expiry are available.
- **A3 — Question concerns access.** Who accessed what, and when.

### Failure flows
- **F1 — Records fall outside a retention window.** The platform states what is retained and what is not. It does not present a partial history as complete.
- **F2 — Content was removed under a deletion request.** `[GATE-0]` The governance record and the proof of what was evaluated remain intact; the removed content itself is unavailable, and this is visible rather than silent. See UC-18.

**Postconditions.** An exportable evidence package. No change to any record — audit access is itself audited.

---

## UC-15 — Resolve judge disagreement through human review

**Primary persona:** U-4 Tomas, U-1 Maya
**Capabilities:** CAP-03, CAP-08, CAP-12 · **Priority: P1**
**Canonical basis:** `[CANON §8]` — *"low-agreement cases can escalate to human review."* `[GATE-0]` Minimal human review queue approved for v1.

**Trigger.** Judges disagree beyond a configured threshold, a judgment is flagged as possibly manipulated, or a policy explicitly requires human review.

**Preconditions.** A judged run has completed. Reviewers are designated.

### Main flow
1. The platform identifies cases requiring human attention and places them in a review queue.
2. A reviewer sees the input, the output, each judge's score and rationale, and the reason for escalation.
3. The reviewer records a decision and a rationale.
4. The decision is recorded and attributed.
5. The human decision informs the affected result and becomes reference data for assessing judge quality.

### Alternate flows
- **A1 — Escalation due to suspected manipulation of a judge.** `[CANON §16]` The suspicious content is highlighted. The reviewer can mark the dataset example for correction, which flows into UC-05.
- **A2 — The reviewer also cannot decide.** Recorded as genuinely ambiguous. `[DERIVED]` This is valuable information — it usually means the rubric is underspecified, which is a fixable problem in the evaluation rather than in the system being evaluated.
- **A3 — Queue exceeds review capacity.** Prioritized by impact — cases affecting a pending gate decision first.

### Failure flows
- **F1 — No reviewer acts before a gate deadline.** The gate reports that required human review did not occur. It does not proceed by assuming a default. `[CANON §25]`

**Postconditions.** A recorded, attributed human decision; an updated result where applicable; reference data for judge quality assessment.

---

## UC-16 — Plan an evaluation with assistance

**Primary persona:** U-1 Maya, U-3 Priya
**Capabilities:** CAP-06, CAP-07 · **Priority: P2**
**Canonical basis:** `[CANON §8]` — *"Evaluation Planner Agent: inspect objective, datasets, benchmark suites, candidate systems, budgets, and policies; produce a typed, reviewable evaluation plan."*

**Trigger.** A user knows what they want to learn but not how to configure an evaluation to learn it.

**Preconditions.** Assets exist that the user is authorized to see.

### Main flow
1. Maya states an objective and a budget in her own words.
2. The platform proposes a **plan** — suites, datasets, sample sizes, candidates, judges, and estimated cost.
3. The plan is presented for review as an explicit, editable artifact.
4. Maya reviews, edits, and approves it.
5. Only after approval does the plan execute.

### Alternate flows
- **A1 — The user writes the plan directly.** `[GATE-0]` A fully explicit authoring path exists and is the default for automated and CI usage. **Assistance is optional; the product is completely usable without it.**
- **A2 — The proposed plan exceeds budget.** Reported before execution with the estimate and its basis, so the user can reduce scope deliberately.
- **A3 — The proposal is unsuitable.** Maya edits or discards it. Nothing has been executed or changed.

### Failure flows
- **F1 — A valid plan cannot be produced.** Reported as such within bounded effort. The platform does not retry indefinitely, and does not emit a low-quality plan to avoid admitting failure.
- **F2 — Asset descriptions contain content attempting to manipulate the planning process.** `[CANON §16]` Defended against; the proposal is still subject to validation and human approval before anything runs.

**Postconditions.** A reviewed, approved plan artifact — or nothing at all. **The planning step never executes anything and never modifies any asset.** `[CANON §25]`

---

## UC-17 — Add a custom evaluator

**Primary persona:** U-3 Priya
**Capabilities:** CAP-06, CAP-03, CAP-04 · **Priority: P1**
**Canonical basis:** `[CANON §8]` — *"Plugin Evaluator Framework: stable Evaluator SDK with capability metadata, schemas, versions, dependencies, permissions, cost characteristics, and compatibility constraints."*

**Trigger.** A team needs a domain-specific metric the platform does not provide, or wants to use a third-party evaluation library.

**Preconditions.** Priya holds the necessary permission. `[GATE-0]` The evaluator is authored inside her organization — v1 supports trusted-tenant plugins, not arbitrary untrusted third-party code.

### Main flow
1. Priya implements an evaluator against the published interface.
2. She declares its metadata: what it measures, its input and output schemas, its version, its dependencies, the permissions it requires, its cost characteristics, and what it is compatible with.
3. She registers it. The platform validates the declaration.
4. She verifies it distinguishes known-good from known-bad cases.
5. She binds it into a benchmark suite version.
6. It runs alongside built-in evaluators, with identical treatment for versioning, cost accounting, failure handling, and result provenance.

### Alternate flows
- **A1 — A third-party evaluation library is wrapped.** Registered as an evaluator like any other. The platform's own architecture is unaffected, and the library's version is pinned and recorded.
- **A2 — The evaluator's metric name collides with an existing one.** `[DERIVED]` The platform requires disambiguation. Two different computations sharing a name is a serious source of invalid cross-team comparison — exactly Priya's original problem.
- **A3 — An evaluator needs revision.** A new version is created. Existing results stay attributed to the version that produced them.

### Failure flows
- **F1 — The evaluator crashes, hangs, or exhausts resources.** Bounded and contained. The failure is recorded as an evaluation failure for the affected samples. It does not destabilize the run or the platform.
- **F2 — The evaluator returns output not matching its declared schema.** Rejected as a failure, not coerced into a score.
- **F3 — The evaluator attempts an action it did not declare.** Denied and recorded.

**Postconditions.** A registered, versioned, usable evaluator.

**Documented limitation.** `[GATE-0]` v1 provides defence-in-depth for trusted-tenant plugins. It does **not** claim to safely execute hostile code. This is stated in [`non-goals.md`](non-goals.md) and must be stated to customers rather than implied away.

---

## UC-18 — Honour a data deletion request

**Primary persona:** U-6 Rachel
**Capabilities:** CAP-05, CAP-12 · **Priority: P1**
**Canonical basis:** `[CANON §10]`, `[CANON §16]` — PII and sensitive-data handling, retention and deletion, redaction. `[GATE-0]` Resolution approved at Architecture Gate 0.

**Trigger.** A data-subject deletion request, a retention obligation, or discovery that sensitive content entered an evaluation dataset.

**Preconditions.** The content is identified. Rachel holds the necessary permission.

### Main flow
1. Rachel identifies the content to be removed and submits a deletion request.
2. The platform identifies **everywhere that content propagated** — the dataset example itself, generated outputs derived from it, retrieved contexts, agent trajectories, judge rationales quoting it, cached copies, and any report containing it.
3. The platform removes the content from all of those locations.
4. The platform **retains** the record that an example existed at that position in the dataset version, and the cryptographic evidence of what was evaluated — so that past decisions remain provably about what they were about.
5. Runs that depended on the removed content are marked as having degraded evidence: their aggregate results remain valid and immutable, but per-example inspection of the removed content is no longer available.
6. The deletion is recorded as an audited governance event with actor, time, and justification.

### Alternate flows
- **A1 — Content is used by many runs.** All are marked. The scope is reported before execution so the impact is known in advance.
- **A2 — Content is in a released, immutable dataset version.** `[GATE-0]` The version's structure and integrity evidence remain immutable; the content within is removed. **Immutability of the record and removal of the payload coexist by design** — this is the resolution to the tension Rachel would otherwise be told is unresolvable.
- **A3 — Only redaction is required, not removal.** Redacted fields are replaced; the example remains usable for evaluation.

### Failure flows
- **F1 — Removal cannot complete everywhere.** The request does not report success. Partial deletion reported as complete would be a false compliance claim, which is worse than an honest failure.
- **F2 — Deletion would break an active legal hold.** Blocked, with the conflict reported for human resolution.

**Postconditions.** Content unreachable from every location. Structural and audit records intact. Affected runs visibly marked. An audit entry created.

---

## Capability coverage

Every canonical capability is exercised by at least one use case. `[CANON §6]`

| Capability | Use cases |
|---|---|
| **CAP-01** Prompt regression testing | UC-01 |
| **CAP-02** Model migration & provider comparison | UC-02 |
| **CAP-03** RAG evaluation | UC-03, UC-15, UC-17 |
| **CAP-04** Agent evaluation | UC-04, UC-17 |
| **CAP-05** Golden Dataset Manager | UC-05, UC-18 |
| **CAP-06** Benchmark Suite Registry | UC-06, UC-16, UC-17 |
| **CAP-07** Reproducible experiment tracking | UC-01, UC-02, UC-07, UC-14, UC-16 |
| **CAP-08** Regression detection | UC-01, UC-08, UC-09, UC-11, UC-15 |
| **CAP-09** CI/CD quality gates | UC-09 |
| **CAP-10** Scheduled & post-deployment evaluation | UC-10, UC-11 |
| **CAP-11** Analytics & reporting | UC-02, UC-12, UC-13 |
| **CAP-12** Enterprise governance | UC-05, UC-09, UC-14, UC-15, UC-18 |

## Persona coverage

| Persona | Use cases |
|---|---|
| **U-1** Maya | UC-01, UC-02, UC-03, UC-04, UC-05, UC-07, UC-08, UC-09, UC-12, UC-15, UC-16 |
| **U-2** Devin | UC-02, UC-07, UC-08, UC-10, UC-11, UC-12 |
| **U-3** Priya | UC-05, UC-06, UC-14, UC-16, UC-17 |
| **U-4** Tomas | UC-01, UC-06, UC-08, UC-09, UC-11, UC-14, UC-15 |
| **U-5** Elena | UC-02, UC-12, UC-13 |
| **U-6** Rachel | UC-05, UC-07, UC-14, UC-18 |

---

## Cross-cutting behaviours

Behaviours appearing across many use cases. Recorded once here so that M1.3 captures them as cross-cutting requirements rather than repeating them per feature — and so that they are not lost by being implicit everywhere and owned nowhere.

| # | Behaviour | Appears in |
|---|---|---|
| **X-1** | Incomplete results are visibly incomplete everywhere they appear | UC-01, UC-02, UC-08, UC-09, UC-10, UC-12 |
| **X-2** | Failed and abstained evaluations are never recorded as zero scores | UC-01, UC-03, UC-04, UC-17 |
| **X-3** | Insufficient evidence is a distinct outcome from "no change" | UC-01, UC-08, UC-09 |
| **X-4** | Version changes invalidate comparability, and this is enforced rather than warned about | UC-06, UC-07, UC-08, UC-12 |
| **X-5** | Governance-relevant actions are audited with actor, time, and justification | UC-05, UC-09, UC-14, UC-15, UC-18 |
| **X-6** | The platform recommends; it never autonomously changes production, datasets, policies, or release decisions | UC-11, UC-16, UC-18 |
| **X-7** | Content originating outside the platform is treated as untrusted | UC-03, UC-04, UC-05, UC-15, UC-16 |
| **X-8** | Every reported figure is traceable to the run and samples that produced it | UC-12, UC-13, UC-14 |
| **X-9** | Budget is estimated before execution and enforced during it | UC-02, UC-09, UC-10, UC-16 |
| **X-10** | Platform failures are reported distinctly from quality failures | UC-01, UC-09, UC-10, UC-11 |

**X-10 is easy to underestimate.** If a developer is told "your change regressed quality" when in fact the evaluation service was unavailable, they lose trust in every subsequent verdict. Distinguishing these two is a product requirement, not an error-handling detail.

---

## Document history

| Version | Milestone | Change |
|---|---|---|
| 0.1 | M1.1 | Initial draft. Pending external review. |
