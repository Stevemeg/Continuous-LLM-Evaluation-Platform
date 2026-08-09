"""The regression engine: pair, classify, apply the policy, record the evidence.

Four rules shape everything here, and each of them is a requirement rather than a
preference.

**Pair on the example, per evaluator.** `run_sample.score` is the mean of that
sample's evaluators, so comparing it would average an exact-match rate into a
rubric score and call the result a metric. `REQ-F-08-6` requires the two kinds to
stay structurally separate, so the unit of comparison is one evaluator version
over one example, and the mixed per-sample mean is never used.

**A sample that was not scored contributes nothing, and never a zero**
(`REQ-F-08-5`). Both sides must have resolved to `scored` for the example to
enter the pairing. An example the candidate failed on is not a candidate score of
zero; it is an example the comparison has no measurement for, and the pair count
falls, which is visible in the sample size the report carries.

**Comparability is decided from versions, before any arithmetic**
(`REQ-F-08-8`). See `comparability`.

**Statistical parameters come from the policy version.** ADR-007 left them unset
deliberately; the engine reads what a person chose and recorded, and where they
chose nothing the answer is `insufficient_evidence` with a reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from clep.identity import ulid_to_uuid
from clep.regression import comparability, statistics
from clep.regression.statistics import Pair

#: Criterion sources that have a signal, and the ones that do not. A source with
#: no signal abstains loudly rather than passing quietly.
#:
#: Phase 8 emptied the second tuple: `judge_agreement` now reads the per-sample
#: disagreement the ensemble records. The tuple stays, and so does the branch
#: that honours it, because the next source with no signal yet should abstain
#: the same way rather than being written to pass.
SOURCES_WITH_SIGNAL = ("evaluator", "cost", "latency", "judge_agreement")
SOURCES_WITHOUT_SIGNAL = ()

#: How bad a verdict is. The decision takes the worst of its criteria.
SEVERITY = {"pass": 0, "warning": 1, "approval_required": 2, "hard_fail": 3}

RESULT_KIND_BY_SOURCE = {"evaluator": None,   # decided per evaluator version
                         "cost": "operational",
                         "latency": "operational",
                         "judge_agreement": "probabilistic_judge"}


class GateError(RuntimeError):
    pass


@dataclass
class CriterionOutcome:
    criterion_id: str
    metric_key: str
    verdict: str
    rule_fired: str
    detail: str
    comparison: dict | None = None


@dataclass
class GateResult:
    outcome: str
    criterion_outcomes: list[CriterionOutcome] = field(default_factory=list)
    not_comparable_reason: str | None = None


def evaluate(conn, organization_id: str, *, baseline, candidate_run_id: str,
             policy_version, criteria, baseline_identity, candidate_identity
             ) -> GateResult:
    """Produce a gate result without writing anything.

    Separated from persistence so the decision can be tested without a store and
    so the store never holds a half-written decision: everything is computed,
    then recorded in one transaction.
    """
    ambiguous = _ambiguous_candidate(conn, organization_id,
                                     baseline_run_id=baseline.run_id,
                                     candidate_run_id=candidate_run_id)
    verdict = comparability.assess(baseline_identity, candidate_identity)
    if ambiguous or not verdict.comparable:
        reason = ambiguous or verdict.reason()
        outcomes = [
            CriterionOutcome(criterion_id=c.id, metric_key=c.metric_key,
                             verdict=c.on_not_comparable,
                             rule_fired="comparability", detail=reason,
                             comparison=_not_comparable_comparison(c, reason))
            for c in criteria]
        # Through the same precedence as everything else, deliberately. An
        # earlier version returned `not_comparable` directly from here, which
        # quietly ignored what the policy said to do about it — a policy whose
        # criteria block on incomparability would have been answered with an
        # outcome a CI system may well treat as advisory. The run-level failure
        # decides the *reason*; the policy still decides the action.
        return GateResult(outcome=_decision_outcome(outcomes),
                          not_comparable_reason=reason,
                          criterion_outcomes=outcomes)

    outcomes = [_evaluate_criterion(conn, organization_id, criterion=c,
                                    baseline_run_id=baseline.run_id,
                                    candidate_run_id=candidate_run_id,
                                    policy_version=policy_version)
                for c in criteria]
    return GateResult(outcome=_decision_outcome(outcomes),
                      criterion_outcomes=outcomes)


def _decision_outcome(outcomes: list[CriterionOutcome]) -> str:
    """Worst action first, then the most informative description.

    A blocking verdict wins outright: a policy that says an abstention blocks a
    release must block it, and reporting `insufficient_evidence` instead would
    turn a stop into a note. Below that threshold, when nothing could be measured
    at all, "we could not tell" is a truer summary than "warning" — which is
    `REQ-F-08-4` applied to the decision as well as to each metric.
    """
    if not outcomes:
        return "insufficient_evidence"
    worst = max(SEVERITY[o.verdict] for o in outcomes)
    if worst >= SEVERITY["approval_required"]:
        return "hard_fail" if worst == SEVERITY["hard_fail"] else "approval_required"
    classifications = {(o.comparison or {}).get("classification") for o in outcomes}
    if classifications == {"not_comparable"}:
        return "not_comparable"
    if classifications == {"insufficient_evidence"}:
        return "insufficient_evidence"
    return "warning" if worst == SEVERITY["warning"] else "pass"


def _evaluate_criterion(conn, organization_id, *, criterion, baseline_run_id,
                        candidate_run_id, policy_version) -> CriterionOutcome:
    if criterion.source in SOURCES_WITHOUT_SIGNAL:
        detail = (f"no {criterion.source} signal is recorded yet, so this "
                  f"criterion has nothing to measure rather than nothing to "
                  f"report")
        return CriterionOutcome(
            criterion_id=criterion.id, metric_key=criterion.metric_key,
            verdict=criterion.on_insufficient_evidence, rule_fired="no_signal",
            detail=detail,
            comparison={"metric_key": criterion.metric_key,
                        "result_kind": RESULT_KIND_BY_SOURCE[criterion.source],
                        "classification": "insufficient_evidence",
                        "sample_size": 0, "abstention_reason": detail,
                        "evaluator_version_id": None})

    evaluator_version_id = None
    if criterion.source == "evaluator":
        evaluator_version_id = _evaluator_version_for(
            conn, organization_id, run_id=candidate_run_id,
            metric_key=criterion.metric_key)
        if evaluator_version_id is None:
            detail = (f"no evaluator named {criterion.metric_key} ran in the "
                      f"candidate; the policy governs a metric this suite does "
                      f"not produce")
            return CriterionOutcome(
                criterion_id=criterion.id, metric_key=criterion.metric_key,
                verdict=criterion.on_not_comparable, rule_fired="comparability",
                detail=detail,
                comparison=_not_comparable_comparison(criterion, detail))
        pairs = _evaluator_pairs(conn, organization_id, baseline_run_id,
                                 candidate_run_id, evaluator_version_id)
        result_kind = _result_kind(conn, evaluator_version_id)
    elif criterion.source == "cost":
        pairs = _cost_pairs(conn, organization_id, baseline_run_id,
                            candidate_run_id)
        result_kind = "operational"
    elif criterion.source == "judge_agreement":
        pairs = _agreement_pairs(conn, organization_id, baseline_run_id,
                                 candidate_run_id)
        result_kind = "probabilistic_judge"
    else:
        pairs = _latency_pairs(conn, organization_id, baseline_run_id,
                               candidate_run_id)
        result_kind = "operational"

    comparison = statistics.compare(
        pairs, direction=criterion.direction,
        confidence_level=policy_version.confidence_level,
        precision_threshold=criterion.precision_threshold,
        minimum_sample_size=criterion.minimum_sample_size,
        resamples=policy_version.resample_count,
        seed=policy_version.bootstrap_seed)

    baseline_mean = _mean(p.baseline for p in pairs)
    candidate_mean = _mean(p.candidate for p in pairs)
    record = {"metric_key": criterion.metric_key, "result_kind": result_kind,
              "classification": comparison.classification,
              "sample_size": comparison.sample_size,
              "baseline_mean": baseline_mean, "candidate_mean": candidate_mean,
              "mean_difference": comparison.mean_difference,
              "interval_lower": comparison.interval.lower if comparison.interval else None,
              "interval_upper": comparison.interval.upper if comparison.interval else None,
              "confidence_level": comparison.interval.confidence_level
              if comparison.interval else None,
              "effect_size": comparison.effect_size,
              "minimum_sample_size": comparison.minimum_sample_size,
              "abstention_reason": comparison.abstention_reason,
              "evaluator_version_id": evaluator_version_id}

    verdict, rule, detail = _apply_thresholds(criterion, comparison, candidate_mean,
                                              baseline_mean)
    return CriterionOutcome(criterion_id=criterion.id,
                            metric_key=criterion.metric_key, verdict=verdict,
                            rule_fired=rule, detail=detail, comparison=record)


def _apply_thresholds(criterion, comparison, candidate_mean, baseline_mean):
    """Compose the statistical verdict with the configured thresholds (ADR-016).

    Order matters and is deliberate. The absolute floor is a statement about
    where the product may not go at all, so it outranks a comparison against a
    baseline that may itself be below the floor. The relative tolerance is a
    statement about how much movement is acceptable, so it can only forgive a
    regression that was detected — never manufacture one.
    """
    if criterion.absolute_floor is not None and candidate_mean is not None:
        breached = (candidate_mean < criterion.absolute_floor
                    if criterion.direction == statistics.HIGHER_IS_BETTER
                    else candidate_mean > criterion.absolute_floor)
        if breached:
            return ("hard_fail", "absolute_floor",
                    f"candidate mean {candidate_mean} breaches the absolute floor "
                    f"{criterion.absolute_floor} for {criterion.metric_key}, "
                    f"independently of the baseline")

    if comparison.classification == statistics.INSUFFICIENT_EVIDENCE:
        rule = ("minimum_sample"
                if comparison.minimum_sample_size is not None
                and comparison.sample_size < comparison.minimum_sample_size
                else "precision_unset"
                if criterion.precision_threshold is None else "interval")
        return (criterion.on_insufficient_evidence, rule,
                comparison.abstention_reason)

    if comparison.classification == statistics.REGRESSION:
        if criterion.relative_tolerance is not None and baseline_mean:
            relative = abs(comparison.mean_difference) / abs(baseline_mean)
            if relative <= criterion.relative_tolerance:
                return ("pass", "relative_tolerance",
                        f"{criterion.metric_key} moved {relative:.6f} relative to "
                        f"the baseline, within the configured tolerance "
                        f"{criterion.relative_tolerance}; the change is real and "
                        f"accepted rather than undetected")
        return (criterion.on_regression, "interval",
                f"{criterion.metric_key} regressed: mean difference "
                f"{comparison.mean_difference} with a "
                f"{comparison.interval.confidence_level} interval of "
                f"[{comparison.interval.lower}, {comparison.interval.upper}] over "
                f"{comparison.sample_size} paired samples")

    return ("pass", "interval",
            f"{criterion.metric_key} classified {comparison.classification} over "
            f"{comparison.sample_size} paired samples")


def _not_comparable_comparison(criterion, reason: str) -> dict:
    return {"metric_key": criterion.metric_key,
            "result_kind": "operational",
            "classification": "not_comparable", "sample_size": 0,
            "not_comparable_reason": reason, "evaluator_version_id": None}


# --------------------------------------------------------------------- queries
def _ambiguous_candidate(conn, organization_id, *, baseline_run_id,
                         candidate_run_id) -> str | None:
    """A gate compares one thing against one thing.

    A run may hold several candidates — that is what `REQ-F-02-1` is for, and it
    is how a model migration is evaluated. But "the score of the run" is then
    undefined, and pairing across it would silently multiply every example by the
    number of configurations. `REQ-F-09` gates a release, which is one candidate,
    so the ambiguity is reported rather than resolved by picking one.
    """
    for label, run_id in (("baseline", baseline_run_id),
                          ("candidate", candidate_run_id)):
        count = conn.execute(
            "SELECT count(*) FROM clep.run_candidate "
            "WHERE organization_id = %s AND run_id = %s",
            (organization_id, ulid_to_uuid(run_id))).fetchone()[0]
        if count != 1:
            return (f"the {label} run records {count} candidate configuration(s); "
                    f"a gate decision compares one release candidate against one "
                    f"approved baseline, so which score to use is undefined")
    return None


def _evaluator_version_for(conn, organization_id, *, run_id: str,
                           metric_key: str) -> str | None:
    """Resolve a metric name to the evaluator version that produced it.

    The metric key is the evaluator's slug, resolved through the run's own suite
    rather than through anything the caller supplied — a gate that let the caller
    name the evaluator would let it choose which measurement to be judged on.
    """
    row = conn.execute(
        "SELECT ev.id FROM clep.run r "
        "JOIN clep.suite_evaluator se "
        "  ON se.organization_id = r.organization_id "
        " AND se.suite_version_id = r.suite_version_id "
        "JOIN clep.evaluator_version ev ON ev.id = se.evaluator_version_id "
        "JOIN clep.evaluator_definition ed ON ed.id = ev.evaluator_definition_id "
        "WHERE r.organization_id = %s AND r.id = %s AND ed.slug = %s",
        (organization_id, ulid_to_uuid(run_id), metric_key)).fetchone()
    return str(row[0]) if row else None


def _result_kind(conn, evaluator_version_id: str) -> str:
    row = conn.execute(
        "SELECT is_deterministic FROM clep.evaluator_version WHERE id = %s",
        (evaluator_version_id,)).fetchone()
    return "deterministic_evaluator" if row and row[0] else "probabilistic_judge"


def _evaluator_pairs(conn, organization_id, baseline_run_id, candidate_run_id,
                     evaluator_version_id) -> list[Pair]:
    """One row per example scored by this evaluator in both runs.

    The inner join on `example_id` is the pairing, and `resolution = 'scored'` on
    both sides is `REQ-F-08-5`: an example the candidate failed on drops out of
    the comparison instead of entering it as a zero.
    """
    rows = conn.execute(
        "SELECT bs.example_id, bo.score, co.score "
        "FROM clep.run_sample bs "
        "JOIN clep.evaluator_outcome bo "
        "  ON bo.organization_id = bs.organization_id "
        " AND bo.run_sample_id = bs.id AND bo.evaluator_version_id = %s "
        "JOIN clep.run_sample cs "
        "  ON cs.organization_id = bs.organization_id "
        " AND cs.example_id = bs.example_id AND cs.run_id = %s "
        "JOIN clep.evaluator_outcome co "
        "  ON co.organization_id = cs.organization_id "
        " AND co.run_sample_id = cs.id AND co.evaluator_version_id = %s "
        "WHERE bs.organization_id = %s AND bs.run_id = %s "
        "AND bo.resolution = 'scored' AND co.resolution = 'scored' "
        "ORDER BY bs.example_id",
        (evaluator_version_id, ulid_to_uuid(candidate_run_id),
         evaluator_version_id, organization_id,
         ulid_to_uuid(baseline_run_id))).fetchall()
    return [Pair(str(r[0]), r[1], r[2]) for r in rows]


def _cost_pairs(conn, organization_id, baseline_run_id,
                candidate_run_id) -> list[Pair]:
    rows = conn.execute(
        "SELECT bs.example_id, SUM(bc.cost_amount), SUM(cc.cost_amount) "
        "FROM clep.run_sample bs "
        "JOIN clep.sample_cost bc "
        "  ON bc.organization_id = bs.organization_id AND bc.run_sample_id = bs.id "
        "JOIN clep.run_sample cs "
        "  ON cs.organization_id = bs.organization_id "
        " AND cs.example_id = bs.example_id AND cs.run_id = %s "
        "JOIN clep.sample_cost cc "
        "  ON cc.organization_id = cs.organization_id AND cc.run_sample_id = cs.id "
        "WHERE bs.organization_id = %s AND bs.run_id = %s "
        "GROUP BY bs.example_id ORDER BY bs.example_id",
        (ulid_to_uuid(candidate_run_id), organization_id,
         ulid_to_uuid(baseline_run_id))).fetchall()
    return [Pair(str(r[0]), r[1], r[2]) for r in rows]


def _agreement_pairs(conn, organization_id, baseline_run_id,
                     candidate_run_id) -> list[Pair]:
    """Per-example judge disagreement, paired between the two runs.

    Two guards, and both are `REQ-F-08-5` rather than convenience. Only
    judgements whose disagreement was **measured** are paired: an unmeasured one
    holds 1 as a floor on ignorance, and pairing that against a real spread
    would read as a large regression caused by a judge failing to answer. And
    both sides must be measured, for the same reason a comparison needs a
    scored sample on both sides — one number and one absence is not a pair.

    The metric direction for an agreement criterion is `lower_is_better`: the
    ensemble spreading further apart between baseline and candidate is the
    regression, and the criterion states that itself rather than this function
    assuming it.
    """
    rows = conn.execute(
        "SELECT bs.example_id, bc.disagreement, cc.disagreement "
        "FROM clep.run_sample bs "
        "JOIN clep.consensus_result bc "
        "  ON bc.organization_id = bs.organization_id "
        " AND bc.run_sample_id = bs.id AND bc.disagreement_measured "
        "JOIN clep.run_sample cs "
        "  ON cs.organization_id = bs.organization_id "
        " AND cs.example_id = bs.example_id AND cs.run_id = %s "
        "JOIN clep.consensus_result cc "
        "  ON cc.organization_id = cs.organization_id "
        " AND cc.run_sample_id = cs.id AND cc.disagreement_measured "
        "WHERE bs.organization_id = %s AND bs.run_id = %s "
        "ORDER BY bs.example_id",
        (ulid_to_uuid(candidate_run_id), organization_id,
         ulid_to_uuid(baseline_run_id))).fetchall()
    return [Pair(str(r[0]), r[1], r[2]) for r in rows]


def _latency_pairs(conn, organization_id, baseline_run_id,
                   candidate_run_id) -> list[Pair]:
    """Evaluator duration per example.

    This is evaluation latency, not model latency: `duration_ms` on
    `evaluator_outcome` is the only per-sample timing the platform records today.
    Naming it here rather than in a report footnote, because a latency gate that
    quietly measured something else would be worse than none.
    """
    rows = conn.execute(
        "SELECT bs.example_id, SUM(bo.duration_ms), SUM(co.duration_ms) "
        "FROM clep.run_sample bs "
        "JOIN clep.evaluator_outcome bo "
        "  ON bo.organization_id = bs.organization_id AND bo.run_sample_id = bs.id "
        "JOIN clep.run_sample cs "
        "  ON cs.organization_id = bs.organization_id "
        " AND cs.example_id = bs.example_id AND cs.run_id = %s "
        "JOIN clep.evaluator_outcome co "
        "  ON co.organization_id = cs.organization_id AND co.run_sample_id = cs.id "
        "WHERE bs.organization_id = %s AND bs.run_id = %s "
        "GROUP BY bs.example_id ORDER BY bs.example_id",
        (ulid_to_uuid(candidate_run_id), organization_id,
         ulid_to_uuid(baseline_run_id))).fetchall()
    return [Pair(str(r[0]), Decimal(r[1]), Decimal(r[2])) for r in rows]


def _mean(values) -> Decimal | None:
    values = list(values)
    if not values:
        return None
    return (sum(values, Decimal(0)) / Decimal(len(values))).quantize(
        statistics.RESOLUTION)
