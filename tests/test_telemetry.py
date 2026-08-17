"""The telemetry port: correlation, and the cardinality refusal.

The tests that matter here are the negative ones. `REQ-N-OBS-4` is satisfied by
what the port *refuses*, and a test suite that only records legal samples would
pass against a port with no validation in it at all.
"""
from __future__ import annotations

import pytest

from clep.identity import is_ulid
from clep.telemetry import (CATALOGUE, METRIC_CLASSES, CardinalityError,
                            Correlation, MetricCatalogue, MetricSpec,
                            NullBackend, RecordingBackend, Telemetry,
                            correlated, current, current_id, new_correlation,
                            sanitize_inbound)


# ------------------------------------------------------------- correlation
def test_a_correlation_identifier_is_a_ulid_and_is_unique():
    a, b = new_correlation(), new_correlation()
    assert is_ulid(a.correlation_id) and is_ulid(b.correlation_id)
    assert a.correlation_id != b.correlation_id


def test_there_is_no_correlation_outside_a_scope():
    # None rather than a manufactured identifier: a chain with an invented hop
    # looks like a chain, and REQ-N-OBS-1 is the requirement that would defeat.
    assert current() is None and current_id() is None


def test_a_scope_restores_the_previous_one_on_the_way_out():
    with correlated() as outer:
        assert current_id() == outer.correlation_id
        with correlated() as inner:
            assert inner.correlation_id != outer.correlation_id
            assert current_id() == inner.correlation_id
        assert current_id() == outer.correlation_id
    assert current() is None


def test_a_worker_resumes_a_correlation_that_began_in_another_process():
    # The identifier crosses the boundary as data in a job payload; the context
    # is rebuilt from the string on the far side.
    with correlated("01ARZ3NDEKTSV4RRFFQ69G5FAV") as resumed:
        assert current_id() == "01ARZ3NDEKTSV4RRFFQ69G5FAV"
        assert resumed.inbound_reference is None


def test_a_caller_supplied_reference_is_recorded_and_never_adopted():
    c = new_correlation("client-abc-123")
    assert c.inbound_reference == "client-abc-123"
    assert c.correlation_id != "client-abc-123"
    assert is_ulid(c.correlation_id)


@pytest.mark.parametrize("hostile,expected", [
    ("ok-1\r\nlevel=ERROR forged=yes", "ok-1level=ERROR forged=yes"),
    ("\x00\x1b[31mred", "[31mred"),
    ("   ", None),
    ("", None),
    (None, None),
])
def test_an_inbound_reference_cannot_forge_a_log_line(hostile, expected):
    # A log line is terminated by a newline, so a header containing one is a
    # second log entry the client wrote. Stripped at the one place it enters.
    assert sanitize_inbound(hostile) == expected


def test_an_inbound_reference_is_length_bounded():
    assert len(sanitize_inbound("x" * 10_000)) == 200


def test_a_correlation_identifier_carries_no_tenant_identity():
    # Structural, not incidental: the identifier is generated from a clock and
    # os.urandom, and nothing tenant-scoped is in scope where it is made.
    org = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
    with correlated() as c:
        assert org not in c.correlation_id
        assert len(c.correlation_id) == 26


# ------------------------------------------------- the cardinality refusal
def _catalogue() -> MetricCatalogue:
    return MetricCatalogue((
        MetricSpec(name="m_test", kind="counter", metric_class="errors",
                   unit="1", description="test",
                   labels={"outcome": ("ok", "bad")}),))


def test_an_undeclared_label_is_refused():
    t = Telemetry(RecordingBackend(), catalogue=_catalogue())
    with pytest.raises(CardinalityError) as exc:
        t.observe("m_test", 1, outcome="ok", run_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
    assert "run_id" in str(exc.value)
    assert "never on a metric" in str(exc.value)


def test_a_missing_declared_label_is_refused():
    t = Telemetry(RecordingBackend(), catalogue=_catalogue())
    with pytest.raises(CardinalityError):
        t.observe("m_test", 1)


def test_a_value_outside_the_declared_enumeration_is_refused():
    t = Telemetry(RecordingBackend(), catalogue=_catalogue())
    with pytest.raises(CardinalityError):
        t.observe("m_test", 1, outcome="something-new")


def test_an_undeclared_metric_is_refused():
    t = Telemetry(RecordingBackend(), catalogue=_catalogue())
    with pytest.raises(CardinalityError):
        t.observe("m_never_declared", 1)


def test_a_label_declaring_no_values_is_refused_at_declaration():
    with pytest.raises(ValueError):
        MetricSpec(name="m", kind="counter", metric_class="errors", unit="1",
                   description="d", labels={"x": ()})


def test_a_metric_naming_a_class_outside_the_nine_is_refused():
    with pytest.raises(ValueError):
        MetricSpec(name="m", kind="counter", metric_class="vibes", unit="1",
                   description="d", labels={"x": ("a",)})


def test_cardinality_is_bounded_by_declaration_alone():
    # The property REQ-N-OBS-4 asks for: the largest series count this metric
    # can ever produce is computable without observing any traffic.
    spec = _catalogue().get("m_test")
    assert spec.max_series == 2


# ------------------------------------------------------ the nine classes
def test_the_catalogue_covers_all_nine_required_classes():
    assert CATALOGUE.missing_classes() == []
    assert set(CATALOGUE.classes()) == set(METRIC_CLASSES)
    assert len(METRIC_CLASSES) == 9


def test_no_declared_metric_can_carry_an_identifier_label():
    forbidden = {"organization_id", "org", "tenant", "tenant_id", "project_id",
                 "run_id", "sample_id", "candidate_id", "correlation_id",
                 "user", "principal_id", "evaluator_version_id"}
    for spec in CATALOGUE:
        assert not (set(spec.labels) & forbidden), spec.name


def test_the_whole_catalogue_has_a_computable_series_ceiling():
    # If any metric had an unbounded label this could not be computed at all.
    assert 0 < CATALOGUE.max_series() < 1000


# --------------------------------------- a backend is not allowed to matter
class _BrokenBackend:
    def observe(self, spec, value, labels, correlation):
        raise RuntimeError("exporter queue is full")

    def event(self, name, attributes, correlation):
        raise RuntimeError("exporter queue is full")


def test_a_backend_that_raises_never_reaches_the_caller():
    # ADR-022 rule 3. A telemetry exporter that takes a gate evaluation down
    # with it turns an observability problem into a verdict.
    t = Telemetry(_BrokenBackend())
    t.observe("clep_request_outcome_total", 1, outcome_class="success")
    t.event("gate.decided", verdict="pass")
    assert t.backend_failures == 2


def test_a_bad_call_site_still_raises_even_though_a_bad_backend_does_not():
    # The asymmetry is the design: a wrong call site is static and must be
    # loud; a broken backend is an environment and must be invisible.
    t = Telemetry(_BrokenBackend())
    with pytest.raises(CardinalityError):
        t.observe("clep_request_outcome_total", 1, outcome_class="nonsense")


def test_the_default_backend_records_nothing_and_cannot_fail():
    t = Telemetry()
    assert isinstance(t.backend, NullBackend)
    t.observe("clep_request_outcome_total", 1, outcome_class="success")
    assert t.backend_failures == 0


# --------------------------------------------------------------- recording
def test_a_recorded_sample_carries_the_correlation_in_scope():
    backend = RecordingBackend()
    t = Telemetry(backend)
    with correlated() as c:
        t.observe("clep_request_outcome_total", 1, outcome_class="success")
    assert backend.correlations() == {c.correlation_id}


def test_timed_validates_before_running_the_block():
    # A CardinalityError raised in a finally would replace whatever the block
    # was raising, so the labels are checked on the way in.
    t = Telemetry(RecordingBackend())
    entered = False
    with pytest.raises(CardinalityError):
        with t.timed("clep_gate_decision_duration_ms", verdict="not-a-verdict"):
            entered = True
    assert entered is False


def test_timed_records_a_duration_into_its_histogram():
    backend = RecordingBackend()
    clock = iter([0.0, 0.25])
    t = Telemetry(backend, clock=lambda: next(clock))
    with t.timed("clep_gate_decision_duration_ms", verdict="pass"):
        pass
    assert backend.values_for("clep_gate_decision_duration_ms") == [250.0]


def test_series_count_does_not_grow_with_the_number_of_tenants():
    # The behavioural form of REQ-N-OBS-4, in miniature. The full version runs
    # real tenants through the platform in the Phase 13 validator.
    backend = RecordingBackend()
    t = Telemetry(backend)
    for n in range(50):
        with correlated():
            t.observe("clep_request_outcome_total", 1, outcome_class="success")
    assert len(backend.series()) == 1
    assert len(backend.samples) == 50
