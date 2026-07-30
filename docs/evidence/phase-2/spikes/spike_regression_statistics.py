"""Spike: regression comparison methods for the quality gate (ADR-007).

QUESTION
    Which comparison method can (a) detect a real quality regression, (b) refuse
    to call an inconclusive delta a regression, and (c) report uncertainty and
    effect size -- as REQ-F-08-1 through REQ-F-08-4 require?

WHY A SPIKE
    Canonical section 25 rejects "arbitrary quality-gate scores without baselines
    or uncertainty", and REQ-F-08-4 makes "insufficient evidence" a distinct
    outcome. Whether a method actually delivers that is an empirical question, so
    choosing one by preference would be the failure canonical section 22 forbids.

METHOD
    Evaluation scores are PAIRED: baseline and candidate are scored on the same
    dataset version, so the unit of analysis is the per-sample difference. Three
    candidate methods are run against simulated data with a KNOWN true effect,
    across sample sizes, and their verdict rates are measured.

    Method N (naive threshold)   -- the canonical anti-pattern, included as a control
    Method B (paired bootstrap)  -- percentile CI on the mean paired difference
    Method P (permutation)       -- sign-flip test on paired differences

    Ground truth is known by construction, so every verdict is scoreable.

DEPENDENCIES
    Standard library only. Deterministic under a fixed seed.

Usage: python spike_regression_statistics.py
"""
import math
import random
import statistics
import sys

sys.stdout.reconfigure(encoding="utf-8")

SEED = 20260730
TRIALS = 100
BOOTSTRAP = 300
ALPHA = 0.05
NAIVE_THRESHOLD = 0.02      # declare regression if mean drops by more than this
PRECISION = 0.05            # CI wider than this cannot distinguish anything useful
MIN_DETECT_RATE = 0.80      # detection rate required to call a sample size adequate

EFFECTS = [
    (0.00, "no real change"),
    (-0.01, "tiny (noise-scale)"),
    (-0.05, "moderate"),
    (-0.15, "large"),
]
SAMPLE_SIZES = [20, 50, 200]

# Per-sample scores resemble a bounded quality metric.
#
# DEFECT FOUND AND FIXED DURING THIS SPIKE: the first model drew baselines from
# Beta(8,2), concentrated near 1.0. Candidate scores are baseline + effect +
# noise, clipped to [0,1], so positive noise was clipped at the ceiling while
# negative noise was not. That induced a systematically negative mean paired
# difference even when the true effect was zero -- the null was not null, and
# every method's regression rate was inflated. Beta(5,3) centres the
# distribution away from the boundary. The null calibration is now measured and
# reported rather than assumed.
SCORE_A, SCORE_B = 5.0, 3.0
NOISE_SD = 0.08


def make_pair(n, effect, rng):
    """Return paired (baseline, candidate) score lists with a known true effect."""
    base, cand = [], []
    for _ in range(n):
        b = rng.betavariate(SCORE_A, SCORE_B)
        c = b + effect + rng.gauss(0.0, NOISE_SD)
        base.append(b)
        cand.append(min(1.0, max(0.0, c)))
    return base, cand


def diffs(base, cand):
    return [c - b for b, c in zip(base, cand)]


# ---------------------------------------------------------------- methods
def method_naive(d, rng):
    """Threshold on the mean difference. No uncertainty, no abstention."""
    m = statistics.fmean(d)
    return ("regression" if m < -NAIVE_THRESHOLD else "no change"), m, None


def method_bootstrap(d, rng):
    """Percentile CI on the mean paired difference.

    Three-way verdict:
      regression          -- the whole interval lies below zero
      insufficient        -- the interval is too wide to distinguish anything
      no change           -- interval contains zero and is tight enough to say so
    """
    n = len(d)
    means = []
    for _ in range(BOOTSTRAP):
        means.append(statistics.fmean(rng.choices(d, k=n)))
    means.sort()
    lo = means[int((ALPHA / 2) * BOOTSTRAP)]
    hi = means[min(BOOTSTRAP - 1, int((1 - ALPHA / 2) * BOOTSTRAP))]
    width = hi - lo
    if width > PRECISION:
        verdict = "insufficient"
    elif hi < 0:
        verdict = "regression"
    elif lo > 0:
        verdict = "improvement"
    else:
        verdict = "no change"
    return verdict, statistics.fmean(d), (lo, hi)


def method_permutation(d, rng):
    """Sign-flip permutation test on paired differences."""
    n = len(d)
    obs = statistics.fmean(d)
    count = 0
    for _ in range(BOOTSTRAP):
        flipped = [x if rng.random() < 0.5 else -x for x in d]
        if statistics.fmean(flipped) <= obs:
            count += 1
    p = (count + 1) / (BOOTSTRAP + 1)
    verdict = "regression" if (p < ALPHA and obs < 0) else "no change"
    return verdict, obs, p


def cohen_dz(d):
    """Standardised paired effect size."""
    if len(d) < 2:
        return float("nan")
    sd = statistics.stdev(d)
    return statistics.fmean(d) / sd if sd > 0 else float("inf")


# ---------------------------------------------------------------- run
print("=" * 78)
print("SPIKE — REGRESSION COMPARISON METHODS (ADR-007)")
print("=" * 78)
print(f"seed={SEED}  trials/cell={TRIALS}  bootstrap/permutation resamples={BOOTSTRAP}")
print(f"alpha={ALPHA}  naive_threshold={NAIVE_THRESHOLD}  ci_precision={PRECISION}")
print(f"score model: Beta({SCORE_A},{SCORE_B}) baseline, gaussian noise sd={NOISE_SD}")
print("paired design: baseline and candidate scored on the same samples")
print()

table = []
for effect, label in EFFECTS:
    for n in SAMPLE_SIZES:
        rng = random.Random(SEED + n + int(effect * 1000))
        tally = {m: {} for m in ("naive", "bootstrap", "permutation")}
        dz = []
        for _ in range(TRIALS):
            base, cand = make_pair(n, effect, rng)
            d = diffs(base, cand)
            dz.append(cohen_dz(d))
            for name, fn in (("naive", method_naive),
                             ("bootstrap", method_bootstrap),
                             ("permutation", method_permutation)):
                v, _, _ = fn(d, rng)
                tally[name][v] = tally[name].get(v, 0) + 1
        table.append((effect, label, n, tally, statistics.fmean(dz)))

# Null calibration: with a true effect of zero the mean paired difference must be
# indistinguishable from zero, and the clipping rate must be negligible. If either
# fails, no verdict rate below is interpretable.
_rng = random.Random(SEED)
_null_means, _clipped = [], 0
for _ in range(2000):
    _b = _rng.betavariate(SCORE_A, SCORE_B)
    _raw = _b + _rng.gauss(0.0, NOISE_SD)
    if _raw > 1.0 or _raw < 0.0:
        _clipped += 1
    _null_means.append(min(1.0, max(0.0, _raw)) - _b)
print("NULL CALIBRATION (true effect = 0)")
print(f"  mean paired difference: {statistics.fmean(_null_means):+.5f}  "
      f"(must be ~0; a negative value indicates ceiling-clipping bias)")
print(f"  clipping rate: {_clipped}/2000 samples")
print()

hdr = f"{'true effect':>12} {'n':>5} {'method':>12} {'regression':>11} {'no change':>10} {'insufficient':>13} {'improvement':>12}"
print(hdr)
print("-" * len(hdr))
for effect, label, n, tally, mdz in table:
    for name in ("naive", "bootstrap", "permutation"):
        t = tally[name]
        print(f"{effect:>12.2f} {n:>5} {name:>12} "
              f"{t.get('regression', 0):>11} {t.get('no change', 0):>10} "
              f"{t.get('insufficient', 0):>13} {t.get('improvement', 0):>12}")
    print(f"{'':>12} {'':>5} {'mean paired dz':>12} {mdz:>11.3f}")
    print()

# ---------------------------------------------------------------- findings
print("=" * 78)
print("FINDINGS")
print("=" * 78)

fp = {}
for effect, label, n, tally, _ in table:
    if effect != 0.0:
        continue
    for name in ("naive", "bootstrap", "permutation"):
        fp.setdefault(name, []).append((n, tally[name].get("regression", 0)))
print("1. FALSE POSITIVES under a true effect of zero (regressions declared out of")
print(f"   {TRIALS} trials — lower is better):")
for name, vals in fp.items():
    print(f"     {name:>12}: " + ", ".join(f"n={n} -> {c}" for n, c in vals))

print()
print("2. ABSTENTION. Only the bootstrap method can return 'insufficient':")
for effect, label, n, tally, _ in table:
    ins = tally["bootstrap"].get("insufficient", 0)
    if ins:
        print(f"     effect={effect:+.2f} n={n:>3}: insufficient in {ins}/{TRIALS} trials")

print()
print("3. DETECTION of a real regression (bootstrap), and the smallest sample size")
print(f"   reaching a {int(MIN_DETECT_RATE * 100)} in 100 detection rate:")
for effect, label in EFFECTS:
    if effect == 0.0:
        continue
    row = []
    adequate = None
    for e, l, n, tally, _ in table:
        if e != effect:
            continue
        det = tally["bootstrap"].get("regression", 0)
        row.append((n, det))
        if adequate is None and det >= MIN_DETECT_RATE * TRIALS:
            adequate = n
    print(f"     effect={effect:+.2f} ({label}): " +
          ", ".join(f"n={n} -> {d}/{TRIALS}" for n, d in row) +
          f"   | minimum adequate n: {adequate if adequate else 'not reached at n<=200'}")

print()
print("4. UNCERTAINTY AND EFFECT SIZE. The bootstrap method returns an interval, so")
print("   it satisfies REQ-F-08-2 directly. Paired Cohen's dz is computable for")
print("   every comparison and is reported above per cell. The naive method returns")
print("   a point estimate only and cannot satisfy REQ-F-08-2 at all.")

print()
print("=" * 78)
print("CONCLUSION")
print("=" * 78)
print("The decisive finding is not the false-positive rate. At n=200 the naive")
print("threshold produced the FEWEST false positives of the three methods, because a")
print("fixed 0.02 threshold becomes very conservative once the sample mean")
print("concentrates. That is not a virtue: the same fixed threshold makes it fire on")
print("pure noise in roughly one run in six at n=20, and makes it structurally unable")
print("to detect any real regression smaller than the threshold at any sample size.")
print("Its behaviour is governed by an arbitrary constant rather than by the evidence,")
print("which is precisely the 'arbitrary quality-gate score without uncertainty' that")
print("canonical section 25 rejects.")
print()
print("The decisive finding is ABSTENTION. At n=20 the bootstrap declines to classify")
print("in roughly nine trials in ten -- including when the true effect is large --")
print("because the interval is genuinely too wide to distinguish anything. At the same")
print("sample size the naive method issues confident regression verdicts on pure")
print("noise. One method reports that it cannot tell; the other guesses.")
print()
print("Against the requirements:")
print("  REQ-F-08-2 uncertainty and effect size : bootstrap YES (interval + dz)")
print("                                           permutation partial (p only)")
print("                                           naive NO")
print("  REQ-F-08-4 insufficient-evidence state : bootstrap YES")
print("                                           permutation NO, naive NO")
print("  REQ-F-08-1 detects real regressions    : bootstrap YES at adequate n")
print("  REQ-F-08-3 minimum-sample guidance     : bootstrap YES, derived from the")
print("                                           detection table above")
print()
print("The paired bootstrap percentile interval is therefore the only candidate that")
print("satisfies all four requirements. The permutation test is retained as a")
print("cross-check for a future spike, not as the primary method, because it cannot")
print("abstain.")
print()
print("A second finding constrains the product directly: a noise-scale effect")
print("(-0.01) is not reliably detectable even at n=200. Minimum-sample guidance must")
print("therefore be stated per effect size, and the product must refuse to claim")
print("detection of effects below what the sample supports rather than reporting a")
print("small delta as a verdict.")
print()
print("LIMITATIONS. Simulated scores from one distribution family; one noise level;")
print("independent samples; a single alpha and precision setting; 100 trials per cell,")
print("so rates carry sampling error of a few counts. This spike supports a")
print("METHOD-CLASS decision only. Production alpha, per-metric precision thresholds,")
print("and minimum-sample tables must be set from real evaluation data, and the")
print("percentile bootstrap's known small-sample undercoverage should be re-examined")
print("against a bias-corrected variant before those values are fixed.")
