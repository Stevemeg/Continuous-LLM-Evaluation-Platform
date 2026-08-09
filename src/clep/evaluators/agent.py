"""Agent evaluators — computed from the trajectory, judged only where needed.

`REQ-F-04-2` task success, tool-selection correctness, tool-call validity.
`REQ-F-04-3` trajectory and planning quality, including non-terminating loops
and recovery after a failed step. `REQ-F-04-4` final-answer quality, reported
separately from trajectory quality.

Most of this is computable, and computing it is better than judging it. Whether
a tool call's arguments match the tool's declared schema is a fact. Whether the
same call was made five times running is a fact. Whether the agent used the
tools the dataset says the task needed is a fact, once the dataset says.

`REQ-F-04-4` is the one that is structural rather than arithmetic: final-answer
quality is a judgement, it belongs to the ensemble, and the requirement is that
it is **reported separately** from trajectory quality. So there is no evaluator
here that mixes them, and no combined "agent score" for one to hide inside. An
agent that took a terrible route to the right answer and one that took a good
route to the wrong one are different problems, and a single number makes them
look identical.

Truncation is refused rather than answered wherever the answer would change.
`REQ-F-04-5` says a truncated trajectory is not evaluated as complete, and
"did this agent stop looping?" read from a prefix is a guess.
"""
from __future__ import annotations

from decimal import Decimal

from clep.evaluators.sdk import (EvaluatorOutcome, SampleContext, abstained,
                                 scored)
from clep.evaluators.trajectory import (Trajectory, consecutive_repeats,
                                        recovered_after_failure)

#: How many identical consecutive calls count as a loop. A reporting threshold
#: for a deterministic signal, not a calibrated statistical parameter: three
#: identical requests in a row cannot be productive, because each received the
#: same result as the last.
CONSECUTIVE_REPEAT_LIMIT = 3


def _trajectory(sample: SampleContext):
    return getattr(sample, "agent_trajectory", None)


class ToolCallValidity:
    """Every call names a declared tool and supplies its required arguments.

    A fact, checked against the tool schemas the sample carries. Abstains when
    no schemas are declared — an undeclared tool cannot be called wrongly,
    because nothing says what right would be.
    """
    name = "tool_call_validity"
    version = "1.0.0"
    requires_tier = "full"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        trajectory = _trajectory(sample)
        if trajectory is None or not trajectory.steps:
            return abstained("no trajectory on this sample")
        schemas = getattr(sample, "tool_schemas", {}) or {}
        if not schemas:
            return abstained("no tool schemas are declared, so a call cannot be "
                             "invalid against anything")
        problems, offending = [], set()
        for step in trajectory.steps:
            declared = schemas.get(step.tool)
            if declared is None:
                problems.append(f"step {step.step}: {step.tool} is not declared")
                offending.add(step.step)
                continue
            missing = [r for r in declared.get("required", ())
                       if r not in step.arguments]
            unknown = [a for a in step.arguments
                       if declared.get("properties") is not None
                       and a not in declared["properties"]]
            if missing:
                problems.append(f"step {step.step}: {step.tool} missing "
                                f"{', '.join(missing)}")
                offending.add(step.step)
            if unknown:
                problems.append(f"step {step.step}: {step.tool} unknown "
                                f"{', '.join(unknown)}")
                offending.add(step.step)
        # Counted by offending STEP, not by problem: one call with two faults is
        # one invalid call, and counting faults would let a single bad step drag
        # the score below zero.
        valid = len(trajectory.steps) - len(offending)
        return EvaluatorOutcome(
            "scored", score=Decimal(valid) / Decimal(len(trajectory.steps)),
            detail="; ".join(problems[:5]))


class ToolSelectionCorrectness:
    """Did the agent use the tools the task needed, and only those.

    Scored against the dataset's expected tool set. Abstains without one: any
    tool choice is defensible when nothing says what the task required.
    """
    name = "tool_selection_correctness"
    version = "1.0.0"
    requires_tier = "full"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        trajectory = _trajectory(sample)
        if trajectory is None:
            return abstained("no trajectory on this sample")
        expected = set(getattr(sample, "expected_tools", ()) or ())
        if not expected:
            return abstained("this example does not say which tools the task "
                             "required")
        used = set(trajectory.tools_used)
        correct = expected & used
        spurious = used - expected
        # Jaccard rather than recall: an agent that calls every tool it has
        # would score a perfect recall while doing something senseless.
        union = expected | used
        return EvaluatorOutcome(
            "scored", score=Decimal(len(correct)) / Decimal(len(union)),
            detail=(f"missed: {', '.join(sorted(expected - used))}; "
                    f"spurious: {', '.join(sorted(spurious))}").strip("; "))


class TaskSuccess:
    """Did the trajectory reach the outcome the dataset defines as success.

    Deterministic and deliberately literal: the dataset states a success
    condition, and this checks it. It is not an opinion about whether the agent
    did well — that is `REQ-F-04-3` and `REQ-F-04-4`, and they are separate.
    """
    name = "task_success"
    version = "1.0.0"
    requires_tier = "full"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        trajectory = _trajectory(sample)
        if trajectory is None:
            return abstained("no trajectory on this sample")
        if sample.expected is None:
            return abstained("no success condition on this example")
        if trajectory.truncated:
            return EvaluatorOutcome(
                "truncated",
                detail="the trajectory was truncated on ingest; whether the "
                       "task completed is not readable from a prefix")
        answer = (trajectory.final_answer or sample.output or "").strip().lower()
        return scored(1 if sample.expected.strip().lower() in answer else 0)


class NonTerminatingLoop:
    """1.0 when no identical call repeats consecutively past the limit.

    Scored the same way round as every other evaluator: higher is better, so a
    clean trajectory is 1.0 and a looping one is 0.0. Refuses on a truncated
    trajectory, because a prefix cannot show that a loop ended.
    """
    name = "no_non_terminating_loop"
    version = "1.0.0"
    requires_tier = "full"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        trajectory = _trajectory(sample)
        if trajectory is None or not trajectory.steps:
            return abstained("no trajectory on this sample")
        longest = consecutive_repeats(trajectory)
        if trajectory.truncated and longest < CONSECUTIVE_REPEAT_LIMIT:
            return EvaluatorOutcome(
                "truncated",
                detail=f"the trajectory was truncated with a run of {longest} "
                       f"identical calls in progress; whether it terminated is "
                       f"not readable from a prefix")
        return EvaluatorOutcome(
            "scored",
            score=Decimal(0 if longest >= CONSECUTIVE_REPEAT_LIMIT else 1),
            detail=f"longest run of identical consecutive calls: {longest}")


class RecoveryAfterFailure:
    """1.0 when a failed step was followed by a different call that worked.

    Abstains when nothing failed. An agent that never hit an error has not
    demonstrated recovery, and scoring it 1.0 would reward the absence of a
    test rather than passing one — the same reasoning that stops an unscored
    sample being a zero.
    """
    name = "recovery_after_failure"
    version = "1.0.0"
    requires_tier = "full"

    def evaluate(self, sample: SampleContext) -> EvaluatorOutcome:
        trajectory = _trajectory(sample)
        if trajectory is None:
            return abstained("no trajectory on this sample")
        recovered = recovered_after_failure(trajectory)
        if recovered is None:
            return abstained("no step failed, so recovery was never exercised")
        if not recovered and trajectory.truncated:
            return EvaluatorOutcome(
                "truncated",
                detail="a step failed and the trajectory was truncated before "
                       "any recovery could be observed")
        return scored(1 if recovered else 0)


def register_agent_evaluators(registry) -> list:
    return [registry.register(evaluator(), is_builtin=True)
            for evaluator in (ToolCallValidity, ToolSelectionCorrectness,
                              TaskSuccess, NonTerminatingLoop,
                              RecoveryAfterFailure)]
