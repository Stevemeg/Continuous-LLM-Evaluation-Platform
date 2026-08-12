"""Quality drift, against baseline history — `REQ-F-10-4`.

The requirement names the failure it exists to prevent: comparing against *a
single prior run*. Two runs differ for many reasons that are not drift, and a
detector that fires on one difference fires constantly and is switched off within
a month. So the comparison here is against the history of approved baselines for
a project and suite — every baseline that has held the scope, including the ones
since superseded, because a superseded baseline is history rather than a mistake.

What this module will not do is more important than what it does.

**It invents no threshold.** The project's known risks include statistical
calibration, and a drift tolerance chosen here would be a calibrated-looking
number nobody calibrated. Classification therefore requires the caller to supply
`minimum_history` and `tolerance`; without them the verdict is
`insufficient_configuration` with a reason, exactly as `statistics.compare`
abstains when a policy has no precision threshold. ADR-007 made that choice for
gate comparisons and this follows it rather than quietly making the opposite one.

**It refuses to work from one point.** `minimum_history` below two is rejected
outright, because a "history" of one run is the single prior run the requirement
forbids, whatever it is called.

**It reports a fact even when it cannot classify.** Where the current value sits
relative to the range the history actually spanned is an observation, not a
threshold — a value outside every figure the baselines ever produced is worth a
human's attention, and saying so costs no calibration. `position` is always
reported; `verdict` is only reported when someone configured what drift means.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from clep.identity import ulid_to_uuid, uuid_to_ulid
from clep.regression.statistics import RESOLUTION

BELOW_RANGE = "below_observed_range"
WITHIN_RANGE = "within_observed_range"
ABOVE_RANGE = "above_observed_range"
POSITIONS = (BELOW_RANGE, WITHIN_RANGE, ABOVE_RANGE)

DRIFTED = "drifted"
STABLE = "stable"
INSUFFICIENT_HISTORY = "insufficient_history"
INSUFFICIENT_CONFIGURATION = "insufficient_configuration"
NOT_MEASURED = "not_measured"
VERDICTS = (DRIFTED, STABLE, INSUFFICIENT_HISTORY, INSUFFICIENT_CONFIGURATION,
            NOT_MEASURED)

#: The smallest history this module will reason over. Not a tuning parameter:
#: `REQ-F-10-4` forbids comparing against a single prior run, and two is the
#: first number that is not one.
MINIMUM_HISTORY_FLOOR = 2


class DriftError(ValueError):
    pass


@dataclass(frozen=True)
class HistoryPoint:
    run_id: str
    baseline_id: str
    baseline_state: str
    mean_score: Decimal
    observations: int
    approved_at: object


@dataclass(frozen=True)
class DriftAnalysis:
    metric_key: str
    run_id: str
    current_value: Decimal | None
    current_observations: int
    history: tuple
    historical_minimum: Decimal | None
    historical_median: Decimal | None
    historical_maximum: Decimal | None
    position: str | None
    verdict: str
    deviation_from_median: Decimal | None
    tolerance: Decimal | None
    minimum_history: int | None
    detail: str

    def as_dict(self) -> dict:
        return {
            "metricKey": self.metric_key,
            "runId": self.run_id,
            "currentValue": _str(self.current_value),
            "currentObservations": self.current_observations,
            "verdict": self.verdict,
            "detail": self.detail,
            "position": self.position,
            "historySize": len(self.history),
            "historicalMinimum": _str(self.historical_minimum),
            "historicalMedian": _str(self.historical_median),
            "historicalMaximum": _str(self.historical_maximum),
            "deviationFromMedian": _str(self.deviation_from_median),
            "tolerance": _str(self.tolerance),
            "minimumHistory": self.minimum_history,
            "history": [{"runId": p.run_id, "baselineId": p.baseline_id,
                         "baselineState": p.baseline_state,
                         "meanScore": _str(p.mean_score),
                         "observations": p.observations,
                         "approvedAt": p.approved_at.isoformat()
                         if p.approved_at else None}
                        for p in self.history],
        }


class DriftRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    def baseline_history(self, project_id: str, *, suite_version_id: str,
                         metric_key: str,
                         exclude_run_id: str | None = None) -> list[HistoryPoint]:
        """Every approved-or-superseded baseline's own measurement, oldest first.

        Superseded baselines are included deliberately. A baseline is superseded
        when a newer one takes the scope, not when it is found to be wrong, and
        dropping them would shrink "history" to "the current baseline" — which
        is the single prior run again.
        """
        params = [self._org, ulid_to_uuid(project_id),
                  ulid_to_uuid(suite_version_id), metric_key]
        exclusion = ""
        if exclude_run_id:
            exclusion = " AND b.run_id <> %s"
            params.append(ulid_to_uuid(exclude_run_id))
        rows = self._conn.execute(
            "SELECT b.run_id, b.id, b.state, "
            "       avg(eo.score) FILTER (WHERE eo.resolution = 'scored'), "
            "       count(*) FILTER (WHERE eo.resolution = 'scored'), "
            "       b.approved_at "
            "FROM clep.baseline b "
            "JOIN clep.run_sample s ON s.organization_id = b.organization_id "
            "  AND s.run_id = b.run_id "
            "JOIN clep.evaluator_outcome eo "
            "  ON eo.organization_id = s.organization_id "
            " AND eo.run_sample_id = s.id "
            "JOIN clep.evaluator_version ev ON ev.id = eo.evaluator_version_id "
            "JOIN clep.evaluator_definition ed "
            "  ON ed.id = ev.evaluator_definition_id "
            "WHERE b.organization_id = %s AND b.project_id = %s "
            "  AND b.suite_version_id = %s AND ed.slug = %s "
            "  AND b.state IN ('approved', 'superseded')" + exclusion +
            " GROUP BY b.run_id, b.id, b.state, b.approved_at "
            " HAVING count(*) FILTER (WHERE eo.resolution = 'scored') > 0 "
            " ORDER BY b.approved_at, b.id", params).fetchall()
        return [HistoryPoint(run_id=uuid_to_ulid(r[0]),
                             baseline_id=uuid_to_ulid(r[1]), baseline_state=r[2],
                             mean_score=r[3], observations=r[4], approved_at=r[5])
                for r in rows]

    def current_value(self, run_id: str, metric_key: str) -> tuple:
        row = self._conn.execute(
            "SELECT avg(eo.score) FILTER (WHERE eo.resolution = 'scored'), "
            "       count(*) FILTER (WHERE eo.resolution = 'scored') "
            "FROM clep.run_sample s "
            "JOIN clep.evaluator_outcome eo "
            "  ON eo.organization_id = s.organization_id "
            " AND eo.run_sample_id = s.id "
            "JOIN clep.evaluator_version ev ON ev.id = eo.evaluator_version_id "
            "JOIN clep.evaluator_definition ed "
            "  ON ed.id = ev.evaluator_definition_id "
            "WHERE s.organization_id = %s AND s.run_id = %s AND ed.slug = %s",
            (self._org, ulid_to_uuid(run_id), metric_key)).fetchone()
        return (row[0], row[1]) if row else (None, 0)

    def analyse(self, project_id: str, *, run_id: str, suite_version_id: str,
                metric_key: str, minimum_history: int | None = None,
                tolerance: Decimal | None = None) -> DriftAnalysis:
        history = self.baseline_history(project_id,
                                        suite_version_id=suite_version_id,
                                        metric_key=metric_key,
                                        exclude_run_id=run_id)
        value, observations = self.current_value(run_id, metric_key)
        return classify(metric_key=metric_key, run_id=run_id,
                        current_value=value, current_observations=observations,
                        history=history, minimum_history=minimum_history,
                        tolerance=tolerance)


def classify(*, metric_key: str, run_id: str, current_value: Decimal | None,
             current_observations: int, history, minimum_history: int | None,
             tolerance: Decimal | None) -> DriftAnalysis:
    """The whole decision, with no database and no hidden defaults."""
    history = tuple(history)
    if minimum_history is not None and minimum_history < MINIMUM_HISTORY_FLOOR:
        raise DriftError(
            f"a minimum history of {minimum_history} compares against a single "
            f"prior run, which REQ-F-10-4 exists to prevent; the floor is "
            f"{MINIMUM_HISTORY_FLOOR}")

    values = [p.mean_score for p in history]
    low = min(values) if values else None
    high = max(values) if values else None
    middle = _median(values)
    common = dict(metric_key=metric_key, run_id=run_id,
                  current_value=current_value,
                  current_observations=current_observations, history=history,
                  historical_minimum=low, historical_median=middle,
                  historical_maximum=high, tolerance=tolerance,
                  minimum_history=minimum_history)

    if current_value is None:
        return DriftAnalysis(
            position=None, verdict=NOT_MEASURED, deviation_from_median=None,
            detail=("this run produced no score for this metric, so there is "
                    "nothing to compare against its history"), **common)

    position = None
    if values:
        position = (BELOW_RANGE if current_value < low
                    else ABOVE_RANGE if current_value > high else WITHIN_RANGE)
    deviation = (current_value - middle).copy_abs() if middle is not None else None

    if len(history) < MINIMUM_HISTORY_FLOOR:
        return DriftAnalysis(
            position=position, verdict=INSUFFICIENT_HISTORY,
            deviation_from_median=deviation,
            detail=(f"{len(history)} baseline(s) of history for this metric; "
                    f"REQ-F-10-4 compares against baseline history rather than "
                    f"a single prior run, and this is not yet a history"),
            **common)
    if minimum_history is not None and len(history) < minimum_history:
        return DriftAnalysis(
            position=position, verdict=INSUFFICIENT_HISTORY,
            deviation_from_median=deviation,
            detail=(f"{len(history)} baseline(s) of history is below the "
                    f"configured minimum of {minimum_history}"),
            **common)
    if tolerance is None or minimum_history is None:
        # ADR-007's rule, applied here: a threshold invented in this module
        # would be a calibrated-looking number nobody calibrated, and it would
        # become the product's definition of drift by default.
        return DriftAnalysis(
            position=position, verdict=INSUFFICIENT_CONFIGURATION,
            deviation_from_median=deviation,
            detail=("no drift tolerance and minimum history are configured, so "
                    "drift cannot be distinguished from ordinary variation; "
                    f"the current value sits {position.replace('_', ' ')} of "
                    f"the {len(history)} baselines observed"
                    if position else "no history"),
            **common)

    drifted = deviation > tolerance
    return DriftAnalysis(
        position=position, verdict=DRIFTED if drifted else STABLE,
        deviation_from_median=deviation,
        detail=(f"the current value deviates from the median of "
                f"{len(history)} baseline(s) by {deviation}, which "
                f"{'exceeds' if drifted else 'is within'} the configured "
                f"tolerance of {tolerance}"),
        **common)


def _median(values):
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return ((ordered[middle - 1] + ordered[middle]) / Decimal(2)).quantize(
        RESOLUTION)


def _str(value) -> str | None:
    return None if value is None else str(value)
