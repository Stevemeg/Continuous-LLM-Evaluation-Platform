# Technology Spike Sprint

Two ADRs were held open through Phases 2, 3 and 4 because they turn on properties
that documentation cannot settle. This sprint existed only to close them, and it
ran on infrastructure provisioned for the purpose rather than assumed.

| ADR | Question | Outcome |
|---|---|---|
| [ADR-001](../../adr/ADR-001-durable-execution.md) | durable workflow engine, or task queue with explicit checkpointing? | task queue with explicit checkpointing |
| [ADR-003](../../adr/ADR-003-provider-abstraction.md) | provider-aggregation library, or internal adapters behind a project-owned port? | internal adapters behind the port |

Environment, versions and provisioning: [`environment.md`](environment.md).

## Contents

| File | What it is |
|---|---|
| `spike_durable_execution.py` | S-1. Two candidates, two fault regimes, three zero-conditions |
| `spike_resume_latency.py` | S-1b. Is the resume-latency gap an engine property or a setting? |
| `spike_provider_abstraction.py` | S-2. Three approaches, four questions, three endpoints |
| `common.py`, `crash.py` | shared workload, ledger, and the crash injector |
| `cand_temporal_worker.py`, `cand_arq_worker.py` | the two durable-execution candidates |
| `port.py`, `adapters.py` | the project-owned port and the three provider approaches |
| `stub_provider.py`, `leak_probe.py` | the fault endpoint, and the credential probe with debug logging on |
| `s1-output.txt`, `s1b-output.txt`, `s2-output.txt` | verbatim run output |
| `s1-results.json`, `s1b-results.json`, `s2-results.json` | machine-readable measurements |

## The result that mattered most in S-1

Both candidates passed every randomly-timed worker kill: six trials, zero samples
lost, zero recomputed, zero cost entries double-counted.

**That result was worthless, and the spike was built to prove it.** The window in
which a process death can destroy durability is the few milliseconds between
committing a side effect and telling the engine it happened, against a 120 ms unit
of work. A randomly-timed kill almost never lands there. A second regime crashes
the worker *inside* that window deliberately:

| Candidate | Ledger | Recomputed | Cost double-counted | Cost recorded |
|---|---|---|---|---|
| durable workflow engine | naive | 1 | **1** | 410 (expected 400) |
| durable workflow engine | idempotent | 1 | 0 | 400 |
| task queue + checkpointing | naive | 1 | **1** | 410 (expected 400) |
| task queue + checkpointing | idempotent | 1 | 0 | 400 |

Both engines are at-least-once. Neither provides exactly-once side effects.
`REQ-N-REL-2` is satisfied only by an application-level idempotency key — which
`I-21` and `component-architectures.md` had already required before this ran, and
which the spike showed to be load-bearing rather than defensive.

This fired ADR-001's falsification clause, and the revisit landed on one of the
spike's own measurements: "completed samples recomputed must be zero" is stricter
than `REQ-N-REL-1`, unachievable by any at-least-once system, and was reclassified
from a gate to a cost measurement. The correction costs more than it saves — it
makes idempotency keys mandatory across every externally-visible effect.

## The result that mattered most in S-2

The aggregation library reports an **identical** exception class, status code and
cause for two failure modes that `REQ-N-REL-4` requires to be handled
individually:

```
A: malformed response == outage  (same class, status and cause)
B: none - all four modes carry distinct structured signals
C: malformed response == outage  (same class, status and cause)
```

The malformed case was an HTTP 200; the outage had no HTTP exchange at all. Both
were reported as `InternalServerError`, `status_code = 500`. The only
discriminator is message text, and the text for the malformed body says
`"Connection error."` — which is not what happened.

Separately, with debug logging enabled, the library wrote the API key to stdout.
Putting a project-owned port in front of it fixed neither problem, because both
happen below the port.

## What each spike did to check itself

A spike that only confirms its author's expectation is a spike that has not been
run.

- **S-1's decisive experiment exists to falsify S-1's first result.** Regime A is
  reported, and reported as establishing nothing.
- **The bespoke-code measurement is mechanical.** Lines existing solely for
  durability, resume or idempotency carry a `# BESPOKE` tag and are counted by the
  script, so the figure cannot be adjusted after the answer is known.
- **Resume latency was measured and then deliberately not used.** It is
  timeout-bound, non-monotonic in one candidate, and absent from the decision
  rule. It is recorded so a reader can see it was measured, not skipped.
- **S-2's failure-mode analysis reports signal collisions**, not just a score.
  Approach C scored 3/4 but cannot distinguish two modes at all; its third
  correct answer is an artifact of which colliding mode its fallback happens to
  name. The raw score alone would have been misleading.
- **The credential leak detector was self-tested.** The canary was planted on all
  five inspected surfaces and found on all five. A leak detector that has never
  reported a leak has not been shown to work.
- **S-2 found a live bug in the approach it recommends.** Both hosted credentials
  were exhausted, and the two providers reported that with different HTTP statuses
  — 429 and 401 — for the same `insufficient_quota` condition. The internal
  adapter maps 429 to a retryable rate limit, so against a real exhausted account
  it would retry forever. Recorded rather than quietly fixed.

## Evidence gap

No successful call to a **hosted commercial** provider was made: both credentials
in the environment were out of quota, so no billable token was spent and none
could be. Usage reconciliation against a paid provider, and against a provider
whose usage schema differs from the OpenAI shape, is untested.

It does not affect either decision, and it is not presented as if it might. It is
a verification obligation on the first hosted provider integrated.
