"""Shared workload and ledger for the durable-execution spike (ADR-001).

Both candidates run the *same* synthetic evaluation run so that the only variable
is the execution engine. The workload mirrors the shape the requirements care
about: a run of N samples, each scored independently, each producing a cost entry.

Two ledger modes exist deliberately:

  naive       - plain INSERT. Measures what the engine gives you for free.
  idempotent  - unique key on (run_id, sample_id) with ON CONFLICT DO NOTHING.
                Measures what is achievable with application-level effort.

Running both is the point. If the naive ledger double-counts under either engine,
then REQ-N-REL-2 is not satisfied by the engine alone, and the difference between
the candidates is not "exactly-once vs not" but "how much bespoke work each needs".
"""
import os
import time

import psycopg

# No password in the default. The spike's PostgreSQL runs with local trust
# authentication, so there is no credential here to leak; a real one is supplied
# through the environment.
DSN = os.environ.get("SPIKE_PG_DSN", "postgresql://postgres@localhost:5439/spike")
N_SAMPLES = int(os.environ.get("SPIKE_N", "40"))
SCORE_MS = int(os.environ.get("SPIKE_SCORE_MS", "120"))


def connect():
    return psycopg.connect(DSN, autocommit=True)


def reset(mode: str):
    """Recreate the ledger. `mode` is 'naive' or 'idempotent'."""
    uniq = (
        "CONSTRAINT uq_cost_entry__run_sample UNIQUE (run_id, sample_id)"
        if mode == "idempotent"
        else "CONSTRAINT uq_cost_entry__never UNIQUE (entry_id)"
    )
    with connect() as c:
        c.execute("DROP TABLE IF EXISTS cost_entry")
        c.execute("DROP TABLE IF EXISTS sample_result")
        c.execute("DROP TABLE IF EXISTS run_checkpoint")
        c.execute(f"""
            CREATE TABLE cost_entry (
                entry_id     bigserial PRIMARY KEY,
                run_id       text        NOT NULL,
                sample_id    integer     NOT NULL,
                cost_units   integer     NOT NULL,
                written_at   timestamptz NOT NULL DEFAULT clock_timestamp(),
                {uniq}
            )""")
        c.execute("""
            CREATE TABLE sample_result (
                run_id      text        NOT NULL,
                sample_id   integer     NOT NULL,
                score       integer     NOT NULL,
                attempt_seq bigserial,
                scored_at   timestamptz NOT NULL DEFAULT clock_timestamp()
            )""")
        # Bespoke checkpoint table. Used only by the task-queue candidate; the
        # durable engine keeps this state itself. Counted as bespoke surface.
        c.execute("""
            CREATE TABLE run_checkpoint (
                run_id           text PRIMARY KEY,
                last_completed   integer NOT NULL,
                updated_at       timestamptz NOT NULL DEFAULT clock_timestamp()
            )""")


def score_sample(run_id: str, sample_id: int, mode: str) -> int:
    """The unit of work. Deliberately does real elapsed work, then writes both a
    result row and a cost entry. `sample_result` is append-only on purpose: a
    second row for the same sample IS the recomputation signal."""
    time.sleep(SCORE_MS / 1000.0)
    score = (sample_id * 7919) % 100
    conflict = "ON CONFLICT DO NOTHING" if mode == "idempotent" else ""
    with connect() as c:
        c.execute(
            "INSERT INTO sample_result (run_id, sample_id, score) VALUES (%s, %s, %s)",
            (run_id, sample_id, score),
        )
        c.execute(
            f"INSERT INTO cost_entry (run_id, sample_id, cost_units) "
            f"VALUES (%s, %s, %s) {conflict}",
            (run_id, sample_id, 10),
        )
    return score


def measure(run_id: str):
    """The three zero-conditions from ADR-001, plus what was actually completed."""
    with connect() as c:
        distinct = c.execute(
            "SELECT count(DISTINCT sample_id) FROM sample_result WHERE run_id=%s",
            (run_id,)).fetchone()[0]
        rows = c.execute(
            "SELECT count(*) FROM sample_result WHERE run_id=%s", (run_id,)).fetchone()[0]
        cost_rows = c.execute(
            "SELECT count(*) FROM cost_entry WHERE run_id=%s", (run_id,)).fetchone()[0]
        cost_units = c.execute(
            "SELECT coalesce(sum(cost_units),0) FROM cost_entry WHERE run_id=%s",
            (run_id,)).fetchone()[0]
    return {
        "samples_completed": distinct,
        "samples_lost": N_SAMPLES - distinct,
        "samples_recomputed": rows - distinct,
        "cost_entries": cost_rows,
        "cost_double_counted": cost_rows - distinct,
        "cost_units": cost_units,
    }


def completed_count(run_id: str) -> int:
    with connect() as c:
        return c.execute(
            "SELECT count(DISTINCT sample_id) FROM sample_result WHERE run_id=%s",
            (run_id,)).fetchone()[0]


def last_write_time(run_id: str):
    with connect() as c:
        return c.execute(
            "SELECT max(scored_at) FROM sample_result WHERE run_id=%s", (run_id,)
        ).fetchone()[0]
