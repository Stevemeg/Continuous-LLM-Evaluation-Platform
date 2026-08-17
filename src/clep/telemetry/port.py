"""The telemetry port: what the core emits through, and what may sit behind it.

ADR-022 rules 1, 2, 3 and 7. Nothing in this module imports a telemetry package,
and the default build of this project installs none — `REQ-N-OBS-3` is therefore
a property of the dependency graph rather than a claim about intent.

Three things happen here, in this order, and the order is the design:

1. **The catalogue validates.** An undeclared label raises before any backend is
   consulted, so cardinality is bounded in the core (ADR-009 rule 5) and stays
   bounded in the build that has no backend at all.
2. **The correlation is attached.** Produced by the core (ADR-022 rule 4), so the
   chain survives removing every backend.
3. **The backend is offered the sample, and is not allowed to matter.** Every
   exception a backend raises is caught and counted. A telemetry exporter that
   takes a gate evaluation down with it converts an observability problem into a
   verdict — the exact confusion `REQ-X-10` exists to prevent.

The asymmetry in 1 versus 3 is deliberate and is the one thing to understand
about this module. A `CardinalityError` is a *call site* that is wrong: static,
deterministic, and found by the tests that run against every build. A backend
failure is an *environment* that is wrong: transient, remote, and nothing the
platform can fix mid-request. The first must be loud; the second must be
invisible.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Mapping, Protocol, runtime_checkable

from clep.telemetry.catalog import CATALOGUE
from clep.telemetry.correlation import Correlation, current
from clep.telemetry.metrics import CardinalityError, MetricCatalogue, MetricSpec


@runtime_checkable
class TelemetryBackend(Protocol):
    """What a backend must provide. Deliberately two methods.

    A wide interface is one an adapter implements partially and a core comes to
    depend on unevenly, which is how "individually removable" (ADR-009 rule 2)
    stops being true.
    """

    def observe(self, spec: MetricSpec, value: float,
                labels: Mapping[str, str],
                correlation: Correlation | None) -> None:
        ...

    def event(self, name: str, attributes: Mapping[str, str],
              correlation: Correlation | None) -> None:
        ...


class NullBackend:
    """The default. Records nothing and cannot fail.

    Not a placeholder — this is the backend the product ships with, and the one
    every test run and every developer's machine exercises (ADR-022 rule 2).
    """

    def observe(self, spec, value, labels, correlation) -> None:
        return None

    def event(self, name, attributes, correlation) -> None:
        return None


class RecordingBackend:
    """An in-process backend that keeps what it was given.

    Its purpose is measurement of the port itself: `series()` is what makes
    `REQ-N-OBS-4` checkable behaviourally, by driving many tenants and many runs
    through the platform and observing that the number of series does not move.
    A source-text inspection cannot establish that, and this project has lost
    checks to source-text inspection before.
    """

    def __init__(self):
        self.samples: list[tuple[str, float, dict[str, str], str | None]] = []
        self.events: list[tuple[str, dict[str, str], str | None]] = []

    def observe(self, spec, value, labels, correlation) -> None:
        self.samples.append((spec.name, float(value), dict(labels),
                             correlation.correlation_id if correlation else None))

    def event(self, name, attributes, correlation) -> None:
        self.events.append((name, dict(attributes),
                            correlation.correlation_id if correlation else None))

    # ------------------------------------------------------------ inspection
    def series(self) -> set[tuple]:
        """Distinct (metric, labels) combinations seen. The cardinality figure."""
        return {(name, tuple(sorted(labels.items())))
                for name, _, labels, _ in self.samples}

    def names(self) -> set[str]:
        return {name for name, _, _, _ in self.samples}

    def correlations(self) -> set[str]:
        return {c for _, _, _, c in self.samples if c is not None}

    def values_for(self, name: str) -> list[float]:
        return [v for n, v, _, _ in self.samples if n == name]


class Telemetry:
    """The facade the rest of the platform calls."""

    def __init__(self, backend: TelemetryBackend | None = None, *,
                 catalogue: MetricCatalogue | None = None, clock=None):
        self._backend = backend or NullBackend()
        self._catalogue = catalogue or CATALOGUE
        self._clock = clock or time.monotonic
        #: Backend failures, counted rather than raised. Exposed so that a
        #: deployment can see telemetry is broken without telemetry being able
        #: to break anything else.
        self.backend_failures = 0

    @property
    def catalogue(self) -> MetricCatalogue:
        return self._catalogue

    @property
    def backend(self) -> TelemetryBackend:
        return self._backend

    def observe(self, name: str, value: float = 1.0, **labels: str) -> None:
        """Record one sample. Raises on a bad call site; never on a bad backend."""
        spec = self._catalogue.get(name)
        checked = self._catalogue.validate(name, labels)
        try:
            self._backend.observe(spec, float(value), checked, current())
        except Exception:  # noqa: BLE001 - ADR-022 rule 3, deliberately total
            self.backend_failures += 1

    def event(self, name: str, **attributes: str) -> None:
        """A named point in the chain, carrying the correlation and no metric.

        This is where an identifier legitimately travels: `observability-strategy.md`
        §3 puts tenant, project and run identifiers on traces and logs, and this
        is the trace side of that sentence.
        """
        try:
            self._backend.event(name, {k: str(v) for k, v in attributes.items()},
                                current())
        except Exception:  # noqa: BLE001 - ADR-022 rule 3
            self.backend_failures += 1

    @contextmanager
    def timed(self, name: str, **labels: str):
        """Time a block into a declared histogram.

        The labels are validated on the way in, not on the way out, so a bad call
        site fails before the work runs rather than after it — a `CardinalityError`
        raised in a `finally` would replace whatever the block was raising.
        """
        self._catalogue.validate(name, labels)
        started = self._clock()
        try:
            yield
        finally:
            elapsed_ms = max(0.0, (self._clock() - started) * 1000.0)
            self.observe(name, elapsed_ms, **labels)


#: The instance the platform uses when nothing was injected. A module-level
#: default rather than a global that services reach for: every service takes its
#: telemetry as a constructor argument, and this is only what `create_app`
#: composes when a deployment configured no backend.
NULL_TELEMETRY = Telemetry(NullBackend())


__all__ = ["CardinalityError", "NULL_TELEMETRY", "NullBackend",
           "RecordingBackend", "Telemetry", "TelemetryBackend"]
