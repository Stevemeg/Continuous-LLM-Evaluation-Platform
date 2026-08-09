"""RAG: inputs, deterministic evaluators, hallucination analysis, attribution."""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.evaluators.rag import (RAG_RUBRICS, missing_required,
                                 register_rag_evaluators)
from clep.evaluators.sdk import (EvaluatorError, EvaluatorRegistry,
                                 RetrievedContext, SampleContext, run_evaluator)
from clep.rag.attribution import (GENERATION, NEITHER, NOT_ATTRIBUTABLE,
                                  RETRIEVAL, attribute)
from clep.rag.hallucination import (CONTRADICTED, GROUNDED, NOT_ANALYSABLE,
                                    UNSUPPORTED, analyse, analyse_claim)


@pytest.fixture
def registry():
    r = EvaluatorRegistry()
    register_rag_evaluators(r)
    return r


def sample(**over):
    base = dict(example_id="x", prompt="who?", output="alpha", integration_tier="full",
                contexts=(RetrievedContext("c1", "alpha text", 0),
                          RetrievedContext("c2", "beta text", 1)))
    base.update(over)
    return SampleContext(**base)


class Consensus:
    """Just enough of a consensus result: a verdict, or none."""

    def __init__(self, verdict):
        self.verdict = Decimal(verdict) if verdict is not None else None


# ------------------------------------------------------------------- inputs
def test_a_context_must_be_identifiable():
    with pytest.raises(EvaluatorError):
        RetrievedContext("", "text")
    with pytest.raises(EvaluatorError):
        RetrievedContext("c1", "text", rank=-1)


def test_two_contexts_cannot_share_an_id():
    with pytest.raises(EvaluatorError):
        sample(contexts=(RetrievedContext("c1", "a"), RetrievedContext("c1", "b")))


def test_the_flat_and_identified_forms_cannot_disagree():
    with pytest.raises(EvaluatorError):
        SampleContext(example_id="x", prompt="p", output="o",
                      retrieved_context=("something else",),
                      contexts=(RetrievedContext("c1", "alpha text"),))
    # Derived rather than restated, so the old evaluators keep working.
    assert sample().retrieved_context == ("alpha text", "beta text")


def test_a_citation_naming_nothing_retrieved_is_visible():
    assert sample(citations=("c1", "c9")).unresolved_citations == ("c9",)
    assert sample(citations=("c1",)).unresolved_citations == ()


# ------------------------------------------------------- retrieval as a fact
def test_the_required_set_lives_on_the_sample_not_on_what_came_back():
    """A passage the retriever missed is absent from what it returned, so a
    label on the returned rows could never express the case that matters."""
    s = sample(required_context_ids=("c1", "c3"))
    assert missing_required(s) == ("c3",)


def test_retrieval_hit_rate_counts_what_was_required(registry):
    s = sample(required_context_ids=("c1", "c3"))
    outcome = run_evaluator(registry.get("retrieval_hit_rate@1.0.0"), s)
    assert outcome.score == Decimal("0.500000000")
    assert "missing: c3" in outcome.detail


def test_a_perfect_retrieval_scores_one(registry):
    s = sample(required_context_ids=("c1", "c2"))
    assert run_evaluator(registry.get("retrieval_hit_rate@1.0.0"), s).score == 1


def test_an_unlabelled_example_abstains_rather_than_scoring_one(registry):
    """Scoring 1.0 because nothing was reported missing would report the absence
    of a question as a good answer."""
    for name in ("retrieval_hit_rate@1.0.0", "required_context_present@1.0.0"):
        outcome = run_evaluator(registry.get(name), sample())
        assert outcome.resolution == "abstained"
        assert outcome.score is None


def test_required_context_present_is_binary(registry):
    present = run_evaluator(registry.get("required_context_present@1.0.0"),
                            sample(required_context_ids=("c1",)))
    absent = run_evaluator(registry.get("required_context_present@1.0.0"),
                           sample(required_context_ids=("c9",)))
    assert (present.score, absent.score) == (1, 0)


# ----------------------------------------------------------------- citations
def test_citation_validity_measures_resolution_not_correctness(registry):
    outcome = run_evaluator(registry.get("citation_validity@1.0.0"),
                            sample(citations=("c1", "c9")))
    assert outcome.score == Decimal("0.500000000")
    assert "unresolved: c9" in outcome.detail


def test_an_uncited_answer_abstains(registry):
    outcome = run_evaluator(registry.get("citation_validity@1.0.0"), sample())
    assert outcome.resolution == "abstained"


def test_citation_coverage_is_not_a_quality_score(registry):
    outcome = run_evaluator(registry.get("citation_coverage@1.0.0"),
                            sample(citations=("c1",)))
    assert outcome.score == Decimal("0.500000000")


def test_scores_are_quantised_to_the_stores_resolution(registry):
    """Two thirds is a repeating decimal; the store holds numeric(18, 9). If the
    two differed a reproduction would report a gap that is arithmetic, not
    evidence."""
    s = sample(contexts=(RetrievedContext("c1", "a"), RetrievedContext("c2", "b"),
                         RetrievedContext("c3", "c")),
               citations=("c1", "c2"))
    outcome = run_evaluator(registry.get("citation_coverage@1.0.0"), s)
    assert outcome.score == Decimal("0.666666667")
    assert outcome.score.as_tuple().exponent == -9


# ------------------------------------------------- the split that is the point
def test_the_semantic_four_are_rubrics_and_not_evaluators(registry):
    """REQ-F-08-6 and canonical §25. A lexical overlap named `groundedness` is
    a metric nobody measured."""
    assert set(RAG_RUBRICS) == {"context_relevance", "faithfulness",
                                "groundedness", "answer_relevance"}
    for judged in RAG_RUBRICS:
        assert not any(judged in key for key in registry.keys())


# ------------------------------------------------------ hallucination, ADR-018
def analysis(support, contradiction, support_threshold="0.7",
             contradiction_threshold="0.5"):
    return analyse_claim(
        "the sky is green", support=Consensus(support),
        contradiction=Consensus(contradiction),
        support_threshold=Decimal(support_threshold) if support_threshold else None,
        contradiction_threshold=(Decimal(contradiction_threshold)
                                 if contradiction_threshold else None))


def test_unsupported_and_contradicted_are_different_findings():
    """The requirement is the distinction. Retrieval did not bring the evidence
    is a different failure from the evidence says otherwise."""
    assert analysis("0.9", "0.0").finding == GROUNDED
    assert analysis("0.1", "0.0").finding == UNSUPPORTED
    assert analysis("0.1", "0.9").finding == CONTRADICTED


def test_contradiction_outranks_support():
    """A passage can partly support a claim and deny part of it. Denial is the
    finding that says the answer is wrong rather than merely unevidenced."""
    assert analysis("0.95", "0.9").finding == CONTRADICTED


def test_an_escalated_judgement_is_not_a_low_score():
    result = analyse_claim("c", support=Consensus(None),
                           contradiction=Consensus("0.1"),
                           support_threshold=Decimal("0.7"),
                           contradiction_threshold=Decimal("0.5"))
    assert result.finding == NOT_ANALYSABLE
    assert "support" in result.reason


def test_without_thresholds_nothing_is_analysed():
    """ADR-018 keeps the ADR-007 discipline: the platform abstains until told."""
    assert analysis("0.9", "0.0", support_threshold=None).finding == NOT_ANALYSABLE
    assert analysis("0.9", "0.0",
                    contradiction_threshold=None).finding == NOT_ANALYSABLE


def test_the_analysis_never_widens_the_judge_parse():
    """The defence Phase 8 built rests on a reply being a bounded score or
    nothing. Two orthogonal bounded questions keep it that way."""
    import inspect
    from clep.rag import hallucination
    source = inspect.getsource(hallucination)
    assert "parse_reply" not in source
    assert "re.compile" not in source


def test_a_report_ranks_contradiction_first():
    report = analyse(
        [("a", Consensus("0.9"), Consensus("0.0")),
         ("b", Consensus("0.1"), Consensus("0.0")),
         ("c", Consensus("0.1"), Consensus("0.9"))],
        support_threshold=Decimal("0.7"), contradiction_threshold=Decimal("0.5"))
    assert report.counts == {GROUNDED: 1, UNSUPPORTED: 1, CONTRADICTED: 1,
                             NOT_ANALYSABLE: 0}
    assert report.worst() == CONTRADICTED
    assert report.analysable == 3


# ----------------------------------------------------------------- attribution
def test_a_missing_required_passage_is_a_retrieval_failure():
    result = attribute(sample(required_context_ids=("c9",)),
                       faithfulness=Consensus("0.1"),
                       faithfulness_threshold=Decimal("0.7"))
    assert result.stage == RETRIEVAL
    assert result.missing_context_ids == ("c9",)


def test_retrieval_outranks_generation():
    """A generator handed incomplete evidence may be unfaithful because of the
    gap. Calling that a generation failure sends someone to the wrong component."""
    result = attribute(sample(required_context_ids=("c1", "c9")),
                       faithfulness=Consensus("0.0"),
                       faithfulness_threshold=Decimal("0.7"))
    assert result.stage == RETRIEVAL


def test_complete_evidence_and_an_unfaithful_answer_is_the_generator():
    result = attribute(sample(required_context_ids=("c1",)),
                       faithfulness=Consensus("0.2"),
                       faithfulness_threshold=Decimal("0.7"))
    assert result.stage == GENERATION
    assert result.faithfulness == Decimal("0.2")


def test_neither_stage_failed_is_a_distinct_answer():
    result = attribute(sample(required_context_ids=("c1",)),
                       faithfulness=Consensus("0.9"),
                       faithfulness_threshold=Decimal("0.7"))
    assert result.stage == NEITHER


def test_without_labels_the_two_stages_are_indistinguishable():
    result = attribute(sample(), faithfulness=Consensus("0.1"),
                       faithfulness_threshold=Decimal("0.7"))
    assert result.stage == NOT_ATTRIBUTABLE
    assert "does not say which passages were required" in result.reason


def test_an_escalated_faithfulness_judgement_attributes_nothing():
    result = attribute(sample(required_context_ids=("c1",)),
                       faithfulness=Consensus(None),
                       faithfulness_threshold=Decimal("0.7"))
    assert result.stage == NOT_ATTRIBUTABLE
    assert "no verdict" in result.reason


def test_every_attribution_states_its_grounds():
    for faithfulness, threshold in ((Consensus("0.1"), Decimal("0.7")),
                                    (Consensus("0.9"), Decimal("0.7")),
                                    (None, None)):
        result = attribute(sample(required_context_ids=("c1",)),
                           faithfulness=faithfulness,
                           faithfulness_threshold=threshold)
        assert result.reason
