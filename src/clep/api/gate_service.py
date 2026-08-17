"""Application service for baselines, gate policies and gate decisions.

Same rules as the other services: a tenant-bound session per call, the
organization from the ingress principal, responses in the contract's vocabulary,
and every governance write audited inside the same transaction as the thing it
records.

The one rule specific to this service: a gate evaluation computes everything
before it writes anything. A decision half-written is a decision whose evidence
is missing exactly the parts that failed.
"""
from __future__ import annotations

import time
import uuid
from decimal import Decimal

from clep.api import audit
from clep.db.session import tenant_session
from clep.experiments.repository import IdentityRepository
from clep.identity import ulid_to_uuid, uuid_to_ulid
from clep.regression import engine, report
from clep.regression.repository import (BaselineNotEligible, PolicyFrozen,
                                        PolicyNotPublished, RegressionError,
                                        RegressionRepository)
from clep.regression.statistics import METHOD_VERSION
from clep.telemetry import NULL_TELEMETRY
from clep.telemetry.catalog import GATE_VERDICTS


class GateService:
    def __init__(self, runtime_dsn: str, *, telemetry=None):
        self._dsn = runtime_dsn
        self._telemetry = telemetry or NULL_TELEMETRY

    # ------------------------------------------------------------- baselines
    def create_baseline(self, *, organization_id: str, project_id: str,
                        run_id: str, actor_id: str,
                        label: str | None = None) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            try:
                baseline_id = repo.create_baseline(run_id=run_id, label=label,
                                                   created_by=actor_id)
            except BaselineNotEligible:
                return None
            _audit(conn, organization_id, actor_id, "baseline.created", "baseline",
                   baseline_id)
            return _present_baseline(repo.get_baseline(baseline_id))

    def approve_baseline(self, *, organization_id: str, baseline_id: str,
                         actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            if repo.get_baseline(baseline_id) is None:
                return None
            repo.approve_baseline(baseline_id, approved_by=actor_id)
            _audit(conn, organization_id, actor_id, "baseline.approved", "baseline",
                   baseline_id)
            return _present_baseline(repo.get_baseline(baseline_id))

    # -------------------------------------------------------------- policies
    def create_gate_policy(self, *, organization_id: str, project_id: str,
                           slug: str, display_name: str, actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            policy_id = repo.create_gate_policy(project_id=project_id, slug=slug,
                                                display_name=display_name)
            _audit(conn, organization_id, actor_id, "gate_policy.created",
                   "gate_policy", policy_id)
            return {"id": policy_id, "projectId": project_id, "slug": slug,
                    "displayName": display_name}

    def add_policy_version(self, *, organization_id: str, policy_id: str,
                           confidence_level: Decimal, resample_count: int,
                           bootstrap_seed: int, criteria: list[dict],
                           actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            exists = conn.execute(
                "SELECT 1 FROM clep.gate_policy WHERE organization_id = %s "
                "AND id = %s", (organization_id, ulid_to_uuid(policy_id))).fetchone()
            if exists is None:
                return None
            version_id = repo.add_policy_version(
                policy_id, confidence_level=confidence_level,
                resample_count=resample_count, bootstrap_seed=bootstrap_seed,
                created_by=actor_id)
            for criterion in criteria:
                repo.add_criterion(
                    version_id,
                    metric_key=criterion["metricKey"],
                    dimension=criterion["dimension"], source=criterion["source"],
                    direction=criterion["direction"],
                    on_regression=criterion["onRegression"],
                    on_insufficient_evidence=criterion["onInsufficientEvidence"],
                    on_not_comparable=criterion["onNotComparable"],
                    precision_threshold=_decimal(criterion.get("precisionThreshold")),
                    minimum_sample_size=criterion.get("minimumSampleSize"),
                    absolute_floor=_decimal(criterion.get("absoluteFloor")),
                    relative_tolerance=_decimal(criterion.get("relativeTolerance")))
            _audit(conn, organization_id, actor_id, "gate_policy_version.created",
                   "gate_policy_version", version_id)
            return self._present_version(repo, version_id)

    def publish_policy_version(self, *, organization_id: str, version_id: str,
                               actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            if repo.get_policy_version(version_id) is None:
                return None
            repo.publish_policy_version(version_id)
            _audit(conn, organization_id, actor_id, "gate_policy_version.published",
                   "gate_policy_version", version_id)
            return self._present_version(repo, version_id)

    # -------------------------------------------------------------- decisions
    def evaluate_gate(self, *, organization_id: str, project_id: str,
                      candidate_run_id: str, policy_version_id: str,
                      baseline_id: str | None, actor_id: str) -> dict | None:
        """`REQ-N-PERF-1`'s measurement point, and only the platform's share of it.

        The clock starts here and stops when the decision is persisted, so what
        it measures is the gate's own work: the statistics, the comparability
        check and the writes. Provider time is not in it — the candidate run
        finished before this was called — which is what keeps the gate-latency
        indicator from silently including somebody else's outage (ADR-023 rule 5).

        `D-4` is untouched. This observes how long a decision took; it changes
        nothing about what any gate criterion measures or how any verdict is
        reached.
        """
        started = time.monotonic()
        decision = self._evaluate_gate(
            organization_id=organization_id, project_id=project_id,
            candidate_run_id=candidate_run_id,
            policy_version_id=policy_version_id, baseline_id=baseline_id,
            actor_id=actor_id)
        if decision is not None:
            # `evaluatedOutcome`, not `outcome`: the latter is the effective
            # result after a live policy exception is applied, and how long the
            # decision took is a fact about evaluating it, not about waiving it.
            verdict = decision.get("evaluatedOutcome")
            # A database CHECK constrains this column to exactly GATE_VERDICTS,
            # so the guard cannot fire. It is here for what happens if that ever
            # stops being true: one missing histogram sample, rather than a
            # CardinalityError turning a real gate decision into a 500.
            if verdict in GATE_VERDICTS:
                elapsed = (time.monotonic() - started) * 1000.0
                self._telemetry.observe("clep_gate_decision_duration_ms",
                                        elapsed, verdict=verdict)
        return decision

    def _evaluate_gate(self, *, organization_id: str, project_id: str,
                       candidate_run_id: str, policy_version_id: str,
                       baseline_id: str | None, actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            identities = IdentityRepository(conn, organization_id)

            policy_version = repo.get_policy_version(policy_version_id)
            if policy_version is None:
                return None
            if policy_version.state != "published":
                raise PolicyNotPublished(
                    f"gate policy version {policy_version_id} is a draft; a "
                    f"release decision cites a version that cannot still change")
            criteria = repo.criteria_of(policy_version_id)

            run = conn.execute(
                "SELECT suite_version_id, project_id FROM clep.run "
                "WHERE organization_id = %s AND id = %s",
                (organization_id, ulid_to_uuid(candidate_run_id))).fetchone()
            if run is None:
                return None

            baseline = (repo.get_baseline(baseline_id) if baseline_id
                        else repo.approved_baseline_for(
                            project_id=project_id,
                            suite_version_id=uuid_to_ulid(run[0])))
            if baseline is None:
                # REQ-F-09-5 and REQ-F-08-4 together: no baseline is not a
                # quality verdict and not a platform failure. It is a decision
                # that the two things could not be compared, recorded as such.
                return self._record_uncomparable(
                    conn, repo, organization_id=organization_id,
                    project_id=project_id, candidate_run_id=candidate_run_id,
                    policy_version_id=policy_version_id, criteria=criteria,
                    actor_id=actor_id,
                    reason=("no approved baseline exists for this project and "
                            "suite version, so there is nothing to compare "
                            "against"))

            result = engine.evaluate(
                conn, organization_id, baseline=baseline,
                candidate_run_id=candidate_run_id, policy_version=policy_version,
                criteria=criteria,
                baseline_identity=identities.components_of(baseline.run_id),
                candidate_identity=identities.components_of(candidate_run_id))

            decision_id = self._persist(
                conn, repo, organization_id=organization_id,
                project_id=project_id, candidate_run_id=candidate_run_id,
                baseline_id=baseline.id, policy_version_id=policy_version_id,
                result=result, actor_id=actor_id)
            return self._present_decision(repo, decision_id)

    def get_decision(self, organization_id: str, decision_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            if repo.get_decision(decision_id) is None:
                return None
            return self._present_decision(repo, decision_id)

    def decision_report(self, organization_id: str, decision_id: str) -> str | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            decision = repo.get_decision(decision_id)
            if decision is None:
                return None
            return report.human_readable(
                decision, repo.comparisons_of(decision_id),
                repo.criterion_results_of(decision_id),
                repo.live_exception_for(decision_id))

    def create_exception(self, *, organization_id: str, decision_id: str,
                         justification: str, expires_at, actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegressionRepository(conn, organization_id)
            if repo.get_decision(decision_id) is None:
                return None
            exception_id = repo.create_exception(
                decision_id, actor_id=actor_id, justification=justification,
                expires_at=expires_at)
            _audit(conn, organization_id, actor_id, "policy_exception.created",
                   "gate_decision", decision_id)
            live = repo.live_exception_for(decision_id)
            return {"id": exception_id, "actorId": live["actorId"],
                    "justification": live["justification"],
                    "expiresAt": live["expiresAt"], "createdAt": live["createdAt"]}

    # ---------------------------------------------------------------- internals
    def _persist(self, conn, repo, *, organization_id, project_id,
                 candidate_run_id, baseline_id, policy_version_id, result,
                 actor_id) -> str:
        evidence = {"outcome": result.outcome,
                    "criteria": [{"metric": o.metric_key, "verdict": o.verdict,
                                  "rule": o.rule_fired,
                                  "comparison": o.comparison}
                                 for o in result.criterion_outcomes]}
        decision_id = repo.record_decision(
            project_id=project_id, candidate_run_id=candidate_run_id,
            baseline_id=baseline_id, policy_version_id=policy_version_id,
            evaluated_outcome=result.outcome, method_version=METHOD_VERSION,
            evidence=evidence, decided_by=actor_id)
        for outcome in result.criterion_outcomes:
            comparison_id = None
            if outcome.comparison:
                comparison_id = repo.record_comparison(
                    decision_id, method_version=METHOD_VERSION,
                    **_comparison_columns(outcome.comparison))
            repo.record_criterion_result(
                decision_id, criterion_id=outcome.criterion_id,
                comparison_id=comparison_id, verdict=outcome.verdict,
                rule_fired=outcome.rule_fired, detail=outcome.detail)
        _audit(conn, organization_id, actor_id, "gate_decision.recorded",
               "gate_decision", decision_id)
        return decision_id

    def _record_uncomparable(self, conn, repo, *, organization_id, project_id,
                             candidate_run_id, policy_version_id, criteria,
                             actor_id, reason) -> dict:
        result = engine.GateResult(
            outcome="not_comparable", not_comparable_reason=reason,
            criterion_outcomes=[
                engine.CriterionOutcome(
                    criterion_id=c.id, metric_key=c.metric_key,
                    verdict=c.on_not_comparable, rule_fired="comparability",
                    detail=reason,
                    comparison={"metric_key": c.metric_key,
                                "result_kind": "operational",
                                "classification": "not_comparable",
                                "sample_size": 0,
                                "not_comparable_reason": reason})
                for c in criteria])
        decision_id = self._persist(
            conn, repo, organization_id=organization_id, project_id=project_id,
            candidate_run_id=candidate_run_id, baseline_id=None,
            policy_version_id=policy_version_id, result=result, actor_id=actor_id)
        return self._present_decision(repo, decision_id)

    def _present_decision(self, repo, decision_id: str) -> dict:
        return report.machine_readable(
            repo.get_decision(decision_id), repo.comparisons_of(decision_id),
            repo.criterion_results_of(decision_id),
            repo.live_exception_for(decision_id))

    def _present_version(self, repo, version_id: str) -> dict:
        version = repo.get_policy_version(version_id)
        return {"id": version.id, "gatePolicyId": version.gate_policy_id,
                "versionNumber": version.version_number, "state": version.state,
                "confidenceLevel": float(version.confidence_level),
                "resampleCount": version.resample_count,
                "bootstrapSeed": version.bootstrap_seed,
                "criteria": [{"id": c.id, "metricKey": c.metric_key,
                              "dimension": c.dimension, "source": c.source,
                              "direction": c.direction,
                              "precisionThreshold": _str(c.precision_threshold),
                              "minimumSampleSize": c.minimum_sample_size,
                              "absoluteFloor": _str(c.absolute_floor),
                              "relativeTolerance": _str(c.relative_tolerance),
                              "onRegression": c.on_regression,
                              "onInsufficientEvidence": c.on_insufficient_evidence,
                              "onNotComparable": c.on_not_comparable}
                             for c in repo.criteria_of(version_id)]}


def _comparison_columns(comparison: dict) -> dict:
    """Map the engine's record onto the repository's keyword arguments.

    Explicit rather than `**comparison`: the engine's dict is an internal shape
    and a silent key rename would become a column that stops being written.
    """
    return {"metric_key": comparison["metric_key"],
            "result_kind": comparison["result_kind"],
            "classification": comparison["classification"],
            "sample_size": comparison["sample_size"],
            "evaluator_version_id": comparison.get("evaluator_version_id"),
            "baseline_mean": comparison.get("baseline_mean"),
            "candidate_mean": comparison.get("candidate_mean"),
            "mean_difference": comparison.get("mean_difference"),
            "interval_lower": comparison.get("interval_lower"),
            "interval_upper": comparison.get("interval_upper"),
            "confidence_level": comparison.get("confidence_level"),
            "effect_size": comparison.get("effect_size"),
            "minimum_sample_size": comparison.get("minimum_sample_size"),
            "abstention_reason": comparison.get("abstention_reason"),
            "not_comparable_reason": comparison.get("not_comparable_reason")}


def _present_baseline(row) -> dict | None:
    if row is None:
        return None
    body = {"id": row.id, "projectId": row.project_id, "runId": row.run_id,
            "suiteVersionId": row.suite_version_id,
            "datasetVersionId": row.dataset_version_id, "state": row.state,
            "identityDigest": row.identity_digest}
    if row.label:
        body["label"] = row.label
    return body


def _decimal(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _str(value) -> str | None:
    return None if value is None else str(value)


def _audit(conn, organization_id: str, actor: str, action: str,
           target_type: str, target_id: str) -> None:
    audit.record(conn, organization_id, actor, action, target_type, target_id)
