"""One writer for audit events.

Phase 7 began with a second copy of this insert in the gate service, and the copy
named columns that do not exist — `subject_kind` and `subject_id` rather than
`target_type` and `target_id`. Every gate call failed at once, which was lucky:
the same divergence in a rarely-taken branch would have meant a governed action
completing with no audit row, and I-35 says an unaudited action must not be
possible.

So there is one function. A column rename now breaks every caller at once instead
of half of them.
"""
from __future__ import annotations

import uuid

from clep.identity import actor_uuid, ulid_to_uuid


def record(conn, organization_id: str, actor_id: str, action: str,
           target_type: str, target_id: str) -> None:
    """Written inside the caller's transaction, never afterwards.

    An audit trail written after the fact is an audit trail missing exactly the
    events that failed. The runtime role has INSERT and no DELETE (I-33), so an
    actor cannot remove the record of their own change.
    """
    conn.execute(
        "INSERT INTO clep.audit_event (id, organization_id, actor_id, action, "
        "target_type, target_id) VALUES (%s, %s, %s, %s, %s, %s)",
        (uuid.uuid4(), str(organization_id), actor_uuid(actor_id), action,
         target_type, ulid_to_uuid(target_id)))
