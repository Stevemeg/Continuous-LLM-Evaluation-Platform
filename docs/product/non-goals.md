# Non-Goals
## Continuous LLM Evaluation & Regression Testing Platform

| Field | Value |
|---|---|
| Status | **Draft — pending external review** |
| Milestone | M1.1 — Product Definition, Personas, and Use Cases |
| Phase | Phase 1 — Product Foundation |
| Source | `[CANON §2]`, `[CANON §7]`, `[CANON §11]`, `[CANON §22]`, `[CANON §25]` |
| Related | [`prd.md`](prd.md) · [`personas.md`](personas.md) · [`use-cases.md`](use-cases.md) · [`success-criteria.md`](success-criteria.md) |

---

## Why this document exists

A scope boundary that is only implied is not a boundary. Every item below is something a reasonable person might expect this product to do, or might be tempted to build, and which it deliberately does not do.

Three kinds of entry appear here:

| Kind | Meaning |
|---|---|
| **NOT THE PRODUCT** | Building this would make it a different, lesser product |
| **REJECTED PRACTICE** | An implementation approach that is forbidden regardless of convenience |
| **OUT OF SCOPE (v1)** | Legitimate, deferred, and revisitable — recorded with the condition that would justify revisiting |

The distinction matters. Confusing "we decided against this" with "we have not got to it yet" is how scope quietly expands.

---

## Part 1 — What this product is not

`[CANON §2]` names five of these directly. Each is expanded with the failure it would represent.

### NG-01 — Not a benchmark notebook · **NOT THE PRODUCT**
`[CANON §2]` A notebook is one person's evaluation, at one moment, unversioned and unshared. This product exists because that artifact does not survive contact with a second engineer or a second month.

### NG-02 — Not an evaluation script collection · **NOT THE PRODUCT**
`[CANON §2]` Scripts compute metrics. They do not provide identity, versioning, baselines, governance, or reproducibility — which is the entire value proposition.

### NG-03 — Not a thin wrapper around third-party evaluation libraries · **NOT THE PRODUCT**
`[CANON §2]`, `[CANON §25]` — *"Thin UI around third-party evaluation libraries."*

RAGAS, DeepEval, Promptfoo, and OpenAI Evals compute metrics. This platform provides everything they do not: versioned assets, run identity, statistical comparison, release gates, multi-tenancy, and audit. `[CANON §8]` They are **adapters, not the architecture.**

**The concrete test:** removing every third-party evaluation library must leave a working product with fewer metrics. If removing them leaves nothing, this product was never built.

### NG-04 — Not a hackathon demo · **NOT THE PRODUCT**
`[CANON §2]` A demo optimizes for a five-minute happy path. This product must survive partial failures, provider outages, budget exhaustion, worker crashes, and hostile input — which is most of the engineering.

### NG-05 — Not an academic experiment · **NOT THE PRODUCT**
`[CANON §2]` The output is a system other people operate, not a result other people cite.

### NG-06 — Not an inference provider or model reseller · **OUT OF SCOPE (v1)**
`[GATE-0]` Customer-supplied credentials are the default model. The platform does not resell inference, does not mark up tokens, and does not front its own credentials as a production capability.

*Revisit if:* an approved requirement establishes a managed-credential tier. `[GATE-0]` explicitly withheld this.

### NG-07 — Not a model or prompt optimizer · **NOT THE PRODUCT**
The platform measures; it does not improve. It will not automatically rewrite prompts, tune parameters, or search for better configurations.

This is a deliberate boundary, not a missing feature. A system that both proposes changes and judges them has no independent measurement left. Separating the thing that changes from the thing that measures is the entire premise of regression testing.

### NG-08 — Not an AI application framework · **NOT THE PRODUCT**
The platform does not provide retrieval, agent orchestration, or serving for customer systems. It evaluates systems built elsewhere.

### NG-09 — Not an observability or tracing product · **OUT OF SCOPE (v1)**
The platform is deeply instrumented and correlates its own execution, but it is not a general-purpose tracing product for customer applications. `[CANON §14]` positions vendor tracing tools as optional integrations, not as the product.

### NG-10 — Not a data labelling platform · **OUT OF SCOPE (v1)**
Golden datasets are governed, versioned, reviewed, and approved here. Large-scale labelling workflows, annotator management, and inter-annotator workflow tooling are a different product.

*Revisit if:* human calibration data collection proves to need more than the minimal review queue approved for v1.

### NG-11 — Not a guardrail or runtime safety filter · **OUT OF SCOPE (v1)**
The platform evaluates safety-relevant properties offline and can gate releases on them. It does not sit in a customer's request path filtering live traffic. That is a latency-critical inline component with entirely different engineering constraints.

### NG-12 — Not a compliance certification · **NOT THE PRODUCT**
`[GATE-0]` The posture is governance-capable and informed by data-protection principles. **No compliance certification is claimed, implied, or suggested anywhere.** Persona U-6 in [`personas.md`](personas.md) identifies an overclaim here as disqualifying.

---

## Part 2 — Rejected practices

`[CANON §25]` lists ten anti-patterns. All ten appear below with the reason each is forbidden. These are **not** scope decisions — they are prohibitions that hold regardless of schedule pressure.

### NG-20 — No thin user interface over third-party libraries · **REJECTED PRACTICE**
`[CANON §25]` See NG-03.

### NG-21 — No single judge treated as ground truth · **REJECTED PRACTICE**
`[CANON §25]` — *"Single LLM-as-a-Judge treated as ground truth."*

A single language model scoring outputs is one opinion with unmeasured bias, unmeasured variance, and unmeasured drift. Presenting it as truth is the central methodological error this product exists to correct. Judgment must be plural, its disagreement must be visible, and its agreement with human labels must be measured and shown.

### NG-22 — No calling every deterministic service an agent · **REJECTED PRACTICE**
`[CANON §7]`, `[CANON §25]` — *"Calling every deterministic service an agent."*

Persistence, authentication, queueing, scheduling, metric computation, consensus arithmetic, statistical comparison, and threshold evaluation are conventional software. Reasoning components are used only where input is genuinely under-specified, and each must justify itself.

### NG-23 — No provider or evaluator logic scattered through the codebase · **REJECTED PRACTICE**
`[CANON §25]` — *"Hard-coded provider/evaluator logic across the codebase."*

Provider-specific and evaluator-specific behaviour is confined behind stable boundaries. Adding a provider or an evaluator must not require changes distributed across the system.

### NG-24 — Nothing that affects a result may be unversioned · **REJECTED PRACTICE**
`[CANON §25]` — *"Unversioned datasets/prompts/models/rubrics/evaluators."*

Datasets, prompts, model configurations, rubrics, evaluators, judges, and consensus configurations all carry identity and version. A result whose inputs cannot be named exactly is not evidence.

### NG-25 — No thresholds without baselines and uncertainty · **REJECTED PRACTICE**
`[CANON §25]` — *"Arbitrary quality-gate scores without baselines or uncertainty."*

A gate that fires on "score below 0.8" with no baseline and no uncertainty is a number chosen by someone in a meeting. Gate decisions must rest on a comparison against an approved baseline, with the uncertainty of that comparison stated.

### NG-26 — No infrastructure without a documented requirement · **REJECTED PRACTICE**
`[CANON §22]`, `[CANON §25]` — *"Fake enterprise complexity: Kafka/Kubernetes/microservices without requirements."*

Event-streaming infrastructure, container orchestration, and service decomposition are introduced only when a documented, measured requirement establishes the need. Impressiveness is not a requirement. Neither is portfolio value.

The inverse is equally prohibited: necessary complexity must not be removed merely to make implementation easier.

### NG-27 — No framework-driven domain architecture · **REJECTED PRACTICE**
`[CANON §25]` — *"Framework-driven architecture where LangGraph/RAGAS/etc. dictate domain boundaries."*

Libraries and frameworks serve the architecture; they do not define it. The core domain must remain free of framework types, so that replacing a framework is a contained change rather than a rewrite.

### NG-28 — No claims without reproducible evidence · **REJECTED PRACTICE**
`[CANON §24]`, `[CANON §25]` — *"Claims such as 'reduced hallucinations by 40%' without reproducible evidence."*

No metric, benchmark figure, coverage number, latency figure, or cost figure appears anywhere in this repository unless an executed run produced it and the raw output is retained. This applies to documentation, commit messages, reports, and any portfolio material.

### NG-29 — No autonomous modification of governed assets · **REJECTED PRACTICE**
`[CANON §25]` — *"Autonomous self-modification of golden datasets, policies, or release decisions without governance."*

The platform never modifies a golden dataset, a quality-gate policy, or a release decision on its own initiative. It proposes; people approve. This extends to the assisted planning capability (UC-16), which produces a proposal that executes nothing until a person approves it.

### NG-30 — No autonomous production changes · **REJECTED PRACTICE**
`[CANON §11]` — *"do not make unsafe autonomous production changes."*

Post-deployment evaluation produces **rollback recommendations**. The platform has no capability to modify a customer's production deployment. See UC-11.

### NG-31 — No placeholder presented as implementation · **REJECTED PRACTICE**
`[CANON §22]` Placeholder business logic, stub-only core paths, fake integrations, mocked production dependencies, and tests written only to raise a coverage number are prohibited in production paths. Deterministic substitutes inside tests are not only permitted but required.

### NG-32 — No silent degradation · **REJECTED PRACTICE**
`[DERIVED]` from `[CANON §9]`, `[CANON §11]`.

When the platform cannot do what was asked, it says so. It does not:

- report an aggregate computed over part of a dataset as though the run completed
- substitute a weaker metric under the name of a stronger one
- convert a failed or abstained evaluation into a zero
- pass a gate whose evidence is incomplete
- present an inconclusive comparison as "no change"
- report a platform outage as a quality regression

Each of these makes the product *feel* more capable and *be* less trustworthy. Every one is a specific failure mode named in [`use-cases.md`](use-cases.md).

---

## Part 3 — Deliberately out of scope for v1

Legitimate capabilities, deferred with the condition that would justify revisiting. Deferral is recorded so that later work is a decision rather than a discovery.

| ID | Out of scope | Reason | Revisit when |
|---|---|---|---|
| **NG-40** | Execution of arbitrary untrusted third-party evaluator code | `[GATE-0]` v1 provides defence-in-depth for trusted-tenant plugins. Safely executing hostile code is a substantially harder problem and **is not claimed** | An isolation approach has been evaluated and demonstrated, and a requirement justifies it |
| **NG-41** | Federated identity and directory synchronization | Not required to demonstrate the governance model; the identity boundary is preserved so it can be added | A deployment requires it |
| **NG-42** | Fine-grained per-record data residency | Meaningful residency guarantees require infrastructure commitments outside v1 scope | A regulated deployment requires it |
| **NG-43** | Billing, invoicing, metering for resale | The platform accounts for cost; it does not bill for it | Commercial operation requires it |
| **NG-44** | Public marketplace for shared datasets or evaluators | Sharing across tenants raises isolation, licensing, and provenance questions that v1 does not need to answer | Demand and an isolation model both exist |
| **NG-45** | Automatic dataset generation from production traffic | `[CANON §25]` prohibits autonomous dataset modification. Traffic-derived datasets also raise personal-data questions that must be answered before, not after | A governed, human-approved ingestion workflow is designed |
| **NG-46** | Real-time streaming evaluation of live traffic | The product evaluates against controlled datasets. Live-traffic evaluation is a different latency and sampling problem | A requirement establishes the need |
| **NG-47** | Multi-region active-active deployment | No availability requirement justifies it | An availability requirement justifies it |
| **NG-48** | Mobile applications | The interfaces are a web application, a command-line tool, and an API | Never anticipated |
| **NG-49** | Fine-tuning, training, or model hosting | Adjacent but distinct product categories | Never anticipated for this product |
| **NG-50** | Natural-language querying of evaluation history | `[GATE-0]` Historical evaluation memory is deterministic and queryable by design, because audit answers must be exact and repeatable | A deterministic query surface proves insufficient, and the addition does not compromise reproducibility |

---

## Part 4 — Non-goals for how this project is built

`[CANON §22]` These constrain the development process rather than the product.

| ID | Non-goal | Basis |
|---|---|---|
| **NG-60** | No generating the repository in one pass | `[CANON §22]` Work proceeds by approved phase, milestone, and slice |
| **NG-61** | No silent changes to frozen architecture | `[CANON §22]` A change proposal is raised first |
| **NG-62** | No dependency without a documented requirement | `[CANON §22]` |
| **NG-63** | No optimizing for speed at the expense of architectural correctness | `[CANON §22]` |
| **NG-64** | No reducing requirements to make implementation easier | `[CANON §22]` |
| **NG-65** | No claiming completion on unvalidated work | `[CANON §22]` Milestones are validated before completion is declared, and completion is not self-certified |
| **NG-66** | No secrets committed, at any point in history | `[CANON §16]`, `[CANON §22]` |
| **NG-67** | No weakening security boundaries to simplify local development | `[CANON §16]` |
| **NG-68** | No premature repository reorganization | `[CANON §22]` Deferred to final cleanup |

---

## Boundary cases

Items that could reasonably be read either way. Recorded so the reading is deliberate.

| Question | Answer | Reasoning |
|---|---|---|
| Is prompt *versioning* in scope, given NG-07 says the platform is not a prompt optimizer? | **In scope.** | `[CANON §17]` requires prompts and prompt versions. Versioning is identity, which the platform needs to attribute results. Optimization is change-generation, which it does not do. |
| Is *safety evaluation* in scope, given NG-11 says the platform is not a guardrail? | **In scope.** | `[CANON §11]` allows policies to combine safety metrics. Evaluating safety offline and gating on it differs from filtering live traffic inline. |
| Is *cost measurement* in scope, given NG-43 excludes billing? | **In scope.** | `[CANON §9]` requires cost accounting at fine granularity. Measuring and attributing spend is not the same as charging for it. |
| Is *drift detection* in scope, given NG-46 excludes live-traffic evaluation? | **In scope.** | Drift is detected by re-running controlled evaluations over time, not by observing production traffic. |
| Does the assisted planning capability violate NG-29? | **No.** | `[GATE-0]` The planning step produces a proposal that executes nothing and modifies nothing until a person approves it. The separation between proposing and executing is what makes it compliant. |
| Does storing generated outputs make this a tracing product, contradicting NG-09? | **No.** | Outputs are retained as evaluation evidence for a specific run. The platform does not ingest or index customer application traces generally. |

---

## Document history

| Version | Milestone | Change |
|---|---|---|
| 0.1 | M1.1 | Initial draft. Pending external review. |
