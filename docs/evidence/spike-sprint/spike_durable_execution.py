"""Spike S-1 - durable execution (ADR-001).

ADR-001 specified a synthetic evaluation run, a deliberate worker kill at a
mid-run checkpoint, and a deliberate duplicate submission, measured against three
zero-conditions: no completed sample recomputed, no sample lost, no cost entry
double-counted.

This script runs that specification against two candidates:

  C1  durable workflow engine        - Temporal, dev server in a container
  C2  task queue + explicit checkpointing - ARQ over Redis, checkpoint in PostgreSQL

and under two ledger modes:

  naive       - plain INSERT, no uniqueness. What the engine gives you for free.
  idempotent  - unique (run_id, sample_id), ON CONFLICT DO NOTHING.

TWO FAULT REGIMES, and the difference between them is the whole point.

  Regime A - randomly timed kill. The worker is hard-killed once the run is
             demonstrably in flight. This is the fault ADR-001 literally named.
             It is also weak evidence: the window in which a kill can destroy
             durability is the few milliseconds between "side effect committed"
             and "engine told", against a 120 ms unit of work. A randomly timed
             kill almost never lands there, so passing Regime A does not show
             the zero-conditions hold - only that they were not contradicted.

  Regime B - deliberate crash inside that window. The worker exits hard, with no
             unwinding, immediately after the database commit and before the
             engine records completion. This is the trial that can actually
             falsify the zero-conditions, and it is the trial the decision rests
             on. A candidate that passes Regime A and fails Regime B has not
             satisfied REQ-N-REL-2; it has merely not been asked the question.

Usage:  python spike_durable_execution.py
Requires: PostgreSQL, Redis and a Temporal dev server. See spike README.
"""
import asyncio
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common   # noqa: E402
import crash    # noqa: E402

PY = sys.executable
N = common.N_SAMPLES
TRIALS = int(os.environ.get("SPIKE_TRIALS", "3"))
CRASH_AT = int(os.environ.get("SPIKE_CRASH_SAMPLE", "15"))
sys.stdout.reconfigure(encoding="utf-8")
random.seed(20260802)


def log(m=""):
    print(m, flush=True)


def bespoke_lines(path: Path) -> int:
    return sum(1 for ln in path.read_text(encoding="utf-8").splitlines()
               if re.search(r"(#|--) BESPOKE", ln))


def spawn(cmd, env_extra=None):
    env = dict(os.environ)
    env.pop("SPIKE_CRASH_AT", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.Popen(cmd, cwd=str(HERE), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


TEMPORAL_CMD = [PY, "cand_temporal_worker.py"]
ARQ_CMD = [PY, "-m", "arq", "cand_arq_worker.WorkerSettings"]


async def wait_for(pred, timeout, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return False


# --------------------------------------------------------------------- C1
async def run_temporal(mode, run_id, regime):
    from temporalio.client import Client
    from cand_temporal_worker import EvaluationRun, QUEUE, TARGET

    common.reset(mode)
    crash.reset_marker()
    client = await Client.connect(TARGET)

    env = {"SPIKE_CRASH_AT": str(CRASH_AT)} if regime == "B" else None
    worker = spawn(TEMPORAL_CMD, env)
    await asyncio.sleep(4)

    handle = await client.start_workflow(
        EvaluationRun.run, args=[run_id, N, mode], id=run_id, task_queue=QUEUE)
    try:
        await client.start_workflow(
            EvaluationRun.run, args=[run_id, N, mode], id=run_id, task_queue=QUEUE)
        dup = "ACCEPTED - second execution started"
    except Exception as e:
        dup = f"rejected ({type(e).__name__})"

    if regime == "A":
        target = random.randint(5, N // 2)
        await wait_for(lambda: common.completed_count(run_id) >= target, 90)
        await asyncio.sleep(random.uniform(0, common.SCORE_MS / 1000.0))
        at_kill = common.completed_count(run_id)
        worker.kill(); worker.wait()
    else:
        # the worker kills itself inside the vulnerable window
        await asyncio.to_thread(worker.wait, 120)
        at_kill = common.completed_count(run_id)

    killed_at = time.time()
    await asyncio.sleep(1.5)
    stalled = common.completed_count(run_id)

    worker2 = spawn(TEMPORAL_CMD)
    restart_at = time.time()
    resumed = await wait_for(lambda: common.completed_count(run_id) > stalled, 180)
    resumed_at = time.time() if resumed else None

    try:
        await asyncio.wait_for(handle.result(), timeout=180)
        finished = True
    except Exception as e:
        log(f"      workflow did not finish: {type(e).__name__}")
        finished = False
    worker2.kill(); worker2.wait()

    m = common.measure(run_id)
    m.update(candidate="C1", engine="Temporal", mode=mode, regime=regime,
             killed_after=at_kill, duplicate_submission=dup, finished=finished,
             resume_latency_s=round(resumed_at - restart_at, 2) if resumed else None,
             outage_s=round(resumed_at - killed_at, 2) if resumed else None,
             bespoke_loc=bespoke_lines(HERE / "cand_temporal_worker.py"))
    return m


# --------------------------------------------------------------------- C2
async def run_arq(mode, run_id, regime):
    from arq import create_pool
    from cand_arq_worker import REDIS

    common.reset(mode)
    crash.reset_marker()
    pool = await create_pool(REDIS)
    await pool.flushall()

    env = {"SPIKE_CRASH_AT": str(CRASH_AT)} if regime == "B" else None
    worker = spawn(ARQ_CMD, env)
    await asyncio.sleep(3)

    await pool.enqueue_job("evaluation_run", run_id, N, mode, _job_id=run_id)
    dup_job = await pool.enqueue_job("evaluation_run", run_id, N, mode, _job_id=run_id)
    dup = "rejected (duplicate job id)" if dup_job is None else "ACCEPTED - second job queued"

    if regime == "A":
        target = random.randint(5, N // 2)
        await wait_for(lambda: common.completed_count(run_id) >= target, 90)
        await asyncio.sleep(random.uniform(0, common.SCORE_MS / 1000.0))
        at_kill = common.completed_count(run_id)
        worker.kill(); worker.wait()
    else:
        await asyncio.to_thread(worker.wait, 120)
        at_kill = common.completed_count(run_id)

    killed_at = time.time()
    await asyncio.sleep(1.5)
    stalled = common.completed_count(run_id)

    worker2 = spawn(ARQ_CMD)
    restart_at = time.time()
    resumed = await wait_for(lambda: common.completed_count(run_id) > stalled, 180)
    resumed_at = time.time() if resumed else None
    finished = await wait_for(lambda: common.completed_count(run_id) >= N, 180, 0.2)
    await asyncio.sleep(1)
    worker2.kill(); worker2.wait()
    await pool.aclose()

    m = common.measure(run_id)
    m.update(candidate="C2", engine="ARQ+Redis", mode=mode, regime=regime,
             killed_after=at_kill, duplicate_submission=dup, finished=finished,
             resume_latency_s=round(resumed_at - restart_at, 2) if resumed else None,
             outage_s=round(resumed_at - killed_at, 2) if resumed else None,
             bespoke_loc=bespoke_lines(HERE / "cand_arq_worker.py"))
    return m


RUNNERS = {"C1": run_temporal, "C2": run_arq}


def verdict_row(r):
    ok = (r["samples_lost"] == 0 and r["samples_recomputed"] == 0
          and r["cost_double_counted"] == 0 and r["finished"])
    return "PASS" if ok else "FAIL"


async def main():
    log("=" * 78)
    log("SPIKE S-1 - DURABLE EXECUTION (ADR-001)")
    log("=" * 78)
    log(f"samples per run        : {N}")
    log(f"unit of work           : {common.SCORE_MS} ms, then one result row and one cost row")
    log(f"regime A trials        : {TRIALS} per candidate, randomly timed hard kill")
    log(f"regime B               : 1 per candidate per ledger mode, crash inside the")
    log(f"                         window between commit and completion (sample {CRASH_AT})")
    log("zero-conditions        : samples_lost = 0, samples_recomputed = 0, "
        "cost_double_counted = 0")
    log()

    out = []

    log("-" * 78)
    log("REGIME A - randomly timed worker loss (ledger: naive)")
    log("-" * 78)
    for cand in ("C1", "C2"):
        for t in range(TRIALS):
            r = await RUNNERS[cand]("naive", f"A-{cand}-{t}", "A")
            r["trial"] = t
            out.append(r)
            log(f"  {cand} trial {t}: killed after {r['killed_after']:>2} samples  "
                f"lost={r['samples_lost']} recomputed={r['samples_recomputed']} "
                f"double_cost={r['cost_double_counted']} "
                f"resume={r['resume_latency_s']}s  [{verdict_row(r)}]")
    log()

    log("-" * 78)
    log("REGIME B - crash inside the commit-to-completion window")
    log("-" * 78)
    for cand in ("C1", "C2"):
        for mode in ("naive", "idempotent"):
            r = await RUNNERS[cand](mode, f"B-{cand}-{mode}", "B")
            out.append(r)
            log(f"  {cand} ledger={mode:<11} lost={r['samples_lost']} "
                f"recomputed={r['samples_recomputed']} "
                f"double_cost={r['cost_double_counted']} "
                f"cost_units={r['cost_units']} (expected {10 * N})  "
                f"[{verdict_row(r)}]")
    log()

    (HERE / "s1-results.json").write_text(json.dumps(out, indent=2), encoding="utf-8")

    log("-" * 78)
    log("SUMMARY")
    log("-" * 78)
    for cand, engine in (("C1", "Temporal"), ("C2", "ARQ+Redis")):
        a = [r for r in out if r["candidate"] == cand and r["regime"] == "A"]
        b = {r["mode"]: r for r in out if r["candidate"] == cand and r["regime"] == "B"}
        lat = [r["resume_latency_s"] for r in a if r["resume_latency_s"] is not None]
        log(f"{cand} {engine}")
        log(f"   regime A ({len(a)} trials)      : "
            f"{sum(1 for r in a if verdict_row(r) == 'PASS')}/{len(a)} PASS")
        log(f"   regime B naive ledger      : {verdict_row(b['naive'])}  "
            f"recomputed={b['naive']['samples_recomputed']} "
            f"double_cost={b['naive']['cost_double_counted']}")
        log(f"   regime B idempotent ledger : {verdict_row(b['idempotent'])}  "
            f"recomputed={b['idempotent']['samples_recomputed']} "
            f"double_cost={b['idempotent']['cost_double_counted']}")
        log(f"   resume latency             : median {statistics.median(lat):.2f}s  "
            f"range {min(lat):.2f}-{max(lat):.2f}s")
        log(f"   bespoke state-management   : {a[0]['bespoke_loc']} tagged lines")
        log(f"   duplicate submission       : {a[0]['duplicate_submission']}")
        log()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
