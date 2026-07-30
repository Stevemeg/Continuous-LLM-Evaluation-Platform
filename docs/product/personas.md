# Personas
## Continuous LLM Evaluation & Regression Testing Platform

| Field | Value |
|---|---|
| Status | **Draft — pending external review** |
| Milestone | M1.1 — Product Definition, Personas, and Use Cases |
| Phase | Phase 1 — Product Foundation |
| Source | `[CANON §5]` — six named user groups |
| Related | [`prd.md`](prd.md) · [`use-cases.md`](use-cases.md) · [`success-criteria.md`](success-criteria.md) · [`non-goals.md`](non-goals.md) |

---

## How to read this document

`[CANON §5]` names six user groups. Each is developed into one persona below, retaining its canonical group identifier (`U-1` … `U-6`) so that coverage is verifiable.

Personas are **archetypes, not research findings.** No user interviews have been conducted for this project. Each persona is constructed from the canonical specification's description of that user group plus the product consequences that follow from it. Where a persona attributes a behaviour or frustration to its group, that is a **design premise** — a falsifiable claim about who this product serves — not an observed fact. Premises are marked `[PREMISE]` where they carry real weight.

This distinction matters: a persona document that reads as though it summarizes user research, when no research was performed, is exactly the kind of unsupported claim `[CANON §24]` forbids.

Each persona records:

- **Canonical basis** — the `[CANON §5]` text this persona derives from
- **Context** — their working situation
- **Goals** — what success means to them
- **Frustrations** — what makes their work hard today `[PREMISE]`
- **Current workaround** — what they do in the absence of this product `[PREMISE]`
- **What they need from this platform** — the product obligations they create
- **Anti-goals** — what would make them abandon the product
- **Data they touch** — sensitivity classes from [`prd.md`](prd.md) §7
- **Primary capabilities** — capability IDs from [`prd.md`](prd.md) §5
- **Use cases** — every use case in which this persona appears, whether as primary or secondary, cross-referenced to [`use-cases.md`](use-cases.md)

---

## Persona index

| ID | Persona | Canonical group | Relationship to product |
|---|---|---|---|
| **U-1** | Maya — GenAI Application Engineer | AI/GenAI engineers validating prompt, model, RAG, and agent changes | **Daily user.** Highest interaction frequency. Adoption depends on her. |
| **U-2** | Devin — LLMOps Engineer | ML/LLMOps teams running scheduled benchmarks and post-deployment evaluations | **Daily user.** Owns continuous and post-release evaluation. |
| **U-3** | Priya — AI Platform Engineer | AI platform teams exposing evaluation as an internal service | **Internal champion and integrator.** Makes the platform standard, or doesn't. |
| **U-4** | Tomas — QA / Release Engineer | QA teams defining regression thresholds and release policies | **Policy author.** Decides what "ship" means. |
| **U-5** | Elena — AI Product Manager | Product teams comparing quality, latency, and cost | **Output consumer.** Makes trade-off decisions from the platform's reports. |
| **U-6** | Rachel — Governance / Compliance Lead | Regulated enterprises requiring reproducibility, auditability, approvals, and dataset governance | **Requirement source and blocker.** Converts "useful tool" into "approved platform." |

**Adoption dynamics `[PREMISE]`:** U-1 and U-2 generate usage. U-3 decides whether usage becomes standard. U-4 and U-5 consume output and confer legitimacy. U-6 holds veto power in regulated organizations. A product that delights U-1 alone becomes a developer utility that never gets funded; a product that satisfies U-6 alone becomes shelfware nobody uses. Both ends must be served — this is stated in [`prd.md`](prd.md) §4 and is repeated here because it is the single most important thing the persona set is meant to convey.

---

## U-1 — Maya, GenAI Application Engineer

> *"I changed one line of the system prompt. I have no idea what else it broke."*

**Canonical basis** `[CANON §5]` — *AI/GenAI engineers validating prompt, model, RAG, and agent changes.*

### Context
Maya builds a customer-facing AI feature — a support assistant with retrieval over an internal knowledge base. She ships changes several times a week: prompt edits, retrieval parameter tuning, occasionally a model swap. Her team is small. She is measured on shipping features, not on evaluation rigour, which means any evaluation practice that costs her more than a few minutes per change will lose to deadline pressure.

### Goals
- Ship prompt, retrieval, and model changes without silently degrading quality
- Know *before* merging whether a change is safe
- Understand *why* a change regressed, not merely that it did
- Spend her time building, not building evaluation infrastructure

### Frustrations `[PREMISE]`
- A prompt edit that improves one behaviour degrades another, and she finds out from a customer
- Her "test set" is a handful of examples in a notebook that drift out of date and are not shared with anyone
- Re-running an evaluation from three weeks ago is impossible; the prompt, the model version, and the examples have all changed
- Comparing two candidates means eyeballing outputs side by side, which does not scale past a dozen samples and is not defensible to anyone else
- She cannot distinguish "this change is better" from "this change got luckier on ten examples"

### Current workaround `[PREMISE]`
A notebook with a list of test prompts, manual reading of outputs, and intuition. Occasionally a script using an evaluation library, run once, results pasted into a pull request description and never reproduced.

### What she needs from this platform
| Need | Product obligation |
|---|---|
| Evaluation triggered from her existing workflow, not a separate ritual | Must be invocable from CI on a pull request (CAP-09) |
| Fast feedback on small changes | Must support small, fast suites, not only exhaustive benchmark runs |
| A verdict she can act on | Must state whether the change regressed, improved, or is indistinguishable — and must say "indistinguishable" when that is the truth |
| Per-sample drill-down | Must show *which* examples regressed and what the outputs were, not just an aggregate |
| Low integration cost | Must offer an adoption path that does not require her to expose or restructure her application first |

### Anti-goals — what makes her abandon the product
- Evaluation that takes longer than her patience on a pull request
- False regression alarms on unchanged code. **This is the fastest route to abandonment**: a gate that cries wolf gets disabled, and once disabled it is never re-enabled
- Being asked to hand-label hundreds of examples before getting any value
- A verdict she cannot explain to a reviewer who asks "how do you know?"

### Data she touches
DS-1 (dataset content), DS-2 (candidate outputs), DS-3 (retrieved contexts), DS-6 (prompts).

### Primary capabilities
CAP-01, CAP-02, CAP-03, CAP-07, CAP-08, CAP-09.

### Use cases
UC-01, UC-02, UC-03, UC-04, UC-05, UC-07, UC-08, UC-09, UC-12, UC-15, UC-16.

---

## U-2 — Devin, LLMOps Engineer

> *"It passed the gate last month. Nothing changed on our side. So why are the numbers different now?"*

**Canonical basis** `[CANON §5]` — *ML/LLMOps teams running scheduled benchmarks and post-deployment evaluations.*

### Context
Devin operates AI systems in production across several teams. He owns the scheduled benchmark runs, the post-deployment checks, and the alerting when quality drifts. He is the person paged when a provider degrades, a model is deprecated, or evaluation costs spike unexpectedly. He thinks in terms of runs, failures, retries, and budgets.

### Goals
- Detect quality drift after release, not only before it
- Run benchmarks on a schedule without babysitting them
- Distinguish *our system changed* from *the provider changed underneath us*
- Keep evaluation spend predictable and attributable
- Recover cleanly when a long evaluation run fails partway through

### Frustrations `[PREMISE]`
- A long benchmark run fails partway through and must be restarted from the beginning
- Provider rate limits make large runs unpredictable in duration
- A model provider silently updates a snapshot and every historical comparison becomes suspect, with no signal that anything happened
- Evaluation costs appear as an unattributed line item; nobody can say which team or which run consumed it
- Partial results are reported as if complete, so an aggregate computed over part of a dataset is indistinguishable from one computed over all of it

### Current workaround `[PREMISE]`
Cron jobs invoking scripts, results in a spreadsheet or a time-series dashboard, cost tracked at the provider account level with no per-run attribution, and manual restarts when runs fail.

### What he needs from this platform
| Need | Product obligation |
|---|---|
| Runs that survive interruption | Long evaluations must be resumable, not restart-from-zero |
| Honest partial results | An incomplete run must be visibly incomplete everywhere its numbers appear |
| Cost attribution | Spend must be attributable to run, project, and team |
| Drift detection | Quality change over time must be observable independently of any deliberate change |
| Provider change visibility | The exact model identity used must be recorded, so provider-side change is distinguishable from own-side change |
| Scheduled and post-deployment execution | CAP-10 |

### Anti-goals
- A system that reports a number without telling him how much of the dataset it actually covers
- Autonomous action against production. He wants a recommendation and an alert, not a platform that rolls back his deployment
- Unbounded cost. A misconfigured run that spends thousands of dollars before anyone notices is an incident, not an inconvenience

### Data he touches
DS-1, DS-2, DS-7 (provider credentials), DS-8 (cost records).

### Primary capabilities
CAP-02, CAP-07, CAP-08, CAP-10, CAP-11.

### Use cases
UC-02, UC-07, UC-08, UC-10, UC-11, UC-12.

---

## U-3 — Priya, AI Platform Engineer

> *"Five teams have five different definitions of 'faithfulness'. I need one substrate, or none of these numbers mean anything together."*

**Canonical basis** `[CANON §5]` — *AI platform teams exposing evaluation as an internal service.*

### Context
Priya builds internal platform capability for an organization with multiple AI product teams. She is not evaluating any single system; she is providing the capability by which all of them evaluate. She is judged on adoption, on reliability, and on whether she has created a maintenance burden for herself. She is deeply sceptical of tools that solve one team's problem while creating three teams' worth of integration work.

### Goals
- Provide one evaluation substrate the whole organization uses
- Make results comparable across teams and across time
- Support teams whose systems differ substantially — some are simple prompt-and-model, some are complex agents
- Allow teams to extend the platform with their own domain-specific evaluators without her becoming a bottleneck
- Keep teams isolated from one another

### Frustrations `[PREMISE]`
- Every team invents its own metric with the same name and different semantics, so cross-team comparison is meaningless
- A tool that works for one team's architecture cannot accommodate another's
- Extension requires forking, which means every team's fork drifts
- Isolation is by convention, so one team can see another's data by accident

### What she needs from this platform
| Need | Product obligation |
|---|---|
| Multiple integration modes | Teams with different architectures must all be able to adopt without rebuilding — the three approved integration modes exist for exactly this |
| First-class extensibility | Teams must be able to add their own evaluators through a stable, supported interface, not by forking |
| Real isolation between teams | Tenant and project boundaries must be enforced by the system, not by convention |
| Shared, versioned definitions | A metric named "faithfulness" must mean one specific, versioned thing |
| An API and a command-line interface, not only a web application | Platform teams integrate programmatically |

### Anti-goals
- Becoming the maintainer of every team's evaluation code
- A product whose extension model requires her to trust arbitrary code from other teams without any boundary
- Silent coupling: a change one team makes affecting another team's historical results

### Data she touches
All classes, in an operator capacity. Notably DS-7 (credential custody across tenants) and DS-9 (audit records).

### Primary capabilities
CAP-05, CAP-06, CAP-12, plus the extensibility surface underlying CAP-03 and CAP-04.

### Use cases
UC-05, UC-06, UC-14, UC-16, UC-17.

**Note on the v1 plugin trust boundary.** Priya's need for team-contributed evaluators is served in v1 by trusted-tenant plugins with defence-in-depth. This means v1 supports "evaluators written by teams inside your organization," not "arbitrary untrusted third-party code." That limitation is real, is documented in [`non-goals.md`](non-goals.md), and must be stated to her plainly rather than implied away — she is precisely the persona who would be harmed by overstating it.

---

## U-4 — Tomas, QA / Release Engineer

> *"Give me a rule I can defend. 'The score went down a bit' is not a release policy."*

**Canonical basis** `[CANON §5]` — *QA teams defining regression thresholds and release policies.*

### Context
Tomas owns release quality across several products. He is accustomed to deterministic test suites where a failure is unambiguous. AI systems break his mental model: the same input produces different outputs, and "quality" is a distribution rather than a boolean. He needs release rules that are precise enough to automate and defensible enough to justify when they block someone's release.

### Goals
- Define release criteria that are precise, automated, and consistently applied
- Block releases that genuinely regress; do not block releases that do not
- Give developers an unambiguous reason when a release is blocked
- Handle exceptional cases through a governed process rather than by disabling the check

### Frustrations `[PREMISE]`
- Thresholds are picked arbitrarily and defended by nobody
- Non-deterministic results mean the same candidate passes and then fails on re-run, destroying trust in the gate
- With many metrics checked at once, something is almost always "down a bit," so the gate either blocks everything or is tuned until it blocks nothing
- No governed path exists for a justified exception, so the exception becomes "someone disabled the check"

### What he needs from this platform
| Need | Product obligation |
|---|---|
| Policies as explicit, versioned artifacts | A release policy must be a reviewable object, not a threshold buried in a script |
| Statistically defensible verdicts | The gate must distinguish a real regression from noise, and must say so |
| An honest inconclusive outcome | When the evidence cannot support a decision, the gate must say so rather than guessing — this is what makes the gate trustworthy enough to leave enabled |
| Multiple enforcement modes | Hard fail, warning, and manual approval are different situations and need different handling |
| Governed exceptions | An exception must be recorded, justified, attributed, and time-bounded |
| Evidence attached to every decision | He must be able to answer "why was this blocked?" without rerunning anything |

### Anti-goals
- A flaky gate. **This is his abandonment condition.** A gate that produces different verdicts for an unchanged candidate will be turned off, and his trust will not return
- A verdict he cannot explain to an engineer who disputes it
- An exception mechanism that is really just a bypass with no record

### Data he touches
DS-2, DS-8 (cost thresholds in policies), DS-9 (approval and exception records).

### Primary capabilities
CAP-08, CAP-09, CAP-12.

### Use cases
UC-01, UC-06, UC-08, UC-09, UC-11, UC-14, UC-15.

---

## U-5 — Elena, AI Product Manager

> *"Is the cheaper model actually worse, or does it just feel worse? And worse by how much, for which users?"*

**Canonical basis** `[CANON §5]` — *Product teams comparing quality, latency, and cost.*

### Context
Elena owns an AI product line. She makes trade-off decisions: whether to move to a cheaper model, whether a quality improvement justifies a latency increase, whether to invest engineering time in retrieval or in prompting. She is not going to read a statistics textbook, but she will absolutely notice if a number is presented with more confidence than it deserves — and she will stop trusting the source.

### Goals
- Make model and provider decisions on evidence rather than demos
- Understand the quality/cost/latency trade-off for a proposed change
- Communicate quality status to leadership without overclaiming
- Track whether quality is improving over time

### Frustrations `[PREMISE]`
- Model comparisons arrive as anecdotes and cherry-picked examples
- Quality, cost, and latency are measured separately, at different times, by different people, so no single trade-off view exists
- Reports state improvements without indicating whether the difference is meaningful
- She cannot answer "is our AI quality getting better?" with anything but impressions

### What she needs from this platform
| Need | Product obligation |
|---|---|
| Three axes from one run | Quality, cost, and latency must come from the same reproducible evaluation |
| Comparison at the decision level | "Which of these candidates should we ship?" must be directly answerable |
| Honest uncertainty, in plain language | A difference that is not meaningful must be presented as not meaningful, without requiring statistical literacy to notice |
| Trend over time | CAP-11 |
| Reports she can forward | Executive-level output that does not overstate |

### Anti-goals
- A dashboard of numbers with no indication of which differences matter
- Being handed a conclusion that later turns out to have been noise. **This destroys her trust permanently**, because she will have repeated it to leadership
- Statistical presentation so dense that she cannot use it

### Data she touches
DS-8 (cost), plus aggregate quality and latency reporting. Rarely raw DS-1/DS-2.

### Primary capabilities
CAP-02, CAP-08, CAP-11.

### Use cases
UC-02, UC-12, UC-13.

**Design consequence.** Elena is the reason uncertainty must be expressed in the product surface and not only in the underlying computation. A confidence interval that exists in the data model but is absent from the report she reads has not served its purpose.

---

## U-6 — Rachel, Governance / Compliance Lead

> *"Show me what evidence supported that release, who approved it, and prove the dataset has not changed since."*

**Canonical basis** `[CANON §5]` — *Regulated enterprises requiring reproducibility, auditability, approvals, and dataset governance.*

### Context
Rachel is responsible for ensuring that AI systems deployed by the organization meet internal governance and external regulatory expectations. She is not an engineer. She reads evidence, asks questions, and either signs or does not. Her four canonical questions map directly to `[CANON §4]`: who changed what, what evidence supported release, who approved it, and whether quality later drifted.

### Goals
- Demonstrate that AI releases follow a controlled, evidenced process
- Reconstruct any past release decision on demand
- Ensure evaluation datasets are governed: sourced, approved, access-controlled, retained, and deleted appropriately
- Ensure sensitive data in evaluation assets is handled correctly
- Satisfy data-subject deletion obligations without destroying the audit record

### Frustrations `[PREMISE]`
- Release decisions are documented in chat threads and tickets, if at all
- Evaluation datasets are copied between machines with no provenance, no approval record, and no access control
- Reproducing a past evaluation is impossible because nothing was versioned
- Deletion requests conflict with audit retention, and nobody can articulate how both are satisfied
- Personal data enters evaluation datasets because they were built from production traffic without review

### What she needs from this platform
| Need | Product obligation |
|---|---|
| Reproducible evidence | A past release decision must be reconstructable from stored records |
| Immutable released datasets | A dataset version used for a decision must be provably unchanged since |
| Approval records | Who approved what, when, and on what basis |
| Complete audit history | Every governance-relevant change recorded |
| Access control and isolation | CAP-12 |
| Deletion that does not destroy audit | Data-subject deletion and audit integrity must coexist, and the mechanism must be explainable to her |
| Honest capability statements | She must not be told the product provides a compliance guarantee it does not |

### Anti-goals
- Claimed compliance certifications the product does not hold. **This is disqualifying** — an overclaim discovered during her review ends the evaluation and damages trust in everything else
- A system that autonomously modifies datasets, policies, or release decisions
- "Deleted" data that remains reachable somewhere in the system

### Data she touches
DS-1 (governance over dataset content), DS-9 (audit records), and the retention and deletion posture across all classes.

### Primary capabilities
CAP-05, CAP-07, CAP-12.

### Use cases
UC-05, UC-07, UC-14, UC-18.

**Design consequence.** Rachel is the persona whose needs most directly generate the deletion-versus-immutability tension identified and resolved at Architecture Gate 0. She needs both the immutable evidence trail and the ability to honour a deletion request, and the product must be able to explain to her, in her own terms, how it provides both without either being fictional.

---

## Cross-persona tensions

Real products serve users whose needs conflict. Naming those conflicts now prevents a requirement set that silently favours one persona.

| # | Tension | Between | Resolution direction |
|---|---|---|---|
| **T-1** | **Speed vs. statistical confidence.** Maya wants a fast verdict on a pull request. Meaningful confidence requires enough samples, which costs time and money. | U-1 ↔ U-4, U-5 | Do not resolve by silently lowering confidence. Make sample-size adequacy visible, and make the honest inconclusive outcome a first-class result. Support both fast smoke suites and thorough gating suites as different, explicitly-labelled things. |
| **T-2** | **Sensitivity vs. false alarms.** Tomas wants regressions caught. Maya will disable a gate that produces false alarms. | U-1 ↔ U-4 | Treat false-alarm rate as a product-quality property, not a tuning parameter left to the user. A gate that is not trusted is not used, so the product's own credibility depends on this. |
| **T-3** | **Openness vs. isolation.** Priya wants teams to contribute evaluators. Rachel and Priya both need real isolation. | U-3 ↔ U-3, U-6 | v1 serves this with trusted-tenant plugins and defence-in-depth, with the limitation stated openly rather than obscured. |
| **T-4** | **Immutability vs. deletion.** Rachel needs released datasets provably unchanged, and also needs data-subject deletion honoured. | U-6 ↔ U-6 | Resolved at Architecture Gate 0. The product must be able to explain the resolution to a non-engineer. |
| **T-5** | **Simplicity vs. honesty.** Elena wants a clear answer. Honest evaluation frequently produces "we cannot tell from this evidence." | U-5 ↔ product principle PR-5 | Never resolve toward false clarity. Invest in communicating uncertainty legibly rather than in hiding it. |
| **T-6** | **Cost visibility vs. cost control.** Devin needs spend attributable and bounded. Maya needs evaluation to feel free at the point of use. | U-1 ↔ U-2 | Cost limits belong to the project and the policy, not to the individual engineer's workflow. Maya should encounter a budget as a pre-run estimate, not as a mid-run failure. |

**T-2 is the tension most likely to determine whether the product succeeds.** Both failure directions are fatal: a gate that misses regressions provides no value, and a gate that raises false alarms gets disabled and then provides no value. The product's central technical difficulty and its central product risk are the same thing.

---

## Coverage confirmation

| Canonical group `[CANON §5]` | Persona | Use cases |
|---|---|---|
| U-1 AI/GenAI engineers | Maya | UC-01, UC-02, UC-03, UC-04, UC-05, UC-07, UC-08, UC-09, UC-12, UC-15, UC-16 |
| U-2 ML/LLMOps teams | Devin | UC-02, UC-07, UC-08, UC-10, UC-11, UC-12 |
| U-3 AI platform teams | Priya | UC-05, UC-06, UC-14, UC-16, UC-17 |
| U-4 QA teams | Tomas | UC-01, UC-06, UC-08, UC-09, UC-11, UC-14, UC-15 |
| U-5 Product teams | Elena | UC-02, UC-12, UC-13 |
| U-6 Regulated enterprises | Rachel | UC-05, UC-07, UC-14, UC-18 |

All six canonical user groups have a persona and at least one use case.

**Personas deliberately not created.** `[CANON §5]` names six groups; six personas exist. No additional personas were invented. In particular there is no "AI researcher" or "data labeller" persona, because neither appears in the canonical user set, and adding unrequested personas would expand product scope without authorization.

---

## Document history

| Version | Milestone | Change |
|---|---|---|
| 0.1 | M1.1 | Initial draft. Pending external review. |
