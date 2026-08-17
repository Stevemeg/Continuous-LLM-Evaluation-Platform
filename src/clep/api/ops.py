"""The operational surface: a separate application, on purpose and by necessity.

ADR-024. Health, readiness and metrics are conventionally unauthenticated,
because the things that call them — an orchestrator's probe, a metrics scraper —
have no principal and cannot acquire one. The tenant application refuses to start
with an unguarded route: `_assert_every_route_is_guarded` walks what FastAPI
registered and raises unless every route carries the guard's marker, with no
exemption list. That is ADR-020 rule 6, and it was chosen precisely so that
"forgetting is not expressible".

So this is not a stylistic separation. Adding `/metrics` to the tenant
application either fails to start, or starts because somebody created the first
entry in an exemption list — and the second outcome is worse than the first.

**Nothing here is tenant-scoped, and nothing here can become tenant-scoped by
accident.** The metric series rendered below carry only labels the catalogue
declared, and the catalogue cannot express an organization, project, run or
correlation identifier because no bounded enumeration of those exists to declare
(ADR-022 rule 5). ADR-024 rule 3's prohibition and its mechanism were designed
together, in that order.

Liveness and readiness are separate answers. Liveness says whether the process
should be restarted; readiness says whether it should receive traffic. A process
whose database is unreachable is *not ready*, which is not the same as unhealthy,
and collapsing the two makes a dependency outage look like a defect in this
system — the operational form of the `REQ-X-10` category error.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse

#: Prometheus' own content type for the text exposition format.
EXPOSITION_MEDIA_TYPE = "text/plain; version=0.0.4; charset=utf-8"


def create_ops_app(*, backend=None, dependencies=None, version: str = "0.1.0"
                   ) -> FastAPI:
    """Build the operational application.

    `dependencies` maps a name to a zero-argument callable returning True when
    that dependency is reachable. They are injected rather than constructed here
    so that readiness checks connectivity the deployment actually cares about,
    and so that a probe cannot become a tenant query by someone adding one.
    """
    app = FastAPI(title="clep operations", version=version, openapi_url=None)
    app.state.backend = backend
    app.state.dependencies = dict(dependencies or {})

    @app.get("/health")
    async def health():
        """Liveness. Answers whether this process should be restarted.

        Deliberately does not touch a dependency. A liveness probe that fails
        when the database is down causes the orchestrator to restart a process
        that was working correctly, turning one outage into two.
        """
        return {"status": "alive", "version": version}

    @app.get("/ready")
    async def ready():
        """Readiness. Answers whether this process should receive traffic.

        Reports reachability and never data: each probe is a connectivity check,
        not a query, so readiness cannot become a route that reads a tenant's
        rows without a principal.
        """
        results, ok = {}, True
        for name, probe in app.state.dependencies.items():
            try:
                reachable = bool(probe())
            except Exception:  # noqa: BLE001 - an unreachable dependency raises
                reachable = False
            results[name] = "reachable" if reachable else "unreachable"
            ok = ok and reachable
        body = {"status": "ready" if ok else "not_ready", "dependencies": results}
        # 503 for not-ready is the orchestrator's convention and is about this
        # process's readiness to serve. No tenant ever sees it; a tenant that
        # calls during an outage receives the honest refusal from the tenant API
        # (REQ-F-09-5), which is a different surface saying a different thing.
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/metrics")
    async def metrics():
        if app.state.backend is None:
            # Empty rather than absent. A scraper that gets a 404 reports the
            # endpoint as broken; an empty exposition is a truthful "this build
            # has no metrics backend configured".
            return PlainTextResponse("", media_type=EXPOSITION_MEDIA_TYPE)
        return PlainTextResponse(app.state.backend.render(),
                                 media_type=EXPOSITION_MEDIA_TYPE)

    return app


#: Operator alert conditions — `observability-strategy.md` §6's other audience.
#:
#: These are not `clep.alert_rule` rows and deliberately so. That table is
#: tenant- and project-scoped, with `organization_id NOT NULL` and a foreign key
#: to a project, because a quality-drift rule belongs to somebody's project. A
#: provider outage belongs to nobody's project. Putting these there would mean a
#: nullable project on a tenant table, which is the ADR-010 rule 4 exception
#: being widened for an operational convenience.
#:
#: What is reused is the *shape* Phase 11 established — a named condition, a
#: metric, a direction, a threshold and a minimum sample — so an operator alert
#: is read the same way a tenant alert is, without a second subsystem to keep
#: consistent. ADR-024 rule 7.
OPERATOR_CONDITIONS = (
    {"slug": "provider_outage", "metric": "clep_provider_call_total",
     "labels": {"outcome": "provider_outage"}, "direction": "lower_is_better",
     "audience": "operator",
     "why": "REQ-N-REL-4. A provider that is down is not the tenant's problem "
            "and must not reach the tenant as a quality result."},
    {"slug": "audit_write_failure", "metric": "clep_failure_attribution_total",
     "labels": {"attribution": "platform", "surface": "api"},
     "direction": "lower_is_better", "audience": "operator",
     "why": "An audit write that fails makes a governed action unprovable, and "
            "I-35 says an unaudited action must not be possible."},
    {"slug": "queue_depth", "metric": "clep_work_unit_queue_duration_ms",
     "labels": {"queue": "default"}, "direction": "lower_is_better",
     "audience": "operator",
     "why": "Queue time growing while execution time does not is contention, "
            "which no tenant can act on and every operator must."},
    {"slug": "coordination_store_unavailable",
     "metric": "clep_retry_total",
     "labels": {"surface": "worker", "retryable": "retryable"},
     "direction": "lower_is_better", "audience": "operator",
     "why": "Redelivery rising is the coordination store failing underneath "
            "work that otherwise looks successful."},
)


def operator_conditions() -> tuple[dict, ...]:
    """The operator audience. An operator is never paged for a tenant's quality
    regression, and a tenant is never alerted for platform degradation —
    conflating the two is the known way to make both audiences mute their
    alerts."""
    return OPERATOR_CONDITIONS
