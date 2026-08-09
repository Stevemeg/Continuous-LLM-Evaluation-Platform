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
class RetrievedContext:
    """One passage the system retrieved, as it retrieved it.

    `REQ-F-03-1`. Identified, because a citation has to point at something and a
    position in a list is not an identity — reranking would silently repoint
    every citation in the run.

    What the dataset says *should* have been retrieved lives on the sample, in
    `required_context_ids`, and not here. It has to: a required passage that the
    retriever missed is absent from this list entirely, so a label on the
    retrieved rows could never express the case that matters.
    """
    id: str
    text: str
    rank: int = 0

    def __post_init__(self):
        if not self.id:
            raise EvaluatorError("a retrieved context must be identifiable; a "
                                 "citation cannot point at a list position")
        if self.rank < 0:
            raise EvaluatorError("a rank is a position, not a negative number")


@dataclass(frozen=True)
class SampleContext:
    """Everything an evaluator may see, and nothing else.

    `retrieved_context` stays a tuple of strings for the evaluators written
    before Phase 9; `contexts` is the identified form, and `citations` names the
    context ids the answer claimed to rest on. Both are untrusted
    (`REQ-F-03-5`, `REQ-F-04-6`): they arrive from a retriever or a tool, which
    is a legitimate path and an entirely uncontrolled one.
    """
    example_id: str
    prompt: str
    output: str
    expected: str | None = None
    retrieved_context: tuple[str, ...] = ()
    trajectory: tuple[str, ...] = ()
    integration_tier: str = "output_only"
    #: The identified form. When present it is authoritative and
    #: `retrieved_context` is derived from it, so the two cannot disagree.
    contexts: tuple = ()
    #: Context ids the answer cited. A citation naming nothing retrieved is a
    #: defect the evaluators report rather than ignore.
    citations: tuple = ()
    #: What the dataset says retrieval was supposed to find. Empty means the
    #: dataset does not say, and the evaluators that need it abstain — an
    #: unlabelled example has not been answered well, it has not been asked.
    required_context_ids: tuple = ()
    #: The typed trajectory (`REQ-F-04-1`). `trajectory` above stays the flat
    #: string form the judges render inside the fence; this is the structure the
    #: agent evaluators read.
    agent_trajectory: object | None = None
    #: Tool schemas the task declared, keyed by tool name. Without them a call
    #: cannot be invalid, because nothing says what valid would be.
    tool_schemas: dict | None = None
    #: The tools the dataset says the task required.
    expected_tools: tuple = ()

    def __post_init__(self):
        if self.integration_tier not in TIERS:
            raise EvaluatorError(f"unknown integration tier {self.integration_tier!r}")
        if self.contexts:
            derived = tuple(c.text for c in self.contexts)
            if self.retrieved_context and self.retrieved_context != derived:
                raise EvaluatorError(
                    "retrieved_context and contexts disagree; one sample cannot "
                    "carry two versions of what was retrieved")
            object.__setattr__(self, "retrieved_context", derived)
            ids = [c.id for c in self.contexts]
            if len(set(ids)) != len(ids):
                raise EvaluatorError("two retrieved contexts share an id")

    def context_by_id(self) -> dict:
        return {c.id: c for c in self.contexts}

    @property
    def unresolved_citations(self) -> tuple:
        """Citations naming something that was not retrieved."""
        known = set(self.context_by_id())
        return tuple(c for c in self.citations if c not in known)


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
        if self.score is not None:
            # Quantised to the store's resolution here, at the one boundary
            # every evaluator passes through. A ratio like 2/3 is a repeating
            # decimal; leaving it unrounded means the number the evaluator
            # returned and the number `numeric(18, 9)` holds are different, and
            # a reproduction would compare them and report a gap that is an
            # artefact of arithmetic rather than of the run.
            object.__setattr__(self, "score",
                               self.score.quantize(Decimal("1e-9")))


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
