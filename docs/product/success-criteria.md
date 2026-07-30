# Success Criteria
## Continuous LLM Evaluation & Regression Testing Platform

| Field | Value |
|---|---|
| Status | **Draft — pending external review** |
| Milestone | M1.1 — Product Definition, Personas, and Use Cases |
| Phase | Phase 1 — Product Foundation |
| Source | `[CANON §20]`, `[CANON §24]`, `[CANON §27]` |
| Related | [`prd.md`](prd.md) · [`personas.md`](personas.md) · [`use-cases.md`](use-cases.md) · [`non-goals.md`](non-goals.md) |

---

> # ⚠ NOTHING IN THIS DOCUMENT IS A MEASURED RESULT
>
> Every figure below is a **target**: a threshold chosen in advance, against which the system will later be measured. **No target in this document has been measured, tested, benchmarked, or observed**, because no part of this system has been built.
>
> The `Status` column of every criterion reads `NOT YET MEASURED` and will only change when an executed run produces evidence that is retained in the repository.
>
> `[CANON §20]` — *"Never claim a metric unless an executed test or benchmark produced it."*
> `[CANON §24]` — *"Never invent resume metrics."*
>
> Several targets below are explicitly marked **PROVISIONAL — pending spike**. Those numbers are placeholders for values that can only be chosen responsibly after measurement, and choosing them now by intuition would be exactly the failure mode these rules exist to prevent.

---

## How to read this document

Each criterion has:

| Field | Meaning |
|---|---|
| **ID** | Stable identifier for traceability |
| **Criterion** | What is being asserted |
| **Metric** | How it is measured — precisely enough that two people would measure it the same way |
| **Target** | The threshold. **Unvalidated.** |
| **Method** | How evidence will be produced |
| **Status** | Always `NOT YET MEASURED` in this document |
| **Owner milestone** | Where the evidence is expected to be produced |

**Confidence markers on targets:**

| Marker | Meaning |
|---|---|
| **FIRM** | The target follows from a canonical requirement or a logical necessity; it is not a guess |
| **PROVISIONAL — pending spike** | A number that cannot be responsibly chosen without measurement. Named here so the gap is visible rather than hidden |
| **PROVISIONAL — pending requirements** | Depends on the formal requirement set from M1.3 |

---

## Category A — Functional completeness

Whether the product does what the canonical specification says it must do.

| ID | Criterion | Metric | Target | Method | Status | Owner |
|---|---|---|---|---|---|---|
| **SC-A1** | All twelve canonical capabilities are implemented | Proportion of CAP-01…CAP-12 with a working implementation exercised by an automated end-to-end test | **100%** — FIRM | Automated end-to-end test suite, one test per capability | NOT YET MEASURED | Phase 15 |
| **SC-A2** | All eighteen use cases are supported | Proportion of UC-01…UC-18 whose main flow is exercised by an automated test | **100%** — FIRM | End-to-end tests mapped to use-case IDs | NOT YET MEASURED | Phase 15 |
| **SC-A3** | Declared failure flows behave as specified | Proportion of use-case failure flows with an automated test proving the specified behaviour | **100%** — FIRM | Failure-path test suite | NOT YET MEASURED | Phase 15 |
| **SC-A4** | All three integration modes work | Each of the three approved system-under-test integration modes has a working end-to-end path | **3 of 3** — FIRM | Integration tests per mode | NOT YET MEASURED | Phase 5 |
| **SC-A5** | Every canonical failure mode is handled | Proportion of failure modes named in `[CANON §21]` with a defined behaviour and a test | **100%** — FIRM | Failure-mode test suite mapped to canonical items | NOT YET MEASURED | Phase 15 |

---

## Category B — Evaluation validity

**The most important category in this document.** A platform that measures the wrong thing confidently is worse than no platform, because it produces decisions people act on.

| ID | Criterion | Metric | Target | Method | Status | Owner |
|---|---|---|---|---|---|---|
| **SC-B1** | Deterministic evaluators discriminate correctly | Proportion of deterministic evaluators that correctly separate hand-built known-good from known-bad fixtures | **100%** — FIRM | Evaluator meta-tests `[CANON §20]` | NOT YET MEASURED | Phase 5 |
| **SC-B2** | Judges discriminate correctly | Proportion of judges that correctly separate known-good from known-bad fixtures | **100%** — FIRM | Judge meta-tests | NOT YET MEASURED | Phase 8 |
| **SC-B3** | **Judge agreement with human labels is measured and published** | Agreement between each judge and a human-labelled calibration set, computed and reported alongside every result that uses that judge | **Measured and disclosed for every judge in use** — FIRM.<br>*No numeric agreement threshold is set here.* — PROVISIONAL — pending spike | Human-labelled calibration set per benchmark suite | NOT YET MEASURED | Phase 8 |
| **SC-B4** | Inter-judge agreement is measured | Agreement statistics computed per run and tracked over time | **Computed and reported for every judged run** — FIRM | Agreement computation over judge votes | NOT YET MEASURED | Phase 8 |
| **SC-B5** | Judge self-consistency is measured | Variability of a judge's own scores on identical repeated inputs | **Measured, and reported when a decision depends on it** — FIRM | Repeated-invocation measurement | NOT YET MEASURED | Phase 8 |
| **SC-B6** | Position bias in pairwise judging is detected and controlled | Rate at which a judge's preference reverses when candidate order is swapped | **Measured and reported per judge**; control applied by default — FIRM | Swap-replication measurement | NOT YET MEASURED | Phase 8 |
| **SC-B7** | **Failed and abstained evaluations are never scored as zero** | Count of failed or abstained evaluations that reached an aggregate as a numeric zero | **Exactly 0** — FIRM | Automated test; enforced structurally | NOT YET MEASURED | Phase 5 |
| **SC-B8** | Every aggregate carries completeness | Proportion of aggregate metrics persisted without completeness metadata | **Exactly 0** — FIRM | Automated test; enforced structurally | NOT YET MEASURED | Phase 5 |
| **SC-B9** | Dataset quality checks detect what they claim to detect | Detection rate on crafted fixtures for duplicates, near-duplicates, leakage, personal data, and judge-manipulating content | **Detection on every crafted positive fixture**; false-positive rate on clean fixtures **measured and reported** — FIRM for detection, PROVISIONAL — pending spike for the acceptable false-positive rate | Fixture-based check tests | NOT YET MEASURED | Phase 4 |
| **SC-B10** | Judges resist manipulation by evaluated content | Success rate of an adversarial corpus at inflating a judge's score | **Measured and reported honestly, whatever the value.** No target claim of immunity is made — FIRM | Adversarial corpus with measured outcomes | NOT YET MEASURED | Phase 8 / Phase 12 |

**On SC-B3 and SC-B10.** These two criteria are deliberately written to require *disclosure* rather than to assert a threshold. Setting a numeric target for judge-human agreement before measuring it would be inventing the very kind of metric `[CANON §24]` forbids. Claiming immunity to prompt injection would be worse — it is a claim no honest system makes. In both cases the product commitment is that the number is measured and shown, not that it reaches a value chosen in advance.

---

## Category C — Statistical validity

| ID | Criterion | Metric | Target | Method | Status | Owner |
|---|---|---|---|---|---|---|
| **SC-C1** | **False regressions are controlled** | Proportion of comparisons of an unchanged candidate against its own baseline that are classified as a regression | **At or below the configured significance level** — FIRM.<br>Specific level — PROVISIONAL — pending spike | Simulation under the null hypothesis, executed and recorded | NOT YET MEASURED | Phase 7 |
| **SC-C2** | Trivially small differences are not reported as regressions | Behaviour on a detectable but negligible difference | **Classified as not meaningful, never as a regression** — FIRM | Table-driven classification tests | NOT YET MEASURED | Phase 7 |
| **SC-C3** | Insufficient evidence produces an explicit inconclusive result | Behaviour when sample size is inadequate relative to variability | **Inconclusive** — never pass, never fail — FIRM | Classification tests | NOT YET MEASURED | Phase 7 |
| **SC-C4** | Instrument-limited comparisons are identified | Behaviour when judge variability exceeds the observed difference | **Reported as limited by the measuring instrument** — FIRM | Classification tests | NOT YET MEASURED | Phase 7 |
| **SC-C5** | Minimum sample guidance is produced | Whether the platform states the sample size needed to detect a difference of a given size | **Produced for every comparison** — FIRM.<br>Specific sample sizes — PROVISIONAL — pending spike | Power analysis grounded in measured variability | NOT YET MEASURED | Phase 7 |
| **SC-C6** | Comparing many metrics does not inflate false alarms | Behaviour of a multi-metric gate on an unchanged candidate | **False-alarm rate controlled across the metric family** — FIRM.<br>Method — deferred to architecture | Simulation, executed and recorded | NOT YET MEASURED | Phase 7 |
| **SC-C7** | Statistical results are deterministic | Repeat computation on identical inputs | **Identical output every time** — FIRM | Repeated-computation test | NOT YET MEASURED | Phase 7 |

**SC-C1 and SC-C6 together are the product's credibility test.** Persona tension T-2 in [`personas.md`](personas.md) identifies false alarms as the fastest route to a gate being disabled. These two criteria are how that risk is held to account, and both require measurement rather than assertion.

---

## Category D — Reproducibility

| ID | Criterion | Metric | Target | Method | Status | Owner |
|---|---|---|---|---|---|---|
| **SC-D1** | Run configuration is captured completely | Proportion of inputs affecting a result that are captured in the run record | **100%** — FIRM | Test: altering any captured input changes the run's identity | NOT YET MEASURED | Phase 5 |
| **SC-D2** | Re-scoring reproduces identical scores | Re-scoring stored outputs with identical evaluator versions | **Identical scores** — FIRM | Replay test | NOT YET MEASURED | Phase 5 |
| **SC-D3** | Re-execution reproduces identical outputs under deterministic conditions | Full re-execution using a deterministic substitute for the model | **Identical outputs** — FIRM | Replay test with a deterministic model substitute | NOT YET MEASURED | Phase 5 |
| **SC-D4** | Non-reproducibility is disclosed, not hidden | Whether the platform states what could not be reproduced and why | **Stated in every reproduction result** — FIRM | Reproducibility report test | NOT YET MEASURED | Phase 5 |
| **SC-D5** | Released dataset versions are provably unchanged | Attempts to modify a released dataset version through any available path | **All rejected** — FIRM | Immutability tests at every layer | NOT YET MEASURED | Phase 4 |
| **SC-D6** | Caching never changes a result | Comparison of cached and uncached execution of the same configuration | **Identical results** — FIRM | Cache-correctness tests | NOT YET MEASURED | Phase 5 |

**On SC-D3.** `[GATE-0]` The reproducibility commitment is **configuration reproducibility plus artifact replay**, not byte-identical regeneration of model output. Hosted providers do not guarantee identical outputs for identical inputs. SC-D3 therefore tests determinism against a deterministic substitute, which is the strongest honest claim available. Stating otherwise would be an unsupported claim.

---

## Category E — Security and governance

| ID | Criterion | Metric | Target | Method | Status | Owner |
|---|---|---|---|---|---|---|
| **SC-E1** | **No cross-tenant access is possible** | Proportion of tenant-scoped resources with a passing cross-tenant denial test | **100%**, and **zero** successful cross-tenant accesses — FIRM | Systematic cross-tenant test suite, verified by deliberately disabling controls and confirming tests fail | NOT YET MEASURED | Phase 3 onward; verified Phase 12 |
| **SC-E2** | No secrets in the repository | Secrets found by scanning the full history and working tree | **Exactly 0** — FIRM | Automated secret scan over history and tree | NOT YET MEASURED | Every milestone; final Phase 15 |
| **SC-E3** | Credentials never appear in logs, traces, artifacts, or errors | Occurrences in captured output | **Exactly 0** — FIRM | Automated leakage tests | NOT YET MEASURED | Phase 5 |
| **SC-E4** | Every governance-relevant action is audited | Proportion of mutating operations emitting an audit record | **100%** — FIRM | Automated audit-coverage test derived from the operation set | NOT YET MEASURED | Phase 12 |
| **SC-E5** | Deletion removes content everywhere it propagated | Locations still holding deleted content after a deletion request | **Exactly 0**, with structural and audit records intact | FIRM | Deletion-cascade tests across every store | NOT YET MEASURED | Phase 12 |
| **SC-E6** | No unauthorized action is reachable | Proportion of operations with an enforced authorization check | **100%**, deny by default — FIRM | Generated authorization test matrix | NOT YET MEASURED | Phase 12 |
| **SC-E7** | Misbehaving evaluators cannot destabilize the platform | Outcome when an evaluator hangs, exhausts memory, crashes, or attempts undeclared actions | **Contained in every case**; recorded as an evaluation failure — FIRM | Misbehaving-plugin test suite | NOT YET MEASURED | Phase 5 |
| **SC-E8** | Requests to internal or restricted network addresses are blocked | Proportion of a crafted address-abuse corpus that reaches a restricted destination | **Exactly 0** — FIRM | Dedicated network-abuse test suite | NOT YET MEASURED | Phase 5 |
| **SC-E9** | No compliance certification is claimed anywhere | Occurrences of unsupported compliance or certification claims in any artifact | **Exactly 0** — FIRM | Documentation scan | NOT YET MEASURED | Every milestone |

---

## Category F — Reliability and operability

| ID | Criterion | Metric | Target | Method | Status | Owner |
|---|---|---|---|---|---|---|
| **SC-F1** | Interrupted runs resume without loss or duplication | Lost or duplicated work units after killing a worker mid-run and restarting | **Exactly 0 lost, exactly 0 duplicated** — FIRM | Kill-and-resume integration test | NOT YET MEASURED | Phase 5 |
| **SC-F2** | Provider failure is isolated | Effect of one provider's outage on runs using other providers | **No effect** — FIRM | Fault-injection test | NOT YET MEASURED | Phase 5 |
| **SC-F3** | Deploys do not lose in-flight work | Work units lost during a rolling restart mid-run | **Exactly 0** — FIRM | Rolling-restart test | NOT YET MEASURED | Phase 14 |
| **SC-F4** | Budget limits are enforced | Spend beyond a configured ceiling | **Exactly 0 overspend**; run halts with partial results retained — FIRM | Budget-enforcement test | NOT YET MEASURED | Phase 5 |
| **SC-F5** | Cost accounting reconciles exactly | Difference between attributed cost and provider-reported usage | **Exact reconciliation** — FIRM | Reconciliation test against provider-reported usage | NOT YET MEASURED | Phase 5 / Phase 13 |
| **SC-F6** | Platform failures are distinguishable from quality failures | Whether a platform outage can present as a quality regression | **Never** — FIRM | Failure-classification tests | NOT YET MEASURED | Phase 10 |
| **SC-F7** | A run at target scale completes within an acceptable duration | Wall-clock duration and resource usage for a run at the target sample scale | PROVISIONAL — pending requirements and spike | Executed load test with recorded measurements | NOT YET MEASURED | Phase 14 |
| **SC-F8** | Service level objectives are met | Availability and latency against defined objectives | PROVISIONAL — pending requirements | Measured against a running instance under synthetic load | NOT YET MEASURED | Phase 13 / Phase 14 |
| **SC-F9** | Backup and restore work | Executed restore drill with verified data integrity | **At least one successful drill with measured recovery time** — FIRM | Executed drill, results recorded | NOT YET MEASURED | Phase 14 |

**SC-F7 and SC-F8 carry no numbers.** Latency, throughput, and availability targets belong to the non-functional requirements owned by M1.3, and any number written here now would be invented. They are listed so the gap is visible rather than forgotten.

---

## Category G — Engineering quality

| ID | Criterion | Metric | Target | Method | Status | Owner |
|---|---|---|---|---|---|---|
| **SC-G1** | Strict typing is clean | Type-checker errors under strict configuration | **Exactly 0** — FIRM | Type checker in continuous integration | NOT YET MEASURED | Every milestone |
| **SC-G2** | Architecture boundaries hold | Dependency-direction violations | **Exactly 0** — FIRM | Automated architecture contract check | NOT YET MEASURED | Every milestone |
| **SC-G3** | Test coverage meets its floors | Branch coverage by layer | Core domain **≥ 90%**, application **≥ 85%**, adapters **≥ 70%** — PROVISIONAL — pending requirements | Coverage measurement in continuous integration | NOT YET MEASURED | Every milestone |
| **SC-G4** | No flaky tests | Tests failing intermittently across repeated full runs | **Exactly 0** — FIRM | Repeated full-suite execution | NOT YET MEASURED | Phase 15 |
| **SC-G5** | No live provider calls in continuous integration | Outbound provider calls during a CI run | **Exactly 0** — FIRM | Network-blocking test fixture | NOT YET MEASURED | Every milestone |
| **SC-G6** | No placeholders in production paths | Unimplemented markers or stubs outside tests | **Exactly 0** — FIRM | Automated scan | NOT YET MEASURED | Every milestone |
| **SC-G7** | Every requirement is traced to implementation and test | Requirements with no owning implementation or no test | **Exactly 0** — FIRM | Traceability generator with a failing check | NOT YET MEASURED | Phase 3 onward |

**SC-G3 is marked provisional.** Coverage floors are a judgement call that should follow from the requirement set and the risk profile of each layer, not from convention. The values shown are the ones proposed and approved at Architecture Gate 0 and are carried forward, but they are subject to M1.3.

---

## Category H — Credibility

`[CANON §24]`, `[CANON §27]`. These criteria govern the project's own honesty.

| ID | Criterion | Metric | Target | Method | Status | Owner |
|---|---|---|---|---|---|---|
| **SC-H1** | **No claim without evidence** | Quantitative claims in any repository artifact lacking committed raw evidence | **Exactly 0** — FIRM | Automated scan plus review at every milestone | NOT YET MEASURED | Every milestone |
| **SC-H2** | Documentation matches the built system | Architecture documents contradicted by the implementation | **Exactly 0** — FIRM | Documentation audit against code | NOT YET MEASURED | Phase 15 |
| **SC-H3** | Limitations are stated, not implied | Whether each known limitation is documented explicitly | **All documented** — FIRM | Limitations document reviewed against the system | NOT YET MEASURED | Phase 15 |
| **SC-H4** | Headline results are independently reproducible | Whether a reader can reproduce every headline figure from committed instructions and evidence | **All reproducible** — FIRM | Reproduction from a clean checkout | NOT YET MEASURED | Phase 15 |
| **SC-H5** | Requirement coverage is demonstrable | Proportion of canonical requirements traceable to implementation and evidence | **100%** — FIRM | Traceability matrix audit | NOT YET MEASURED | Phase 15 |

---

## Definition of success

`[CANON §27]` The canonical specification defines overall success as a repository proving its author can *design, implement, test, deploy, observe, secure, govern, and explain an enterprise AI quality platform — not merely call evaluation libraries.*

Restated as a single checkable statement:

> **The project is successful when a competent reviewer can, from a clean checkout, reproduce every quantitative claim it makes, trace every canonical requirement to working code and a passing test, and find no place where the system claims more confidence, more coverage, or more capability than it has demonstrated.**

The specific properties `[CANON §27]` names, each mapped to the criteria that would evidence it:

| `[CANON §27]` property | Evidenced by |
|---|---|
| Reproducible LLM evaluation | SC-D1 … SC-D6 |
| Regression prevention | SC-C1 … SC-C7, SC-A1 |
| Golden dataset governance | SC-D5, SC-B9, SC-E5 |
| CI/CD release gates | SC-A1, SC-A2, SC-F6 |
| RAG and agent evaluation | SC-A1, SC-B1, SC-B2 |
| Multi-judge uncertainty handling | SC-B3 … SC-B6, SC-C4 |
| Production observability | SC-F5, SC-F8 |
| Disciplined software architecture | SC-G1 … SC-G7 |

---

## Criteria deliberately not defined here

| Not defined | Why | Owner |
|---|---|---|
| Adoption, retention, revenue, user counts | This is a single-developer project with no users. Any such target would be fictional. | Not applicable |
| Latency, throughput, availability numbers | Belong to the non-functional requirement set | M1.3 |
| Judge agreement thresholds | Cannot be chosen responsibly before measurement | Judge-reliability spike |
| Significance levels and minimum sample sizes | Cannot be chosen responsibly before measurement | Statistical-power spike |
| Competitive benchmarks against other products | Requires competitive analysis, and any comparison would need executed evidence for both sides | M1.2 |
| Storage and infrastructure cost targets | Depend on measurements not yet taken | Artifact-volume spike |

---

## Document history

| Version | Milestone | Change |
|---|---|---|
| 0.1 | M1.1 | Initial draft. Pending external review. No criterion has been measured. |
