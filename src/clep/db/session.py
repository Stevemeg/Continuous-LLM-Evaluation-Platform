"""Database access, and the one place tenant context is established.

ADR-010 rule 3: tenant context is set once at the ingress boundary and never
re-derived from caller-supplied data deeper in the stack. This module is that
boundary. Every runtime connection is opened through `tenant_session`, which
sets `clep.organization_id` for the transaction and unsets it afterwards.

The context is set with `set_config(..., is_local => true)`, so it lives for the
transaction and cannot leak to the next user of a pooled connection — a leak that
would present as one tenant reading another's rows, with no error anywhere.
"""
from __future__ import annotations

import contextlib
import uuid
from typing import Iterator

import psycopg

TENANT_SETTING = "clep.organization_id"


class TenantContextError(RuntimeError):
    """Raised when work is attempted without a tenant context."""


@contextlib.contextmanager
def tenant_session(dsn: str, organization_id: uuid.UUID | str) -> Iterator[psycopg.Connection]:
    """A transaction bound to exactly one tenant.

    Committed on success, rolled back on any exception. The context is set
    inside the transaction so that a rollback also discards it.
    """
    if organization_id is None:
        raise TenantContextError(
            "refusing to open a session with no tenant; a NULL context matches "
            "no rows and would look like an empty result rather than an error")
    org = str(organization_id)
    # Validate before it reaches the database. set_config takes text, so a
    # malformed value would otherwise fail later and less clearly.
    uuid.UUID(org)
    conn = psycopg.connect(dsn)
    try:
        with conn.transaction():
            conn.execute("SELECT set_config(%s, %s, true)", (TENANT_SETTING, org))
            yield conn
    finally:
        conn.close()


@contextlib.contextmanager
def directory_session(dsn: str) -> Iterator[psycopg.Connection]:
    """A runtime session with no tenant context, for the one read that has none.

    A scheduler has no request and therefore no principal, so it cannot be told
    which tenant it is acting for. It has to ask which tenants exist and then
    open a real `tenant_session` for each — which is ADR-010 rule 3 obeyed, not
    bypassed: the ingress here is the schedule sweep, and the context is still
    established once per tenant before any tenant-scoped work.

    What makes this safe is not discipline. It is the runtime role: every
    tenant-scoped table has FORCE ROW LEVEL SECURITY and a policy comparing
    `organization_id` with the context, and an unset context is NULL, which
    compares equal to nothing. So this session can read `clep.organization` — the
    tenant root, which has no policy because it *is* the tenant — and can read
    nothing else, by construction. Using it for anything further returns an empty
    result rather than another tenant's rows.
    """
    conn = psycopg.connect(dsn)
    try:
        with conn.transaction():
            yield conn
    finally:
        conn.close()


def known_organizations(dsn: str) -> list[str]:
    """Every tenant, for a sweep that must consider all of them."""
    with directory_session(dsn) as conn:
        return [str(r[0]) for r in
                conn.execute("SELECT id FROM clep.organization ORDER BY id")]


@contextlib.contextmanager
def admin_session(dsn: str) -> Iterator[psycopg.Connection]:
    """A session with no tenant context, for migrations and provisioning only.

    Deliberately separate and deliberately named. Nothing that serves a request
    may use this: without a tenant context every policy matches no rows, so the
    only way this is useful is with a role that bypasses them.
    """
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        yield conn
    finally:
        conn.close()


def current_tenant(conn: psycopg.Connection) -> str | None:
    row = conn.execute(
        "SELECT NULLIF(current_setting(%s, true), '')", (TENANT_SETTING,)).fetchone()
    return row[0] if row else None
