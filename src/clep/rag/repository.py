"""Persistence and read-back for what Phase 9 evaluates.

Retrieved passages, citations, trajectory steps, hallucination findings and
stage attribution, written by the harness as a run executes and reconstructed
afterwards through `getSampleAnalysis`.

Two rules shape this module.

**Written by the run, not by a fixture.** The Phase 9 review accepted the
real-model experiment as runtime validation while noting that it wrote files
rather than rows. This is the production path: the same code writes whether the
judgement came from a real model or a deterministic stub, and nothing copies
test output into the store.

**Read back is reconstruction, not a second rendering.** `analysis` returns what
was written, in the contract's shape, with no value computed a second time.
A summary that recomputed anything could disagree with the evidence it claims
to summarise — the same rule `clep.memory` follows.
"""
from __future__ import annotations

import hashlib
import uuid

import psycopg

from clep.identity import ulid_to_uuid, uuid_to_ulid


def digest_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


class AnalysisRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    # ------------------------------------------------------------- retrieval
    def record_required_context(self, example_id: str, refs) -> int:
        """What the dataset says retrieval was supposed to find.

        On the example, because that is what it is a fact about, and because a
        required passage the retriever missed is absent from what came back.
        """
        written = 0
        for ref in refs:
            written += self._conn.execute(
                "INSERT INTO clep.required_context (id, organization_id, "
                "example_id, context_ref) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (organization_id, example_id, context_ref) "
                "DO NOTHING",
                (uuid.uuid4(), self._org, ulid_to_uuid(example_id),
                 ref)).rowcount
        return written

    def record_retrieval(self, *, run_sample_id: str, contexts, citations=()) -> int:
        """The passages this sample retrieved, and which of them it cited.

        The citation is written as a foreign key into what was retrieved, so a
        citation naming a passage the system never saw cannot be stored at all.
        A citation that resolves to nothing is dropped here and reported by the
        `citation_validity` evaluator, which is where it belongs: it is a
        finding about the answer, not a storage error.
        """
        sample = ulid_to_uuid(run_sample_id)
        by_ref = {}
        for context in contexts:
            row_id = uuid.uuid4()
            self._conn.execute(
                "INSERT INTO clep.retrieved_context (id, organization_id, "
                "run_sample_id, context_ref, retrieval_rank, content_digest) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (organization_id, run_sample_id, context_ref) "
                "DO NOTHING",
                (row_id, self._org, sample, context.id, context.rank,
                 digest_of(context.text)))
            stored = self._conn.execute(
                "SELECT id FROM clep.retrieved_context WHERE organization_id = %s "
                "AND run_sample_id = %s AND context_ref = %s",
                (self._org, sample, context.id)).fetchone()
            by_ref[context.id] = stored[0]

        written = 0
        for ref in citations:
            target = by_ref.get(ref)
            if target is None:
                continue
            written += self._conn.execute(
                "INSERT INTO clep.sample_citation (id, organization_id, "
                "run_sample_id, retrieved_context_id) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (organization_id, run_sample_id, "
                "retrieved_context_id) DO NOTHING",
                (uuid.uuid4(), self._org, sample, target)).rowcount
        return written

    # ------------------------------------------------------------ trajectory
    def record_trajectory(self, *, run_sample_id: str, trajectory) -> int:
        """The steps, and the fact that there may have been more.

        Truncation is NOT written here. It goes on the sample at insert time,
        because a resolved sample is immutable and the runtime role has no
        UPDATE grant on it — the store refused that write, correctly. A run
        that happened to stop at the limit and one that was cut at it are
        different facts, and only the ingest knows which, so the ingest's answer
        travels with the sample rather than being inferred from a step count
        here.
        """
        sample = ulid_to_uuid(run_sample_id)
        for step in trajectory.steps:
            self._conn.execute(
                "INSERT INTO clep.trajectory_step (id, organization_id, "
                "run_sample_id, step_order, tool, arguments, result_digest, "
                "failed, error) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (organization_id, run_sample_id, step_order) "
                "DO NOTHING",
                (uuid.uuid4(), self._org, sample, step.step, step.tool,
                 psycopg.types.json.Json(step.arguments),
                 digest_of(step.result) if step.result else None,
                 step.failed, step.error or None))
        return len(trajectory.steps)

    # -------------------------------------------------------------- analysis
    def record_hallucination(self, *, run_sample_id: str, report) -> int:
        sample = ulid_to_uuid(run_sample_id)
        for ordinal, claim in enumerate(report.claims):
            self._conn.execute(
                "INSERT INTO clep.hallucination_finding (id, organization_id, "
                "run_sample_id, claim_ordinal, claim_digest, finding, "
                "support_score, contradiction_score, support_threshold, "
                "contradiction_threshold, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (organization_id, run_sample_id, claim_ordinal) "
                "DO NOTHING",
                (uuid.uuid4(), self._org, sample, ordinal,
                 digest_of(claim.claim), claim.finding, claim.support,
                 claim.contradiction,
                 report.support_threshold if claim.finding != "not_analysable"
                 else None,
                 report.contradiction_threshold
                 if claim.finding != "not_analysable" else None,
                 claim.reason or None))
        return len(report.claims)

    def record_attribution(self, *, run_sample_id: str, attribution) -> None:
        self._conn.execute(
            "INSERT INTO clep.stage_attribution (id, organization_id, "
            "run_sample_id, stage, reason, missing_context_refs, "
            "faithfulness_score, faithfulness_threshold) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, run_sample_id) DO NOTHING",
            (uuid.uuid4(), self._org, ulid_to_uuid(run_sample_id),
             attribution.stage, attribution.reason,
             ",".join(attribution.missing_context_ids)
             if attribution.missing_context_ids else None,
             attribution.faithfulness, None))

    # ------------------------------------------------------------- read back
    def analysis(self, run_sample_id: str) -> dict | None:
        """Reconstruct what was written. Nothing is recomputed."""
        sample = ulid_to_uuid(run_sample_id)
        row = self._conn.execute(
            "SELECT s.id, s.example_id, s.trajectory_truncated "
            "FROM clep.run_sample s "
            "WHERE s.organization_id = %s AND s.id = %s",
            (self._org, sample)).fetchone()
        if row is None:
            return None

        cited = {r[0] for r in self._conn.execute(
            "SELECT c.context_ref FROM clep.sample_citation sc "
            "JOIN clep.retrieved_context c "
            "  ON c.organization_id = sc.organization_id "
            " AND c.id = sc.retrieved_context_id "
            "WHERE sc.organization_id = %s AND sc.run_sample_id = %s",
            (self._org, sample)).fetchall()}
        required = [r[0] for r in self._conn.execute(
            "SELECT context_ref FROM clep.required_context "
            "WHERE organization_id = %s AND example_id = %s ORDER BY context_ref",
            (self._org, row[1])).fetchall()]
        contexts = [
            {"contextRef": r[0], "retrievalRank": r[1], "contentDigest": r[2],
             "cited": r[0] in cited, "required": r[0] in set(required)}
            for r in self._conn.execute(
                "SELECT context_ref, retrieval_rank, content_digest "
                "FROM clep.retrieved_context WHERE organization_id = %s "
                "AND run_sample_id = %s ORDER BY retrieval_rank",
                (self._org, sample)).fetchall()]
        steps = [
            {"stepOrder": r[0], "tool": r[1], "arguments": r[2],
             "resultDigest": r[3], "failed": r[4], "error": r[5]}
            for r in self._conn.execute(
                "SELECT step_order, tool, arguments, result_digest, failed, error "
                "FROM clep.trajectory_step WHERE organization_id = %s "
                "AND run_sample_id = %s ORDER BY step_order",
                (self._org, sample)).fetchall()]
        claims = [
            {"claimOrdinal": r[0], "claimDigest": r[1], "finding": r[2],
             "supportScore": str(r[3]) if r[3] is not None else None,
             "contradictionScore": str(r[4]) if r[4] is not None else None,
             "reason": r[5]}
            for r in self._conn.execute(
                "SELECT claim_ordinal, claim_digest, finding, support_score, "
                "contradiction_score, reason FROM clep.hallucination_finding "
                "WHERE organization_id = %s AND run_sample_id = %s "
                "ORDER BY claim_ordinal", (self._org, sample)).fetchall()]
        attribution_row = self._conn.execute(
            "SELECT stage, reason, missing_context_refs, faithfulness_score "
            "FROM clep.stage_attribution WHERE organization_id = %s "
            "AND run_sample_id = %s", (self._org, sample)).fetchone()

        body = {"runSampleId": uuid_to_ulid(row[0]),
                "retrievedContexts": contexts,
                "requiredContextRefs": required,
                "trajectory": steps,
                "trajectoryTruncated": row[2],
                "claims": claims}
        if attribution_row is not None:
            body["attribution"] = {
                "stage": attribution_row[0], "reason": attribution_row[1],
                "missingContextRefs": (attribution_row[2].split(",")
                                       if attribution_row[2] else []),
                "faithfulness": (str(attribution_row[3])
                                 if attribution_row[3] is not None else None)}
        return body
