"""Which stage failed — retrieval or generation.

`REQ-F-03-6`: attribute a retrieval-stage failure separately from a
generation-stage failure **when both are observable**. The qualifier is the hard
part of the requirement, and it is why this module has a `not_attributable`
outcome that it uses often.

The two stages are observable separately only when the dataset says what
retrieval was supposed to find. Without that label there is no way to tell a
retriever that missed the evidence from a generator that ignored it: both look
like a wrong answer with some passages attached.

So:

| Required passages | Faithfulness | Attribution |
|---|---|---|
| some missing | anything | `retrieval` — the generator was never given what it needed |
| all present | below threshold | `generation` — the evidence was there and the answer left it |
| all present | at or above | `neither` — nothing in this sample failed |
| not labelled | anything | `not_attributable` |
| all present | no verdict | `not_attributable` |

Retrieval outranks generation deliberately. A generator handed incomplete
evidence may produce an unfaithful answer *because* of the gap, so calling that
a generation failure would send someone to fix the wrong component. The
converse is not true: complete evidence and an unfaithful answer is the
generator's alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from clep.evaluators.rag import missing_required

RETRIEVAL = "retrieval"
GENERATION = "generation"
NEITHER = "neither"
NOT_ATTRIBUTABLE = "not_attributable"
STAGES = (RETRIEVAL, GENERATION, NEITHER, NOT_ATTRIBUTABLE)


class AttributionError(ValueError):
    pass


@dataclass(frozen=True)
class Attribution:
    stage: str
    reason: str
    missing_context_ids: tuple = ()
    faithfulness: Decimal | None = None

    def __post_init__(self):
        if self.stage not in STAGES:
            raise AttributionError(f"unknown stage {self.stage!r}")
        if not self.reason:
            raise AttributionError("an attribution states its grounds")


def attribute(sample, *, faithfulness=None,
              faithfulness_threshold: Decimal | None = None) -> Attribution:
    """`faithfulness` is a consensus result from the ensemble, or None."""
    if not sample.required_context_ids:
        return Attribution(
            stage=NOT_ATTRIBUTABLE,
            reason="this example does not say which passages were required, so "
                   "a retriever that missed the evidence and a generator that "
                   "ignored it are indistinguishable")

    missing = missing_required(sample)
    if missing:
        return Attribution(
            stage=RETRIEVAL, missing_context_ids=missing,
            reason=f"retrieval did not return {', '.join(missing)}; the "
                   f"generator was never given what the answer needed, so an "
                   f"unfaithful answer here is not evidence against it")

    if faithfulness_threshold is None:
        return Attribution(
            stage=NOT_ATTRIBUTABLE,
            reason="every required passage was retrieved, but no faithfulness "
                   "threshold is configured, so whether the generator used them "
                   "cannot be decided")

    verdict = getattr(faithfulness, "verdict", None) if faithfulness else None
    if verdict is None:
        return Attribution(
            stage=NOT_ATTRIBUTABLE,
            reason="every required passage was retrieved, but the faithfulness "
                   "judgement reached no verdict; an escalated judgement is not "
                   "a low score")

    if verdict < faithfulness_threshold:
        return Attribution(
            stage=GENERATION, faithfulness=verdict,
            reason=f"every required passage was retrieved and faithfulness "
                   f"scored {verdict} against a threshold of "
                   f"{faithfulness_threshold}; the evidence was present and the "
                   f"answer left it")

    return Attribution(
        stage=NEITHER, faithfulness=verdict,
        reason=f"every required passage was retrieved and faithfulness scored "
               f"{verdict}; neither stage failed on this sample")
