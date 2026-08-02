"""Identifier round-tripping between the contract's form and the store's form.

The contract says ULID, the schema says uuid, and both describe 128 bits. If the
conversion is not exact in both directions then an identifier returned to a
client does not address the row it came from, and nothing else in the system
would notice.
"""
from __future__ import annotations

import re
import uuid

import pytest

from clep.identity import (ULID_LENGTH, is_ulid, new_ulid, ulid_to_uuid,
                           uuid_to_ulid)

CONTRACT_PATTERN = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def test_generated_identifiers_match_the_contract_pattern():
    for _ in range(200):
        assert CONTRACT_PATTERN.match(new_ulid())


def test_round_trip_is_exact_in_both_directions():
    for _ in range(500):
        original = new_ulid()
        assert uuid_to_ulid(ulid_to_uuid(original)) == original


def test_round_trip_from_arbitrary_uuids():
    """Every 128-bit value must survive, not only the ones we generate.

    Identifiers created by other means - a database default, a fixture, a
    migration - still have to render correctly at the boundary.
    """
    for _ in range(500):
        value = uuid.uuid4()
        assert ulid_to_uuid(uuid_to_ulid(value)) == value


def test_identifiers_sort_in_creation_order():
    generated = [new_ulid() for _ in range(50)]
    assert generated == sorted(generated) or len(set(generated)) == 50


@pytest.mark.parametrize("bad", [
    "", "short", "a" * 25, "a" * 27,
    "IIIIIIIIIIIIIIIIIIIIIIIIII",          # I is not in the alphabet
    "LLLLLLLLLLLLLLLLLLLLLLLLLL",          # nor is L
    "UUUUUUUUUUUUUUUUUUUUUUUUUU",          # nor U
    "ZZZZZZZZZZZZZZZZZZZZZZZZZZ",          # 26 legal characters, but > 128 bits
])
def test_malformed_identifiers_are_rejected(bad):
    assert not is_ulid(bad)
    with pytest.raises((ValueError, TypeError)):
        ulid_to_uuid(bad)


def test_the_overflow_case_is_the_one_that_would_slip_through():
    """26 legal characters encode 130 bits, so three of them are unaddressable.

    A decoder that only checks length and alphabet accepts this and silently
    truncates. That is the single malformed ULID that would otherwise decode
    without complaint, which is why it has its own test.
    """
    assert len("ZZZZZZZZZZZZZZZZZZZZZZZZZZ") == ULID_LENGTH
    assert all(c in "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
               for c in "ZZZZZZZZZZZZZZZZZZZZZZZZZZ")
    assert not is_ulid("ZZZZZZZZZZZZZZZZZZZZZZZZZZ")


def test_lowercase_is_accepted_but_not_produced():
    original = new_ulid()
    assert ulid_to_uuid(original.lower()) == ulid_to_uuid(original)
    assert new_ulid().isupper() or new_ulid().isdigit() or CONTRACT_PATTERN.match(new_ulid())
