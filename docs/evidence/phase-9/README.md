# Validation Evidence — Phase 9

Phase: **Phase 9 — RAG and agent evaluation suites**
Milestones: M9.1 through M9.9

## Contents

| File | What it is |
|---|---|
| `check_phase9.py` | Phase validator, 34 checks. `python docs/evidence/phase-9/check_phase9.py .` |
| `selftest_phase9.py` | Plants 25 violations and proves each is caught. Refuses to run on a dirty tree |
| `real_model_run.py` | Drives the judge layer against three real self-hosted models |
| `real-model-evidence.md` | What was real, what was not, and what remains unvalidated |
| `real-model-output*.txt` / `.json` | Verbatim output of each real-model run |
| `validation-output.txt` | Verbatim output of the validator |
| `selftest-output.txt` | Verbatim output of the self-test |
| `test-output.txt` | Verbatim output of the test suite with coverage |

The validator is **spliced** from the Phase 8 frame rather than copied. The
isolated-clone gate runner, the security sweeps and the reachability closure are
identical between phases by design, and two copies of a gate drift while only
one of them gets reviewed.

## What Phase 9 is for

Retrieval and agents are where a number most easily looks like a measurement. A
groundedness score computed from word overlap. A retrieval hit rate of 1.0
because nothing said what was required. A truncated trajectory scored as a
completed one. A hallucination "score" that cannot say whether the evidence was
absent or contradictory. Each reads as a result and is an artefact of the
arithmetic.

## The line down the middle of REQ-F-03-2

The requirement names six things. They are not the same kind of question, and
the split falls exactly on the `REQ-F-08-6` line.

| Computed — facts about the record | Judged — semantic, and left to the ensemble |
|---|---|
| Retrieval hit rate | Context relevance |
| Required context present | Faithfulness |
| Citation validity | Groundedness |
| Citation coverage | Answer relevance |

The four on the right are **rubrics**, not evaluators. `P-19a` fails if one of
them ever appears as a registered deterministic evaluator, because
"fraction of expected tokens appearing in the context" is not groundedness, and
canonical §25 rejects claiming a metric no executed measurement produced.

## The defect that shaped the input model

The first `retrieval_hit_rate` computed over the passages that came **back** —
and scored 1.0 on every sample, always. A required passage the retriever missed
is absent from the retriever's own output, so a label carried there can never
express the case that matters.

Required context is therefore a property of the **example**. That one move is
what makes `REQ-F-03-6` attribution possible at all: without it, a retriever
that missed the evidence and a generator that ignored it are indistinguishable,
and the honest answer is `not_attributable` — which the analysis gives, often.

## ADR-018, and why the judge's vocabulary stayed narrow

`REQ-F-03-3` requires **unsupported** and **contradicted** to be distinguished.
The obvious implementation asks a judge for a category — and widens the reply
parse, which is one of the two Phase 8 defences that hold without the model
cooperating.

So a claim is judged twice, on orthogonal bounded questions. They are not
complements: a silent passage scores low on **both**, which is exactly the
unsupported case and which a single signed score cannot represent. Contradiction
outranks support, because denial says the answer is *wrong* rather than merely
unevidenced.

## Real-model validation

Carried from the Phase 8 review as a Phase 9 priority. Three self-hosted
`llama.cpp` servers, one model each, no credentials, nothing billed. Full detail
in [`real-model-evidence.md`](real-model-evidence.md); the short version:

| Element | Result |
|---|---|
| Invocation, parse, vote, consensus, disagreement, escalation, agreement, regeneration | **all exercised against real models** |
| Replies nobody designed | a bare `0.0`; a template placeholder `<reason>`; and `SCORE: 0.0\nABSTAIN: …`, which is `reply-multi-line` from the adversarial corpus arriving with no adversary involved |
| Regeneration | fired 4 times, terminated on `no_progress` |
| Hosted commercial providers | **not exercised** — no credential with quota, and none invented |

**The finding that matters: the machinery was correct and the judges were
useless.** One model scored "Sydney is the capital of Australia" at 1.0 against
a passage saying Canberra. The single agreement in the run was three judges
agreeing a passage refuting flat-Earth supports the answer "Yes". Five samples
escalated to a human. That is what ADR-017's unset threshold and default
escalation are for, demonstrated rather than argued.

## What the gates found in themselves

Three separate times, and all the same class:

| Where | What |
|---|---|
| `P-19e` | Looked for `trajectory_truncated` in `run_sample`'s `CREATE TABLE` body. It arrives by `ALTER`, because file 05 is sealed by SHA-256 |
| `P-19f` | An abstention loop passed one keyword twice |
| Self-test | Two plants defined an evaluator class without registering it. Registration is an explicit tuple, so the registry never saw them: the checks were right and the plants were inert |

Also, the traceability generator failed the run, which is what it is for: seven
requirements were still registered as deferred **to Phase 9** while Phase 9 was
delivering them. Six deferrals remain, owned by Phases 10, 11 and 15.

## Results

| | |
|---|---|
| Validator | **34 checks, all PASS**, exit 0 |
| Self-test | **25 planted violations, 25 caught** (after two inert plants were made real) |
| Tests | **629 passed**, coverage **93.25%** against an 85% gate |
| Schema | 67 tables, 66 tenant-scoped with ENABLE and FORCE |
| Contract | 37 operations, 103 schemas |
| Regression | Spike 26/26 · Phase 4 19/19 · Phase 5 21/21 · Phase 6 27/27 · Phase 7 30/30 · Phase 8 34/34 · Phase 1 11/14/18 |
| Traceability | 144 of 150 traced, 6 deferred, 0 untracked |
| ADRs | 18 recorded, 0 undecided |
| Dependencies added | **none** — the real-model validation runs against a container, not a library |
