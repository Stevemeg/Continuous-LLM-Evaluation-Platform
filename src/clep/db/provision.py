"""Login roles, and bootstrapping an organization's first principal.

The schema defines `clep_migration` and `clep_runtime` as NOLOGIN group roles,
which is correct: a group role carries privileges, a login role carries a
password, and conflating them means rotating a credential edits the schema.

This module creates the local login roles and grants them the group roles. It is
development provisioning, not schema, which is why it is here and not in
`docs/data/schema/`.

`NOSUPERUSER NOBYPASSRLS` on the login role is not decoration. ADR-012 D-3 is
satisfied by the *effective* role at connection time; granting `clep_runtime` to
a superuser would satisfy every check in the schema and isolate nothing.

Phase 12 adds the second kind of provisioning: creating a tenant's first
principal. It lives here rather than behind a route for a reason ADR-020 rule 5
forces — an organization with no role binding can do nothing at all, so the
first binding cannot itself require a permission. Bootstrapping is therefore an
operator action alongside creating the tenant root, which is where creating an
organization already lives.
"""
from __future__ import annotations

import uuid

import psycopg
from psycopg import sql

from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid
from clep.security import credentials as creds


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


def ensure_user(conn: psycopg.Connection, external_subject: str,
                display_name: str = "") -> str:
    """A global identity, created once and reused across organizations.

    Idempotent on `external_subject`, because the same person joining a second
    organization must not become a second person — which is the whole reason
    `app_user` carries no `organization_id`.
    """
    row = conn.execute("SELECT id FROM clep.app_user WHERE external_subject = %s",
                       (external_subject,)).fetchone()
    if row:
        return uuid_to_ulid(row[0])
    user_id = new_ulid()
    conn.execute(
        "INSERT INTO clep.app_user (id, external_subject, display_name) "
        "VALUES (%s, %s, %s)",
        (ulid_to_uuid(user_id), external_subject,
         display_name or external_subject))
    return user_id


def ensure_membership(conn: psycopg.Connection, organization_id: str,
                      user_id: str) -> None:
    conn.execute(
        "INSERT INTO clep.membership (id, organization_id, app_user_id) "
        "VALUES (%s, %s, %s) ON CONFLICT (organization_id, app_user_id) "
        "DO NOTHING",
        (uuid.uuid4(), str(organization_id), ulid_to_uuid(user_id)))


def grant_role(conn: psycopg.Connection, organization_id: str, *,
               role_slug: str, principal_kind: str, subject_id: str,
               actor_id: str, scope_kind: str = "organization",
               project_id: str | None = None) -> str:
    binding_id = new_ulid()
    conn.execute(
        "INSERT INTO clep.role_binding (id, organization_id, role_slug, "
        "principal_kind, app_user_id, service_account_id, scope_kind, "
        "project_id, created_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (ulid_to_uuid(binding_id), str(organization_id), role_slug,
         principal_kind,
         ulid_to_uuid(subject_id) if principal_kind == "user" else None,
         ulid_to_uuid(subject_id) if principal_kind == "service_account" else None,
         scope_kind, ulid_to_uuid(project_id) if project_id else None,
         ulid_to_uuid(actor_id)))
    return binding_id


def issue_bootstrap_key(conn: psycopg.Connection, organization_id: str, *,
                        user_id: str, display_name: str = "bootstrap") -> str:
    """The organization's first credential. Returns the presented string once.

    Written here rather than through `SecurityRepository` because that class
    runs inside a tenant session under the runtime role, and this runs under the
    provisioning role before any credential exists to open one with. The stored
    shape is identical — the secret is not stored, only a verifier over a salt.
    """
    minted = creds.mint(uuid_to_ulid(uuid.UUID(str(organization_id))))
    conn.execute(
        "INSERT INTO clep.api_key (id, organization_id, principal_kind, "
        "app_user_id, display_name, verifier, salt, kdf, kdf_iterations, "
        "created_by) VALUES (%s, %s, 'user', %s, %s, %s, %s, %s, %s, %s)",
        (ulid_to_uuid(minted.key_id), str(organization_id),
         ulid_to_uuid(user_id), display_name, minted.verifier, minted.salt,
         minted.kdf, minted.kdf_iterations, ulid_to_uuid(user_id)))
    return minted.presented


def bootstrap_organization(conn: psycopg.Connection, organization_id: str, *,
                           external_subject: str,
                           role_slug: str = "owner") -> tuple[str, str]:
    """User, membership, role binding and first credential, in one call.

    Returns `(user_id, presented_credential)`. The credential is the only copy;
    nothing here or in the store can produce it again (I-2).
    """
    user_id = ensure_user(conn, external_subject)
    ensure_membership(conn, organization_id, user_id)
    grant_role(conn, organization_id, role_slug=role_slug, principal_kind="user",
               subject_id=user_id, actor_id=user_id)
    return user_id, issue_bootstrap_key(conn, organization_id, user_id=user_id)


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
