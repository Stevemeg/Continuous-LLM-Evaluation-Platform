"""Recovering one request's chain, months later, from the durable record.

`REQ-N-OBS-1` requires a single request to be correlatable through workflow,
model call, evaluator, judge, artifact and gate decision. `observability-strategy.md`
§2 adds the condition that makes it hard: **the chain is queryable after the
fact, not only live** — an auditor asks months later, and a tracing backend with
a fourteen-day retention window cannot answer.

So the chain is read from the operational store, not from telemetry. That is
ADR-023 rule 6 in its other application: an availability figure that disagrees
with the audit trail is worse than no figure, and a chain that disagrees with the
run record is worse than no chain.

Nothing here writes. Every query runs under the caller's tenant session, so
row-level security does the isolation — one tenant cannot read another tenant's
chain by knowing its identifier, which matters precisely because the identifier
is returned to clients in a response header.

**The artifact hop is absent, and is reported as absent.** `clep.artifact` has no
writer anywhere in `src/clep`, and cannot get one here: a non-erased artifact
must carry a `payload_ref` under `ck_artifact__erasure_consistent`, and that is
an object-store reference the store adapter is `D-3`, owned by Phase 14. The
query looks for artifacts and reports what it finds, which is nothing, rather
than omitting the hop and letting a complete-looking result imply a complete
chain.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from clep.identity import ulid_to_uuid, uuid_to_ulid


@dataclass(frozen=True)
class Hop:
    """One link, named as `observability-strategy.md` §2 names it."""
    name: str
    #: How the identifier reaches this hop: stored on the row, or reached from a
    #: row that stores it. Recorded so a reader can tell a correlation from a
    #: coincidence.
    reached_by: str
    rows: tuple = ()

    @property
    def present(self) -> bool:
        return bool(self.rows)


@dataclass(frozen=True)
class Chain:
    correlation_id: str
    hops: tuple[Hop, ...] = field(default_factory=tuple)

    @property
    def present_hops(self) -> tuple[str, ...]:
        return tuple(h.name for h in self.hops if h.present)

    @property
    def absent_hops(self) -> tuple[str, ...]:
        return tuple(h.name for h in self.hops if not h.present)

    def __bool__(self) -> bool:
        return bool(self.present_hops)


class CorrelationChainRepository:
    def __init__(self, conn, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    def chain_for(self, correlation_id: str) -> Chain:
        """Every hop reachable from one correlation identifier.

        Two hops store the identifier directly — the run, because it is the root,
        and the audit event, because nothing links it back. The rest are reached
        by the foreign keys that already exist, which is why the schema needed
        one column rather than six.
        """
        runs = self._runs(correlation_id)
        run_ids = [r["id"] for r in runs]
        return Chain(correlation_id=correlation_id, hops=(
            Hop("run", "stored on clep.run.correlation_id", runs),
            Hop("work_unit", "clep.run_sample.run_id", self._samples(run_ids)),
            Hop("model_call", "clep.sample_cost.run_id and "
                              "clep.run_sample.model_latency_ms",
                self._model_calls(run_ids)),
            Hop("evaluator_invocation",
                "stored on clep.evaluator_invocation.correlation_id",
                self._evaluator_invocations(correlation_id)),
            Hop("judge_invocation", "clep.judge_run.run_sample_id",
                self._judge_runs(run_ids)),
            Hop("artifact", "clep.artifact.correlation_id — no writer exists; "
                            "the artifact store is D-3, owned by Phase 14",
                self._artifacts(correlation_id)),
            Hop("gate_decision", "clep.gate_decision.candidate_run_id",
                self._gate_decisions(run_ids)),
            Hop("audit_event", "stored on clep.audit_event.correlation_id",
                self._audit_events(correlation_id)),
        ))

    # ----------------------------------------------------------------- hops
    def _runs(self, correlation_id: str) -> tuple:
        rows = self._conn.execute(
            "SELECT id, execution_state, completeness FROM clep.run "
            "WHERE organization_id = %s AND correlation_id = %s ORDER BY id",
            (self._org, correlation_id)).fetchall()
        return tuple({"id": uuid_to_ulid(r[0]), "execution_state": r[1],
                      "completeness": r[2]} for r in rows)

    def _samples(self, run_ids: list[str]) -> tuple:
        if not run_ids:
            return ()
        rows = self._conn.execute(
            "SELECT id, run_candidate_id, resolution FROM clep.run_sample "
            "WHERE organization_id = %s AND run_id = ANY(%s::uuid[]) "
            "ORDER BY sample_index",
            (self._org, [self._uuid(i) for i in run_ids])).fetchall()
        return tuple({"id": uuid_to_ulid(r[0]),
                      "run_candidate_id": uuid_to_ulid(r[1]),
                      "resolution": r[2]} for r in rows)

    def _model_calls(self, run_ids: list[str]) -> tuple:
        if not run_ids:
            return ()
        rows = self._conn.execute(
            "SELECT rs.id, rs.model_latency_ms, sc.prompt_tokens, "
            "       sc.completion_tokens, sc.cost_amount, sc.cost_currency "
            "FROM clep.run_sample rs "
            "LEFT JOIN clep.sample_cost sc "
            "  ON sc.organization_id = rs.organization_id "
            " AND sc.run_sample_id = rs.id "
            "WHERE rs.organization_id = %s AND rs.run_id = ANY(%s::uuid[]) "
            "  AND (rs.model_latency_ms IS NOT NULL "
            "       OR sc.run_sample_id IS NOT NULL) "
            "ORDER BY rs.sample_index",
            (self._org, [self._uuid(i) for i in run_ids])).fetchall()
        return tuple({"sample_id": uuid_to_ulid(r[0]), "latency_ms": r[1],
                      "prompt_tokens": r[2], "completion_tokens": r[3],
                      "cost": r[4], "currency": r[5]} for r in rows)

    def _evaluator_invocations(self, correlation_id: str) -> tuple:
        rows = self._conn.execute(
            "SELECT id, outcome, granted_permissions FROM clep.evaluator_invocation "
            "WHERE organization_id = %s AND correlation_id = %s ORDER BY invoked_at",
            (self._org, correlation_id)).fetchall()
        return tuple({"id": uuid_to_ulid(r[0]), "outcome": r[1],
                      "granted_permissions": r[2]} for r in rows)

    def _judge_runs(self, run_ids: list[str]) -> tuple:
        if not run_ids:
            return ()
        rows = self._conn.execute(
            "SELECT jr.id, jr.resolution FROM clep.judge_run jr "
            "JOIN clep.run_sample rs "
            "  ON rs.organization_id = jr.organization_id AND rs.id = jr.run_sample_id "
            "WHERE jr.organization_id = %s AND rs.run_id = ANY(%s::uuid[]) "
            "ORDER BY jr.id",
            (self._org, [self._uuid(i) for i in run_ids])).fetchall()
        return tuple({"id": uuid_to_ulid(r[0]), "resolution": r[1]} for r in rows)

    def _artifacts(self, correlation_id: str) -> tuple:
        rows = self._conn.execute(
            "SELECT id, artifact_class FROM clep.artifact "
            "WHERE organization_id = %s AND correlation_id = %s ORDER BY id",
            (self._org, correlation_id)).fetchall()
        return tuple({"id": uuid_to_ulid(r[0]), "class": r[1]} for r in rows)

    def _gate_decisions(self, run_ids: list[str]) -> tuple:
        if not run_ids:
            return ()
        rows = self._conn.execute(
            "SELECT id, evaluated_outcome FROM clep.gate_decision "
            "WHERE organization_id = %s AND candidate_run_id = ANY(%s::uuid[]) "
            "ORDER BY decided_at",
            (self._org, [self._uuid(i) for i in run_ids])).fetchall()
        return tuple({"id": uuid_to_ulid(r[0]), "outcome": r[1]} for r in rows)

    def _audit_events(self, correlation_id: str) -> tuple:
        rows = self._conn.execute(
            "SELECT id, action, target_type FROM clep.audit_event "
            "WHERE organization_id = %s AND correlation_id = %s ORDER BY id",
            (self._org, correlation_id)).fetchall()
        return tuple({"id": uuid_to_ulid(r[0]), "action": r[1],
                      "target_type": r[2]} for r in rows)

    @staticmethod
    def _uuid(ulid: str):
        return ulid_to_uuid(ulid)
