"""Reading history back out of the records that already hold it.

Every query here runs under the caller's tenant session, so row-level security
does the isolation and this module does not have to remember to. Nothing writes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import psycopg

from clep.identity import ulid_to_uuid, uuid_to_ulid
from clep.regression.statistics import RESOLUTION

#: `REQ-N-COMP-3`. Governance records are retained independently of a tenant
#: retention policy and are not deletable by the actors they record. Reported
#: with every answer so a reader can tell which window they were given.
AUDIT_FLOOR_DAYS = 2555  # seven years

#: An evaluator is called unstable when this fraction or more of its outcomes
#: did not resolve to a score. Not a calibrated figure: it is a reporting
#: threshold for a list a human reads, and it changes no decision. A gate that
#: turned on it would need a decided ADR, and does not exist.
INSTABILITY_REPORTING_THRESHOLD = Decimal("0.10")


@dataclass(frozen=True)
class JudgeCalibration:
    judge_version_id: str
    judgements: int
    mean_deviation: Decimal | None
    deviation_spread: Decimal | None
    abstention_rate: Decimal | None
    failure_rate: Decimal | None


@dataclass(frozen=True)
class RecurringFailure:
    signature: str
    occurrences: int
    first_seen: object
    last_seen: object


@dataclass(frozen=True)
class EvaluatorInstability:
    evaluator_version_id: str
    runs: int
    failure_rate: Decimal | None
    unstable: bool


@dataclass(frozen=True)
class EvaluationMemory:
    window_days: int | None
    gate_decisions: dict
    regressions: int
    escalations: int
    judge_calibration: tuple = ()
    recurring_failures: tuple = ()
    evaluator_instability: tuple = ()
    retention_floor_days: int = AUDIT_FLOOR_DAYS


class MemoryRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    def evaluation_memory(self, project_id: str, *,
                          window_days: int | None = None) -> EvaluationMemory:
        project = ulid_to_uuid(project_id)
        return EvaluationMemory(
            window_days=window_days,
            gate_decisions=self._decisions_by_outcome(project, window_days),
            regressions=self._regressions(project, window_days),
            escalations=self._escalations(project, window_days),
            judge_calibration=self._judge_calibration(project, window_days),
            recurring_failures=self._recurring_failures(project, window_days),
            evaluator_instability=self._evaluator_instability(project, window_days),
        )

    def judge_calibration(self, project_id: str, *,
                          window_days: int | None = None) -> tuple:
        """Per-judge calibration on its own, for a caller that wants only that.

        Phase 11's analytics needs this figure and must not recompute it: two
        definitions of "how far this judge sits from its ensemble" is how a
        dashboard and an escalation come to disagree about which judge is
        drifting. Public here rather than reached into from there.
        """
        return self._judge_calibration(ulid_to_uuid(project_id), window_days)

    # ------------------------------------------------------------------ pieces
    def _window(self, column: str, window_days: int | None) -> tuple[str, list]:
        """A tenant retention window narrows what is reported, and stops there.

        The audit floor is not applied as a second filter: it is the reason
        these rows still exist to be read. Reporting it is what makes the
        distinction visible to a caller who asked for a 30-day window and is
        looking at seven years of decisions in the store.
        """
        if window_days is None:
            return "", []
        return f" AND {column} >= now() - make_interval(days => %s)", [window_days]

    def _decisions_by_outcome(self, project, window_days) -> dict:
        clause, params = self._window("d.decided_at", window_days)
        # `evaluated_outcome`, not an outcome with exceptions folded in: an
        # exception is a separate, later act, and counting decisions by what
        # they were *decided* to be is the honest history. A reader who wants
        # the effective outcome joins the exceptions, which are dated.
        rows = self._conn.execute(
            "SELECT d.evaluated_outcome, count(*) FROM clep.gate_decision d "
            "WHERE d.organization_id = %s AND d.project_id = %s" + clause +
            " GROUP BY d.evaluated_outcome",
            [self._org, project, *params]).fetchall()
        return {outcome: count for outcome, count in rows}

    def _regressions(self, project, window_days) -> int:
        clause, params = self._window("d.decided_at", window_days)
        row = self._conn.execute(
            "SELECT count(*) FROM clep.comparison c "
            "JOIN clep.gate_decision d ON d.organization_id = c.organization_id "
            "  AND d.id = c.gate_decision_id "
            "WHERE c.organization_id = %s AND d.project_id = %s "
            "  AND c.classification = 'regression'" + clause,
            [self._org, project, *params]).fetchone()
        return row[0] if row else 0

    def _escalations(self, project, window_days) -> int:
        clause, params = self._window("e.raised_at", window_days)
        row = self._conn.execute(
            "SELECT count(*) FROM clep.escalation e "
            "WHERE e.organization_id = %s AND e.project_id = %s" + clause,
            [self._org, project, *params]).fetchone()
        return row[0] if row else 0

    def _judge_calibration(self, project, window_days) -> tuple:
        """Per judge version: how far it sits from the ensemble, and how often
        it declines to answer.

        The deviation is computed against the consensus median of each
        judgement, and only where the judgement had a measured disagreement —
        deviation from a median of one vote is deviation from itself.
        """
        clause, params = self._window("jr.judged_at", window_days)
        rows = self._conn.execute(
            "SELECT jr.judge_version_id, "
            "       count(*) AS judgements, "
            "       count(*) FILTER (WHERE jr.resolution = 'abstained') AS abstained, "
            "       count(*) FILTER (WHERE jr.resolution NOT IN ('scored','abstained'))"
            "           AS failed "
            "FROM clep.judge_run jr "
            "JOIN clep.run r ON r.organization_id = jr.organization_id "
            "  AND r.id = jr.run_id "
            "WHERE jr.organization_id = %s AND r.project_id = %s" + clause +
            " GROUP BY jr.judge_version_id ORDER BY jr.judge_version_id",
            [self._org, project, *params]).fetchall()

        deviations = self._conn.execute(
            "SELECT jr.judge_version_id, jv.score - cr.verdict AS deviation "
            "FROM clep.judge_vote jv "
            "JOIN clep.judge_run jr ON jr.organization_id = jv.organization_id "
            "  AND jr.id = jv.judge_run_id "
            "JOIN clep.consensus_result cr "
            "  ON cr.organization_id = jr.organization_id "
            "  AND cr.run_sample_id = jr.run_sample_id "
            "JOIN clep.run r ON r.organization_id = jr.organization_id "
            "  AND r.id = jr.run_id "
            "WHERE jr.organization_id = %s AND r.project_id = %s "
            "  AND cr.disagreement_measured AND cr.verdict IS NOT NULL" + clause,
            [self._org, project, *params]).fetchall()

        by_judge: dict[str, list] = {}
        for judge_id, deviation in deviations:
            by_judge.setdefault(str(judge_id), []).append(Decimal(deviation))

        out = []
        for judge_id, judgements, abstained, failed in rows:
            values = by_judge.get(str(judge_id), [])
            out.append(JudgeCalibration(
                judge_version_id=uuid_to_ulid(judge_id),
                judgements=judgements,
                mean_deviation=_mean(values),
                deviation_spread=(max(values) - min(values)).quantize(RESOLUTION)
                if len(values) >= 2 else None,
                abstention_rate=_rate(abstained, judgements),
                failure_rate=_rate(failed, judgements)))
        return tuple(out)

    def _recurring_failures(self, project, window_days) -> tuple:
        """Failure kinds that have happened more than once.

        A failure that happened once is an incident. The same failure kind
        recurring is a property of the system, which is the thing worth
        remembering.
        """
        clause, params = self._window("s.scored_at", window_days)
        rows = self._conn.execute(
            "SELECT s.failure_kind, count(*), min(s.scored_at), max(s.scored_at) "
            "FROM clep.run_sample s "
            "JOIN clep.run r ON r.organization_id = s.organization_id "
            "  AND r.id = s.run_id "
            "WHERE s.organization_id = %s AND r.project_id = %s "
            "  AND s.failure_kind IS NOT NULL" + clause +
            " GROUP BY s.failure_kind HAVING count(*) > 1 "
            " ORDER BY count(*) DESC, s.failure_kind",
            [self._org, project, *params]).fetchall()
        return tuple(RecurringFailure(signature=kind, occurrences=count,
                                      first_seen=first, last_seen=last)
                     for kind, count, first, last in rows)

    def _evaluator_instability(self, project, window_days) -> tuple:
        clause, params = self._window("s.scored_at", window_days)
        rows = self._conn.execute(
            "SELECT eo.evaluator_version_id, count(*), "
            "       count(*) FILTER (WHERE eo.resolution <> 'scored') "
            "FROM clep.evaluator_outcome eo "
            "JOIN clep.run_sample s ON s.organization_id = eo.organization_id "
            "  AND s.id = eo.run_sample_id "
            "JOIN clep.run r ON r.organization_id = s.organization_id "
            "  AND r.id = s.run_id "
            "WHERE eo.organization_id = %s AND r.project_id = %s" + clause +
            " GROUP BY eo.evaluator_version_id ORDER BY eo.evaluator_version_id",
            [self._org, project, *params]).fetchall()
        out = []
        for evaluator_id, total, unresolved in rows:
            rate = _rate(unresolved, total)
            out.append(EvaluatorInstability(
                evaluator_version_id=uuid_to_ulid(evaluator_id),
                runs=total, failure_rate=rate,
                unstable=rate is not None
                and rate >= INSTABILITY_REPORTING_THRESHOLD))
        return tuple(out)


def _mean(values) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal(0)) / Decimal(len(values))).quantize(RESOLUTION)


def _rate(part: int, whole: int) -> Decimal | None:
    if not whole:
        return None
    return (Decimal(part) / Decimal(whole)).quantize(RESOLUTION)
