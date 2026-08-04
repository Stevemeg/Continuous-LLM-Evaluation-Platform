"""Identifiers at the contract boundary and in the store.

The OpenAPI contract specifies `Ulid` — 26 characters of Crockford base32, and
lexicographically sortable. The schema specifies `uuid` columns. Both describe
the same 128 bits, so this module is the single place where one becomes the
other, and the round trip is property-tested rather than assumed.

Writing that conversion in more than one place is how an identifier ends up
formatted two ways in two responses. Writing it in none is how a contract quietly
stops being true.
"""
from __future__ import annotations

import os
import time
import uuid

# Crockford base32, with I, L, O and U removed so that they cannot be confused
# with 1, 1, 0 and V when a human retypes an identifier from a log.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DECODE = {c: i for i, c in enumerate(_ALPHABET)}
ULID_LENGTH = 26


def new_ulid() -> str:
    """A fresh identifier: 48 bits of millisecond timestamp, 80 bits random.

    Time-ordered on purpose. Identifiers generated during one run land next to
    each other in an index, and a listing ordered by id is ordered by creation
    without a second column to sort on.
    """
    ms = int(time.time() * 1000) & ((1 << 48) - 1)
    value = (ms << 80) | int.from_bytes(os.urandom(10), "big")
    return _encode(value)


def _encode(value: int) -> str:
    out = []
    for _ in range(ULID_LENGTH):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def ulid_to_uuid(ulid: str) -> uuid.UUID:
    """Storage form. Raises on anything that is not a well-formed ULID."""
    return uuid.UUID(int=_decode(ulid))


def uuid_to_ulid(value: uuid.UUID | str) -> str:
    """Contract form."""
    if isinstance(value, str):
        value = uuid.UUID(value)
    return _encode(value.int)


def _decode(ulid: str) -> int:
    if not isinstance(ulid, str) or len(ulid) != ULID_LENGTH:
        raise ValueError(f"not a ULID: expected {ULID_LENGTH} characters")
    value = 0
    for ch in ulid:
        digit = _DECODE.get(ch.upper())
        if digit is None:
            raise ValueError(f"not a ULID: illegal character {ch!r}")
        value = (value << 5) | digit
    if value >= 1 << 128:
        # The first character can encode 5 bits but only 3 are addressable in
        # 128 bits, so 26 legal characters can still overflow. Rejecting this
        # matters: it is the one malformed ULID that decodes without complaint.
        raise ValueError("not a ULID: value exceeds 128 bits")
    return value


def is_ulid(value: str) -> bool:
    try:
        _decode(value)
        return True
    except (ValueError, TypeError):
        return False


#: Namespace for deriving a stable actor identifier from a non-UUID subject.
#: Until Phase 12 issues real principals, an actor arrives as whatever the
#: ingress presents. A stable derivation keeps the audit trail joinable; a random
#: identifier per request would satisfy the column and destroy the requirement.
ACTOR_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def actor_uuid(subject: str) -> uuid.UUID:
    """Lives here rather than in a service because two services need it.

    It was defined in the registry service and copied into the regression
    repository, which is how the two would have drifted into recording the same
    person under two identifiers.
    """
    try:
        return uuid.UUID(str(subject))
    except (ValueError, AttributeError, TypeError):
        return uuid.uuid5(ACTOR_NAMESPACE, str(subject))
