"""Paired comparison statistics — ADR-007 as executable code.

ADR-007 is accepted and frozen: the paired bootstrap percentile interval on the
mean paired difference is the primary comparison method, and classification is
derived from the interval rather than from a fixed threshold on the delta. This
module is that decision and nothing else; it holds no policy.

Four properties are deliberate.

**Parameters are arguments, never defaults.** ADR-007 records four values it
refused to set — confidence level, per-metric precision threshold, minimum sample
size, resample count — because setting them from simulated data would be the
arbitrary constant canonical §25 rejects. A default here would set them anyway,
quietly, and every gate decision in the product would inherit a number nobody
chose. When the policy has no value, the answer is `insufficient_evidence` with a
reason, not a guess.

**The width rule is checked first.** ADR-007's table is ordered, and the spike
evidence behind it shows why: at n=20 the bootstrap declines in about nine trials
in ten *including when the true effect is large*, because the interval genuinely
cannot resolve it. An interval of [-0.9, -0.1] names a direction but not a
magnitude anyone can act on.

**Direction is per metric.** ADR-007 speaks of an interval "entirely below zero"
as a regression, which holds only where higher is better. `clep.threshold`
carries `direction` per metric because latency and cost run the other way. The
sign is interpreted through it; the ADR is applied, not amended.

**Nothing here is floating point.** Scores arrive as `Decimal` from
`numeric(18,9)` columns and stay exact through the mean, the resamples, the
percentile selection and the effect size, so the same inputs and seed produce the
same interval everywhere. A verdict that depended on binary representation error
would be irreproducible in the one way nobody thinks to check.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal

#: Recorded in every comparison and every gate decision (REQ-F-08-7). The suffix
#: changes when the *method* changes, which is not the same as its parameters
#: changing: parameters live in the policy version and are recorded there.
METHOD_VERSION = "paired-bootstrap-percentile/1"

#: The smallest difference `numeric(18, 9)` can hold. Anything below it is not a
#: quantity the platform can record, so it is not a quantity to reason about.
RESOLUTION = Decimal("1e-9")

HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"
DIRECTIONS = (HIGHER_IS_BETTER, LOWER_IS_BETTER)

REGRESSION = "regression"
IMPROVEMENT = "improvement"
NO_CHANGE = "no_change"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
CLASSIFICATIONS = (REGRESSION, IMPROVEMENT, NO_CHANGE, INSUFFICIENT_EVIDENCE)


class StatisticsError(ValueError):
    pass


@dataclass(frozen=True)
class Pair:
    """One example scored under both the baseline and the candidate.

    The unit of analysis is the difference, so an example scored in only one of
    the two runs cannot appear here at all. Dropping it is not data loss; keeping
    it would be pairing a number with nothing.
    """
    example_id: str
    baseline: Decimal
    candidate: Decimal

    @property
    def difference(self) -> Decimal:
        return self.candidate - self.baseline


@dataclass(frozen=True)
class Interval:
    lower: Decimal
    upper: Decimal
    confidence_level: Decimal

    @property
    def width(self) -> Decimal:
        return self.upper - self.lower

    def excludes_zero_below(self) -> bool:
        return self.upper < 0

    def excludes_zero_above(self) -> bool:
        return self.lower > 0


@dataclass(frozen=True)
class Comparison:
    classification: str
    sample_size: int
    method_version: str
    mean_difference: Decimal | None = None
    interval: Interval | None = None
    effect_size: Decimal | None = None
    minimum_sample_size: int | None = None
    #: Why a classification was withheld. Present exactly when the classification
    #: is `insufficient_evidence`, because "we could not tell" is only useful if
    #: it says what was missing.
    abstention_reason: str | None = None


def compare(pairs, *, direction: str, confidence_level: Decimal,
            precision_threshold: Decimal | None, minimum_sample_size: int | None,
            resamples: int, seed: int) -> Comparison:
    pairs = list(pairs)
    if direction not in DIRECTIONS:
        raise StatisticsError(
            f"{direction!r} is not a metric direction; a comparison that does not "
            f"know which way is better cannot classify anything")
    if not (Decimal(0) < confidence_level < Decimal(1)):
        raise StatisticsError("confidence level must lie strictly inside (0, 1)")
    if resamples < 1:
        raise StatisticsError("a bootstrap needs at least one resample")

    n = len(pairs)
    if n == 0:
        return Comparison(
            classification=INSUFFICIENT_EVIDENCE, sample_size=0,
            method_version=METHOD_VERSION, minimum_sample_size=minimum_sample_size,
            abstention_reason="no example was scored under both runs")

    differences = [p.difference for p in pairs]
    mean_difference = _mean(differences)

    if minimum_sample_size is not None and n < minimum_sample_size:
        # REQ-F-08-3. The interval is still computed and reported: refusing to
        # classify is not refusing to show the evidence.
        return Comparison(
            classification=INSUFFICIENT_EVIDENCE, sample_size=n,
            method_version=METHOD_VERSION, mean_difference=mean_difference,
            interval=_interval(differences, confidence_level, resamples, seed),
            effect_size=_effect_size(differences, mean_difference),
            minimum_sample_size=minimum_sample_size,
            abstention_reason=(
                f"{n} paired sample(s) is below the configured minimum of "
                f"{minimum_sample_size} for this metric"))

    interval = _interval(differences, confidence_level, resamples, seed)
    effect_size = _effect_size(differences, mean_difference)
    common = dict(sample_size=n, method_version=METHOD_VERSION,
                  mean_difference=mean_difference, interval=interval,
                  effect_size=effect_size,
                  minimum_sample_size=minimum_sample_size)

    if precision_threshold is None:
        # Without a precision threshold there is no way to tell a tight interval
        # around zero from one that spans everything, and those two mean opposite
        # things. ADR-007 left the value unset on purpose; inventing one here
        # would set it for the whole product.
        return Comparison(classification=INSUFFICIENT_EVIDENCE, **common,
                          abstention_reason=(
                              "no precision threshold is configured for this metric, "
                              "so 'no change' cannot be distinguished from 'cannot tell'"))
    if interval.width > precision_threshold:
        return Comparison(classification=INSUFFICIENT_EVIDENCE, **common,
                          abstention_reason=(
                              f"interval width {interval.width} exceeds the configured "
                              f"precision threshold {precision_threshold}"))

    if interval.excludes_zero_below():
        worse = direction == HIGHER_IS_BETTER
        return Comparison(classification=REGRESSION if worse else IMPROVEMENT, **common)
    if interval.excludes_zero_above():
        worse = direction == LOWER_IS_BETTER
        return Comparison(classification=REGRESSION if worse else IMPROVEMENT, **common)
    return Comparison(classification=NO_CHANGE, **common)


def _mean(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal(0)) / Decimal(len(values))


def _interval(differences: list[Decimal], confidence_level: Decimal,
              resamples: int, seed: int) -> Interval:
    """Percentile interval over resampled means.

    Seeded explicitly rather than from the global random state: a gate decision
    that cannot be re-derived is not evidence, and a shared global generator makes
    the result depend on whatever else ran first in the process.
    """
    rng = random.Random(seed)
    n = len(differences)
    means = []
    for _ in range(resamples):
        total = Decimal(0)
        for _ in range(n):
            total += differences[rng.randrange(n)]
        means.append(total / Decimal(n))
    means.sort()
    tail = (Decimal(1) - confidence_level) / Decimal(2)
    lower_rank = int(tail * resamples)
    upper_rank = min(resamples - 1, int((Decimal(1) - tail) * resamples))
    return Interval(lower=means[lower_rank], upper=means[upper_rank],
                    confidence_level=confidence_level)


def _effect_size(differences: list[Decimal], mean_difference: Decimal) -> Decimal | None:
    """Paired standardised mean difference.

    Undefined rather than enormous when the paired differences barely vary. The
    first version of this tested the deviation against exact zero, and the test
    that was meant to confirm it reported an effect size of -2.9e15: the spread
    was not zero, it was numerical dust, and dividing by dust produces a number
    that looks like an extraordinary finding.

    The threshold is the resolution the store actually has. Scores are
    `numeric(18, 9)`, so a spread below the ninth decimal is not a spread — every
    difference is the same value at the precision the data is recorded in, and a
    standardised effect over it means nothing. `REQ-F-08-2` asks for effect size
    "where the metric makes it meaningful", and this is the boundary of that.
    """
    n = len(differences)
    if n < 2:
        return None
    variance = sum(((d - mean_difference) ** 2 for d in differences), Decimal(0)) \
        / Decimal(n - 1)
    deviation = variance.sqrt()
    if deviation < RESOLUTION:
        return None
    return (mean_difference / deviation).quantize(RESOLUTION)
