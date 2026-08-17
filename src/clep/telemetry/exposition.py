"""A metrics backend that accumulates and renders, with no dependency at all.

ADR-022 rule 1: the core depends on no telemetry package. Canonical §14 names
Prometheus for platform metrics, and the Prometheus exposition format is plain
text with a published grammar — so rendering it needs a formatter, not a client
library. That keeps `/metrics` working in the default build, which is the build
`REQ-N-OBS-3` is about.

The histogram is a bucketed counter rather than a reservoir. Buckets are declared
up front and shared by every histogram, which keeps series count a function of
the declaration exactly as ADR-022 rule 5 requires; a reservoir would keep raw
observations and its memory would grow with traffic, which is the same failure
`REQ-N-OBS-4` is about wearing different clothes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

#: Milliseconds. Chosen to span the range the platform actually operates in --
#: a gate decision measured at tens of milliseconds, a provider call at
#: hundreds to thousands -- rather than to be a round set.
BUCKETS_MS = (1.0, 5.0, 10.0, 25.0, 50.0, 100.0, 250.0, 500.0, 1000.0,
              2500.0, 5000.0, 10000.0)


@dataclass
class _Series:
    count: float = 0.0
    total: float = 0.0
    buckets: dict[float, float] = field(default_factory=dict)


class PrometheusBackend:
    """Accumulates samples and renders them in the Prometheus text format.

    Carries no correlation identifier into any series. The `correlation`
    argument arrives and is ignored, deliberately and visibly: it is a trace and
    log dimension, and a backend that quietly promoted it to a label would
    defeat the refusal in the catalogue by going around it.
    """

    def __init__(self, buckets=BUCKETS_MS):
        self._buckets = tuple(sorted(buckets))
        self._series: dict[tuple, _Series] = {}
        self._specs: dict[str, object] = {}
        self._lock = threading.Lock()

    def observe(self, spec, value, labels, correlation) -> None:
        key = (spec.name, tuple(sorted(labels.items())))
        with self._lock:
            self._specs[spec.name] = spec
            series = self._series.get(key)
            if series is None:
                series = self._series[key] = _Series(
                    buckets={b: 0.0 for b in self._buckets})
            series.count += 1
            series.total += float(value)
            if spec.kind == "histogram":
                for bound in self._buckets:
                    if float(value) <= bound:
                        series.buckets[bound] += 1

    def event(self, name, attributes, correlation) -> None:
        # Events are trace data. They carry identifiers, which is exactly why
        # they are not rendered here: /metrics is the surface ADR-024 rule 3
        # forbids tenant identity on.
        return None

    def series(self) -> set[tuple]:
        with self._lock:
            return set(self._series)

    def render(self) -> str:
        """Prometheus text exposition. One HELP and TYPE per metric."""
        lines: list[str] = []
        with self._lock:
            by_metric: dict[str, list[tuple]] = {}
            for (name, labels), series in sorted(self._series.items()):
                by_metric.setdefault(name, []).append((labels, series))
            for name in sorted(by_metric):
                spec = self._specs[name]
                lines.append(f"# HELP {name} {spec.description}")
                lines.append(f"# TYPE {name} "
                             f"{'histogram' if spec.kind == 'histogram' else 'counter'}")
                for labels, series in by_metric[name]:
                    rendered = _labels(labels)
                    if spec.kind == "histogram":
                        for bound in self._buckets:
                            lines.append(
                                f"{name}_bucket"
                                f"{_labels(labels + (('le', _num(bound)),))} "
                                f"{_num(series.buckets[bound])}")
                        lines.append(
                            f"{name}_bucket"
                            f"{_labels(labels + (('le', '+Inf'),))} "
                            f"{_num(series.count)}")
                        lines.append(f"{name}_sum{rendered} {_num(series.total)}")
                        lines.append(f"{name}_count{rendered} {_num(series.count)}")
                    else:
                        lines.append(f"{name}{rendered} {_num(series.total)}")
        return "\n".join(lines) + ("\n" if lines else "")


def _labels(pairs) -> str:
    if not pairs:
        return ""
    inner = ",".join(f'{k}="{_escape(str(v))}"' for k, v in pairs)
    return "{" + inner + "}"


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _num(value) -> str:
    if isinstance(value, str):
        return value
    if float(value).is_integer():
        return str(int(value))
    return repr(float(value))
