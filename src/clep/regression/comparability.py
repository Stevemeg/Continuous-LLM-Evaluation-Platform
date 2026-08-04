"""Whether two runs may be compared at all — decided before any arithmetic.

`REQ-F-08-8`: "shall invalidate comparability, rather than warn, when a judge or
evaluator version changes between baseline and candidate, and shall offer
re-scoring of the baseline as the remedy."

The distinction this module exists to keep is between the things a comparison is
*about* and the things it holds *fixed*. A prompt version or a model
configuration that differs is the entire point — that is the change under test. A
dataset version, suite version, evaluator version or integration tier that
differs means the two runs answered different questions, and a number computed
across them is not a small inaccuracy but a category error: it looks exactly like
a real regression.

The components come from Phase 6's captured run identity, so this is a comparison
of recorded facts rather than of whatever the two runs claim about themselves now.
Environment is captured and deliberately ignored here, per ADR-014: two runs on
machines with different interpreter patch versions are the same measurement, and
treating them otherwise would expire every baseline on upgrade.
"""
from __future__ import annotations

from dataclasses import dataclass

#: Differ here and the runs are not comparable. Each is something the evaluation
#: holds fixed while something else varies.
PINNED_KINDS = ("dataset_version", "suite_version", "evaluator_version",
                "integration_tier")

#: Differ here and that is the experiment. Listed rather than implied, so that
#: adding a kind to the identity forces a decision about which list it joins.
VARYING_KINDS = ("prompt_version", "model_configuration", "system_version", "seed")

#: Captured, never compared (ADR-014).
IGNORED_KINDS = ("environment",)


@dataclass(frozen=True)
class Difference:
    kind: str
    baseline: tuple[str, ...]
    candidate: tuple[str, ...]

    def describe(self) -> str:
        gone = sorted(set(self.baseline) - set(self.candidate))
        added = sorted(set(self.candidate) - set(self.baseline))
        parts = []
        if gone:
            parts.append(f"baseline had {', '.join(gone)}")
        if added:
            parts.append(f"candidate has {', '.join(added)}")
        return f"{self.kind}: " + "; ".join(parts)


@dataclass(frozen=True)
class Verdict:
    comparable: bool
    differences: tuple[Difference, ...] = ()

    def reason(self) -> str | None:
        if self.comparable:
            return None
        return ("the two runs do not hold the same things fixed, so their scores "
                "measure different questions — "
                + "; ".join(d.describe() for d in self.differences)
                + ". Re-score the baseline against the current versions to restore "
                  "comparability (REQ-F-08-8)")


def assess(baseline_identity, candidate_identity) -> Verdict:
    """Compare the pinned components of two captured identities.

    A component that is *absent* on one side counts as a difference. An
    unrecorded evaluator version is not evidence that it was the same one; it is
    evidence that nothing is known, and a comparison drawn across it would be
    asserting more than the record supports.
    """
    differences = []
    for kind in PINNED_KINDS:
        baseline = _refs(baseline_identity, kind)
        candidate = _refs(candidate_identity, kind)
        if baseline != candidate:
            differences.append(Difference(kind=kind, baseline=baseline,
                                          candidate=candidate))
    return Verdict(comparable=not differences, differences=tuple(differences))


def _refs(identity, kind: str) -> tuple[str, ...]:
    return tuple(sorted(c.ref for c in identity.components if c.kind == kind))


def unknown_kinds(identity) -> tuple[str, ...]:
    """Kinds this module has no opinion about.

    Nothing calls this in the request path; the Phase 7 validator does. A
    component kind that is neither pinned, varying nor ignored would be silently
    dropped from the comparability decision, and silence is the failure mode
    least likely to be noticed.
    """
    known = set(PINNED_KINDS) | set(VARYING_KINDS) | set(IGNORED_KINDS)
    return tuple(sorted({c.kind for c in identity.components} - known))
