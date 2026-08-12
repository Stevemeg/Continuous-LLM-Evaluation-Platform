"""Whether a figure may be read at face value — `REQ-F-11-7`.

The requirement is unusually specific about scope: a figure computed from
incomplete data must be marked as incomplete *in every view and export in which
it appears*. So this is one type, produced in one place, carried by every
analytics response and rendered by every report, rather than a boolean each
surface decides for itself. The failure it prevents is not a wrong number; it is
a right number that means something narrower than the reader assumes.

Two independent things make evidence incomplete, and they are reported
separately because they call for different responses.

  * **A run did not finish complete.** Cancelled, exhausted, partial, rejected —
    four of the five `Completeness` states are not success. A mean over such a
    run is a mean of the part that happened.
  * **Samples in the window produced no score.** A provider outage leaves a
    sample resolved `failed` with no number (`REQ-X-8`), and it is excluded from
    the mean rather than counted as zero. That is right, and it also means the
    figure rests on fewer observations than the window suggests.

Neither is an error, and neither is hidden. `reason` is a sentence a
non-specialist can act on, because `REQ-F-11-8` requires the executive report to
keep these qualifications rather than tidy them away.
"""
from __future__ import annotations

from dataclasses import dataclass

COMPLETE = "complete"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class Completeness:
    """What the evidence behind a figure was, and what is missing from it."""
    state: str
    contributing_runs: int = 0
    incomplete_runs: int = 0
    observations: int = 0
    unresolved_observations: int = 0
    reason: str | None = None

    @property
    def is_complete(self) -> bool:
        return self.state == COMPLETE

    def as_dict(self) -> dict:
        body = {"state": self.state,
                "contributingRuns": self.contributing_runs,
                "incompleteRuns": self.incomplete_runs,
                "observations": self.observations,
                "unresolvedObservations": self.unresolved_observations}
        if self.reason:
            body["reason"] = self.reason
        return body


def completeness_of(*, contributing_runs: int, incomplete_runs: int,
                    observations: int, unresolved_observations: int
                    ) -> Completeness:
    """Mark a figure from the evidence it was computed over.

    A figure with no observations at all is incomplete rather than complete-and-
    empty: "we measured nothing" and "we measured and found nothing wrong" are
    the two answers `REQ-F-08-4` exists to keep apart, and an empty view that
    reads as healthy is how that distinction gets lost outside the gate.
    """
    reasons = []
    if observations == 0:
        reasons.append("no observation in this window produced a score, so "
                       "there is nothing behind this figure")
    if incomplete_runs:
        reasons.append(
            f"{incomplete_runs} of {contributing_runs} contributing run(s) did "
            f"not finish complete, so this covers the part that happened rather "
            f"than the whole")
    if unresolved_observations:
        reasons.append(
            f"{unresolved_observations} of "
            f"{observations + unresolved_observations} observation(s) produced "
            f"no score and are excluded rather than counted as zero")
    state = COMPLETE if not reasons else INCOMPLETE
    return Completeness(state=state, contributing_runs=contributing_runs,
                        incomplete_runs=incomplete_runs,
                        observations=observations,
                        unresolved_observations=unresolved_observations,
                        reason="; ".join(reasons) or None)
