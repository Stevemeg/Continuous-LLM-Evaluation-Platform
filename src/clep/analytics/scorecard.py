"""The executive scorecard — `REQ-F-11-8`.

"Suitable for a non-specialist reader, without discarding the incompleteness and
uncertainty qualifications." The second half is the requirement. Any report can
be made readable by removing the caveats, and that is precisely the report this
one must not be: a summary that drops "computed over a run that was cancelled
halfway" is not a simplification, it is a different claim.

So three rules hold throughout.

**Every figure carries its evidence.** A number appears with the observation
count behind it and the runs it came from. `REQ-F-11-6` requires that of the
analytics; a scorecard that quoted the number alone would be the one export where
it stopped being true.

**Every incomplete figure says so, in plain words.** Not a footnote marker, not a
colour: the sentence sits with the number. `REQ-F-11-7` says "in every view and
export in which it appears", and this is the export most likely to be read by
someone who will not go looking for the footnote.

**What the platform has not established is stated.** Judge accuracy, judge
quality, statistical calibration, agreement and hallucination thresholds, and
hosted-provider behaviour are open questions in this product. A scorecard that
presented judge agreement without saying that the threshold behind it is
uncalibrated would be inviting a decision the evidence does not support. That
section is not decoration and it is not removable.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from clep.analytics.alerts import AlertRepository
from clep.analytics.completeness import INCOMPLETE
from clep.analytics.drift import DriftRepository
from clep.analytics.repository import AnalyticsRepository

#: Standing limitations of this product, not findings about a project. They are
#: listed on every scorecard because their absence would read as their absence
#: from the risk register — and each one is a place where a confident-looking
#: figure would be a figure nobody has justified.
UNCALIBRATED = (
    "Judge accuracy and judge quality are not established. A judge that is "
    "consistently wrong looks exactly like a judge that is consistently right.",
    "The agreement threshold that decides when judges are said to disagree is "
    "configured per ensemble and has not been calibrated against human "
    "judgement.",
    "Hallucination support and contradiction thresholds are configured, not "
    "calibrated.",
    "No figure here has been validated against a hosted provider; the "
    "evaluations behind it ran against the endpoints this deployment "
    "configured.",
)


@dataclass(frozen=True)
class Scorecard:
    project_id: str
    suite_version_id: str | None
    window_days: int | None
    generated_at: object
    trend: tuple
    leaderboard: tuple
    operational: object
    judges: object
    agents: object
    drift: tuple
    alerts: tuple
    alert_rules: int


def build(conn, organization_id: str, project_id: str, *,
          suite_version_id: str | None = None, window_days: int | None = None,
          metric_key: str | None = None, minimum_history: int | None = None,
          drift_tolerance: Decimal | None = None, now=None) -> Scorecard:
    """Assemble the scorecard from the analytics, computing nothing new.

    Every figure comes from `AnalyticsRepository`, `DriftRepository` or
    `AlertRepository`. A scorecard that computed its own version of a number
    would eventually disagree with the screen the reader checks next.
    """
    from datetime import datetime, timezone

    analytics = AnalyticsRepository(conn, organization_id)
    trend = analytics.quality_trend(project_id, suite_version_id=suite_version_id,
                                    metric_key=metric_key,
                                    window_days=window_days)
    leaderboard = (tuple(analytics.leaderboard(project_id,
                                               suite_version_id=suite_version_id,
                                               window_days=window_days))
                   if suite_version_id else ())
    drift = ()
    if suite_version_id and trend:
        latest = trend[-1]
        metrics = sorted({p.metric_key for p in trend})
        repository = DriftRepository(conn, organization_id)
        drift = tuple(
            repository.analyse(project_id, run_id=latest.run_id,
                               suite_version_id=suite_version_id,
                               metric_key=key, minimum_history=minimum_history,
                               tolerance=drift_tolerance)
            for key in metrics)
    alerts_repository = AlertRepository(conn, organization_id)
    return Scorecard(
        project_id=project_id, suite_version_id=suite_version_id,
        window_days=window_days,
        generated_at=now or datetime.now(timezone.utc),
        trend=tuple(trend), leaderboard=leaderboard,
        operational=analytics.operational(project_id,
                                          suite_version_id=suite_version_id,
                                          window_days=window_days),
        judges=analytics.judge_analytics(project_id, window_days=window_days),
        agents=analytics.agent_analytics(project_id,
                                         suite_version_id=suite_version_id,
                                         window_days=window_days),
        drift=drift,
        alerts=tuple(alerts_repository.events_for_project(project_id)),
        alert_rules=len(alerts_repository.list_rules(project_id)))


def machine_readable(card: Scorecard) -> dict:
    operational = card.operational
    return {
        "projectId": card.project_id,
        "suiteVersionId": card.suite_version_id,
        "windowDays": card.window_days,
        "generatedAt": card.generated_at.isoformat(),
        "qualityTrend": [
            {"runId": p.run_id, "metricKey": p.metric_key,
             "meanScore": _str(p.mean_score), "observations": p.observations,
             "trigger": p.trigger, "isBaseline": p.is_baseline,
             "runCompleteness": p.run_completeness,
             "createdAt": p.created_at.isoformat() if p.created_at else None,
             "completeness": p.completeness.as_dict()}
            for p in card.trend],
        "leaderboard": [
            {"modelConfigurationId": e.model_configuration_id,
             "modelIdentifier": e.model_identifier,
             "providerSlug": e.provider_slug, "metricKey": e.metric_key,
             "meanScore": _str(e.mean_score), "observations": e.observations,
             "runIds": list(e.run_ids),
             "completeness": e.completeness.as_dict()}
            for e in card.leaderboard],
        "operational": {
            "modelLatencyMs": operational.model_latency_ms.as_dict(),
            "evaluatorLatencyMs": operational.evaluator_latency_ms.as_dict(),
            "successfulTasks": operational.successful_tasks,
            "promptTokens": operational.prompt_tokens,
            "completionTokens": operational.completion_tokens,
            "costTotal": _str(operational.cost_total),
            "costCurrency": operational.cost_currency,
            "tokensPerSuccessfulTask": _str(
                operational.tokens_per_successful_task),
            "costPerSuccessfulTask": _str(operational.cost_per_successful_task),
            "runIds": list(operational.run_ids),
            "completeness": operational.completeness.as_dict()},
        "judges": {
            "judgements": card.judges.judgements, "scored": card.judges.scored,
            "abstained": card.judges.abstained, "failed": card.judges.failed,
            "consensusResults": card.judges.consensus_results,
            "agreed": card.judges.agreed, "escalated": card.judges.escalated,
            "disagreementMeasured": card.judges.disagreement_measured,
            "meanDisagreement": _str(card.judges.mean_disagreement),
            "escalationReasons": card.judges.escalation_reasons,
            "calibration": [
                {"judgeVersionId": c.judge_version_id,
                 "judgements": c.judgements,
                 "meanDeviation": _str(c.mean_deviation),
                 "deviationSpread": _str(c.deviation_spread),
                 "abstentionRate": _str(c.abstention_rate),
                 "failureRate": _str(c.failure_rate)}
                for c in card.judges.calibration],
            "runIds": list(card.judges.run_ids),
            "completeness": card.judges.completeness.as_dict()},
        "agents": {
            "samplesWithTrajectory": card.agents.samples_with_trajectory,
            "completedTasks": card.agents.completed_tasks,
            "truncatedTrajectories": card.agents.truncated_trajectories,
            "toolCalls": card.agents.tool_calls,
            "failedToolCalls": card.agents.failed_tool_calls,
            "toolSuccessRate": _str(card.agents.tool_success_rate),
            "samplesWithLoops": card.agents.samples_with_loops,
            "samplesWithRetries": card.agents.samples_with_retries,
            "trajectoryFailures": card.agents.trajectory_failures,
            "byTool": list(card.agents.by_tool),
            "runIds": list(card.agents.run_ids),
            "completeness": card.agents.completeness.as_dict()},
        "drift": [d.as_dict() for d in card.drift],
        "alerts": [
            {"id": e.id, "alertRuleId": e.alert_rule_id, "runId": e.run_id,
             "observedValue": _str(e.observed_value),
             "threshold": _str(e.threshold), "sampleSize": e.sample_size,
             "evidenceCompleteness": e.evidence_completeness,
             "detail": e.detail,
             "firedAt": e.fired_at.isoformat() if e.fired_at else None}
            for e in card.alerts],
        "alertRules": card.alert_rules,
        "notEstablished": list(UNCALIBRATED),
    }


def human_readable(card: Scorecard) -> str:
    """A page a non-specialist can read, with every caveat still on it."""
    out: list[str] = []
    window = (f"the last {card.window_days} day(s)" if card.window_days
              else "all recorded history")
    out.append("# AI quality scorecard")
    out.append("")
    out.append(f"Project `{card.project_id}` · {window} · generated "
               f"{card.generated_at.isoformat()}")
    if card.suite_version_id:
        out.append(f"Benchmark: suite version `{card.suite_version_id}`. Every "
                   f"comparison below is within this benchmark and means "
                   f"nothing outside it.")
    else:
        out.append("No benchmark was named, so there is no leaderboard and no "
                   "drift analysis: a ranking without a stated benchmark is "
                   "the comparison this product refuses to make.")
    out.append("")

    out.append("## Quality over time")
    out.append("")
    if not card.trend:
        out.append("No finished run in this window produced a score. That is "
                   "not a good result or a bad one — it is an absence of "
                   "evidence.")
    else:
        out.append("| Run | Metric | Mean | Observations | Why it ran | Notes |")
        out.append("|---|---|---|---|---|---|")
        for point in card.trend[-12:]:
            note = "baseline" if point.is_baseline else ""
            if point.completeness.state == INCOMPLETE:
                note = (note + "; " if note else "") + "incomplete"
            out.append(f"| `{point.run_id}` | {point.metric_key} | "
                       f"{_str(point.mean_score) or '—'} | {point.observations} "
                       f"| {point.trigger} | {note or '—'} |")
        out += _caveats("quality", [p.completeness for p in card.trend])
    out.append("")

    if card.leaderboard:
        out.append("## Standings within this benchmark")
        out.append("")
        out.append("| Metric | Model | Provider | Mean | Observations | Notes |")
        out.append("|---|---|---|---|---|---|")
        for entry in card.leaderboard:
            note = ("incomplete" if entry.completeness.state == INCOMPLETE
                    else "—")
            out.append(f"| {entry.metric_key} | `{entry.model_identifier}` | "
                       f"{entry.provider_slug} | "
                       f"{_str(entry.mean_score) or '—'} | "
                       f"{entry.observations} | {note} |")
        out += _caveats("the standings",
                        [e.completeness for e in card.leaderboard])
        out.append("")

    operational = card.operational
    out.append("## Speed and cost")
    out.append("")
    latency = operational.model_latency_ms
    out.append(f"Model calls: {latency.measured} measured. "
               f"Median {_q(latency, '0.5')} ms, "
               f"95th percentile {_q(latency, '0.95')} ms, "
               f"slowest {latency.maximum if latency.maximum is not None else '—'} ms. "
               f"The 95th percentile is the number a user notices.")
    out.append("")
    if operational.successful_tasks:
        out.append(f"Across {operational.successful_tasks} successful task(s): "
                   f"{operational.prompt_tokens + operational.completion_tokens} "
                   f"token(s) and {operational.cost_total} "
                   f"{operational.cost_currency or ''} in total — "
                   f"{operational.tokens_per_successful_task} token(s) and "
                   f"{operational.cost_per_successful_task} per successful "
                   f"task. Tasks that failed are excluded rather than counted "
                   f"as free.")
    else:
        out.append("No task in this window succeeded, so there is no cost per "
                   "successful task to report.")
    out += _caveats("speed and cost", [operational.completeness])
    out.append("")

    judges = card.judges
    out.append("## Judges")
    out.append("")
    if judges.judgements:
        out.append(f"{judges.judgements} judgement(s): {judges.scored} scored, "
                   f"{judges.abstained} declined to answer, {judges.failed} "
                   f"could not be read. Of {judges.consensus_results} panel "
                   f"verdict(s), {judges.agreed} agreed and {judges.escalated} "
                   f"were escalated to a person.")
        if judges.disagreement_measured:
            out.append(f"Mean disagreement across the "
                       f"{judges.disagreement_measured} verdict(s) where it "
                       f"could be measured: {_str(judges.mean_disagreement)}.")
        else:
            out.append("Disagreement could not be measured on any verdict in "
                       "this window, so there is no spread to report.")
    else:
        out.append("No judge ran in this window.")
    out += _caveats("the judge figures", [judges.completeness])
    out.append("")

    agents = card.agents
    out.append("## Agents")
    out.append("")
    if agents.tool_calls:
        out.append(f"{agents.tool_calls} tool call(s) across "
                   f"{agents.samples_with_trajectory} sample(s): "
                   f"{agents.failed_tool_calls} failed, success rate "
                   f"{_str(agents.tool_success_rate)}. "
                   f"{agents.completed_tasks} task(s) completed, "
                   f"{agents.trajectory_failures} did not. "
                   f"{agents.samples_with_loops} sample(s) repeated an "
                   f"identical call back to back, and "
                   f"{agents.samples_with_retries} repeated one at all. "
                   f"{agents.truncated_trajectories} trajectory(ies) were cut "
                   f"at the ingest limit and cannot be read as finished.")
    else:
        out.append("No agent trajectory was recorded in this window.")
    out += _caveats("the agent figures", [agents.completeness])
    out.append("")

    if card.drift:
        out.append("## Drift against baseline history")
        out.append("")
        for analysis in card.drift:
            out.append(f"- **{analysis.metric_key}**: {analysis.verdict}. "
                       f"{analysis.detail}.")
        out.append("")

    out.append("## Alerts")
    out.append("")
    if card.alerts:
        for event in card.alerts[:12]:
            out.append(f"- Run `{event.run_id}`: {event.detail}.")
    elif card.alert_rules:
        out.append(f"{card.alert_rules} rule(s) are configured and none fired "
                   f"in this window.")
    else:
        out.append("No alert rule is configured, so nothing here could have "
                   "fired. That is a gap in coverage, not a clean bill of "
                   "health.")
    out.append("")

    out.append("## What this does not tell you")
    out.append("")
    for limitation in UNCALIBRATED:
        out.append(f"- {limitation}")
    out.append("")
    return "\n".join(out)


def _caveats(subject: str, completenesses) -> list[str]:
    """The incompleteness sentences, kept with the figures they qualify."""
    reasons = []
    for completeness in completenesses:
        if completeness.state == INCOMPLETE and completeness.reason:
            if completeness.reason not in reasons:
                reasons.append(completeness.reason)
    if not reasons:
        return []
    lines = ["", f"**Read {subject} with these qualifications.**"]
    lines += [f"- {reason}." for reason in reasons]
    return lines


def _q(distribution, quantile: str) -> str:
    value = distribution.quantiles.get(Decimal(quantile))
    return "—" if value is None else str(value)


def _str(value) -> str | None:
    return None if value is None else str(value)
