"""Metric declarations, and the refusal that makes `REQ-N-OBS-4` enforceable.

ADR-022 rule 5. A metric declares its label names and, for each, the complete set
of values that label may take. Recording an undeclared label, a missing one, or a
value outside the declared set raises `CardinalityError`.

The reason this is a refusal rather than a convention is in ADR-022's rationale
and is worth repeating where the code is. Every project that has blown up an
observability bill had a developer who knew not to put an identifier on a metric.
Adding `run_id` to a metric is, at the call site, the most useful thing you can
do; the cost lands somewhere else, later, as money, and nothing fails in between.
A rule that depends on remembering survives until the first busy week.

So there is nothing to remember. Tenant, project, run, sample and correlation
identifiers are not expressible as labels, because the enumeration a label must
declare does not exist for them and cannot be written down. They are trace and
log dimensions -- `observability-strategy.md` §3 -- and that is the only place
this package will carry them.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: The nine classes `REQ-N-OBS-2` requires, in the order `observability-strategy.md`
#: §3 lists them. Every declared metric names the class it serves, so "are all
#: nine present" is answered by grouping the catalogue rather than by a reviewer
#: reading a list and believing it.
METRIC_CLASSES: tuple[str, ...] = (
    "latency",
    "errors",
    "queue_time",
    "provider_behaviour",
    "tokens_and_cost",
    "judge_behaviour",
    "evaluator_failures",
    "retries",
    "workflow_transitions",
)

KINDS = ("counter", "histogram", "gauge")


class CardinalityError(ValueError):
    """A label that is not declared, is missing, or carries an undeclared value.

    A programming error, and deliberately not survivable. Contrast the backend
    failures in `port.py`, which are environment conditions and are swallowed:
    ADR-022 rule 3 keeps telemetry from changing a verdict, and it is about a
    backend that is absent or broken, not about a call site that is wrong.
    Dropping a mislabelled sample instead of raising would leave a metric that
    everybody believes exists and that records nothing.
    """


@dataclass(frozen=True)
class MetricSpec:
    name: str
    kind: str
    metric_class: str
    unit: str
    description: str
    #: label name -> the complete set of permitted values. An empty tuple is not
    #: allowed: a label with no permitted values can never be recorded, which
    #: would be a declaration that quietly disables its own metric.
    labels: Mapping[str, tuple[str, ...]]

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"{self.name}: unknown metric kind {self.kind!r}")
        if self.metric_class not in METRIC_CLASSES:
            raise ValueError(
                f"{self.name}: {self.metric_class!r} is not one of the nine "
                f"classes REQ-N-OBS-2 requires ({', '.join(METRIC_CLASSES)})")
        for label, values in self.labels.items():
            if not values:
                raise ValueError(
                    f"{self.name}: label {label!r} declares no permitted values, "
                    f"so nothing could ever be recorded against it")
            if len(set(values)) != len(values):
                raise ValueError(f"{self.name}: label {label!r} repeats a value")

    @property
    def max_series(self) -> int:
        """The largest number of series this metric can ever produce.

        Computable at all is the point: a metric whose series count cannot be
        derived from its declaration is a metric with an unbounded label, and
        `REQ-N-OBS-4` is the requirement that there are none.
        """
        total = 1
        for values in self.labels.values():
            total *= len(values)
        return total


class MetricCatalogue:
    """The declared metrics. Validation happens here, before any backend sees a
    sample, so cardinality is bounded in the core (ADR-009 rule 5) and stays
    bounded in the default build that has no backend at all."""

    def __init__(self, specs: tuple[MetricSpec, ...] = ()):
        self._specs: dict[str, MetricSpec] = {}
        for spec in specs:
            self.declare(spec)

    def declare(self, spec: MetricSpec) -> MetricSpec:
        if spec.name in self._specs:
            raise ValueError(f"{spec.name} is already declared")
        self._specs[spec.name] = spec
        return spec

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __iter__(self):
        return iter(self._specs.values())

    def __len__(self) -> int:
        return len(self._specs)

    def get(self, name: str) -> MetricSpec:
        spec = self._specs.get(name)
        if spec is None:
            raise CardinalityError(
                f"{name!r} is not a declared metric; declare it in the catalogue "
                f"with its labels and their permitted values")
        return spec

    def classes(self) -> dict[str, list[str]]:
        """Metric names grouped by the class they serve."""
        out: dict[str, list[str]] = {c: [] for c in METRIC_CLASSES}
        for spec in self._specs.values():
            out[spec.metric_class].append(spec.name)
        return out

    def missing_classes(self) -> list[str]:
        return [c for c, names in self.classes().items() if not names]

    def max_series(self) -> int:
        return sum(spec.max_series for spec in self._specs.values())

    def validate(self, name: str, labels: Mapping[str, str]) -> dict[str, str]:
        """The refusal. Returns the labels when they are legal, raises otherwise."""
        spec = self.get(name)
        declared = set(spec.labels)
        given = set(labels)
        undeclared = sorted(given - declared)
        if undeclared:
            raise CardinalityError(
                f"{name}: label(s) {undeclared} are not declared. If this is an "
                f"identifier -- tenant, project, run, sample, correlation -- it "
                f"belongs on a trace or a log line, never on a metric "
                f"(REQ-N-OBS-4, observability-strategy.md §3)")
        missing = sorted(declared - given)
        if missing:
            raise CardinalityError(
                f"{name}: label(s) {missing} are declared and were not given; a "
                f"partially labelled sample would land in a series that no other "
                f"sample shares")
        for label, value in labels.items():
            if value not in spec.labels[label]:
                raise CardinalityError(
                    f"{name}: {value!r} is not a permitted value for label "
                    f"{label!r}. Permitted: {sorted(spec.labels[label])}")
        return dict(labels)
