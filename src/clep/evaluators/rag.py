"""RAG evaluators — and the line between what is computed and what is judged.

`REQ-F-03-2` names six things to evaluate: retrieval quality, context relevance,
faithfulness, groundedness, citation quality, and answer relevance. They are not
the same kind of question, and pretending they were would put a number on
something nobody measured.

**Computed here.** Retrieval quality and citation quality are facts about the
record. Did the passages the dataset marks as required come back? Does every
citation resolve to something that was actually retrieved? Those have answers
that do not depend on anyone's opinion, and they are deterministic evaluators.

**Judged elsewhere.** Context relevance, faithfulness, groundedness and answer
relevance are semantic judgements. They belong to the ensemble in
`clep.judges`, under rubrics, with disagreement exposed and escalation
available — not to a lexical-overlap heuristic wearing their names. Canonical
§25 rejects claiming a metric an executed measurement did not produce, and
"fraction of expected tokens appearing in the context" is not groundedness. The
rubrics live in `RAG_RUBRICS` below and are used to construct judge versions;
this module computes nothing from them.

That split is `REQ-F-08-6` arriving where it always pointed: deterministic
results and probabilistic ones stay structurally apart, and the six items of
`REQ-F-03-2` divide cleanly along the same line.

Every evaluator here abstains rather than guessing when its input is absent, and
declares the integration tier it needs so the SDK reports it `unavailable`
rather than running it on nothing (`REQ-F-03-4`).
"""
from __future__ import annotations

from decimal import Decimal

from clep.evaluators.sdk import (EvaluatorOutcome, SampleContext, abstained,
                                 scored)

#: Rubrics for the judgements this module deliberately does not compute. Held
#: here so the four semantic items of REQ-F-03-2 are visible next to the two
#: deterministic ones, and so a reader can see which is which. A judge version
#: is created from one of these; nothing in this module evaluates them.
RAG_RUBRICS = {
    "context_relevance":
        "You are scoring RETRIEVAL, not the answer. Given the question and one "
        "retrieved passage, score how relevant the passage is to answering the "
        "question. 1.0 means directly answers it; 0.0 means unrelated.",
    "faithfulness":
        "You are scoring whether the answer STAYS WITHIN the passages. Score "
        "1.0 if every claim in the answer is stated by or follows from the "
        "passages; 0.0 if the answer asserts things the passages do not. Do not "
        "reward or penalise correctness in the world — only fidelity to the "
        "passages.",
    "groundedness":
        "Score how much of the answer is traceable to a specific passage. 1.0 "
        "means every substantive claim can be pointed at a passage; 0.0 means "
        "none can. An answer that is correct but untraceable scores low.",
    "answer_relevance":
        "Score whether the answer addresses the question that was asked, "
        "ignoring whether it is correct. 1.0 means it answers that question; "
        "0.0 means it answers a different one.",
}


def missing_required(sample: SampleContext) -> tuple:
    """Required context ids that retrieval did not return.

    The one fact both evaluators below and the attribution in
    `clep.evaluators.attribution` rest on, computed once here so the three
    cannot come to different conclusions about the same sample.
    """
    return tuple(sorted(set(sample.required_context_ids)
                        - set(sample.context_by_id())))


class RetrievalHitRate:
    """Of the passages the dataset says were required, how many came back.

    Retrieval quality as a fact rather than an opinion. The required set is on
    the sample rather than on the retrieved rows, which is the whole point: a
    passage the retriever missed is absent from what it returned, so a label
    carried by the returned rows could never express the case that matters.

    Abstains when the dataset does not say. Scoring 1.0 because nothing was
    reported missing would be reporting the absence of a question as a good
    answer.
    """
    name = "retrieval_hit_rate"
    version = "1.0.0"
    requires_tier = "partial"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        required = set(sample.required_context_ids)
        if not required:
            return abstained(
                "this example does not say which passages were required, so "
                "there is no hit rate to compute")
        found = required & set(sample.context_by_id())
        missing = missing_required(sample)
        return EvaluatorOutcome(
            "scored", score=Decimal(len(found)) / Decimal(len(required)),
            detail=(f"missing: {', '.join(missing)}" if missing else ""))


class RequiredContextPresent:
    """1.0 when nothing required is missing, 0.0 when something is.

    The binary form, and the one a gate criterion can sensibly sit on: a
    retrieval that missed a required passage failed at the retrieval stage
    whatever the generator then did with what it got (`REQ-F-03-6`).
    """
    name = "required_context_present"
    version = "1.0.0"
    requires_tier = "partial"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        if not sample.required_context_ids:
            return abstained("this example does not say which passages were "
                             "required")
        missing = missing_required(sample)
        return EvaluatorOutcome("scored", score=Decimal(0 if missing else 1),
                                detail=(f"missing: {', '.join(missing)}"
                                        if missing else ""))


class CitationValidity:
    """Every citation resolves to something that was retrieved.

    A citation naming a passage the system did not retrieve is a defect
    regardless of whether the answer happens to be right — the answer is citing
    a source it cannot have read. Deterministic, and the half of "citation
    quality" that does not need a judge.
    """
    name = "citation_validity"
    version = "1.0.0"
    requires_tier = "partial"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        if not sample.citations:
            return abstained("the answer cited nothing, so there is nothing to "
                             "resolve; an uncited answer is judged elsewhere")
        unresolved = sample.unresolved_citations
        return EvaluatorOutcome(
            "scored",
            score=Decimal(len(sample.citations) - len(unresolved))
            / Decimal(len(sample.citations)),
            detail=(f"unresolved: {', '.join(unresolved)}" if unresolved else ""))


class CitationCoverage:
    """How much of the retrieved evidence the answer actually points at.

    Not a quality score and named so it cannot be read as one: it is the
    fraction of cited passages among those retrieved. Low coverage with a
    correct answer is normal; the number is here because
    `REQ-F-11-6` requires a reported figure to resolve to what produced it, and
    "which passages did this answer claim to use" is that.
    """
    name = "citation_coverage"
    version = "1.0.0"
    requires_tier = "partial"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        if not sample.contexts:
            return abstained("no identified contexts on this sample")
        cited = {c for c in sample.citations} & set(sample.context_by_id())
        return scored(Decimal(len(cited)) / Decimal(len(sample.contexts)))


def register_rag_evaluators(registry) -> list:
    """Register the deterministic four. The judged four are not evaluators."""
    return [registry.register(evaluator(), is_builtin=True)
            for evaluator in (RetrievalHitRate, RequiredContextPresent,
                              CitationValidity, CitationCoverage)]
