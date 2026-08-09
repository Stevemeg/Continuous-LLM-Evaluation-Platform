# ADR-017 — What judge disagreement is measured as, and what an ensemble may be composed of

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M8.3 |
| Canonical basis | §8, §17, §19, §25 |
| Requirements | `REQ-F-AG-2`, `REQ-F-AG-3`, `REQ-F-AG-4`, `REQ-F-11-4`, `REQ-F-08-6` |
| Depends on | [ADR-004](ADR-004-judge-ensemble.md), which is accepted and unchanged by this record |

## Context

ADR-004 decided the *shape* of consensus: a configurable rule producing a
verdict **and** a disagreement measure, deterministic evaluators never voting,
low agreement escalating and terminating rather than retrying. It recorded three
things as deliberately not decided, because each needs real judge outputs:

| Left open by ADR-004 | Why |
|---|---|
| The agreement metric | Choosing between inter-rater measures requires real judge outputs |
| The escalation threshold | A threshold without calibration data is the arbitrary constant §25 rejects |
| Ensemble size and composition | Cost and marginal value trade off; both need measurement |

Implementation cannot proceed on all three being open. A consensus function must
compute *something*, and if the choice is made in code rather than in a record it
becomes a methodology nobody decided — the failure canonical §19 exists to
prevent.

This record closes the two that are structural and leaves the third exactly
where ADR-004 left it. The distinction is the same one ADR-016 drew against
ADR-007: **the shape of the measurement is an engineering decision; the constant
is a calibration result.**

## Decision

### 1. Disagreement is the range of the scoring votes

For scoring votes `s₁ … sₙ`, disagreement is `max(sᵢ) − min(sᵢ)`, in score units.

| Property | Why it decided this |
|---|---|
| Monotone in the worst dissenter | One judge disagreeing is precisely the signal `REQ-F-AG-4` escalates on. Mean absolute deviation hides one dissenter among four agreeable ones — the more judges, the better it hides |
| Defined at n = 2 | Ensembles will be small. Krippendorff's α and the intraclass correlation are undefined or degenerate on two raters with no variance across items |
| Same units as the score | A threshold of `0.2` means "the judges are within a fifth of the scale". A threshold on α means nothing without knowing α |
| No distributional assumption | The measure makes no claim about how judge error is distributed, because nothing has measured that yet |

The cost is stated rather than hidden: the range ignores the votes between the
extremes, so nine agreeing judges and one outlier score exactly as badly as five
and five. That is the *intended* behaviour under `REQ-F-AG-4` — a judge that
disagrees is a reason to look, not a vote to be outnumbered — but it means the
measure is not an estimate of ensemble quality and must not be read as one.
Per-judge deviation from the median is exposed separately for that purpose.

### 2. An ensemble must be able to disagree with itself

Three composition rules, each enforced at construction rather than checked at
judgement time:

1. **At least two judges.** One judge is a single judge treated as ground truth.
2. **At least two distinct model configurations.** ADR-004 D-1. A slug is not
   evidence of difference; endpoint and model together are.
3. **No configuration holds a strict majority of the seats.** An ensemble in
   which one model configuration can outvote every other configuration is that
   configuration with witnesses. This rule is new here, and it is the reason
   ensemble *size* can remain uncalibrated: whatever size is chosen, the
   composition cannot collapse into a single opinion.

### 3. The escalation threshold and the minimum vote count stay unset

Both are arguments without defaults, supplied per ensemble. An ensemble with no
threshold configured **escalates every judgement**, with `no_threshold_configured`
as the reason.

This is deliberately inconvenient. The alternative — a plausible-looking default
such as `0.2` — would be applied to every tenant, would be cited in gate
decisions, and would be indistinguishable in the record from a value someone
chose. ADR-007 established the pattern and ADR-016 kept it; the platform abstains
until a person supplies the number.

### 4. Fewer than two scoring votes is reported at maximum disagreement

A single vote has a range of zero. Reporting zero would be the strongest
available statement of consensus made on the weakest available evidence, and
I-22 requires *some* measure on every result.

So a judgement with fewer than two scoring votes reports disagreement at `1` —
the maximum two scores in `[0, 1]` can differ by — with a separate
`disagreement_measured` flag set false, and escalates with
`insufficient_scoring_votes`. The flag exists because "they disagreed
completely" and "there was nothing to compare" are different facts, and a single
number cannot carry both.

### 5. The verdict is the median of the scoring votes

Computed only when the ensemble agreed, where median and mean are close by
construction. The median is chosen anyway because the mean lets one extreme vote
move the number, and nothing in a consensus rule should reward being extreme.
Quantised to the store's resolution, exact in `Decimal`, so a verdict never
depends on binary representation.

### 6. Confidence is a transformation of the disagreement, and says so

`confidence = 1 − disagreement`, present only when the disagreement was measured.
`REQ-F-AG-3` requires a confidence signal to be exposed; it does not require a
probability, and this is not one. Calling a rescaled range a probability would
be a stronger claim than the data supports — the same error ADR-007's spike found
the fixed-threshold method making.

## What this record still does not decide

| Open | Owner |
|---|---|
| The escalation threshold value | Calibration against real judge outputs |
| The minimum scoring-vote count | Same |
| Ensemble size | Same, plus cost measurement |
| Whether the range should be replaced once judge-error distributions are known | A future ADR, on measured evidence, superseding §1 of this one |

## Alternatives rejected

**Standard deviation of the votes.** Divides by `n`, so adding agreeable judges
lowers disagreement while the dissenter is still there. That is a mechanism for
making escalation go away by buying more judges.

**Krippendorff's α.** The literature-standard answer, and the right one once
there is data. Undefined for the degenerate cases small ensembles produce
constantly, requires a distance metric decision of its own, and yields a number
whose threshold cannot be reasoned about before calibration — three unsupported
decisions in place of one.

**Majority vote on a discretised score.** Discards the magnitude of the
disagreement, which is the thing `REQ-F-AG-3` requires exposing.

**Average the votes and report the spread as diagnostics.** ADR-004 rejected
this by name. Averaging is the mechanism by which disagreement disappears.

## Consequences

- Every ensemble is, at construction, capable of producing a disagreement. An
  ensemble that could not is a configuration error rather than a judgement that
  always agrees.
- Until a threshold is configured, judge-backed evaluation escalates everything
  and gates that depend on judge agreement abstain. Both are visible, and
  neither is silent success.
- `judge_agreement` becomes a real gate criterion source: the per-sample
  disagreement is a metric where lower is better, paired between baseline and
  candidate like any other. Judgements whose disagreement was not measured are
  excluded rather than counted as zero, per `REQ-F-08-5`.
- Historical memory accumulates per-judge deviation from the median, which is
  the bias signal canonical §8 asks for and the input any future calibration
  will need.
