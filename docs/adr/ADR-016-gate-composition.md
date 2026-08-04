# ADR-016 — How statistical evidence and configured thresholds compose into a gate decision

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M7.5 |
| Canonical basis | §9, §11, §25 |
| Requirements | `REQ-F-08-1`, `REQ-F-08-4`, `REQ-F-09-2`, `REQ-F-09-3`, `REQ-F-09-5`, `REQ-X-10` |
| Depends on | [ADR-007](ADR-007-regression-statistics.md), which is accepted and unchanged by this record |

## Context

ADR-007 decided how to tell whether a metric moved. It did not decide what to do
about it, and the two are separate questions with different owners: the first is
a property of the data, the second is a release policy.

`REQ-F-08-1` requires classification "using absolute and relative thresholds
defined per metric". Read alone, that is the fixed-threshold method ADR-007
rejected. Read with ADR-007, it is a second layer: the interval decides whether
there is **evidence** of a change; the thresholds decide whether the change
**matters**. Nothing in either requirement says how the two combine, and every
plausible combination produces a different verdict on the same data.

`REQ-F-09-2` compounds it. Four outcomes must exist — hard fail, warning, manual
approval, policy exception — and `REQ-F-08-4` adds "insufficient evidence" as a
first-class result that must never be presented as "no change". A gate that took
the worst of everything would report an abstention as a failure; one that took
the best would report it as a pass. Both are wrong in the way canonical §25
warns about.

## Decision

### 1. Each criterion states its own consequences

A criterion carries three mappings — `on_regression`, `on_insufficient_evidence`,
`on_not_comparable` — each one of `hard_fail`, `warning` or `approval_required`.

**`pass` is not an available mapping.** A policy that could map an abstention to
a pass would erase the `REQ-F-08-4` distinction at exactly the point where it
costs something. A team that genuinely does not care about a metric removes the
criterion, which is visible in the policy version, rather than configuring the
gate to look at it and say nothing.

### 2. Thresholds are applied in a fixed order, and each can only do one thing

| Order | Rule | Can produce | Cannot produce |
|---|---|---|---|
| 1 | **Absolute floor** | `hard_fail` | anything else |
| 2 | **Statistical classification** (ADR-007) | the criterion's configured mapping | — |
| 3 | **Relative tolerance** | `pass`, for a detected regression only | a regression |

The absolute floor outranks the comparison because it is a statement about where
the product may not go **at all**. A baseline that is itself below the floor
would otherwise let a candidate sit below it indefinitely, improving slightly
each time and passing every gate.

The relative tolerance can only forgive, never accuse. A tolerance that could
manufacture a regression from a difference the interval could not distinguish
from zero would be the fixed-threshold method smuggled back in through the
policy — firing on noise, which is precisely what ADR-007's spike measured it
doing at small samples.

A forgiven regression is recorded as `relative_tolerance` in `rule_fired`, with
the measured relative movement in the detail. It passed **because someone
decided that much was acceptable**, not because nothing was detected, and the
evidence says which.

### 3. The decision outcome takes the worst action, then the truest description

```
hard_fail            if any criterion hard-fails
approval_required    else if any criterion requires approval
not_comparable       else if every criterion was not comparable
insufficient_evidence else if every criterion abstained
warning              else if any criterion warns
pass                 otherwise
```

Blocking verdicts come first because a policy that says an abstention blocks a
release must block it; reporting `insufficient_evidence` in its place would turn
a stop into a note. Below that threshold the more informative description wins:
if nothing could be measured, "we could not tell" is a truer summary of the
decision than "warning", which is `REQ-F-08-4` applied to the decision as well as
to each metric.

### 4. A platform failure is never a gate outcome

`REQ-F-09-5`. The contract's `GateOutcome` has no member for it and the schema's
check constraint has no value for it. An evaluation that could not run produces
an error response, not a verdict — a quality gate that reports infrastructure
trouble as a quality result teaches its users to ignore it.

### 5. An exception never edits a decision

`REQ-F-09-6` requires an actor, a justification and an expiry. The decision row
is audit-class and immutable; an exception is a separate, later, audited act
recorded against it, and the outcome a reader sees is derived from both. A
decision that could be rewritten after the fact is not evidence that a release
was justified — it is evidence that someone had write access.

An expired exception stops applying without anything having to run. The query
asks for the exception in force *now*, because an expiry that requires a
scheduled job to take effect is an expiry that silently does not.

## Alternatives rejected

**Take the worst of all criteria, including abstentions.** Simple, and it makes
"insufficient evidence" indistinguishable from failure. Teams would respond by
lowering minimum sample sizes until the gate stopped abstaining, which is the
opposite of what ADR-007 is for.

**Let the policy decide the threshold ordering.** Maximum flexibility, and the
same data would yield different verdicts under two policies that both claim to
implement `REQ-F-08-1`. The ordering is a correctness property, not a preference.

**Apply the relative tolerance before the interval.** This is the fixed-threshold
method: a delta larger than the tolerance fails regardless of whether it can be
distinguished from noise. ADR-007's spike measured that behaviour firing on pure
noise in roughly one run in six at n=20.

## Consequences

- A policy version with no criteria cannot be published. A gate that governs
  nothing would report `pass` for every release, which is worse than no gate
  because it carries the appearance of one.
- Criteria whose source has no signal yet — judge agreement, until the ensemble
  exists — abstain with a reason rather than passing. A gate for a capability
  that has not been built should not be quietly reporting success.
- The per-metric precision threshold and minimum sample size remain **unset by
  default**, as ADR-007 requires. A criterion without a precision threshold
  abstains rather than guessing, which will look like an over-cautious platform
  until real data supplies the values. That is the correct direction to be wrong
  in.
