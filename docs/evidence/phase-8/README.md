# Validation Evidence — Phase 8

Phase: **Phase 8 — Agentic evaluation layer: planner agent, judge ensemble,
bounded reflection, historical memory**
Milestones: M8.1 through M8.9

## Contents

| File | What it is |
|---|---|
| `check_phase8.py` | Phase validator, 34 checks. `python docs/evidence/phase-8/check_phase8.py .` |
| `selftest_phase8.py` | Plants a violation per Phase 8 rule and proves each is caught. Refuses to run on a dirty tree |
| `probe_judge_schema.py` | 35 constraint and trigger probes as the runtime role, 14 more as a superuser |
| `injection-corpus.json` | Adversarial content and replies for `REQ-N-SEC-3`, executed by the tests and by the validator |
| `probe-output.txt` | Verbatim output of the probes |
| `validation-output.txt` | Verbatim output of the validator |
| `test-output.txt` | Verbatim output of the test suite with coverage |
| `selftest-output.txt` | Verbatim output of the validator's own self-test |

Run in an environment where this package and its dev extras are installed
(`python -m pip install -e ".[dev]"`) with the compose stack up, or set
`CLEP_TEST_PYTHON` to such an interpreter.

## What Phase 8 is for

The platform's whole argument is that it does not guess. Phase 8 adds the parts
that reason, which is where that argument is easiest to lose — not by breaking,
but by producing a judge layer that **looks** rigorous. An ensemble that cannot
disagree with itself. A disagreement measure that reports zero when it measured
nothing. A regeneration loop that quietly re-asks a judge until the number
improves. A bound with a default nobody chose. A plan editable after somebody
signed it.

Canonical §7 is the other half: reasoning only where reasoning adds value.
Validation, persistence, arithmetic and thresholds stayed conventional.

## The decision that needed ADR-017

ADR-004 decided the *shape* of consensus and deliberately left three things
open: the agreement metric, the escalation threshold, and ensemble composition.
Implementation cannot proceed on all three being open — a consensus function has
to compute something, and a choice made in code rather than in a record is a
methodology nobody decided.

[ADR-017](../../adr/ADR-017-judge-agreement.md) closes the two that are
structural and leaves the third where ADR-004 left it, the same split ADR-016
drew against ADR-007.

| Decided | Left open |
|---|---|
| Disagreement is the **range** of the scoring votes | The threshold value |
| An ensemble must be able to disagree: ≥2 judges, ≥2 configurations, no configuration holding a majority | The minimum scoring-vote count |
| Fewer than two scoring votes reports disagreement at its **maximum**, flagged unmeasured | Ensemble size |
| The verdict is the median, and confidence is `1 − disagreement`, which is not a probability | |

The range was chosen because it is monotone in the single worst dissenter, which
is exactly the signal `REQ-F-AG-4` escalates on. A mean deviation hides one
dissenter among four agreeable ones — the more judges you buy, the better it
hides, which makes escalation something you can pay to avoid.

## Three defences against injected content

`REQ-N-SEC-3` asks that no injected instruction change a score or a gate
outcome. `injection-corpus.json` carries 16 hostile contents and 7 hostile
replies; they are executed by `tests/test_injection.py` and again by `P-23`.

| # | Defence | What it rests on |
|---|---|---|
| 1 | **Containment.** Untrusted text is fenced and cannot close its own fence | The property asserted is that the instruction region is **byte-identical** for every corpus entry. A mitigation, and documented as one |
| 2 | **A constrained parse.** A reply is a bounded score, an abstention, or nothing | Load-bearing. There is no reply that means "pass the gate" — an injected verdict parses to `failed` |
| 3 | **The ensemble.** One judge decides nothing | Load-bearing, and it does not depend on the model behaving. A judge talked into 1.0 disagrees with the others and escalates |

The residual is stated rather than hidden: an injection that moves one judge by
less than the configured threshold does not escalate. What holds is that with
three judges the median of one compromised vote and two honest ones **always
lies between the two honest votes** — a single judge cannot carry the verdict
anywhere the honest judges did not already bracket.

## What the probes found

A defect, and one the runtime-role pass could not have found.
`refuse_change_to_used_ensemble` is attached to two tables and coalesced
`OLD.judge_ensemble_id` with `OLD.id`, which raises `record "old" has no field
"judge_ensemble_id"` on the ensemble table. Every update to any ensemble was
refused — including a legitimate correction to one that had never judged — and
from outside it looked exactly like correct enforcement.

Under the runtime role the grant refused first and hid it. The superuser pass
found it, and the fix came with the positive probe that would have caught it:
**an ensemble that has judged nothing can still be corrected**. A trigger that
refuses everything is not a stricter trigger; it is a broken one, and an
uncalibrated threshold nobody can fix is the opposite of what ADR-017 wants.

## The requirement that finally became enforceable

`REQ-F-08-8` has said "when a **judge** or evaluator version changes" since
Phase 1. Until Phase 8 only half of it could be enforced, because judge versions
were not captured. `judge_version` now joins run identity (ADR-004 D-5) and the
pinned comparability kinds, and `P-25` asserts behaviourally that two identities
differing only in judge version are refused with the judge named in the reason.

`judge_agreement` likewise stopped being a criterion source that abstains
because the capability did not exist. It reads the per-sample disagreement, and
only where the disagreement was **measured** — pairing an unmeasured 1 against a
real spread would read as a large regression caused by a judge failing to answer.

## Two tables for one judgement

`judge_run` is the attempt and always exists. `judge_vote` is the score and
exists only when there is one. A nullable score column on the attempt would put
"did not answer" one NULL check away from "answered zero"; here an unscored
judgement has **no row to read as a zero**. That is `REQ-X-8` enforced by
absence, and it is why the domain model's two entities earn their keep.

## Tenant isolation

Thirteen new tables, all tenant-scoped, `ENABLE` + `FORCE`, every foreign key
carrying `organization_id`. The parametrised negative-test list is now **derived
from the schema** rather than hand-maintained — which immediately found three
tables (`dataset_label`, `evaluator_definition`, `evaluator_version`) that had
been absent from it since the phases that added them.

Judges are tenant-scoped throughout, narrowing the domain model's "project or
global". Recorded as [D-2](../../architecture/tracked-debt.md), with the reason:
a judge version binds a rubric to a model configuration, and a global row cannot
carry a tenant-carrying foreign key into tenant data.

## Scope, stated rather than implied

| Capability | Status |
|---|---|
| Judge ensemble, consensus, escalation | Implemented; escalation is a reviewable surface, closed by a person |
| Bounded reflection | Implemented over a shared harness; used for plan drafting and for unreadable judge replies **only** |
| Planner | Implemented with a deterministic built-in drafter, so `REQ-F-AG-8` holds without a test mode |
| Historical memory | Derived at read time from existing records; never a second store |
| Agreement threshold, minimum votes, ensemble size | **Unset.** Every judgement escalates until a person configures them |
| Judge-agreement gate criteria | Real, and abstain until judgements exist to pair |

## Results

| | |
|---|---|
| Validator | **34 checks, all PASS**, exit 0 |
| Self-test | planted violations, all caught |
| Schema probes | 35 as the runtime role, 14 as superuser |
| Tests | **573 passed**, coverage **93.65%** against an 85% gate |
| Schema | 61 tables, 60 tenant-scoped with ENABLE and FORCE |
| Contract | 36 operations, 96 schemas |
| Regression | Spike Sprint; Phases 4, 5, 6, 7; Phase 1 milestones |
| ADRs | 17 recorded, 0 undecided |
| Dependencies added | **none** — ADR-002 declined the agent framework, and the orchestration is project code |
