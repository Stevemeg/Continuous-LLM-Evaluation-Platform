"""Migrations, applied directly from the schema specification.

`REQ-N-OPS-3` requires migrations. Schema governance requires that implementation
must not redefine the schema. Both are satisfied by not having two artifacts:
the files in `docs/data/schema/` are the specification AND the migration set, and
this runner applies them in filename order.

A framework that generates migrations by diffing models against a database would
invert that — the models would become the authority and the specification would
become documentation of whatever the models happened to produce.

Two rules make this safe:

  1. A file that has been applied is recorded with the SHA-256 of its contents.
     If that file later changes, the runner refuses to proceed. Editing applied
     history is how two environments silently diverge.
  2. Migrations run as the migration role, which owns the schema. The runtime
     role never runs them, and does not own the tables it reads (ADR-012 D-4).
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS clep_schema_history (
    filename     text PRIMARY KEY,
    content_sha  text        NOT NULL,
    applied_at   timestamptz NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    pass


@dataclass(frozen=True)
class Migration:
    filename: str
    sql: str

    @property
    def content_sha(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(schema_dir: Path) -> list[Migration]:
    files = sorted(p for p in schema_dir.glob("*.sql"))
    if not files:
        raise MigrationError(f"no schema files in {schema_dir}")
    for p in files:
        if not re.match(r"^\d{2}-", p.name):
            raise MigrationError(
                f"{p.name} is not ordered; schema files are applied in filename "
                f"order and must begin with two digits")
    return [Migration(p.name, p.read_text(encoding="utf-8")) for p in files]


def applied(conn: psycopg.Connection) -> dict[str, str]:
    conn.execute(HISTORY_DDL)
    return {r[0]: r[1] for r in
            conn.execute("SELECT filename, content_sha FROM clep_schema_history")}


def plan(conn: psycopg.Connection, migrations: list[Migration]) -> list[Migration]:
    """What still needs applying — and a hard stop if history has been edited."""
    seen = applied(conn)
    changed = [m.filename for m in migrations
               if m.filename in seen and seen[m.filename] != m.content_sha]
    if changed:
        raise MigrationError(
            f"already-applied schema file(s) have changed since they were "
            f"applied: {changed}. Add a new file rather than editing history; "
            f"editing it makes two environments diverge without any error.")
    return [m for m in migrations if m.filename not in seen]


def apply(conn: psycopg.Connection, migrations: list[Migration]) -> list[str]:
    """Apply each pending file in its own transaction.

    Per file rather than all-or-nothing on purpose: PostgreSQL rolls back a
    failed statement's whole transaction, and a half-applied 300-line file is
    far harder to reason about than a clearly-recorded stopping point.
    """
    done = []
    for m in plan(conn, migrations):
        try:
            with conn.transaction():
                conn.execute(m.sql)
                conn.execute(
                    "INSERT INTO clep_schema_history (filename, content_sha) "
                    "VALUES (%s, %s)", (m.filename, m.content_sha))
        except psycopg.Error as e:
            raise MigrationError(f"{m.filename} failed to apply: {e}") from e
        done.append(m.filename)
    return done


def schema_dir(root: Path) -> Path:
    return root / "docs" / "data" / "schema"
