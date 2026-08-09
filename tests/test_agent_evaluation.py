"""Agent evaluation: the trajectory type, and what can be read from a prefix."""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.evaluators.agent import register_agent_evaluators
from clep.evaluators.sdk import EvaluatorRegistry, SampleContext, run_evaluator
from clep.evaluators.trajectory import (MAX_TRAJECTORY_STEPS, ToolCall,
                                        Trajectory, TrajectoryError,
                                        consecutive_repeats, ingest,
                                        recovered_after_failure,
                                        repeated_calls)

SCHEMAS = {"search": {"required": ["q"], "properties": {"q": {}}},
           "open": {"required": ["id"], "properties": {"id": {}}},
           "answer": {"required": ["text"], "properties": {"text": {}}}}


@pytest.fixture
def registry():
    r = EvaluatorRegistry()
    register_agent_evaluators(r)
    return r


def good_calls():
    return [ToolCall(0, "search", {"q": "x"}, "hit"),
            ToolCall(1, "open", {"id": "1"}, "", failed=True, error="404"),
            ToolCall(2, "search", {"q": "y"}, "hit"),
            ToolCall(3, "answer", {"text": "42"}, "ok")]


def sample(trajectory, **over):
    base = dict(example_id="x", prompt="q", output="", expected="42",
                integration_tier="full", agent_trajectory=trajectory,
                tool_schemas=SCHEMAS, expected_tools=("search", "answer"))
    base.update(over)
    return SampleContext(**base)


# ------------------------------------------------------------ the type itself
def test_a_tool_call_records_what_it_called_and_with_what():
    with pytest.raises(TrajectoryError):
        ToolCall(0, "", {})
    with pytest.raises(TrajectoryError):
        ToolCall(0, "search", "not a mapping")
    with pytest.raises(TrajectoryError):
        ToolCall(0, "search", {}, failed=True)          # no error recorded
    with pytest.raises(TrajectoryError):
        ToolCall(0, "search", {}, error="but not failed")


def test_arguments_are_part_of_what_makes_a_call_the_same_call():
    a = ToolCall(0, "search", {"q": "x"})
    b = ToolCall(1, "search", {"q": "y"})
    c = ToolCall(2, "search", {"q": "x"})
    assert a.signature != b.signature
    assert a.signature == c.signature


def test_steps_must_be_in_a_single_strict_order():
    with pytest.raises(TrajectoryError):
        Trajectory(steps=(ToolCall(1, "a", {}), ToolCall(0, "a", {})))
    with pytest.raises(TrajectoryError):
        Trajectory(steps=(ToolCall(0, "a", {}), ToolCall(0, "b", {})))


def test_a_trajectory_past_the_bound_cannot_be_constructed_directly():
    calls = [ToolCall(i, "a", {"i": i}) for i in range(MAX_TRAJECTORY_STEPS + 1)]
    with pytest.raises(TrajectoryError):
        Trajectory(steps=tuple(calls))


# ------------------------------------------------------------------ ingest
def test_ingest_keeps_the_prefix_and_says_it_truncated():
    calls = [ToolCall(i, "a", {"i": i}) for i in range(10)]
    t = ingest(calls, limit=4)
    assert t.truncated is True
    assert [s.step for s in t.steps] == [0, 1, 2, 3]


def test_a_trajectory_within_the_bound_is_not_marked_truncated():
    assert ingest(good_calls()).truncated is False


# ----------------------------------------------------------------- signals
def test_repeats_and_consecutive_repeats_are_different_questions():
    interleaved = ingest([ToolCall(0, "a", {}), ToolCall(1, "b", {}),
                          ToolCall(2, "a", {})])
    consecutive = ingest([ToolCall(i, "a", {}) for i in range(3)])
    assert repeated_calls(interleaved) and consecutive_repeats(interleaved) == 1
    assert consecutive_repeats(consecutive) == 3


def test_recovery_is_none_when_nothing_failed():
    """An agent that never hit an error has not demonstrated recovery."""
    assert recovered_after_failure(ingest([ToolCall(0, "a", {}, "ok")])) is None


def test_recovery_requires_a_different_call_that_worked():
    failed_then_same = ingest([ToolCall(0, "a", {}, failed=True, error="x"),
                               ToolCall(1, "a", {}, failed=True, error="x")])
    assert recovered_after_failure(failed_then_same) is False
    assert recovered_after_failure(ingest(good_calls())) is True


# --------------------------------------------------------------- evaluators
def test_tool_call_validity_counts_offending_steps_not_faults(registry):
    """One call with two faults is one invalid call. Counting faults could take
    the score below zero."""
    calls = [ToolCall(0, "search", {"wrong": 1, "alsowrong": 2}),
             ToolCall(1, "search", {"q": "x"})]
    outcome = run_evaluator(registry.get("tool_call_validity@1.0.0"),
                            sample(ingest(calls)))
    assert outcome.score == Decimal("0.500000000")


def test_an_undeclared_tool_cannot_be_called_wrongly(registry):
    outcome = run_evaluator(registry.get("tool_call_validity@1.0.0"),
                            sample(ingest(good_calls()), tool_schemas={}))
    assert outcome.resolution == "abstained"


def test_tool_selection_penalises_spurious_tools_as_well_as_missing_ones(registry):
    """Recall alone would give an agent that calls every tool a perfect score."""
    everything = ingest([ToolCall(0, "search", {"q": "x"}),
                         ToolCall(1, "open", {"id": "1"}),
                         ToolCall(2, "answer", {"text": "42"})])
    only_needed = ingest([ToolCall(0, "search", {"q": "x"}),
                          ToolCall(1, "answer", {"text": "42"})])
    loose = run_evaluator(registry.get("tool_selection_correctness@1.0.0"),
                          sample(everything))
    tight = run_evaluator(registry.get("tool_selection_correctness@1.0.0"),
                          sample(only_needed))
    assert tight.score == 1
    assert loose.score < tight.score


def test_tool_selection_abstains_without_an_expected_set(registry):
    outcome = run_evaluator(registry.get("tool_selection_correctness@1.0.0"),
                            sample(ingest(good_calls()), expected_tools=()))
    assert outcome.resolution == "abstained"


def test_task_success_is_literal_and_separate_from_route_quality(registry):
    reached = ingest(good_calls(), final_answer="the answer is 42")
    wandered = ingest([ToolCall(i, "search", {"q": str(i)}) for i in range(9)]
                      + [ToolCall(9, "answer", {"text": "42"})],
                      final_answer="the answer is 42")
    for trajectory in (reached, wandered):
        outcome = run_evaluator(registry.get("task_success@1.0.0"),
                                sample(trajectory))
        assert outcome.score == 1, "a bad route to the right answer still succeeded"


def test_a_loop_scores_zero_and_a_clean_run_scores_one(registry):
    looped = ingest([ToolCall(i, "search", {"q": "x"}) for i in range(5)])
    assert run_evaluator(registry.get("no_non_terminating_loop@1.0.0"),
                         sample(looped)).score == 0
    assert run_evaluator(registry.get("no_non_terminating_loop@1.0.0"),
                         sample(ingest(good_calls()))).score == 1


def test_recovery_abstains_when_nothing_failed(registry):
    clean = ingest([ToolCall(0, "search", {"q": "x"}, "hit"),
                    ToolCall(1, "answer", {"text": "42"}, "ok")])
    assert run_evaluator(registry.get("recovery_after_failure@1.0.0"),
                         sample(clean)).resolution == "abstained"


# ---------------------------------------------------------------- truncation
def test_a_truncated_trajectory_is_not_evaluated_as_complete(registry):
    """REQ-F-04-5. Whether the task finished, or the loop ended, is not readable
    from a prefix — and a guess in either direction is wrong half the time."""
    truncated = ingest([ToolCall(i, "search", {"q": str(i)}) for i in range(9)],
                       limit=3)
    for name in ("task_success@1.0.0", "no_non_terminating_loop@1.0.0"):
        outcome = run_evaluator(registry.get(name), sample(truncated))
        assert outcome.resolution == "truncated"
        assert outcome.score is None


def test_a_loop_already_visible_in_the_prefix_is_still_a_loop(registry):
    """Truncation refuses the questions a prefix cannot answer, not the ones it
    can: three identical calls in a row is a loop however the run ended."""
    truncated = ingest([ToolCall(i, "search", {"q": "x"}) for i in range(9)],
                       limit=4)
    outcome = run_evaluator(registry.get("no_non_terminating_loop@1.0.0"),
                            sample(truncated))
    assert outcome.resolution == "scored"
    assert outcome.score == 0


def test_truncation_before_any_recovery_refuses_rather_than_scoring_zero(registry):
    truncated = ingest([ToolCall(0, "open", {"id": "1"}, failed=True, error="404"),
                        ToolCall(1, "open", {"id": "2"}, failed=True, error="404"),
                        ToolCall(2, "search", {"q": "x"}, "hit")], limit=2)
    outcome = run_evaluator(registry.get("recovery_after_failure@1.0.0"),
                            sample(truncated))
    assert outcome.resolution == "truncated"


# ----------------------------------------------------- the required separation
def test_no_evaluator_combines_route_quality_with_answer_quality(registry):
    """REQ-F-04-4. An agent that took a terrible route to the right answer and
    one that took a good route to the wrong one are different problems."""
    names = set(registry.keys())
    assert "task_success@1.0.0" in names
    assert "no_non_terminating_loop@1.0.0" in names
    for combined in ("agent_score", "overall", "composite"):
        assert not any(combined in name for name in names)
