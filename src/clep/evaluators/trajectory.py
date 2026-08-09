"""An agent trajectory, as an evaluation input.

`REQ-F-04-1`: the ordered sequence of tool calls with arguments and results.
`REQ-F-04-5`: bounded on ingest, and a truncated trajectory marked as truncated
rather than evaluated as complete. `REQ-F-04-6`: tool results are untrusted.

Three properties.

**The bound is on ingest, not on evaluation.** A trajectory arrives already
finished; refusing to *read* past the limit is what keeps a runaway agent from
turning into a runaway evaluation. What is kept is the prefix, because the
beginning of a trajectory is where the plan is visible.

**Truncated is a state of the trajectory, not a footnote.** Every evaluator here
reads it, and the ones whose answer would change refuse rather than answer.
"Did this agent terminate?" cannot be answered from a prefix: the run may have
finished on the next step or looped for a thousand more, and those are opposite
verdicts.

**A tool result is content from outside.** It reaches a judge through exactly
the path a retrieved passage does, and is fenced by the same containment. That
is `REQ-F-04-6` and it is the reason `SampleContext.trajectory` is rendered
inside the fence like everything else.
"""
from __future__ import annotations

import json
from dataclasses import dataclass

#: How many steps the platform will ingest. Stated rather than defaulted into a
#: function signature: it is a product limit, it appears in the report, and a
#: run that hit it says so.
MAX_TRAJECTORY_STEPS = 200


class TrajectoryError(ValueError):
    pass


@dataclass(frozen=True)
class ToolCall:
    """One step: what was called, with what, and what came back."""
    step: int
    tool: str
    arguments: dict
    result: str = ""
    failed: bool = False
    error: str = ""

    def __post_init__(self):
        if self.step < 0:
            raise TrajectoryError("a step index is a position")
        if not self.tool:
            raise TrajectoryError("a tool call names the tool it called")
        if not isinstance(self.arguments, dict):
            raise TrajectoryError("tool arguments are a mapping")
        if self.failed and not self.error:
            raise TrajectoryError("a failed call records what went wrong")
        if not self.failed and self.error:
            raise TrajectoryError("a call that carries an error did not succeed")

    @property
    def signature(self) -> str:
        """What makes two calls the same call, for loop detection.

        Arguments included and canonically encoded: calling the same tool twice
        with different arguments is progress, and calling it twice with the same
        arguments is not.
        """
        return self.tool + ":" + json.dumps(self.arguments, sort_keys=True,
                                            separators=(",", ":"), default=str)


@dataclass(frozen=True)
class Trajectory:
    steps: tuple
    truncated: bool = False
    final_answer: str = ""

    def __post_init__(self):
        indices = [s.step for s in self.steps]
        if indices != sorted(indices) or len(set(indices)) != len(indices):
            raise TrajectoryError("steps are not in a single, strict order")
        if len(self.steps) > MAX_TRAJECTORY_STEPS:
            raise TrajectoryError(
                f"{len(self.steps)} steps exceeds the ingest bound of "
                f"{MAX_TRAJECTORY_STEPS}; use `ingest`, which truncates and "
                f"says so, rather than constructing one past the limit")

    @property
    def tools_used(self) -> tuple:
        return tuple(dict.fromkeys(s.tool for s in self.steps))

    @property
    def failures(self) -> tuple:
        return tuple(s for s in self.steps if s.failed)


def ingest(calls, *, final_answer: str = "",
           limit: int = MAX_TRAJECTORY_STEPS) -> Trajectory:
    """Read at most `limit` steps, and mark the trajectory if there were more.

    The prefix is kept rather than the tail. A trajectory's plan is visible at
    the start; its last twenty steps out of a thousand are the part that tells
    you least.
    """
    calls = list(calls)
    if limit < 1:
        raise TrajectoryError("an ingest limit of zero reads nothing")
    return Trajectory(steps=tuple(calls[:limit]), truncated=len(calls) > limit,
                      final_answer=final_answer)


def repeated_calls(trajectory: Trajectory) -> tuple:
    """Signatures that occur more than once, in order of first appearance.

    Not "a loop" on its own: an agent may legitimately re-read a file after
    writing it. The loop evaluator applies a threshold to this; the function
    reports the fact.
    """
    seen, repeats = {}, []
    for step in trajectory.steps:
        seen[step.signature] = seen.get(step.signature, 0) + 1
        if seen[step.signature] == 2:
            repeats.append(step.signature)
    return tuple(repeats)


def consecutive_repeats(trajectory: Trajectory) -> int:
    """The longest run of identical consecutive calls.

    This is the shape that cannot be productive. A step whose call is identical
    to the one before it received the same result and did the same thing with
    it, so the agent is not making progress — it is making the same request.
    """
    longest = current = 0
    previous = None
    for step in trajectory.steps:
        current = current + 1 if step.signature == previous else 1
        previous = step.signature
        longest = max(longest, current)
    return longest


def recovered_after_failure(trajectory: Trajectory) -> bool | None:
    """Whether a failed step was followed by a different call that succeeded.

    `None` when nothing failed: an agent that never hit an error has not
    demonstrated recovery, and scoring it as though it had would reward the
    absence of a test rather than passing one.
    """
    failures = trajectory.failures
    if not failures:
        return None
    for failure in failures:
        for later in trajectory.steps:
            if later.step <= failure.step:
                continue
            if later.signature != failure.signature and not later.failed:
                return True
    return False
