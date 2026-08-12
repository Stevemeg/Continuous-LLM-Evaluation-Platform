"""Application service for analytics, the scorecard, and alerting.

Same rules as the other services: a tenant-bound session per call, the
organization from the ingress principal, responses in the contract's vocabulary.

The rule specific to this service is that almost all of it is read-only. Three
operations write — creating an alert rule, pausing one, and recording the
firings an evaluation produced — and each of those is configuration or a
historical fact, never a change to a production system. Nothing here decides
quality: every figure comes from the analytics repositories, which read the
evaluation record and compute nothing new.
"""
from __future__ import annotations

from decimal import Decimal

from clep.analytics import alerts as alerting
from clep.analytics import scorecard as scorecards
from clep.analytics.drift import DriftError, DriftRepository
from clep.analytics.repository import AnalyticsError, AnalyticsRepository
from clep.api import audit
from clep.db.session import tenant_session
from clep.identity import ulid_to_uuid


class AnalyticsService:
    def __init__(self, runtime_dsn: str):
        self._dsn = runtime_dsn

    # -------------------------------------------------------------- REQ-F-11-1
    def quality_trend(self, *, organization_id: str, project_id: str,
                      suite_version_id: str | None = None,
                      metric_key: str | None = None,
                      window_days: int | None = None, limit: int = 100) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            points = AnalyticsRepository(conn, organization_id).quality_trend(
                project_id, suite_version_id=suite_version_id,
                metric_key=metric_key, window_days=window_days, limit=limit)
        return {"items": [_trend_point(p) for p in points],
                "windowDays": window_days, "suiteVersionId": suite_version_id}

    # -------------------------------------------------------------- REQ-F-11-2
    def leaderboard(self, *, organization_id: str, project_id: str,
                    suite_version_id: str, window_days: int | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            entries = AnalyticsRepository(conn, organization_id).leaderboard(
                project_id, suite_version_id=suite_version_id,
                window_days=window_days)
        return {"suiteVersionId": suite_version_id, "windowDays": window_days,
                "items": [_leaderboard_entry(e) for e in entries]}

    # -------------------------------------------------------------- REQ-F-11-3
    def operational(self, *, organization_id: str, project_id: str,
                    suite_version_id: str | None = None,
                    window_days: int | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            figures = AnalyticsRepository(conn, organization_id).operational(
                project_id, suite_version_id=suite_version_id,
                window_days=window_days)
        return _operational(figures)

    # -------------------------------------------------------------- REQ-F-11-4
    def judges(self, *, organization_id: str, project_id: str,
               window_days: int | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            figures = AnalyticsRepository(conn, organization_id).judge_analytics(
                project_id, window_days=window_days)
        return _judges(figures)

    # -------------------------------------------------------------- REQ-F-11-5
    def agents(self, *, organization_id: str, project_id: str,
               suite_version_id: str | None = None,
               window_days: int | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            figures = AnalyticsRepository(conn, organization_id).agent_analytics(
                project_id, suite_version_id=suite_version_id,
                window_days=window_days)
        return _agents(figures)

    # -------------------------------------------------------------- REQ-F-11-1
    def rag(self, *, organization_id: str, project_id: str,
            suite_version_id: str | None = None,
            window_days: int | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            figures = AnalyticsRepository(conn, organization_id).rag_analytics(
                project_id, suite_version_id=suite_version_id,
                window_days=window_days)
        return _rag(figures)

    # -------------------------------------------------------------- REQ-F-10-4
    def drift(self, *, organization_id: str, project_id: str, run_id: str,
              suite_version_id: str, metric_key: str,
              minimum_history: int | None = None,
              tolerance: Decimal | None = None) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            if not _run_exists(conn, organization_id, run_id):
                return None
            analysis = DriftRepository(conn, organization_id).analyse(
                project_id, run_id=run_id, suite_version_id=suite_version_id,
                metric_key=metric_key, minimum_history=minimum_history,
                tolerance=tolerance)
        return analysis.as_dict()

    # -------------------------------------------------------------- REQ-F-11-8
    def scorecard(self, *, organization_id: str, project_id: str,
                  suite_version_id: str | None = None,
                  window_days: int | None = None,
                  minimum_history: int | None = None,
                  tolerance: Decimal | None = None,
                  representation: str = "json"):
        with tenant_session(self._dsn, organization_id) as conn:
            card = scorecards.build(
                conn, organization_id, project_id,
                suite_version_id=suite_version_id, window_days=window_days,
                minimum_history=minimum_history, drift_tolerance=tolerance)
        if representation == "markdown":
            return scorecards.human_readable(card)
        return scorecards.machine_readable(card)

    # -------------------------------------------------------------- REQ-F-11-9
    def create_alert_rule(self, *, organization_id: str, project_id: str,
                          actor_id: str, **rule) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repository = alerting.AlertRepository(conn, organization_id)
            rule_id = repository.create_rule(
                project_id=project_id, created_by=actor_id, **rule)
            audit.record(conn, organization_id, actor_id, "alert_rule.created",
                         "alert_rule", rule_id)
            return _alert_rule(repository.get_rule(rule_id))

    def pause_alert_rule(self, *, organization_id: str, rule_id: str,
                         actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repository = alerting.AlertRepository(conn, organization_id)
            if repository.get_rule(rule_id) is None:
                return None
            if repository.pause_rule(rule_id):
                audit.record(conn, organization_id, actor_id,
                             "alert_rule.paused", "alert_rule", rule_id)
            return _alert_rule(repository.get_rule(rule_id))

    def list_alert_rules(self, *, organization_id: str, project_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            rules = alerting.AlertRepository(conn, organization_id).list_rules(
                project_id)
        return {"items": [_alert_rule(r) for r in rules]}

    def evaluate_alerts(self, *, organization_id: str, run_id: str,
                        actor_id: str) -> dict | None:
        """Every active rule on the run's own project, against that run.

        The project is read from the run rather than taken from the caller: a
        caller who could name the project could have rules from one project
        evaluated against another's evidence.
        """
        with tenant_session(self._dsn, organization_id) as conn:
            row = conn.execute(
                "SELECT project_id, execution_state FROM clep.run "
                "WHERE organization_id = %s AND id = %s",
                (organization_id, ulid_to_uuid(run_id))).fetchone()
            if row is None:
                return None
            if row[1] != "terminal":
                raise alerting.AlertError(
                    "this run has not finished; alerting on a run still in "
                    "flight would fire on a figure that is still changing")
            from clep.identity import uuid_to_ulid
            outcomes = alerting.evaluate_run(
                conn, organization_id, project_id=uuid_to_ulid(row[0]),
                run_id=run_id)
            if any(o.fired for o in outcomes):
                audit.record(conn, organization_id, actor_id,
                             "alert.evaluated", "run", run_id)
        return {"runId": run_id,
                "items": [{"alertRuleId": o.rule_id, "slug": o.slug,
                           "outcome": o.outcome,
                           "observedValue": _str(o.observed_value),
                           "sampleSize": o.sample_size,
                           "alertEventId": o.event_id, "detail": o.detail}
                          for o in outcomes]}

    def list_alert_events(self, *, organization_id: str, project_id: str,
                          limit: int = 50) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            events = alerting.AlertRepository(
                conn, organization_id).events_for_project(project_id, limit)
        return {"items": [_alert_event(e) for e in events]}


# ------------------------------------------------------------------ presentation
def _trend_point(point) -> dict:
    return {"runId": point.run_id, "metricKey": point.metric_key,
            "meanScore": _str(point.mean_score),
            "observations": point.observations, "unresolved": point.unresolved,
            "trigger": point.trigger, "isBaseline": point.is_baseline,
            "runCompleteness": point.run_completeness,
            "createdAt": point.created_at.isoformat() if point.created_at else None,
            "completeness": point.completeness.as_dict()}


def _leaderboard_entry(entry) -> dict:
    return {"modelConfigurationId": entry.model_configuration_id,
            "modelIdentifier": entry.model_identifier,
            "providerSlug": entry.provider_slug, "metricKey": entry.metric_key,
            "meanScore": _str(entry.mean_score),
            "observations": entry.observations, "unresolved": entry.unresolved,
            "runIds": list(entry.run_ids),
            "completeness": entry.completeness.as_dict()}


def _operational(figures) -> dict:
    return {"modelLatencyMs": figures.model_latency_ms.as_dict(),
            "evaluatorLatencyMs": figures.evaluator_latency_ms.as_dict(),
            "successfulTasks": figures.successful_tasks,
            "promptTokens": figures.prompt_tokens,
            "completionTokens": figures.completion_tokens,
            "costTotal": _str(figures.cost_total),
            "costCurrency": figures.cost_currency,
            "tokensPerSuccessfulTask": _str(figures.tokens_per_successful_task),
            "costPerSuccessfulTask": _str(figures.cost_per_successful_task),
            "runIds": list(figures.run_ids),
            "completeness": figures.completeness.as_dict()}


def _judges(figures) -> dict:
    return {"judgements": figures.judgements, "scored": figures.scored,
            "abstained": figures.abstained, "failed": figures.failed,
            "consensusResults": figures.consensus_results,
            "agreed": figures.agreed, "escalated": figures.escalated,
            "disagreementMeasured": figures.disagreement_measured,
            "meanDisagreement": _str(figures.mean_disagreement),
            "escalationReasons": figures.escalation_reasons,
            "calibration": [{"judgeVersionId": c.judge_version_id,
                             "judgements": c.judgements,
                             "meanDeviation": _str(c.mean_deviation),
                             "deviationSpread": _str(c.deviation_spread),
                             "abstentionRate": _str(c.abstention_rate),
                             "failureRate": _str(c.failure_rate)}
                            for c in figures.calibration],
            "runIds": list(figures.run_ids),
            "completeness": figures.completeness.as_dict()}


def _agents(figures) -> dict:
    return {"samplesWithTrajectory": figures.samples_with_trajectory,
            "completedTasks": figures.completed_tasks,
            "truncatedTrajectories": figures.truncated_trajectories,
            "toolCalls": figures.tool_calls,
            "failedToolCalls": figures.failed_tool_calls,
            "toolSuccessRate": _str(figures.tool_success_rate),
            "samplesWithLoops": figures.samples_with_loops,
            "samplesWithRetries": figures.samples_with_retries,
            "trajectoryFailures": figures.trajectory_failures,
            "byTool": list(figures.by_tool), "runIds": list(figures.run_ids),
            "completeness": figures.completeness.as_dict()}


def _rag(figures) -> dict:
    return {"claimsAnalysed": figures.claims_analysed,
            "claimsNotAnalysable": figures.claims_not_analysable,
            "findings": figures.findings,
            "attributionStages": figures.attribution_stages,
            "retrievedContexts": figures.retrieved_contexts,
            "citedContexts": figures.cited_contexts,
            "samplesWithRetrieval": figures.samples_with_retrieval,
            "requiredContextsMissing": figures.required_contexts_missing,
            "runIds": list(figures.run_ids),
            "completeness": figures.completeness.as_dict()}


def _alert_rule(rule) -> dict:
    return {"id": rule.id, "projectId": rule.project_id, "slug": rule.slug,
            "displayName": rule.display_name, "dimension": rule.dimension,
            "metricKey": rule.metric_key, "direction": rule.direction,
            "threshold": str(rule.threshold),
            "minimumSampleSize": rule.minimum_sample_size, "state": rule.state}


def _alert_event(event) -> dict:
    return {"id": event.id, "alertRuleId": event.alert_rule_id,
            "runId": event.run_id, "observedValue": str(event.observed_value),
            "threshold": str(event.threshold), "sampleSize": event.sample_size,
            "evidenceCompleteness": event.evidence_completeness,
            "detail": event.detail,
            "firedAt": event.fired_at.isoformat() if event.fired_at else None}


def _run_exists(conn, organization_id: str, run_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM clep.run WHERE organization_id = %s AND id = %s",
        (organization_id, ulid_to_uuid(run_id))).fetchone() is not None


def _str(value) -> str | None:
    return None if value is None else str(value)


__all__ = ["AnalyticsService", "AnalyticsError", "DriftError"]
