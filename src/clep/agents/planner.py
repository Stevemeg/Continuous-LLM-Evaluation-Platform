"""The evaluation planner — a typed plan a person can read, argue with, and sign.

`REQ-F-AG-1`: a typed, reviewable plan produced from an objective, datasets,
suites, candidates, budgets and policies, which a human may inspect and amend
before execution. Canonical §7 is the other half of the requirement: reasoning is
used only where it adds value, and everything else stays conventional software.

The split this module draws:

  * **Drafting** is where reasoning belongs. Turning "check the new prompt does
    not make refusals worse" into an ordered list of steps is judgement, and a
    model may do it. The drafter is a port, and the built-in one is deterministic
    so the planner has something to do when no model is configured and so
    `REQ-F-AG-8` holds without a special test mode.

  * **Validation is not.** Whether a plan references a suite that exists, stays
    inside its budget, and orders a gate evaluation only when it has a policy to
    evaluate against are facts, not opinions. The critic is deterministic, it
    runs on every draft including a human-amended one, and no plan reaches
    `accepted` without passing it. A reasoning component that could talk its way
    past validation would make the type system decorative.

  * **Acceptance is a person's act.** A plan is `draft` until someone accepts it,
    and frozen afterwards. `accept` records the actor and the digest of exactly
    what was accepted, so that "this is the plan that was approved" is checkable
    rather than remembered.

The estimate is a refusal, not a warning. `REQ-F-10-5` requires a run whose
estimate exceeds its budget to be skipped rather than partially executed, so an
over-budget plan fails validation and never reaches acceptance.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import Decimal

from clep.agents.sdk import Bounds, Proposal, Reasoning, run_bounded

DRAFT = "draft"
ACCEPTED = "accepted"
REJECTED = "rejected"
PLAN_STATES = (DRAFT, ACCEPTED, REJECTED)

#: What a step can be. Closed, because a plan step nobody can execute is a
#: sentence, not a plan.
STEP_KINDS = ("score_candidate", "run_evaluator", "run_ensemble",
              "compare_to_baseline", "evaluate_gate")


class PlanError(Exception):
    pass


@dataclass(frozen=True)
class PlanInputs:
    """Everything the plan is allowed to be about."""
    objective: str
    suite_version_id: str
    dataset_version_ids: tuple
    candidate_labels: tuple
    evaluator_version_keys: tuple = ()
    ensemble_judge_keys: tuple = ()
    baseline_id: str | None = None
    gate_policy_version_id: str | None = None
    judge_ensemble_id: str | None = None
    budget: Decimal | None = None
    currency: str = "USD"
    integration_tier: str = "output_only"

    def __post_init__(self):
        if not self.objective.strip():
            raise PlanError("a plan without an objective cannot be reviewed "
                            "against anything")
        if not self.candidate_labels:
            raise PlanError("a plan must name at least one candidate")


@dataclass(frozen=True)
class PlanStep:
    order: int
    kind: str
    subject: str
    detail: str = ""
    estimated_cost: Decimal = Decimal(0)

    def __post_init__(self):
        if self.kind not in STEP_KINDS:
            raise PlanError(f"unknown plan step kind {self.kind!r}")
        if self.estimated_cost < 0:
            raise PlanError("a step cannot cost less than nothing")


@dataclass(frozen=True)
class EvaluationPlan:
    inputs: PlanInputs
    steps: tuple
    state: str = DRAFT
    accepted_by: str | None = None
    amendments: tuple = ()

    def __post_init__(self):
        if self.state not in PLAN_STATES:
            raise PlanError(f"unknown plan state {self.state!r}")
        if (self.state == ACCEPTED) != (self.accepted_by is not None):
            raise PlanError("an accepted plan records who accepted it")

    @property
    def estimated_cost(self) -> Decimal:
        return sum((s.estimated_cost for s in self.steps), Decimal(0))

    @property
    def digest(self) -> str:
        """Content identity. Two plans that would do the same thing are the same
        plan, and an amended plan is a different one."""
        body = json.dumps({
            "objective": self.inputs.objective,
            "suite_version_id": self.inputs.suite_version_id,
            "dataset_version_ids": list(self.inputs.dataset_version_ids),
            "candidate_labels": list(self.inputs.candidate_labels),
            "evaluator_version_keys": list(self.inputs.evaluator_version_keys),
            "ensemble_judge_keys": list(self.inputs.ensemble_judge_keys),
            "baseline_id": self.inputs.baseline_id,
            "gate_policy_version_id": self.inputs.gate_policy_version_id,
            "integration_tier": self.inputs.integration_tier,
            "steps": [[s.order, s.kind, s.subject, str(s.estimated_cost)]
                      for s in self.steps],
        }, sort_keys=True, separators=(",", ":"))
        return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def draft_plan(inputs: PlanInputs, *, unit_cost: Decimal = Decimal("0.001"),
               sample_count: int = 1) -> EvaluationPlan:
    """The built-in deterministic drafter.

    Produces the plan the inputs imply, in the order the harness must execute
    them: score every candidate, run every evaluator, run the ensemble where one
    is configured, compare to a baseline where one is named, and evaluate the
    gate where a policy version is named. No judgement is exercised, which is
    exactly why it is available with no model configured.
    """
    steps: list[PlanStep] = []
    order = 0
    for label in inputs.candidate_labels:
        steps.append(PlanStep(order, "score_candidate", label,
                              f"{sample_count} sample(s)",
                              unit_cost * Decimal(sample_count)))
        order += 1
    for key in inputs.evaluator_version_keys:
        steps.append(PlanStep(order, "run_evaluator", key,
                              "deterministic; no model call"))
        order += 1
    if inputs.ensemble_judge_keys:
        steps.append(PlanStep(
            order, "run_ensemble", ",".join(inputs.ensemble_judge_keys),
            f"{len(inputs.ensemble_judge_keys)} judges over {sample_count} sample(s)",
            unit_cost * Decimal(sample_count * len(inputs.ensemble_judge_keys))))
        order += 1
    if inputs.baseline_id:
        steps.append(PlanStep(order, "compare_to_baseline", inputs.baseline_id))
        order += 1
    if inputs.gate_policy_version_id:
        steps.append(PlanStep(order, "evaluate_gate", inputs.gate_policy_version_id))
        order += 1
    return EvaluationPlan(inputs=inputs, steps=tuple(steps))


def validate(plan: EvaluationPlan) -> str:
    """Every reason this plan cannot be executed, or an empty string.

    Deterministic and total. It is the critic of the bounded loop, and it is also
    what an amended plan is re-checked against — a human amendment gets no more
    trust than a model's draft, because a person editing a plan by hand is at
    least as likely to point it at the wrong suite.
    """
    problems: list[str] = []
    if not plan.steps:
        problems.append("the plan has no steps; a plan that does nothing cannot "
                        "answer an objective")
    orders = [s.order for s in plan.steps]
    if orders != sorted(orders) or len(set(orders)) != len(orders):
        problems.append("steps are not in a single, strict order")

    known_subjects = (set(plan.inputs.candidate_labels)
                      | set(plan.inputs.evaluator_version_keys)
                      | {",".join(plan.inputs.ensemble_judge_keys)}
                      | {plan.inputs.baseline_id, plan.inputs.gate_policy_version_id})
    for step in plan.steps:
        if step.subject not in known_subjects:
            problems.append(
                f"step {step.order} acts on {step.subject!r}, which is not among "
                f"the plan's inputs")

    kinds = {s.kind for s in plan.steps}
    if "evaluate_gate" in kinds and not plan.inputs.gate_policy_version_id:
        problems.append("the plan evaluates a gate with no policy version to "
                        "evaluate against")
    if "evaluate_gate" in kinds and not plan.inputs.baseline_id:
        problems.append("the plan evaluates a gate with no baseline; a gate "
                        "decision is a comparison, not a measurement")
    if "compare_to_baseline" in kinds and not plan.inputs.baseline_id:
        problems.append("the plan compares to a baseline it does not name")
    if "run_ensemble" in kinds and len(plan.inputs.ensemble_judge_keys) < 2:
        problems.append("the plan runs an ensemble of fewer than two judges")
    if not any(s.kind == "score_candidate" for s in plan.steps):
        problems.append("no candidate is scored, so nothing is evaluated")

    if plan.inputs.budget is not None and plan.estimated_cost > plan.inputs.budget:
        # REQ-F-10-5. Skipped, not started: a run that stops halfway has spent
        # money and produced an incomplete record, which is the worse of both.
        problems.append(
            f"the estimate of {plan.estimated_cost} {plan.inputs.currency} exceeds "
            f"the budget of {plan.inputs.budget}; the run is refused rather than "
            f"partially executed")
    return "; ".join(problems)


def plan_with_reflection(inputs: PlanInputs, bounds: Bounds, *, drafter=None,
                         clock=None) -> Reasoning:
    """Draft, critique, redraft — bounded, and with every draft retained.

    `REQ-F-AG-5`. The drafter receives the previous critique and may respond to
    it; the critic is `validate` and is not negotiable. If no acceptable plan
    exists within the bounds, the result carries the drafts and the reasons they
    were refused, and carries no plan — an unvalidated plan is not a plan, and
    handing one back as a partial result is how it would end up executed.
    """
    drafter = drafter or (lambda inputs, index, critique: draft_plan(inputs))

    def step(index, critique):
        plan = drafter(inputs, index, critique)
        if not isinstance(plan, EvaluationPlan):
            raise PlanError(f"a drafter must return an EvaluationPlan, not "
                            f"{type(plan).__name__}")
        return Proposal(value=plan, cost=Decimal(0))

    kwargs = {"fingerprint": lambda plan: plan.digest}
    if clock is not None:
        kwargs["clock"] = clock
    return run_bounded(step, validate, bounds, **kwargs)


def amend(plan: EvaluationPlan, *, steps=None, inputs=None, note: str = "",
          actor: str = "") -> EvaluationPlan:
    """A human edit, before acceptance and never after.

    The amendment is appended to the plan's history rather than replacing it,
    because `REQ-F-AG-1` makes the plan reviewable and a plan whose edits are
    invisible is reviewable only in the sense that it can be looked at.
    """
    if plan.state != DRAFT:
        raise PlanError(
            f"this plan is {plan.state}; an accepted plan is the record of what "
            f"was approved and amending it would change that record after the "
            f"fact — draft a new plan instead")
    if not actor:
        raise PlanError("an amendment records who made it")
    amended = replace(plan,
                      steps=tuple(steps) if steps is not None else plan.steps,
                      inputs=inputs if inputs is not None else plan.inputs,
                      amendments=plan.amendments + ((actor, note, plan.digest),))
    return amended


def accept(plan: EvaluationPlan, actor: str) -> EvaluationPlan:
    """Freeze a plan. Refuses one that does not validate."""
    if plan.state != DRAFT:
        raise PlanError(f"this plan is already {plan.state}")
    if not actor:
        raise PlanError("an acceptance records who accepted it")
    problems = validate(plan)
    if problems:
        raise PlanError(f"this plan does not validate and cannot be accepted: "
                        f"{problems}")
    return replace(plan, state=ACCEPTED, accepted_by=actor)


def reject(plan: EvaluationPlan, actor: str, reason: str) -> EvaluationPlan:
    if plan.state != DRAFT:
        raise PlanError(f"this plan is already {plan.state}")
    if not reason:
        raise PlanError("a rejection records its reason")
    return replace(plan, state=REJECTED,
                   amendments=plan.amendments + ((actor, f"rejected: {reason}",
                                                  plan.digest),))
