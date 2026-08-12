# Phase 12 — implementation plan

| Field | Value |
|---|---|
| Phase | Phase 12 — Enterprise security, RBAC, multi-tenancy, audit/compliance hardening `[CANON §23]` |
| Written | Before any Phase 12 code, per canonical §22 |
| Base | `47f3874`, the accepted Phase 11 tree |
| Canonical basis | §6 (enterprise governance), §16 (security, governance, multi-tenancy), §17 (data model domains), §19 (multi-tenancy isolation ADR), §21 (cross-tenant leakage), §23 (Phase 12) |

This is the plan the phase was executed against. It is kept as written rather than
edited to match the outcome; where the outcome differed, the difference is recorded
in [`README.md`](README.md) instead. A plan revised to agree with its result records
nothing.

---

## 1. What the phase is for

Every phase so far has created records. Phase 12 is the phase that decides **who may
create them, who may read them, and what remains provable afterwards**.

Concretely, the platform today authenticates by splitting a bearer token on a colon:

```python
org, _, subject = token.partition(":")
```

Anyone who can reach the API can name any tenant. Row-level security is real and
enforced by the store, but it is enforced *for the tenant the caller claimed to be*.
The isolation architecture is sound and its front door is open. `identity.py` says so
in as many words — "until Phase 12 issues real principals". That is the first thing
this phase fixes.

## 2. Requirement scope, from the canonical specification

Phase 12 owns every requirement below. The status column is the traceability matrix
at `47f3874`: **architecture and data model traced, implementation and tests empty**.
That emptiness is the phase.

| Requirement | Substance | At `47f3874` |
|---|---|---|
| `REQ-F-12-1` | Organizations, projects, environments as first-class scopes | schema only |
| `REQ-F-12-2` | RBAC over datasets, prompts, suites, baselines, policies, approvals, runs, reports | nothing |
| `REQ-F-12-3` | Scoped API and service credentials, rotation, revocation | nothing |
| `REQ-F-12-4` | Audit of every governed change, with actor, time, justification | writer exists; no surface, no justification |
| `REQ-F-12-5` | Tenant isolation | enforced by the store, but the principal is unverified |
| `REQ-F-12-6` | Per-tenant retention and deletion policy, floored by `REQ-N-COMP-3` | nothing |
| `REQ-F-12-7` | Approvals recorded with actor, time, artifact version | partially, per-surface |
| `REQ-F-12-8` | Governance in every deployment configuration, never tier-gated | assertion only |
| `REQ-F-12-9` | Tool and custom-evaluator invocation permission-scoped, schema-validated, audited, tenant-isolated | schema validation only |
| `REQ-X-5` | Every governance-relevant action audited | per-surface, no justification column used |
| `REQ-N-SEC-1` | Every request authenticated; every authorization decision server-side | shape only |
| `REQ-N-SEC-2` | Cross-tenant attempts fail **and are audited** | fail: yes. audited: no |
| `REQ-N-SEC-4` | Custom evaluators and tools under an explicit permission boundary | nothing |
| `REQ-N-SEC-5` | Credentials never persisted in plaintext, logged, or in reports | config only |
| `REQ-N-SEC-6` | Encrypted in transit; at rest where applicable | nothing |
| `REQ-N-SEC-7` | Dependency vulnerability scanning with a defined failure policy | nothing |
| `REQ-N-SEC-8` | A formal threat model before production hardening | architectural predecessor exists (SR-4) |
| `REQ-N-SEC-9` | Per-tenant rate limits and quotas | nothing |
| `REQ-N-PRIV-1` | Sensitive data classes classified and handled by class | nothing |
| `REQ-N-PRIV-2` | Redaction where content must not reach a judge, report or log | nothing |
| `REQ-N-PRIV-3` | Deletion executable within a defined period, auditable | schema only |
| `REQ-N-PRIV-4` | Deletion extends to derived artifacts and traces | schema only |
| `REQ-N-COMP-1` | An auditor can answer what evidence supported a release decision | schema only |
| `REQ-N-COMP-3` | Audit retained independently, not deletable by the actors it records | grants only |
| `REQ-N-SCALE-2` | Concurrent multi-tenant execution without interference | nothing |
| `REQ-N-SCALE-3` | Adding a tenant requires no schema or deployment change | assertion only |

Two carried debts are explicitly assigned here by the register:

- **D-1** — `comparison.evaluator_version_id` does not carry the tenant. Owning phase
  recorded as "the tenancy and authentication phase (Phase 12)".
- **`REQ-F-05-8` erasure** — the contract declares `createErasureRequest` and the
  schema declares `erasure_request`; nothing implements either. Its requirement set
  (`REQ-N-PRIV-3`, `REQ-N-PRIV-4`, `REQ-N-COMP-3`) is compliance hardening, which is
  this phase by canonical §23.

### Explicitly NOT in scope

| Not in scope | Why |
|---|---|
| `listDatasetVersions`, `createDatasetVersion`, `getSampleAnalysis` | Declared in the contract, unimplemented, and tracing to `REQ-F-05-*` and `REQ-F-03/04/11-*`. Those are capability phases, not this one. Implementing them here would be inventing scope. |
| Judge accuracy, calibration, agreement or hallucination thresholds | Canon does not assign them here, and no number would be measured. |
| Hosted-provider validation | Not assigned here; no credential will be fabricated. |
| D-3 object-store adapter | Register says Phase 14. |
| D-4 gate latency semantics | Register says whichever phase revisits gate policy semantics. Not this one. |
| Out-of-process evaluator sandboxing | ADR-006 rule 4 defers the *mechanism* to the deployment model (Phase 14). This phase delivers the grant, the deny-by-default decision and the audit; it does not claim a process boundary it has not built. |
| The final README | Deferred to Phase 15. |

## 3. Architecture decisions required

Three decisions cannot be made incidentally. Each gets an ADR before the code.

| ADR | Question | Why it cannot be decided in a commit message |
|---|---|---|
| ADR-019 | How is a credential presented, stored and verified? | Storing a verifier rather than a secret, and which derivation, is the difference between a breach that leaks hashes and one that leaks keys. |
| ADR-020 | What is the RBAC model — roles, scopes, defaults, and where the decision is made? | Deny-by-default versus allow-by-default is not reversible once surfaces depend on it. |
| ADR-021 | Where are rate limits and quotas enforced, and do they fail open or closed? | A limiter that fails open under load is a limiter that is absent exactly when it is needed. |

**No accepted decision is changed.** In particular ADR-010 rule 4 — the enumerated
global-record exception that makes built-in evaluators tenant-less — stands. D-1 is
closed *within* it (§5, M12.8) rather than by amending it. Had the only available fix
required amending it, this plan would have stopped for review instead.

## 4. Milestones

Dependencies are strictly left to right; each milestone is committed only when its
own tests pass.

```
M12.1 ─► M12.2 ─► M12.3 ─┬─► M12.4
                          ├─► M12.5
                          ├─► M12.6
                          └─► M12.7 ─► M12.8 ─► M12.9
```

### M12.1 — Principals and credentials

*Slices:* schema → verifier → repository → contract → service → routes.

| Item | Location |
|---|---|
| Schema | `docs/data/schema/12-identity-and-access.sql` — `app_user` (global, ADR-010 r4), `membership`, `role` + `role_permission` (global), `role_binding`, `service_account`, `api_key` |
| Verification | `src/clep/security/credentials.py` — key minting, PBKDF2-HMAC-SHA256 verifier, constant-time comparison, presented-key parsing |
| Store | `src/clep/security/repository.py` |
| Contract | `docs/api/openapi.json` — `issueApiKey`, `listApiKeys`, `rotateApiKey`, `revokeApiKey` |
| Ingress | `src/clep/api/app.py` — `principal_from_authorization` resolves against the store |

*Acceptance:* a token the caller invented is rejected; the secret is returned exactly
once and is not recoverable from the store; revocation and expiry deny; rotation
issues a new key and leaves the audit trail of the old one; no plaintext secret and
no verifier appears in any response.

*Security impact:* this is the phase's core. Negative tests: forged token, revoked
key, expired key, key belonging to another tenant, key with a valid id and wrong
secret, timing-independent comparison.

### M12.2 — RBAC and server-side authorization

| Item | Location |
|---|---|
| Vocabulary | contract enum `Permission`; `src/clep/security/rbac.py` holds the same set, compared by the validator |
| Decision | `rbac.decide()` — deny by default, org-scope and project-scope bindings |
| Enforcement | every route declares its required permission; a route without one fails at import, as contract drift already does |
| Contract | `createRoleBinding`, `listRoleBindings`, `revokeRoleBinding`, `listRoles` |

*Acceptance:* a principal without the permission is refused server-side and the
refusal is audited; a project-scoped binding does not grant on a sibling project;
the last administrative binding in an organization cannot be revoked (I-4), enforced
by the store rather than by the service.

### M12.3 — Audit surface and denial auditing

| Item | Location |
|---|---|
| Surface | `listAuditEvents` (already in the contract), cursor-paged |
| Writer | `src/clep/api/audit.py` gains `justification` and `target_content_digest` |
| Denial | every 401 and 403, and every well-formed identifier that resolves to nothing under the caller's tenant, emits `access.denied` |

*Note on `REQ-N-SEC-2`.* The platform deliberately cannot distinguish "another
tenant's run" from "no such run" — a 404 that differs from a 403 tells an attacker
which identifiers exist. So the audit records what is honestly observable: *this
principal asked for this identifier under this tenant and was refused*. It does not
claim to know the row belongs to someone else. This is written down because the
alternative reading — that the platform detects cross-tenant reads — would be a
claim the design forbids it from making.

### M12.4 — Erasure and retention

| Item | Location |
|---|---|
| Erasure | `src/clep/security/erasure.py` — demote → destroy → verify, per the state ordering the schema comments already fix |
| Surface | `createErasureRequest` (already in the contract) |
| Retention | `tenant_retention_policy`, with the audit floor enforced by a store constraint, not by service code |

*Acceptance:* content is destroyed, `Example` survives, every referencing run is
demoted to `auditable` **before** destruction, derived artifacts sharing the source
digest are destroyed too, `gate_evidence` is not, completion requires
`verified_count = target_count`, a baseline pin returns 409 unless an audited
override is supplied, and no tenant policy can lower the audit floor.

### M12.5 — Rate limits and quotas

`src/clep/security/limits.py`, per-tenant, Redis-backed, injected clock.
*Acceptance:* the limit is enforced per tenant; exhausting tenant A leaves tenant B
unaffected (`REQ-N-SCALE-2`); the limiter fails **closed**, per ADR-021.

### M12.6 — Evaluator and tool permission boundary

`src/clep/security/grants.py` — deny-by-default capability grants, invocation audited
with evaluator identity, version and permissions used; cross-tenant reach not
expressible in the interface. The declared-permission column already exists on
`evaluator_version` and is currently written and never read; this milestone makes it
load-bearing.

*Honesty constraint:* the out-of-process boundary ADR-006 rule 4 requires is **not**
delivered here and will be recorded as still open.

### M12.7 — Privacy: classification and redaction

`src/clep/security/privacy.py`. Classes come from the PRD's `DS-1`…`DS-9` taxonomy
the threat model already consumes; redaction is applied on the paths that reach a
judge, a report or a log, and is tested by asserting the redacted content does not
appear downstream.

### M12.8 — Transport, dependency scanning, D-1, and the threat model

| Item | Substance |
|---|---|
| `REQ-N-SEC-6` | Configuration refuses a non-TLS database or Redis DSN outside a local environment. Verifiable by executing the loader, not by reading it. |
| `REQ-N-SEC-7` | `docs/evidence/phase-12/dependency_scan.py` queries OSV for each declared dependency, with a stated failure policy. Executed for real; the run is recorded. Offline, it fails rather than passes. |
| D-1 | A store-level trigger on `clep.comparison` refusing an `evaluator_version` that is neither global nor the row's own tenant. The composite foreign key remains unformable while the global exception stands; the guarantee moves into the store regardless. |
| `REQ-N-SEC-8` | The threat model is updated to a post-implementation document: `SR-5` closes, `T1`/`T2` mitigations become implemented rather than intended, and what is still open is stated. |

### M12.9 — Validation, self-test, evidence, closure

`check_phase12.py`, `selftest_phase12.py`, evidence, traceability regeneration, full
regression closure, finalized-tree re-run.

## 5. Validation strategy

The project has lost checks to source-text matching in nine recorded places. So:

| Check style | Used for |
|---|---|
| **Executed** | Every security property. A forged credential is presented to a real app; an unauthorized principal is refused by a real route; a rate limiter is driven past its bucket; an erasure is executed against PostgreSQL and the rows are counted afterwards. |
| **Store-level** | Anything the application could forget: last-admin, audit grants, retention floor, D-1. Asserted by executing SQL against a migrated database, not by reading DDL. |
| **Static, with a stated reason** | Only where the property *is* textual: that no route lacks a declared permission (derived from the app's own route table at import, not from a grep), and that a vocabulary in code equals the vocabulary in the contract (compared as sets of values, not searched for as strings). |

Fast subset after each milestone; the complete validator and the regression chain
once, at the end.

## 6. Self-test

Every new check gets at least one planted violation proving it can fail. The
restoration mechanism is the Phase 11 one — `git checkout -- .` followed by
`git clean -fdq`, derived rather than enumerated, with the tree compared against HEAD
after every case.

## 7. Regression, database, and API impact

| Area | Impact |
|---|---|
| Schema | One new file, `12-identity-and-access.sql`. Additive: no existing table is redefined, so the applied-file digest chain is untouched. Every new tenant-scoped table gets `organization_id NOT NULL`, ENABLE + FORCE row-level security, a policy comparing the tenant context, and composite foreign keys (P-1, P-2, P-5). `app_user` and `role` are global under ADR-010 rule 4 and are added to the enumerated exception in `data-model.md` P-4 — where `user` and `role` were already enumerated in Phase 3. |
| API | Contract first, always. Ten new operations, all tenant-derived from the credential; **no new operation takes an organization in its path**, because a tenant read from a path is the thing ADR-010 rule 3 forbids. |
| Existing tests | `test_tenant_isolation.py` enumerates every tenant-scoped table and asserts the list equals the live catalogue, so the new tables must be added there or that test fails — which is the mechanism working. |
| Existing surfaces | Every existing route acquires an authorization requirement. Existing tests construct principals by hand; they will need a real one. This is the largest regression surface in the phase. |

## 8. Risks carried into the phase

| # | Risk | Handling |
|---|---|---|
| R-1 | Authorization added to 47 existing routes breaks a large fraction of the suite at once | Do M12.1 and M12.2 as one commit boundary, with the test fixture that mints a real credential written first |
| R-2 | The erasure path touches artifacts, runs and reproducibility state; a partial erasure is worse than none | The state machine's ordering is already fixed by the schema comments; verification is a stored count, and completion is refused without it |
| R-3 | A rate limiter with a wall-clock dependency is untestable | Clock injected |
| R-4 | Dependency scanning depends on a network service | Recorded as executed-with-a-date evidence, and the check fails rather than passes when the source is unreachable |
| R-5 | The phase could drift into implementing dataset management because erasure needs content | Erasure operates on `example_content` rows that already exist; no dataset surface is added |

## 9. What this phase will not close

Carried forward, unchanged and unclosed:

- judge quality is uncalibrated;
- statistical, agreement and hallucination thresholds are uncalibrated;
- hosted-provider execution is unvalidated;
- scheduled execution has still not been combined with a live real model;
- D-3 (object-store adapter) and D-4 (published latency semantics) remain open;
- out-of-process evaluator isolation (ADR-006 rule 4, SR-2) remains open;
- `REQ-N-SEC-6` at-rest encryption remains a deployment property (Phase 14);
- `REQ-N-COMP-4` remains deferred to Phase 15.
