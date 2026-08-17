"""Persistence for runs, samples, cost and checkpoints.

Every write that has an externally visible effect is idempotent by construction.
This is not caution. The ADR-001 spike crashed a worker in the window between a
sample's side effect committing and the engine recording its completion, and
*both* candidate engines then billed that sample twice. Neither provides
exactly-once effects; the unique key does.

`ON CONFLICT DO NOTHING` therefore appears on the sample and the cost entry, and
the idempotency key is derived, never generated: the same work unit must produce
the same key on every attempt, or the constraint protects nothing.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal

import psycopg

from clep.identity import new_ulid, ulid_to_uuid


def sample_key(run_id: str, candidate_label: str, example_id: str) -> str:
    """Derived from what the work IS, not when it ran or which worker ran it.

    A key containing an attempt number, a timestamp or a worker identity would
    differ between the first attempt and the redelivery, and the second write
    would be accepted — which is the bug the unique constraint exists to stop.
    """
    material = f"{run_id}\x1f{candidate_label}\x1f{example_id}"
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def cost_key(sample_idempotency_key: str) -> str:
    return "cost:" + sample_idempotency_key


@dataclass(frozen=True)
class RunRow:
    id: str
    project_id: str
    execution_state: str
    completeness: str | None
    incomplete_reason: str | None
    reproducibility: str
    identity_digest: str
    integration_tier: str
    budget_limit: Decimal | None
    budget_currency: str | None
    created_at: object
    correlation_id: str | None
    dataset_version_id: str
    suite_version_id: str
    frozen_at: object


class RunRepository:
    """All statements are parameterised. The tenant is never one of the
    parameters: it comes from the session's context, which row-level security
    reads directly, so a query cannot be written that forgets it."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    @property
    def organization_id(self) -> str:
        """Readable, not writable. The execution loop needs the tenant to scope
        an evaluator's grant to it (ADR-006 rule 5), and reaching into a private
        attribute to get it would be a second way to learn the tenant — which is
        exactly the arrangement ADR-010 rule 3 exists to prevent."""
        return self._org

    # ------------------------------------------------------------------- runs
    def create_run(self, *, project_id: str, suite_version_id: str,
                   dataset_version_id: str, identity_digest: str,
                   integration_tier: str, idempotency_key: str,
                   budget_limit: Decimal | None = None,
                   budget_currency: str | None = None,
                   correlation_id: str | None = None,
                   trigger_kind: str = "manual") -> str:
        """Returns the run's ULID. Re-submitting the same idempotency key
        returns the existing run rather than starting a second one — REQ-N-REL-2
        applies to run creation, not only to sample effects.

        `trigger_kind` defaults to `manual` because that is what a run with no
        stated reason is: someone asked for it. The scheduler states its own.
        """
        existing = self.find_run_by_idempotency_key(project_id, idempotency_key)
        if existing:
            return existing

        run_ulid = new_ulid()
        self._conn.execute(
            """
            INSERT INTO clep.run (id, organization_id, project_id, suite_version_id,
                                  dataset_version_id, identity_digest,
                                  integration_tier, execution_state,
                                  budget_limit, budget_currency, correlation_id,
                                  idempotency_key, trigger_kind)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s)
            """,
            (ulid_to_uuid(run_ulid), self._org, ulid_to_uuid(project_id),
             ulid_to_uuid(suite_version_id), ulid_to_uuid(dataset_version_id),
             identity_digest, integration_tier, budget_limit, budget_currency,
             correlation_id, idempotency_key, trigger_kind))
        self._conn.execute(
            "INSERT INTO clep.run_checkpoint (id, organization_id, run_id) "
            "VALUES (%s, %s, %s)",
            (ulid_to_uuid(new_ulid()), self._org, ulid_to_uuid(run_ulid)))
        return run_ulid

    def find_run_by_idempotency_key(self, project_id: str,
                                    idempotency_key: str) -> str | None:
        """The run a key already produced, or None.

        Separate from `create_run` because the scheduler needs the answer
        *before* it decides to do anything: a trigger that has already fired
        must not enqueue a second job, and "create_run returned an id" cannot
        distinguish the first firing from the second.
        """
        from clep.identity import uuid_to_ulid
        row = self._conn.execute(
            "SELECT id FROM clep.run WHERE organization_id = %s AND project_id = %s "
            "AND idempotency_key = %s",
            (self._org, ulid_to_uuid(project_id), idempotency_key)).fetchone()
        return uuid_to_ulid(row[0]) if row else None

    def add_candidate(self, run_id: str, *, label: str, model_configuration_id: str,
                      endpoint_kind: str, prompt_version_id: str | None = None) -> str:
        """Idempotent on (run, label), like every other write here.

        A resubmitted run creation reaches this with the same candidates. Letting
        the unique constraint raise would make the caller distinguish "already
        done" from "went wrong", and that is a distinction callers get wrong.
        """
        from clep.identity import uuid_to_ulid
        candidate = new_ulid()
        row = self._conn.execute(
            """
            INSERT INTO clep.run_candidate (id, organization_id, run_id, label,
                                            model_configuration_id,
                                            prompt_version_id, endpoint_kind)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, run_id, label) DO NOTHING
            RETURNING id
            """,
            (ulid_to_uuid(candidate), self._org, ulid_to_uuid(run_id), label,
             ulid_to_uuid(model_configuration_id),
             ulid_to_uuid(prompt_version_id) if prompt_version_id else None,
             endpoint_kind)).fetchone()
        if row:
            return candidate
        existing = self._conn.execute(
            "SELECT id FROM clep.run_candidate WHERE organization_id = %s "
            "AND run_id = %s AND label = %s",
            (self._org, ulid_to_uuid(run_id), label)).fetchone()
        return uuid_to_ulid(existing[0])

    def mark_running(self, run_id: str) -> None:
        self._conn.execute(
            "UPDATE clep.run SET execution_state = 'running', "
            "started_at = coalesce(started_at, now()) "
            "WHERE organization_id = %s AND id = %s AND execution_state = 'queued'",
            (self._org, ulid_to_uuid(run_id)))

    def finish_run(self, run_id: str, completeness: str,
                   incomplete_reason: str | None = None) -> None:
        if completeness != "complete" and not incomplete_reason:
            raise ValueError(
                "a run that did not complete must record why; the schema refuses "
                "the row otherwise, and a caller is entitled to the reason")
        self._conn.execute(
            "UPDATE clep.run SET execution_state = 'terminal', completeness = %s, "
            "incomplete_reason = %s, completed_at = now() "
            "WHERE organization_id = %s AND id = %s",
            (completeness, incomplete_reason, self._org, ulid_to_uuid(run_id)))

    def get_run(self, run_id: str) -> RunRow | None:
        from clep.identity import uuid_to_ulid
        row = self._conn.execute(
            """
            SELECT id, project_id, execution_state, completeness, incomplete_reason,
                   reproducibility, identity_digest, integration_tier,
                   budget_limit, budget_currency, created_at, correlation_id,
                   dataset_version_id, suite_version_id, frozen_at
            FROM clep.run WHERE organization_id = %s AND id = %s
            """, (self._org, ulid_to_uuid(run_id))).fetchone()
        if not row:
            return None
        return RunRow(uuid_to_ulid(row[0]), uuid_to_ulid(row[1]), row[2], row[3],
                      row[4], row[5], row[6], row[7], row[8], row[9], row[10],
                      row[11], uuid_to_ulid(row[12]), uuid_to_ulid(row[13]), row[14])

    def adopt_correlation(self, run_id: str, correlation_id: str) -> str:
        """Give a run a correlation identifier if it does not already have one.

        A run created through the API carries the identifier its request
        produced. A run created by the scheduler has none, because no request
        made it — and a chain that starts at the first hop the platform happens
        to be looking at is not a chain. So execution adopts one, once, and
        writes it where it is durable.

        Conditional on NULL rather than unconditional: a redelivered job must
        resume the correlation the first delivery established, not replace it and
        orphan every record already written under the old one. The `IS NULL`
        predicate makes that a property of the statement rather than of the
        caller's memory, and returns whichever identifier is now in force.
        """
        row = self._conn.execute(
            "UPDATE clep.run SET correlation_id = COALESCE(correlation_id, %s) "
            "WHERE organization_id = %s AND id = %s RETURNING correlation_id",
            (correlation_id, self._org, ulid_to_uuid(run_id))).fetchone()
        return row[0] if row else correlation_id

    # ---------------------------------------------------------------- samples
    def record_sample(self, *, run_id: str, candidate_id: str, candidate_label: str,
                      example_id: str, sample_index: int, resolution: str,
                      score: Decimal | None = None, failure_kind: str | None = None,
                      example_content_digest: str | None = None,
                      served_from_cache: bool = False,
                      trajectory_truncated: bool = False,
                      model_latency_ms: int | None = None) -> tuple[str, bool]:
        """Returns (sample ULID, was_inserted).

        `model_latency_ms` obeys the same rule as `trajectory_truncated`: it is
        known when the sample is written, so it is written then. `REQ-F-11-3`
        needs it per sample rather than per run, because a distribution cannot
        be recovered from a mean.

        `trajectory_truncated` is written here rather than patched afterwards.
        A resolved sample is immutable (I-18) and the runtime role has no UPDATE
        grant on it — the store refused the later UPDATE the first version of
        this tried, which was correct: whether a trajectory was cut is known
        when the sample is written, and a fact known at insert has no business
        arriving as an amendment.

        `was_inserted` is False when this exact work unit had already been
        recorded — a redelivery. The caller uses it to avoid re-billing, and the
        unique constraint guarantees it even if the caller forgets.
        """
        from clep.identity import uuid_to_ulid
        key = sample_key(run_id, candidate_label, example_id)
        sample_ulid = new_ulid()
        row = self._conn.execute(
            """
            INSERT INTO clep.run_sample
                (id, organization_id, run_id, run_candidate_id, example_id,
                 sample_index, example_content_digest, resolution, score,
                 is_served_from_cache, failure_kind, idempotency_key,
                 trajectory_truncated, model_latency_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            (ulid_to_uuid(sample_ulid), self._org, ulid_to_uuid(run_id),
             ulid_to_uuid(candidate_id), ulid_to_uuid(example_id), sample_index,
             example_content_digest, resolution, score, served_from_cache,
             failure_kind, key, trajectory_truncated, model_latency_ms)).fetchone()
        if row:
            return sample_ulid, True
        existing = self._conn.execute(
            "SELECT id FROM clep.run_sample WHERE organization_id = %s "
            "AND idempotency_key = %s", (self._org, key)).fetchone()
        return uuid_to_ulid(existing[0]), False

    def record_cost(self, *, run_id: str, sample_id: str, sample_key_value: str,
                    prompt_tokens: int, completion_tokens: int,
                    amount: Decimal, currency: str) -> bool:
        """Returns True when this call created the entry. The second attempt on
        the same work unit returns False and changes nothing."""
        row = self._conn.execute(
            """
            INSERT INTO clep.sample_cost
                (id, organization_id, run_id, run_sample_id, prompt_tokens,
                 completion_tokens, cost_amount, cost_currency, idempotency_key)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            (ulid_to_uuid(new_ulid()), self._org, ulid_to_uuid(run_id),
             ulid_to_uuid(sample_id), prompt_tokens, completion_tokens, amount,
             currency, cost_key(sample_key_value))).fetchone()
        return row is not None

    def record_evaluator_outcome(self, *, sample_id: str, evaluator_version_id: str,
                                 resolution: str, score: Decimal | None,
                                 unavailable_reason: str | None, duration_ms: int) -> None:
        self._conn.execute(
            """
            INSERT INTO clep.evaluator_outcome
                (id, organization_id, run_sample_id, evaluator_version_id,
                 resolution, score, unavailable_reason, duration_ms)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (organization_id, run_sample_id, evaluator_version_id)
            DO NOTHING
            """,
            (ulid_to_uuid(new_ulid()), self._org, ulid_to_uuid(sample_id),
             ulid_to_uuid(evaluator_version_id), resolution, score,
             unavailable_reason, duration_ms))

    def record_evaluator_invocation(self, *, sample_id: str,
                                    evaluator_version_id: str,
                                    granted_permissions: str, outcome: str,
                                    correlation_id: str) -> None:
        """ADR-006 rule 6 and `REQ-F-12-9`: every invocation of untrusted code,
        with the version that ran and the boundary it ran inside.

        Separate from `record_evaluator_outcome` although both describe the same
        invocation, and the split is the point: the outcome is a *score* and
        lives in the evaluation domain; this is a *governance record* and is
        append-only, immutable by trigger, and answers a different question —
        not what did it say, but what was it allowed to do.
        """
        self._conn.execute(
            """
            INSERT INTO clep.evaluator_invocation
                (id, organization_id, run_sample_id, evaluator_version_id,
                 granted_permissions, outcome, correlation_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (ulid_to_uuid(new_ulid()), self._org, ulid_to_uuid(sample_id),
             ulid_to_uuid(evaluator_version_id), granted_permissions, outcome,
             correlation_id))

    # ------------------------------------------------------------- checkpoint
    def checkpoint(self, run_id: str) -> int:
        row = self._conn.execute(
            "SELECT last_completed_index FROM clep.run_checkpoint "
            "WHERE organization_id = %s AND run_id = %s",
            (self._org, ulid_to_uuid(run_id))).fetchone()
        return row[0] if row else -1

    def advance_checkpoint(self, run_id: str, index: int) -> None:
        """Monotonic. A redelivered job that has already been overtaken must not
        drag the marker backwards and cause completed work to be redone."""
        self._conn.execute(
            """
            UPDATE clep.run_checkpoint
               SET last_completed_index = GREATEST(last_completed_index, %s),
                   attempt_count = attempt_count + 1,
                   updated_at = now()
             WHERE organization_id = %s AND run_id = %s
            """, (index, self._org, ulid_to_uuid(run_id)))

    # --------------------------------------------------------------- readings
    def cost_total(self, run_id: str) -> tuple[Decimal, str | None]:
        row = self._conn.execute(
            "SELECT coalesce(sum(cost_amount), 0), min(cost_currency) "
            "FROM clep.sample_cost WHERE organization_id = %s AND run_id = %s",
            (self._org, ulid_to_uuid(run_id))).fetchone()
        return row[0], row[1]

    def sample_counts(self, run_id: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT resolution, count(*) FROM clep.run_sample "
            "WHERE organization_id = %s AND run_id = %s GROUP BY resolution",
            (self._org, ulid_to_uuid(run_id))).fetchall()
        return {r[0]: r[1] for r in rows}

    def list_samples(self, run_id: str, limit: int = 100, offset: int = 0) -> list[dict]:
        from clep.identity import uuid_to_ulid
        rows = self._conn.execute(
            """
            SELECT id, example_id, example_content_digest, resolution, score,
                   is_served_from_cache, failure_kind, sample_index
            FROM clep.run_sample
            WHERE organization_id = %s AND run_id = %s
            ORDER BY sample_index, id
            LIMIT %s OFFSET %s
            """, (self._org, ulid_to_uuid(run_id), limit, offset)).fetchall()
        return [{"id": uuid_to_ulid(r[0]), "exampleId": uuid_to_ulid(r[1]),
                 "exampleContentDigest": r[2], "resolution": r[3],
                 "score": None if r[4] is None else str(r[4]),
                 "servedFromCache": r[5], "failureKind": r[6],
                 "sampleIndex": r[7]} for r in rows]
