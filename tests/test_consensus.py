"""Consensus: a verdict and a disagreement, an escalation, and never an average."""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.judges.consensus import (ABOVE_THRESHOLD, AGREED, ESCALATED,
                                   MAXIMUM_DISAGREEMENT, NO_THRESHOLD,
                                   TOO_FEW_VOTES, Consensus, ConsensusError,
                                   Ensemble, reach_consensus)
from clep.judges.sdk import JudgeVersion, Vote


def judge(slug="a", model="model-a", endpoint="e1"):
    return JudgeVersion(slug=slug, version="1", model=model,
                        endpoint_name=endpoint, rubric="r")


A, B, C = judge("a", "model-a", "e1"), judge("b", "model-b", "e2"), \
    judge("c", "model-c", "e3")


def vote(j, score=None, resolution="scored"):
    return Vote(judge=j, resolution=resolution,
                score=Decimal(str(score)) if score is not None else None)


def ensemble(judges=(A, B), threshold="0.20", minimum=None):
    return Ensemble(judges=tuple(judges),
                    agreement_threshold=Decimal(threshold) if threshold else None,
                    minimum_scoring_votes=minimum)


# ---------------------------------------------------------------- composition

def test_an_ensemble_of_one_is_a_single_judge_as_ground_truth():
    with pytest.raises(ConsensusError):
        Ensemble(judges=(A,), agreement_threshold=Decimal("0.2"),
                 minimum_scoring_votes=None)


def test_an_ensemble_of_identical_configurations_is_refused():
    """ADR-004 D-1. Different names, one model, correlated errors."""
    twin = JudgeVersion(slug="b", version="1", model="model-a",
                        endpoint_name="e1", rubric="r")
    with pytest.raises(ConsensusError) as e:
        Ensemble(judges=(A, twin), agreement_threshold=None,
                 minimum_scoring_votes=None)
    assert "correlated" in str(e.value)


def test_no_configuration_may_hold_a_majority_of_the_seats():
    """ADR-017 §2. One configuration that can outvote every other is not an
    ensemble; it is that configuration with witnesses."""
    twin = JudgeVersion(slug="a2", version="1", model="model-a",
                        endpoint_name="e1", rubric="r")
    with pytest.raises(ConsensusError) as e:
        Ensemble(judges=(A, twin, B), agreement_threshold=None,
                 minimum_scoring_votes=None)
    assert "outvote" in str(e.value)
    # The same three seats, spread across three configurations, are fine.
    assert Ensemble(judges=(A, B, C), agreement_threshold=None,
                    minimum_scoring_votes=None)


def test_a_minimum_below_two_is_not_a_minimum():
    with pytest.raises(ConsensusError):
        Ensemble(judges=(A, B), agreement_threshold=None, minimum_scoring_votes=1)


def test_the_parameters_adr_004_left_open_have_no_defaults():
    import inspect
    signature = inspect.signature(Ensemble.__init__)
    for name in ("agreement_threshold", "minimum_scoring_votes"):
        assert signature.parameters[name].default is inspect.Parameter.empty


# ----------------------------------------------------------------- the measure

def test_disagreement_is_the_range_of_the_scoring_votes():
    result = reach_consensus(ensemble((A, B, C), threshold="1"),
                             [vote(A, "0.20"), vote(B, "0.55"), vote(C, "0.90")])
    assert result.disagreement == Decimal("0.70")
    assert result.disagreement_measured is True


def test_one_dissenter_is_not_diluted_by_agreeable_judges():
    """The property that chose the range over a mean deviation."""
    D = judge("d", "model-d", "e4")
    E = judge("e", "model-e", "e5")
    agreeable = [vote(A, "0.80"), vote(B, "0.80"), vote(C, "0.80"), vote(D, "0.80")]
    dissenter = vote(E, "0.10")
    room = ensemble((A, B, C, D, E), threshold="0.20")
    assert reach_consensus(room, agreeable + [dissenter]).state == ESCALATED
    # Adding more agreeable judges cannot buy the escalation away.
    assert reach_consensus(room, agreeable + [dissenter]).disagreement == Decimal("0.70")


def test_a_non_scoring_vote_is_not_a_score_of_zero():
    """REQ-X-8. An abstaining judge must not drag the range to the floor."""
    result = reach_consensus(ensemble((A, B, C)),
                             [vote(A, "0.80"), vote(B, "0.85"),
                              vote(C, resolution="abstained")])
    assert result.disagreement == Decimal("0.05")
    assert result.state == AGREED


def test_fewer_than_two_scoring_votes_reports_maximum_disagreement():
    """A single vote has a range of zero. Reporting zero would be the strongest
    statement of consensus on the weakest evidence."""
    result = reach_consensus(ensemble(),
                             [vote(A, "0.90"), vote(B, resolution="failed")])
    assert result.state == ESCALATED
    assert result.escalation_reason == TOO_FEW_VOTES
    assert result.disagreement == MAXIMUM_DISAGREEMENT
    assert result.disagreement_measured is False


def test_the_measured_flag_separates_total_disagreement_from_no_evidence():
    nothing = reach_consensus(ensemble(), [vote(A, resolution="failed"),
                                           vote(B, resolution="failed")])
    total = reach_consensus(ensemble(threshold="0.1"),
                            [vote(A, "0"), vote(B, "1")])
    assert nothing.disagreement == total.disagreement == Decimal(1)
    assert nothing.disagreement_measured is False
    assert total.disagreement_measured is True


# -------------------------------------------------------------------- verdicts

def test_agreement_produces_the_median_and_a_confidence():
    result = reach_consensus(ensemble((A, B, C)),
                             [vote(A, "0.70"), vote(B, "0.80"), vote(C, "0.75")])
    assert result.state == AGREED
    assert result.verdict == Decimal("0.750000000")
    assert result.confidence == Decimal("0.900000000")
    assert result.escalation_reason is None


def test_the_verdict_is_the_median_not_the_mean():
    result = reach_consensus(ensemble((A, B, C), threshold="1"),
                             [vote(A, "0.10"), vote(B, "0.80"), vote(C, "0.90")])
    assert result.verdict == Decimal("0.800000000")


def test_an_even_number_of_votes_takes_the_middle_pair():
    result = reach_consensus(ensemble((A, B), threshold="1"),
                             [vote(A, "0.20"), vote(B, "0.50")])
    assert result.verdict == Decimal("0.350000000")


def test_no_verdict_is_produced_when_the_ensemble_escalated():
    """REQ-F-AG-4. Producing a number anyway is the averaging this prevents."""
    result = reach_consensus(ensemble(threshold="0.01"),
                             [vote(A, "0.10"), vote(B, "0.90")])
    assert result.state == ESCALATED
    assert result.verdict is None
    assert result.confidence is None


# ----------------------------------------------------------------- escalation

def test_disagreement_above_the_threshold_escalates():
    result = reach_consensus(ensemble(threshold="0.10"),
                             [vote(A, "0.40"), vote(B, "0.90")])
    assert result.escalation_reason == ABOVE_THRESHOLD
    assert "0.50" in result.escalation_detail


def test_an_unconfigured_threshold_escalates_rather_than_guessing():
    """ADR-017 §3, the ADR-007 pattern: the platform abstains until told."""
    result = reach_consensus(ensemble(threshold=None),
                             [vote(A, "0.80"), vote(B, "0.80")])
    assert result.state == ESCALATED
    assert result.escalation_reason == NO_THRESHOLD


def test_too_few_votes_against_a_configured_minimum_escalates():
    result = reach_consensus(ensemble((A, B, C), minimum=3),
                             [vote(A, "0.80"), vote(B, "0.80"),
                              vote(C, resolution="timed_out")])
    assert result.escalation_reason == TOO_FEW_VOTES
    assert result.disagreement_measured is True


def test_consensus_cannot_obtain_another_vote_even_if_it_wanted_one():
    """ADR-004 D-4, I-24. Retrying until judges agree manufactures consensus.

    Asserted as a capability rather than as an absence of loop keywords: the
    function takes votes that already exist and is given no way to reach a
    provider, so there is no implementation of it that could re-ask a judge.
    """
    import inspect
    parameters = set(inspect.signature(reach_consensus).parameters)
    assert parameters == {"ensemble", "votes"}
    import clep.judges.consensus as module
    assert not hasattr(module, "ProviderGateway")
    assert "clep.providers" not in {m.__name__ for m in vars(module).values()
                                    if inspect.ismodule(m)}


def test_escalating_is_deterministic_and_changes_nothing():
    """A terminal state is one a second look does not move."""
    votes = [vote(A, "0.10"), vote(B, "0.90")]
    room = ensemble(threshold="0.01")
    first = reach_consensus(room, votes)
    second = reach_consensus(room, votes)
    assert first == second
    assert first.state == ESCALATED


def test_an_escalation_always_names_its_reason():
    with pytest.raises(ConsensusError):
        Consensus(state=ESCALATED, disagreement=Decimal(0),
                  disagreement_measured=True, method_version="x", votes=())
    with pytest.raises(ConsensusError):
        Consensus(state=AGREED, disagreement=Decimal(0), disagreement_measured=True,
                  method_version="x", votes=(), verdict=Decimal("0.5"),
                  escalation_reason=ABOVE_THRESHOLD)


def test_a_verdict_without_a_disagreement_is_not_representable():
    """I-22, enforced by the type rather than by convention."""
    import inspect
    signature = inspect.signature(Consensus.__init__)
    assert signature.parameters["disagreement"].default is inspect.Parameter.empty
    assert signature.parameters["disagreement_measured"].default is \
        inspect.Parameter.empty


# ------------------------------------------------------------- identity guards

def test_votes_from_outside_the_ensemble_are_refused():
    stranger = judge("z", "model-z", "e9")
    with pytest.raises(ConsensusError) as e:
        reach_consensus(ensemble(), [vote(A, "0.5"), vote(stranger, "0.9")])
    assert "outside the ensemble" in str(e.value)


def test_per_judge_deviation_exposes_the_bias_signal():
    """Canonical §8. Accumulated across judgements, this is what calibration needs."""
    result = reach_consensus(ensemble((A, B, C), threshold="1"),
                             [vote(A, "0.60"), vote(B, "0.70"), vote(C, "0.90")])
    deviations = result.per_judge_deviation()
    assert deviations["a@1"] == Decimal("-0.100000000")
    assert deviations["b@1"] == Decimal("0E-9")
    assert deviations["c@1"] == Decimal("0.200000000")


def test_deviation_is_empty_when_there_was_nothing_to_deviate_from():
    result = reach_consensus(ensemble(), [vote(A, "0.5"),
                                          vote(B, resolution="abstained")])
    assert result.per_judge_deviation() == {}
