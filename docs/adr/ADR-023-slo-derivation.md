# ADR-023 — Service-level objectives are derived from measurement, or they are not set

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M13.5 |
| Phase | Phase 13 — Production observability, SLOs, cost/latency telemetry |
| Canonical basis | §14, §20, §23, §24 |
| Requirements | `REQ-N-REL-5`, `REQ-N-PERF-1`…`REQ-N-PERF-4`, `REQ-N-COST-1`, `REQ-X-10` |
| Implements | [`../architecture/observability-strategy.md`](../architecture/observability-strategy.md) §5 |

## Context

`REQ-N-REL-5` requires platform availability to be defined by an explicit
service-level objective, and canonical §23 places that definition in this phase.
The observability strategy already fixed *what* will be measured: five candidate
service-level indicators, every one carrying `TARGET NOT YET SET`.

Setting those five numbers is the deliverable, and it is the single place in this
phase where the easiest available action is also a governance failure. Canonical
§20 and §24 forbid the invented figure. An SLO target is the most invitable
figure in the project: 99.9% is *always* available, it is defensible-sounding, it
survives review because everyone has seen it before, and nothing in the artifact
records that it was chosen rather than measured. A month later it is quoted as a
property of the platform.

This ADR exists because "do not invent numbers" is not, on its own, an
enforceable rule. It needs a definition of what a derived target is, and a
defined thing to do when derivation is impossible — because for at least one of
the five indicators, derivation *is* impossible in this phase, and a rule with no
honest exit produces a violation rather than a blocker.

## Decision

1. **A target exists only if an executed measurement produced it.** The raw
   output of that measurement is committed under `docs/evidence/phase-13/`, and
   the published target cites it. A target whose evidence file does not exist is
   a defect, not a rounding.

2. **A target is published together with its derivation method**, and the method
   names: the measurement script, the number of observations, the statistic
   (which percentile, which window), and the workload the observations were taken
   against. A number without its method is not a target; it is a number.

3. **Where derivation is not possible, the indicator keeps `TARGET NOT YET SET`
   and gains two things it did not have: a named blocker, and the evidence
   showing what was attempted.** The blocker names the specific missing
   capability, not a generality. "Requires hosted-provider execution, which no
   credential in this project can perform" is a blocker; "insufficient data" is
   not.

4. **A measurement taken against a workload that does not represent the
   requirement's stated condition is recorded, and does not promote a target.**
   `REQ-N-PERF-1` asks for latency low enough to stay on the pull-request path,
   at realistic suite sizes, against real providers. A measurement against
   deterministic local stubs is a genuine measurement of the platform's own
   contribution and an invalid basis for that target. It is published as what it
   is.

5. **Where an observation contains both the platform's contribution and an
   external provider's, the two are reported separately and never as one
   blended figure.** A single latency number that silently includes provider time
   attributes somebody else's outage to this platform, and — in the other
   direction — hides this platform's cost behind a fast provider.

6. **Indicators are computed from the same durable records the product reports
   from** — runs, samples, evaluator invocations, gate decisions, audit events —
   and not from a parallel telemetry store. Trace backends have short retention
   and are allowed to drop data under load; the decision record is the thing an
   auditor reads months later (`REQ-N-COMP-1`, ADR-011). An availability figure
   that disagrees with the audit trail is worse than no figure.

7. **A target is never adjusted to make a check pass.** Changing a published
   target requires a new measurement and is a change proposal, not a fix. A
   validator that fails against an SLO has found either a regression or a wrong
   target, and which one it is cannot be decided by editing the number.

## Rationale

Rule 3 is the one that makes the rest survive contact with the deadline.

A rule that says only "derive every target" has no defined behaviour when
derivation is impossible, and the undefined case is exactly where the pressure
is. The person holding an unmeasurable indicator and a phase exit criterion will
produce a number, and the artifact will not record which kind of number it is.
Giving `TARGET NOT YET SET` a required structure — blocker, evidence, attempt —
makes the honest outcome a *completed* piece of work rather than an admission of
one. It is cheaper to do correctly than to fake, which is the only durable way to
enforce a rule of this kind.

Rule 5 is not pedantry; it is `REQ-X-10` applied to a number. The product's
central promise is that platform failure is distinguishable from everything else.
An SLI that blends this platform's latency with a provider's abandons that
distinction in the one artifact most likely to be quoted at an operator during an
incident.

Rule 6 has a cost — computing indicators from durable records is slower than
scraping a metrics backend — and it is paid deliberately. The alternative
produces two sources of truth about whether the platform kept its promise, and
the faster one is the one with permission to lose data.

Rule 7 closes the loop that would otherwise make all of this decorative. Any SLO
regime where the target is editable by whoever is failing it measures nothing.

## Consequences

- At least one of the five indicators is expected to leave this phase with
  `TARGET NOT YET SET`. Cost attribution accuracy is defined as agreement between
  attributed and provider-reported cost, and no provider-reported cost is
  obtainable here. That is a phase outcome, not a phase failure, and rule 3 is
  what makes it recordable as such.
- Gate latency will be published as the platform's own contribution, separately
  from provider time, with the `REQ-N-PERF-1` target's dependence on realistic
  provider behaviour stated rather than assumed away.
- Indicator computation is a query over the operational store, which puts it on
  the `REQ-N-PERF-3` analytics-responsiveness surface rather than outside it.
- `observability-strategy.md` §5 is updated from measurement in this phase, and
  its status advances from Draft.

## Deferred

- **Error budgets and burn-rate alerting.** Both are derived from a target and a
  traffic volume; the traffic volume does not exist. Deferring is the same
  decision as not inventing the target.
- **Externally observed availability.** An SLO measured from outside the system
  requires a deployment to measure from (Phase 14).
- **Per-tenant SLOs.** No requirement asks for one, and a per-tenant target is a
  contractual commitment rather than an engineering measurement.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Set conventional industry targets now and calibrate later | The invented figure canonical §20 and §24 forbid. "Calibrate later" has no owner, no trigger, and no record that the number was never measured. |
| Set targets from the local measurements regardless of workload realism | Produces a gate-latency target derived from stub providers that would be quoted as a platform property. Rule 4 exists because this is the plausible mistake, not the reckless one. |
| Leave every target unset until a production deployment exists | Discards measurements that *are* valid — the platform's own contribution is genuinely measurable here — and defers `REQ-N-REL-5` past the phase canon assigns it to. |
| Compute indicators from the metrics backend for speed | Two sources of truth about the platform's promise, and the fast one may drop data under load. Rule 6 pays the cost instead. |
| Record unmeasurable indicators as "not applicable" | It is applicable and it is unmeasured. "Not applicable" retires the requirement; `TARGET NOT YET SET` with a blocker keeps it owed. |
