# Validation Evidence — Phase 7

Phase: **Phase 7 — Regression engine, baselines, statistics, quality-gate policy engine**
Milestones: M7.1 through M7.7

## Contents

| File | What it is |
|---|---|
| `check_phase7.py` | Phase validator, 30 checks. `python docs/evidence/phase-7/check_phase7.py .` |
| `selftest_phase7.py` | Plants eleven violations and proves each is caught. Refuses to run on a dirty tree |
| `probe_gate_schema.py` | Twenty constraint probes as the runtime role, nine trigger probes as a superuser |
| `probe-output.txt` | Verbatim output of the probes |
| `validation-output.txt` | Verbatim output of the validator |
| `test-output.txt` | Verbatim output of the test suite with coverage |
| `selftest-output.txt` | Verbatim output of the validator's own self-test |

Run it in an environment where this package and its dev extras are installed
(`python -m pip install -e ".[dev]"`) with the compose stack up, or set
`CLEP_TEST_PYTHON` to such an interpreter.

## What Phase 7 is for

Phase 7 is the phase that decides whether software ships, so the failure that
matters is not a gate that breaks — it is a gate that **looks like it is
working**. A threshold applied in the wrong order, an abstention counted as a
pass, a decision editable after the fact, a statistical parameter that acquired a
default nobody chose: each of those produces release decisions that read as
perfectly reasonable.

ADR-007 decided how to tell whether a metric moved. It did not decide what to do
about it, and the two are different questions with different owners.

## The decision that needed ADR-016

`REQ-F-08-1` requires classification "using absolute and relative thresholds
defined per metric". Read alone that is the fixed-threshold method ADR-007
rejected. Read with ADR-007 it is a second layer, and **nothing in either
requirement says how the two combine** — while every plausible combination gives
a different verdict on the same data.

[ADR-016](../../adr/ADR-016-gate-composition.md) fixes the order and the reasons:

| Order | Rule | Can produce | Why |
|---|---|---|---|
| 1 | Absolute floor | `hard_fail` only | It states where the product may not go **at all**. A baseline below the floor would otherwise let a candidate sit there indefinitely, improving slightly each time and passing every gate |
| 2 | Statistical classification | the criterion's configured action | ADR-007, unchanged |
| 3 | Relative tolerance | `pass`, for a **detected** regression only | A tolerance that could manufacture a regression would be the fixed-threshold method smuggled back in through the policy |

A forgiven regression records `relative_tolerance` as the rule that fired. It
passed **because someone decided that much was acceptable**, not because nothing
was detected, and the evidence says which.

`pass` is not an available mapping for any criterion outcome. A policy that could
map an abstention to a pass would erase the `REQ-F-08-4` distinction exactly
where it costs something; a team that does not care about a metric removes the
criterion, which is visible in the policy version.

## The parameters that stay unset

ADR-007 recorded four values it refused to set. They are arguments without
defaults, supplied by a **gate policy version** where a person chose them and the
choice is versioned alongside the decisions made under it. A criterion with no
precision threshold abstains rather than guessing.

This will look like an over-cautious platform until real data supplies the
values. It is the correct direction to be wrong in, and `P-19` fails if any of
those parameters ever acquires a default.

## The defects execution found

**The effect size reported -2.9e15 where the code claimed "undefined".** It
tested the deviation against exact zero, and float residues around 1e-17 are not
zero, so dividing by numerical dust produced a number that reads as an
extraordinary finding. The threshold is now the resolution the store actually has
— scores are `numeric(18, 9)`, and a spread below the ninth decimal is not a
spread. Found by the test written to confirm the behaviour the code claimed.

**Run-level incomparability ignored the policy.** The engine returned
`not_comparable` directly when the two runs pinned different versions, without
consulting the criterion's `on_not_comparable` action — so a team that had
configured incomparability to block would have received an outcome their CI might
treat as a note. Both paths now go through the same precedence: the run-level
failure decides the *reason*, the policy still decides the *action*.

**A second copy of the audit insert named columns that do not exist.** Every gate
call failed at once, which was lucky: the same divergence in a rarely-taken
branch would have meant a governed action completing with no audit row, and I-35
says an unaudited action must not be possible. There is now one audit writer.

## What the self-test found

Three of the Phase 7 checks passed a planted violation, and all three were
reading source text where the requirement is about behaviour.

| Check | What it did | What the plant did | What it does now |
|---|---|---|---|
| `P-20` | Compared the positions of three strings in the engine | Reversed the threshold order, leaving a later mention of `relative_tolerance` in place | Exercises `_apply_thresholds` over four scenarios |
| `P-21` | Counted `resolution = 'scored'` and required two | Removed the baseline-side guard — the docstring above the query contains the phrase, so two matches remained | Requires both alias-qualified guards by name |
| `P-22` | Looked for two kind names and two headings in the report source | Merged the two tables; both names stayed in the list comprehensions | Renders a report and fails if any section contains both kinds |

A check a comment can satisfy is not a check.

## The regression chain, derived

`P-30` exists because writing this README required tracing the chain by hand.
Phase 7 re-runs the Spike Sprint, Phase 4, Phase 5 and Phase 6 gates and the
three Phase 1 milestone validators; Phases 1, 2 and 3 are covered *through*
Phase 4, which re-runs Phase 3, which re-runs Phase 2, which re-runs Phase 1.

That closure is what makes "regression across every earlier phase" true, and a
hand-traced closure is a claim that stops being true without anyone noticing — a
validator added for a new phase and never wired in, an edge dropped from an old
one. `P-30` derives it from the invocation paths each gate contains and requires
every validator in the repository to be reachable: **13 of 13**. The self-test
plants the obvious break, Phase 4 no longer re-running Phase 3, and it is caught.

## Probing the schema

Twenty probes against a real database, then nine more as a **superuser**. The
first run refused eight writes with `permission denied for table` — which proves
the grants and says nothing about the triggers, and the schema comment claims the
audit-class tables refuse modification "even if a grant were added by mistake".
Under a superuser no grant and no policy applies, so only the trigger can refuse:
9/9 did.

## Tenant isolation

The eight new tables are tenant-scoped, `ENABLE` + `FORCE`, and in the
parametrised negative-test list — 47 tables checked in the live catalogue. A gate
decision is the record of why a release shipped and a policy is one tenant's
statement of what it will accept; neither may be visible to, or writable by,
anyone else.

## Scope, stated rather than implied

| Capability | Status |
|---|---|
| Quality criteria over evaluator scores | Implemented, paired per example per evaluator version |
| Cost and latency criteria | Implemented through the same paired mechanism |
| Latency signal | Evaluator duration — the only per-sample timing the platform records. Model-call latency arrives with the observability phase |
| Judge-agreement and safety criteria | Declarable, and they **abstain with a reason** rather than passing. A gate for a capability that does not exist yet must not report success |
| Multi-candidate runs | Reported as not comparable. A gate decides one release candidate against one approved baseline; pairing across several would multiply every example by the number of configurations |

## Results

| | |
|---|---|
| Validator | **30 checks, all PASS**, exit 0 |
| Self-test | **11 planted violations, 11 caught** (after three checks were strengthened) |
| Schema probes | 20 constraint and trigger probes, plus 9 trigger-only probes as superuser |
| Tests | **310 passed**, coverage **93.7%** against an 85% gate |
| Schema | 48 tables, 47 tenant-scoped with ENABLE and FORCE |
| Contract | 24 operations, 68 schemas |
| Regression | Spike Sprint 26/26; Phase 4 19/19; Phase 5 21/21; Phase 6 27/27; Phase 1 11/14/18 |
| ADRs | 16 recorded, 0 undecided |
| Dependencies added | **none** — the bootstrap is the standard library |
