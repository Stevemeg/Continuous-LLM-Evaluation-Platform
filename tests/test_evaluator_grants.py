"""The evaluator permission boundary (ADR-006 rules 3, 5, 6; REQ-F-12-9).

What is asserted here is that an evaluator declaring a capability nobody granted
is **not run** — not that it runs and fails, and not that a comment says it will
not. The plugin used below records whether it was called, so the refusal is
observed rather than inferred from an outcome.

What is deliberately NOT asserted: that a granted evaluator is contained. ADR-006
rule 4 requires isolation outside the evaluator's own process boundary and that
mechanism is Phase 14's. Nothing here should be read as a sandbox.
"""
from __future__ import annotations

import pytest

from clep.evaluators.sdk import (EvaluatorRegistry, SampleContext, abstained,
                                 run_evaluator, scored)
from clep.security.grants import (CAPABILITIES, DENY_ALL, Grant, GrantError,
                                  grant_for, parse_declared)

SAMPLE = SampleContext(example_id="x", prompt="p", output="o",
                       integration_tier="full")


class Reaching:
    """A plugin that says it needs the network. Records whether it was run."""
    name = "reaching"
    version = "1"
    requires_tier = "output_only"
    requires_capabilities = ("network",)

    def __init__(self):
        self.called = 0

    def evaluate(self, sample):
        self.called += 1
        return scored("1")


class SelfContained:
    name = "self_contained"
    version = "1"
    requires_tier = "output_only"

    def __init__(self):
        self.called = 0

    def evaluate(self, sample):
        self.called += 1
        return scored("1")


def _register(evaluator):
    registry = EvaluatorRegistry()
    return registry.register(evaluator)


# ------------------------------------------------------------------- grants
def test_the_default_grant_permits_nothing():
    for capability in CAPABILITIES:
        assert not DENY_ALL.permits(capability)


def test_a_grant_cannot_name_a_capability_the_platform_cannot_enforce():
    """Silently dropping it would leave the evaluator believing it has one."""
    with pytest.raises(GrantError):
        Grant(organization_id="o", capabilities=frozenset({"telepathy"}))
    with pytest.raises(GrantError):
        DENY_ALL.permits("telepathy")


def test_a_grant_names_exactly_one_tenant():
    """ADR-006 rule 5: cross-tenant reach is not expressible in the interface.
    There is one field and it holds one value."""
    grant = grant_for("org-a", ["network"])
    assert grant.organization_id == "org-a"
    assert not hasattr(grant, "organizations")


def test_what_is_recorded_is_the_grant_and_not_the_declaration():
    assert grant_for("o", ["network", "filesystem"]).recorded == \
        "filesystem,network"
    assert DENY_ALL.recorded == "none"


@pytest.mark.parametrize("declared,expected", [
    (None, ()), ("", ()), ("none", ()), ("-", ()),
    ("network", ("network",)),
    ("network, filesystem", ("filesystem", "network")),
    ("network,network", ("network",)),
])
def test_the_declaration_column_is_read_the_way_it_was_written(declared,
                                                               expected):
    """`evaluator_version.declared_permissions` has existed since Phase 4 and
    has been written and never read. Both spellings of "nothing" must mean
    nothing, because the alternative reading is everything."""
    assert parse_declared(declared) == expected


def test_a_declaration_the_platform_cannot_enforce_is_refused():
    with pytest.raises(GrantError, match="cannot enforce"):
        parse_declared("network, mind_reading")


# -------------------------------------------------------------- enforcement
def test_an_evaluator_declaring_a_capability_is_not_run_without_a_grant():
    plugin = Reaching()
    registration = _register(plugin)
    outcome = run_evaluator(registration, SAMPLE)
    assert outcome.resolution == "unavailable"
    assert "was not granted" in outcome.unavailable_reason
    assert plugin.called == 0, \
        "the plugin ran; a boundary checked after the code executes is not one"


def test_the_same_evaluator_runs_once_the_capability_is_granted():
    plugin = Reaching()
    registration = _register(plugin)
    outcome = run_evaluator(registration, SAMPLE,
                            grant=grant_for("o", ["network"]))
    assert outcome.resolution == "scored"
    assert plugin.called == 1


def test_a_narrower_grant_than_the_declaration_still_refuses():
    plugin = Reaching()
    registration = _register(plugin)
    outcome = run_evaluator(registration, SAMPLE,
                            grant=grant_for("o", ["filesystem"]))
    assert outcome.resolution == "unavailable"
    assert plugin.called == 0


def test_an_evaluator_that_reaches_for_nothing_is_unaffected():
    """Deny-by-default costs the built-ins nothing, which is why it could be
    turned on for every existing evaluator without a single exemption."""
    plugin = SelfContained()
    outcome = run_evaluator(_register(plugin), SAMPLE)
    assert outcome.resolution == "scored"
    assert plugin.called == 1


def test_the_refusal_is_reported_as_unavailable_and_never_as_a_score():
    """`REQ-X-2`. A refused evaluator has produced no evidence, and a zero would
    be evidence of a bad answer."""
    outcome = run_evaluator(_register(Reaching()), SAMPLE)
    assert outcome.score is None


def test_a_declaration_is_validated_when_the_evaluator_is_registered():
    """Refusing at invocation would mean it was already part of a suite version
    somebody depends on."""
    class Impossible:
        name = "impossible"
        version = "1"
        requires_tier = "output_only"
        requires_capabilities = ("wishful_thinking",)

        def evaluate(self, sample):
            return abstained("never reached")

    with pytest.raises(GrantError):
        EvaluatorRegistry().register(Impossible())
