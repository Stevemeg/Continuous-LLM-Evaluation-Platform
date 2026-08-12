# ADR-021 — Where per-tenant rate limits and quotas are enforced, and how they fail

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M12.5 |
| Phase | Phase 12 — Enterprise security, RBAC, multi-tenancy, audit/compliance hardening |
| Canonical basis | §16 (rate limits, quotas), §21 (budget exhaustion) |
| Requirements | `REQ-N-SEC-9`, `REQ-N-SCALE-2`, `REQ-X-9`, `REQ-F-10-5` |
| Threat model | T2 — resource abuse at the ingress boundary |

## Context

`REQ-N-SEC-9` requires rate limits and quotas enforced **per tenant**.
`REQ-N-SCALE-2` requires that concurrent evaluation across tenants shows no
cross-tenant interference. T2 rates resource abuse as *high* consequence rather than
merely an availability concern, for a specific reason: evaluation spends real money
at provider APIs, so an abuse path is a direct financial exposure.

The platform already bounds cost in one place — `REQ-X-9` and `REQ-F-10-5`, the
planner's pre-execution estimate, which refuses a run whose estimate exceeds its
budget. That is a bound on *one submission*. It says nothing about ten thousand
submissions each individually within budget, which is the threat here.

## Decision

1. **Enforcement is at the ingress boundary**, in the same layer that establishes the
   principal, and before any handler runs. A limit checked inside a handler has
   already paid for the work it was meant to prevent.

2. **The subject of a limit is the tenant, never the credential.** Rotating a key or
   issuing a second one must not multiply the allowance, which is what per-credential
   limiting would permit.

3. **Two distinct controls, deliberately not merged.**

   | Control | Bounds | Window |
   |---|---|---|
   | **Rate limit** | requests accepted per unit time | seconds |
   | **Quota** | evaluation runs started per period | a configured period |

   A rate limit protects the platform; a quota protects the tenant's spend. Merging
   them would give one number two meanings, and the correct value for each differs by
   orders of magnitude.

4. **The rate limiter is a fixed-capacity token bucket in Redis**, keyed by tenant,
   with the refill computed from an **injected clock**. Redis because coordination
   across processes is exactly what it is already in this repository for, and a
   per-process limiter is not a limit when there are two processes.

5. **The limiter fails closed.** If the coordination store is unreachable, requests
   are refused with a platform failure rather than admitted. A limiter that fails
   open is absent precisely when the system is under the load that broke it — and
   under this threat model, being absent means unbounded provider spend.

6. **Limits are per-tenant configuration with a platform default**, stored in the same
   tenant-scoped table as the retention policy. A tenant with no configured limit gets
   the default, and there is no value meaning "unlimited": `REQ-F-12-8` forbids
   governance that some deployments do without.

7. **Refusal is a `429` carrying `client_error`**, and states which control was
   exhausted and when it resets. `REQ-N-USE-3` requires a failure message to say what
   the caller can do; "you are rate limited" without a reset time does not.

8. **Refusals are counted, not audited per event.** A denied request is not a
   governance action — auditing each one would let an attacker fill the audit trail,
   which is the one store `REQ-N-COMP-3` forbids anyone to prune. Quota exhaustion,
   which is a tenant-visible policy outcome, **is** audited once per period.

9. **Consumption is recorded per tenant and is never shared.** Two tenants under load
   consume two buckets; exhausting one leaves the other's allowance untouched
   (`REQ-N-SCALE-2`), and this is asserted by an executed test rather than by the
   keying scheme being self-evidently correct.

## Rationale

Rule 5 is the one with a real cost, and it is the one T2 decides. The usual argument
for failing open is availability: a limiter outage should not become an outage. That
argument assumes the resource being protected is the platform's own capacity, which
recovers. Here the resource is money spent at a third party, which does not. Between
"the platform is briefly unavailable" and "the platform is briefly willing to spend
without limit", the requirement set has already chosen — `REQ-N-COST-2` makes budget
exhaustion a defined outcome rather than an incident, and SA-5 in the security
architecture says fail closed.

Rule 8 is a smaller decision with the same shape. An audit trail that an unauthenticated
flood can grow is an audit trail whose retention floor becomes a liability.

Rule 4's injected clock is what makes any of this testable. A limiter that reads the
wall clock can only be tested by sleeping, so it is tested loosely or not at all.

## Deferred

- **Concurrency limits** — a bound on simultaneously executing runs rather than on
  runs started. The harness already bounds concurrency internally; a tenant-level
  concurrency ceiling is a scheduling decision that belongs with the deployment model.
- **Burst shaping and per-route weighting.** One bucket per tenant, uniformly. Route
  weighting requires a cost model per route that nothing has measured, and
  `REQ-N-MAINT-4` forbids inventing one.
- **Provider-spend quotas denominated in currency.** The planner already bounds a
  single run in currency; a rolling per-tenant currency ceiling needs cost
  reconciliation across a period, which is Phase 13 telemetry work.

## Consequences

- Redis moves from "required for scheduled execution" to "required to serve a
  request". Recorded here because it changes the deployment's failure domain.
- Every authenticated request pays two Redis round trips.
- A test suite that hammers the API needs either a generous default or a test-scoped
  configuration; the default is set with that in mind and stated in the code.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| In-process limiter | Not a limit across processes, and the deployment model has more than one. |
| Limit per credential | Rotation or a second key multiplies the allowance, which is not a limit. |
| Fail open when the coordination store is down | Removes the control exactly when load is highest; under T2 the exposure is provider spend, which does not self-recover. |
| Enforce in a reverse proxy or gateway | Real, and outside this repository. It would also be invisible to the tests that must prove `REQ-N-SEC-9`, and it cannot see the tenant, which is resolved from a credential the proxy does not verify. |
| One combined limit for requests and runs | One number with two meanings; the right values differ by orders of magnitude. |
| Audit every refusal | Lets an attacker grow the one store nobody is permitted to prune. |
