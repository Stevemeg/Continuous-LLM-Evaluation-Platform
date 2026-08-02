"""Login roles for local development.

The schema defines `clep_migration` and `clep_runtime` as NOLOGIN group roles,
which is correct: a group role carries privileges, a login role carries a
password, and conflating them means rotating a credential edits the schema.

This module creates the local login roles and grants them the group roles. It is
development provisioning, not schema, which is why it is here and not in
`docs/data/schema/`.

`NOSUPERUSER NOBYPASSRLS` on the login role is not decoration. ADR-012 D-3 is
satisfied by the *effective* role at connection time; granting `clep_runtime` to
a superuser would satisfy every check in the schema and isolate nothing.
"""
from __future__ import annotations

import psycopg
from psycopg import sql


def ensure_login_roles(conn: psycopg.Connection, runtime_password: str = "") -> None:
    """Idempotent. Safe to call on every local startup.

    An empty password creates the role WITHOUT one, which is correct only where
    the server authenticates by another means — the local compose stack uses
    trust authentication precisely so that no local password exists to be
    committed. Anywhere else, pass one.

    CREATE ROLE is a utility statement, so PostgreSQL will not accept a bound
    parameter for the password. It is composed with `psycopg.sql.Literal`, which
    quotes and escapes it — never with string formatting, which is how a password
    containing a quote turns into an injection.
    """
    exists = conn.execute(
        "SELECT 1 FROM pg_roles WHERE rolname = 'clep_app'").fetchone()
    verb = "ALTER" if exists else "CREATE"
    base = sql.SQL(
        "{verb} ROLE clep_app {opts} LOGIN NOSUPERUSER NOBYPASSRLS "
        "NOCREATEDB NOCREATEROLE").format(
            verb=sql.SQL(verb), opts=sql.SQL("WITH" if exists else ""))
    if runtime_password:
        conn.execute(base + sql.SQL(" PASSWORD {}").format(
            sql.Literal(runtime_password)))
    else:
        conn.execute(base)
    conn.execute("GRANT clep_runtime TO clep_app")
    conn.execute("GRANT USAGE ON SCHEMA clep TO clep_app")


def assert_isolation_preconditions(conn: psycopg.Connection) -> list[str]:
    """Verify at runtime what the schema checker verifies statically.

    A schema can pass every static check and still be deployed to a database
    where the connecting role is a superuser, or owns the tables, or was granted
    BYPASSRLS after the fact. All three silently disable row-level security while
    leaving every policy visibly correct.

    Returns the list of violations. Empty means the four ADR-012 conditions hold
    for the role actually connected.
    """
    problems = []
    row = conn.execute(
        "SELECT current_user, rolsuper, rolbypassrls FROM pg_roles "
        "WHERE rolname = current_user").fetchone()
    user, is_super, bypasses = row
    if is_super:
        problems.append(f"{user} is a superuser and bypasses row-level security")
    if bypasses:
        problems.append(f"{user} has BYPASSRLS and bypasses row-level security")

    owned = conn.execute("""
        SELECT tablename FROM pg_tables
        WHERE schemaname = 'clep' AND tableowner = current_user
        ORDER BY tablename
    """).fetchall()
    if owned:
        problems.append(
            f"{user} owns {len(owned)} table(s) in clep, and owners bypass row "
            f"security unless FORCE is set: {[r[0] for r in owned][:5]}")

    unforced = conn.execute("""
        SELECT c.relname FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'clep' AND c.relkind = 'r'
          AND (c.relrowsecurity IS false OR c.relforcerowsecurity IS false)
          AND c.relname <> 'organization'
        ORDER BY c.relname
    """).fetchall()
    if unforced:
        problems.append(
            f"{len(unforced)} tenant-scoped table(s) lack ENABLE or FORCE row "
            f"level security: {[r[0] for r in unforced][:5]}")
    return problems
