"""Vendor-neutral telemetry: the port, the correlation chain, and the catalogue.

ADR-009 decided this package exists and what it may depend on; ADR-022 decided
what it is. Nothing here imports a telemetry package, a metrics client or a
vendor SDK — only the standard library and two pure-stdlib project modules,
`clep.identity` for the identifier form and `clep.security.privacy` for the
redaction the log surface must not re-implement. That is what makes
`REQ-N-OBS-3` checkable rather than claimed: the default build of this project
installs no telemetry dependency at all, and the build that has one gets it
through an optional extra.
"""
from clep.telemetry.catalog import CATALOGUE, build_catalogue
from clep.telemetry.correlation import (Correlation, correlated, current,
                                        current_id, new_correlation,
                                        sanitize_inbound)
from clep.telemetry.logs import (Classified, ContentCapture, ListSink,
                                 StructuredLogger)
from clep.telemetry.metrics import (METRIC_CLASSES, CardinalityError,
                                    MetricCatalogue, MetricSpec)
from clep.telemetry.port import (NULL_TELEMETRY, NullBackend, RecordingBackend,
                                 Telemetry, TelemetryBackend)

__all__ = [
    "CATALOGUE", "METRIC_CLASSES", "NULL_TELEMETRY", "CardinalityError",
    "Classified", "ContentCapture", "Correlation", "ListSink",
    "MetricCatalogue", "MetricSpec", "NullBackend", "RecordingBackend",
    "StructuredLogger", "Telemetry", "TelemetryBackend", "build_catalogue",
    "correlated", "current", "current_id", "new_correlation", "sanitize_inbound",
]
