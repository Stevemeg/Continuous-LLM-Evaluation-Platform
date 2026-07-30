# Competitive Analysis
## Continuous LLM Evaluation & Regression Testing Platform

| Field | Value |
|---|---|
| Document | Competitive Analysis |
| Status | **Draft — pending external review** |
| Milestone | M1.2 — Competitive Analysis and Product Positioning |
| Phase | Phase 1 — Product Foundation |
| Governing specification | `Continuous LLM Evaluation Platform - Canonical Master Prompt v3.docx` (immutable; held locally, not distributed in this repository) |
| Required by | Canonical §18 (competitor analysis) and §23 (Phase 1 competitor research) |
| Related documents | [`positioning.md`](positioning.md) · [`prd.md`](prd.md) · [`non-goals.md`](non-goals.md) · [source register](../evidence/M1.2/sources.md) |

> **No measured comparison appears in this document.** It contains no benchmark result, accuracy figure, latency figure, cost figure, pricing, market-share estimate, revenue figure, customer count, or adoption statistic for any product, including this one. None of those were verifiable from the development environment, and canonical §25 rejects comparative claims without reproducible evidence. Where such a figure would ordinarily appear, the gap is recorded instead.

---

## Reading conventions

Every substantive line in this document carries one of the following markers. `check_m12.py` enforces their use.

| Marker | Meaning |
|---|---|
| `[VERIFIED S-nn]` | A statement of fact drawn from the vendor's own documentation or source repository, citing the [source register](../evidence/M1.2/sources.md). It reports **what the vendor documents about itself**, not the outcome of hands-on testing. |
| `[OBSERVATION]` | An inference drawn from verified facts. The reasoning is stated so the inference can be challenged independently of the facts it rests on. |
| `[POSITIONING]` | A claim about where this product intends to sit. Unproven by construction: this product does not yet exist. |
| `[ASSUMPTION]` | A belief about the market or about buyers that this analysis depends on and has not verified. Falsifiable, and listed for challenge. |
| `[RECOMMENDATION]` | A proposed course of action for a later milestone. |
| `[EVIDENCE GAP]` | Something material that could not be established from the available environment. Recorded rather than guessed. |
| `[CANON §n]` | Traced to section *n* of the canonical specification, which is authoritative. |

## Integrity rules this analysis is bound by

These are not stylistic preferences. They are the rules that separate a competitive analysis from a set of convenient assertions about absent competitors, and `check_m12.py` enforces the first two mechanically.

1. **Absence of documentation is never recorded as absence of capability.** No competitor is described as lacking a feature. The analysis states what was documented; anything else is `[EVIDENCE GAP]`. The reason is empirical rather than theoretical — see below.
2. **Every verified claim cites a source.** Each one names a source in the register, and every registered source is actually cited.
3. **No competitor was installed, purchased, or run.** Nothing here rests on hands-on trial, so nothing here describes how well anything works — only what it is documented to do.
4. **No pricing, market share, funding, adoption, or customer figure appears.** These change quickly, were not verifiable from primary sources in this environment, and are the fastest way to make a document like this false.
5. **Feature parity is not the comparison.** The canonical product is defined by an enforcement and governance model `[CANON §2]` `[CANON §3]`, not by a metric catalogue. Counting metrics would measure the wrong axis.

### Why rule 1 exists

`[VERIFIED S-06]` `[VERIFIED S-07]` LangSmith's product overview page, retrieved for this analysis, does not mention role-based access control, single sign-on, self-hosted deployment, or usage controls. Its administration overview documents all four, including three built-in workspace roles and organization-level custom roles.

`[OBSERVATION]` The same product therefore looked like it was missing four enterprise capabilities on one page and documented all four on another. Had this analysis drawn a competitive conclusion from the first page alone, it would have asserted four false weaknesses and then built positioning on them. The failure mode is not hypothetical; it occurred during the research for this document and was caught only because a second page was retrieved.

`[RECOMMENDATION]` Treat any competitive claim of the form "they cannot do X" as unsupported until a vendor-documented statement, not a silent page, establishes it.

---

## 1. Scope and competitor selection

### 1.1 Why these products

`[CANON §8]` `[CANON §15]` The canonical specification names Ragas, DeepEval, Promptfoo, and OpenAI Evals as evaluation adapters — "adapters, not the architecture" — and names LangSmith and Arize Phoenix as optional observability integrations that the core platform must not depend on.

`[OBSERVATION]` That gives two distinct relationships, and conflating them would distort the analysis:

- **Adapter candidates.** Ragas, DeepEval, Promptfoo, OpenAI Evals. The canonical intent is to *consume* these. They compete only to the extent that a team could stop at the library and never buy a platform.
- **Integration candidates that are also platforms.** LangSmith and Arize Phoenix. The canonical intent is optional integration, but both are documented as platforms with overlapping scope `[VERIFIED S-06]` `[VERIFIED S-08]`, so `[OBSERVATION]` both are genuine competitors as well as integration targets.

`[OBSERVATION]` The canonical set is weighted toward libraries, while the canonical product is a platform `[CANON §2]`. Analysing only the canonical set would therefore compare this product against the wrong tier and produce a flattering, useless result. Two further platforms were added on that basis:

- **Langfuse** — documented as an open-source LLM engineering platform `[VERIFIED S-09]`.
- **Braintrust** — documented as a commercial observability platform for agents `[VERIFIED S-10]`.

`[VERIFIED S-04]` Confident AI is also treated as a platform-tier competitor, because DeepEval documents it as its companion cloud platform, described as an AI quality platform with observability, evals, and monitoring. It is analysed alongside DeepEval rather than separately, since it was reached through DeepEval's documentation.

### 1.2 Explicitly outside this analysis

| Excluded | Reason |
|---|---|
| Model providers and their native eval tooling | Out of category. They evaluate their own models; this product evaluates *systems* built on any provider `[CANON §6]`. |
| General experiment trackers and ML monitoring tools | Adjacent category. Included only where the vendor documents LLM evaluation as a product surface. |
| Academic benchmark suites and leaderboards | `[CANON §2]` explicitly excludes benchmark notebooks and academic experiments from the product definition. |
| Internal tools built by individual companies | Not purchasable, not documented, not verifiable. |
| Guardrail and runtime-safety products | Different job. They constrain live traffic; this product gates releases `[CANON §11]`. Promptfoo is the exception, because its own documentation places red teaming inside the same tool as evaluation `[VERIFIED S-02]`. |

`[EVIDENCE GAP]` This selection was made from the canonical specification plus the products reachable and documented in this environment. It is not the output of a systematic market scan, and no claim is made that these ten are the ten most significant competitors by any commercial measure.

---

## 2. The category map

`[OBSERVATION]` The products divide into three tiers by what the buyer has to assemble. The tier, not the feature list, is what determines whether a product competes with this one.

### Tier 1 — Evaluation libraries

`[VERIFIED S-01]` Ragas describes itself as "a library that helps you move from 'vibe checks' to systematic evaluation loops for your AI applications."
`[VERIFIED S-04]` DeepEval describes itself as "an open-source LLM eval package" that runs evaluations locally.
`[VERIFIED S-02]` Promptfoo describes itself as "an open-source CLI and library for evaluating and red-teaming LLM apps," and states that "This software runs completely locally. The evals run on your machine."
`[VERIFIED S-05]` OpenAI Evals describes itself as "a framework for evaluating LLMs and LLM systems, and an open-source registry of benchmarks."

`[OBSERVATION]` These supply scoring capability. The team supplies everything around it: where results live, what a baseline is, who approved it, what blocks a release, and what the audit trail looks like.

### Tier 2 — Observability-first platforms that added evaluation

`[VERIFIED S-06]` LangSmith is framed around "full visibility into your LLM application: from individual traces to production-wide performance metrics," with dashboards, alerts, rules, webhooks, online evaluations, and annotation queues.
`[VERIFIED S-08]` Arize Phoenix describes itself as "an open-source AI observability platform designed for experimentation, evaluation, and troubleshooting," built on OpenTelemetry-based instrumentation.
`[VERIFIED S-09]` Langfuse describes itself as an "open source LLM engineering platform" for teams to "collaboratively develop, monitor, evaluate, and debug AI applications."
`[VERIFIED S-10]` Braintrust describes itself as "the active observability platform for instrumenting, understanding, and improving agents."

`[OBSERVATION]` These start from production traffic and work backward toward evaluation. Their centre of gravity is the trace.

### Tier 3 — Platforms positioned on evaluation and quality

`[VERIFIED S-04]` Confident AI is documented as an AI quality platform with observability, evals, and monitoring, providing centralized testing reports, regression analysis, and production monitoring.
`[VERIFIED S-03]` Promptfoo Enterprise adds RBAC, teams-based configurability, audit logging, sharing and export, and an on-premises deployment with network isolation and a dedicated runner.

`[OBSERVATION]` This tier is where the canonical product competes directly, and it is not empty. Any positioning that treats the category as unserved is wrong.

---

## 3. Competitor profiles

### 3.1 Ragas

`[VERIFIED S-01]` Documented as a library. Provides LLM-driven metrics, custom metrics defined through decorators, built-in dataset management with result tracking, an experiments-first approach for evaluating changes consistently, and integrations with frameworks including LangChain and LlamaIndex. Test data generation is documented. The documentation emphasises self-service setup rather than a hosted platform, and mentions optional consulting engagements.

`[EVIDENCE GAP]` CI/CD pipeline integration and any hosted service were not established from `S-01`.

`[OBSERVATION]` Relative to the canonical product, the overlap is metric computation and dataset handling. The canonical requirements Ragas is not documented against are the governance ones: immutable release snapshots, approval state, RBAC, and audit history `[CANON §10]` `[CANON §16]`.

`[CANON §8]` Canonically an adapter, not a competitor to displace.

### 3.2 DeepEval, and Confident AI

`[VERIFIED S-04]` DeepEval is documented as an open-source eval package running locally. It provides LLM-as-judge metrics including GEval and ConversationalGEval, custom metric criteria, component-level and end-to-end evaluation, pytest integration through a `deepeval test run` command, single-turn and multi-turn test cases, tracing for evaluating internal components, dataset creation and management, golden test case iteration, synthetic data generation, and integrations across several agent and application frameworks.

`[VERIFIED S-04]` Confident AI is its companion cloud platform, documented as an AI quality platform with observability, evals, and monitoring, offering centralized testing reports, regression analysis, and production monitoring. DeepEval runs without it.

`[OBSERVATION]` This is the closest architectural analogue to the canonical product's own intent: an open evaluation core with a platform above it for history, regression analysis, and reporting. Pytest integration is a materially different adoption path from a bespoke CLI — it enters an existing test suite rather than asking for a new pipeline stage.

`[EVIDENCE GAP]` Dataset versioning with approvals, RBAC, multi-tenancy, and audit logging were not established for either DeepEval or Confident AI from `S-04`. No conclusion is drawn about whether they exist.

`[OBSERVATION]` This is the competitor most likely to be misjudged. Because its library tier is the part most engineers encounter, it is easy to file DeepEval under "library" and overlook that a platform tier exists directly above it.

### 3.3 Promptfoo, and Promptfoo Enterprise

`[VERIFIED S-02]` The open-source tier is documented as a CLI and library that runs completely locally, with evaluation and red teaming in one tool, usable in CI/CD including a GitHub Action, supporting several major hosted providers alongside open-weight and custom API providers. Red teaming is extensively documented, covering target discovery, plugins, strategies, and compliance frameworks including the NIST AI Risk Management Framework.

`[VERIFIED S-03]` Promptfoo Enterprise, offered as SaaS and on-premises, is documented with RBAC controls for multiple users and teams, teams-based configurability of targets, plugins, and scan configurations, sharing and export functions, result sharing with privacy controls, detailed reporting and analytics, audit logging, and an on-premises option with network isolation and a dedicated runner inside the customer's network perimeter.

`[OBSERVATION]` Two things here matter more than the feature list. First, Promptfoo already occupies the CI-integrated evaluation position the canonical product targets, and does so with a documented GitHub Action `[CANON §11]`. Second, its enterprise tier already answers the governance questions — RBAC, audit logging, on-prem isolation — that an enterprise buyer would otherwise raise as objections.

`[OBSERVATION]` Its documented centre of gravity is security and red teaming rather than regression gating against approved baselines. That is a difference in emphasis established by what the documentation foregrounds, not a capability the product is claimed to lack.

`[EVIDENCE GAP]` Versioned golden datasets with approval workflows, statistical regression classification, and judge-ensemble consensus were not established from `S-02` or `S-03`.

### 3.4 OpenAI Evals

`[VERIFIED S-05]` Documented as a framework plus an open-source registry of benchmarks, MIT licensed, supporting eval templates that need little or no code, custom and private evals that do not expose data publicly, and an integration for running evaluations with Weights & Biases. The repository states that it is "currently not accepting evals with custom code," accepting model-graded YAML configurations instead. It emphasises that quality evaluations require careful thought and rigorous experimentation rather than automation alone.

`[VERIFIED S-05]` No hosted service, CI gate, dataset versioning, or governance feature is described in the repository.

`[OBSERVATION]` This is the weakest platform-tier competitor and the most useful adapter target: a benchmark registry and a scoring harness, explicitly not a service. The contribution restriction is a signal about the registry's direction of travel, not about the framework's quality.

### 3.5 LangSmith

`[VERIFIED S-06]` Documented around tracing and production-wide performance metrics, with trace filtering, export, sharing and comparison via UI or API; dashboards and alerts to track quality; automation through rules, webhooks, and online evaluations; annotation queues and inline annotation for feedback; and automated detection of recurring issues with root-cause diagnosis.

`[VERIFIED S-07]` Administration is documented with organizations as a logical grouping defining shared settings across workspaces; workspaces grouping users and resources; three built-in system roles — Workspace Admin, Editor, and Viewer — plus organization-created custom roles; single sign-on and OAuth provider configuration; self-hosted deployment including Helm charts; and usage limits, data retention settings, rate limits, and per-project and per-user trace limits.

`[VERIFIED S-07]` Tier gating is explicit: RBAC "is a feature that is only available to Enterprise customers," Organization User and Viewer roles require Plus or Enterprise plans, and Developer plan organizations default all users to Organization Admin.

`[OBSERVATION]` This is the most complete governance story of any product examined, and it directly answers the multi-tenancy and RBAC requirements in canonical §16. Any positioning implying that incumbents ignore enterprise access control is contradicted by `S-07`.

`[OBSERVATION]` Its documented emphasis remains observability and online evaluation rather than release gating against approved baselines with statistical regression classification. That is a difference in product centre, established from what the documentation covers.

`[OBSERVATION]` `[ASSUMPTION]` Tier gating of RBAC to Enterprise creates a segment — teams needing access control without Enterprise procurement — that a self-hostable alternative could serve. The existence of the gate is verified; that it constitutes a reachable commercial opening is an assumption this analysis has not tested.

### 3.6 Arize Phoenix

`[VERIFIED S-08]` Documented as an open-source AI observability platform for experimentation, evaluation, and troubleshooting. Capabilities documented in the repository: OpenTelemetry-based tracing of application runtime; LLM-based evaluation with response and retrieval evaluators; datasets as versioned example collections for experimentation and fine-tuning; experiments tracking changes to prompts, models, and retrieval systems; prompt management using version control, tagging, and experimentation; a playground for comparing models and replaying calls; an AI engineering agent for debugging; and a remote MCP server for editor and agent clients. Self-hosting is documented through container images, Helm charts, and Compose.

`[VERIFIED S-08]` It is licensed under the Elastic License 2.0, and the repository notes that portions of the code are protected by one or more US patents.

`[OBSERVATION]` The licence matters more than any feature here. Elastic License 2.0 is not an OSI-approved open-source licence and restricts providing the software as a managed service to third parties. A product that intends to *offer* evaluation as an internal or commercial service `[CANON §5]` must treat Phoenix as an integration target under licence review, never as a component to absorb. The repository's self-description as open source and its actual licence terms are not the same question.

`[OBSERVATION]` Versioned datasets and prompt version control are documented, which overlaps the canonical Golden Dataset Manager more than any other examined product `[CANON §10]`.

`[EVIDENCE GAP]` Authentication, RBAC, and multi-tenancy were not established from `S-08`, and the Phoenix documentation site returned HTTP 403 during retrieval. Phoenix's access-control model is therefore unknown to this analysis, not absent.

`[CANON §14]` `[CANON §19]` Canonically an optional integration behind an ADR, with the core forbidden to depend on a proprietary tracing vendor. The licence finding reinforces that constraint rather than changing it.

### 3.7 Langfuse

`[VERIFIED S-09]` Documented as an open-source LLM engineering platform for collaboratively developing, monitoring, evaluating, and debugging AI applications. Capabilities documented: trace ingestion covering LLM calls and surrounding logic; centralized prompt management with version control, collaborative iteration, and caching; evaluation via LLM-as-judge, code evaluators, user feedback collection, manual labeling, and custom evaluation pipelines; datasets and experiments providing test sets and benchmarks; and a playground for prompt and model-configuration iteration. Self-hosting is documented through containers, Kubernetes with Helm, and cloud provider templates, with multi-project support and per-project API credentials. Integrations include OpenTelemetry.

`[VERIFIED S-09]` The repository is MIT licensed "except for the `ee` folders," indicating enterprise-edition components under different terms.

`[OBSERVATION]` This is the strongest combination of permissive licensing, self-hostability, and platform breadth among the products examined, which makes it the most credible alternative for a team that rejects both a hosted vendor and a build-it-yourself project.

`[OBSERVATION]` The split licence is the same structural pattern as Promptfoo and LangSmith: the collaboration and governance surface is where the commercial boundary is drawn. Three of the examined platforms independently placed the boundary in the same place.

`[EVIDENCE GAP]` RBAC, SSO, and audit logging were not established from `S-09`, and the enterprise-edition directories were not examined.

### 3.8 Braintrust

`[VERIFIED S-10]` Documented as an active observability platform for instrumenting, understanding, and improving agents, surfacing patterns in agent traces. Capabilities documented: trace collection through provider and framework integrations; evaluation and experiments with playgrounds and evals to measure and improve quality; datasets and annotation including human feedback collection; a playground; log analysis and pattern identification; and a `bt` command-line interface for evals, instrumentation, log querying, and code agent configuration. It directs users to create accounts on its hosted service, indicating a commercial hosted model. Administration covering organizations, projects, and access control is referenced.

`[EVIDENCE GAP]` Self-hosting, RBAC specifics, enterprise governance detail, and CI/CD gating mechanisms were not established from `S-10`.

`[OBSERVATION]` Its documented framing has moved toward agents specifically, which is convergent with the canonical agent-evaluation capability `[CANON §6]` and worth re-checking at each later milestone.

---

## 4. Verified capability comparison

`[OBSERVATION]` This table records **documentation status**, not capability, and not quality. `documented` means a cited source documents it. `not established` means this analysis did not verify it and draws no conclusion. No cell asserts that a product lacks anything.

| Dimension | Ragas | DeepEval | Promptfoo | OpenAI Evals | LangSmith | Phoenix | Langfuse | Braintrust |
|---|---|---|---|---|---|---|---|---|
| Local / library execution | documented `S-01` | documented `S-04` | documented `S-02` | documented `S-05` | not established | documented `S-08` | not established | CLI documented `S-10` |
| Hosted platform tier | consulting only `S-01` | Confident AI `S-04` | Enterprise SaaS `S-03` | none described `S-05` | documented `S-06` | not established | not established | documented `S-10` |
| Self-hosting | not established | not established | on-prem `S-03` | not established | documented `S-07` | documented `S-08` | documented `S-09` | not established |
| CI/CD integration | not established | pytest `S-04` | CI + Action `S-02` | none described `S-05` | not established | not established | not established | not established |
| Dataset versioning | management only `S-01` | not established | not established | none described `S-05` | not established | documented `S-08` | datasets `S-09` | not established |
| Prompt versioning | not established | not established | not established | not established | not established | documented `S-08` | documented `S-09` | not established |
| Tracing / observability core | not established | tracing `S-04` | not established | not established | documented `S-06` | documented `S-08` | documented `S-09` | documented `S-10` |
| RBAC | not established | not established | Enterprise `S-03` | not established | Enterprise `S-07` | not established | not established | referenced `S-10` |
| Red teaming | not established | not established | documented `S-02` | not established | not established | not established | not established | not established |
| Licence | not established | open source `S-04` | open source + Enterprise `S-02` `S-03` | MIT `S-05` | commercial tiers `S-07` | ELv2 `S-08` | MIT except `ee` `S-09` | commercial `S-10` |

`[OBSERVATION]` The table's most informative column is how many cells read `not established`. This is a documentation survey, not a product evaluation, and the density of gaps is the honest measure of its authority. Any later milestone that needs a specific cell resolved should resolve it deliberately rather than treating a gap as a finding.

---

## 5. Overlap with the canonical product

### 5.1 Where the canonical requirements are already served

`[OBSERVATION]` Stated plainly, because positioning built on pretending otherwise would collapse under the first informed question:

| Canonical requirement | Already documented by |
|---|---|
| `[CANON §6]` Prompt regression testing with version history | Prompt versioning in Phoenix `S-08` and Langfuse `S-09` |
| `[CANON §6]` Golden dataset versioning | Versioned datasets in Phoenix `S-08`; datasets in Langfuse `S-09`; dataset management in Ragas `S-01` and DeepEval `S-04` |
| `[CANON §6]` CI/CD quality gates | CI and GitHub Action in Promptfoo `S-02`; pytest integration in DeepEval `S-04` |
| `[CANON §6]` Quality dashboards and alerts | Dashboards and alerts in LangSmith `S-06` |
| `[CANON §6]` Enterprise governance, RBAC, audit logs | RBAC and audit logging in Promptfoo Enterprise `S-03`; roles, SSO, retention in LangSmith `S-07` |
| `[CANON §8]` Multi-judge evaluation | LLM-as-judge with code evaluators and manual labeling in Langfuse `S-09`; judge metrics in DeepEval `S-04` |
| `[CANON §14]` OpenTelemetry-based tracing | Phoenix `S-08`; Langfuse `S-09` |

`[OBSERVATION]` No individual canonical capability is unclaimed by every competitor. A differentiation argument resting on any single capability is therefore unavailable.

### 5.2 Where the canonical composition was not found

`[OBSERVATION]` What was not found in any single examined product is the *combination* the canonical specification requires, in particular these together:

- `[CANON §9]` A statistical comparison layer reporting uncertainty and effect size, with minimum-sample guidance and explicit protection against misleading tiny deltas, feeding a regression classification.
- `[CANON §8]` A heterogeneous judge ensemble with configurable consensus that exposes disagreement, confidence, judge version, and bias/variance signals, and escalates low-agreement cases to human review.
- `[CANON §11]` Gate decisions in hard-fail, warning, manual-approval, and policy-exception modes, each accompanied by the exact evidence behind the decision.
- `[CANON §10]` Golden datasets with immutable release snapshots, approval state, lineage, and contamination checks.
- `[CANON §9]` Immutable run identity capturing dataset, prompt, model, evaluator and judge versions, seeds, and environment, sufficient for replay.

`[EVIDENCE GAP]` This is a statement about what the retrieved documentation covered. It is not proof that no examined product implements this combination, and it would be false to present it as such. Rule 1 applies to composition claims exactly as it applies to feature claims.

`[OBSERVATION]` The defensible form of the claim is narrower and still useful: across ten products, the retrieved documentation consistently foregrounds tracing, scoring, and dashboards, and consistently does not foreground statistical defensibility of a release decision. The gap this product targets is *decision defensibility*, not measurement.

---

## 6. Competitive risks to this product

| # | Risk | Basis | Severity |
|---|---|---|---|
| R-1 | The category is already occupied at the platform tier, so "no one does this" is false and any messaging implying it will fail on contact with an informed reviewer. | `S-03` `S-04` `S-06` `S-07` `S-10` | High |
| R-2 | Incumbent observability platforms already hold the trace data, so release gating is an increment on an existing corpus for them, while a new platform starts with no corpus at all. The asymmetry runs against this product. | `[OBSERVATION]` from `S-06` `S-08` `S-09` `S-10` | High |
| R-3 | Two competitors already answer enterprise governance objections with documented RBAC and audit logging, removing a differentiator that a new entrant might expect to own. | `S-03` `S-07` | High |
| R-4 | Adapter targets are themselves platforms or have platform tiers, so the adapter strategy imports competitors into the product surface. | `S-03` `S-04` | Medium |
| R-5 | Permissively licensed self-hostable platforms already exist, weakening "self-hostable and open" as a differentiator. | `S-09` | Medium |
| R-6 | Pytest-native adoption is a lower-friction path into existing test suites than a new pipeline stage. | `S-04` | Medium |
| R-7 | Agent evaluation is converging across the field, so it will likely be table stakes rather than a differentiator by the time it ships. | `[OBSERVATION]` from `S-10` `S-04` | Medium |
| R-8 | An integration target's licence may forbid the intended service model, discoverable only on licence review rather than from feature documentation. | `S-08` | Medium |
| R-9 | This analysis rests on documentation retrieved on one date with no hands-on verification, so it will decay and may already misstate emphasis. | §7 | Medium |

`[RECOMMENDATION]` R-1, R-2, and R-3 should be treated as constraints on the positioning document rather than as marketing problems to solve later. Positioning that survives them is the only positioning worth writing down.

---

## 7. Evidence gaps

`[EVIDENCE GAP]` Recorded so that no later milestone mistakes silence for a finding:

| # | Gap | Why it was not closed |
|---|---|---|
| G-1 | No pricing, packaging, or tier cost for any product. | Not verifiable from primary sources retrieved; volatile. Deliberately excluded under rule 4. |
| G-2 | No market share, revenue, adoption, customer count, or funding figure. | Same. Canonical §25 rejects unevidenced claims of this kind. |
| G-3 | No hands-on verification of any competitor. | Nothing was installed, purchased, or run. All claims are documentation-level. |
| G-4 | Phoenix documentation site unreachable (HTTP 403). | Phoenix claims rest on its repository only; its access-control model is unknown. |
| G-5 | Access control unestablished for Ragas, DeepEval, Confident AI, Langfuse, Braintrust specifics. | Not documented on the pages retrieved. Not investigated further; absence not inferred. |
| G-6 | Enterprise-edition directories of Langfuse not examined. | Out of scope for a documentation survey. |
| G-7 | No statistical-rigour comparison across competitors. | Would require hands-on testing to state honestly. |
| G-8 | Competitor roadmaps unknown. | Not reliably knowable from documentation; R-2 and R-7 stand in for the risk. |
| G-9 | No buyer or user research. | None conducted for this project. Every `[ASSUMPTION]` about buyers is unvalidated. |
| G-10 | Coverage is not a systematic market scan. | Selection was canonical plus reachable, as recorded in §1.2. |

---

## 8. What would change this analysis

`[RECOMMENDATION]` Falsification triggers, so this document is revisable on evidence rather than on opinion:

1. Any competitor documenting statistical regression classification with uncertainty reporting weakens §5.2 materially and must be recorded.
2. Any competitor documenting immutable golden-dataset snapshots with approval workflow removes a §5.2 element.
3. Any competitor documenting a judge ensemble with configurable consensus and exposed disagreement removes another.
4. A licence change to Phoenix or to Langfuse's enterprise boundary changes the integration analysis in §3.6 and §3.7.
5. Hands-on verification of any product may contradict its documentation, and takes precedence over it when it does.
6. Closing G-1 or G-2 through primary sources would permit commercial analysis that is currently withheld.

`[RECOMMENDATION]` Re-verify before any external presentation of this material, and re-record the retrieval date. `[OBSERVATION]` A competitive analysis is accurate on the date it was retrieved and decays from then on; this one carries its own date in the source register so the decay is visible rather than hidden.
