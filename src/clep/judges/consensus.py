"""Consensus — ADR-004's structure with ADR-017's measure, and nothing averaged.

ADR-004 decided that consensus produces a verdict *and* a disagreement measure,
that low agreement escalates and terminates, and that deterministic evaluators
never vote. It deliberately did not decide which measure, at what threshold, or
with what composition, because those need real judge outputs. ADR-017 decides
the two of those three that are structural and leaves the third where ADR-004
left it: unset, supplied per ensemble, and abstaining when absent.

Four properties this module exists to keep.

**Disagreement is the range, and it is always reported.** I-22 makes a verdict
without a disagreement measure unrepresentable. The measure is the spread
between the highest and lowest scoring vote, in score units, which is monotone
in the single worst dissenter — the signal `REQ-F-AG-4` exists to catch. Mean
deviation would hide one dissenting judge among four agreeable ones.

**Fewer than two scoring votes is not agreement.** A single vote has a range of
zero, and reporting zero would be the strongest possible statement of consensus
made on the weakest possible evidence. In that case the measure is reported at
its maximum with `disagreement_measured` false, so a reader can tell "they
disagreed completely" from "there was nothing to compare".

**Escalation is terminal.** There is no retry, no second round, no re-ask with a
sharper rubric. ADR-004 D-4: retrying until judges agree manufactures consensus,
and it is the canonical §25 anti-pattern wearing a loop. `escalate` returns; it
does not call anything.

**The verdict is the median, not the mean.** Only computed when the ensemble
agreed, where the two are close in any case — but the mean lets one extreme vote
move the number, and nothing in this module should reward being extreme.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from clep.regression.statistics import RESOLUTION

METHOD_VERSION = "range-disagreement/1"

AGREED = "agreed"
ESCALATED = "escalated"
STATES = (AGREED, ESCALATED)

#: Why a judgement went to a human instead of producing a verdict.
ABOVE_THRESHOLD = "disagreement_above_threshold"
NO_THRESHOLD = "no_threshold_configured"
TOO_FEW_VOTES = "insufficient_scoring_votes"
ESCALATION_REASONS = (ABOVE_THRESHOLD, NO_THRESHOLD, TOO_FEW_VOTES)

#: The widest two scores in [0, 1] can be. Reported when disagreement could not
#: be measured, so that the absence of evidence never reads as agreement.
MAXIMUM_DISAGREEMENT = Decimal(1)


class ConsensusError(Exception):
    pass


@dataclass(frozen=True)
class Ensemble:
    """A configured ensemble. ADR-004 D-1 and ADR-017 §2 are enforced here.

    The parameters ADR-004 declined to set — the agreement threshold and the
    minimum number of scoring votes — are fields without defaults, on the same
    reasoning ADR-007's parameters are: a default is a number nobody chose,
    applied to every tenant. When either is absent the ensemble escalates and
    says which was missing.
    """
    judges: tuple
    agreement_threshold: Decimal | None
    minimum_scoring_votes: int | None

    def __post_init__(self):
        if len(self.judges) < 2:
            raise ConsensusError(
                "an ensemble of one judge is a single judge treated as ground "
                "truth, which canonical §25 forbids by name")
        configurations = [j.configuration_key for j in self.judges]
        distinct = set(configurations)
        if len(distinct) < 2:
            raise ConsensusError(
                f"every judge in this ensemble runs {configurations[0]}; "
                f"identical judges produce correlated errors and the appearance "
                f"of consensus without its substance (ADR-004 D-1)")
        for configuration in distinct:
            if configurations.count(configuration) * 2 > len(configurations):
                raise ConsensusError(
                    f"{configuration} holds {configurations.count(configuration)} "
                    f"of {len(configurations)} seats and can outvote every other "
                    f"configuration; that is one model with witnesses, not an "
                    f"ensemble (ADR-017 §2)")
        if self.agreement_threshold is not None and self.agreement_threshold < 0:
            raise ConsensusError("an agreement threshold cannot be negative")
        if self.minimum_scoring_votes is not None and self.minimum_scoring_votes < 2:
            raise ConsensusError(
                "agreement between fewer than two judges is not agreement")


@dataclass(frozen=True)
class Consensus:
    state: str
    disagreement: Decimal
    disagreement_measured: bool
    method_version: str
    votes: tuple
    verdict: Decimal | None = None
    confidence: Decimal | None = None
    escalation_reason: str | None = None
    escalation_detail: str = ""

    def __post_init__(self):
        if self.state not in STATES:
            raise ConsensusError(f"unknown consensus state {self.state!r}")
        if (self.state == ESCALATED) != (self.escalation_reason is not None):
            raise ConsensusError(
                "an escalation names its reason, and an agreement has none")
        if (self.state == AGREED) != (self.verdict is not None):
            raise ConsensusError(
                "a verdict exists exactly when the ensemble agreed; an escalated "
                "judgement has no number, because producing one is the averaging "
                "REQ-F-AG-4 exists to prevent")
        if self.escalation_reason is not None and \
                self.escalation_reason not in ESCALATION_REASONS:
            raise ConsensusError(f"unknown escalation reason {self.escalation_reason!r}")

    @property
    def scoring_votes(self) -> tuple:
        return tuple(v for v in self.votes if v.is_scoring)

    def per_judge_deviation(self) -> dict:
        """Each judge's signed distance from the ensemble's central score.

        The bias signal canonical §8 asks the ensemble to expose. Computed
        against the median of this judgement, so a judge that is consistently
        above it across many judgements is consistently generous — which is a
        fact about the judge, and is what historical memory accumulates.
        """
        scoring = self.scoring_votes
        if len(scoring) < 2:
            return {}
        centre = _median(sorted(v.score for v in scoring))
        return {v.judge.version_key: (v.score - centre).quantize(RESOLUTION)
                for v in scoring}


def reach_consensus(ensemble: Ensemble, votes) -> Consensus:
    """Turn a set of votes into a verdict or an escalation. Never into a retry."""
    votes = tuple(votes)
    known = {j.version_key for j in ensemble.judges}
    strangers = sorted({v.judge.version_key for v in votes} - known)
    if strangers:
        raise ConsensusError(
            f"votes from judges outside the ensemble: {strangers}; the ensemble "
            f"is part of the run identity and cannot acquire members at runtime")

    scoring = [v for v in votes if v.is_scoring]
    if len(scoring) < 2:
        return Consensus(
            state=ESCALATED, disagreement=MAXIMUM_DISAGREEMENT,
            disagreement_measured=False, method_version=METHOD_VERSION,
            votes=votes, escalation_reason=TOO_FEW_VOTES,
            escalation_detail=(
                f"{len(scoring)} of {len(votes)} judges produced a score; "
                f"agreement between fewer than two is not agreement, and a "
                f"single opinion reported as consensus is the §25 anti-pattern"))

    scores = sorted(v.score for v in scoring)
    disagreement = scores[-1] - scores[0]
    minimum = ensemble.minimum_scoring_votes
    if minimum is not None and len(scoring) < minimum:
        return Consensus(
            state=ESCALATED, disagreement=disagreement, disagreement_measured=True,
            method_version=METHOD_VERSION, votes=votes,
            escalation_reason=TOO_FEW_VOTES,
            escalation_detail=(
                f"{len(scoring)} scoring vote(s) against a configured minimum of "
                f"{minimum}"))
    if ensemble.agreement_threshold is None:
        return Consensus(
            state=ESCALATED, disagreement=disagreement, disagreement_measured=True,
            method_version=METHOD_VERSION, votes=votes,
            escalation_reason=NO_THRESHOLD,
            escalation_detail=(
                "no agreement threshold is configured for this ensemble, so "
                "agreement cannot be distinguished from disagreement; ADR-004 "
                "left the value unset and ADR-017 declines to invent one"))
    if disagreement > ensemble.agreement_threshold:
        return Consensus(
            state=ESCALATED, disagreement=disagreement, disagreement_measured=True,
            method_version=METHOD_VERSION, votes=votes,
            escalation_reason=ABOVE_THRESHOLD,
            escalation_detail=(
                f"the judges spread {disagreement} against a configured "
                f"threshold of {ensemble.agreement_threshold}; routed to human "
                f"review rather than averaged away"))

    return Consensus(
        state=AGREED, disagreement=disagreement, disagreement_measured=True,
        method_version=METHOD_VERSION, votes=votes,
        verdict=_median(scores),
        confidence=(MAXIMUM_DISAGREEMENT - disagreement).quantize(RESOLUTION))


def _median(sorted_scores) -> Decimal:
    n = len(sorted_scores)
    middle = n // 2
    if n % 2:
        return sorted_scores[middle].quantize(RESOLUTION)
    return ((sorted_scores[middle - 1] + sorted_scores[middle])
            / Decimal(2)).quantize(RESOLUTION)
