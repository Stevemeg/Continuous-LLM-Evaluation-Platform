# ADR-003 — Model and provider abstraction

| Field | Value |
|---|---|
| Status | **Proposed — decision gated on spike. NOT DECIDED.** |
| Milestone | M2.3 |
| Canonical basis | §15 (evaluate an aggregation library versus internal adapters), §19 |
| Requirements | `REQ-F-02-2`, `REQ-F-02-4`, `REQ-F-02-6`, `REQ-F-07-6`, `REQ-N-REL-4`, `REQ-N-SEC-5` |

## Context

The Provider Gateway is the sole egress to model providers. The requirements on it are unusually demanding for an abstraction layer:

- `REQ-F-07-6` — token and cost accounting per candidate, evaluator, judge, **sample**, run, project, and tenant.
- `REQ-F-02-2` — record every configuration parameter that affects output.
- `REQ-F-02-6` — isolate provider failure to a single candidate, leaving siblings valid.
- `REQ-N-REL-4` — defined, tested behaviour for outage, rate limiting, malformed response, and model deprecation, individually.
- `REQ-F-02-4` — hosted and self-hosted endpoints as first-class candidates.
- `REQ-N-SEC-5` — credentials never persisted in plaintext, logged, or present in reports.

Per-sample cost attribution and per-failure-mode semantics are the demanding pair. An aggregation library that normalises provider differences may also normalise away the error detail that `REQ-N-REL-4` requires, and may not expose usage at the granularity `REQ-F-07-6` needs.

## Decision

**None. This ADR does not select an approach.**

Whether a third-party aggregation library exposes sufficient error granularity and per-call usage detail is an empirical question about its actual behaviour under induced failure, not a question its documentation can settle. Deciding from documentation would risk discovering during Phase 5 that a core accounting requirement is unmet.

## Spike required before this ADR can be decided

| Element | Specification |
|---|---|
| Hypothesis | A third-party provider-aggregation library satisfies `REQ-F-07-6` per-sample accounting and `REQ-N-REL-4` per-failure-mode distinction without bespoke per-provider handling. |
| Candidates | Aggregation library; internal adapters behind a project-owned port; aggregation library behind a project-owned port. |
| Method | Drive each candidate against at least one hosted and one self-hosted endpoint. Induce each of the four named failure modes deliberately, including malformed responses via a stub endpoint. |
| Measurements | Whether per-call token and cost figures are retrievable and reconcile against provider-reported usage; whether the four failure modes are distinguishable programmatically; whether a single candidate's failure can be isolated; whether any credential appears in logs or serialised errors. |
| Decision rule | Reject any candidate that cannot distinguish all four failure modes or cannot supply per-call usage. Among survivors, prefer the smaller bespoke surface. |
| Falsification | If the aggregation library cannot supply per-call usage, `REQ-F-07-6` decides the outcome and internal adapters follow — regardless of the convenience the library offers elsewhere. |
| Environment | Requires provider credentials and network access to at least two endpoint types. Not available in the current environment. |

## Consequences of deferral

- Blocks Provider Gateway implementation (Phase 5 onward). Does not block Phase 2: the gateway is a container behind a port and the architecture names no library.
- Phase 3 must define the gateway port so that per-sample usage and discriminated failure types are expressible, whichever candidate wins. This is the constraint the spike protects.

## Interim architectural constraint

Whatever the outcome, **a project-owned port sits between the domain and any provider library**. `REQ-N-OBS-3` and canonical §25 both point the same way: a dependency must not define the domain's model of a provider call. Adopting a library is therefore at most an adapter decision, never a domain one — which is why the deferral is safe.
