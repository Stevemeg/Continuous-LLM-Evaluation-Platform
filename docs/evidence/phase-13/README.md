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
| [`validation-output.txt`](validation-output.txt) | The complete gate, run against the finalized tree. |
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

## The three findings worth reading

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
python docs/evidence/phase-13/check_phase13.py .
python docs/evidence/phase-13/selftest_phase13.py .
```

The complete gate takes hours: `P-5` runs Phase 12's gate, which runs Phase 11's,
and so on down the chain.
