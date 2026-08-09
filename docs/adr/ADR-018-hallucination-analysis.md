# ADR-018 — How a hallucination is classified, and why the judge's vocabulary stays narrow

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M9.3 |
| Canonical basis | §6, §12, §19, §25 |
| Requirements | `REQ-F-03-3`, `REQ-F-03-6`, `REQ-X-2`, `REQ-N-SEC-3` |
| Depends on | [ADR-004](ADR-004-judge-ensemble.md) and [ADR-017](ADR-017-judge-agreement.md), both accepted and unchanged by this record |

## Context

`REQ-F-03-3` requires hallucination analysis that distinguishes a claim
**unsupported** by the provided context from one **contradicted** by it.

The distinction is the requirement, not a refinement of it. They are different
failures with different owners:

| Finding | What happened | Who fixes it |
|---|---|---|
| Unsupported | The passages are silent on the claim | Retrieval — the evidence was not brought |
| Contradicted | The passages say otherwise | Generation — the model asserted against its evidence |

A single "hallucination score" cannot express which, and a team acting on one
when it was the other spends its effort on the wrong component. `REQ-F-03-6`
depends on the same distinction reaching the attribution.

## The decision that is not obvious

The natural implementation is to ask a judge for a category: *grounded*,
*unsupported*, or *contradicted*. It reads well and it is wrong here.

Phase 8's injection defence rests on two load-bearing properties, and the second
is that **a judge reply is a bounded score, an abstention, or nothing at all**.
There is no reply a judge can produce that means anything else — which is why an
injected `GATE: pass` parses to `failed` rather than to a verdict. Adding a
category vocabulary widens that parse. It reintroduces exactly the surface the
narrow parse closed, in exchange for convenience.

## Decision

### 1. Two orthogonal bounded judgements, not one categorical answer

Each claim is judged twice, by the ordinary ensemble, under two rubrics:

```
support        do the passages state or entail this claim?
contradiction  do the passages deny it?
```

Both are scores in `[0, 1]`. The parse is untouched.

### 2. They are not complements, and that is the point

A passage that is silent scores low on **both** — which is precisely the
`unsupported` case, and a formulation where contradiction is `1 − support`
cannot represent it at all. A passage that denies the claim scores high on
contradiction whatever support says, because real passages partly support and
partly deny.

The finding is the quadrant:

| | contradiction below threshold | contradiction at or above |
|---|---|---|
| **support at or above** | `grounded` | `contradicted` |
| **support below** | `unsupported` | `contradicted` |

### 3. Contradiction is tested first

A claim that is both partly supported and denied is `contradicted`. Denial is
the finding that says the answer is *wrong* rather than merely unevidenced, and
the more serious reading of ambiguous evidence is the safe one for a release
gate.

### 4. An escalated judgement is not a low score

If either ensemble escalated, the claim is `not_analysable` with a reason. An
escalation means the judges disagreed and a human was asked; reading it as
"unsupported" would answer a question that was explicitly deferred, and would
quietly remove the human `REQ-F-AG-4` put in the loop.

### 5. Both thresholds stay unset

Arguments without defaults, supplied per policy. With either absent the analysis
returns `not_analysable`. Same discipline as ADR-007's precision threshold and
ADR-017's agreement threshold, and for the same reason: a plausible default is a
number nobody chose, applied to every tenant, indistinguishable in the record
from one someone did choose.

## Alternatives rejected

**A three-way categorical judge output.** Simplest, and it widens the reply
parse. The cost is not hypothetical: the parse is one of the two defences that
hold without the model cooperating.

**One signed score, −1 contradicted to +1 supported.** Collapses "silent" and
"balanced evidence" onto the same midpoint, and those are different findings.

**Natural-language rationale parsed for keywords.** Reintroduces the untrusted
free-text path this architecture removed, and keyword matching over a model's
prose is the least reliable classifier available.

**Entailment scoring with a dedicated NLI model.** A reasonable future option
and a new dependency, a new model to version, and a second scoring path to keep
comparable. Revisit if calibration shows the two-judgement form is too coarse —
on measured evidence, not on preference.

## Consequences

- Hallucination analysis costs two ensemble judgements per claim. Visible in the
  cost record, and budgeted like any other judge call.
- Claim extraction is out of scope here: this record decides how a claim is
  classified once identified, not how an answer is split into claims. Splitting
  is a separate decision and is not made by implication.
- Until thresholds are configured, hallucination analysis reports
  `not_analysable` for everything — the platform abstaining rather than
  guessing, and visible rather than silent.
