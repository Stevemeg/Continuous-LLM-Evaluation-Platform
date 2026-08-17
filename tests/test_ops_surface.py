"""ADR-024: the operational surface, and the boundary it must not cross.

The most important test in this file is the one that shows the separation is
*forced* rather than stylistic — adding an unguarded route to the tenant
application does not start.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clep.api import contract
from clep.api.app import _assert_every_route_is_guarded
from clep.api.ops import EXPOSITION_MEDIA_TYPE, create_ops_app, operator_conditions
from clep.telemetry import CATALOGUE, Telemetry, correlated
from clep.telemetry.exposition import PrometheusBackend

ORG = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


def _driven_backend():
    backend = PrometheusBackend()
    t = Telemetry(backend)
    with correlated():
        t.observe("clep_request_outcome_total", 1, outcome_class="success")
        t.observe("clep_gate_decision_duration_ms", 42.0, verdict="pass")
        t.observe("clep_run_terminal_total", 1, completeness="partial")
    return backend


# ------------------------------------------------ the boundary is forced
def test_an_unguarded_route_on_the_tenant_application_does_not_start():
    """ADR-020 rule 6, still absolute. This is why /metrics lives elsewhere:
    the alternative does not start, and the only way to make it start is to
    create the first entry in an exemption list."""
    app = FastAPI()

    @app.get("/metrics")
    async def _metrics():
        return "would have been convenient"

    with pytest.raises(contract.ContractError) as exc:
        _assert_every_route_is_guarded(app)
    assert "/metrics" in str(exc.value)


def test_the_operational_endpoints_are_absent_from_the_tenant_contract():
    """ADR-024 rule 4. A client discovering the contract discovers no
    operational endpoint."""
    paths = set(contract.load()["paths"])
    for path in ("/health", "/ready", "/metrics"):
        assert path not in paths


# ----------------------------------------------------- liveness/readiness
def test_liveness_does_not_touch_a_dependency():
    """A liveness probe that fails when the database is down causes a restart
    of a process that was working, turning one outage into two."""
    calls = []
    app = create_ops_app(dependencies={"db": lambda: calls.append(1) or True})
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "alive"
    assert calls == [], "liveness consulted a dependency"


def test_readiness_reports_every_dependency_and_fails_when_one_is_unreachable():
    app = create_ops_app(dependencies={"db": lambda: True, "redis": lambda: False})
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "not_ready"
    assert body["dependencies"] == {"db": "reachable", "redis": "unreachable"}


def test_a_dependency_that_raises_is_unreachable_rather_than_a_crash():
    def _explodes():
        raise ConnectionError("no route to host")

    app = create_ops_app(dependencies={"db": _explodes})
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 503
    assert response.json()["dependencies"]["db"] == "unreachable"


def test_readiness_is_ready_when_everything_is_reachable():
    app = create_ops_app(dependencies={"db": lambda: True})
    with TestClient(app) as client:
        response = client.get("/ready")
    assert response.status_code == 200
    assert response.json()["status"] == "ready"


# ------------------------------------------------------------- /metrics
def test_metrics_renders_the_exposition_format():
    app = create_ops_app(backend=_driven_backend())
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "# TYPE clep_request_outcome_total counter" in body
    assert 'clep_request_outcome_total{outcome_class="success"} 1' in body
    assert "# TYPE clep_gate_decision_duration_ms histogram" in body
    assert 'clep_gate_decision_duration_ms_bucket{verdict="pass",le="50"} 1' in body
    assert 'clep_gate_decision_duration_ms_count{verdict="pass"} 1' in body


def test_metrics_with_no_backend_is_empty_rather_than_missing():
    """A 404 tells a scraper the endpoint is broken. An empty exposition tells
    it the truth: this build has no metrics backend configured."""
    app = create_ops_app()
    with TestClient(app) as client:
        response = client.get("/metrics")
    assert response.status_code == 200
    assert response.text == ""
    assert response.headers["content-type"] == EXPOSITION_MEDIA_TYPE


def test_no_tenant_identity_reaches_the_operational_surface():
    """ADR-024 rule 3, checked over the rendered bytes rather than over intent.

    It holds because of ADR-022 rule 5 rather than because of care here: the
    catalogue cannot express an organization or run label, so there is nothing
    for this surface to leak.
    """
    backend = PrometheusBackend()
    t = Telemetry(backend)
    for _ in range(25):
        with correlated() as c:
            t.observe("clep_request_outcome_total", 1, outcome_class="success")
            correlation = c.correlation_id
    app = create_ops_app(backend=backend)
    with TestClient(app) as client:
        body = client.get("/metrics").text + client.get("/ready").text \
            + client.get("/health").text

    assert ORG not in body
    assert correlation not in body
    for forbidden in ("organization", "tenant", "project_id", "run_id",
                      "correlation"):
        assert forbidden not in body, f"{forbidden!r} reached the ops surface"


def test_the_rendered_series_count_does_not_grow_with_correlations():
    """The exposition is the surface where an unbounded label would actually
    cost money, so cardinality is asserted after rendering, not before."""
    backend = PrometheusBackend()
    t = Telemetry(backend)
    for _ in range(100):
        with correlated():
            t.observe("clep_request_outcome_total", 1, outcome_class="success")
    app = create_ops_app(backend=backend)
    with TestClient(app) as client:
        lines = [l for l in client.get("/metrics").text.splitlines()
                 if l and not l.startswith("#")]
    assert len(lines) == 1
    assert lines[0].endswith(" 100")


def test_an_event_carrying_identifiers_is_not_rendered_as_a_metric():
    """Events are trace data and carry identifiers. The backend accepts them
    and renders nothing, which is what keeps /metrics free of them."""
    backend = PrometheusBackend()
    t = Telemetry(backend)
    with correlated() as c:
        t.event("run.started", run_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
    app = create_ops_app(backend=backend)
    with TestClient(app) as client:
        body = client.get("/metrics").text
    assert body == ""
    assert c.correlation_id not in body


# --------------------------------------------------- operator alerting
def test_operator_conditions_name_declared_metrics_and_declared_labels():
    """A condition on a metric that does not exist is a condition that never
    fires, which is worse than no alert because somebody believes it works."""
    for condition in operator_conditions():
        spec = CATALOGUE.get(condition["metric"])
        assert condition["audience"] == "operator"
        assert condition["why"].strip()
        for label, value in condition["labels"].items():
            assert label in spec.labels, (condition["slug"], label)
            assert value in spec.labels[label], (condition["slug"], label, value)


def test_every_operator_condition_is_about_the_platform_not_a_tenant():
    """observability-strategy.md §6. An operator must never be paged for a
    tenant's quality regression."""
    slugs = {c["slug"] for c in operator_conditions()}
    assert slugs == {"provider_outage", "audit_write_failure", "queue_depth",
                     "coordination_store_unavailable"}
    for condition in operator_conditions():
        spec = CATALOGUE.get(condition["metric"])
        assert spec.metric_class in ("provider_behaviour", "errors",
                                     "queue_time", "retries")
