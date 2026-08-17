"""One writer for audit events.

Phase 7 began with a second copy of this insert in the gate service, and the copy
named columns that do not exist — `subject_kind` and `subject_id` rather than
`target_type` and `target_id`. Every gate call failed at once, which was lucky:
the same divergence in a rarely-taken branch would have meant a governed action
completing with no audit row, and I-35 says an unaudited action must not be
possible.

So there is one function. A column rename now breaks every caller at once instead
of half of them.

Phase 12 added the two columns the schema had carried since Phase 4 and nothing
had ever written: `justification`, which `REQ-F-12-4` requires wherever an action
demands one, and `target_content_digest`, which is what lets an auditor ask
`REQ-N-COMP-1`'s question — *which version of the thing was approved* — rather
than only *which thing*.
"""
from __future__ import annotations

import uuid

from clep.identity import actor_uuid, is_ulid, new_ulid, ulid_to_uuid
from clep.telemetry.correlation import current_id


def record(conn, organization_id: str, actor_id: str, action: str,
           target_type: str, target_id: str | None,
           justification: str | None = None,
           target_content_digest: str | None = None,
           returning: bool = False) -> uuid.UUID | None:
    """Written inside the caller's transaction, never afterwards.

    An audit trail written after the fact is an audit trail missing exactly the
    events that failed. The runtime role has INSERT and no DELETE (I-33), so an
    actor cannot remove the record of their own change.

    `target_id` may be absent. Some governed events have no row to point at — a
    refused request names a route, not an object — and the schema makes the
    column nullable for exactly that case. `target_type` never is: an event with
    no idea what it was about is not a record.
    """
    # A ULID rather than a random uuid4. The contract types `AuditEvent.id` as a
    # `Ulid`, and a uuid4 rendered in that alphabet is a well-formed ULID that
    # sorts arbitrarily — which is invisible until something pages through the
    # trail by id and returns events in an order nobody chose. Phase 12 added
    # that paging, and this is what makes the cursor mean "everything before
    # this event" rather than "everything with a smaller random number".
    event_id = ulid_to_uuid(new_ulid())
    # The audit trail is the last hop of the correlation chain and the only one
    # with no foreign key back to a run — an event may be about a route or a
    # principal rather than an object. Taken from the ambient scope rather than
    # passed by every caller, because a parameter thirty call sites have to
    # remember is a parameter some of them will not (I-35).
    #
    # None outside a request, and left None: a scheduler waking up genuinely has
    # no correlation, and inventing one here would produce an identifier that
    # correlates nothing while looking exactly like one that does.
    conn.execute(
        "INSERT INTO clep.audit_event (id, organization_id, actor_id, action, "
        "target_type, target_id, justification, target_content_digest, "
        "correlation_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (event_id, str(organization_id), actor_uuid(actor_id), action,
         target_type,
         ulid_to_uuid(target_id) if target_id and is_ulid(target_id) else None,
         justification, target_content_digest, current_id()))
    return event_id if returning else None
