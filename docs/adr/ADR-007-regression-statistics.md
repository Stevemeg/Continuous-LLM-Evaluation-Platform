# ADR-007 — Regression statistics and quality-gate semantics

| Field | Value |
|---|---|
| Status | **Accepted** (method class), on spike evidence. Parameter values gated on real-data calibration. |
| Milestone | M2.3 |
| Canonical basis | §9, §11, §19, §25 |
| Requirements | `REQ-F-08-1`, `REQ-F-08-2`, `REQ-F-08-3`, `REQ-F-08-4`, `REQ-F-08-7`, `REQ-X-3` |
| Evidence | [`../evidence/phase-2/spikes/spike-regression-statistics-output.txt`](../evidence/phase-2/spikes/spike-regression-statistics-output.txt) — executed, seed `20260730` |

## Context

This is the ADR the product's differentiation rests on. `REQ-F-08-2` requires uncertainty and effect size; `REQ-F-08-3` requires minimum-sample guidance and refusal below it; `REQ-F-08-4` makes "insufficient evidence" distinct from "no change"; canonical §25 rejects "arbitrary quality-gate scores without baselines or uncertainty".

Evaluation comparison is **paired**: baseline and candidate are scored over the same dataset version, so the unit of analysis is the per-sample difference.

## Evidence

A spike was executed comparing three method classes against simulated data with known true effects across sample sizes 20, 50, and 200. Null calibration was measured before interpreting any result: mean paired difference under a true effect of zero was `-0.00029` with a clipping rate of 18 in 2000, so the null is null and the verdict rates are interpretable.

**A defect in the spike itself was found and fixed before the results were used.** The first score model drew baselines near the upper bound, so positive noise was clipped at the ceiling while negative noise was not, biasing the mean paired difference negative even at zero true effect. Every method's regression rate was inflated. The model was re-centred and the calibration check added.

Measured behaviour (100 trials per cell, from the recorded output):

| Finding | Result |
|---|---|
| False positives at zero true effect | naive 17/5/0 at n=20/50/200; bootstrap 0/6/4; permutation 7/6/8 |
| Abstention | Only the bootstrap can abstain. At n=20 it abstains in roughly 9 trials in 10 across all effect sizes |
| Detection, moderate effect (−0.05) | bootstrap 6/100 at n=20, 91/100 at n=50, 100/100 at n=200 |
| Detection, noise-scale effect (−0.01) | 1/100, 10/100, 41/100 — never reaches an 80-in-100 rate at n ≤ 200 |

## Decision

**The paired bootstrap percentile interval on the mean paired difference is the primary comparison method.** Classification is three-way and derived from the interval:

| Interval condition | Verdict |
|---|---|
| Width exceeds the per-metric precision threshold | **insufficient evidence** (`REQ-F-08-4`) |
| Entirely below zero | **regression** |
| Entirely above zero | **improvement** |
| Contains zero, width within precision | **no change** |

Paired standardised effect size is reported with every comparison. The statistical method is versioned and recorded with each gate decision (`REQ-F-08-7`).

## Rationale — what actually decided it

**Not the false-positive rate.** At n=200 the naive threshold produced the *fewest* false positives, because a fixed threshold becomes conservative once the sample mean concentrates. That is not a virtue: the same constant makes it fire on pure noise in roughly one run in six at n=20, and makes it structurally blind to any real regression smaller than the threshold at any sample size. Its behaviour is governed by an arbitrary constant rather than by the evidence — canonical §25's anti-pattern exactly.

**Abstention decided it.** At n=20 the bootstrap declines to classify in about nine trials in ten, including when the true effect is large, because the interval genuinely cannot distinguish anything. At the same sample size the naive method issues confident regression verdicts on noise. One method reports that it cannot tell; the other guesses. `REQ-F-08-4` requires the first.

The permutation test controls false positives acceptably and yields a p-value, but returns no interval and no abstention state, so it satisfies neither `REQ-F-08-2` nor `REQ-F-08-4` on its own. It is retained as a cross-check for a future spike, not as the primary method.

## Parameter values deliberately not set

| Parameter | Why not now |
|---|---|
| Confidence level | One value was used in the spike; the production value is a policy choice needing real data. |
| Per-metric precision threshold | Governs the abstention boundary and must be per metric. Setting it from simulated data would be the arbitrary constant this ADR rejects. |
| Minimum-sample table | Derivable from detection curves on **real** evaluation data. The spike shows the shape, not the values. |
| Resample count | Trades precision against latency; needs measurement against `REQ-N-PERF-1`. |

## Consequences

- Gate latency includes resampling cost, which bears on `REQ-N-PERF-1` and must be measured.
- The gate must represent and report abstention as a normal outcome, including in CI exit status.
- A noise-scale regression is **not detectable at small sample sizes and the product must say so** rather than reporting a small delta as a verdict. This is a product-visible limitation and belongs in user-facing documentation.
- Minimum-sample guidance must be per metric and per effect size, not global.

## Limitations of the evidence

Simulated scores from one distribution family, one noise level, independent samples, single confidence and precision settings, 100 trials per cell so rates carry sampling error of a few counts. This supports a **method-class** decision only. The percentile bootstrap's known small-sample undercoverage should be re-examined against a bias-corrected variant before parameter values are fixed.
