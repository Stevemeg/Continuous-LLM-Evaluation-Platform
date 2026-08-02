"""Candidate C1 — durable workflow engine (Temporal).

BESPOKE STATE MANAGEMENT: the workflow body below carries no resume logic, no
checkpoint table, no "where did I get to" query. The engine replays history to
reconstruct position. That absence is the measurement.
"""
import asyncio
import os
import sys
from datetime import timedelta

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.worker import Worker

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
with workflow.unsafe.imports_passed_through():
    import common
    import crash

TARGET = os.environ.get("SPIKE_TEMPORAL", "localhost:7239")
QUEUE = "clep-spike"
# Read once at module scope. Reading environment inside a workflow would be
# non-deterministic under replay, which Temporal rejects.
ACTIVITY_TIMEOUT = timedelta(seconds=int(os.environ.get("SPIKE_ACTIVITY_TIMEOUT", "10")))


@activity.defn
async def score(run_id: str, sample_id: int, mode: str) -> int:
    result = await asyncio.to_thread(common.score_sample, run_id, sample_id, mode)
    # The side effect is now committed. Temporal has NOT yet been told.
    crash.maybe_crash(sample_id)
    return result


@workflow.defn
class EvaluationRun:
    @workflow.run
    async def run(self, run_id: str, n: int, mode: str) -> int:
        total = 0
        for i in range(n):
            total += await workflow.execute_activity(
                score, args=[run_id, i, mode],
                # Matched to the task queue's job_timeout so that recovery
                # latency is compared on equal configuration, not on defaults.
                start_to_close_timeout=ACTIVITY_TIMEOUT,
            )
        return total


async def main():
    client = await Client.connect(TARGET)
    async with Worker(client, task_queue=QUEUE,
                      workflows=[EvaluationRun], activities=[score]):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
