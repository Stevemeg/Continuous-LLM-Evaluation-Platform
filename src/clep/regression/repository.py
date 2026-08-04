"""Persistence for baselines, gate policies, decisions and their evidence.

The store enforces the invariants that matter — a decision cannot be edited, a
published policy version cannot move, one approved baseline exists per scope — so
this module's job is to refuse to *try*, producing a clear error instead of a
trigger message, and to read back the evidence a report is assembled from.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from clep.identity import actor_uuid, new_ulid, ulid_to_uuid, uuid_to_ulid


class RegressionError(RuntimeError):
    pass


class BaselineNotEligible(RegressionError):
    """A run that has not finished cannot be a baseline."""


class PolicyFrozen(RegressionError):
    """Raised instead of attempting a write the store would refuse."""


class PolicyNotPublished(RegressionError):
    """A draft cannot decide a release.

    A caller error rather than a platform failure: the request named a policy
    version that exists and is not yet fit to be cited, which is a 422 and not a
    503. REQ-F-09-5 turns on that distinction.
    """


@dataclass(frozen=True)
class BaselineRow:
    id: str
    project_id: str
    run_id: str
    suite_version_id: str
    dataset_version_id: str
    state: str
    identity_digest: str
    label: str | None


@dataclass(frozen=True)
class CriterionRow:
    id: str
    metric_key: str
    dimension: str
    source: str
    direction: str
    precision_threshold: Decimal | None
    minimum_sample_size: int | None
    absolute_floor: Decimal | None
    relative_tolerance: Decimal | None
    on_regression: str
    on_insufficient_evidence: str
    on_not_comparable: str


@dataclass(frozen=True)
class PolicyVersionRow:
    id: str
    gate_policy_id: str
    version_number: int
    state: str
    confidence_level: Decimal
    resample_count: int
    bootstrap_seed: int


def evidence_digest(payload: object) -> str:
    """A digest over the evidence, so a report can be shown to be the one decided on.

    Canonical encoding for the same reason the run identity uses one: a digest
    that depends on dict ordering cannot be re-derived in another process.
    """
    material = json.dumps(payload, ensure_ascii=True, separators=(",", ":"),
                          sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class RegressionRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    # ------------------------------------------------------------- baselines
    def create_baseline(self, *, run_id: str, created_by: str,
                        label: str | None = None) -> str:
        """Derive the baseline's scope from the run rather than from the caller.

        A caller-supplied dataset or suite could disagree with the run's own, and
        the resulting baseline would claim to be a measurement of something it is
        not.
        """
        run = self._conn.execute(
            "SELECT project_id, suite_version_id, dataset_version_id, "
            "identity_digest, execution_state, completeness "
            "FROM clep.run WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(run_id))).fetchone()
        if run is None:
            raise BaselineNotEligible(f"run {run_id} does not exist")
        if run[4] != "terminal":
            raise BaselineNotEligible(
                f"run {run_id} is {run[4]}; a baseline is an approved measurement "
                f"and an unfinished run has not measured anything yet")
        if run[5] != "complete":
            raise BaselineNotEligible(
                f"run {run_id} finished {run[5]}; a partial run makes a baseline "
                f"whose gaps would be read as scores")

        baseline_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.baseline (id, organization_id, project_id, run_id, "
            "suite_version_id, dataset_version_id, label, state, identity_digest, "
            "created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, 'pending_approval', "
            "%s, %s)",
            (ulid_to_uuid(baseline_id), self._org, run[0], ulid_to_uuid(run_id),
             run[1], run[2], label, run[3], actor_uuid(created_by)))
        return baseline_id

    def approve_baseline(self, baseline_id: str, *, approved_by: str) -> None:
        """Approve, superseding whatever held the scope, in one transaction.

        Two statements, never two transactions: between them there would be a
        moment with no approved baseline, and `REQ-F-09-7` lets a caller ask for
        "the approved baseline" without naming one.
        """
        row = self.get_baseline(baseline_id)
        if row is None:
            raise RegressionError(f"baseline {baseline_id} does not exist")
        self._conn.execute(
            "UPDATE clep.baseline SET state = 'superseded', superseded_at = now() "
            "WHERE organization_id = %s AND project_id = %s "
            "AND suite_version_id = %s AND state = 'approved'",
            (self._org, ulid_to_uuid(row.project_id),
             ulid_to_uuid(row.suite_version_id)))
        self._conn.execute(
            "UPDATE clep.baseline SET state = 'approved', approved_by = %s, "
            "approved_at = now() WHERE organization_id = %s AND id = %s",
            (actor_uuid(approved_by), self._org, ulid_to_uuid(baseline_id)))

    def get_baseline(self, baseline_id: str) -> BaselineRow | None:
        row = self._conn.execute(
            "SELECT id, project_id, run_id, suite_version_id, dataset_version_id, "
            "state, identity_digest, label FROM clep.baseline "
            "WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(baseline_id))).fetchone()
        return _baseline(row)

    def approved_baseline_for(self, *, project_id: str,
                              suite_version_id: str) -> BaselineRow | None:
        row = self._conn.execute(
            "SELECT id, project_id, run_id, suite_version_id, dataset_version_id, "
            "state, identity_digest, label FROM clep.baseline "
            "WHERE organization_id = %s AND project_id = %s "
            "AND suite_version_id = %s AND state = 'approved'",
            (self._org, ulid_to_uuid(project_id),
             ulid_to_uuid(suite_version_id))).fetchone()
        return _baseline(row)

    # -------------------------------------------------------------- policies
    def create_gate_policy(self, *, project_id: str, slug: str,
                           display_name: str) -> str:
        policy_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.gate_policy (id, organization_id, project_id, slug, "
            "display_name) VALUES (%s, %s, %s, %s, %s)",
            (ulid_to_uuid(policy_id), self._org, ulid_to_uuid(project_id), slug,
             display_name))
        return policy_id

    def add_policy_version(self, policy_id: str, *, confidence_level: Decimal,
                           resample_count: int, bootstrap_seed: int,
                           created_by: str) -> str:
        version_id = new_ulid()
        number = self._next_version_number(policy_id)
        digest = evidence_digest({"policy": policy_id, "version": number,
                                  "confidence_level": str(confidence_level),
                                  "resamples": resample_count,
                                  "seed": bootstrap_seed})
        self._conn.execute(
            "INSERT INTO clep.gate_policy_version (id, organization_id, "
            "gate_policy_id, version_number, content_digest, state, "
            "confidence_level, resample_count, bootstrap_seed, created_by) "
            "VALUES (%s, %s, %s, %s, %s, 'draft', %s, %s, %s, %s)",
            (ulid_to_uuid(version_id), self._org, ulid_to_uuid(policy_id), number,
             digest, confidence_level, resample_count, bootstrap_seed,
             actor_uuid(created_by)))
        return version_id

    def add_criterion(self, version_id: str, *, metric_key: str, dimension: str,
                      source: str, direction: str, on_regression: str,
                      on_insufficient_evidence: str, on_not_comparable: str,
                      precision_threshold: Decimal | None = None,
                      minimum_sample_size: int | None = None,
                      absolute_floor: Decimal | None = None,
                      relative_tolerance: Decimal | None = None) -> str:
        version = self.get_policy_version(version_id)
        if version is None:
            raise RegressionError(f"gate policy version {version_id} does not exist")
        if version.state == "published":
            raise PolicyFrozen(
                f"gate policy version {version_id} is published; add a criterion "
                f"to a new version rather than to a decided one")
        criterion_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.gate_criterion (id, organization_id, "
            "gate_policy_version_id, metric_key, dimension, source, direction, "
            "precision_threshold, minimum_sample_size, absolute_floor, "
            "relative_tolerance, on_regression, on_insufficient_evidence, "
            "on_not_comparable) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s)",
            (ulid_to_uuid(criterion_id), self._org, ulid_to_uuid(version_id),
             metric_key, dimension, source, direction, precision_threshold,
             minimum_sample_size, absolute_floor, relative_tolerance,
             on_regression, on_insufficient_evidence, on_not_comparable))
        return criterion_id

    def publish_policy_version(self, version_id: str) -> None:
        version = self.get_policy_version(version_id)
        if version is None:
            raise RegressionError(f"gate policy version {version_id} does not exist")
        if version.state == "published":
            return
        if not self.criteria_of(version_id):
            raise RegressionError(
                f"gate policy version {version_id} has no criteria; publishing it "
                f"would create a gate that passes everything")
        self._conn.execute(
            "UPDATE clep.gate_policy_version SET state = 'published', "
            "published_at = now() WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(version_id)))

    def get_policy_version(self, version_id: str) -> PolicyVersionRow | None:
        row = self._conn.execute(
            "SELECT id, gate_policy_id, version_number, state, confidence_level, "
            "resample_count, bootstrap_seed FROM clep.gate_policy_version "
            "WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(version_id))).fetchone()
        if row is None:
            return None
        return PolicyVersionRow(id=uuid_to_ulid(row[0]),
                                gate_policy_id=uuid_to_ulid(row[1]),
                                version_number=row[2], state=row[3],
                                confidence_level=row[4], resample_count=row[5],
                                bootstrap_seed=row[6])

    def criteria_of(self, version_id: str) -> list[CriterionRow]:
        rows = self._conn.execute(
            "SELECT id, metric_key, dimension, source, direction, "
            "precision_threshold, minimum_sample_size, absolute_floor, "
            "relative_tolerance, on_regression, on_insufficient_evidence, "
            "on_not_comparable FROM clep.gate_criterion "
            "WHERE organization_id = %s AND gate_policy_version_id = %s "
            "ORDER BY metric_key",
            (self._org, ulid_to_uuid(version_id))).fetchall()
        return [CriterionRow(id=uuid_to_ulid(r[0]), metric_key=r[1], dimension=r[2],
                             source=r[3], direction=r[4], precision_threshold=r[5],
                             minimum_sample_size=r[6], absolute_floor=r[7],
                             relative_tolerance=r[8], on_regression=r[9],
                             on_insufficient_evidence=r[10],
                             on_not_comparable=r[11])
                for r in rows]

    def _next_version_number(self, policy_id: str) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(version_number), 0) + 1 "
            "FROM clep.gate_policy_version "
            "WHERE organization_id = %s AND gate_policy_id = %s",
            (self._org, ulid_to_uuid(policy_id))).fetchone()
        return int(row[0])

    # -------------------------------------------------------------- decisions
    def record_decision(self, *, project_id: str, candidate_run_id: str,
                        baseline_id: str | None, policy_version_id: str,
                        evaluated_outcome: str, method_version: str,
                        evidence: object, decided_by: str) -> str:
        decision_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.gate_decision (id, organization_id, project_id, "
            "candidate_run_id, baseline_id, gate_policy_version_id, "
            "evaluated_outcome, statistical_method_version, evidence_digest, "
            "decided_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(decision_id), self._org, ulid_to_uuid(project_id),
             ulid_to_uuid(candidate_run_id),
             ulid_to_uuid(baseline_id) if baseline_id else None,
             ulid_to_uuid(policy_version_id), evaluated_outcome, method_version,
             evidence_digest(evidence), actor_uuid(decided_by)))
        return decision_id

    def record_comparison(self, decision_id: str, *, metric_key: str,
                          result_kind: str, classification: str, sample_size: int,
                          method_version: str, evaluator_version_id: str | None = None,
                          baseline_mean: Decimal | None = None,
                          candidate_mean: Decimal | None = None,
                          mean_difference: Decimal | None = None,
                          interval_lower: Decimal | None = None,
                          interval_upper: Decimal | None = None,
                          confidence_level: Decimal | None = None,
                          effect_size: Decimal | None = None,
                          minimum_sample_size: int | None = None,
                          abstention_reason: str | None = None,
                          not_comparable_reason: str | None = None) -> str:
        comparison_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.comparison (id, organization_id, gate_decision_id, "
            "metric_key, result_kind, evaluator_version_id, classification, "
            "sample_size, baseline_mean, candidate_mean, mean_difference, "
            "interval_lower, interval_upper, confidence_level, effect_size, "
            "minimum_sample_size, statistical_method_version, abstention_reason, "
            "not_comparable_reason) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, "
            "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(comparison_id), self._org, ulid_to_uuid(decision_id),
             metric_key, result_kind,
             uuid.UUID(evaluator_version_id) if evaluator_version_id else None,
             classification, sample_size, baseline_mean, candidate_mean,
             mean_difference, interval_lower, interval_upper, confidence_level,
             effect_size, minimum_sample_size, method_version, abstention_reason,
             not_comparable_reason))
        return comparison_id

    def record_criterion_result(self, decision_id: str, *, criterion_id: str,
                                comparison_id: str | None, verdict: str,
                                rule_fired: str, detail: str) -> str:
        result_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.gate_criterion_result (id, organization_id, "
            "gate_decision_id, gate_criterion_id, comparison_id, verdict, "
            "rule_fired, detail) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(result_id), self._org, ulid_to_uuid(decision_id),
             ulid_to_uuid(criterion_id),
             ulid_to_uuid(comparison_id) if comparison_id else None,
             verdict, rule_fired, detail))
        return result_id

    def get_decision(self, decision_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, project_id, candidate_run_id, baseline_id, "
            "gate_policy_version_id, evaluated_outcome, statistical_method_version, "
            "evidence_digest, decided_at FROM clep.gate_decision "
            "WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(decision_id))).fetchone()
        if row is None:
            return None
        return {"id": uuid_to_ulid(row[0]), "projectId": uuid_to_ulid(row[1]),
                "candidateRunId": uuid_to_ulid(row[2]),
                "baselineId": uuid_to_ulid(row[3]) if row[3] else None,
                "gatePolicyVersionId": uuid_to_ulid(row[4]),
                "evaluatedOutcome": row[5], "statisticalMethodVersion": row[6],
                "gateEvidenceDigest": row[7], "decidedAt": row[8]}

    def comparisons_of(self, decision_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT metric_key, result_kind, classification, sample_size, "
            "baseline_mean, candidate_mean, mean_difference, interval_lower, "
            "interval_upper, confidence_level, effect_size, minimum_sample_size, "
            "statistical_method_version, abstention_reason, not_comparable_reason, "
            "evaluator_version_id, id FROM clep.comparison "
            "WHERE organization_id = %s AND gate_decision_id = %s "
            "ORDER BY metric_key",
            (self._org, ulid_to_uuid(decision_id))).fetchall()
        return [{"metric": r[0], "resultKind": r[1], "classification": r[2],
                 "sampleSize": r[3], "baselineMean": r[4], "candidateMean": r[5],
                 "meanDifference": r[6], "intervalLower": r[7],
                 "intervalUpper": r[8], "confidenceLevel": r[9],
                 "effectSize": r[10], "minimumSampleSize": r[11],
                 "statisticalMethodVersion": r[12], "abstentionReason": r[13],
                 "notComparableReason": r[14],
                 "evaluatorVersionId": str(r[15]) if r[15] else None,
                 "id": uuid_to_ulid(r[16])}
                for r in rows]

    def criterion_results_of(self, decision_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT c.metric_key, r.verdict, r.rule_fired, r.detail, c.dimension "
            "FROM clep.gate_criterion_result r "
            "JOIN clep.gate_criterion c "
            "  ON c.organization_id = r.organization_id "
            " AND c.id = r.gate_criterion_id "
            "WHERE r.organization_id = %s AND r.gate_decision_id = %s "
            "ORDER BY c.metric_key",
            (self._org, ulid_to_uuid(decision_id))).fetchall()
        return [{"metric": r[0], "verdict": r[1], "ruleFired": r[2],
                 "detail": r[3], "dimension": r[4]} for r in rows]

    # ------------------------------------------------------------- exceptions
    def create_exception(self, decision_id: str, *, actor_id: str,
                         justification: str, expires_at) -> str:
        exception_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.policy_exception (id, organization_id, "
            "gate_decision_id, actor_id, justification, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(exception_id), self._org, ulid_to_uuid(decision_id),
             actor_uuid(actor_id), justification, expires_at))
        return exception_id

    def live_exception_for(self, decision_id: str) -> dict | None:
        """The exception in force *now*, not merely the most recent one.

        An expired exception is a historical fact about a release that was
        allowed through last month, not permission for this one.
        """
        row = self._conn.execute(
            "SELECT id, actor_id, justification, expires_at, created_at "
            "FROM clep.policy_exception "
            "WHERE organization_id = %s AND gate_decision_id = %s "
            "AND expires_at > now() ORDER BY created_at DESC LIMIT 1",
            (self._org, ulid_to_uuid(decision_id))).fetchone()
        if row is None:
            return None
        return {"id": uuid_to_ulid(row[0]), "actorId": str(row[1]),
                "justification": row[2], "expiresAt": row[3],
                "createdAt": row[4]}


def _baseline(row) -> BaselineRow | None:
    if row is None:
        return None
    return BaselineRow(id=uuid_to_ulid(row[0]), project_id=uuid_to_ulid(row[1]),
                       run_id=uuid_to_ulid(row[2]),
                       suite_version_id=uuid_to_ulid(row[3]),
                       dataset_version_id=uuid_to_ulid(row[4]), state=row[5],
                       identity_digest=row[6], label=row[7])
