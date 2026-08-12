"""Alerting on quality, cost and latency conditions — `REQ-F-11-9`.

An alert rule is a standing statement that a figure is worth knowing about, and
a firing is the record that the condition held. Both are stored, because neither
is derivable from evaluation results: a rule is somebody's decision, and a firing
is a historical fact about a particular run.

Four properties, each of them a rule the rest of the platform already follows.

**An alert never acts.** There is no delivery column, no endpoint, no
acknowledgement, and no retry. `REQ-F-10-3` forbids the product from changing a
production system, and the same reasoning that kept an actuation column out of
`release_observation` keeps a webhook target out of here. Outbound delivery is an
egress capability with its own security surface; nothing in `CAP-11` asks for it,
and inventing it would be product scope this phase does not own.

**A rule states which way is bad.** `direction` is explicit, exactly as it is on
`clep.threshold` and on a gate criterion, because latency and quality run
opposite ways and a rule that does not know which cannot decide anything.

**A rule does not fire on noise.** `minimum_sample_size` is required and
positive. `REQ-F-08-3` protects a gate from a misleading tiny delta; the same
protection applied to alerting is what stops one failed sample paging someone.

**A firing carries the completeness of the evidence behind it.** `REQ-F-11-7`
requires a figure computed from incomplete data to be marked in every view it
appears in, and an alert is a view — very often the only one anybody reads.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import psycopg

from clep.identity import actor_uuid, new_ulid, ulid_to_uuid, uuid_to_ulid

QUALITY = "quality"
COST = "cost"
LATENCY = "latency"
DIMENSIONS = (QUALITY, COST, LATENCY)

HIGHER_IS_BETTER = "higher_is_better"
LOWER_IS_BETTER = "lower_is_better"
DIRECTIONS = (HIGHER_IS_BETTER, LOWER_IS_BETTER)

#: What a cost or latency rule may name. Closed, because these are figures the
#: platform computes rather than names a tenant chose: a rule on
#: `cost_per_task_maybe` would be a rule that never fires and never says why.
#: A quality rule names an evaluator definition's slug instead, which is open
#: because the evaluator catalogue is.
OPERATIONAL_METRICS = {
    COST: ("cost_total", "cost_per_successful_task"),
    LATENCY: ("model_latency_p50_ms", "model_latency_p95_ms",
              "model_latency_maximum_ms", "evaluator_latency_p95_ms"),
}

FIRED = "fired"
WITHIN_THRESHOLD = "within_threshold"
NOT_MEASURED = "not_measured"
BELOW_MINIMUM_SAMPLE = "below_minimum_sample"
ALREADY_RECORDED = "already_recorded"


class AlertError(ValueError):
    pass


@dataclass(frozen=True)
class AlertRuleRow:
    id: str
    project_id: str
    slug: str
    display_name: str
    dimension: str
    metric_key: str
    direction: str
    threshold: Decimal
    minimum_sample_size: int
    state: str

    def breached_by(self, value: Decimal) -> bool:
        """Whether this value is on the wrong side of the threshold.

        Strict on both sides: a value exactly at the threshold has not breached
        it. A rule written as "alert below 0.8" that fires at exactly 0.8 is a
        rule whose author will disable it.
        """
        if self.direction == HIGHER_IS_BETTER:
            return value < self.threshold
        return value > self.threshold


@dataclass(frozen=True)
class AlertEventRow:
    id: str
    alert_rule_id: str
    run_id: str
    observed_value: Decimal
    threshold: Decimal
    sample_size: int
    evidence_completeness: str
    detail: str
    fired_at: object


@dataclass(frozen=True)
class AlertOutcome:
    """What one rule decided about one run, whether or not it fired."""
    rule_id: str
    slug: str
    outcome: str
    observed_value: Decimal | None = None
    sample_size: int = 0
    event_id: str | None = None
    detail: str = ""

    @property
    def fired(self) -> bool:
        return self.outcome == FIRED


class AlertRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    # ------------------------------------------------------------------ rules
    def create_rule(self, *, project_id: str, slug: str, display_name: str,
                    dimension: str, metric_key: str, direction: str,
                    threshold: Decimal, minimum_sample_size: int,
                    created_by: str) -> str:
        if dimension not in DIMENSIONS:
            raise AlertError(
                f"{dimension!r} is not a dimension this product alerts on; "
                f"REQ-F-11-9 names {list(DIMENSIONS)}")
        if direction not in DIRECTIONS:
            raise AlertError(
                "a rule that does not state which way is bad cannot decide "
                "anything")
        permitted = OPERATIONAL_METRICS.get(dimension)
        if permitted and metric_key not in permitted:
            raise AlertError(
                f"{metric_key!r} is not a {dimension} figure this platform "
                f"computes; a rule on a figure that does not exist never fires "
                f"and never says why. Choose one of {list(permitted)}")
        if minimum_sample_size < 1:
            raise AlertError(
                "REQ-F-08-3 applied to alerting: a rule with no minimum sample "
                "size fires on noise")
        rule_id = new_ulid()
        self._conn.execute(
            """
            INSERT INTO clep.alert_rule
                (id, organization_id, project_id, slug, display_name, dimension,
                 metric_key, direction, threshold, minimum_sample_size, state,
                 created_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
            """,
            (ulid_to_uuid(rule_id), self._org, ulid_to_uuid(project_id), slug,
             display_name, dimension, metric_key, direction, threshold,
             minimum_sample_size, actor_uuid(created_by)))
        return rule_id

    def pause_rule(self, rule_id: str) -> bool:
        row = self._conn.execute(
            "UPDATE clep.alert_rule SET state = 'paused', paused_at = now() "
            "WHERE organization_id = %s AND id = %s AND state = 'active' "
            "RETURNING id", (self._org, ulid_to_uuid(rule_id))).fetchone()
        return row is not None

    def get_rule(self, rule_id: str) -> AlertRuleRow | None:
        row = self._conn.execute(
            _RULE_SELECT + " WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(rule_id))).fetchone()
        return _rule(row) if row else None

    def active_rules(self, project_id: str) -> list[AlertRuleRow]:
        rows = self._conn.execute(
            _RULE_SELECT + " WHERE organization_id = %s AND project_id = %s "
                           " AND state = 'active' ORDER BY slug",
            (self._org, ulid_to_uuid(project_id))).fetchall()
        return [_rule(r) for r in rows]

    def list_rules(self, project_id: str) -> list[AlertRuleRow]:
        rows = self._conn.execute(
            _RULE_SELECT + " WHERE organization_id = %s AND project_id = %s "
                           " ORDER BY slug",
            (self._org, ulid_to_uuid(project_id))).fetchall()
        return [_rule(r) for r in rows]

    # ----------------------------------------------------------------- events
    def record_event(self, *, rule_id: str, run_id: str, observed_value: Decimal,
                     threshold: Decimal, sample_size: int,
                     evidence_completeness: str, detail: str) -> str | None:
        """Returns the event's ULID, or None when this rule already fired here.

        The store's unique key is what guarantees one firing per rule per run,
        so evaluating the same run twice — a redelivery, a re-read, a second
        sweep — cannot produce a second alert about the same evidence.
        """
        event_id = new_ulid()
        row = self._conn.execute(
            """
            INSERT INTO clep.alert_event
                (id, organization_id, alert_rule_id, run_id, observed_value,
                 threshold, sample_size, evidence_completeness, detail)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, alert_rule_id, run_id) DO NOTHING
            RETURNING id
            """,
            (ulid_to_uuid(event_id), self._org, ulid_to_uuid(rule_id),
             ulid_to_uuid(run_id), observed_value, threshold, sample_size,
             evidence_completeness, detail)).fetchone()
        return event_id if row else None

    def events_for_project(self, project_id: str,
                           limit: int = 50) -> list[AlertEventRow]:
        rows = self._conn.execute(
            _EVENT_SELECT +
            " JOIN clep.alert_rule ru ON ru.organization_id = e.organization_id "
            "   AND ru.id = e.alert_rule_id "
            " WHERE e.organization_id = %s AND ru.project_id = %s "
            " ORDER BY e.fired_at DESC, e.id DESC LIMIT %s",
            (self._org, ulid_to_uuid(project_id), limit)).fetchall()
        return [_event(r) for r in rows]

    def events_for_run(self, run_id: str) -> list[AlertEventRow]:
        rows = self._conn.execute(
            _EVENT_SELECT + " WHERE e.organization_id = %s AND e.run_id = %s "
                            " ORDER BY e.fired_at, e.id",
            (self._org, ulid_to_uuid(run_id))).fetchall()
        return [_event(r) for r in rows]


def evaluate_run(conn, organization_id: str, *, project_id: str, run_id: str
                 ) -> list[AlertOutcome]:
    """Every active rule, against one finished run.

    Reads the figures through `AnalyticsRepository`, scoped to this run, so an
    alert and a dashboard are looking at the same number. Computing them here
    would be a second definition, and the first symptom would be an alert nobody
    can reproduce from the analytics screen.
    """
    from clep.analytics.repository import AnalyticsRepository

    repository = AlertRepository(conn, organization_id)
    rules = repository.active_rules(project_id)
    if not rules:
        return []

    run = conn.execute(
        "SELECT completeness, suite_version_id FROM clep.run "
        "WHERE organization_id = %s AND id = %s",
        (str(organization_id), ulid_to_uuid(run_id))).fetchone()
    if run is None:
        raise AlertError(f"run {run_id} does not exist in this tenant")
    completeness = run[0] or "partial"

    figures = AnalyticsRepository(conn, organization_id).figures_for_run(
        project_id, run_id)

    outcomes = []
    for rule in rules:
        value, sample_size = figures.get(_key(rule), (None, 0))
        if value is None:
            outcomes.append(AlertOutcome(
                rule.id, rule.slug, NOT_MEASURED,
                detail=(f"this run produced no {rule.dimension} figure named "
                        f"{rule.metric_key!r}, so the rule has nothing to "
                        f"decide about")))
            continue
        if sample_size < rule.minimum_sample_size:
            outcomes.append(AlertOutcome(
                rule.id, rule.slug, BELOW_MINIMUM_SAMPLE, value, sample_size,
                detail=(f"{sample_size} observation(s) is below the rule's "
                        f"minimum of {rule.minimum_sample_size}; firing here "
                        f"would be firing on noise")))
            continue
        if not rule.breached_by(value):
            outcomes.append(AlertOutcome(
                rule.id, rule.slug, WITHIN_THRESHOLD, value, sample_size,
                detail=(f"{value} is within the threshold of {rule.threshold} "
                        f"for a {rule.direction} metric")))
            continue
        detail = (f"{rule.metric_key} was {value} against a threshold of "
                  f"{rule.threshold} ({rule.direction}) over {sample_size} "
                  f"observation(s); the evidence behind it is a run that "
                  f"finished {completeness}")
        event_id = repository.record_event(
            rule_id=rule.id, run_id=run_id, observed_value=value,
            threshold=rule.threshold, sample_size=sample_size,
            evidence_completeness=completeness, detail=detail)
        outcomes.append(AlertOutcome(
            rule.id, rule.slug, FIRED if event_id else ALREADY_RECORDED, value,
            sample_size, event_id, detail))
    return outcomes


def _key(rule: AlertRuleRow) -> tuple:
    return (rule.dimension, rule.metric_key)


_RULE_SELECT = ("SELECT id, project_id, slug, display_name, dimension, "
                "       metric_key, direction, threshold, minimum_sample_size, "
                "       state FROM clep.alert_rule")

_EVENT_SELECT = ("SELECT e.id, e.alert_rule_id, e.run_id, e.observed_value, "
                 "       e.threshold, e.sample_size, e.evidence_completeness, "
                 "       e.detail, e.fired_at FROM clep.alert_event e")


def _rule(row) -> AlertRuleRow:
    return AlertRuleRow(id=uuid_to_ulid(row[0]), project_id=uuid_to_ulid(row[1]),
                        slug=row[2], display_name=row[3], dimension=row[4],
                        metric_key=row[5], direction=row[6], threshold=row[7],
                        minimum_sample_size=row[8], state=row[9])


def _event(row) -> AlertEventRow:
    return AlertEventRow(id=uuid_to_ulid(row[0]),
                         alert_rule_id=uuid_to_ulid(row[1]),
                         run_id=uuid_to_ulid(row[2]), observed_value=row[3],
                         threshold=row[4], sample_size=row[5],
                         evidence_completeness=row[6], detail=row[7],
                         fired_at=row[8])
