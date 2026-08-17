# Service-level objectives, and the three that remain unset

`REQ-N-REL-5` requires platform availability to be defined by an explicit
service-level objective, and canonical §23 places that definition in this phase.
[ADR-023](../../adr/ADR-023-slo-derivation.md) governs how a target may come to
exist.

Five candidate indicators entered this phase, from
[`observability-strategy.md` §5](../../architecture/observability-strategy.md).
**Two leave it with an objective. Three leave it with `TARGET NOT YET SET` and a
named blocker.** That distribution is the intended outcome of ADR-023 rule 3, not
a shortfall against it: the rule exists so that the honest result is a completed
piece of work rather than an admission, and so that the alternative — writing
99.9% because it is always available and nobody checks — has somewhere else to
go.

Every number below was produced by
[`tests/test_slo_measurement.py`](../../../tests/test_slo_measurement.py),
executed against a real PostgreSQL. Raw output:
[`slo-measurement.txt`](slo-measurement.txt).

## Summary

| Indicator | Objective | Derived from |
|---|---|---|
| Gate latency (3-sample band) | **p95 ≤ 97.6 ms** | Measurement — the maximum observed across 20 executed evaluations |
| Verdict integrity | **1.000 (100%)** | `REQ-X-10`, under ADR-023 rule 8 — the requirement, not the measurement |
| Gate availability | `TARGET NOT YET SET` | Blocked — see below |
| Run completion | `TARGET NOT YET SET` | Blocked — see below |
| Cost attribution accuracy | `TARGET NOT YET SET` | Blocked — see below |

## Gate latency — a target, derived

| Field | Value |
|---|---|
| Definition | Wall-clock, invocation to reported decision |
| Band | 3 samples per candidate run |
| Observations | 20 executed gate evaluations |
| p50 | 70.0 ms |
| p95 | 97.1 ms |
| Maximum | 97.6 ms |
| Statistic | Nearest-rank percentile |
| Environment | Windows 11, Python 3.11.0, PostgreSQL in the project's `docker-compose.yml` |
| **Objective** | **p95 ≤ 97.6 ms** |

**Why this is measurable when `REQ-N-PERF-1`'s end-to-end target is not.** By the
time a gate runs, the candidate run has already terminated. The gate reads stored
samples and computes statistics; no provider call happens inside it. So this
measures the platform's own contribution and nothing else — which is precisely
the quantity ADR-023 rule 5 requires be reported separately from provider time,
and it is measurable here without a hosted provider.

**Why the bound is the observed maximum, and not a round number with headroom.**
Every candidate round number — 100 ms, 250 ms — would be chosen rather than
derived, which is the invented figure canonical §20 and §24 forbid. The observed
maximum is an observation that actually happened, so it is a bound the platform
has demonstrably met on every one of the twenty evaluations. It is derived in the
strict sense: no judgement was applied to it.

**What this target does not cover.** Bands other than 3 samples are unmeasured;
no suite of another size was executed in this phase, and a latency target
extrapolated across suite sizes would be an invented figure with a measurement
attached. Larger bands are `TARGET NOT YET SET` for that reason.

**Why it is not enforced as a build gate, with the evidence for that decision.**
The derivation was run twice on the same machine, minutes apart, with no code
change between them. The first run observed p50 53.3 ms, p95 64.7 ms, max
68.4 ms; the committed run observed p50 70.0 ms, p95 97.1 ms, max 97.6 ms — a
p95 that moved by roughly 50% for reasons entirely outside the platform.

That variance is the argument. A validator asserting p95 ≤ 97.6 ms would fail on
a busier machine for a reason that has nothing to do with the gate, and ADR-023
rule 7 forbids the repair everybody reaches for, which is editing the target
until it passes. So the validator asserts that the measurement **ran** and that
this document cites its output; the number stands as a published baseline for the
named environment, and re-measurement is required in any other one.

The committed run is the one quoted above. The earlier run is reported here
rather than discarded, because a baseline whose run-to-run spread is unstated
invites exactly the false confidence this whole document exists to prevent.

## Verdict integrity — an objective, from the requirement

| Field | Value |
|---|---|
| Definition | Proportion of gate outcomes where platform failure was reported as platform failure rather than as a quality verdict |
| **Objective** | **1.000**, under ADR-023 rule 8 |
| Derived from | `REQ-X-10` |
| Observed | 1.000 |
| Observations | 1 gate decision |
| Detail | 0 of 1 decision returned a quality verdict over a candidate run carrying platform-caused incompleteness |

This is the indicator the observability strategy calls "the unusual one and the
most important", and it is the one place in this document where a target is not
empirical. Rule 8 governs it: `REQ-X-10` makes distinguishability a correctness
requirement, so every value below 1.000 is a published statement that the
platform will sometimes report its own failure as a verdict about somebody else's
code. That is not a service level anybody would choose; it is a defect rate.

The measurement therefore establishes *conformance*, not the target. It is
computed in [`src/clep/analytics/slo.py`](../../../src/clep/analytics/slo.py) and
is checkable in SQL because the record carries what it needs: a decision violates
integrity when it returned a quality verdict over a candidate run whose evidence
was incomplete for a **platform** reason. `evaluator_error` is a platform reason,
because the evaluator runs inside our boundary; the five provider failure modes
are not, and `budget_exhausted` and `cancelled` are the caller's own decisions.

**The observation count is one.** That is honest and it is weak: a single
conforming decision does not establish that the property holds under load, only
that the check runs and that this decision conformed. The objective stands
regardless, because it comes from the requirement rather than from the sample.

## Gate availability — `TARGET NOT YET SET`

**Blocker.** Availability is a proportion over production traffic and elapsed
time. A test harness that executes *N* gate evaluations and observes *N*
successes establishes that the gate works, not that it is available 99.9% of the
time — the two differ by every failure mode a test does not induce, and by the
duration over which none of them occurred.

**Evidence.** The gate was executed 20 times during the latency derivation with
no platform failure. That is recorded as what it is: a successful workload, not
an availability measurement.

**What would unblock it.** A deployment, real traffic, and elapsed time — Phase 14
and beyond.

## Run completion — `TARGET NOT YET SET`

**Blocker.** Same shape. The indicator is measurable and was measured — 2 of 2
terminal runs carried no platform-caused incompleteness, giving an observed
1.000 — but a proportion over a handful of runs in a test harness cannot
distinguish 99% from 99.99%, and publishing either would be an invented figure
wearing a measurement's clothes.

**What would unblock it.** The same three things.

## Cost attribution accuracy — `TARGET NOT YET SET`

**Blocker.** Agreement between attributed and provider-reported cost requires a
hosted provider that issues a billing record. Every run in this repository
executes against deterministic local adapters, so no provider-reported figure
exists, and no credential in this project could obtain one. **No hosted-provider
validation was performed and none is claimed.**

**Evidence.** What *is* verified is recomputation: every `sample_cost` row's
attributed amount is recomputed from the token counts the provider reported and
the declared price, and required to agree — with a test that recomputes using a
deliberately wrong price and requires disagreement, so the reconciliation is not
comparing a number with itself. See
[`tests/test_cost_reconciliation.py`](../../../tests/test_cost_reconciliation.py).

`provider_reported_total` is `None` rather than `0`. A zero would enter the
comparison and make attributed cost appear wrong by exactly its own value.

**What would unblock it.** A hosted provider credential and a billing record to
reconcile against.

## What was deliberately not done

- No target was rounded, padded, or given headroom. Headroom is a judgement, and
  a judgement applied to a measurement produces a number that is neither.
- No indicator was retired as "not applicable". All five are applicable; three
  are unmeasured, and `TARGET NOT YET SET` keeps them owed in a way that "not
  applicable" would not.
- No target was set for a band, environment or workload other than the one
  measured.
