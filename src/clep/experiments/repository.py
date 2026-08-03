"""Persistence for run identity, reproduction attempts and their gaps.

`REQ-F-07-1` is satisfied by writing the components, not by writing a digest.
`REQ-F-07-3` is satisfied by being able to read them back and say which ones are
no longer there.
"""
from __future__ import annotations

import uuid

import psycopg

from clep.experiments.identity import Component, RunIdentity
from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid


class IdentityRepository:
    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    def capture(self, run_id: str, identity: RunIdentity) -> str:
        """Write every captured component and return the identity digest.

        `ON CONFLICT DO NOTHING` for the same reason it appears on samples: run
        submission is retryable, and a second capture of the same identity must
        converge rather than raise. The unique key is
        (organization, run, kind, ref), so a *different* digest for the same
        component is silently kept out — which is why `IdentityBuilder` refuses
        the contradiction before it ever reaches here.
        """
        for component in identity.components:
            self._conn.execute(
                "INSERT INTO clep.run_identity_component "
                "(id, organization_id, run_id, component_kind, component_ref, "
                "component_digest) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (organization_id, run_id, component_kind, "
                "component_ref) DO NOTHING",
                (ulid_to_uuid(new_ulid()), self._org, ulid_to_uuid(run_id),
                 component.kind, component.ref, component.digest))
        return identity.digest()

    def components_of(self, run_id: str) -> RunIdentity:
        rows = self._conn.execute(
            "SELECT component_kind, component_ref, component_digest "
            "FROM clep.run_identity_component "
            "WHERE organization_id = %s AND run_id = %s "
            "ORDER BY component_kind, component_ref",
            (self._org, ulid_to_uuid(run_id))).fetchall()
        return RunIdentity(components=tuple(
            Component(kind=r[0], ref=r[1], digest=r[2]) for r in rows))

    # ------------------------------------------------------------ reproduction
    def record_attempt(self, *, original_run_id: str, replay_run_id: str | None,
                       outcome: str, gaps: list[dict]) -> str:
        attempt_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.reproduction_attempt (id, organization_id, "
            "original_run_id, replay_run_id, outcome) VALUES (%s, %s, %s, %s, %s)",
            (ulid_to_uuid(attempt_id), self._org, ulid_to_uuid(original_run_id),
             ulid_to_uuid(replay_run_id) if replay_run_id else None, outcome))
        for gap in gaps:
            self._conn.execute(
                "INSERT INTO clep.reproduction_gap (id, organization_id, "
                "reproduction_attempt_id, component_kind, component_ref, reason) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (organization_id, reproduction_attempt_id, "
                "component_kind, component_ref) DO NOTHING",
                (uuid.uuid4(), self._org, ulid_to_uuid(attempt_id),
                 gap["componentKind"], gap["componentRef"], gap["reason"]))
        return attempt_id

    def gaps_of(self, attempt_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT component_kind, component_ref, reason "
            "FROM clep.reproduction_gap "
            "WHERE organization_id = %s AND reproduction_attempt_id = %s "
            "ORDER BY component_kind, component_ref",
            (self._org, ulid_to_uuid(attempt_id))).fetchall()
        return [{"componentKind": r[0], "componentRef": r[1], "reason": r[2]}
                for r in rows]

    def attempt(self, attempt_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, original_run_id, replay_run_id, outcome "
            "FROM clep.reproduction_attempt WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(attempt_id))).fetchone()
        if row is None:
            return None
        return {"id": uuid_to_ulid(row[0]),
                "originalRunId": uuid_to_ulid(row[1]),
                "replayRunId": uuid_to_ulid(row[2]) if row[2] else None,
                "outcome": row[3],
                "gaps": self.gaps_of(uuid_to_ulid(row[0]))}
