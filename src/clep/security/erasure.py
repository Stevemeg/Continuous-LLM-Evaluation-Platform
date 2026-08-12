"""Erasure: destroy the content, keep the history, say what was lost.

`REQ-F-05-8` resolves a genuine conflict — immutability of released snapshots
against an enforceable right to erasure — as a product behaviour: content is
removed, history is preserved, and affected runs are demoted from *reproducible*
to *auditable* rather than silently altered. This module is that behaviour.

Three things make it more than a DELETE.

**Order.** Demote before destroy, as `04-artifacts-and-audit.sql` already states
in the column comment. Destroying first leaves a window in which a run still
claims reproducibility whose content is already gone — and a run that claims to
be replayable and is not is worse than one that admits it is not.

**Reach.** `REQ-N-PRIV-4` extends deletion to derived artifacts, not only to the
dataset record. Every content-derived artifact names the example content it
derives from (rule A-4), so this is an indexed lookup rather than a scan — which
is the whole reason that column exists. `gate_evidence` is excluded because the
schema makes it structurally free of erasable content: it is audit class,
permanent under `REQ-N-COMP-1`, and a CHECK constraint already makes the
contradiction unrepresentable.

**Verification.** `ck_erasure_request__verified_on_completion` refuses to record
completion unless every targeted object was confirmed destroyed. Reporting
completion without verification would tell a data subject their content was
removed when it may not have been.

**What this does not do, and must not be read as doing.** The platform holds a
`payload_ref` and a digest; the bytes live in an object store that D-3 records
as having no adapter. Erasure here destroys every record the platform holds and
verifies their absence. It does not, and cannot yet, confirm the object store
destroyed the object — that confirmation arrives with the adapter D-3 owns. The
state machine below therefore verifies what it can observe and says so, rather
than reporting a completion it cannot see.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg

from clep.api import audit
from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid


class ErasureError(RuntimeError):
    pass


class BaselinePinned(ErasureError):
    """`REQ-F-05-9`: an active baseline pins the target.

    Not an error to be worked around — a 409 with a stated remedy. The override
    exists and is audited, which is what `REQ-F-05-9` asks for: an explicit,
    audited override rather than either a silent deletion or a hard refusal.
    """


@dataclass(frozen=True)
class ErasureOutcome:
    id: str
    demoted_run_ids: tuple[str, ...]
    content_destroyed: int
    artifacts_destroyed: int
    verified: int
    target_count: int
    state: str

    def as_contract(self) -> dict:
        """`ErasureAcceptance`. The demoted runs are named because the loss of
        replay fidelity has to be visible rather than silent."""
        return {"id": self.id, "demotedRunIds": list(self.demoted_run_ids)}


def request_erasure(conn: psycopg.Connection, organization_id: str, *,
                    digests: list[str], justification: str, actor_id: str,
                    override_baseline_pin: bool = False) -> ErasureOutcome:
    """Executed in the caller's transaction, in the order the schema fixes."""
    if len(justification.strip()) < 10:
        raise ErasureError(
            "an erasure needs a justification; a deletion nobody has to explain "
            "is a deletion an auditor cannot evaluate")
    org = str(organization_id)
    targets = conn.execute(
        "SELECT content_digest FROM clep.example_content "
        " WHERE organization_id = %s AND content_digest = ANY(%s) "
        "   AND erased_at IS NULL", (org, list(digests))).fetchall()
    live = [r[0] for r in targets]
    if not live:
        raise ErasureError(
            "no live content matches those digests in this organization; "
            "nothing was erased")

    pinned = _baselines_pinning(conn, org, live)
    if pinned and not override_baseline_pin:
        raise BaselinePinned(
            f"{len(pinned)} approved baseline(s) pin a dataset version "
            f"containing this content. Erasing it destroys the evidence those "
            f"baselines rest on, so it requires an explicit override, which is "
            f"audited: {pinned[:3]}")

    erasure_id = new_ulid()
    # The audit record comes first and the request references it: the schema
    # makes `audit_event_id` NOT NULL precisely so that an erasure with no
    # authorising record is not storable (REQ-N-PRIV-3).
    audit_id = audit.record(
        conn, org, actor_id, "content.erasure_requested", "erasure_request",
        erasure_id, justification=justification, returning=True)

    artifact_count = conn.execute(
        "SELECT count(*) FROM clep.artifact "
        " WHERE organization_id = %s AND source_content_digest = ANY(%s) "
        "   AND artifact_class <> 'gate_evidence' AND erased_at IS NULL",
        (org, live)).fetchone()[0]
    target_count = len(live) + artifact_count

    conn.execute(
        "INSERT INTO clep.erasure_request (id, organization_id, "
        "requested_by_actor_id, justification, state, is_override_used, "
        "target_count, audit_event_id) "
        "VALUES (%s, %s, %s, %s, 'accepted', %s, %s, %s)",
        (ulid_to_uuid(erasure_id), org, _actor_uuid(actor_id), justification,
         bool(pinned and override_baseline_pin), target_count, audit_id))

    # ---- demote, then destroy. Never the other way round.
    _advance(conn, org, erasure_id, "demoting")
    demoted = [uuid_to_ulid(r[0]) for r in conn.execute(
        "UPDATE clep.run SET reproducibility = 'auditable' "
        " WHERE organization_id = %s AND reproducibility = 'reproducible' "
        "   AND id IN (SELECT run_id FROM clep.run_sample "
        "               WHERE organization_id = %s "
        "                 AND example_content_digest = ANY(%s)) "
        "RETURNING id", (org, org, live)).fetchall()]

    _advance(conn, org, erasure_id, "destroying")
    content_destroyed = len(conn.execute(
        "UPDATE clep.example_content SET payload_ref = NULL, "
        "       erased_at = now(), erasure_audit_id = %s "
        " WHERE organization_id = %s AND content_digest = ANY(%s) "
        "   AND erased_at IS NULL RETURNING id",
        (audit_id, org, live)).fetchall())
    artifacts_destroyed = len(conn.execute(
        "UPDATE clep.artifact SET payload_ref = NULL, erased_at = now(), "
        "       erasure_audit_id = %s "
        " WHERE organization_id = %s AND source_content_digest = ANY(%s) "
        "   AND artifact_class <> 'gate_evidence' AND erased_at IS NULL "
        "RETURNING id", (audit_id, org, live)).fetchall())

    # ---- verify by looking, not by trusting the update counts
    _advance(conn, org, erasure_id, "verifying")
    surviving_content = conn.execute(
        "SELECT count(*) FROM clep.example_content "
        " WHERE organization_id = %s AND content_digest = ANY(%s) "
        "   AND payload_ref IS NOT NULL", (org, live)).fetchone()[0]
    surviving_artifacts = conn.execute(
        "SELECT count(*) FROM clep.artifact "
        " WHERE organization_id = %s AND source_content_digest = ANY(%s) "
        "   AND artifact_class <> 'gate_evidence' AND payload_ref IS NOT NULL",
        (org, live)).fetchone()[0]
    verified = target_count - surviving_content - surviving_artifacts

    state = "completed" if verified == target_count else "failed"
    conn.execute(
        "UPDATE clep.erasure_request SET state = %s, verified_count = %s, "
        "       completed_at = now() WHERE organization_id = %s AND id = %s",
        (state, verified, org, ulid_to_uuid(erasure_id)))
    audit.record(conn, org, actor_id, f"content.erasure_{state}",
                 "erasure_request", erasure_id,
                 justification=f"{verified} of {target_count} objects verified "
                               f"destroyed")
    return ErasureOutcome(id=erasure_id, demoted_run_ids=tuple(demoted),
                          content_destroyed=content_destroyed,
                          artifacts_destroyed=artifacts_destroyed,
                          verified=verified, target_count=target_count,
                          state=state)


def _advance(conn, org: str, erasure_id: str, state: str) -> None:
    """Each stage is written before it runs.

    So that a failure mid-erasure leaves a record of how far it got. An erasure
    that crashed between demoting and destroying is a recoverable situation; one
    that crashed with no state at all is an investigation.
    """
    conn.execute(
        "UPDATE clep.erasure_request SET state = %s "
        " WHERE organization_id = %s AND id = %s",
        (state, org, ulid_to_uuid(erasure_id)))


def _baselines_pinning(conn, org: str, digests: list[str]) -> list[str]:
    """`REQ-F-05-9`, read through the join the data model provides.

    A baseline pins a run; a run names a dataset version; the content belongs to
    an example in that version. The question "does an active baseline depend on
    this content" is therefore answerable without scanning.
    """
    rows = conn.execute(
        "SELECT DISTINCT b.id FROM clep.baseline b "
        "  JOIN clep.run r ON r.organization_id = b.organization_id "
        "                 AND r.id = b.run_id "
        " WHERE b.organization_id = %s AND b.state = 'approved' "
        "   AND r.dataset_version_id IN ("
        "        SELECT e.dataset_version_id FROM clep.example e "
        "          JOIN clep.example_content c "
        "            ON c.organization_id = e.organization_id "
        "           AND c.example_id = e.id "
        "         WHERE e.organization_id = %s AND c.content_digest = ANY(%s))",
        (org, org, list(digests))).fetchall()
    return [uuid_to_ulid(r[0]) for r in rows]


def _actor_uuid(actor_id: str) -> uuid.UUID:
    from clep.identity import actor_uuid
    return actor_uuid(actor_id)
