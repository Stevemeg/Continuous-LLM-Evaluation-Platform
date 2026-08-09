"""Hallucination analysis — ADR-018 as code.

`REQ-F-03-3`: report hallucination analysis that distinguishes a claim
**unsupported** by the provided context from a claim **contradicted** by it.

The distinction is the requirement. They are different failures with different
remedies: unsupported usually means retrieval did not bring the evidence, and
contradicted means the generator asserted something the evidence denies. A
single "hallucination score" cannot express which, and a team acting on one when
it was the other fixes the wrong component.

**Two orthogonal judgements, not a widened vocabulary.** The obvious
implementation is to let a judge answer with a category. That would widen the
reply parse from "a bounded score or nothing" to "a bounded score, or one of
these words" — and the narrow parse is the load-bearing half of the Phase 8
injection defence. So the analysis asks two independent bounded questions:

    support      — do the passages state or entail this claim?
    contradiction — do the passages deny it?

They are not complements. A passage that is silent scores low on both, which is
exactly the `unsupported` case; a passage that denies the claim scores high on
contradiction whatever support says. The quadrant is the finding.

**The thresholds are unset.** Same discipline as ADR-007 and ADR-017: they are
arguments without defaults, supplied per policy, and the analysis reports
`not_analysable` when they are absent rather than choosing them here.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

GROUNDED = "grounded"
UNSUPPORTED = "unsupported"
CONTRADICTED = "contradicted"
NOT_ANALYSABLE = "not_analysable"
FINDINGS = (GROUNDED, UNSUPPORTED, CONTRADICTED, NOT_ANALYSABLE)


class HallucinationError(ValueError):
    pass


@dataclass(frozen=True)
class ClaimAnalysis:
    claim: str
    finding: str
    support: Decimal | None = None
    contradiction: Decimal | None = None
    reason: str = ""

    def __post_init__(self):
        if self.finding not in FINDINGS:
            raise HallucinationError(f"unknown finding {self.finding!r}")
        if (self.finding == NOT_ANALYSABLE) != (not self.reason):
            # A finding that could not be reached says why; one that was reached
            # does not need an excuse.
            if self.finding == NOT_ANALYSABLE and not self.reason:
                raise HallucinationError(
                    "an unanalysable claim must say what was missing")


@dataclass(frozen=True)
class HallucinationReport:
    claims: tuple
    support_threshold: Decimal | None
    contradiction_threshold: Decimal | None

    @property
    def counts(self) -> dict:
        out = {finding: 0 for finding in FINDINGS}
        for claim in self.claims:
            out[claim.finding] += 1
        return out

    @property
    def analysable(self) -> int:
        return sum(1 for c in self.claims if c.finding != NOT_ANALYSABLE)

    def worst(self) -> str:
        """The finding a reader should act on first.

        Contradiction outranks absence: a system asserting the opposite of its
        evidence is a different severity of wrong from one that went beyond it.
        """
        counts = self.counts
        if counts[CONTRADICTED]:
            return CONTRADICTED
        if counts[UNSUPPORTED]:
            return UNSUPPORTED
        if counts[GROUNDED]:
            return GROUNDED
        return NOT_ANALYSABLE


def analyse_claim(claim: str, *, support, contradiction,
                  support_threshold: Decimal | None,
                  contradiction_threshold: Decimal | None) -> ClaimAnalysis:
    """One claim, two judgements, one quadrant.

    `support` and `contradiction` are consensus results from the ensemble, or
    None where no judgement was reached. An escalated consensus carries no
    verdict, and an escalation is not a quiet zero — it makes the claim
    unanalysable, which is the honest answer and keeps the human in the loop
    that `REQ-F-AG-4` put there.
    """
    if support_threshold is None or contradiction_threshold is None:
        return ClaimAnalysis(
            claim=claim, finding=NOT_ANALYSABLE,
            reason="no support or contradiction threshold is configured; "
                   "ADR-018 declines to invent one")
    support_score = _verdict(support)
    contradiction_score = _verdict(contradiction)
    if support_score is None or contradiction_score is None:
        missing = []
        if support_score is None:
            missing.append("support")
        if contradiction_score is None:
            missing.append("contradiction")
        return ClaimAnalysis(
            claim=claim, finding=NOT_ANALYSABLE,
            support=support_score, contradiction=contradiction_score,
            reason=f"no verdict for {' and '.join(missing)}; the ensemble "
                   f"escalated or could not score")

    # Contradiction is tested first and outranks support. A passage can both
    # partially support a claim and deny part of it, and denial is the finding
    # that matters: it is the one that says the answer is wrong rather than
    # merely unevidenced.
    if contradiction_score >= contradiction_threshold:
        return ClaimAnalysis(claim=claim, finding=CONTRADICTED,
                             support=support_score,
                             contradiction=contradiction_score)
    if support_score >= support_threshold:
        return ClaimAnalysis(claim=claim, finding=GROUNDED,
                             support=support_score,
                             contradiction=contradiction_score)
    return ClaimAnalysis(claim=claim, finding=UNSUPPORTED,
                         support=support_score,
                         contradiction=contradiction_score)


def analyse(claim_judgements, *, support_threshold: Decimal | None,
            contradiction_threshold: Decimal | None) -> HallucinationReport:
    """`claim_judgements` is an iterable of (claim, support, contradiction)."""
    return HallucinationReport(
        claims=tuple(
            analyse_claim(claim, support=support, contradiction=contradiction,
                          support_threshold=support_threshold,
                          contradiction_threshold=contradiction_threshold)
            for claim, support, contradiction in claim_judgements),
        support_threshold=support_threshold,
        contradiction_threshold=contradiction_threshold)


def _verdict(consensus):
    """The agreed score, or None. An escalation has no number, deliberately."""
    if consensus is None:
        return None
    return getattr(consensus, "verdict", None)
