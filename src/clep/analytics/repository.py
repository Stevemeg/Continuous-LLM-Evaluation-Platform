"""Reading analytics out of the evaluation record. Nothing here writes.

Every query runs under the caller's tenant session, so row-level security does
the isolation and this module does not have to remember to — the same rule the
evaluation-memory repository follows, and for the same reason.

Four rules shape the queries themselves.

**A metric is an evaluator definition's slug**, resolved through the evaluator
version each outcome names. That is what the gate compares, so it is what the
trend and the leaderboard report; a second definition of "metric" would let a
dashboard and a release decision disagree about the same word.

**A sample that was not scored contributes nothing, and never a zero.** Means
are taken over `resolution = 'scored'` alone (`REQ-F-08-5`, `REQ-X-8`). What the
excluded ones do is lower the observation count, which is reported, and mark the
figure incomplete, which is also reported.

**Every figure carries the runs behind it.** `REQ-F-11-6` is not satisfiable by
a number with a timestamp; it is satisfiable by a number that names its evidence,
so every row returned here carries `run_ids` and a sample count.

**A leaderboard is benchmark-scoped or it does not exist.** `leaderboard`
requires a suite version and raises without one. `REQ-F-11-2` says never a global
ranking, and an optional parameter defaulting to "all suites" is a global ranking
with extra steps.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import psycopg

from clep.analytics.completeness import Completeness, completeness_of
from clep.identity import ulid_to_uuid, uuid_to_ulid

#: Quantile points reported for every latency distribution. `REQ-F-11-3` asks
#: for tail latency by name, so the tail is not optional and not a separate
#: call: a p50 published without a p95 beside it is the figure that makes a
#: system with a slow tail look healthy.
QUANTILES = (Decimal("0.5"), Decimal("0.9"), Decimal("0.95"), Decimal("0.99"))


class AnalyticsError(ValueError):
    pass


@dataclass(frozen=True)
class TrendPoint:
    run_id: str
    metric_key: str
    mean_score: Decimal | None
    observations: int
    unresolved: int
    trigger: str
    run_completeness: str
    is_baseline: bool
    created_at: object
    completeness: Completeness


@dataclass(frozen=True)
class LeaderboardEntry:
    model_configuration_id: str
    model_identifier: str
    provider_slug: str
    metric_key: str
    mean_score: Decimal | None
    observations: int
    unresolved: int
    run_ids: tuple
    completeness: Completeness


@dataclass(frozen=True)
class Distribution:
    """A quantile summary, reported with the count it was taken over."""
    measured: int
    minimum: int | None
    quantiles: dict
    maximum: int | None

    def as_dict(self) -> dict:
        return {"measured": self.measured, "minimum": self.minimum,
                "maximum": self.maximum,
                "quantiles": {str(k): v for k, v in self.quantiles.items()}}


@dataclass(frozen=True)
class OperationalAnalytics:
    model_latency_ms: Distribution
    evaluator_latency_ms: Distribution
    successful_tasks: int
    prompt_tokens: int
    completion_tokens: int
    cost_total: Decimal
    cost_currency: str | None
    tokens_per_successful_task: Decimal | None
    cost_per_successful_task: Decimal | None
    run_ids: tuple
    completeness: Completeness


@dataclass(frozen=True)
class JudgeAnalytics:
    judgements: int
    scored: int
    abstained: int
    failed: int
    consensus_results: int
    agreed: int
    escalated: int
    disagreement_measured: int
    mean_disagreement: Decimal | None
    escalation_reasons: dict
    calibration: tuple
    run_ids: tuple
    completeness: Completeness


@dataclass(frozen=True)
class AgentAnalytics:
    samples_with_trajectory: int
    completed_tasks: int
    truncated_trajectories: int
    tool_calls: int
    failed_tool_calls: int
    tool_success_rate: Decimal | None
    samples_with_loops: int
    samples_with_retries: int
    trajectory_failures: int
    by_tool: tuple
    run_ids: tuple
    completeness: Completeness


class AnalyticsRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    # ------------------------------------------------------------------ REQ-F-11-1
    def quality_trend(self, project_id: str, *, suite_version_id: str | None = None,
                      metric_key: str | None = None,
                      window_days: int | None = None,
                      limit: int = 100) -> list[TrendPoint]:
        """One point per run per metric, oldest first.

        Ordered by the run's creation rather than by score, because a trend read
        in score order is not a trend. Baselines are flagged where they are, so
        the baseline-versus-candidate comparison `REQ-F-11-1` asks for is visible
        in the same series rather than requiring a second call.
        """
        clause, params = self._scope(project_id, suite_version_id, window_days,
                                     "r.created_at")
        metric_clause, metric_params = ("", [])
        if metric_key:
            metric_clause, metric_params = " AND ed.slug = %s", [metric_key]
        rows = self._conn.execute(
            "SELECT r.id, ed.slug, "
            "       avg(eo.score) FILTER (WHERE eo.resolution = 'scored'), "
            "       count(*) FILTER (WHERE eo.resolution = 'scored'), "
            "       count(*) FILTER (WHERE eo.resolution <> 'scored'), "
            "       r.trigger_kind, r.completeness, r.created_at, "
            "       bool_or(b.run_id IS NOT NULL) "
            "FROM clep.run r "
            "JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "  AND s.run_id = r.id "
            "JOIN clep.evaluator_outcome eo "
            "  ON eo.organization_id = s.organization_id "
            " AND eo.run_sample_id = s.id "
            "JOIN clep.evaluator_version ev ON ev.id = eo.evaluator_version_id "
            "JOIN clep.evaluator_definition ed "
            "  ON ed.id = ev.evaluator_definition_id "
            # Aggregated to at most one row per run before the join. A plain
            # LEFT JOIN would multiply the evaluator outcomes by the number of
            # approved baselines the run has, and the observation count — the
            # thing REQ-F-11-6 makes this figure traceable by — would silently
            # double.
            "LEFT JOIN (SELECT organization_id, run_id FROM clep.baseline "
            "           WHERE state = 'approved' "
            "           GROUP BY organization_id, run_id) b "
            "  ON b.organization_id = r.organization_id AND b.run_id = r.id "
            + clause + metric_clause +
            " GROUP BY r.id, ed.slug, r.trigger_kind, r.completeness, r.created_at"
            " ORDER BY r.created_at, r.id, ed.slug LIMIT %s",
            [*params, *metric_params, limit]).fetchall()
        return [
            TrendPoint(
                run_id=uuid_to_ulid(r[0]), metric_key=r[1], mean_score=r[2],
                observations=r[3], unresolved=r[4], trigger=r[5],
                run_completeness=r[6], created_at=r[7], is_baseline=bool(r[8]),
                completeness=completeness_of(
                    contributing_runs=1,
                    incomplete_runs=0 if r[6] == "complete" else 1,
                    observations=r[3], unresolved_observations=r[4]))
            for r in rows]

    # ------------------------------------------------------------------ REQ-F-11-2
    def leaderboard(self, project_id: str, *, suite_version_id: str,
                    window_days: int | None = None) -> list[LeaderboardEntry]:
        """Model and provider standings, within one named benchmark.

        `suite_version_id` is required and unconditional. A ranking without a
        stated benchmark invites exactly the decontextualised comparison
        canonical §25 rejects — "model A beats model B" with no statement of at
        what — so there is no code path here that produces one.
        """
        if not suite_version_id:
            raise AnalyticsError(
                "a leaderboard is scoped to a named benchmark; REQ-F-11-2 "
                "forbids a global ranking, and one without a suite version is "
                "a global ranking")
        clause, params = self._scope(project_id, suite_version_id, window_days,
                                     "r.created_at")
        rows = self._conn.execute(
            "SELECT mc.id, m.model_identifier, p.slug, ed.slug, "
            "       avg(eo.score) FILTER (WHERE eo.resolution = 'scored'), "
            "       count(*) FILTER (WHERE eo.resolution = 'scored'), "
            "       count(*) FILTER (WHERE eo.resolution <> 'scored'), "
            "       array_agg(DISTINCT r.id), "
            "       count(DISTINCT r.id), "
            "       count(DISTINCT r.id) FILTER (WHERE r.completeness <> 'complete') "
            "FROM clep.run r "
            "JOIN clep.run_candidate rc ON rc.organization_id = r.organization_id "
            "  AND rc.run_id = r.id "
            "JOIN clep.run_sample s ON s.organization_id = rc.organization_id "
            "  AND s.run_candidate_id = rc.id "
            "JOIN clep.evaluator_outcome eo "
            "  ON eo.organization_id = s.organization_id "
            " AND eo.run_sample_id = s.id "
            "JOIN clep.evaluator_version ev ON ev.id = eo.evaluator_version_id "
            "JOIN clep.evaluator_definition ed "
            "  ON ed.id = ev.evaluator_definition_id "
            "JOIN clep.model_configuration mc "
            "  ON mc.organization_id = rc.organization_id "
            " AND mc.id = rc.model_configuration_id "
            "JOIN clep.model m ON m.organization_id = mc.organization_id "
            "  AND m.id = mc.model_id "
            "JOIN clep.provider p ON p.organization_id = m.organization_id "
            "  AND p.id = m.provider_id "
            + clause +
            " GROUP BY mc.id, m.model_identifier, p.slug, ed.slug"
            " ORDER BY ed.slug, 5 DESC NULLS LAST, mc.id", params).fetchall()
        return [
            LeaderboardEntry(
                model_configuration_id=uuid_to_ulid(r[0]), model_identifier=r[1],
                provider_slug=r[2], metric_key=r[3], mean_score=r[4],
                observations=r[5], unresolved=r[6],
                run_ids=tuple(uuid_to_ulid(x) for x in r[7]),
                completeness=completeness_of(
                    contributing_runs=r[8], incomplete_runs=r[9],
                    observations=r[5], unresolved_observations=r[6]))
            for r in rows]

    # ------------------------------------------------------------------ REQ-F-11-3
    def operational(self, project_id: str, *, suite_version_id: str | None = None,
                    window_days: int | None = None) -> OperationalAnalytics:
        """Latency distributions including the tail, and cost per successful task.

        Two latencies, named apart. `model_latency_ms` is the provider call,
        measured at the gateway; `evaluator_latency_ms` is the time the
        evaluators took over that sample. They answer different questions and a
        single "latency" figure covering both would answer neither.

        "Per successful task" means per sample that resolved `scored`. Dividing
        by every attempted sample would make a run that failed half its calls
        look cheaper per task than one that succeeded at all of them.
        """
        clause, params = self._scope(project_id, suite_version_id, window_days,
                                     "r.created_at")
        totals = self._conn.execute(
            "SELECT count(*), "
            "       count(*) FILTER (WHERE s.resolution = 'scored'), "
            "       count(*) FILTER (WHERE s.resolution <> 'scored'), "
            "       count(DISTINCT r.id), "
            "       count(DISTINCT r.id) FILTER (WHERE r.completeness <> 'complete'), "
            "       coalesce(array_agg(DISTINCT r.id), '{}') "
            "FROM clep.run r "
            "JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "  AND s.run_id = r.id " + clause, params).fetchone()
        cost = self._conn.execute(
            "SELECT coalesce(sum(sc.prompt_tokens), 0), "
            "       coalesce(sum(sc.completion_tokens), 0), "
            "       coalesce(sum(sc.cost_amount), 0), min(sc.cost_currency) "
            "FROM clep.run r "
            "JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "  AND s.run_id = r.id "
            "JOIN clep.sample_cost sc ON sc.organization_id = s.organization_id "
            "  AND sc.run_sample_id = s.id "
            + clause + " AND s.resolution = 'scored'", params).fetchone()

        successful = totals[1]
        tokens = int(cost[0]) + int(cost[1])
        return OperationalAnalytics(
            model_latency_ms=self._distribution(
                "s.model_latency_ms", clause, params,
                extra=" AND s.model_latency_ms IS NOT NULL"),
            evaluator_latency_ms=self._evaluator_latency(clause, params),
            successful_tasks=successful,
            prompt_tokens=int(cost[0]), completion_tokens=int(cost[1]),
            cost_total=cost[2], cost_currency=cost[3],
            tokens_per_successful_task=(
                (Decimal(tokens) / Decimal(successful)).quantize(Decimal("0.001"))
                if successful else None),
            cost_per_successful_task=(
                (Decimal(cost[2]) / Decimal(successful)).quantize(Decimal("1e-9"))
                if successful else None),
            run_ids=tuple(uuid_to_ulid(x) for x in totals[5]),
            completeness=completeness_of(
                contributing_runs=totals[3], incomplete_runs=totals[4],
                observations=successful, unresolved_observations=totals[2]))

    # ------------------------------------------------------------------ REQ-F-11-4
    def judge_analytics(self, project_id: str, *,
                        window_days: int | None = None) -> JudgeAnalytics:
        """Agreement, disagreement, failure rates — and calibration, borrowed.

        Per-judge calibration is not recomputed here: `MemoryRepository` already
        defines what deviation from the ensemble means, and two definitions of
        one figure is how a dashboard and an escalation come to disagree about
        which judge is drifting.
        """
        from clep.memory.repository import MemoryRepository

        clause, params = self._scope(project_id, None, window_days, "r.created_at")
        judgements = self._conn.execute(
            "SELECT count(*), "
            "       count(*) FILTER (WHERE jr.resolution = 'scored'), "
            "       count(*) FILTER (WHERE jr.resolution = 'abstained'), "
            "       count(*) FILTER (WHERE jr.resolution NOT IN "
            "                        ('scored', 'abstained')) "
            "FROM clep.run r "
            "JOIN clep.judge_run jr ON jr.organization_id = r.organization_id "
            "  AND jr.run_id = r.id " + clause, params).fetchone()
        consensus = self._conn.execute(
            "SELECT count(*), "
            "       count(*) FILTER (WHERE cr.state = 'agreed'), "
            "       count(*) FILTER (WHERE cr.state = 'escalated'), "
            "       count(*) FILTER (WHERE cr.disagreement_measured), "
            "       avg(cr.disagreement) FILTER (WHERE cr.disagreement_measured), "
            "       count(DISTINCT r.id), "
            "       count(DISTINCT r.id) FILTER (WHERE r.completeness <> 'complete'), "
            "       coalesce(array_agg(DISTINCT r.id), '{}') "
            "FROM clep.run r "
            "JOIN clep.consensus_result cr "
            "  ON cr.organization_id = r.organization_id AND cr.run_id = r.id "
            + clause, params).fetchone()
        reasons = dict(self._conn.execute(
            "SELECT cr.escalation_reason, count(*) FROM clep.run r "
            "JOIN clep.consensus_result cr "
            "  ON cr.organization_id = r.organization_id AND cr.run_id = r.id "
            + clause + " AND cr.escalation_reason IS NOT NULL "
            " GROUP BY cr.escalation_reason", params).fetchall())
        calibration = MemoryRepository(self._conn, self._org).judge_calibration(
            project_id, window_days=window_days)
        return JudgeAnalytics(
            judgements=judgements[0], scored=judgements[1],
            abstained=judgements[2], failed=judgements[3],
            consensus_results=consensus[0], agreed=consensus[1],
            escalated=consensus[2], disagreement_measured=consensus[3],
            mean_disagreement=consensus[4], escalation_reasons=reasons,
            calibration=calibration,
            run_ids=tuple(uuid_to_ulid(x) for x in consensus[7]),
            completeness=completeness_of(
                contributing_runs=consensus[5], incomplete_runs=consensus[6],
                observations=judgements[1],
                unresolved_observations=judgements[2] + judgements[3]))

    # ------------------------------------------------------------------ REQ-F-11-5
    def agent_analytics(self, project_id: str, *,
                        suite_version_id: str | None = None,
                        window_days: int | None = None) -> AgentAnalytics:
        """Tool success, trajectory failures, loops, retries, task completion.

        Loops and retries are the same fact at two strengths, and the platform
        already distinguishes them: `trajectory.consecutive_repeats` is the shape
        that cannot be productive, and `repeated_calls` is one that may be. The
        SQL below is those two definitions, applied to the stored steps —
        identical consecutive calls for a loop, and any repeated signature for a
        retry — so a dashboard and the agent evaluator mean the same thing by
        each word.
        """
        clause, params = self._scope(project_id, suite_version_id, window_days,
                                     "r.created_at")
        steps = self._conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE ts.failed), "
            "       count(DISTINCT ts.run_sample_id), "
            "       count(DISTINCT r.id), "
            "       count(DISTINCT r.id) FILTER (WHERE r.completeness <> 'complete'), "
            "       coalesce(array_agg(DISTINCT r.id), '{}') "
            "FROM clep.run r "
            "JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "  AND s.run_id = r.id "
            "JOIN clep.trajectory_step ts "
            "  ON ts.organization_id = s.organization_id "
            " AND ts.run_sample_id = s.id " + clause, params).fetchone()
        samples = self._conn.execute(
            "SELECT count(DISTINCT s.id) FILTER (WHERE s.resolution = 'scored'), "
            "       count(DISTINCT s.id) FILTER (WHERE s.trajectory_truncated), "
            "       count(DISTINCT s.id) FILTER (WHERE s.resolution <> 'scored') "
            "FROM clep.run r "
            "JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "  AND s.run_id = r.id "
            "WHERE EXISTS (SELECT 1 FROM clep.trajectory_step ts "
            "              WHERE ts.organization_id = s.organization_id "
            "                AND ts.run_sample_id = s.id) "
            + clause.replace("WHERE", "AND", 1), params).fetchone()
        loops = self._conn.execute(
            "WITH ordered AS ("
            "  SELECT ts.run_sample_id AS sample_id, ts.tool, ts.arguments, "
            "         lag(ts.tool) OVER w AS previous_tool, "
            "         lag(ts.arguments) OVER w AS previous_arguments "
            "  FROM clep.run r "
            "  JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "    AND s.run_id = r.id "
            "  JOIN clep.trajectory_step ts "
            "    ON ts.organization_id = s.organization_id "
            "   AND ts.run_sample_id = s.id " + clause +
            "  WINDOW w AS (PARTITION BY ts.run_sample_id ORDER BY ts.step_order)) "
            "SELECT count(DISTINCT sample_id) FROM ordered "
            "WHERE tool = previous_tool AND arguments = previous_arguments",
            params).fetchone()
        retries = self._conn.execute(
            "WITH signatures AS ("
            "  SELECT ts.run_sample_id AS sample_id, ts.tool, ts.arguments, "
            "         count(*) AS occurrences "
            "  FROM clep.run r "
            "  JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "    AND s.run_id = r.id "
            "  JOIN clep.trajectory_step ts "
            "    ON ts.organization_id = s.organization_id "
            "   AND ts.run_sample_id = s.id " + clause +
            "  GROUP BY ts.run_sample_id, ts.tool, ts.arguments) "
            "SELECT count(DISTINCT sample_id) FROM signatures WHERE occurrences > 1",
            params).fetchone()
        by_tool = self._conn.execute(
            "SELECT ts.tool, count(*), count(*) FILTER (WHERE ts.failed) "
            "FROM clep.run r "
            "JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "  AND s.run_id = r.id "
            "JOIN clep.trajectory_step ts "
            "  ON ts.organization_id = s.organization_id "
            " AND ts.run_sample_id = s.id " + clause +
            " GROUP BY ts.tool ORDER BY ts.tool", params).fetchall()
        calls, failed = steps[0], steps[1]
        return AgentAnalytics(
            samples_with_trajectory=steps[2], completed_tasks=samples[0],
            truncated_trajectories=samples[1],
            tool_calls=calls, failed_tool_calls=failed,
            tool_success_rate=(
                (Decimal(calls - failed) / Decimal(calls)).quantize(Decimal("1e-9"))
                if calls else None),
            samples_with_loops=loops[0], samples_with_retries=retries[0],
            trajectory_failures=samples[2],
            by_tool=tuple({"tool": t, "calls": c, "failed": f,
                           "successRate": str((Decimal(c - f) / Decimal(c))
                                              .quantize(Decimal("1e-9")))}
                          for t, c, f in by_tool),
            run_ids=tuple(uuid_to_ulid(x) for x in steps[5]),
            completeness=completeness_of(
                contributing_runs=steps[3], incomplete_runs=steps[4],
                observations=samples[0], unresolved_observations=samples[2]))

    # ------------------------------------------------- figures for one run
    def figures_for_run(self, project_id: str, run_id: str) -> dict:
        """Every figure an alert rule may name, for one run, keyed by
        (dimension, metric key).

        Here rather than in `alerts` deliberately. A rule that fired on a number
        the analytics screen cannot reproduce is an alert nobody can act on, so
        the definition of each figure lives in one module and alerting applies
        thresholds to what this returns.
        """
        scope = ("WHERE r.organization_id = %s AND r.project_id = %s "
                 "AND r.id = %s ")
        params = [self._org, ulid_to_uuid(project_id), ulid_to_uuid(run_id)]
        figures: dict = {}

        for metric_key, mean, observations in self._conn.execute(
                "SELECT ed.slug, "
                "       avg(eo.score) FILTER (WHERE eo.resolution = 'scored'), "
                "       count(*) FILTER (WHERE eo.resolution = 'scored') "
                "FROM clep.run r "
                "JOIN clep.run_sample s "
                "  ON s.organization_id = r.organization_id AND s.run_id = r.id "
                "JOIN clep.evaluator_outcome eo "
                "  ON eo.organization_id = s.organization_id "
                " AND eo.run_sample_id = s.id "
                "JOIN clep.evaluator_version ev "
                "  ON ev.id = eo.evaluator_version_id "
                "JOIN clep.evaluator_definition ed "
                "  ON ed.id = ev.evaluator_definition_id "
                + scope + " GROUP BY ed.slug", params).fetchall():
            if mean is not None:
                figures[("quality", metric_key)] = (mean, observations)

        model = self._distribution("s.model_latency_ms", scope, params,
                                   extra=" AND s.model_latency_ms IS NOT NULL")
        evaluator = self._evaluator_latency(scope, params)
        for name, value in (
                ("model_latency_p50_ms", model.quantiles.get(Decimal("0.5"))),
                ("model_latency_p95_ms", model.quantiles.get(Decimal("0.95"))),
                ("model_latency_maximum_ms", model.maximum)):
            if value is not None:
                figures[("latency", name)] = (Decimal(value), model.measured)
        p95 = evaluator.quantiles.get(Decimal("0.95"))
        if p95 is not None:
            figures[("latency", "evaluator_latency_p95_ms")] = (
                Decimal(p95), evaluator.measured)

        cost = self._conn.execute(
            "SELECT coalesce(sum(sc.cost_amount), 0), "
            "       count(*) FILTER (WHERE s.resolution = 'scored') "
            "FROM clep.run r "
            "JOIN clep.run_sample s "
            "  ON s.organization_id = r.organization_id AND s.run_id = r.id "
            "LEFT JOIN clep.sample_cost sc "
            "  ON sc.organization_id = s.organization_id "
            " AND sc.run_sample_id = s.id " + scope, params).fetchone()
        total, successful = Decimal(cost[0]), cost[1]
        if successful:
            figures[("cost", "cost_total")] = (total, successful)
            figures[("cost", "cost_per_successful_task")] = (
                (total / Decimal(successful)).quantize(Decimal("1e-9")),
                successful)
        return figures

    # --------------------------------------------------------------- internals
    def _scope(self, project_id: str, suite_version_id: str | None,
               window_days: int | None, time_column: str) -> tuple[str, list]:
        """The shared WHERE clause: this tenant, this project, finished runs.

        `execution_state = 'terminal'` rather than `completeness = 'complete'`:
        an incomplete run is evidence, and excluding it would quietly answer a
        different question than the one asked. It is included and marked.
        """
        clause = ("WHERE r.organization_id = %s AND r.project_id = %s "
                  "AND r.execution_state = 'terminal'")
        params: list = [self._org, ulid_to_uuid(project_id)]
        if suite_version_id:
            clause += " AND r.suite_version_id = %s"
            params.append(ulid_to_uuid(suite_version_id))
        if window_days is not None:
            clause += f" AND {time_column} >= now() - make_interval(days => %s)"
            params.append(window_days)
        return clause + " ", params

    def _distribution(self, column: str, clause: str, params: list,
                      extra: str = "") -> Distribution:
        quantiles = ", ".join(
            f"percentile_disc({q}) WITHIN GROUP (ORDER BY {column})"
            for q in QUANTILES)
        row = self._conn.execute(
            f"SELECT count({column}), min({column}), max({column}), {quantiles} "
            "FROM clep.run r "
            "JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "  AND s.run_id = r.id " + clause + extra, params).fetchone()
        return Distribution(
            measured=row[0], minimum=row[1], maximum=row[2],
            quantiles={q: row[3 + i] for i, q in enumerate(QUANTILES)})

    def _evaluator_latency(self, clause: str, params: list) -> Distribution:
        """Per sample, the total time its evaluators took.

        Summed per sample rather than reported per evaluator outcome: the
        question `REQ-F-11-3` is asking is how long an evaluation took, and a
        distribution over individual evaluator calls answers how long one
        evaluator takes, which is a different and less useful number.
        """
        quantiles = ", ".join(
            f"percentile_disc({q}) WITHIN GROUP (ORDER BY total)"
            for q in QUANTILES)
        row = self._conn.execute(
            "WITH per_sample AS ("
            "  SELECT s.id, sum(eo.duration_ms) AS total "
            "  FROM clep.run r "
            "  JOIN clep.run_sample s ON s.organization_id = r.organization_id "
            "    AND s.run_id = r.id "
            "  JOIN clep.evaluator_outcome eo "
            "    ON eo.organization_id = s.organization_id "
            "   AND eo.run_sample_id = s.id " + clause +
            "  GROUP BY s.id) "
            f"SELECT count(total), min(total), max(total), {quantiles} "
            "FROM per_sample", params).fetchone()
        return Distribution(
            measured=row[0], minimum=row[1], maximum=row[2],
            quantiles={q: row[3 + i] for i, q in enumerate(QUANTILES)})
