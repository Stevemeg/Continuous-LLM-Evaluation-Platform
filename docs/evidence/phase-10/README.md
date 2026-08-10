# Validation Evidence — Phase 10

Phase: **Phase 10 — CI/CD CLI and API, release gates, and the end-to-end path**
Milestones: M10.1 through M10.7

## Contents

| File | What it is |
|---|---|
| `check_phase10.py` | Phase validator, 28 checks. `python docs/evidence/phase-10/check_phase10.py .` |
| `selftest_phase10.py` | Plants 21 violations and proves each is caught |
| `real_end_to_end.py` | One complete evaluation against three real self-hosted models |
| `real-end-to-end-output.txt` / `.json` | Verbatim output of that run |
| `validation-output.txt` | Verbatim output of the validator |
| `selftest-output.txt` | Verbatim output of the self-test |
| `test-output.txt` | Verbatim output of the test suite with coverage |

## What Phase 10 is for

Two things the Phase 9 review carried forward, and the interface a pipeline
actually reads.

The carried risks were that the RAG and agent evaluators had never been driven
through the platform's own execution path, and that the real-model judge
experiment wrote files rather than rows. Both are closed. The new surface is a
CLI, and its whole contract with a CI job is one integer.

## The exit code is a decision

A pipeline has one lever. The mapping from gate outcome to exit code is
therefore a decision, made once, in `src/clep/cli/exit_codes.py`.

| Outcome | Code | Blocks |
|---|---|---|
| `pass`, `warning`, `exception_applied` | 0 | no |
| `hard_fail` | 1 | yes |
| `insufficient_evidence` | 70 | **yes** |
| `not_comparable` | 71 | yes |
| `approval_required` | 75 | yes |
| anything unrecognised | 78 | yes |

**An abstention blocks.** A gate that exits zero when it measured nothing is
green within a week and unread within two, and the distinction `REQ-F-08-4`
fought to keep would have been converted into a pass by a shell script. There
is deliberately no flag to override it: a team that wants to proceed records a
policy exception, which is audited, expires and names who decided.

`exception_applied` exits zero — blocking would defeat the exception a human
already signed — and the CLI says an exception carried the build rather than
letting it look like a pass on merit. The check that every contract outcome is
mapped is what found that one missing.

## The end-to-end path, twice

`tests/test_end_to_end.py` runs it deterministically, stubbing only the HTTP
transport beneath the provider adapter; everything above that, including the
parse that rejects an unreadable reply, is production code.
`real_end_to_end.py` runs the same path against three real `llama.cpp` models
with nothing stubbed at all.

    examples with retrieval and a trajectory
      -> RunExecutor, the loop the worker drives
      -> deterministic RAG and agent evaluators
      -> a judge ensemble through JudgePanel
      -> judgements, votes, verdicts and escalations in PostgreSQL
      -> an approved baseline and a published gate policy
      -> a gate decision, persisted
      -> read back, with a matching evidence digest
      -> both report representations

## Three defects, all found by running it

**The store refused an UPDATE on `run_sample`.** `record_trajectory` tried to
patch truncation in afterwards; the runtime role has no UPDATE grant, because a
resolved sample is immutable (I-18). The refusal was right, and the fix is
better: whether a trajectory was cut is known when the sample is written.

**Nine evaluators shared one version id**, so the gate's lookup by metric name
matched every evaluator's outcomes at once and compared a mixture. A metric has
to resolve to exactly one evaluator or it is not that metric.

**The evaluators were never bound to the suite version**, and the gate reported
the metric as one the suite does not produce. That is the platform being right:
it resolves a metric through the run's own suite rather than through anything
the caller supplies, because a gate that let the caller name the evaluator
would let it choose which measurement to be judged on.

## What the gates found in themselves

`P-19a` reported the run loop judging a failed candidate. It does not — the
check read the first four hundred characters of the method, which the docstring
filled.

`P-19b` searched for `if vote.is_scoring` and missed a plant that wrote `if not
vote.is_scoring`. **That is the fourth check across three phases lost to string
matching**, so it now runs a panel over one judge that answers and one that does
not, and requires both judgements to be written.

## REQ-F-10-3, enforced by what cannot be expressed

The schema has no column naming an action the platform would take: no target
endpoint, no applied flag, no rollback timestamp. A schema able to record having
changed a production system is a schema that expects to. The CLI has three
subcommands, all read-only, and a test asserts the list.

## Results

| | |
|---|---|
| Validator | **28 checks, all PASS**, exit 0 |
| Self-test | **21 planted violations, 21 caught** |
| Tests | **652 passed**, coverage **92.58%** against an 85% gate |
| Schema | 69 tables, 68 tenant-scoped with ENABLE and FORCE |
| Contract | 40 operations, 111 schemas |
| Regression | Phase 9 gate 34/34 at its own history in an isolated clone of 34 commits, which transitively re-runs the Spike Sprint and Phases 4 to 8; plus the Phase 1 milestone validators 11/14/18 |
| Closure | 16 validators in the repository, 16 reachable — derived, not asserted |
| Traceability | 145 of 150 traced, 5 deferred, 0 untracked |
| ADRs | 18 recorded, 0 undecided |
| Dependencies added | **none** — the CLI is argparse |

The check count fell from the 34 the earlier phases carried, and the reason is
the scaling fix below: six gate invocations that duplicated the chain became
one. Fewer checks, identical coverage, and `P-26` is what proves the second
half of that sentence.
