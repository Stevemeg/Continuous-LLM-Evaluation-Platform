# ADR-020 — The authorization model: roles, scopes, and where the decision is made

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M12.2 |
| Phase | Phase 12 — Enterprise security, RBAC, multi-tenancy, audit/compliance hardening |
| Canonical basis | §6 (RBAC), §16, §17 |
| Requirements | `REQ-F-12-2`, `REQ-F-12-7`, `REQ-F-12-8`, `REQ-N-SEC-1`, `REQ-X-5` |
| Domain model | `Role`, `RoleBinding`, `Membership` — invariant I-4 |
| Constrained by | [ADR-010](ADR-010-multi-tenancy.md) rules 3, 4 and 8 |

## Context

`REQ-F-12-2` requires role-based access control over datasets, prompts, suites,
baselines, policies, approvals, runs and reports. `REQ-N-SEC-1` requires every
authorization decision to be enforced server-side. `REQ-F-12-8` forbids withholding
access control by deployment tier.

ADR-010 already fixed *tenant* isolation at the persistence boundary. Authorization
is a different question: within one tenant, which principals may do which things, in
which projects. The store cannot answer it — row-level security knows the tenant, not
the permission — so this decision is about the second boundary, and specifically
about making omission impossible there too, since that is what made ADR-010 work.

The failure mode to design against is not a wrong permission check. It is a **missing
one**: a route added later that nobody remembered to protect. That failure is silent,
passes every test written for the route, and is found by an auditor or an attacker.

## Decision

1. **Permissions are a closed vocabulary, declared in the contract.** The set lives in
   `openapi.json` as an enum and is mirrored in code; the phase validator compares the
   two as sets. A permission that exists in one and not the other is a defect, on the
   rule Phase 4 established for every other vocabulary in this project.

2. **Permissions name an action on a resource class**, not a route. `run:create`,
   `baseline:approve`, `audit:read`. Routes change; the governed action does not, and
   `REQ-F-12-2` enumerates resource classes rather than endpoints.

3. **Roles are global; bindings are tenant-scoped.** A role is a named permission set
   and is not tenant data — this is the ADR-010 rule 4 exception the data model
   already enumerates (`user`, `role`). A `role_binding` binds a principal to a role
   within a scope and carries `organization_id NOT NULL` like every other tenant row.

4. **A binding's scope is either the organization or one project.** Two levels, not a
   tree. A hierarchy would need a resolution order, and a resolution order is where an
   inherited deny quietly becomes an inherited allow.

5. **Deny by default.** A principal with no binding has no permission. There is no
   implicit permission from membership, from being the creator of a record, or from
   the organization having exactly one member.

6. **Every route declares the permission it requires, and a route that declares none
   does not start.** The application asserts at import that every registered route
   carries a declared permission, in the same place and by the same mechanism that
   already refuses a route absent from the contract. This is the rule that addresses
   the failure mode above: forgetting is not expressible.

7. **The decision is made once, at ingress, from the verified principal**, and is
   never re-derived from request content deeper in the stack — the authorization
   analogue of ADR-010 rule 3.

8. **A refusal is audited** (`REQ-X-5`), and is a 403 that does not distinguish
   "you lack the permission" from "the object is not yours", for the reason the run
   surface already returns an indistinguishable 404.

9. **At least one principal retains administrative capability in every organization**
   (I-4), enforced by the **store**. Revoking the last binding that carries
   `role:grant` in an organization is refused by a trigger, not by service code,
   because service code is what an operator bypasses at 3am with a direct connection.

10. **Governance is never tier-gated** (`REQ-F-12-8`). There is no configuration flag
    that disables authorization, no bypass principal, and no environment in which the
    check is skipped. The absence of such a flag is asserted by the phase validator.

## Rationale

Rule 6 is the load-bearing decision and the reason this ADR exists. Every other rule
here is conventional RBAC. What makes an RBAC implementation fail in practice is a
surface added without a check, and the only defence that scales is one where the
default is not "unprotected" but "does not start". ADR-010 reached the same conclusion
about tenant predicates and put the enforcement where omission is impossible; this is
that argument applied one boundary out.

Rule 4 rejects a scope hierarchy deliberately. The requirement asks for access control
over named resource classes, not for an organizational tree, and a two-level scope is
the largest model in which "does this principal have this permission here" has an
answer that does not depend on traversal order.

Rule 9 places I-4 in the store for the same reason ADR-010 rule 1 places isolation
there. An organization that has locked itself out is not recoverable by any operation
the platform exposes — by design, since an operation that could recover it would be an
operation that could grant administrative capability without one.

## Deferred

- **Permission delegation and custom roles.** Roles are a fixed, reviewable set. A
  tenant-defined role would be tenant data with a global identity, which is D-1's
  shape, and no requirement asks for it.
- **Environment-scoped bindings.** `Environment` exists in the domain model and has
  no schema; scoping to it would mean specifying it, which is out of phase.

## Consequences

- Every existing route acquires a permission requirement, and every test that
  exercises one needs a principal that holds it. This is the largest single
  regression surface of Phase 12.
- Adding a route is now two decisions rather than one, and the second cannot be
  skipped.
- An organization created with no binding can do nothing. Bootstrapping is therefore
  a provisioning operation, alongside creating the tenant root itself — which is
  where creating an organization already lives.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Check permissions inside each service method | The check becomes invisible at the surface, and a route that forgets to call the service correctly is unprotected with no signal. Rule 6 is not expressible. |
| Attribute-based access control | More expressive, and the expressiveness is the problem: a policy language is a second system to test, and `REQ-F-12-2` asks for roles. |
| Ownership-implies-permission | Convenient and unauditable: "who may approve this baseline" would depend on who happened to create it, which is not a governance answer. |
| Enforce authorization in the database alongside tenancy | Row-level security answers "which rows", not "which actions". Expressing `baseline:approve` as a policy predicate would encode verbs in a mechanism designed for rows. |
| A superuser principal for operations | Directly contradicts rule 10 and `REQ-F-12-8`, and is the bypass every audit finds. |
