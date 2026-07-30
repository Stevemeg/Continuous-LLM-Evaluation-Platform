# Product Requirements Document
## Continuous LLM Evaluation & Regression Testing Platform

| Field | Value |
|---|---|
| Document | Product Requirements Document (PRD) |
| Status | **Draft — pending external review** |
| Milestone | M1.1 — Product Definition, Personas, and Use Cases |
| Phase | Phase 1 — Product Foundation |
| Governing specification | `Continuous LLM Evaluation Platform - Canonical Master Prompt v3.docx` (immutable; held locally, not distributed in this repository) |
| Supersedes | Nothing |
| Related documents | [`personas.md`](personas.md) · [`use-cases.md`](use-cases.md) · [`success-criteria.md`](success-criteria.md) · [`non-goals.md`](non-goals.md) |

### Reading conventions used in this document

| Marker | Meaning |
|---|---|
| `[CANON §n]` | Requirement traced directly to section *n* of the canonical specification. The canonical document is authoritative. |
| `[PREMISE]` | A statement about the world that this product's value depends on. Stated as a premise, **not** as a measured fact. Where the premise originates in the canonical specification, that is cited. Premises are falsifiable and are revisited in M1.2 (competitive analysis) and M1.3 (requirements). |
| `[DERIVED]` | A product decision that follows from canonical requirements but is not literally stated in them. |
| `[DEFERRED → Mx.y]` | Deliberately not decided here; owned by the named milestone. |

> **No measured results appear in this document.** Nothing in this PRD reports a benchmark, latency figure, cost figure, accuracy figure, or quality metric produced by this system, because this system does not yet exist. Targets appear only in [`success-criteria.md`](success-criteria.md) and are explicitly labelled as unvalidated targets.

---

## 1. Product definition

### 1.1 What this product is

`[CANON §2]` A **commercial, multi-tenant control plane for AI quality** — positioned as *"GitHub Actions for AI Quality."*

The product's central contract with its user:

> **No meaningful change to a prompt, model, provider, retrieval strategy, tool, agent graph, dataset, or AI policy reaches production without being measured against a controlled, versioned baseline — and every release decision leaves a reproducible evidence trail.**

`[CANON §3]` Stated as a vision: the enterprise control plane for AI quality, which centralizes evaluation assets, executes reproducible evaluations, detects regressions, compares candidates, enforces release gates, preserves audit history, and explains why an AI release should or should not ship.

### 1.2 What this product is explicitly not

`[CANON §2]` The canonical specification names five things this product must not be. They are reproduced here verbatim in substance because they are product-defining constraints, not stylistic preferences:

1. Not a benchmark notebook
2. Not an evaluation script collection
3. Not a thin wrapper around RAGAS / DeepEval
4. Not a hackathon demo
5. Not an academic experiment

The full non-goal set, including the anti-patterns from `[CANON §25]`, is in [`non-goals.md`](non-goals.md).

### 1.3 The distinction that defines the product

`[CANON §8]` Third-party evaluation libraries (RAGAS, DeepEval, Promptfoo, OpenAI Evals) are **metric implementations**. This platform is the **control plane** around them.

The defensible product is everything those libraries do not provide:

| Libraries provide | This platform provides |
|---|---|
| Metric computation | Versioned, governed evaluation assets |
| A function you call | Run identity and reproducibility |
| A score | A baseline, a statistical comparison, and an uncertainty statement |
| A number in a notebook | A release gate decision with evidence |
| Per-developer usage | Multi-tenant governance, RBAC, approvals, and audit |

`[CANON §8]` states this directly: *"RAGAS, DeepEval, Promptfoo, and OpenAI Evals are adapters — not the architecture."*

---

## 2. Business problem

`[CANON §4]` The canonical specification identifies five problems. Each is restated below with the product consequence it creates. These are **premises**: they describe the conditions under which this product has value.

### BP-1 — AI systems change continuously along axes traditional testing does not cover
`[CANON §4]` `[PREMISE]` AI teams continuously change prompts, models, retrieval strategies, tools, agent graphs, and providers.

*Product consequence:* the unit of change that must be testable is not "a code commit." It is any versioned change to a prompt, a model configuration, a retrieval strategy, a tool definition, an agent graph, a dataset, or an AI policy. The platform must therefore treat all of these as first-class, versioned, comparable artifacts — not just code.

### BP-2 — Traditional tests cannot measure semantic quality
`[CANON §4]` `[PREMISE]` Traditional tests cannot adequately measure semantic quality, hallucination, faithfulness, retrieval quality, task completion, or judge disagreement.

*Product consequence:* assertion-based testing over free-form natural language is either trivially weak (exact-match on a paraphrasable answer) or brittle (regex on generated prose). The platform must support a spectrum from fully deterministic evaluators through to probabilistic judgment, **and must never conflate the two**.

### BP-3 — Manual evaluation does not scale and is not reproducible
`[CANON §4]` `[PREMISE]` Manual evaluation is slow, inconsistent, expensive, difficult to reproduce, and often performed too late.

*Product consequence:* evaluation must be automatable, schedulable, and CI-invocable, and must produce identical answers when re-asked the same question — which makes reproducibility a core capability rather than a nicety.

### BP-4 — Regressions reach customers
`[CANON §4]` `[PREMISE]` Regressions reach customers because teams lack versioned golden datasets, statistically defensible comparisons, automated gates, and traceable evaluation history.

*Product consequence:* this names the four missing pieces the product must supply. Note that all four are required together — versioned datasets without statistical comparison produce confident nonsense; statistical comparison without automated gates produces reports nobody acts on.

### BP-5 — Enterprises require governance, not just measurement
`[CANON §4]` `[PREMISE]` Enterprises require governance: who changed what, what evidence supported release, who approved it, and whether quality later drifted.

*Product consequence:* these are four distinct audit questions, and each maps to a durable record the platform must keep: change history, evidence linkage, approval records, and post-release monitoring. Governance is therefore a data-model concern from the beginning, not a feature bolted on late.

### BP-6 — Quality is not a single axis `[DERIVED]`
`[CANON §6]`, `[CANON §12]` The canonical specification repeatedly pairs quality with cost and latency (quality/cost/latency dashboards; token and cost per successful task; latency distributions and tail latency).

*Product consequence:* "better" is not a decidable claim without cost and latency alongside it. A model migration decision is a three-axis trade-off. The platform must produce all three axes from a single reproducible run, or it forces users back into ad-hoc measurement for two of the three.

---

## 3. Value proposition

`[DERIVED]` from `[CANON §3]`, `[CANON §4]`, `[CANON §6]`. Stated the way a buyer would state it.

| # | Value | What it means concretely | Primary canonical basis |
|---|---|---|---|
| **V-1 Prevent** | Catch quality regressions in the pull request, before customers encounter them — the equivalent of what continuous integration did for functional defects | `[CANON §11]` |
| **V-2 Decide** | Turn model, provider, prompt, and retrieval migrations into defensible engineering decisions backed by quality, cost, and latency evidence rather than a demo and an opinion | `[CANON §6]`, `[CANON §12]` |
| **V-3 Standardize** | Give an organization one evaluation substrate, so results are comparable across teams and across time instead of every team maintaining a private notebook with private conventions | `[CANON §5]`, `[CANON §6]` |
| **V-4 Prove** | Produce an audit trail linking a release decision to the dataset version, evaluator versions, statistical evidence, policy, and human approver | `[CANON §4]`, `[CANON §16]` |
| **V-5 Economize** | Make evaluation spend visible and governable by accounting for token and cost consumption at every level of granularity | `[CANON §9]`, `[CANON §12]` |
| **V-6 Be honest** `[DERIVED]` | Report when the evidence is insufficient to support a conclusion, rather than producing a confident verdict from noise | `[CANON §9]`, `[CANON §25]` |

**On V-6.** `[CANON §9]` requires uncertainty and confidence reporting, effect size, minimum-sample guidance, and *"protection against misleading tiny deltas."* `[CANON §25]` rejects *"arbitrary quality-gate scores without baselines or uncertainty"* and *"single LLM-as-a-Judge treated as ground truth."* Taken together these are not three separate features — they describe a product whose distinguishing behaviour is that it declines to answer when it cannot answer well. This PRD treats **calibrated honesty as a headline value proposition**, not an implementation detail.

---

## 4. Target market and users

`[CANON §5]` Six user groups are named in the canonical specification. Each is developed into a persona in [`personas.md`](personas.md).

| Group | Canonical description |
|---|---|
| U-1 | AI/GenAI engineers validating prompt, model, RAG, and agent changes |
| U-2 | ML/LLMOps teams running scheduled benchmarks and post-deployment evaluations |
| U-3 | AI platform teams exposing evaluation as an internal service |
| U-4 | QA teams defining regression thresholds and release policies |
| U-5 | Product teams comparing quality, latency, and cost |
| U-6 | Regulated enterprises requiring reproducibility, auditability, approvals, and dataset governance |

`[DERIVED]` **Buying centre vs. usage centre.** U-1 and U-2 are the daily users and the source of adoption. U-3 is the internal champion who makes the platform standard. U-4 and U-5 are the consumers of its output. U-6 supplies the compliance requirement that converts "useful tool" into "funded platform." A product that satisfies only U-1 becomes a developer utility; one that satisfies only U-6 becomes shelfware. **Both ends must be served.**

`[DEFERRED → M1.2]` Market sizing, competitive positioning, pricing, and go-to-market.

---

## 5. Product capabilities

`[CANON §6]` The canonical specification defines twelve core capabilities. They are assigned stable capability identifiers here for traceability. These identifiers are **product-level**, not requirement identifiers; the formal requirement set with `REQ-*` identifiers is `[DEFERRED → M1.3]`.

| ID | Capability | Canonical text (substance) |
|---|---|---|
| **CAP-01** | Prompt regression testing | Prompt regression testing with approved baselines and version history |
| **CAP-02** | Model migration & provider comparison | Model migration / provider comparison across hosted and self-hosted models |
| **CAP-03** | RAG evaluation | Retrieval quality, context relevance, faithfulness, groundedness, citation quality, answer relevance, hallucination analysis |
| **CAP-04** | Agent evaluation | Task success, tool-selection correctness, tool-call validity, trajectory/planning quality, loop detection, recovery, final-answer quality |
| **CAP-05** | Golden Dataset Manager | Versioning, lineage, provenance, schemas, splits, approvals, access control, immutable release snapshots |
| **CAP-06** | Benchmark Suite Registry | Reusable suites, evaluator configurations, thresholds, versions, ownership |
| **CAP-07** | Reproducible experiment tracking | Reproducible experiment tracking |
| **CAP-08** | Regression detection | Baselines, absolute/relative thresholds, statistically appropriate comparisons |
| **CAP-09** | CI/CD quality gates | Gates for pull requests, releases, and deployments |
| **CAP-10** | Scheduled & post-deployment evaluation | Scheduled and post-deployment evaluations |
| **CAP-11** | Analytics & reporting | Quality/cost/latency dashboards, scorecards, alerts, executive reports |
| **CAP-12** | Enterprise governance | Organizations, projects, RBAC, API keys, audit logs, approvals, retention, traceability |

### 5.1 Capability dependency structure `[DERIVED]`

The twelve capabilities are not independent. Their dependency structure is a product fact that constrains delivery sequencing:

```
CAP-12 governance ──────────► underlies every other capability
CAP-05 datasets ─────┐
CAP-06 suites  ──────┼──────► CAP-07 reproducible experiments
                     │              │
                     │              ├──► CAP-01 prompt regression
                     │              ├──► CAP-02 model migration
                     │              ├──► CAP-03 RAG evaluation
                     │              └──► CAP-04 agent evaluation
                     │                        │
                     └────────────────────────┴──► CAP-08 regression detection
                                                        │
                                                        ├──► CAP-09 CI/CD gates
                                                        └──► CAP-10 scheduled/post-deploy
                                                                  │
                                                                  └──► CAP-11 analytics
```

Two consequences worth stating explicitly at product level:

1. **CAP-08 (regression detection) is the product's centre of gravity.** CAP-01 through CAP-04 are four applications of the same underlying comparison; CAP-09 and CAP-10 are two delivery channels for its verdict. A platform with excellent CAP-03 and weak CAP-08 is a metrics library.
2. **CAP-12 (governance) cannot be sequenced last.** Tenancy, project ownership, and audit are properties of every record the other eleven capabilities create. This is recorded here as a *product* observation; its architectural consequence was raised and approved at Gate 0.

### 5.2 Capability requiring particular care: CAP-03 and CAP-04 `[DERIVED]`

CAP-03 and CAP-04 evaluate systems the platform does not own — a customer's retrieval pipeline, a customer's agent. This means the platform must be able to observe intermediate state (retrieved contexts, tool calls, trajectories), not merely final outputs. That is a product requirement with real integration consequences for the customer, and it is called out here so that M1.3 captures it as an explicit requirement rather than discovering it during implementation.

---

## 6. Product principles

`[DERIVED]` from `[CANON §7]`, `[CANON §9]`, `[CANON §22]`, `[CANON §25]`. These are the rules a reviewer should be able to hold the product to.

| # | Principle | Origin |
|---|---|---|
| **PR-1** | **Reasoning where it adds value, conventional software everywhere else.** Persistence, authentication, queueing, deterministic metrics, and threshold checks are ordinary software. Reasoning components are used only where the input is genuinely under-specified. | `[CANON §7]` |
| **PR-2** | **Deterministic evaluators and probabilistic judges are never conflated.** They are separated structurally, not by naming convention. | `[CANON §9]` |
| **PR-3** | **No single judge is treated as ground truth.** | `[CANON §25]` |
| **PR-4** | **Nothing that affects a result is unversioned.** Datasets, prompts, models, rubrics, evaluators, and judges all carry identity and version. | `[CANON §25]` |
| **PR-5** | **A verdict without uncertainty is not a verdict.** Gate decisions carry the evidence that produced them, including the confidence in that evidence. | `[CANON §9]`, `[CANON §25]` |
| **PR-6** | **Governance decisions stay with humans.** The platform never autonomously modifies golden datasets, policies, or release decisions. It recommends; people decide. | `[CANON §25]`, `[CANON §11]` |
| **PR-7** | **Complexity must be earned.** Every substantial dependency or infrastructure component solves a documented requirement. | `[CANON §22]`, `[CANON §25]` |
| **PR-8** | **No claim without evidence.** No metric, benchmark, coverage figure, or quality claim is stated anywhere unless an executed run produced it and the raw output is retained. | `[CANON §20]`, `[CANON §24]` |
| **PR-9** | **Security is a design constraint, not a hardening phase.** Tenant isolation, credential custody, and untrusted-content handling are present from the first record created. | `[CANON §16]` |
| **PR-10** | **Incomplete evidence never passes a gate.** A run that did not produce enough successful measurements yields an inconclusive result, never a pass. | `[CANON §9]`, `[CANON §11]` |

**PR-8 and PR-10 are the two principles most likely to be quietly violated under delivery pressure**, and are therefore the two most heavily enforced by the universal quality gates approved at Gate 0.

---

## 7. Data sensitivity classes

`[DERIVED]` from `[CANON §10]`, `[CANON §16]`. Identified here so that the M2.5 threat model has a product-level input rather than inventing its own taxonomy. No handling mechanism is specified — that is `[DEFERRED → Phase 2]`.

| Class | Contents | Why it is sensitive |
|---|---|---|
| **DS-1 Golden dataset content** | Inputs, reference answers, contexts, labels | Frequently derived from real customer interactions; may contain personal data; represents proprietary evaluation investment |
| **DS-2 Candidate system outputs** | Generated text, structured outputs | May reproduce or paraphrase sensitive input content |
| **DS-3 Retrieved contexts** | Documents surfaced by a customer's retrieval system | Often the most sensitive data in the system — internal documents the customer never intended to expose |
| **DS-4 Agent trajectories** | Tool calls, tool inputs and outputs, intermediate reasoning | Tool inputs routinely contain identifiers, queries, and credentials-adjacent parameters |
| **DS-5 Judge rationales** | Natural-language explanations produced by judges | Frequently quote DS-1/DS-2/DS-3 content verbatim; a commonly overlooked propagation path |
| **DS-6 Prompts** | System prompts, templates | Encode proprietary business logic and competitive differentiation |
| **DS-7 Provider credentials** | Tenant-supplied API keys | Direct financial and data-access exposure if leaked |
| **DS-8 Cost and usage records** | Spend by tenant, project, run | Commercially confidential; reveals scale and activity |
| **DS-9 Audit and governance records** | Who changed what, who approved what | Integrity-critical rather than confidentiality-critical; must be tamper-evident |

**DS-5 deserves specific emphasis.** Judge rationales are generated text that quotes evaluated content. Any redaction, retention, or deletion obligation that applies to DS-1 through DS-3 propagates to DS-5, and this is easy to miss because rationales feel like system output rather than customer data.

---

## 8. Product scope boundaries

### 8.1 In scope
The twelve capabilities CAP-01 … CAP-12, and the governance, reproducibility, and honesty properties described in §3 and §6.

### 8.2 Out of scope
See [`non-goals.md`](non-goals.md) for the complete, reasoned list.

### 8.3 Scope questions carried forward from Architecture Gate 0

These were raised, answered, and approved at Gate 0. They are recorded here because they materially shape the product surface, and a reader of the PRD alone would otherwise not know they had been settled.

| Question | Gate 0 resolution | Product consequence |
|---|---|---|
| How does the platform obtain outputs from the system being evaluated? | Approved: three integration modes — local/in-process, remote HTTPS endpoint, and recorded/offline outputs | Determines how a customer adopts the product. The recorded/offline mode is the low-friction entry point; it lets a team get value before integrating anything. |
| Whose provider credentials are used? | Approved: customer-supplied ("bring your own") credentials as the default model | The platform does not resell inference. Evaluation spend is the customer's, visible to the customer, and governed by the customer's budget. |
| Can customers run arbitrary third-party evaluator code? | Approved for v1: trusted-tenant plugins with defence-in-depth, with the limitation documented explicitly | The extensibility story for v1 is "your team's own evaluators," not "an open marketplace of untrusted code." This limitation is stated publicly rather than implied. |
| Is human review part of v1? | Approved: minimal human review queue in v1 | Judge disagreement escalates to a person. The product does not pretend automated judgment is always sufficient. |
| What regulatory posture is claimed? | Approved: governance-capable and GDPR-informed; **no compliance certification is claimed** | Marketing and documentation must never imply certification the product does not hold. |

---

## 9. Canonical coverage

Traceability of this document to the canonical specification, at Phase 1 maturity. Formal requirement-level traceability is `[DEFERRED → M1.3]` and its mechanical enforcement is deferred to Phase 3.

| Canonical section | Addressed in |
|---|---|
| §2 Non-negotiable product definition | §1.1, §1.2 |
| §3 Product vision | §1.1 |
| §4 Business problem | §2 (BP-1 … BP-5) |
| §5 Target users & use cases | §4, and [`personas.md`](personas.md) |
| §6 Core product capabilities | §5 (CAP-01 … CAP-12), and [`use-cases.md`](use-cases.md) |
| §7 Agentic architecture rule | §6 PR-1 |
| §9 Evaluation harness (product-visible properties only) | §3 V-6, §6 PR-2/PR-5/PR-10 |
| §10 Golden dataset manager | §5 CAP-05, §7 DS-1 |
| §11 CI/CD quality gates | §5 CAP-09, §6 PR-6 |
| §12 Benchmark & analytics | §5 CAP-11, §2 BP-6 |
| §16 Security, governance, multi-tenancy | §5 CAP-12, §7, §6 PR-9 |
| §20 Testing & validation (product-visible only) | §6 PR-8 |
| §24 Portfolio requirements | §6 PR-8 |
| §25 Anti-patterns | §1.2, §6, [`non-goals.md`](non-goals.md) |
| §27 Definition of success | [`success-criteria.md`](success-criteria.md) |

Canonical sections §8, §13, §14, §15, §17, §18, §19, §21, §22, §23, §26 are **architecture, technology, process, or governance** sections. They are deliberately **not** addressed in this PRD; they are owned by Phase 2 and Phase 3 documents.

---

## 10. Open questions

Product-level questions that remain genuinely open after Gate 0. None block M1.1.

| ID | Question | Owner | Why it is not answered here |
|---|---|---|---|
| PQ-1 | For CAP-03/CAP-04, what is the minimum intermediate state a customer must expose for evaluation to be meaningful, and how much value remains if they expose none? | M1.3 | Requires the requirement-level decomposition of RAG and agent metrics to answer precisely |
| PQ-2 | Is a benchmark suite shareable across projects within a tenant, or is it project-scoped? | M1.3 | Affects the governance model; needs the requirement set to reason about ownership and access |
| PQ-3 | When a judge version changes, what is the product's expected behaviour toward existing baselines — invalidate, warn, or offer re-scoring? | M1.3 | Product-visible behaviour with architectural consequences; the mechanism was approved at Gate 0, the user-facing policy was not |
| PQ-4 | Does the product need to support comparing more than two candidates in a single decision, or is pairwise baseline-vs-candidate sufficient for v1? | M1.3 | `[CANON §6]` mentions leaderboards, which implies N-way; the gate flow implies pairwise. Both may be needed for different capabilities. |

---

## 11. Document history

| Version | Milestone | Change |
|---|---|---|
| 0.1 | M1.1 | Initial draft. Pending external review. |
