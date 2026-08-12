# Validation Evidence — Phase 11

Phase: **Phase 11 — Dashboards, reports, analytics, alerts**
Milestones: M11.1 through M11.9

## Contents

| File | What it is |
|---|---|
| `check_phase11.py` | Phase validator, 30 checks. `python docs/evidence/phase-11/check_phase11.py .` |
| `selftest_phase11.py` | Plants 40 violations, proves each is caught, and verifies nothing survives |
| `ci_execution.py` | Installs the package from a clean checkout into an isolated environment and runs `clep` |
| `ci-execution-output.txt` / `.json` | Verbatim output of that run |
| `validation-output.txt` | Verbatim output of the validator |
| `selftest-output.txt` | Verbatim output of the self-test |
| `test-output.txt` | Verbatim output of the test suite with coverage |

## What Phase 11 is for

The canonical roadmap gives Phase 11 as dashboards, reports, analytics and
alerts. Two things had to come first, because the Phase 10 review left them
open: the scheduled-execution model had never executed, and the CLI had never
been run as an installed console script.

## The schedule now fires

`REQ-F-10-1` says evaluations execute on a schedule without human initiation,
and the word doing the work is *execute*. A stored schedule and a CRUD surface
over it are not that. Neither is a callback someone invokes by hand.

The trigger is `arq`'s own cron, registered on the worker ADR-001 selected.

    a cadence that matches this UTC minute
      -> the queue's cron enqueues the sweep
      -> the sweep creates a run, with the schedule's trigger_kind
      -> the sweep enqueues the run
      -> the worker executes it through RunExecutor
      -> evaluators run; samples, costs and model latency are persisted
      -> the gate is evaluated against the approved baseline
      -> a release observation records what the platform RECOMMENDS
      -> all of it readable back out of the store

`tests/test_scheduled_execution.py` starts a real `arq.worker.Worker` against the
real broker with the real `sweep_cron()` factory — the same one `WorkerSettings`
uses, at a one-second cadence so a test does not wait a minute — and then watches
PostgreSQL. It calls no sweep, enqueues no job and invokes no callback; `P-27a`
asserts that it does not, by name and by call.

Four things had to exist for that chain to close.

**A schedule could not name its candidates.** The contract's
`EvaluationScheduleRequest` has taken `candidates` since Phase 3 and there was
nowhere to put them, so a schedule was a record of an intention.

**A scheduled run has no caller to hand it examples.** It resolves the dataset
through the suite and reads the record from PostgreSQL; the payload arrives
through a port, because ADR-005 splits record from content and ADR-013's object
store belongs to the deployment phase. A source with no reader refuses rather
than evaluating empty prompts, and a payload that no longer hashes to the digest
the dataset version recorded stops the run — run identity is frozen over it.

**`release_observation` admits only `post_deployment` and `canary`**, so a
schedule that observes a live system has to say so. Without that column
`REQ-F-10-2` was unreachable through the scheduler and the constraint read as an
accident.

**Nothing recorded model-call latency.** The gateway is the sole egress
(ADR-003), so it times the call, and the sample carries it — written at insert,
like truncation, because a resolved sample is immutable (I-18).

### The trigger has an identity

The idempotency key is the schedule and the UTC minute its cadence matched. Two
sweeps in the same minute derive the same key, find the run that already exists,
and enqueue nothing. It is checked *before* the run is created, because
`create_run` returning an identifier cannot distinguish the first firing from the
second.

An over-budget schedule creates no run at all. The estimate comes from the
planner — the same `draft_plan`/`validate` pair a human-reviewed plan goes
through — rather than from a second cost model that would eventually disagree.

## The CLI, as a pipeline actually has it

Every earlier proof ran `main(argv)` inside the development environment with the
working tree on `sys.path`. That hides a missing `[project.scripts]` entry, a
package that only imports because the repository is the working directory, and a
module that resolves through an editable install.

`ci_execution.py` builds the real thing: `git clone` of committed content, a
fresh `venv`, a non-editable `pip install .`, and `clep` resolved on that
environment's `PATH`. The exit code is read from the process; the CI decision is
computed from the exit status and nothing else, because that is the whole
contract.

| Step | Exit | CI |
|---|---|---|
| successful evaluation | 0 | continues |
| blocking evaluation | 1 | stops |
| abstention blocks | 70 | stops |
| malformed identifier | 78 | stops |
| blocking evaluation, re-run | 1 | stops — identical |

**This is a local CI-style environment, not hosted CI.** It says so in the
evidence it writes, and nothing here claims a run on GitHub Actions or any other
service.

## Analytics are derived, never stored

Phase 11 adds thirteen operations and no aggregate table. Every figure — a trend
point, a leaderboard row, a latency quantile, a judge agreement rate — is
computed on read from `run_sample`, `evaluator_outcome`, `sample_cost`,
`consensus_result` and `trajectory_step`.

That is `REQ-F-11-6` made structural. A stored aggregate is a figure whose
provenance is a previous computation; asked *which samples produced this*, it can
only answer *the ones that were there when the job ran*. Every response carries
the runs it was computed from and the observations behind it, because it has just
read them.

`REQ-F-11-7` is why `Completeness` is a type rather than a flag. A mean over a
run that was cancelled halfway is a mean of the part that happened, and a reader
comparing it with last week's is being misled unless something says so. It
travels with the figure into every view, including the executive scorecard —
which is exactly where such a qualification would be dropped for being untidy.

## Three refusals

**A leaderboard requires a benchmark.** Not by convention: `suite_version_id` is
a required argument and raises without one, before any query runs. `REQ-F-11-2`
forbids a global ranking, and an optional benchmark defaulting to every suite is
a global ranking with extra steps.

**Drift invents no threshold.** `REQ-F-10-4` forbids comparing against a single
prior run, so the comparison is against every baseline that has held the scope,
superseded ones included — a superseded baseline is history, not a mistake.
Classification requires a caller-supplied minimum history and tolerance; without
them the verdict is `insufficient_configuration` with a reason, which is the
choice ADR-007 made for gate comparisons rather than the opposite one. What is
always reported is where the value sits relative to the range the history
spanned, because that costs no calibration.

**An alert never acts.** No delivery column, no endpoint, no acknowledgement. The
reasoning that kept an actuation column out of `release_observation` keeps a
webhook target out of `alert_rule`. A firing carries the completeness of its
evidence, and one firing per rule per run is a unique constraint rather than a
caller remembering.

## Grounding is reported as counts, not as a rate

Canonical §12 asks for hallucination, faithfulness, groundedness and citation
trends. Retrieval and citation quality are evaluator scores and appear in the
trend and the leaderboard like any other metric. Hallucination and attribution
are not: a finding is a categorical verdict about one claim, and a stage
attribution says which part of the pipeline a failure belongs to. Neither can be
recovered from a mean, so `getRagAnalytics` reports them apart from the quality
trend.

It reports counts. A "hallucination rate" would imply a denominator the platform
can defend, and the support and contradiction thresholds behind a finding are
configured rather than calibrated — one of this project's open risks. Claims the
platform declined to judge are counted separately from the ones it judged, which
is `REQ-F-08-4` applied to grounding rather than to a gate.

## Defects found by building it

**`create_app` registered its optional sections through a chain of early
returns.** A deployment that supplied an analytics service but no schedule
service silently lost every analytics route. Found by an API test that got a 404
where the contract said 200. Each block now guards on its own service.

**The baseline flag on a quality trend multiplied its own evidence.** A plain
`LEFT JOIN` onto `baseline` duplicated every evaluator outcome once per approved
baseline, so the observation count — the thing `REQ-F-11-6` makes the figure
traceable by — would silently double. The join is aggregated to at most one row
per run.

**A one-second connect timeout made the scheduled-execution test flaky under
load.** It passed alone and failed in the full suite: the run loop is synchronous
`psycopg` inside an async job, so the event loop stops being serviced while a run
executes. The failure only appeared behind ninety seconds of other integration
tests, which is the worst way for a test to be flaky.

**Four self-test plants were not caught by the first version of the validator**,
and all four were substring matching: a constraint renamed to
`uq_alert_event__rule_run_removed` still contains the name a substring search
looks for; `_Worker(` contains `Worker(`; a module can hold the name `draft_plan`
bound to `None`. Three of the four are now word-bounded or identity checks. That
is the **fifth, sixth, seventh and eighth** check lost to string matching across
four phases.

## What the self-test proves

40 planted violations, 40 caught. It is stricter than Phase 10's in two ways.
Restoration removes untracked files as well as reverting modifications — a plant
that created a file would otherwise have survived, which is how the earlier leak
happened. And the restoration is verified rather than assumed: after every case
the tree is compared with `HEAD`, and the run fails if anything survived.

## Results

| | |
|---|---|
| Validator | **30 checks, all PASS**, exit 0 |
| Self-test | **40 planted violations, 40 caught**; restoration verified |
| Tests | **784 passed**, coverage **93.08%** against an 85% gate |
| Schema | 72 tables, 71 tenant-scoped with ENABLE and FORCE |
| Contract | 53 operations, 137 schemas |
| Regression | Phase 10 gate at its own history in an isolated clone, which transitively re-runs the Spike Sprint and Phases 4 to 9; plus the Phase 1 milestone validators 11/14/18 |
| Closure | 17 validators in the repository, 17 reachable — derived, not asserted |
| Traceability | 149 of 150 traced, 1 deferred to Phase 15, 0 untracked |
| ADRs | 18 recorded, 0 undecided |
| Dependencies added | **none** — the cron parser is `re`, the analytics are SQL |

## Deliberately unresolved

Phase 11 reports on judges; it does not calibrate them. Judge accuracy, judge
quality, statistical calibration, agreement thresholds and hallucination
thresholds remain open, and no figure in this phase was tuned to make them look
settled. The executive scorecard states all of it on every rendering, in a
section that is not removable, because a report that presented judge agreement
without saying the threshold behind it is uncalibrated would be inviting a
decision the evidence does not support.

Alert **delivery** — webhooks, email, paging — is absent. `REQ-F-11-9` asks the
product to alert on defined conditions, and this phase delivers that: rules,
evaluation, and an audited firing record. Outbound delivery is an egress
capability with its own threat surface and no requirement asks for it.

The object-store adapter ADR-013 selected does not exist yet. A scheduled run
reads example payloads through a port whose local implementation reads `file://`
references; the S3-compatible adapter belongs to the deployment phase, and until
then a deployment that configures nothing gets a refusal rather than an
evaluation of empty prompts.
