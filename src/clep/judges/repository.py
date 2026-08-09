"""Persistence for judges, ensembles, judgements and escalations.

The store holds the invariants that matter — a published judge version cannot
move, a used ensemble cannot be recomposed, an escalation is reviewed once, an
unscored judgement has no score row — so this module's job is to refuse to
*attempt* what the triggers would refuse, and to return an error a caller can act
on rather than a trigger message.

Two things it deliberately does not do. It does not decide consensus: that is
`consensus.reach_consensus`, which takes votes and has no way to reach a
provider. And it does not write a score for a judgement that produced none, for
the reason schema 08 splits the tables in the first place.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from clep.identity import actor_uuid, new_ulid, ulid_to_uuid, uuid_to_ulid
from clep.judges.consensus import Ensemble
from clep.judges.sdk import JudgeVersion


class JudgeRepositoryError(RuntimeError):
    pass


class JudgeVersionFrozen(JudgeRepositoryError):
    """A published version, or one that has judged, cannot change."""


class EnsembleInUse(JudgeRepositoryError):
    """An ensemble that reached a verdict cannot be recomposed."""


class JudgeVersionNotPublished(JudgeRepositoryError):
    """A draft judge cannot decide anything.

    A caller error rather than a platform failure, on the same reasoning as
    `PolicyNotPublished`: the request named something that exists and is not yet
    fit to be cited. `REQ-F-09-5` turns on that distinction.
    """


class EscalationAlreadyReviewed(JudgeRepositoryError):
    """I-24. A second review would be a retry."""


@dataclass(frozen=True)
class JudgeVersionRow:
    id: str
    judge_definition_id: str
    model_configuration_id: str
    version_number: int
    state: str
    rubric_digest: str
    content_digest: str


@dataclass(frozen=True)
class EnsembleRow:
    id: str
    slug: str
    judge_version_ids: tuple
    agreement_threshold: Decimal | None
    minimum_scoring_votes: int | None


def content_digest(payload: object) -> str:
    material = json.dumps(payload, ensure_ascii=True, separators=(",", ":"),
                          sort_keys=True, default=str)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class JudgeRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    # ------------------------------------------------------------- definitions
    def create_judge(self, *, project_id: str, slug: str,
                     display_name: str) -> str:
        judge_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.judge_definition (id, organization_id, project_id, "
            "slug, display_name) VALUES (%s, %s, %s, %s, %s)",
            (ulid_to_uuid(judge_id), self._org, ulid_to_uuid(project_id), slug,
             display_name))
        return judge_id

    def add_version(self, *, judge_id: str, model_configuration_id: str,
                    rubric: str, created_by: str) -> JudgeVersionRow:
        rubric_digest = content_digest(rubric)
        row = self._conn.execute(
            "SELECT coalesce(max(version_number), 0) + 1 "
            "FROM clep.judge_version WHERE organization_id = %s "
            "AND judge_definition_id = %s",
            (self._org, ulid_to_uuid(judge_id))).fetchone()
        version_number = row[0]
        digest = content_digest({"judge": judge_id, "version": version_number,
                                 "rubric": rubric_digest,
                                 "model_configuration": model_configuration_id})
        version_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.judge_version (id, organization_id, "
            "judge_definition_id, model_configuration_id, version_number, "
            "rubric_digest, content_digest, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(version_id), self._org, ulid_to_uuid(judge_id),
             ulid_to_uuid(model_configuration_id), version_number,
             rubric_digest, digest, actor_uuid(created_by)))
        return JudgeVersionRow(id=version_id, judge_definition_id=judge_id,
                               model_configuration_id=model_configuration_id,
                               version_number=version_number, state="draft",
                               rubric_digest=rubric_digest, content_digest=digest)

    def publish_version(self, version_id: str) -> JudgeVersionRow:
        current = self.get_version(version_id)
        if current is None:
            return None
        if current.state == "published":
            raise JudgeVersionFrozen(
                f"judge version {version_id} is already published; publish a new "
                f"version rather than moving one runs have already cited")
        self._conn.execute(
            "UPDATE clep.judge_version SET state = 'published', "
            "published_at = now() WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(version_id)))
        return self.get_version(version_id)

    def get_version(self, version_id: str) -> JudgeVersionRow | None:
        row = self._conn.execute(
            "SELECT id, judge_definition_id, model_configuration_id, "
            "version_number, state, rubric_digest, content_digest "
            "FROM clep.judge_version WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(version_id))).fetchone()
        if row is None:
            return None
        return JudgeVersionRow(id=uuid_to_ulid(row[0]),
                               judge_definition_id=uuid_to_ulid(row[1]),
                               model_configuration_id=uuid_to_ulid(row[2]),
                               version_number=row[3], state=row[4],
                               rubric_digest=row[5], content_digest=row[6])

    # ---------------------------------------------------------------- ensembles
    def create_ensemble(self, *, project_id: str, slug: str,
                        judge_version_ids, agreement_threshold: Decimal | None,
                        minimum_scoring_votes: int | None,
                        created_by: str) -> EnsembleRow:
        """Refuses a draft member, then refuses a composition ADR-017 forbids.

        The composition rules live in `Ensemble`, and this constructs one rather
        than restating them. A second copy of "at least two configurations"
        would be a second thing to keep in step with the ADR.
        """
        members = []
        for version_id in judge_version_ids:
            version = self.get_version(version_id)
            if version is None:
                raise JudgeRepositoryError(f"no judge version {version_id}")
            if version.state != "published":
                raise JudgeVersionNotPublished(
                    f"judge version {version_id} is a draft; an ensemble made of "
                    f"drafts would judge under a rubric that can still change")
            members.append(version)

        Ensemble(judges=tuple(
            JudgeVersion(slug=m.id, version=str(m.version_number),
                         model=m.model_configuration_id, endpoint_name="stored",
                         rubric=m.rubric_digest) for m in members),
            agreement_threshold=agreement_threshold,
            minimum_scoring_votes=minimum_scoring_votes)

        ensemble_id = new_ulid()
        digest = content_digest({"members": sorted(m.id for m in members),
                                 "threshold": str(agreement_threshold),
                                 "minimum": minimum_scoring_votes})
        self._conn.execute(
            "INSERT INTO clep.judge_ensemble (id, organization_id, project_id, "
            "slug, agreement_threshold, minimum_scoring_votes, content_digest, "
            "created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(ensemble_id), self._org, ulid_to_uuid(project_id), slug,
             agreement_threshold, minimum_scoring_votes, digest,
             actor_uuid(created_by)))
        for member in members:
            self._conn.execute(
                "INSERT INTO clep.judge_ensemble_member (id, organization_id, "
                "judge_ensemble_id, judge_version_id) VALUES (%s, %s, %s, %s)",
                (uuid.uuid4(), self._org, ulid_to_uuid(ensemble_id),
                 ulid_to_uuid(member.id)))
        return self.get_ensemble(ensemble_id)

    def get_ensemble(self, ensemble_id: str) -> EnsembleRow | None:
        row = self._conn.execute(
            "SELECT id, slug, agreement_threshold, minimum_scoring_votes "
            "FROM clep.judge_ensemble WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(ensemble_id))).fetchone()
        if row is None:
            return None
        members = self._conn.execute(
            "SELECT judge_version_id FROM clep.judge_ensemble_member "
            "WHERE organization_id = %s AND judge_ensemble_id = %s "
            "ORDER BY judge_version_id",
            (self._org, ulid_to_uuid(ensemble_id))).fetchall()
        return EnsembleRow(id=uuid_to_ulid(row[0]), slug=row[1],
                           judge_version_ids=tuple(uuid_to_ulid(m[0])
                                                   for m in members),
                           agreement_threshold=row[2],
                           minimum_scoring_votes=row[3])

    # --------------------------------------------------------------- judgements
    def record_judgement(self, *, run_id: str, run_sample_id: str,
                         judge_version_id: str, vote, prompt_digest: str,
                         idempotency_key: str) -> str:
        """One attempt, and a score only if there was one.

        The score is a second row rather than a nullable column, so an
        abstention is not one NULL check away from being a zero (REQ-X-8).
        """
        judge_run_id = uuid.uuid4()
        self._conn.execute(
            "INSERT INTO clep.judge_run (id, organization_id, run_id, "
            "run_sample_id, judge_version_id, resolution, latency_ms, cost, "
            "currency, content_neutralised, prompt_digest, detail, "
            "idempotency_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, idempotency_key) DO NOTHING",
            (judge_run_id, self._org, ulid_to_uuid(run_id),
             ulid_to_uuid(run_sample_id), ulid_to_uuid(judge_version_id),
             vote.resolution, vote.latency_ms, vote.cost, vote.currency,
             vote.content_neutralised, prompt_digest, vote.detail or None,
             idempotency_key))
        stored = self._conn.execute(
            "SELECT id FROM clep.judge_run WHERE organization_id = %s "
            "AND idempotency_key = %s",
            (self._org, idempotency_key)).fetchone()
        judge_run_id = stored[0]
        if vote.is_scoring:
            self._conn.execute(
                "INSERT INTO clep.judge_vote (id, organization_id, judge_run_id, "
                "score) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (organization_id, judge_run_id) DO NOTHING",
                (uuid.uuid4(), self._org, judge_run_id, vote.score))
        return uuid_to_ulid(judge_run_id)

    def record_consensus(self, *, run_id: str, run_sample_id: str,
                         ensemble_id: str, consensus, project_id: str) -> str:
        """The verdict and, when it escalated, the row a person will act on."""
        consensus_id = uuid.uuid4()
        self._conn.execute(
            "INSERT INTO clep.consensus_result (id, organization_id, run_id, "
            "run_sample_id, judge_ensemble_id, state, disagreement, "
            "disagreement_measured, method_version, verdict, confidence, "
            "escalation_reason, escalation_detail, scoring_vote_count) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (consensus_id, self._org, ulid_to_uuid(run_id),
             ulid_to_uuid(run_sample_id), ulid_to_uuid(ensemble_id),
             consensus.state, consensus.disagreement,
             consensus.disagreement_measured, consensus.method_version,
             consensus.verdict, consensus.confidence,
             consensus.escalation_reason, consensus.escalation_detail or None,
             len(consensus.scoring_votes)))
        if consensus.state == "escalated":
            self._conn.execute(
                "INSERT INTO clep.escalation (id, organization_id, project_id, "
                "consensus_result_id, reason) VALUES (%s, %s, %s, %s, %s)",
                (uuid.uuid4(), self._org, ulid_to_uuid(project_id), consensus_id,
                 consensus.escalation_reason))
        return uuid_to_ulid(consensus_id)

    # -------------------------------------------------------------- escalations
    def list_escalations(self, project_id: str, state: str | None = None):
        clause, params = ("", [])
        if state:
            clause, params = " AND e.state = %s", [state]
        rows = self._conn.execute(
            "SELECT e.id, e.state, e.reason, c.disagreement, e.raised_at, "
            "       e.reviewed_by, e.reviewed_at, e.review_outcome, "
            "       c.run_id, c.run_sample_id "
            "FROM clep.escalation e "
            "JOIN clep.consensus_result c "
            "  ON c.organization_id = e.organization_id "
            "  AND c.id = e.consensus_result_id "
            "WHERE e.organization_id = %s AND e.project_id = %s" + clause +
            " ORDER BY e.raised_at, e.id",
            [self._org, ulid_to_uuid(project_id), *params]).fetchall()
        return [{"id": uuid_to_ulid(r[0]), "state": r[1], "reason": r[2],
                 "disagreement": float(r[3]), "raisedAt": r[4],
                 "reviewedBy": str(r[5]) if r[5] else None,
                 "reviewedAt": r[6], "reviewOutcome": r[7],
                 "runId": uuid_to_ulid(r[8]),
                 "runSampleId": uuid_to_ulid(r[9])} for r in rows]

    def review_escalation(self, escalation_id: str, *, actor_id: str,
                          outcome: str, justification: str):
        current = self._conn.execute(
            "SELECT state FROM clep.escalation WHERE organization_id = %s "
            "AND id = %s", (self._org, ulid_to_uuid(escalation_id))).fetchone()
        if current is None:
            return None
        if current[0] == "reviewed":
            raise EscalationAlreadyReviewed(
                f"escalation {escalation_id} has already been reviewed; a second "
                f"review would be a retry, and escalation is terminal (I-24)")
        self._conn.execute(
            "UPDATE clep.escalation SET state = 'reviewed', reviewed_by = %s, "
            "reviewed_at = now(), review_outcome = %s, justification = %s "
            "WHERE organization_id = %s AND id = %s",
            (actor_uuid(actor_id), outcome, justification, self._org,
             ulid_to_uuid(escalation_id)))
        row = self._conn.execute(
            "SELECT e.id, e.state, e.reason, c.disagreement, e.raised_at, "
            "       e.reviewed_by, e.reviewed_at, e.review_outcome "
            "FROM clep.escalation e JOIN clep.consensus_result c "
            "  ON c.organization_id = e.organization_id "
            "  AND c.id = e.consensus_result_id "
            "WHERE e.organization_id = %s AND e.id = %s",
            (self._org, ulid_to_uuid(escalation_id))).fetchone()
        return {"id": uuid_to_ulid(row[0]), "state": row[1], "reason": row[2],
                "disagreement": float(row[3]), "raisedAt": row[4],
                "reviewedBy": str(row[5]), "reviewedAt": row[6],
                "reviewOutcome": row[7]}
