# ADR-003 — Model and provider abstraction

| Field | Value |
|---|---|
| Status | **Accepted — decided on executed spike evidence** |
| Milestone | M2.3 proposed; decided in the Technology Spike Sprint |
| Canonical basis | §15 (evaluate an aggregation library versus internal adapters), §19 |
| Requirements | `REQ-F-02-2`, `REQ-F-02-4`, `REQ-F-02-6`, `REQ-F-07-6`, `REQ-N-REL-4`, `REQ-N-SEC-5` |
| Evidence | [`../evidence/spike-sprint/`](../evidence/spike-sprint/) — `spike_provider_abstraction.py` |

## Context

The Provider Gateway is the sole egress to model providers, and the requirements on it are unusually demanding for an abstraction layer: per-**sample** token and cost accounting (`REQ-F-07-6`), defined behaviour for four named failure modes **individually** (`REQ-N-REL-4`), isolation of one candidate's failure from its siblings (`REQ-F-02-6`), hosted and self-hosted endpoints as first-class citizens (`REQ-F-02-4`), and credentials that are never persisted in plaintext, logged, or present in reports (`REQ-N-SEC-5`).

This ADR previously declined to decide, on the grounds that whether a third-party aggregation library exposes sufficient error granularity is an empirical question about its behaviour under induced failure. The spike has now run.

## Decision

**Internal adapters behind a project-owned port.** The aggregation library is **not** adopted, in either the direct or the wrapped form.

## What the spike found

Three approaches, four questions, three endpoints — a real commercial provider, a real self-hosted inference server, and a fault endpoint the spike controls. Full output in [`../evidence/spike-sprint/s2-output.txt`](../evidence/spike-sprint/s2-output.txt).

| | A — library, direct | B — internal adapter behind the port | C — library behind the port |
|---|---|---|---|
| Per-call usage retrievable and reconciling | yes | yes | yes |
| Failure modes distinguished | **2 / 4** | **4 / 4** | **3 / 4** |
| …on a structural signal rather than message text | 2 / 4 | 4 / 4 | 3 / 4 |
| Failing candidate isolated from siblings | yes | yes | yes |
| Credential leaked, default logging | no | no | no |
| Credential leaked, **debug logging on** | **yes** | no | **yes** |
| Decision rule | REJECTED | **SURVIVES** | REJECTED |

### The library cannot tell an outage from a malformed response

This is the finding the decision rests on, and it is mechanical rather than interpretive. The spike records every structured signal an adapter could branch on — exception class, status code, `__cause__`, error code, provider — and then reports which failure modes produced *identical* signals:

```
A: malformed response == outage  (same class, status and cause)
B: none - all four modes carry distinct structured signals
C: malformed response == outage  (same class, status and cause)
```

A connection to a dead port and an HTTP 200 whose body is truncated JSON both arrive as `InternalServerError` with `status_code = 500` and no `__cause__`. The only thing that differs is the message text: `"Connection error."` against `"Expecting value: line 1 column 26"`.

Two further details make this worse than a missing feature:

- **The status code is synthesised, not observed.** The malformed case was an HTTP **200**. The outage case had no HTTP exchange at all. The library reports `500` for both.
- **The message is wrong, not merely unstructured.** The malformed-body case is described as `"Connection error."` when the connection succeeded.

`REQ-N-REL-4` requires defined, tested behaviour for these two conditions *individually*, and they are conditions that call for opposite responses: an outage should be retried against the same endpoint, a malformed response should not be retried blindly and indicates a candidate or provider defect. Discriminating them by substring is a control-flow decision resting on a vendor's prose, and it breaks silently the first time that prose is reworded.

**Approach C's 3 / 4 is an artifact, not a capability.** It scored one higher than A only because its fallback branch happens to name one of the two colliding modes. It classified `malformed` correctly and `outage` incorrectly; had the fallback named the other, the scores would have swapped. The collision analysis is what exposes this, and it is why the raw count alone would have been misleading.

### The library leaks the API key when debug logging is on

| Approach | Default logging | `LITELLM_LOG=DEBUG` | Log volume |
|---|---|---|---|
| A | no leak | **canary found on stdout** | 16,973 bytes |
| B | no leak | no leak | 4,540 bytes |
| C | no leak | **canary found on stdout** | 16,973 bytes |

`REQ-N-SEC-5` says a credential is never logged. It does not say "never logged unless someone was debugging" — and enabling verbose logging is precisely what an operator does when runs start failing, which is precisely when credentials are most likely to be in play. Wrapping the library in the project's own port does not fix this: approach C leaks identically, because the logging happens inside the library, below the port.

**The detector was self-tested before any of these results were believed.** The canary was planted on all five inspected surfaces and found on all five. A leak detector that has never reported a leak has not been shown to work.

### Per-call usage — answered, with a gap stated plainly

All three approaches retrieved per-call token counts and reconciled them against the provider's own usage object, taken off the wire by a separate raw call:

| Endpoint | Provider-reported prompt tokens | All three approaches |
|---|---|---|
| self-hosted llama.cpp, Qwen2.5-0.5B-Instruct | 36 | 36 |
| fault endpoint | 7 | 7 |

Prompt tokens are compared exactly, because tokenisation of a fixed prompt is deterministic and prompt tokens drive input cost. Completion tokens are not compared across two separate calls, because sampling may legally return a different count; what is required instead is that a completion count is reported and that the three figures are self-consistent. One approach did return 3 completion tokens where the reference call returned 2 — that is the model sampling differently, not the abstraction losing data, and treating it as a failure would have been testing the model.

**Evidence gap, not closed:** no successful call to a *hosted commercial* provider was made. Both credentials available in the environment were out of quota, so usage reconciliation against a paid provider — and against a provider whose usage schema differs from the OpenAI shape, such as one reporting `input_tokens`/`output_tokens` — remains untested. This does not affect the decision, because approach B parses the provider's response itself and its correctness for a new schema is a matter of writing and testing one adapter. It is recorded as an outstanding verification obligation for the first hosted provider integrated.

### A finding that neither approach anticipated

Both hosted credentials were exhausted, and the way each provider said so is itself evidence:

| Provider | HTTP status | `error.type` |
|---|---|---|
| OpenAI | **429** | `insufficient_quota` |
| Perplexity | **401** | `insufficient_quota` |

The same semantic condition arrives as a rate-limit status from one provider and an authentication status from another. Two consequences follow:

1. **Status code alone is an unsound classifier.** A `429` does not mean "retry later"; it may mean "this account will never succeed again until someone pays". A `401` does not always mean "bad credential".
2. **This spike found a defect in the approach it recommends.** The internal adapter as written maps `429 → ProviderRateLimited`, which the port treats as retryable. Against a real exhausted OpenAI account it would retry forever, burning wall-clock on a condition that cannot resolve itself. That is a live bug in approach B, found before any of it was written into the product, which is what the spike was for.

## Consequences

- **The failure taxonomy gains a fifth member**, `QuotaExhausted`, distinguished from rate limiting by being **terminal** rather than retryable. Classification is on `error.type` / `error.code` first and HTTP status only as a fallback — the reverse of the obvious implementation.
- **`REQ-N-REL-4`'s enumeration of four failure modes is incomplete** relative to observed provider behaviour. This is flagged for external review as a recommendation; the requirement is approved and is not amended here. Handling a fifth mode exceeds the requirement rather than changing it.
- One adapter per provider is project code, with tests that induce all five modes against a controllable endpoint. The fault endpoint built for this spike is the seed of that test fixture.
- Self-hosted endpoints are first-class by construction (`REQ-F-02-4`): the adapter takes a base URL, and the spike drove a real local inference server through it unmodified.
- Credential handling stays inside project code, where `REQ-N-SEC-5` can be enforced and tested rather than hoped for.
- **Cost, honestly:** every new provider is now our work. The aggregation library's genuine value — many providers, one call — is real and is being given up. It is given up because it also normalises away the error detail this product's core requirement depends on, and because it prints the key when asked to explain itself.

## Re-evaluation trigger

Reopen this ADR if the number of providers to support grows past the point where per-provider adapters dominate maintenance, **and** an aggregation library can be shown, by re-running this spike, to distinguish all five failure modes on structural signals and to keep credentials out of its debug output.

## Interim architectural constraint — now permanent

The project-owned port stays between the domain and any provider code. `REQ-N-OBS-3` and canonical §25 both point the same way: a dependency must not define the domain's model of a provider call. That constraint made this deferral safe, and it is what would make a future reversal cheap.

## Alternatives considered

**Adopt the library and accept message-text classification.** Rejected: it makes `REQ-N-REL-4` depend on a vendor's prose, and the prose observed here was actively wrong about what happened.

**Adopt the library and scrub credentials from its logs downstream.** Rejected: the leak is emitted inside the library and reached stdout directly. Filtering another component's log output is a control that fails open, and it would have to keep working across upgrades of a library that has no obligation to preserve its log format.

**Use the library only for transport and parse responses ourselves.** This is approach C in all but name, and C leaked the credential and inherited the collision.
