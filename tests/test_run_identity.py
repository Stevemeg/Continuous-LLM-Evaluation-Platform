"""Run identity: REQ-F-07-1, and the decision recorded in ADR-014.

The digest is the thing every comparability claim rests on, so these tests care
about two properties above all: the same measurement must always produce the same
digest, and any change to what is measured must change it. A digest that is
merely usually stable is not identity.
"""
from __future__ import annotations

import pytest

from clep.experiments.identity import (CAPTURED_KINDS, IDENTITY_KINDS,
                                       Component, IdentityBuilder, IdentityError,
                                       RunIdentity, digest_of)

D1 = digest_of("one")
D2 = digest_of("two")


def build(**kwargs) -> RunIdentity:
    builder = IdentityBuilder()
    builder.add("dataset_version", kwargs.get("dataset", "DS1"),
                kwargs.get("dataset_digest", D1))
    builder.add("suite_version", "SU1", D1)
    builder.add_literal("integration_tier", kwargs.get("tier", "output_only"))
    return builder.build()


# ------------------------------------------------------------------ stability
def test_the_same_measurement_always_produces_the_same_digest():
    assert build().digest() == build().digest()


def test_the_digest_does_not_depend_on_the_order_components_were_added():
    forward = IdentityBuilder()
    forward.add("dataset_version", "DS1", D1)
    forward.add("suite_version", "SU1", D2)
    backward = IdentityBuilder()
    backward.add("suite_version", "SU1", D2)
    backward.add("dataset_version", "DS1", D1)
    assert forward.build().digest() == backward.build().digest()


@pytest.mark.parametrize("change", [
    {"dataset": "DS2"},
    {"dataset_digest": D2},
    {"tier": "full"},
])
def test_any_change_to_what_is_measured_changes_the_digest(change):
    """Including a change to CONTENT under an unchanged identifier. Two runs
    naming the same dataset version measured the same thing only if that
    version's content is the same."""
    assert build().digest() != build(**change).digest()


def test_the_environment_is_captured_and_excluded_from_the_digest():
    """ADR-014. If the interpreter version entered the digest, two runs of the
    same measurement on two machines would have different identities and
    REQ-F-01-3 could never be satisfied across hosts."""
    without = build()
    with_environment = IdentityBuilder()
    for component in without.components:
        with_environment.add(component.kind, component.ref, component.digest)
    with_environment.add_environment()
    built = with_environment.build()

    assert built.digest() == without.digest()
    assert "environment" in built.kinds()
    assert len(built.components) == len(without.components) + 1
    assert "environment" not in {c.kind for c in built.identity_components}


def test_every_identity_kind_is_a_kind_the_schema_permits():
    """The CHECK constraint on run_identity_component is the other half of this
    list. A kind here that the schema rejects would fail on insert, after the
    digest had already been computed from it."""
    assert set(IDENTITY_KINDS) <= set(CAPTURED_KINDS)


# ----------------------------------------------------------------- refusals
def test_a_component_kind_the_schema_forbids_is_refused_at_construction():
    with pytest.raises(IdentityError, match="component kind"):
        Component(kind="phase_of_the_moon", ref="waxing", digest=D1)


def test_a_malformed_digest_is_refused():
    with pytest.raises(IdentityError, match="sha256"):
        Component(kind="dataset_version", ref="DS1", digest="deadbeef")


def test_an_empty_identity_has_no_digest_rather_than_a_constant_one():
    """A digest over nothing is the same value for every run, which would make
    every empty run "the same measurement" as every other."""
    with pytest.raises(IdentityError, match="at least one"):
        RunIdentity().digest()


def test_the_builder_refuses_a_contradiction_rather_than_keeping_the_last_write():
    builder = IdentityBuilder()
    builder.add("dataset_version", "DS1", D1)
    with pytest.raises(IdentityError, match="cannot hold both"):
        builder.add("dataset_version", "DS1", D2)


def test_adding_the_same_component_twice_is_accepted():
    builder = IdentityBuilder()
    builder.add("dataset_version", "DS1", D1)
    builder.add("dataset_version", "DS1", D1)
    assert len(builder.build().components) == 1


def test_require_names_what_is_missing():
    with pytest.raises(IdentityError, match="prompt_version"):
        build().require("dataset_version", "prompt_version")
