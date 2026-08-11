"""What the platform says about a system that is already live.

`REQ-F-10-2` asks for post-deployment and canary evaluation. `REQ-F-10-3` bounds
what may come of it: a recommendation, addressed to a person, and never an action.
The schema enforces the second — `release_observation` has no column naming a
target, an applied flag or a rollback timestamp — and this module is the part
that could still have got it wrong, so it is small and it decides nothing that is
not written down here.

The mapping from a gate outcome to a recommendation is a decision, made once, in
one table. It is deliberately not a judgement made per run:

    hard_fail             -> rollback       the gate blocked, and the system is live
    approval_required     -> hold_release   a person owes an answer before more ships
    insufficient_evidence -> investigate    nothing was measured; that is not "fine"
    not_comparable        -> investigate    the comparison was invalid, not favourable
    pass, warning         -> none           nothing needs doing
    exception_applied     -> none           a person already signed for it

`investigate` for both abstention outcomes is the same choice `exit_codes` made
for the pipeline: a gate that could not tell must not read as a gate that
approved. The rationale carries the outcome and the decision it came from, so a
reader can reach the evidence rather than take the word for it.
"""
from __future__ import annotations

from dataclasses import dataclass

import psycopg

from clep.identity import actor_uuid, new_ulid, ulid_to_uuid, uuid_to_ulid

ROLLBACK = "rollback"
INVESTIGATE = "investigate"
HOLD_RELEASE = "hold_release"
NONE = "none"
RECOMMENDATIONS = (ROLLBACK, INVESTIGATE, HOLD_RELEASE, NONE)

#: Every gate outcome the contract declares. Exhaustive on purpose: an outcome
#: added later and not mapped here reaches `recommendation_for` and is refused,
#: rather than defaulting to `none` and telling an operator that a state nobody
#: has thought about needs no attention.
BY_OUTCOME = {
    "hard_fail": ROLLBACK,
    "approval_required": HOLD_RELEASE,
    "insufficient_evidence": INVESTIGATE,
    "not_comparable": INVESTIGATE,
    "pass": NONE,
    "warning": NONE,
    "exception_applied": NONE,
}


class ReleaseObservationError(RuntimeError):
    pass


def recommendation_for(outcome: str) -> str:
    try:
        return BY_OUTCOME[outcome]
    except KeyError:
        raise ReleaseObservationError(
            f"gate outcome {outcome!r} has no recommendation; an unmapped "
            f"outcome must not default to 'none', which would tell an operator "
            f"that a state nobody has considered needs no attention") from None


@dataclass(frozen=True)
class ReleaseObservationRow:
    id: str
    project_id: str
    run_id: str
    gate_decision_id: str | None
    trigger_kind: str
    recommendation: str
    rationale: str
    observed_at: object


class ReleaseObservationRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    def record(self, *, project_id: str, run_id: str, trigger_kind: str,
               recommendation: str, rationale: str, observed_by: str,
               gate_decision_id: str | None = None) -> str:
        if trigger_kind not in ("post_deployment", "canary"):
            raise ReleaseObservationError(
                f"{trigger_kind!r} does not describe a system that is already "
                f"live; a manual or pull-request run is an evaluation before "
                f"release, not an observation of one after it")
        if recommendation not in RECOMMENDATIONS:
            raise ReleaseObservationError(
                f"{recommendation!r} is not a recommendation this platform makes")
        if not rationale.strip():
            raise ReleaseObservationError(
                "advice with no rationale cannot be acted on or argued with")
        observation_id = new_ulid()
        self._conn.execute(
            """
            INSERT INTO clep.release_observation
                (id, organization_id, project_id, run_id, gate_decision_id,
                 trigger_kind, recommendation, rationale, observed_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (ulid_to_uuid(observation_id), self._org, ulid_to_uuid(project_id),
             ulid_to_uuid(run_id),
             ulid_to_uuid(gate_decision_id) if gate_decision_id else None,
             trigger_kind, recommendation, rationale, actor_uuid(observed_by)))
        return observation_id

    def get(self, observation_id: str) -> ReleaseObservationRow | None:
        row = self._conn.execute(
            _SELECT + " WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(observation_id))).fetchone()
        return _hydrate(row) if row else None

    def for_run(self, run_id: str) -> ReleaseObservationRow | None:
        row = self._conn.execute(
            _SELECT + " WHERE organization_id = %s AND run_id = %s",
            (self._org, ulid_to_uuid(run_id))).fetchone()
        return _hydrate(row) if row else None

    def list_for_project(self, project_id: str,
                         limit: int = 50) -> list[ReleaseObservationRow]:
        rows = self._conn.execute(
            _SELECT + " WHERE organization_id = %s AND project_id = %s "
                      " ORDER BY observed_at DESC, id DESC LIMIT %s",
            (self._org, ulid_to_uuid(project_id), limit)).fetchall()
        return [_hydrate(r) for r in rows]


_SELECT = ("SELECT id, project_id, run_id, gate_decision_id, trigger_kind, "
           "       recommendation, rationale, observed_at "
           "FROM clep.release_observation")


def _hydrate(row) -> ReleaseObservationRow:
    return ReleaseObservationRow(
        id=uuid_to_ulid(row[0]), project_id=uuid_to_ulid(row[1]),
        run_id=uuid_to_ulid(row[2]),
        gate_decision_id=uuid_to_ulid(row[3]) if row[3] else None,
        trigger_kind=row[4], recommendation=row[5], rationale=row[6],
        observed_at=row[7])
