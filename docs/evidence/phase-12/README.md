# Validation Evidence — Phase 12

Phase: **Phase 12 — Enterprise security, RBAC, multi-tenancy, audit/compliance hardening**
Milestones: M12.1 through M12.9

## Contents

| File | What it is |
|---|---|
| `plan.md` | The implementation plan, written before any Phase 12 code and kept as written |
| `check_phase12.py` | Phase validator, 31 checks. `python docs/evidence/phase-12/check_phase12.py .` |
| `selftest_phase12.py` | Plants 58 violations, proves each is caught, and verifies nothing survives |
| `dependency_scan.py` | Queries OSV for every declared dependency at its installed version |
| `dependency-scan.json` | Verbatim record of that scan, with the failure policy it enforced |
| `validation-output.txt` | Verbatim output of the validator — the run that **failed**, 31 PASS / 1 FAIL, exit 1, kept rather than replaced |
| `finalization-output.txt` | The same gate against the corrected tree: 32 PASS, exit 0 |
| `selftest-output.txt` | Verbatim output of the self-test |
| `test-output.txt` | Verbatim output of the test suite with coverage |

## What Phase 12 is for

The canonical roadmap gives Phase 12 as enterprise security, RBAC, multi-tenancy
and audit/compliance hardening. Every phase before it created records. This is
the phase that decides who may create them, who may read them, and what remains
provable afterwards.

The state it inherited is worth stating plainly, because it is the reason the
phase exists. Tenant isolation was real and enforced by the datastore: every
tenant-scoped table carried `organization_id NOT NULL`, ENABLE and FORCE row-level
security, a policy comparing the session's tenant context, and composite foreign
keys so a row could not even *link* across tenants. All of that worked. It was
evaluated against a tenant the caller had asserted, because the ingress derived
the organization by splitting a bearer token on a colon:

```python
org, _, subject = token.partition(":")
```

The isolation architecture was sound and its front door was open. `identity.py`
said so in as many words — *"until Phase 12 issues real principals"*.

## The credential

A credential is `clep_<organization>_<key>_<secret>`. The store holds a
PBKDF2-HMAC-SHA256 verifier over a per-key salt, at a work factor recorded
beside it rather than compiled in, so raising the factor later does not
invalidate keys already issued. There is no column a secret could occupy and no
operation that returns one twice.

The organization segment is the part worth explaining, because it looks like the
thing ADR-010 rule 3 forbids. It is not trusted. Isolation is enforced by the
store, so the credential lookup must itself happen inside a tenant context — the
context the request has not yet earned. So the session is opened on the
organization the credential *names*, and the key is looked up inside it. A
credential naming an organization that does not own it finds nothing, because
row-level security hides the row. The organization is established by the lookup
succeeding, not by having been presented. Nothing is returned on that path, so a
failed attempt discloses nothing.

`tests/test_identity_and_access.py` presents a real key relabelled with another
tenant's organization and asserts the refusal.

## The permission, in three places on purpose

Thirty-five permissions, declared three times: the CHECK constraint on
`clep.role_permission`, the `Permission` enum in the API contract, and
`rbac.PERMISSIONS`. The validator compares all three **as sets**. Duplication
that cannot drift silently is worth more than a single definition nothing checks:
the store refuses a row outside the vocabulary, the contract refuses to describe
one, and a disagreement is a permission the contract publishes that no role could
ever hold.

Five roles. Bindings at organization or project scope — two levels and no tree,
because a hierarchy needs a resolution order and a resolution order is where an
inherited deny quietly becomes an inherited allow.

## The rule that carries the phase

Every route reads the permission it requires from its own contract operation, and
`create_app` walks the routes FastAPI actually registered and refuses to start if
any lacks the guard.

This is the decision ADR-020 exists for. RBAC implementations do not usually fail
because a rule is wrong; they fail because a surface was added and nobody
attached a rule to it. That failure is silent, passes every test written for the
route, and is found by an auditor or an attacker. Making it *unexpressible* is
the only defence that survives a year of new routes.

The validator asks the question directly: it replaces the contract accessor so
that one operation declares no permission, builds the application, and requires
it to refuse.

## Erasure

Demote, then destroy, then verify — the order the schema comment fixed in Phase 4
and nothing had executed until now. Destroying first leaves a window in which a
run still claims reproducibility whose content is already gone.

Verification is obtained by **looking**: after the destruction, the surviving
objects are counted, and completion is refused unless every target is confirmed
gone. `ck_erasure_request__verified_on_completion` makes that refusal the store's
rather than the service's.

Derived artifacts go with the content, found through the source digest every
content-derived artifact carries — the reason that column exists. Gate evidence
does not, and cannot: the schema already makes it structurally free of erasable
content.

**What erasure does not prove.** The platform destroys every record it holds and
verifies their absence. It cannot confirm the object store destroyed the object,
because nothing speaks to the object store — D-3. Recorded as `SR-6`.

## D-1, closed without amending an accepted decision

`comparison.evaluator_version_id` was a plain `uuid` while every other
cross-table link carried the tenant. The composite foreign key is unformable:
`evaluator_version` is dual-scoped, a built-in carries a NULL `organization_id`
under the ADR-010 rule 4 exception, and a composite key from a NOT NULL column
cannot reference a NULL one.

Making it formable means narrowing rule 4, which is an accepted decision and
therefore a change proposal rather than a milestone. So the guarantee moved into
the store by the mechanism that remained: a trigger. It runs under the caller's
own tenant context and under FORCE row-level security, so another tenant's
evaluator version is not merely rejected — it is invisible, and the row is
refused for not existing. A built-in stays visible through the policy's NULL
branch and is accepted. That is exactly the rule the composite key would have
expressed, and the protection is now in the store rather than in the derivation
path, which is what the debt said was missing.

The same guard covers `evaluator_invocation`, which this phase adds with the
identical shape. Closing D-1 on one table while reintroducing it on the next
would have been no closure at all.

## Defects found by running

### The audit cursor dropped events that shared a millisecond

Paging filtered on the identifier alone. A ULID leads with a millisecond
timestamp and ends in eighty random bits, and every event a transaction writes
shares `occurred_at` — so identifiers written together do not sort in the order
the events happened, and a page boundary landing inside a transaction silently
skipped events.

Found by a test written to check paging worked. The cursor now compares
`(occurred_at, id)` as a pair, and the test that failed is kept as the test that
proves it, over nine events written in one transaction on purpose.

### A retention refusal became an unrelated error

`set_retention_policy` built its message by asking the database for the audit
floor — *after* the CHECK violation had already aborted the transaction. A clear
refusal became `InFailedSqlTransaction`. The floor is read first and the write
runs in a savepoint, so the caller's transaction survives the refusal.

### The role catalogue was keyed on a natural key

`clep.role` used `slug` as its primary key and `clep.role_permission` the
`(role_slug, permission)` pair. Both are in breach of N-2 — *the primary key is
`id`* — which has been a naming standard since Phase 3 and which the Phase 4
conformance checker enforces. It was reported by the complete validator, not by
the fast subset, which is why it survived until the first full run.

Both now carry `id uuid PRIMARY KEY`. The slug stays unique and stays the
foreign-key target, so a role binding still names `owner` rather than a UUID: a
surrogate key does not have to mean an opaque reference. The uniqueness the
natural keys carried is preserved as constraints and proved by attempting both
duplicates against the live database.

### The Phase 4 checker modelled two scope categories and the model has three

Not a Phase 12 defect — a defect Phase 12 was the first to trigger. The checker
knew the tenant root and dual-scoped rows carrying a nullable `organization_id`,
because those were the only two categories that existed when it was written.
`data-model.md` P-4 has always permitted a third: globally scoped tables with no
tenant column at all. When this phase realised the canonical global entities the
checker reported them as `P-1/N-4` defects. The specification allowed them; the
checker had never been told.

The exemption is an exact set of three names, checked in both directions — a
table declared global that acquires an `organization_id` is now also a failure —
and fifteen regression tests drive the checker over synthetic schemas to prove
that a table cannot become exempt by *resembling* a global one. Six impostors
are tested, because an exemption implemented as a prefix is one a future table
can satisfy by accident.

### The gate's secret scan failed on the test that proves secrets are removed

The first complete run returned **31 PASS, 1 FAIL, exit 1**. The failure was
P-20, the secret sweep, on three matches in `tests/test_privacy.py`: a
private-key header, a JWT, and a password in a connection string.

None is a credential. They are the inputs that prove `redact_credentials`
removes each shape — which makes that file the one file in the repository
guaranteed to contain credential-shaped strings, and the scanner entirely right
to flag it. A scanner that made an exception for files whose names suggest they
are tests would be a scanner with a way around it.

Fixed in the working tree rather than excused: every vector is now assembled
from parts, so no contiguous run of characters in the file matches a secret
pattern while `redact_credentials` still receives the identical complete string.
Assembly introduces its own risk — a vector split wrongly stops matching the
pattern it stands for, the redactor legitimately ignores it, and the test passes
because the string was never removable in the first place — so a further test
asserts each assembled vector still matches the detector's own patterns.

The superseded blob is disclosed rather than removed, because removing it means
rewriting history and this project's Git policy forbids that. The disclosure
states the difference from the two spike entries beside it: those are unremovable
because they are in *published* history; this one is unpublished and would be
removable by a rewrite, and the reason it stays is the policy rather than the
impossibility.

### The build was running a setuptools with eight advisories

The dependency scan's first real execution found setuptools 65.5.0 in the build
environment, carrying eight OSV advisories including a command injection through
a package URL — and *below the floor `pyproject.toml` already declared*. The
declaration had been right and unenforced. Upgraded, scan clean, floor raised to
78.1.1, and the reasoning recorded next to it: a floor cannot know about
tomorrow's advisory, which is why the scan is the enforceable part.

### Ten validator checks were weaker than they looked

The self-test's first run caught ten, and its second caught an eleventh. They are
worth listing because they are all the same failure the validator exists to
prevent:

| Check | What was wrong |
|---|---|
| `rebuild_fast` | Excised everything from P-1 to P-11, silently taking P-2, P-3, P-4 and P-6 with it — so the schema-conformance check had never had a plant able to reach it |
| P-35 | Gate evidence is excluded at three statements; removing one left two satisfying a substring search |
| P-35 | The surviving-object query appears twice; same story |
| P-35 | A constraint renamed to `..._removed` still contains the name a search looks for |
| P-35 | Word-bounding that name was still not enough — `COMMENT ON CONSTRAINT ck_…` contains `CONSTRAINT ck_…`, so the check read the comment describing the constraint that had been renamed away |
| P-31 | The work-factor guard was probed through verification, where removing it changes no answer |
| P-34 | The audit writer was checked by searching its source, which a plant defeated by passing `None` for both values |
| P-34 | The cursor check searched for `(occurred_at, id)`, which also appears in the subselect that resolves the cursor |
| P-31 | Constant-time comparison is not behaviourally observable and was being probed as if it were |
| P-36 | The bucket arithmetic lives in Lua that Redis executes, and the check drove an in-memory stand-in |
| P-32 | The probe for an undeclared permission mutated the loaded contract, which `lru_cache(maxsize=1)` evicted the moment the application asked for its title |

Every one is corrected in the check rather than in the plant. Nine checks across
three earlier phases have been lost to source-text matching; these are the tenth
through twentieth, caught before they were relied on.

## What is static, and why

Two checks read rather than execute, and both say so at the point of use.

**No disabling switch exists anywhere in the package.** Absence is not
exercisable — you can only test the bypasses you thought of. What is
mechanically checkable is that no environment variable, flag or keyword with a
disabling name exists in `src/clep` at all, which is where such a thing would
have to live to be reachable from a deployment.

**Constant-time comparison.** `==` and `hmac.compare_digest` return identical
answers and differ only in a timing channel no test on this machine can measure
reliably. The bound function is read through `inspect`, so it reads the code
actually in force rather than a regular expression's guess at where the function
ends.

## Real-model evidence

**None was produced in this phase, and none was needed.** Nothing in Phase 12
depends on a model's output: a credential either verifies or does not, a route
either refuses or does not, an erasure either destroyed the rows or did not.
Every judge-facing change here — the redaction on the path into a judge prompt —
is asserted on the prompt that would be *sent*, which is the correct place to
assert it and needs no provider.

The hosted-provider risk carried since Phase 9 is unchanged and remains open.

## What Phase 12 did not close

| Item | Status |
|---|---|
| Judge accuracy, calibration, agreement and hallucination thresholds | Uncalibrated. Not assigned to this phase and no number was invented |
| Hosted-provider execution | Unvalidated. No credential was fabricated |
| Scheduled execution against a live real model | Still not combined |
| D-3 — object-store adapter | Open, Phase 14. Widened: erasure verifies the platform's records and not the store's bytes (`SR-6`) |
| D-4 — the gate's latency criterion | Open, unchanged |
| D-5 — `REQ-F-12-1` names three scopes and two exist | **Raised here.** `Environment` has no table and nothing is scoped by one; adding one nothing references would satisfy the word and none of its meaning |
| ADR-006 rule 4 — isolation outside the evaluator's process | Open, Phase 14 (`SR-2`). A granted evaluator is permitted, not contained |
| At-rest encryption | A deployment property, Phase 14. In-transit is enforced at startup |
| `REQ-N-COMP-4` | Deferred to Phase 15, unchanged |
| Independent audit-retention *expiry* | The floor is stored and constrained; nothing yet expires anything (`T7`) |
| Authentication failures per attempt | Counted, deliberately not audited — an unauthenticated caller must not be able to grow the one store nobody may prune (`SR-7`) |
