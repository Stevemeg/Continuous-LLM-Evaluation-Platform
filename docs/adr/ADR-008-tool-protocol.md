# ADR-008 — Tool integration protocol

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M2.3 |
| Canonical basis | §13 (create an ADR comparing the interoperability protocol with direct adapters), §16, §19 |
| Requirements | `REQ-F-04-1`, `REQ-F-04-6`, `REQ-F-12-9`, `REQ-N-SEC-4`, `REQ-X-7` |

## Context

Canonical §13 is deliberately conditional: evaluate a standard tool-interoperability protocol "only where it provides genuine interoperability", and produce an ADR comparing it with direct adapters. Any tool access must be permission-scoped, auditable, schema-validated, and tenant-aware.

The platform's relationship to tools is narrower than an agent runtime's. It does not *call* a customer's tools to accomplish work. It **evaluates trajectories** in which tools were already called (`REQ-F-04-1`), and it executes custom evaluators (`REQ-F-12-9`). Tool results arriving for evaluation are untrusted data (`REQ-F-04-6`, `REQ-X-7`).

## Decision

**Direct adapters behind a project-owned port for the first release. A standard interoperability protocol is an optional adapter, adopted only when a concrete interoperability requirement exists.**

The condition for adopting the protocol is stated in advance so the decision is not re-litigated on preference: **a named external tool ecosystem that a tenant already operates, which the platform must integrate with and which the protocol would make integrable without bespoke work per tool.** Absent that, the protocol adds a dependency and an attack surface for interoperability nobody has asked for.

## Rationale

1. **The dominant use is ingesting trajectories, not invoking tools.** `REQ-F-04-1` describes accepting a record of calls already made. A tool-invocation protocol does not help ingest a record.
2. **Canonical §13 makes adoption conditional, and the condition is currently unmet.** No requirement in the set names an external tool ecosystem the platform must interoperate with.
3. **Security surface.** `REQ-N-SEC-4` requires an enforced permission boundary and `REQ-X-7` treats tool output as untrusted. A general protocol widens what must be permission-scoped, schema-validated, and audited, for no current gain.
4. **The port makes it reversible.** With a project-owned port, adopting the protocol later is an adapter addition, not a domain change — which is what makes deferring it safe rather than merely cheaper.

## Consequences

- Each tool integration required in the first release costs a bespoke adapter. Accepted, because the expected count is low and each is small.
- If the adoption condition is met later, the work is an adapter behind the existing port.
- The port must express permission scope, schema validation, tenancy, and audit for every invocation regardless of transport (`REQ-F-12-9`).

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Adopt the protocol now as the primary integration path | Canonical §13 forbids adopting it absent genuine interoperability need. Adds dependency and attack surface for no current requirement. |
| Support both from the start | Two integration paths to secure, audit, and test, doubling the surface governed by `REQ-N-SEC-4` for no present benefit. |
| Rule the protocol out permanently | Overreach. The interoperability case is plausible later; the decision is timing, and the trigger is stated above. |
