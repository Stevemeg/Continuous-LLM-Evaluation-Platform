# Validation Evidence — Phase 6

Phase: **Phase 6 — Prompt/Model/System Registry + reproducible experiment model**
Milestones: M6.1 through M6.7

## Contents

| File | What it is |
|---|---|
| `check_phase6.py` | Phase validator, 27 checks. `python docs/evidence/phase-6/check_phase6.py .` |
| `selftest_p25.py` | Proves P-25 by planting the violation it exists to catch. Seconds, no database |
| `validation-output.txt` | Verbatim output of the validator |
| `test-output.txt` | Verbatim output of the test suite with coverage |
| `selftest-output.txt` | Verbatim output of the validator's own self-test |

## What Phase 6 is for

`REQ-F-07-1` requires an immutable run identity naming the dataset version,
prompt or system version, model and provider configuration, evaluator and judge
versions, seeds, environment and timestamps. Phase 5 recorded a digest over a few
identifiers and stored none of the components.

That is not a small gap. **A digest over rows that can still change is a digest
over nothing**, and none of the elements it named were versioned or immutable —
`run_candidate.model_configuration_id` and `prompt_version_id` were bare `uuid`
columns pointing at tables that did not exist. A run could claim a configuration
that had never been registered.

So the registry comes first and the identity model rests on it.

## The defects execution found

Three, all in artifacts that had been written and reviewed.

**The immutability trigger enumerated columns.** The first draft refused changes
to `content_digest`, `version_number` and `published_at` on a published version.
`body` was not in the list. A published prompt's text could be replaced while its
digest stayed the same, and the digest would quietly stop describing the content
— which is worse than having no digest, because the digest is what every
comparability claim is checked against. Probing the trigger against a real
database found it in the first attempt. It now refuses **any** change to a
published row, and to the row's existence. A list of columns is wrong the moment
a column is added; the rule is not.

**A constraint asserted an `iff` where only one direction holds.**
`ck_reproduction_attempt__replay_matches_outcome` said
`(outcome = 'not_reproducible') = (replay_run_id IS NULL)`, which forbade the
ordinary case: an assessment concluding "reproducible" without having executed a
replay. Assessing whether a run *can* be reproduced and actually re-running it
are separate acts. Running the tests refused every honest assessment.

**Erasure was tested against the wrong shape.** The reproduction check looked for
a missing `example_content` row, but the data model represents erasure as
`erased_at` set and `payload_ref` cleared, with the record surviving (I-8). Both
conditions now count, and the fixture seeds real content so a reproduction gap
means something about the data rather than about the fixture.

## What the gate itself learned

`P-7` re-runs the **Phase 5** gate at its own history. That is the first earlier
gate which is itself history-aware and itself executes the test suite, and it
exposed two limits in the clone technique Phase 5 introduced:

| Symptom | Cause | Fix |
|---|---|---|
| Phase 5's own regression checks reported "could not locate the commit on main" | Phase 5's clone deletes *every* ref, so the re-run gate had no `main` to search | `main` is now **reset** to the target commit rather than deleted — history reachable up to it and no further |
| Phase 5's suite reported 136 passed at 65% coverage | The package is installed editable against the working tree, so the clone's tests imported *Phase 6* code | `PYTHONPATH` points at the clone's `src` |

Neither was a regression. Both were a misconfiguration reporting one, which is
the failure mode a regression gate has to be most careful about — it spends its
credibility every time it cries wolf.

## The decision that needed an ADR

[ADR-014](../../adr/ADR-014-run-identity-scope.md) settles what enters the
identity **digest**, which `REQ-F-07-1` does not say.

Environment metadata is **captured and excluded**. If it entered the digest, two
runs of the same prompt, dataset and evaluators on machines with different
interpreter patch versions would have different identities — and `REQ-F-01-3`,
comparing a candidate against a baseline recorded weeks earlier, could never be
satisfied across hosts. Baselines would expire on upgrade. In a platform whose
purpose is comparing against a baseline, that is not conservative; it is broken.

It is captured anyway, and reported on every reproduction attempt as a gap a
reviewer can weigh. `P-19` enforces both halves mechanically, because a decision
recorded only in prose is a decision that drifts.

[ADR-015](../../adr/ADR-015-cache-correctness.md) settles caching. Only
deterministic configurations are eligible, enforced by a trigger: caching a
sampled configuration replaces a draw from a distribution with one fixed draw, so
**the outcome does change and no cache key can fix it**. The cache is therefore
useless for exactly the configurations that cost most. That is the correct trade
and it is stated rather than engineered around.

## Contract changes, made in the required order

Both amended the contract first and the implementation second.

1. **Seven operations added** for the registry and experiments: 13 → 20.
2. **`RunIdentity` cardinality corrected.** It required a single
   `modelConfigurationId`, but a run compares two or more candidates
   (`REQ-F-02-1`, `REQ-F-02-5`), each with its own configuration. Reporting one
   of N as *the* identity is not a small inaccuracy — it is the field a reader
   uses to decide whether two runs are comparable.

Phase 6 also found that Phase 5's implementation **did not satisfy the contract
it already had**: `RunIdentity` declared seven required fields and the API
emitted `digest` alone. The contract was right and the implementation was short.

## Tenant isolation

The twelve new tables are all tenant-scoped, all `ENABLE` + `FORCE`, and all in
the parametrised negative-test list — 36 tables checked in the live catalogue
rather than in the DDL text. The registry is where a tenant's self-hosted
endpoint and proprietary prompts live, so it is the last place isolation may be
assumed.

`P-21` additionally checks that no registry table carries an endpoint, key or
credential column. A registry row is a thing many people can read.

## The canonical document, at finalization

Phase 5 finalization found the canonical `.docx` reachable from one local branch,
`milestone/M1.1-product-definition`, and disclosed it rather than acting on it.
That was the right answer for a phase whose job was to measure; it was not a
resolution. A disclosed hazard is still a hazard, and this one was a single
`git push --all` away from publishing the specification the whole project is
derived from.

The branch was deleted at Phase 6 finalization, after establishing that it had
never been published and that nothing on it was needed:

| Question | Evidence |
|---|---|
| Was it ever pushed? | `git ls-remote --refs origin` lists `refs/heads/main` alone; `.git/logs/refs/remotes/origin/` has only `HEAD` and `main`, so no remote-tracking ref for it ever existed |
| Is the document on published history? | 0 `.docx` blobs reachable from `refs/remotes/origin/main` |
| Would anything be lost? | Its 7 `wip(M1.1)` commits were squashed into `6adfbab` before the first push; the branch tip differed from `6adfbab` only by the document itself and superseded evidence text whose final form is on `main` |
| Is the document itself untouched? | Present, unmodified — SHA-256 `53329e77…33580f` — ignored by `.gitignore:119`, 0 tracked |

**No ref in the repository can now reach a `.docx`.**

Deleting the branch emptied P-25's allowlist, which is the moment a governance
check quietly stops meaning anything: it would keep passing whether or not it
still worked. `selftest_p25.py` runs the shipped P-25 source against three
repositories differing only in what a ref can reach — clean, document on a local
branch, document on a published ref — and gets PASS, FAIL, FAIL. The allowlist
stays in the source with nothing in it, so re-disclosing anything has to be a
deliberate act.

## Results

| | |
|---|---|
| Validator | **27 checks, all PASS**, exit 0 |
| Self-test | **7 planted violations, 7 caught** |
| Tests | **234 passed**, coverage **94.4%** against an 85% gate |
| Schema | 40 tables, 39 tenant-scoped with ENABLE and FORCE |
| Contract | 20 operations, 55 schemas |
| Regression | Spike Sprint 26/26; Phase 4 19/19; Phase 5 21/21; Phase 1 11/14/18 |
| ADRs | 15 recorded, 0 undecided |
| Dependencies added | **none** |
