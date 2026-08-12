# ADR-019 — How a credential is presented, stored, and verified

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M12.1 |
| Phase | Phase 12 — Enterprise security, RBAC, multi-tenancy, audit/compliance hardening |
| Canonical basis | §16 (scoped API/service keys, rotation, revocation, auditability), §17 |
| Requirements | `REQ-F-12-3`, `REQ-N-SEC-1`, `REQ-N-SEC-5`, `REQ-X-5` |
| Domain model | `ApiKey`, `ServiceAccount`, `Membership` — invariants I-2, I-3 |

## Context

Until this ADR the platform derived the tenant from an unverified bearer token by
splitting it on a colon. The organization was whatever the caller wrote. Every
row-level security policy in the schema was correct and every one of them was
evaluated against a tenant the caller chose.

`REQ-F-12-3` requires scoped credentials with rotation and revocation. `REQ-N-SEC-5`
requires that credentials are never persisted in plaintext, logged, or included in
reports or artifacts. I-2 states the same thing in the domain: an `ApiKey` secret is
never persisted or retrievable after issue.

Three questions have to be answered together, because answering them separately is
how a credential system acquires a lookup that has to scan every row, or a
verification that leaks its own answer through timing.

1. What does the caller present?
2. What does the store hold?
3. How is a presented credential turned into a principal?

## Decision

1. **The store holds a verifier, never the secret.** `api_key.verifier` holds a
   PBKDF2-HMAC-SHA256 derivation over a per-key random salt. The secret exists in
   exactly one response, at issue, and is not recoverable afterwards from the store,
   from a log, or from any surface. There is no "show key" operation, because an
   operation that could show it would mean the store could too.

2. **The presented credential carries its own routing.** The form is
   `clep_<organization-id>_<key-id>_<secret>`, all three identifiers in the
   project's Crockford base32 form, with `<secret>` 160 bits of `os.urandom`. The
   key identifier is there so verification is a single indexed lookup: without it,
   verification would have to derive the candidate against every stored verifier,
   which is both O(keys) per request and, at a work factor high enough to matter, a
   denial-of-service surface the platform would be providing to itself.

3. **The organization segment is a routing hint that the verification proves.**
   Isolation is enforced by the store (ADR-010 rule 1), so the credential lookup
   itself must happen inside a tenant context — which is the context the request has
   not yet earned. The segment resolves that ordering: the context is opened on the
   organization the credential names, and the key is then looked up *inside* it. A
   credential naming the wrong organization finds no row, because row-level security
   hides it, and is refused. So the organization is not trusted because it was
   presented; it is established because the key verified where it claimed to live.
   Nothing is returned to the caller on that path, so a failed attempt reads nothing
   it could disclose.

4. **The identifier is not a credential.** Knowing a key id grants nothing: the
   verifier cannot be inverted, and a wrong secret against a real id is refused
   identically to a right secret against a revoked one.

5. **Comparison is constant-time.** `hmac.compare_digest`, never `==`. The
   derivation cost is fixed by the stored parameters, so a wrong secret costs the
   same as a right one.

6. **The derivation is PBKDF2-HMAC-SHA256 from the standard library**, at a recorded
   iteration count, with the parameters stored **beside** each verifier rather than
   as a global constant. Raising the work factor later must not invalidate keys
   issued under the old one, and a parameter recorded per row is the only arrangement
   in which it does not.

7. **Revocation is state, never deletion** (I-3). A revoked key stays in the store
   with `revoked_at` set, so the audit trail of what it did survives it. Expiry is
   a separate column with the same effect and a different cause; the two are not
   collapsed, because "the operator withdrew this" and "this aged out" are different
   answers to an auditor.

8. **Rotation issues a new key and revokes the old one in the same transaction**,
   returning the new secret once. Rotation is not "change the secret of this key":
   an identifier whose secret changed would make every audit record ambiguous about
   which credential performed the action.

9. **A principal is (organization, subject, kind)**, resolved only from the verified
   credential. `kind` distinguishes a human `user` from a `service_account`, because
   `REQ-F-12-3` calls for service credentials specifically and because an auditor
   reading an action needs to know whether a person did it.

10. **Every credential lifecycle event is audited** — issue, rotation, revocation —
   with actor and time (`REQ-X-5`).

11. **Authentication failure is never specific.** Malformed, unknown, revoked,
    expired, and wrong-secret all produce the same 401. A response that distinguishes
    them is an oracle for enumerating valid key identifiers.

## Rationale

Rule 1 is the requirement restated, and it is the rule that survives a database
disclosure. Rules 2 to 4 exist because the naive alternative — an opaque token
looked up by its own hash — appears simpler and is not: a fast hash makes the lookup
indexable but makes the stored value a viable offline target, and a slow hash makes
the stored value safe and the lookup unindexable. Splitting the identifier from the
secret is what lets the lookup be fast and the verification be slow.

PBKDF2 rather than a memory-hard derivation is chosen for one stated reason: it is in
the standard library. `REQ-N-MAINT-5` requires every dependency to trace to a
documented requirement, and adding a cryptography dependency to this project — whose
threat here is offline attack on a 160-bit random secret, not on a human-chosen
password — buys resistance to hardware parallelism against a secret that has no
dictionary. The work factor is a defence in depth over an already-uniform secret,
which is why the parameters are stored rather than fixed: if that judgement is ever
revised, the ADR does not have to be.

Rule 11 costs a little diagnosability and removes an enumeration oracle. The
operator's diagnostic path is the audit trail, which records the specific reason;
the caller's is not.

## Consequences

- The secret appears exactly once, in the issue response. A caller who loses it
  rotates; there is no recovery path, and this is a property rather than a gap.
- Verification costs a key derivation per request. Accepted, and bounded: the work
  factor is a stored parameter, not a compiled-in constant.
- Tests cannot construct a principal by writing a token. Every test that touches an
  authenticated surface must mint a real credential, which is the point.
- The audit trail can attribute an action to a specific credential, not merely to a
  subject.

## Alternatives considered

| Alternative | Why rejected |
|---|---|
| Opaque random token, looked up by SHA-256 of itself | Indexable, but the stored value is then a fast-hash target: a database disclosure is a credential disclosure at the speed of SHA-256. |
| Opaque token, verified by deriving against every stored verifier | Correct and O(number of keys) per request, at a per-derivation cost deliberately made high. The platform would be building its own denial-of-service amplifier. |
| Signed tokens (JWT or similar) with no store lookup | Revocation becomes either impossible or a store lookup anyway, and `REQ-F-12-3` requires revocation. A revocable stateless token is a stateful token with extra steps. |
| Store the secret encrypted rather than derived | Reversible by definition, so the plaintext exists wherever the key does. I-2 says never retrievable, and "retrievable with the right key" is retrievable. |
| Password-style credentials for humans | Neither the canonical specification nor any requirement asks for interactive login. Adding one would add a session model, a reset flow and a phishing surface, none of them required. |
