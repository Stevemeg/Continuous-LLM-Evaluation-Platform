"""Service-level indicators, computed from the record the product reports from.

ADR-023 rule 6: indicators come from runs, samples, gate decisions and audit
events — not from a telemetry backend. Trace backends have short retention and
are permitted to drop data under load; an availability figure that disagrees with
the audit trail is worse than no figure.

Two of the five indicators in `observability-strategy.md` §5 are computable from
the store, and this module computes them. The other three are measured by
executing a workload, or are blocked; `docs/evidence/phase-13/measure_slos.py`
does the executing and records which is which.

**Verdict integrity is the unusual one**, and it is the reason to read this file.
`REQ-X-10` requires platform failure to be distinguishable from quality failure.
The store makes that checkable: a gate decision is an integrity violation when it
returned a *quality verdict* — pass, hard_fail, warning — over a candidate run
whose evidence was incomplete for a **platform** reason. A provider outage is not
a platform reason; `evaluator_error` is, because the evaluator is ours. So the
question "did the platform ever dress its own failure as a verdict about somebody
else's code" has an answer in SQL, which is a better place for it than in a
policy document.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

#: `run_sample.failure_kind` values attributable to this platform rather than to
#: a provider or to the caller. The five provider modes are somebody else's
#: outage; `budget_exhausted` and `cancelled` are the caller's decisions;
#: `evaluator_error` is ours, because the evaluator runs inside our boundary.
PLATFORM_FAILURE_KINDS = ("evaluator_error",)

#: Outcomes that assert something about the candidate's quality. The other two —
#: `insufficient_evidence` and `not_comparable` — are the gate declining to
#: assert, which is the honest refusal `REQ-F-09-5` requires and is never an
#: integrity violation.
QUALITY_VERDICTS = ("pass", "hard_fail", "warning", "approval_required")


@dataclass(frozen=True)
class Indicator:
    """One measured indicator. `observations` is load-bearing.

    A proportion without its denominator is how "100% available" gets published
    on the strength of four requests, so the count travels with the value and
    every reader of a target has to walk past it.
    """
    name: str
    value: Decimal | None
    observations: int
    detail: str

    @property
    def measured(self) -> bool:
        return self.value is not None and self.observations > 0


def run_completion(conn, organization_id: str) -> Indicator:
    """Proportion of terminal runs with no platform-caused incompleteness.

    `observability-strategy.md` §5 defines it that way, and the qualifier is the
    whole content: a run cut short by an exhausted budget or a cancellation
    completed exactly as the platform promised, and counting those as failures
    would make the indicator measure how often tenants change their minds.
    """
    row = conn.execute(
        "SELECT count(*) FILTER (WHERE r.execution_state = 'terminal'), "
        "       count(*) FILTER (WHERE r.execution_state = 'terminal' "
        "                          AND EXISTS (SELECT 1 FROM clep.run_sample s "
        "                                      WHERE s.organization_id = r.organization_id "
        "                                        AND s.run_id = r.id "
        "                                        AND s.failure_kind = ANY(%s))) "
        "FROM clep.run r WHERE r.organization_id = %s",
        (list(PLATFORM_FAILURE_KINDS), str(organization_id))).fetchone()
    terminal, degraded = int(row[0] or 0), int(row[1] or 0)
    if not terminal:
        return Indicator("run_completion", None, 0,
                         "no terminal runs recorded; a proportion over zero "
                         "runs is not a measurement")
    value = (Decimal(terminal - degraded) / Decimal(terminal))
    return Indicator("run_completion", value, terminal,
                     f"{terminal - degraded} of {terminal} terminal run(s) "
                     f"carried no platform-caused incompleteness "
                     f"({', '.join(PLATFORM_FAILURE_KINDS)})")


def verdict_integrity(conn, organization_id: str) -> Indicator:
    """Proportion of gate decisions that did not dress a platform failure as a
    verdict about somebody else's code.

    The objective is 100% under ADR-023 rule 8, derived from `REQ-X-10` rather
    than from measurement: every other value is a statement that the platform
    will sometimes misattribute its own failure, which is not a service level
    anybody would publish.
    """
    row = conn.execute(
        "SELECT count(*), "
        "       count(*) FILTER (WHERE d.evaluated_outcome = ANY(%s) "
        "                          AND EXISTS (SELECT 1 FROM clep.run_sample s "
        "                                      WHERE s.organization_id = d.organization_id "
        "                                        AND s.run_id = d.candidate_run_id "
        "                                        AND s.failure_kind = ANY(%s))) "
        "FROM clep.gate_decision d WHERE d.organization_id = %s",
        (list(QUALITY_VERDICTS), list(PLATFORM_FAILURE_KINDS),
         str(organization_id))).fetchone()
    decisions, violations = int(row[0] or 0), int(row[1] or 0)
    if not decisions:
        return Indicator("verdict_integrity", None, 0,
                         "no gate decisions recorded")
    value = Decimal(decisions - violations) / Decimal(decisions)
    return Indicator(
        "verdict_integrity", value, decisions,
        f"{violations} of {decisions} decision(s) returned a quality verdict "
        f"over a candidate run carrying platform-caused incompleteness")


def gate_latency_samples(conn, organization_id: str) -> list[int]:
    """Not an indicator: the raw material for one.

    Gate latency is invocation to reported decision, which is wall-clock and is
    not stored — `gate_decision` records when a decision was made, not how long
    making it took. So it is measured by executing gates, and this returns the
    per-run sample counts that let a measurement band its results by suite size.
    """
    rows = conn.execute(
        "SELECT r.id, count(s.id) FROM clep.run r "
        "LEFT JOIN clep.run_sample s "
        "  ON s.organization_id = r.organization_id AND s.run_id = r.id "
        "WHERE r.organization_id = %s GROUP BY r.id ORDER BY count(s.id)",
        (str(organization_id),)).fetchall()
    return [int(c) for _, c in rows]


def percentile(values, q: Decimal) -> Decimal | None:
    """Nearest-rank. Deliberately not interpolated.

    An interpolated percentile reports a latency no request experienced, which
    for a small sample is most of them. Nearest-rank always names an observation
    that actually happened.
    """
    ordered = sorted(values)
    if not ordered:
        return None
    rank = max(1, int((Decimal(q) * Decimal(len(ordered))).to_integral_value(
        rounding="ROUND_CEILING")))
    return Decimal(str(ordered[min(rank, len(ordered)) - 1]))
