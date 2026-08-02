"""Spike S-1b - is the resume-latency gap fundamental or configuration?

The main S-1 run measured Temporal resuming in ~9.7 s and ARQ in ~17.4 s under a
matched 10 s timeout. Reporting that as an engine property would be a claim the
experiment does not support: both engines detect worker loss by *timeout*, and a
timeout is a setting.

This varies the timeout on both candidates. If both track the setting, the gap is
configuration and must not be used as a decision input. If one floors out, the
floor is a real property and belongs in the ADR.
"""
import asyncio
import json
import os
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
sys.stdout.reconfigure(encoding="utf-8")


def spawn(cmd, env_extra):
    env = dict(os.environ)
    env.pop("SPIKE_CRASH_AT", None)
    env.update(env_extra)
    return subprocess.Popen(cmd, cwd=str(HERE), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


async def wait_for(pred, timeout, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return True
        await asyncio.sleep(interval)
    return False


async def temporal_resume(timeout_s, run_id):
    from temporalio.client import Client
    from cand_temporal_worker import EvaluationRun, QUEUE, TARGET
    common.reset("idempotent"); crash.reset_marker()
    client = await Client.connect(TARGET)
    env = {"SPIKE_ACTIVITY_TIMEOUT": str(timeout_s)}
    w = spawn([PY, "cand_temporal_worker.py"], env)
    await asyncio.sleep(4)
    h = await client.start_workflow(EvaluationRun.run, args=[run_id, N, "idempotent"],
                                    id=run_id, task_queue=QUEUE)
    await wait_for(lambda: common.completed_count(run_id) >= 8, 60)
    w.kill(); w.wait()
    await asyncio.sleep(1.0)
    stalled = common.completed_count(run_id)
    w2 = spawn([PY, "cand_temporal_worker.py"], env)
    t0 = time.time()
    ok = await wait_for(lambda: common.completed_count(run_id) > stalled, 120)
    lat = round(time.time() - t0, 2) if ok else None
    try:
        await asyncio.wait_for(h.result(), timeout=120)
    except Exception:
        pass
    w2.kill(); w2.wait()
    return lat


async def arq_resume(timeout_s, poll_s, run_id):
    from arq import create_pool
    from cand_arq_worker import REDIS
    common.reset("idempotent"); crash.reset_marker()
    pool = await create_pool(REDIS); await pool.flushall()
    env = {"SPIKE_TIMEOUT": str(timeout_s), "SPIKE_POLL": str(poll_s)}
    cmd = [PY, "-m", "arq", "cand_arq_worker.WorkerSettings"]
    w = spawn(cmd, env)
    await asyncio.sleep(3)
    await pool.enqueue_job("evaluation_run", run_id, N, "idempotent", _job_id=run_id)
    await wait_for(lambda: common.completed_count(run_id) >= 8, 60)
    w.kill(); w.wait()
    await asyncio.sleep(1.0)
    stalled = common.completed_count(run_id)
    w2 = spawn(cmd, env)
    t0 = time.time()
    ok = await wait_for(lambda: common.completed_count(run_id) > stalled, 120)
    lat = round(time.time() - t0, 2) if ok else None
    await wait_for(lambda: common.completed_count(run_id) >= N, 120, 0.2)
    w2.kill(); w2.wait(); await pool.aclose()
    return lat


async def main():
    print("=" * 78)
    print("SPIKE S-1b - RESUME LATENCY AS A FUNCTION OF THE DETECTION TIMEOUT")
    print("=" * 78)
    print("Both engines detect worker loss by timeout. If latency tracks the")
    print("setting, the gap measured in S-1 is configuration, not an engine property.")
    print()
    rows = []
    for t in (3, 6, 10):
        c1 = await temporal_resume(t, f"L-C1-{t}")
        c2 = await arq_resume(t, max(0.5, t / 10), f"L-C2-{t}")
        rows.append({"timeout_s": t, "C1_temporal_s": c1, "C2_arq_s": c2})
        print(f"  timeout {t:>2}s   C1 Temporal resume {str(c1):>6}s   "
              f"C2 ARQ resume {str(c2):>6}s")
    (HERE / "s1b-results.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print()
    tracks = all(r["C1_temporal_s"] is not None and r["C2_arq_s"] is not None
                 for r in rows)
    if tracks:
        c1v = [r["C1_temporal_s"] for r in rows]
        c2v = [r["C2_arq_s"] for r in rows]
        print(f"  C1 range {min(c1v)}-{max(c1v)}s across timeouts {[r['timeout_s'] for r in rows]}")
        print(f"  C2 range {min(c2v)}-{max(c2v)}s across the same settings")
        print()
        print("  Interpretation is stated in the ADR, not here: this script reports")
        print("  numbers, and whether they track the setting is visible above.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
