# Phase 13 — Production observability, SLOs, cost/latency telemetry

Canonical §23 names this phase; canonical §14 governs it;
[ADR-009](../../adr/ADR-009-observability-core.md) had already decided its
architecture and was implemented here, not amended.

The phase began with a complete design and an empty implementation. `src/clep`
contained no `logging` import, no metrics client and no tracing library, and
carried a **false correlation**: three columns named `correlation_id` holding
three unrelated things, and an `x-correlation-id` header read in exactly one
place — the HTTP error handler, where it was echoed back to the caller and
correlated to nothing the platform had recorded.

## What is here

| Artifact | What it is |
|---|---|
| [`check_phase13.py`](check_phase13.py) | The gate. 30 checks. |
| [`selftest_phase13.py`](selftest_phase13.py) | 23 plants, each a real behavioural violation. |
| [`prove_workspace_cleanup.py`](prove_workspace_cleanup.py) | The validator-infrastructure leak, reproduced and fixed. |
| [`prove_adapter_excluded.py`](prove_adapter_excluded.py) | A fresh environment with no telemetry, built and driven. |
| [`slo-targets.md`](slo-targets.md) | Two objectives, three named blockers. |
| [`validation-output.txt`](validation-output.txt) | The complete gate at `f7f00d4`: 30/30, exit 0. |
| [`finalization-output.txt`](finalization-output.txt) | The complete gate again at `ceff51e`, the finalized tree: 30/30, exit 0. |
| [`selftest-output.txt`](selftest-output.txt) | 23/23 plants caught. |
| [`workspace_cleanup.txt`](workspace_cleanup.txt) | 952 workspaces reclaimed, 362.36 MB, 0 residue. |
| [`adapter-excluded.txt`](adapter-excluded.txt) | 7/7, `REQ-N-OBS-3` established in a clean environment. |
| [`slo-measurement.txt`](slo-measurement.txt) | The raw output every published figure comes from. |

## What Phase 13 delivered

| Requirement | Status | Established by |
|---|---|---|
| `REQ-N-OBS-1` | **Partial** — 7 of 8 hops | One identifier recovered from the store after the scope closed. The **artifact** hop has no writer: `D-7`. |
| `REQ-N-OBS-2` | **Implemented** | All nine classes counted from what a driven platform emitted, not from the catalogue. |
| `REQ-N-OBS-3` | **Implemented** | A fresh environment, `pip install .`, no telemetry distribution, everything works. |
| `REQ-N-OBS-4` | **Implemented** | 100 correlations render as one series; the refusal is in the core, so it holds in the build with no backend. |
| `REQ-N-REL-5` | **Implemented** | Two objectives derived, three `TARGET NOT YET SET` with named blockers. |
| `REQ-N-COST-1` | **Partial** | Recomputation from recorded usage verified; agreement with a provider invoice is blocked. |
| `REQ-N-COST-2` | **Validated** | Budget exhaustion reached by fault injection and recorded as a terminal outcome. |
| `REQ-N-COST-3` | **Implemented** | Estimated from the tenant's own history, or declined with a stated reason. |
| `REQ-N-SEC-5`, `REQ-N-PRIV-2` | **Extended to logs** | Phase 12's redactor reused; 32 adversarial cases. |
| `SR-7` | **Closed** | Authentication failures reach platform metrics and still do not reach the audit store. |

## The four findings worth reading

### 1. `REQ-N-OBS-1` cannot be fully implemented in this phase

`clep.artifact` has **no writer anywhere in `src/clep`** — it is read in
`security/rbac.py` and `security/erasure.py` and written nowhere. It cannot get
one here either: `ck_artifact__erasure_consistent` requires a non-erased artifact
to carry a `payload_ref`, which is an object-store reference, and the
object-store adapter is `D-3` — Phase 14's.

So seven hops are demonstrated and the eighth is **reported as absent**, carrying
its reason in the chain result itself rather than being omitted so that a
complete-looking answer implies a complete chain. The test asserts the absence,
so the day a writer arrives the test fails and somebody reads `D-7`.

**`REQ-N-OBS-1` is not claimed as fully implemented.**

### 2. Three of five SLO targets are unset, and that is the rule working

[ADR-023](../../adr/ADR-023-slo-derivation.md) rule 3 gives the unmeasurable case
a required structure — a named blocker and the evidence of what was attempted —
so that the honest outcome is cheaper to produce than the fabricated one. Gate
availability, run completion and cost attribution accuracy all leave this phase
unset. Two of them were *measured*, at 1.000, and still not promoted: a
proportion over a handful of runs cannot distinguish 99% from 99.99%.

The gate-latency objective is the observed maximum across 20 executed
evaluations, not a round number with headroom, because headroom is a judgement
and a judgement applied to a measurement produces a number that is neither. It is
published as a baseline and deliberately **not** enforced as a build gate: the
derivation ran twice on the same machine minutes apart, with no code change, and
p95 moved from 64.7 ms to 97.1 ms. Both runs are reported.

### 3. The validator's own workspace hygiene was broken, and silently

936 abandoned gate workspaces, 6,899 files, 362.35 MB. Every leaked file a git
packfile, every one read-only, and `shutil.rmtree` raising `PermissionError` into
`ignore_errors=True`. The leak had previously been attributed to open file
handles; `W-1` reproduces it with no handle open anywhere.

Two fixes, because it was two failures: a removal that clears the attribute and
**reports** what it cannot remove, and containment through `TMPDIR`/`TEMP`/`TMP`
that reaches the earlier phases' gates — which leak too and cannot usefully be
edited, since each runs from its own committed tree. Proven two process levels
down. See [`../tooling/README.md`](../tooling/README.md).

### 4. Two runs failed, on one test that was asking the clock in two places

`29/30`, `OBSERVED_EXIT=1`, and the single failure was `P-1` — one test out of
1065, `test_a_cron_trigger_produces_a_run_a_gate_decision_and_an_observation`,
carried from Phase 11.

It asserted `len(observations) == 1`. The schedule it creates has cadence
`* * * * *`, so a second minute is a second legitimate trigger and a second
observation, and whether the test crossed a minute boundary was decided by how
long the tests before it happened to take. Run alone it passed 5/5; run in the
suite it took 11.19 s and failed 3 of 6 suite runs, on a **byte-identical
product tree**. `P-5` passed 32/32 in the same run, so nothing underneath it was
moving. The scheduler was correct every time it was accused.

It was fixed rather than re-run, which is the finding: a gate that is re-run until
the timing cooperates has been passed by chance, and the same coin would have
been tossed again at Phase 14, at Phase 15 and at final closure. What the count
was reaching for — fire at most once per period — is `ALREADY_FIRED`, and
`test_schedules.py` already asserts it against an injected moment. That test also
asserts that the *next* minute fires again, which is precisely the behaviour the
failing assertion contradicted. The property was never this test's to make.

The fix is proven against a boundary rather than around one: 17/17 runs in
isolation, 9 of them crossing a minute boundary deliberately, the crossing swept
through setup, through the worker's cron window and through the assertions at
five offsets. No production code was touched. Phase 11's gate is unaffected
either way — it executes from its own committed tree.

That fix was incomplete, and the record should say so plainly. The closure run
recorded in [`validation-output.txt`](validation-output.txt) passed 30/30 at
`f7f00d4`, and the **finalization** run against that same tree then failed `P-1`
on the same test again:

    AssertionError: assert '01M0AG7EXNSSVYH19FWBCH140E'
                        == '01M0AG78QY159VC2XB8MKTBC4N'

`assert schedule.last_run_id == observation.run_id`, one line below the count
that had just been removed. It was left in place on the reasoning that
`list_for_project` returns newest-first, so the newest observation must belong to
the newest run. That reasoning was wrong. `record_run` is called by the *sweep*,
at run **creation**; an observation is written at the end of **execution**. So
`last_run_id` names the most recently created run while the newest observation
names the most recently finished one, and the moment a second period fires the
second run exists, is still executing, has no observation, and is what the
schedule points at. Two different runs, one cadence period apart, with nothing
wrong anywhere — the same wall clock, wearing a different assertion.

The line's purpose is that the schedule points at its own work rather than at
nothing or at another schedule's, so that is what it now asserts: the schedule
records a run, that run exists, and its idempotency key carries this schedule's
identifier. Proven the same way — the whole file 12/12, four tests per run, a
minute boundary crossed in **every** run, 48 test executions with none of them
lucky — plus the full suite three times at 1065 passed, exit 0, in 9m07s, 9m46s
and 10m36s. The suite is slower than it was that morning as the test database
grows, which widens the window this class of defect lives in rather than
narrowing it. [`finalization-output.txt`](finalization-output.txt) is the run
against the repaired tree.

Two things a reviewer should carry forward from this. First, a green closure run
is evidence about one execution, not a proof that a timing assumption is gone;
this one survived a 30/30 gate. Second, the sibling test
`test_a_scheduled_run_evaluates_the_alert_rules_nobody_was_watching` asserts
`len(events) == 1` with the identical shape — `events_for_project` is
newest-first and one event is written per run. It held 12/12 under forced
boundaries and was left alone, because repairing it is outside the authorization
that produced these two fixes. It is flagged here so the decision is the
reviewer's rather than the next failing run's.

## What this phase deliberately did not do

- **Judge calibration.** Metric class 6 makes agreement, disagreement and
  escalation *visible*. Instrumenting a property is not establishing it: judge
  calibration and every agreement, hallucination and statistical threshold remain
  uncalibrated and unassigned. The panel says so where the telemetry is emitted.
- **`D-4`.** The gate's latency criterion is untouched. This phase observes how
  long a decision took; it changes nothing about what any criterion measures.
- **`D-3`, `D-5`, `D-6`, `SR-2`, `SR-6`** — all still open, all still Phase 14's.
- **Hosted-provider validation.** None was performed and none is claimed.
- **A proprietary vendor adapter.** ADR-009 rule 6 gates adoption on licence
  review, and M1.2's finding `S-08` showed that is not a formality. None adopted.
- **The final README** — Phase 15.

## Validation

**30 checks.** Behavioural wherever the property is executable. Three are static
inspection and say why: the absence of a vendor import is a property of the
source text and executing it would prove less; `pyproject.toml` declaring
telemetry as an extra is a property of the file; and two copies of one vocabulary
agreeing is a set comparison against the schema and the domain modules, which is
what stops a member added on one side and not the other from silently refusing a
legal value.

**23 plants, 23 caught.** Restoration verified against HEAD after every one.

The self-test found four checks that could not fail, and they are worth naming
because three were defects rather than weak plants:

| Check | What it could not see |
|---|---|
| `P-12` | A telemetry package added to the runtime dependencies. The regex stopped at the first `]`, and `psycopg[binary]` carries one, so the block ended four entries early. |
| `P-22` | A blocked SLO target being promoted. It tested presence, then counted — and the file legitimately carries a fourth marker, so three remained after a promotion. It now names the three indicators that must stay blocked. |
| `P-7` | The worker's only queue-time emitter being deleted. The **test** was emitting that class itself; a test that supplies the thing it checks for cannot fail. |
| `P-2` | Nothing — the plant was wrong. A table without `organization_id` is a global table, which the rules permit. Replaced with an index that breaks the naming rule. |

Four checks carry no plant, each because it costs minutes to run: `P-1` (the full
suite), `P-5` (the nested Phase 12 gate), `P-10` (the fresh-environment build)
and `P-30` (the network advisory scan). Each is executed once by the complete
gate. Three more — `P-3`, `P-25`, `P-26` — have no plant and are noted by the
self-test in its own output rather than left unremarked.

## Regression closure

Derived, not trusted, by the two mechanisms Phase 12 established.

`P-29` builds the validator graph by traversing every `docs/evidence/**/check_*.py`
in the repository and computing reachability from this gate, so a validator that
exists and is cited by nobody is *detected* rather than assumed absent.

`P-5` re-runs the Phase 12 gate against its own tree, in an isolated clone pruned
to its reachable commits, which closes the chain transitively rather than by
re-running every gate against the present. It now also contains that gate's
temporary directory and asserts the system temporary directory gained nothing —
so a closure run no longer multiplies the leak it just reclaimed.

## Reproducing

```
docker compose up -d
export PYTHONPATH=$PWD/src          # see below; on Windows, set PYTHONPATH
python docs/evidence/phase-13/check_phase13.py .
python docs/evidence/phase-13/selftest_phase13.py .
```

`PYTHONPATH` is not optional and not a convenience. If the project was installed
with `pip install -e .` from a different checkout — which is what happens the
moment the work moves to a second working tree — then `clep` resolves to *that*
checkout, and the gate silently validates the wrong source. It does not
necessarily fail: a subset of checks that happen not to touch the newer modules
will pass, against code that is not the code under review. The run header records
the resolved `PYTHONPATH` for exactly this reason, so a reader can tell which
tree an evidence file describes.

The complete gate takes hours: `P-5` runs Phase 12's gate, which runs Phase 11's,
and so on down the chain. The closure run recorded here took 5979.2 s and the
finalization run 5451.0 s.
