"""Candidate C2 — task queue with explicit checkpointing (ARQ + Redis + PostgreSQL).

Every line that exists solely to provide durability, resume, or idempotency is
tagged `# BESPOKE`. The tag is counted mechanically by the driver, so the
"lines of bespoke state management" measurement is reproducible rather than a
judgement call made afterwards.
"""
import asyncio
import os
import sys

from arq.connections import RedisSettings

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common
import crash

REDIS = RedisSettings(host="localhost", port=int(os.environ.get("SPIKE_REDIS_PORT", "6399")))
JOB_TIMEOUT = int(os.environ.get("SPIKE_TIMEOUT", "10"))


def read_checkpoint(run_id: str) -> int:                                    # BESPOKE
    """Where did this run get to? The engine does not know, so we must."""   # BESPOKE
    with common.connect() as c:                                              # BESPOKE
        row = c.execute(                                                     # BESPOKE
            "SELECT last_completed FROM run_checkpoint WHERE run_id=%s",     # BESPOKE
            (run_id,)).fetchone()                                            # BESPOKE
    return row[0] if row else -1                                             # BESPOKE


def write_checkpoint(run_id: str, sample_id: int) -> None:                   # BESPOKE
    """Advance the checkpoint. Must be monotonic: a redelivered job that has    BESPOKE
    already been overtaken must not move the marker backwards."""             # BESPOKE
    with common.connect() as c:                                               # BESPOKE
        c.execute("""                                                         -- BESPOKE
            INSERT INTO run_checkpoint (run_id, last_completed)               -- BESPOKE
            VALUES (%s, %s)                                                   -- BESPOKE
            ON CONFLICT (run_id) DO UPDATE                                    -- BESPOKE
               SET last_completed = GREATEST(run_checkpoint.last_completed,   -- BESPOKE
                                             EXCLUDED.last_completed),        -- BESPOKE
                   updated_at = clock_timestamp()                             -- BESPOKE
        """, (run_id, sample_id))                                             # BESPOKE


async def evaluation_run(ctx, run_id: str, n: int, mode: str) -> int:
    resume_from = read_checkpoint(run_id) + 1                                 # BESPOKE
    total = 0
    for i in range(resume_from, n):                                           # BESPOKE (range start)
        total += await asyncio.to_thread(common.score_sample, run_id, i, mode)
        # The side effect is now committed. The checkpoint has NOT yet moved.
        crash.maybe_crash(i)
        write_checkpoint(run_id, i)                                           # BESPOKE
    return total


class WorkerSettings:
    functions = [evaluation_run]
    redis_settings = REDIS
    job_timeout = JOB_TIMEOUT
    max_tries = 20
    keep_result = 5
    health_check_interval = 3
    poll_delay = float(os.environ.get("SPIKE_POLL", "0.5"))
