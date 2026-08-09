"""The planner: a typed plan, a deterministic critic, and acceptance by a person."""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.agents.planner import (ACCEPTED, DRAFT, REJECTED, EvaluationPlan,
                                 PlanError, PlanInputs, PlanStep, accept, amend,
                                 draft_plan, plan_with_reflection, reject,
                                 validate)
from clep.agents.sdk import Bounds


def inputs(**over):
    base = dict(objective="does the new prompt refuse less often",
                suite_version_id="S1", dataset_version_ids=("D1",),
                candidate_labels=("candidate-a",),
                evaluator_version_keys=("exact_match@1",))
    base.update(over)
    return PlanInputs(**base)


def bounds(**over):
    base = dict(max_iterations=4, budget=Decimal("1"), timeout_ms=5000)
    base.update(over)
    return Bounds(**base)


# ------------------------------------------------------------------ the typing

def test_a_plan_without_an_objective_cannot_be_reviewed():
    with pytest.raises(PlanError):
        inputs(objective="   ")
    with pytest.raises(PlanError):
        inputs(candidate_labels=())


def test_a_step_kind_outside_the_vocabulary_is_refused():
    with pytest.raises(PlanError):
        PlanStep(order=0, kind="think_about_it", subject="x")


def test_the_drafter_produces_the_plan_the_inputs_imply():
    plan = draft_plan(inputs(baseline_id="B1", gate_policy_version_id="G1",
                             ensemble_judge_keys=("a@1", "b@1")))
    assert [s.kind for s in plan.steps] == [
        "score_candidate", "run_evaluator", "run_ensemble",
        "compare_to_baseline", "evaluate_gate"]
    assert plan.state == DRAFT
    assert plan.digest.startswith("sha256:")


def test_two_plans_that_would_do_the_same_thing_share_a_digest():
    assert draft_plan(inputs()).digest == draft_plan(inputs()).digest
    assert draft_plan(inputs()).digest != draft_plan(
        inputs(objective="something else")).digest


# ----------------------------------------------------------------- the critic

def test_a_plan_that_evaluates_a_gate_without_a_policy_is_refused():
    plan = draft_plan(inputs(baseline_id="B1", gate_policy_version_id="G1"))
    broken = EvaluationPlan(inputs=inputs(baseline_id="B1"), steps=plan.steps)
    problems = validate(broken)
    assert "no policy version" in problems


def test_a_gate_without_a_baseline_is_a_measurement_not_a_decision():
    plan = draft_plan(inputs(gate_policy_version_id="G1"))
    problems = validate(plan)
    assert "no baseline" in problems


def test_a_step_acting_on_something_outside_the_inputs_is_refused():
    plan = draft_plan(inputs())
    tampered = EvaluationPlan(
        inputs=plan.inputs,
        steps=plan.steps + (PlanStep(order=99, kind="score_candidate",
                                     subject="another-tenants-candidate"),))
    assert "not among the plan's inputs" in validate(tampered)


def test_an_ensemble_of_one_judge_is_refused():
    plan = draft_plan(inputs(ensemble_judge_keys=("a@1",)))
    # The drafter emits the step; the critic is what refuses it.
    assert "fewer than two judges" in validate(plan)


def test_an_over_budget_plan_is_refused_rather_than_started():
    """REQ-F-10-5. A run that stops halfway has spent money and produced an
    incomplete record, which is the worse of both."""
    plan = draft_plan(inputs(budget=Decimal("0.0001")), sample_count=100)
    problems = validate(plan)
    assert "exceeds the budget" in problems
    assert "refused rather than partially executed" in problems


def test_a_plan_within_its_budget_validates():
    assert validate(draft_plan(inputs(budget=Decimal("10")))) == ""


def test_steps_out_of_order_are_refused():
    plan = draft_plan(inputs())
    scrambled = EvaluationPlan(inputs=plan.inputs,
                               steps=tuple(reversed(plan.steps + plan.steps[:1])))
    assert "strict order" in validate(scrambled)


def test_a_plan_that_scores_nothing_is_refused():
    empty = EvaluationPlan(inputs=inputs(), steps=())
    assert "no steps" in validate(empty)


# ------------------------------------------------------------------ reflection

def test_reflection_redrafts_against_the_critique_and_keeps_every_draft():
    """REQ-F-AG-5. The rejected drafts are the history worth having."""
    attempts = []

    def drafter(plan_inputs, index, critique):
        attempts.append(critique)
        if index == 0:
            return EvaluationPlan(inputs=plan_inputs, steps=())
        if index == 1:
            # Different from the first, and still wrong: acts on something that
            # is not among the inputs.
            return EvaluationPlan(
                inputs=plan_inputs,
                steps=(PlanStep(order=0, kind="score_candidate",
                                subject="a candidate nobody asked for"),))
        return draft_plan(plan_inputs)

    result = plan_with_reflection(inputs(), bounds(), drafter=drafter)
    assert result.state == "accepted"
    assert result.iterations == 3
    assert attempts[0] is None
    assert "no steps" in attempts[1]
    assert [a.accepted for a in result.attempts] == [False, False, True]


def test_reflection_that_never_validates_returns_no_plan():
    def drafter(plan_inputs, index, critique):
        return EvaluationPlan(inputs=plan_inputs, steps=())

    result = plan_with_reflection(inputs(), bounds(max_iterations=3),
                                  drafter=drafter)
    # Identical drafts, so the no-progress rule stops it before the third.
    assert result.state == "no_progress"
    assert result.value is None
    assert all(a.critique for a in result.attempts)


def test_reflection_is_bounded_by_the_budget_it_is_given():
    from clep.agents.sdk import Proposal

    def expensive(plan_inputs, index, critique):
        return EvaluationPlan(inputs=plan_inputs,
                              steps=(PlanStep(order=index, kind="run_evaluator",
                                              subject="exact_match@1"),))

    result = plan_with_reflection(inputs(), bounds(max_iterations=10),
                                  drafter=expensive)
    assert result.state in ("iterations_exhausted", "no_progress")
    assert result.value is None


def test_a_drafter_returning_the_wrong_type_fails_the_attempt_not_the_process():
    result = plan_with_reflection(inputs(), bounds(max_iterations=2),
                                  drafter=lambda i, n, c: "a plan, honest")
    assert result.state == "failed"
    assert all(a.error for a in result.attempts)


def test_the_planner_needs_no_model_to_produce_a_plan():
    """REQ-F-AG-8. The built-in drafter is deterministic, so there is no test
    mode: the offline path is the ordinary path."""
    result = plan_with_reflection(inputs(), bounds())
    assert result.state == "accepted"
    assert result.iterations == 1


# ----------------------------------------------------------------- acceptance

def test_a_plan_that_does_not_validate_cannot_be_accepted():
    plan = draft_plan(inputs(gate_policy_version_id="G1"))
    with pytest.raises(PlanError, match="does not validate"):
        accept(plan, "reviewer@example.com")


def test_acceptance_records_who_accepted_it():
    plan = accept(draft_plan(inputs()), "reviewer@example.com")
    assert plan.state == ACCEPTED
    assert plan.accepted_by == "reviewer@example.com"
    with pytest.raises(PlanError):
        accept(draft_plan(inputs()), "")


def test_an_accepted_plan_cannot_be_amended():
    plan = accept(draft_plan(inputs()), "reviewer@example.com")
    with pytest.raises(PlanError, match="after the fact"):
        amend(plan, note="one more thing", actor="reviewer@example.com")
    with pytest.raises(PlanError):
        accept(plan, "someone-else@example.com")


def test_an_amendment_is_appended_with_the_digest_it_edited():
    original = draft_plan(inputs())
    amended = amend(original, note="drop the gate step",
                    actor="reviewer@example.com",
                    steps=original.steps[:1])
    assert amended.state == DRAFT
    assert amended.amendments == (("reviewer@example.com", "drop the gate step",
                                   original.digest),)
    assert amended.digest != original.digest


def test_an_amendment_records_who_made_it():
    with pytest.raises(PlanError):
        amend(draft_plan(inputs()), note="x", actor="")


def test_a_human_amendment_is_re_validated_like_any_draft():
    """A person editing a plan by hand is at least as likely to point it at the
    wrong suite as a model is."""
    original = draft_plan(inputs())
    broken = amend(original, note="point it elsewhere", actor="a@b.c",
                   steps=(PlanStep(order=0, kind="score_candidate",
                                   subject="not-a-candidate"),))
    assert validate(broken)
    with pytest.raises(PlanError, match="does not validate"):
        accept(broken, "a@b.c")


def test_a_rejection_records_its_reason():
    plan = reject(draft_plan(inputs()), "a@b.c", "wrong dataset")
    assert plan.state == REJECTED
    assert "rejected: wrong dataset" in plan.amendments[0][1]
    with pytest.raises(PlanError):
        reject(draft_plan(inputs()), "a@b.c", "")
