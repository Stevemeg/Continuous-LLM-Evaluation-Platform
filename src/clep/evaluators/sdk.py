"""Evaluator SDK — the contract a plugin implements, and the boundary it runs in.

ADR-006 accepted the isolation *model* and deferred the mechanism to this phase.
The model has three parts, and all three are here:

  1. An evaluator declares what it needs. `REQ-F-03-4` requires that an evaluator
     which cannot run at the caller's integration tier is reported as
     `unavailable` — never approximated from what happens to be available. An
     approximation silently answers a question nobody asked.

  2. An evaluator receives a context, not the process. It gets the sample it is
     scoring and nothing else: no database handle, no provider gateway, no
     configuration, no credentials. A third-party evaluator that cannot reach a
     credential cannot leak one (`REQ-N-SEC-5`), and one that cannot reach the
     store cannot read another tenant's rows.

  3. An evaluator's failure is data. `run_evaluator` converts an exception into
     an outcome with resolution `failed`, because one badly-written plugin must
     not end a run, and because a crashed evaluator is a result about that
     evaluator rather than a platform failure.

Scores are `Decimal` and bounded. A float score would make two runs of an
identical evaluator differ in the last bit, and `REQ-X-8` requires that an
unscored sample is never readable as zero — which is why `score` is None unless
the resolution is exactly `scored`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Callable, Protocol

#: The six resolutions the contract and the schema both enumerate.
RESOLUTIONS = ("scored", "failed", "timed_out", "abstained", "unavailable", "truncated")

#: How much intermediate state the caller exposed, per the contract.
TIERS = ("full", "partial", "output_only")
_TIER_RANK = {t: i for i, t in enumerate(("output_only", "partial", "full"))}


class EvaluatorError(Exception):
    """Raised by the SDK itself, never by a plugin's own failure."""


@dataclass(frozen=True)
class SampleContext:
    """Everything an evaluator may see, and nothing else."""
    example_id: str
    prompt: str
    output: str
    expected: str | None = None
    retrieved_context: tuple[str, ...] = ()
    trajectory: tuple[str, ...] = ()
    integration_tier: str = "output_only"

    def __post_init__(self):
        if self.integration_tier not in TIERS:
            raise EvaluatorError(f"unknown integration tier {self.integration_tier!r}")


@dataclass(frozen=True)
class EvaluatorOutcome:
    resolution: str
    score: Decimal | None = None
    unavailable_reason: str | None = None
    detail: str = ""
    duration_ms: int = 0

    def __post_init__(self):
        if self.resolution not in RESOLUTIONS:
            raise EvaluatorError(f"unknown resolution {self.resolution!r}")
        if (self.resolution == "scored") != (self.score is not None):
            raise EvaluatorError(
                "only a `scored` resolution carries a score, and it always does; "
                "anything else must carry none, so that an unscored sample can "
                "never be read as a zero")
        if self.resolution == "unavailable" and not self.unavailable_reason:
            raise EvaluatorError("an unavailable evaluator must say why")
        if self.score is not None and not (Decimal(0) <= self.score <= Decimal(1)):
            raise EvaluatorError(f"score {self.score} is outside [0, 1]")


def scored(value) -> EvaluatorOutcome:
    try:
        return EvaluatorOutcome("scored", score=Decimal(str(value)))
    except (InvalidOperation, TypeError) as e:
        raise EvaluatorError(f"score {value!r} is not an exact decimal") from e


def abstained(detail: str = "") -> EvaluatorOutcome:
    return EvaluatorOutcome("abstained", detail=detail)


class Evaluator(Protocol):
    name: str
    version: str
    #: Lowest integration tier at which this evaluator can run at all.
    requires_tier: str

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        ...


@dataclass
class EvaluatorRegistration:
    name: str
    version: str
    requires_tier: str
    evaluate: Callable[[SampleContext], EvaluatorOutcome]
    is_builtin: bool = False

    @property
    def version_key(self) -> str:
        return f"{self.name}@{self.version}"


class EvaluatorRegistry:
    """Registration is explicit. Nothing is discovered by import side effect.

    Canonical §25 rejects unversioned evaluators, so a name alone is not an
    identity: registering the same name twice with the same version is refused,
    because two different behaviours would then share one identity and past runs
    would silently change meaning.
    """

    def __init__(self):
        self._items: dict[str, EvaluatorRegistration] = {}

    def register(self, evaluator: Evaluator, *, is_builtin: bool = False) -> EvaluatorRegistration:
        for attribute in ("name", "version", "requires_tier"):
            if not getattr(evaluator, attribute, None):
                raise EvaluatorError(f"evaluator must declare {attribute}")
        if evaluator.requires_tier not in TIERS:
            raise EvaluatorError(f"unknown required tier {evaluator.requires_tier!r}")
        reg = EvaluatorRegistration(
            name=evaluator.name, version=evaluator.version,
            requires_tier=evaluator.requires_tier, evaluate=evaluator.evaluate,
            is_builtin=is_builtin)
        if reg.version_key in self._items:
            raise EvaluatorError(
                f"{reg.version_key} is already registered; publish a new version "
                f"rather than redefining one, or past runs change meaning")
        self._items[reg.version_key] = reg
        return reg

    def get(self, version_key: str) -> EvaluatorRegistration:
        try:
            return self._items[version_key]
        except KeyError:
            raise EvaluatorError(f"no evaluator registered as {version_key!r}") from None

    def keys(self) -> list[str]:
        return sorted(self._items)

    def __len__(self) -> int:
        return len(self._items)


def tier_permits(required: str, available: str) -> bool:
    return _TIER_RANK[available] >= _TIER_RANK[required]


def run_evaluator(registration: EvaluatorRegistration, sample: SampleContext,
                  timeout_ms: int | None = None) -> EvaluatorOutcome:
    """Run one evaluator and convert every way it can go wrong into an outcome.

    The tier check happens first and without calling the plugin at all: an
    evaluator that needs a trajectory must not be handed an empty one and left
    to draw a conclusion from it.
    """
    if not tier_permits(registration.requires_tier, sample.integration_tier):
        return EvaluatorOutcome(
            "unavailable",
            unavailable_reason=(
                f"{registration.version_key} requires integration tier "
                f"'{registration.requires_tier}'; this run provides "
                f"'{sample.integration_tier}'"))

    started = time.perf_counter()
    try:
        outcome = registration.evaluate(sample)
    except Exception as e:  # a plugin's failure is data, not a platform failure
        return EvaluatorOutcome(
            "failed", detail=f"{type(e).__name__}: {e}"[:500],
            duration_ms=int((time.perf_counter() - started) * 1000))
    elapsed = int((time.perf_counter() - started) * 1000)

    if not isinstance(outcome, EvaluatorOutcome):
        return EvaluatorOutcome(
            "failed",
            detail=f"{registration.version_key} returned {type(outcome).__name__}, "
                   f"not an EvaluatorOutcome",
            duration_ms=elapsed)
    if timeout_ms is not None and elapsed > timeout_ms:
        # Reported as timed_out rather than accepted late: a result that arrived
        # after its budget is not a result the run is entitled to use.
        return EvaluatorOutcome("timed_out", duration_ms=elapsed)
    return EvaluatorOutcome(outcome.resolution, outcome.score,
                            outcome.unavailable_reason, outcome.detail, elapsed)
