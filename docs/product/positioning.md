# Product Positioning
## Continuous LLM Evaluation & Regression Testing Platform

| Field | Value |
|---|---|
| Document | Product Positioning |
| Status | **Draft — pending external review** |
| Milestone | M1.2 — Competitive Analysis and Product Positioning |
| Phase | Phase 1 — Product Foundation |
| Governing specification | `Continuous LLM Evaluation Platform - Canonical Master Prompt v3.docx` (immutable; held locally, not distributed in this repository) |
| Depends on | [`competitive-analysis.md`](competitive-analysis.md) — every contrast below cites its verified findings |
| Related documents | [`prd.md`](prd.md) · [`personas.md`](personas.md) · [`success-criteria.md`](success-criteria.md) · [`non-goals.md`](non-goals.md) |

> **Every positioning claim in this document is unproven.** No part of this system has been built, so nothing here is a demonstrated capability. Each claim carries an explicit proof obligation in §7 stating what must exist and be measured before the claim may be made outside this repository. Canonical §24 forbids inventing metrics and canonical §25 rejects comparative claims without reproducible evidence; this document is written to be compatible with both while the product does not yet exist.

---

## Reading conventions

The markers are the same as in [`competitive-analysis.md`](competitive-analysis.md), and `check_m12.py` enforces them here identically.

| Marker | Meaning |
|---|---|
| `[VERIFIED S-nn]` | Vendor-documented fact, citing the [source register](../evidence/M1.2/sources.md). |
| `[OBSERVATION]` | Inference from verified facts. |
| `[POSITIONING]` | An intended claim about this product. Unproven by construction. |
| `[ASSUMPTION]` | An unvalidated belief this positioning depends on. |
| `[RECOMMENDATION]` | Proposed action for a later milestone. |
| `[EVIDENCE GAP]` | Material unknown, recorded rather than guessed. |
| `[CANON §n]` | Traced to the canonical specification, which is authoritative. |

---

## 1. Positioning statement

`[POSITIONING]` For engineering and quality teams who ship changes to prompts, models, retrieval, tools, and agents, this platform is the release-gate system of record for AI quality: it decides whether a change may ship, and it produces the evidence for that decision in a form that survives audit.

`[CANON §2]` `[CANON §3]` The canonical framing is "the 'GitHub Actions for AI Quality'" and "the enterprise control plane for AI quality."

`[OBSERVATION]` The operative noun is *decision*, not *measurement*. This is the only frame the competitive evidence leaves available. Measurement is thoroughly served — scoring, tracing, dashboards, and datasets are documented across the field `[VERIFIED S-01]` `[VERIFIED S-04]` `[VERIFIED S-06]` `[VERIFIED S-08]` `[VERIFIED S-09]` `[VERIFIED S-10]`. What the retrieved documentation consistently does not foreground is whether a release decision is *statistically defensible* and *reconstructable months later*.

`[OBSERVATION]` So the claim is deliberately narrow. It is not that this product measures better. It is that it treats the gate decision, and the evidence behind it, as the primary artifact.

## 2. Category and frame of reference

`[POSITIONING]` The frame of reference is the release gate, and the closest honest analogy is a required CI check: something a change must pass, that reports why it passed or failed, and whose verdict is recorded.

`[OBSERVATION]` Choosing this frame accepts an unfavourable comparison, which is why it is credible. Compared against observability platforms, this product will look narrow, and will have no trace corpus at launch `[OBSERVATION]` from `[VERIFIED S-06]` `[VERIFIED S-08]` `[VERIFIED S-09]` `[VERIFIED S-10]`. Compared against libraries, it will look heavy for a team that only wants a score `[VERIFIED S-01]` `[VERIFIED S-04]`.

`[POSITIONING]` Both comparisons are accepted rather than argued away. The product is for teams whose problem is a release decision they cannot currently defend, not for teams whose problem is a missing metric.

`[ASSUMPTION]` A material number of teams have that problem and recognise it as distinct from a measurement problem. This is unvalidated: no user research was conducted `[CANON §22]`, and it is the single assumption most capable of invalidating this positioning.

## 3. Who this is for

`[CANON §5]` The canonical user groups are AI/GenAI engineers, ML/LLMOps teams, AI platform teams, QA teams, product teams, and regulated enterprises. Personas for all six are defined in [`personas.md`](personas.md).

`[POSITIONING]` Positioning is sharpest for three of them, and deliberately weaker for the others:

| Group | Fit | Why |
|---|---|---|
| QA teams defining regression thresholds and release policies `[CANON §5]` | Primary | Their job *is* the gate decision. The product's core artifact is their core artifact. |
| Regulated enterprises requiring reproducibility, auditability, approvals `[CANON §5]` | Primary | Audit history and immutable run identity are canonical requirements `[CANON §9]` `[CANON §16]`, and are the hardest part for a competitor to retrofit. |
| AI platform teams exposing evaluation as an internal service `[CANON §5]` | Primary | They need multi-tenancy and RBAC as platform properties `[CANON §16]`, not as an add-on. |
| AI/GenAI engineers validating changes `[CANON §5]` | Secondary | Well served by libraries today `[VERIFIED S-01]` `[VERIFIED S-04]`, and pytest-native adoption is lower friction than a new pipeline stage `[VERIFIED S-04]`. |
| ML/LLMOps teams running scheduled benchmarks `[CANON §5]` | Secondary | Overlaps heavily with documented observability platform territory `[VERIFIED S-06]` `[VERIFIED S-09]`. |
| Product teams comparing quality, latency, cost `[CANON §5]` | Tertiary | Dashboard territory, already documented by incumbents `[VERIFIED S-06]`. |

`[OBSERVATION]` Naming secondary and tertiary segments is not hedging. A product positioned equally at all six would be positioned at none, and the two secondary groups are precisely where incumbent adoption is already easiest.

## 4. Differentiation pillars

`[OBSERVATION]` Each pillar below states the claim, its canonical basis, the verified competitive contrast, and what would have to be true for the claim to be honest. No pillar rests on a competitor lacking something — per the integrity rules in [`competitive-analysis.md`](competitive-analysis.md), that form of claim is unavailable.

### P-1 — Statistical defensibility of the gate decision

`[POSITIONING]` A gate verdict is accompanied by uncertainty, effect size where meaningful, and minimum-sample guidance, so that a small delta cannot silently block or clear a release.

`[CANON §9]` Canonically required: a statistical comparison layer with uncertainty and confidence reporting, effect size where meaningful, minimum-sample guidance, and protection against misleading tiny deltas.
`[CANON §25]` Canonically prohibited: arbitrary quality-gate scores without baselines or uncertainty.

`[OBSERVATION]` This is the strongest available pillar. Across ten products, no retrieved documentation foregrounded statistical treatment of the release decision. `[EVIDENCE GAP]` That is a statement about retrieved documentation, not proof that none does it — the claim must be phrased as what this product does, never as what others cannot.

**Proof obligation:** PO-1.

### P-2 — Judge uncertainty treated as a first-class output

`[POSITIONING]` Where probabilistic judges are used, disagreement, confidence, judge version, and bias and variance signals are exposed rather than averaged away, and low-agreement cases escalate to human review.

`[CANON §8]` Canonically required, including configurable consensus and escalation.
`[CANON §25]` Canonically prohibited: a single LLM-as-a-judge treated as ground truth.

`[VERIFIED S-09]` Langfuse documents LLM-as-judge alongside code evaluators, user feedback, and manual labeling. `[VERIFIED S-04]` DeepEval documents LLM-as-judge metrics including GEval and ConversationalGEval.
`[OBSERVATION]` Multiple judging modes are therefore documented in the field. The distinction claimed here is not *having* judges or even several of them, but treating inter-judge disagreement as a reportable signal that can block a release. That is a narrower and more defensible claim.

**Proof obligation:** PO-2.

### P-3 — Reconstructable decisions

`[POSITIONING]` Every gate decision can be reconstructed later from immutable run identity: dataset version, prompt version, model and provider, evaluator and judge versions, seeds where relevant, environment metadata, and timestamps.

`[CANON §9]` `[CANON §10]` `[CANON §16]` Canonically required, including immutable release snapshots and audit of every governed change.

`[VERIFIED S-08]` Phoenix documents versioned datasets and prompt management with version control and tagging. `[VERIFIED S-09]` Langfuse documents prompt management with version control.
`[OBSERVATION]` Versioning of individual assets is documented in the field. The claim here is the join: that a *decision* — not merely an asset — is reproducible from a captured configuration, which is what an auditor asks for.

**Proof obligation:** PO-3.

### P-4 — Governance as a platform property

`[POSITIONING]` Tenant isolation, RBAC, approvals, and audit history are properties of the platform at every tier, not features gated behind a commercial plan.

`[CANON §16]` Canonically required: explicit tenant boundaries, RBAC, scoped keys, rotation and revocation, and auditability.

`[VERIFIED S-07]` LangSmith documents organizations, workspaces, three built-in roles plus custom roles, SSO, and self-hosted deployment, and states that RBAC "is a feature that is only available to Enterprise customers." `[VERIFIED S-03]` Promptfoo Enterprise documents RBAC, teams-based configurability, and audit logging as enterprise-tier capabilities.
`[OBSERVATION]` Governance is therefore well served by incumbents, and any claim to differentiate on *having* it is false. The only available distinction is *where the tier boundary sits* — verified to be at the Enterprise plan for two products.
`[ASSUMPTION]` That teams needing access control below an Enterprise procurement threshold exist in useful numbers. Unvalidated, and P-4 is void without it.

**Proof obligation:** PO-4.

### P-5 — An open evaluation core with adapters, not a wrapper

`[POSITIONING]` Third-party evaluation libraries are adapters behind a stable Evaluator SDK; none of them defines the domain model.

`[CANON §8]` Canonically required: "RAGAS, DeepEval, Promptfoo, and OpenAI Evals are adapters—not the architecture," behind a stable Evaluator SDK with capability metadata, schemas, versions, and permissions.
`[CANON §2]` `[CANON §25]` Canonically prohibited: a thin wrapper or thin UI around third-party evaluation libraries.

`[VERIFIED S-01]` `[VERIFIED S-04]` `[VERIFIED S-05]` Ragas, DeepEval, and OpenAI Evals are documented as libraries, packages, or frameworks — consistent with the canonical adapter role.
`[OBSERVATION]` This pillar is primarily an architectural commitment that protects against becoming a wrapper. It is weak as external differentiation, because a buyer cannot see it, and it is listed for internal discipline rather than for messaging.

**Proof obligation:** PO-5.

### P-6 — Vendor-neutral observability core

`[POSITIONING]` The core depends on no proprietary tracing vendor; OpenTelemetry is the foundation and vendor integrations are optional adapters.

`[CANON §14]` `[CANON §19]` Canonically required, with LangSmith and Phoenix as optional integrations subject to an ADR.

`[VERIFIED S-08]` Phoenix is OpenTelemetry-based and licensed under Elastic License 2.0, with a patent notice. `[VERIFIED S-09]` Langfuse documents OpenTelemetry integration and is MIT licensed except its enterprise-edition directories.
`[OBSERVATION]` The licence finding gives this pillar practical force beyond architectural preference: Elastic License 2.0 is not an OSI-approved licence and restricts offering the software as a managed service, which directly constrains the canonical goal of exposing evaluation as an internal service `[CANON §5]`. A vendor-neutral core keeps that option open.

**Proof obligation:** PO-6.

## 5. What this product will not claim

`[OBSERVATION]` Derived directly from the competitive risks. Each is a claim that would be false, unverifiable, or unsurvivable in review, and is therefore prohibited rather than merely discouraged.

| # | Prohibited claim | Why |
|---|---|---|
| N-1 | That the category is unserved, or that nothing like this exists. | False. The platform tier is occupied `[VERIFIED S-03]` `[VERIFIED S-04]` `[VERIFIED S-06]` `[VERIFIED S-10]`. |
| N-2 | That any named competitor cannot do something. | Not established for any competitor, and rule 1 of the analysis forbids inferring absence from silent documentation. |
| N-3 | Any quality, latency, cost, or accuracy comparison against a competitor. | No hands-on testing was performed; canonical §25 rejects unevidenced comparative claims. |
| N-4 | Any numeric improvement figure. | Nothing is built or measured. Canonical §24 forbids inventing metrics. |
| N-5 | Enterprise-readiness, compliance certification, or production-proven status. | Nothing is deployed. |
| N-6 | That this is the only self-hostable or open option. | False. Permissively licensed self-hostable platforms are documented `[VERIFIED S-09]`. |
| N-7 | That incumbents neglect enterprise governance. | False. Documented in detail `[VERIFIED S-03]` `[VERIFIED S-07]`. |
| N-8 | Superiority on tracing or observability breadth. | Implausible against products whose documented centre is tracing, with no trace corpus at launch. |
| N-9 | That agent evaluation is uniquely offered here. | Convergent across the field `[VERIFIED S-04]` `[VERIFIED S-10]`. |
| N-10 | Any pricing, market-size, or adoption statement. | Evidence gaps G-1 and G-2. |

`[RECOMMENDATION]` Treat this table as binding on every README, portfolio artifact, demo script, and interview answer produced from this project, not only on marketing copy. Canonical §24 requires documenting evaluation methodology *and its limitations* for every major capability.

## 6. How the competitive risks constrain positioning

| Risk | Constraint imposed |
|---|---|
| R-1 category already occupied | Positioning must name the incumbent tier explicitly and claim a narrower job. Enforced by N-1. |
| R-2 incumbents hold the trace data | Never compete on observability breadth. P-1 and P-3 must carry the differentiation. Enforced by N-8. |
| R-3 governance already answered | P-4 reduces to tier-boundary placement, and depends on an unvalidated assumption. Enforced by N-7. |
| R-4 adapters have platform tiers | P-5 must be framed as architectural discipline, not competitive advantage. |
| R-5 permissive self-hostable options exist | Openness alone is not a differentiator. Enforced by N-6. |
| R-6 pytest-native adoption is lower friction | Do not position against engineer-tier convenience; concede the secondary segments in §3. |
| R-7 agent evaluation converging | Do not build positioning on it. Enforced by N-9. |
| R-8 integration licences may forbid the service model | Licence review is a gating step in the integration ADRs, not a later detail. |
| R-9 analysis decays | Re-verify before any external use; positioning inherits the source register's date. |

## 7. Proof obligations

`[OBSERVATION]` No pillar may be claimed outside this repository until its obligation is met. This table is the bridge between positioning and the measurable criteria in [`success-criteria.md`](success-criteria.md), and every status below is the same.

| # | Pillar | What must exist and be measured | Status |
|---|---|---|---|
| PO-1 | P-1 | A regression classification that reports uncertainty and effect size, with executed tests proving it declines to call an inconclusive delta a regression. | NOT YET MEASURED |
| PO-2 | P-2 | A judge ensemble exposing disagreement and confidence, with meta-tests proving low-agreement cases escalate rather than resolve silently `[CANON §20]`. | NOT YET MEASURED |
| PO-3 | P-3 | A replay path reconstructing a past gate decision from captured configuration, demonstrated end to end. | NOT YET MEASURED |
| PO-4 | P-4 | Tenant isolation and RBAC enforced and tested at the persistence boundary, with cross-tenant access proven to fail `[CANON §21]`. | NOT YET MEASURED |
| PO-5 | P-5 | At least two independent evaluator adapters behind one unmodified SDK interface, with architecture-boundary checks enforcing the direction of dependency. | NOT YET MEASURED |
| PO-6 | P-6 | The core operating with no proprietary tracing dependency, proven by a build that excludes every vendor adapter. | NOT YET MEASURED |

`[CANON §20]` "Never claim a metric unless an executed test or benchmark produced it." Every status above is therefore identical, and will change only when an executed test produces a result.

## 8. Messaging discipline

`[RECOMMENDATION]` Rules that make the above enforceable in practice:

1. State the job before the category. The job is defending a release decision; the category label is secondary.
2. Describe what the system does, never what a competitor fails to do.
3. Attach the limitation to the claim in the same breath, per canonical §24.
4. Cite the source register date whenever competitive material is presented.
5. Prefer "designed to" over "does" until the corresponding proof obligation is met, and drop the hedge the moment it is.
6. Concede the unfavourable comparisons in §2 voluntarily. A positioning that only survives when competitors are misrepresented is not a positioning.

## 9. Assumptions this positioning depends on

`[ASSUMPTION]` Listed for challenge, since none is validated and each is capable of invalidating part of the document:

| # | Assumption | What it supports | If false |
|---|---|---|---|
| A-1 | Teams experience an undefendable release decision as a distinct problem from a missing metric. | §1, §2, P-1 | The core positioning fails and the product is a measurement tool competing on a crowded axis. |
| A-2 | Buyers needing access control below Enterprise procurement exist in useful numbers. | P-4 | P-4 is void; §3's platform-team segment weakens. |
| A-3 | Statistical defensibility is valued by a buyer, not only by an engineer. | P-1 | P-1 becomes an engineering virtue with no commercial pull. |
| A-4 | Auditability is a purchase driver in regulated settings, not a checkbox satisfied by logs. | §3 primary segments, P-3 | The regulated-enterprise segment weakens materially. |
| A-5 | Adapter breadth is expected rather than differentiating. | P-5 | Adapter coverage would need to become a competitive priority. |

`[RECOMMENDATION]` A-1 is the assumption to test first and the cheapest to test, because it can be probed in conversation long before anything is built.

## 10. Evidence gaps

`[EVIDENCE GAP]` This document inherits every gap in [`competitive-analysis.md`](competitive-analysis.md) §7, and adds:

| # | Gap | Consequence |
|---|---|---|
| PG-1 | No buyer, user, or willingness-to-pay research. | Every `[ASSUMPTION]` in §9 is unvalidated. |
| PG-2 | No win/loss or competitive-displacement evidence. | Segment fit in §3 is reasoned from canonical groups, not observed. |
| PG-3 | No pricing or packaging position. | Deliberate: G-1 makes any statement unfounded. Owned by a later milestone. |
| PG-4 | No brand, naming, or visual identity work. | Out of scope for M1.2. |
| PG-5 | Every pillar unproven. | §7 is the entire basis on which claims become permissible. |
