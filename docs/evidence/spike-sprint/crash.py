"""Deterministic crash injection into the at-least-once window.

Both candidates have the same structural vulnerability: the unit of work commits
its side effect to the database, and only *afterwards* reports completion to the
engine (Temporal: activity completion; ARQ: checkpoint write). A process death
inside that window means completed work whose completion was never recorded.

Randomly-timed kills almost never land in that window - it is milliseconds wide
against a 120 ms unit of work - so a passing random trial establishes nothing.
This module makes the kill land there deliberately, exactly once per run, which
is the only way the zero-conditions in ADR-001 can be falsified rather than
merely un-contradicted.
"""
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKER = HERE / ".crashed"


def maybe_crash(sample_id: int) -> None:
    """Called immediately after the side effect commits and before the engine is
    told the work succeeded. Exits hard - no unwinding, no flush, no reporting."""
    target = os.environ.get("SPIKE_CRASH_AT")
    if target is None or int(target) != sample_id:
        return
    if MARKER.exists():
        return                      # crash once per run, not once per attempt
    MARKER.write_text(str(sample_id), encoding="utf-8")
    os._exit(1)


def reset_marker() -> None:
    if MARKER.exists():
        MARKER.unlink()
