# ADR-024 — The operational surface is a separate application

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M13.6 |
| Phase | Phase 13 — Production observability, SLOs, cost/latency telemetry |
| Canonical basis | §14, §16, §17 |
| Requirements | `REQ-N-OBS-2`, `REQ-N-OBS-4`, `REQ-N-SEC-1`, `REQ-N-OPS-1`, `REQ-F-11-9`, `REQ-X-10` |
| Constrained by | [ADR-020](ADR-020-authorization-model.md) rule 6, [ADR-010](ADR-010-multi-tenancy.md) rule 3, [ADR-022](ADR-022-telemetry-port.md) rule 5 |

## Context

Production observability needs three things reachable over HTTP that the tenant
API cannot provide: a liveness probe, a readiness probe, and a metrics exposition
endpoint. All three are conventionally unauthenticated, because the things that
call them — an orchestrator's health check, a metrics scraper — have no principal
and cannot acquire one.

`REQ-N-SEC-1` requires every authorization decision to be enforced server-side,
and ADR-020 rule 6 implements that with an assertion at application start:
`_assert_every_route_is_guarded` walks the routes FastAPI actually registered and
raises `ContractError` unless every one carries the guard's marker. There is no
exemption list. This is not an inconvenience to be worked around — it is the
mechanism ADR-020 identified as load-bearing, chosen specifically so that
"forgetting is not expressible".

So adding `/metrics` to the tenant application does not merely bend a rule. It
either fails to start, or it starts because somebody added the first entry to an
exemption list — and an exemption list is the thing ADR-020 rule 6 exists to
never have.

There is a second reason, which would apply even if the first did not. A metrics
endpoint is a side channel. Even with ADR-022 rule 5 forbidding tenant
identifiers as labels, request-rate and queue-depth series describe platform
activity, and platform activity on a single-tenant-active deployment is that
tenant's activity. Serving that from the same application, on the same port, as
tenant data is a trust-boundary decision, and trust-boundary decisions belong in
an ADR rather than in a router.

## Decision

1. **Operational endpoints are served by a separate ASGI application**, built by
   its own factory, with its own lifecycle. They are never mounted onto, nor
   registered on, the tenant application.

2. **The tenant application's guard remains absolute.** ADR-020 rule 6 acquires
   no exemption, no allowlist, and no unauthenticated route. Phase 13 adds
   nothing to the tenant application that does not carry a declared permission.

3. **The operational surface carries no tenant identity of any kind**: no
   organization or project identifier, no customer content, no evaluation
   payload, no credential, and no principal. What it may expose is the platform's
   own health and metric series whose labels are bounded enumerations under
   ADR-022 rule 5 — which is what makes this rule enforceable rather than
   aspirational, since a tenant identifier is not expressible as a label.

4. **The operational surface is not part of the tenant contract.** It does not
   appear in `docs/api/openapi.json`, and the two surfaces are asserted to be
   disjoint. A client discovering the tenant contract discovers no operational
   endpoint.

5. **Liveness and readiness are distinct, and neither is a tenant-visible
   outcome.** Liveness answers whether the process should be restarted.
   Readiness answers whether it should receive traffic — a process whose database
   or coordination store is unreachable is *not ready*, which is not the same as
   unhealthy. A tenant never learns this by way of a readiness response; it
   receives the honest refusal `REQ-F-09-5` requires, from the tenant API.

6. **Where the operational application is exposed is a deployment decision, not
   an application one.** The application binds where it is told. Being a separate
   application is what makes "reachable only from the cluster's network" an
   option that exists; choosing it is Phase 14's.

7. **Operator alerting extends the existing alert model rather than adding a
   second one.** Phase 11 built alert rules and evaluation for the tenant
   audience. The operator audience of the observability strategy §6 is a second
   *audience* over that machinery — an operator is never paged for a tenant's
   quality regression, and a tenant is never alerted for platform-internal
   degradation.

## Rationale

Rule 1 is forced, not chosen, and that is worth recording plainly: the
alternative does not start. It is rare and useful when an earlier decision's
enforcement mechanism settles a later question mechanically, and this is one of
those times — ADR-020 rule 6 was written to make a class of mistake
inexpressible, and here it is, refusing a mistake in a phase written two phases
later by making the application fail to import.

Rule 3 depends on rule 5 of ADR-022 for its teeth. "The operational surface must
not expose tenant identifiers" is another rule of the kind that everyone agrees
with and someone eventually violates helpfully. It is enforceable here only
because the metric API cannot express a tenant label at all: there is no bounded
enumeration of organizations to declare. The prohibition and the mechanism were
designed together, in that order, for that reason.

Rule 5's distinction is the `REQ-X-10` distinction in operational clothing.
"Unhealthy" and "not ready" are different claims about the platform, and
collapsing them makes a dependency outage look like a defect in this system —
the same category error as reporting a provider failure as a quality verdict.

Rule 7 exists because the plausible mistake here is building a second alerting
subsystem, since the operator's alerts *feel* different from a tenant's. They are
not different in mechanism; they are different in audience and routing. Two
alerting systems means two sets of rules to keep consistent and one of them
getting less attention, which is how an operator ends up muting both.

## Consequences

- Two applications are constructed, started and tested. Integration tests that
  want both must run both, and a deployment that runs only the tenant application
  has no metrics endpoint — which is the correct behaviour, not a degradation.
- The readiness probe needs a dependency check that does not itself require a
  principal, so it inspects connectivity rather than executing a tenant query.
  It therefore reports reachability, never data.
- Nothing in the tenant contract's operation count changes in this phase from
  operational endpoints. Any change to that count comes from correlation fields,
  not from health or metrics.
- An operator-audience alert is a new routing target in the existing alert model,
  not a new table and not a new evaluator.

## Deferred

- **Network exposure, TLS termination and scrape authentication.** Deployment
  properties (Phase 14). Rule 6 is precisely the statement that this phase does
  not decide them.
- **Paging integrations.** A destination for an alert is a deployment
  integration; the alert condition is the platform's concern and is in scope.
- **Profiling and debug endpoints.** No requirement asks for them, and an
  unauthenticated heap dump is the reason this ADR is cautious about the surface
  in the first place.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Register the operational endpoints on the tenant application with an exemption in the route guard | Creates the first entry in an exemption list that ADR-020 rule 6 exists to never have. The next entry is added by someone who is not reading this ADR. |
| Give the operational endpoints a permission and require a credential | A liveness probe holding a tenant credential is a credential distributed to infrastructure, and a readiness probe that can fail authentication reports "unhealthy" during a credential rotation. It also puts platform health inside a tenant's authorization scope, where it does not belong. |
| Mount a separate ASGI sub-application at a path on the tenant application | Same process, same port, same address — the trust boundary is a routing prefix, and route-guard enforcement walks the parent's routes. It looks separate and is not. |
| Expose metrics by writing a file for a sidecar to read | Avoids the HTTP question by adding a filesystem contract and a sidecar, neither of which any requirement asks for, and makes readiness unanswerable. |
| Serve operational data from the existing analytics surface | Analytics is tenant-scoped, authenticated, and answers questions about evaluations. Platform health is neither tenant-scoped nor an evaluation outcome, and conflating them puts operator data behind a tenant's row-level security. |
