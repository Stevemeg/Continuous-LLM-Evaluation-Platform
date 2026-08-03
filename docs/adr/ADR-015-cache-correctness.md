# ADR-015 — Result caching that cannot change an outcome

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M6.5 |
| Canonical basis | §9 (correctness-aware caching that never breaks reproducibility) |
| Constrained by | [ADR-014](ADR-014-run-identity-scope.md), [ADR-012](ADR-012-primary-datastore.md) |
| Requirements | `REQ-F-07-4`, `REQ-N-COST-1`, `REQ-F-07-1` |

## Context

`REQ-F-07-4` requires that caching "never changes the outcome of an evaluation relative to an uncached execution", and that the platform record whether a result was served from cache.

The failure this guards against is not a cache miss. It is a **hit that answers a different question than the one asked**, because the key covered less than the inputs did. The returned result is well-formed, plausible, and wrong, and nothing anywhere reports a problem. A cache is one of the few components whose defects are invisible by construction.

Canonical §15 names caching as a cost control, so the pressure to cache broadly is real: provider calls dominate the cost of every run.

## Decision

**Cache only deterministic configurations, keyed by a digest over every output-affecting input, with no overwrite.**

| # | Rule | Basis |
|---|---|---|
| C-1 | The key covers model configuration digest, prompt version digest, example content digest, and integration tier. | Anything that can change the output must change the key |
| C-2 | Building a key from partial inputs raises rather than substituting a default. | A key silently derived from whatever the caller passed is the invisible failure |
| C-3 | An unrecognised key field raises rather than being ignored. | Ignoring it means an input the caller believed was in the key was not |
| C-4 | **Only a configuration marked deterministic is eligible.** Enforced by a database trigger, not only in application code. | See below |
| C-5 | Determinism is inferred conservatively when not stated: a non-zero temperature without a seed is not deterministic. | Defaulting the other way admits sampled configurations |
| C-6 | Writes never overwrite an existing key. | The same key must not answer differently over time |
| C-7 | Cache entries are tenant-scoped under the same row-level security as everything else. | A cached completion is model output about the tenant's own data |
| C-8 | `run_sample.is_served_from_cache` records the fact per sample. | The second half of `REQ-F-07-4`; a cached result indistinguishable from a fresh one cannot be audited |

## Why determinism is a precondition, not an optimisation (C-4)

A sampled configuration returns a draw from a distribution. Caching it replaces every subsequent draw with one fixed draw — so the cached evaluation and the uncached evaluation differ in distribution even though each individual response is legitimate. **The outcome does change, and no cache key can fix it**, because the difference is not in the inputs.

This is why C-4 is enforced by a trigger rather than by a comment. The application is not the only thing that can insert into the table, and a rule that lives only in code is a rule that holds only while everyone remembers it.

## Why not cache non-deterministic results with a sample count

Storing *n* prior draws and returning one at random would preserve the distribution in the limit and is a real technique. It is rejected here because it makes the cache a statistical object: correctness then depends on *n*, on the draw policy, and on whether the evaluation is sensitive to correlation between samples. `REQ-F-07-4` says never changes, not converges. A cache with a correctness proof that depends on sample size is not a cache this platform can claim reproducibility on.

## Consequences

- The cache is useless for exactly the configurations that cost the most: high-temperature generation. This is the correct trade and it should be stated plainly rather than engineered around.
- Adding a new output-affecting input to the evaluation path without adding it to `KEY_FIELDS` is the one way to break this design. C-3 makes the omission loud in one direction; a test asserts key sensitivity for every field in the other.
- Cache entries are per tenant, so identical prompts across tenants are computed twice. That is the same trade ADR-013 makes for artifacts, for the same reason.
