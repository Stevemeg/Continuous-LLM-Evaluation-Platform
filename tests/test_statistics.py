"""ADR-007, tested as behaviour rather than as arithmetic.

The property worth protecting is not that the interval is computed correctly —
that is a few lines — but that the module abstains when it should. The spike
behind ADR-007 chose this method over a fixed threshold *because* it declines to
classify at small samples where the alternative issues confident verdicts on
noise. A change that quietly made abstention rarer would look like an
improvement and would be the defect.
"""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from clep.regression import statistics as S


def pairs(baseline, candidate):
    return [S.Pair(str(i), Decimal(str(b)), Decimal(str(c)))
            for i, (b, c) in enumerate(zip(baseline, candidate))]


def scores(n, *, low=0.40, high=0.80, seed=11):
    rng = random.Random(seed)
    return [round(rng.uniform(low, high), 3) for _ in range(n)]


def jitter(values, spread=0.02, seed=12):
    rng = random.Random(seed)
    return [round(v + rng.uniform(-spread, spread), 3) for v in values]


def compare(prs, **over):
    kwargs = dict(direction=S.HIGHER_IS_BETTER, confidence_level=Decimal("0.95"),
                  precision_threshold=Decimal("0.05"), minimum_sample_size=None,
                  resamples=400, seed=20260804)
    kwargs.update(over)
    return S.compare(prs, **kwargs)


# ------------------------------------------------------------------ the method
def test_a_real_regression_is_classified_as_one():
    base = scores(80)
    worse = jitter([b - 0.15 for b in base])
    result = compare(pairs(base, worse))
    assert result.classification == S.REGRESSION
    assert result.interval.upper < 0
    assert result.mean_difference < 0


def test_a_real_improvement_is_not_reported_as_a_regression():
    base = scores(80)
    better = jitter([b + 0.15 for b in base])
    assert compare(pairs(base, better)).classification == S.IMPROVEMENT


def test_no_difference_is_no_change_not_a_verdict_either_way():
    base = scores(80)
    same = jitter(base, spread=0.005)
    assert compare(pairs(base, same)).classification == S.NO_CHANGE


def test_direction_decides_which_sign_is_bad():
    """A latency metric that fell has improved. The interval is identical."""
    base = scores(80)
    lower = jitter([b - 0.15 for b in base])
    assert compare(pairs(base, lower),
                   direction=S.HIGHER_IS_BETTER).classification == S.REGRESSION
    assert compare(pairs(base, lower),
                   direction=S.LOWER_IS_BETTER).classification == S.IMPROVEMENT


# ------------------------------------------------------------------- abstention
def test_an_interval_wider_than_the_precision_threshold_abstains():
    """ADR-007's table is ordered and this row is first.

    The interval below excludes zero, so the direction is not in doubt. It is
    still too wide to act on, and the ADR's spike measured exactly this: at n=20
    the bootstrap declines about nine times in ten *including* when the true
    effect is large.
    """
    base = scores(12)
    worse = jitter([b - 0.15 for b in base], spread=0.30, seed=5)
    result = compare(pairs(base, worse), precision_threshold=Decimal("0.01"))
    assert result.classification == S.INSUFFICIENT_EVIDENCE
    assert "precision threshold" in result.abstention_reason
    assert result.interval is not None, "the evidence is still reported"


def test_a_sample_below_the_configured_minimum_abstains():
    base = scores(6)
    worse = jitter([b - 0.15 for b in base])
    result = compare(pairs(base, worse), minimum_sample_size=30)
    assert result.classification == S.INSUFFICIENT_EVIDENCE
    assert "below the configured minimum" in result.abstention_reason
    assert result.sample_size == 6


def test_without_a_precision_threshold_nothing_is_called_no_change():
    """ADR-007 refused to set this value; the module refuses to invent it.

    A missing threshold makes a tight interval around zero and one that spans
    everything indistinguishable, and those mean opposite things.
    """
    base = scores(80)
    same = jitter(base, spread=0.005)
    result = compare(pairs(base, same), precision_threshold=None)
    assert result.classification == S.INSUFFICIENT_EVIDENCE
    assert "no precision threshold" in result.abstention_reason


def test_no_paired_samples_abstains_rather_than_reporting_no_change():
    result = compare([])
    assert result.classification == S.INSUFFICIENT_EVIDENCE
    assert result.sample_size == 0
    assert result.interval is None


def test_every_abstention_says_what_was_missing():
    for result in (compare([]),
                   compare(pairs(scores(6), scores(6, seed=3)),
                           minimum_sample_size=30),
                   compare(pairs(scores(40), scores(40, seed=3)),
                           precision_threshold=None)):
        assert result.classification == S.INSUFFICIENT_EVIDENCE
        assert result.abstention_reason


def test_a_classification_never_carries_an_abstention_reason():
    base = scores(80)
    worse = jitter([b - 0.15 for b in base])
    assert compare(pairs(base, worse)).abstention_reason is None


# ---------------------------------------------------------------- reproducibility
def test_the_same_inputs_and_seed_produce_the_same_interval():
    base = scores(50)
    worse = jitter([b - 0.05 for b in base])
    first = compare(pairs(base, worse), seed=99)
    second = compare(pairs(base, worse), seed=99)
    assert first == second


def test_a_different_seed_is_visible_rather_than_hidden():
    """Not a demand that seeds agree — a demand that the seed is what varies.

    If two seeds gave identical intervals the resampling would not be doing
    anything, and the recorded seed on the policy version would be decoration.
    """
    base = scores(50)
    worse = jitter([b - 0.05 for b in base])
    assert compare(pairs(base, worse), seed=1).interval != \
        compare(pairs(base, worse), seed=2).interval


def test_the_decision_path_holds_no_floating_point():
    """Exact numeric in, exact numeric out (N-9's reasoning, applied to verdicts).

    A gate decision that turned on binary representation error would be
    irreproducible in the one way nobody would think to check.
    """
    result = compare(pairs(scores(40), jitter(scores(40), spread=0.01)))
    assert isinstance(result.mean_difference, Decimal)
    assert isinstance(result.interval.lower, Decimal)
    assert isinstance(result.interval.upper, Decimal)


# ------------------------------------------------------------------ effect size
def test_effect_size_is_reported_with_the_comparison():
    base = scores(60)
    worse = jitter([b - 0.10 for b in base])
    assert compare(pairs(base, worse)).effect_size < 0


def test_effect_size_is_undefined_rather_than_enormous_when_every_pair_agrees():
    base = scores(30)
    shifted = [Decimal(str(b)) - Decimal("0.10") for b in base]
    prs = [S.Pair(str(i), Decimal(str(b)), c)
           for i, (b, c) in enumerate(zip(base, shifted))]
    result = compare(prs)
    assert result.effect_size is None
    assert result.mean_difference == Decimal("-0.10")


def test_a_spread_below_the_stored_resolution_is_not_a_spread():
    """The case that found the defect.

    Building the same shift in float leaves residues around 1e-17 in the paired
    differences. Testing the deviation against exact zero let those through, and
    dividing by dust reported an effect size of -2.9e15 — a number that reads as
    an extraordinary finding and is an artefact of binary representation. Below
    the ninth decimal the store cannot hold the difference, so neither can the
    statistic.
    """
    base = scores(30)
    dusty = [b - 0.10 for b in base]          # float, deliberately
    result = compare(pairs(base, dusty))
    assert result.effect_size is None
    assert result.classification == S.REGRESSION


def test_effect_size_needs_more_than_one_pair():
    assert compare(pairs([0.5], [0.4])).effect_size is None


# ---------------------------------------------------------------------- refusals
@pytest.mark.parametrize("bad", ["", "higher", "up", None])
def test_a_comparison_that_does_not_know_which_way_is_better_is_refused(bad):
    with pytest.raises(S.StatisticsError):
        compare(pairs(scores(10), scores(10, seed=2)), direction=bad)


@pytest.mark.parametrize("bad", ["0", "1", "1.5", "-0.1"])
def test_a_confidence_level_outside_the_unit_interval_is_refused(bad):
    with pytest.raises(S.StatisticsError):
        compare(pairs(scores(10), scores(10, seed=2)),
                confidence_level=Decimal(bad))


def test_a_bootstrap_with_no_resamples_is_refused():
    with pytest.raises(S.StatisticsError):
        compare(pairs(scores(10), scores(10, seed=2)), resamples=0)


def test_not_comparable_is_never_this_modules_answer():
    """Comparability is decided before the statistics run, from versions rather
    than from numbers. A method that could return it would be deciding something
    it cannot see."""
    assert S.CLASSIFICATIONS == (S.REGRESSION, S.IMPROVEMENT, S.NO_CHANGE,
                                 S.INSUFFICIENT_EVIDENCE)
    assert "not_comparable" not in S.CLASSIFICATIONS


# ------------------------------------------------------------- null calibration
def test_the_null_is_null():
    """The spike behind ADR-007 found a defect in itself this way.

    Its first score model drew baselines near the upper bound, so positive noise
    clipped and negative noise did not, and every method's regression rate was
    inflated at a true effect of zero. The same check belongs in the suite: if
    identical inputs produced a systematic non-zero difference, every verdict
    above would be measuring the harness.
    """
    base = scores(200)
    result = compare(pairs(base, base), precision_threshold=Decimal("0.05"))
    assert result.mean_difference == 0
    assert result.classification == S.NO_CHANGE
    assert result.interval.lower == 0 and result.interval.upper == 0
