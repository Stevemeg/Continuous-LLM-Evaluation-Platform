"""The OpenTelemetry backend: the neutral standard, behind the port.

Canonical §14 names OpenTelemetry as the vendor-neutral foundation, and ADR-022
rule 6 distinguishes it from a vendor adapter: OTLP is the standard the core
emits *through*, not a proprietary platform the core would depend *on*. A backend
for a proprietary platform is a different thing and carries the additional
licence review of ADR-009 rule 6.

**Every OpenTelemetry import is inside a function.** Importing this module
requires nothing; constructing the backend requires the extra. That is the whole
mechanism behind ADR-022 rule 2 — `pip install .` produces a working platform
with no telemetry dependency, and `pip install .[otel]` produces the same
platform with an exporter attached.

The failure mode this guards against is subtle and common: a module-level import
inside an "optional" package makes the package non-optional the moment anything
imports it for a type annotation, and nobody notices until a deployment without
the extra fails at start.
"""
from __future__ import annotations

#: Named here so the error message can name it, and so the Phase 13 validator can
#: check that `pyproject.toml` declares exactly these as an extra rather than as
#: a runtime dependency.
REQUIRED_DISTRIBUTIONS = ("opentelemetry-sdk", "opentelemetry-exporter-otlp")


class TelemetryExtraMissing(RuntimeError):
    """Raised when the OTLP backend is requested and its extra is not installed.

    Raised at *construction*, which happens once at composition time, and never
    at request time. ADR-022 rule 3: a missing backend degrades to the no-op
    recorder; it does not fail a request. `build` is the place a deployment
    finds out it asked for something it did not install.
    """


def available() -> bool:
    """Whether the extra is installed. Never raises."""
    try:  # pragma: no cover - exercised by whichever build has the extra
        import opentelemetry.sdk.metrics  # noqa: F401
        import opentelemetry.sdk.trace  # noqa: F401
        return True
    except ImportError:
        return False


def build(endpoint: str, *, service_name: str = "clep"):
    """Construct an OTLP-exporting backend, or say precisely what is missing."""
    if not available():
        raise TelemetryExtraMissing(
            f"the OTLP backend needs {', '.join(REQUIRED_DISTRIBUTIONS)}, which "
            f"this build does not have. Install them with `pip install "
            f"'clep[otel]'`, or configure no backend and the platform will run "
            f"with the no-op recorder, which is the default and is fully "
            f"supported (REQ-N-OBS-3).")
    return _OtlpBackend(endpoint=endpoint, service_name=service_name)


class _OtlpBackend:
    """Adapts the port to OpenTelemetry instruments.

    Constructed only through `build`, so the import below runs only when the
    extra is present. Instruments are created lazily per metric and cached,
    because OpenTelemetry refuses a duplicate instrument name and the port
    legitimately records the same metric many times.
    """

    def __init__(self, endpoint: str, service_name: str):  # pragma: no cover
        from opentelemetry import metrics, trace
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
            OTLPMetricExporter)
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=endpoint))
        self._provider = MeterProvider(resource=resource, metric_readers=[reader])
        self._meter = self._provider.get_meter("clep")
        self._tracer = trace.get_tracer("clep")
        self._instruments: dict[str, object] = {}
        self._metrics = metrics

    def _instrument(self, spec):  # pragma: no cover
        existing = self._instruments.get(spec.name)
        if existing is None:
            if spec.kind == "histogram":
                existing = self._meter.create_histogram(
                    spec.name, unit=spec.unit, description=spec.description)
            else:
                existing = self._meter.create_counter(
                    spec.name, unit=spec.unit, description=spec.description)
            self._instruments[spec.name] = existing
        return existing

    def observe(self, spec, value, labels, correlation):  # pragma: no cover
        # `labels` has already been validated against the catalogue's declared
        # enumerations before reaching here (ADR-009 rule 5: bounded in the
        # core), so this backend cannot widen cardinality even by accident.
        instrument = self._instrument(spec)
        if spec.kind == "histogram":
            instrument.record(float(value), attributes=dict(labels))
        else:
            instrument.add(float(value), attributes=dict(labels))

    def event(self, name, attributes, correlation):  # pragma: no cover
        # Identifiers travel here, on a span, and not onto a metric.
        payload = dict(attributes)
        if correlation is not None:
            payload["clep.correlation_id"] = correlation.correlation_id
        with self._tracer.start_as_current_span(name) as span:
            for key, value in payload.items():
                span.set_attribute(key, str(value))

    def shutdown(self):  # pragma: no cover
        self._provider.shutdown()
