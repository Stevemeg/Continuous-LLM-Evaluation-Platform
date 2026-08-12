"""Persistence for principals, credentials, bindings and governance policy.

Two rules from elsewhere shape this module.

ADR-010 rule 1 puts isolation in the store, so every read here happens inside a
tenant session and none of them adds a tenant predicate of its own. That is why
`authenticate` opens its session on the organization the credential *names*: the
lookup has to be inside a tenant context, and the context the request has not yet
earned is exactly the one the credential is claiming. Row-level security then
decides whether the claim was true — a key that lives elsewhere is invisible
here, so the claim is proven rather than believed (ADR-019 rule 3).

I-3 puts revocation in a state column rather than in a DELETE, so nothing in this
module deletes anything. An audit trail that names a credential which no longer
exists is an audit trail an auditor cannot follow.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import psycopg

from clep.db.session import tenant_session
from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid
from clep.security import credentials as creds
from clep.security.rbac import Authorization, Grant


class SecurityError(RuntimeError):
    pass


class AuthenticationError(RuntimeError):
    """One class, one message (ADR-019 rule 11).

    The specific reason — malformed, unknown, revoked, expired, wrong secret —
    is recorded for the operator in `reason`, which reaches the audit trail and
    never the response.
    """

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__("credential rejected")


@dataclass(frozen=True)
class Principal:
    """Who is acting (ADR-019 rule 9).

    `kind` is carried because an auditor reading an action needs to know whether
    a person or a pipeline performed it, and because `REQ-F-12-3` asks for
    service credentials specifically.
    """
    organization_id: str
    subject: str
    kind: str
    api_key_id: str


@dataclass(frozen=True)
class ApiKeyRow:
    id: str
    display_name: str
    principal_kind: str
    subject: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    revocation_reason: str | None
    rotated_to: str | None

    @property
    def state(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and self.expires_at <= _now():
            return "expired"
        return "active"


@dataclass(frozen=True)
class RoleBindingRow:
    id: str
    role_slug: str
    principal_kind: str
    subject: str
    scope_kind: str
    project_id: str | None
    state: str
    created_at: datetime


@dataclass(frozen=True)
class RetentionPolicyRow:
    decision_retention_days: int
    content_retention_days: int
    audit_retention_days: int
    updated_at: datetime


@dataclass(frozen=True)
class UsageLimitRow:
    requests_per_minute: int
    runs_per_period: int
    period_days: int


#: What a tenant that has never configured a limit gets. ADR-021 rule 6: there
#: is no value meaning unlimited, so there is a default rather than an absence.
#: Chosen generously enough that a test suite driving the API does not have to
#: know about it, and low enough to bound an abusive caller.
DEFAULT_USAGE_LIMIT = UsageLimitRow(requests_per_minute=600,
                                    runs_per_period=1000, period_days=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SecurityRepository:
    """Everything Phase 12 stores, inside one tenant's session."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    # ---- principals ------------------------------------------------------
    def create_service_account(self, *, slug: str, display_name: str,
                               actor_id: str) -> dict:
        account_id = new_ulid()
        try:
            self._conn.execute(
                "INSERT INTO clep.service_account (id, organization_id, slug, "
                "display_name, created_by) VALUES (%s, %s, %s, %s, %s)",
                (ulid_to_uuid(account_id), self._org, slug, display_name,
                 ulid_to_uuid(actor_id) if _looks_like_ulid(actor_id)
                 else uuid.UUID(int=0)))
        except psycopg.errors.UniqueViolation as exc:
            raise SecurityError(
                f"a service account named {slug!r} already exists in this "
                f"organization") from exc
        return {"id": account_id, "slug": slug, "displayName": display_name,
                "state": "active"}

    def service_account_exists(self, account_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM clep.service_account WHERE organization_id = %s "
            "AND id = %s AND state = 'active'",
            (self._org, ulid_to_uuid(account_id))).fetchone() is not None

    def user_is_a_member(self, user_id: str) -> bool:
        return self._conn.execute(
            "SELECT 1 FROM clep.membership WHERE organization_id = %s "
            "AND app_user_id = %s AND state = 'active'",
            (self._org, ulid_to_uuid(user_id))).fetchone() is not None

    # ---- credentials -----------------------------------------------------
    def issue_api_key(self, *, principal_kind: str, subject_id: str,
                      display_name: str, actor_id: str,
                      expires_at: datetime | None = None,
                      key_id: str | None = None) -> tuple[str, str]:
        """Returns `(key_id, presented)`. The second value is never stored.

        The caller is expected to hand `presented` straight to the response and
        keep no other copy: I-2 makes this the only moment it exists inside the
        platform.
        """
        if principal_kind not in ("user", "service_account"):
            raise SecurityError(f"unknown principal kind {principal_kind!r}")
        if principal_kind == "service_account":
            if not self.service_account_exists(subject_id):
                raise SecurityError("no such service account")
        elif not self.user_is_a_member(subject_id):
            raise SecurityError(
                "the user is not an active member of this organization")
        minted = creds.mint(uuid_to_ulid(uuid.UUID(self._org)),
                            key_id=key_id or new_ulid())
        self._conn.execute(
            "INSERT INTO clep.api_key (id, organization_id, principal_kind, "
            "app_user_id, service_account_id, display_name, verifier, salt, "
            "kdf, kdf_iterations, created_by, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(minted.key_id), self._org, principal_kind,
             ulid_to_uuid(subject_id) if principal_kind == "user" else None,
             ulid_to_uuid(subject_id) if principal_kind == "service_account"
             else None,
             display_name, minted.verifier, minted.salt, minted.kdf,
             minted.kdf_iterations, ulid_to_uuid(actor_id), expires_at))
        return minted.key_id, minted.presented

    def list_api_keys(self) -> list[ApiKeyRow]:
        rows = self._conn.execute(
            "SELECT k.id, k.display_name, k.principal_kind, "
            "       COALESCE(u.external_subject, s.slug), k.created_at, "
            "       k.expires_at, k.revoked_at, k.revocation_reason, "
            "       k.rotated_to_api_key_id "
            "  FROM clep.api_key k "
            "  LEFT JOIN clep.app_user u ON u.id = k.app_user_id "
            "  LEFT JOIN clep.service_account s ON s.id = k.service_account_id "
            " WHERE k.organization_id = %s ORDER BY k.created_at DESC, k.id",
            (self._org,)).fetchall()
        return [ApiKeyRow(id=uuid_to_ulid(r[0]), display_name=r[1],
                          principal_kind=r[2], subject=r[3] or "unknown",
                          created_at=r[4], expires_at=r[5], revoked_at=r[6],
                          revocation_reason=r[7],
                          rotated_to=uuid_to_ulid(r[8]) if r[8] else None)
                for r in rows]

    def revoke_api_key(self, key_id: str, *, reason: str = "revoked") -> bool:
        """Idempotent by design: revoking an already-revoked key changes nothing
        and is not an error, because a caller retrying a revocation is a caller
        doing the right thing twice."""
        row = self._conn.execute(
            "UPDATE clep.api_key SET revoked_at = now(), revocation_reason = %s "
            " WHERE organization_id = %s AND id = %s AND revoked_at IS NULL "
            "RETURNING id", (reason, self._org, ulid_to_uuid(key_id))).fetchone()
        if row is not None:
            return True
        return self._conn.execute(
            "SELECT 1 FROM clep.api_key WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(key_id))).fetchone() is not None

    def rotate_api_key(self, key_id: str, *, actor_id: str) -> tuple[str, str]:
        """A new key, and the old one revoked, in the caller's transaction.

        ADR-019 rule 8: rotation is never "change the secret of this key". An
        identifier whose secret changed would make every audit record ambiguous
        about which credential performed the action.
        """
        row = self._conn.execute(
            "SELECT principal_kind, app_user_id, service_account_id, "
            "       display_name, expires_at, revoked_at "
            "  FROM clep.api_key WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(key_id))).fetchone()
        if row is None:
            raise SecurityError("no such api key")
        kind, user_id, account_id, display_name, expires_at, revoked_at = row
        if revoked_at is not None:
            raise SecurityError(
                "the key has already been revoked; rotation replaces a live "
                "credential, and replacing a dead one would issue authority "
                "nobody currently holds")
        subject = uuid_to_ulid(user_id if kind == "user" else account_id)
        new_key_id, presented = self.issue_api_key(
            principal_kind=kind, subject_id=subject,
            display_name=display_name, actor_id=actor_id, expires_at=expires_at)
        self._conn.execute(
            "UPDATE clep.api_key SET revoked_at = now(), "
            "       revocation_reason = 'rotated', rotated_to_api_key_id = %s "
            " WHERE organization_id = %s AND id = %s",
            (ulid_to_uuid(new_key_id), self._org, ulid_to_uuid(key_id)))
        return new_key_id, presented

    # ---- authorization ---------------------------------------------------
    def create_role_binding(self, *, role_slug: str, principal_kind: str,
                            subject_id: str, scope_kind: str,
                            project_id: str | None, actor_id: str) -> dict:
        if scope_kind not in ("organization", "project"):
            raise SecurityError(f"unknown scope {scope_kind!r}")
        if (scope_kind == "project") != bool(project_id):
            raise SecurityError(
                "a project-scoped binding needs a project, and an "
                "organization-scoped one must not name a project")
        if self._conn.execute("SELECT 1 FROM clep.role WHERE slug = %s",
                              (role_slug,)).fetchone() is None:
            raise SecurityError(f"no such role {role_slug!r}")
        if principal_kind == "service_account":
            if not self.service_account_exists(subject_id):
                raise SecurityError("no such service account")
        elif principal_kind == "user":
            if not self.user_is_a_member(subject_id):
                raise SecurityError(
                    "the user is not an active member of this organization")
        else:
            raise SecurityError(f"unknown principal kind {principal_kind!r}")
        binding_id = new_ulid()
        try:
            self._conn.execute(
                "INSERT INTO clep.role_binding (id, organization_id, role_slug, "
                "principal_kind, app_user_id, service_account_id, scope_kind, "
                "project_id, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (ulid_to_uuid(binding_id), self._org, role_slug, principal_kind,
                 ulid_to_uuid(subject_id) if principal_kind == "user" else None,
                 ulid_to_uuid(subject_id) if principal_kind == "service_account"
                 else None,
                 scope_kind,
                 ulid_to_uuid(project_id) if project_id else None,
                 ulid_to_uuid(actor_id)))
        except psycopg.errors.UniqueViolation as exc:
            raise SecurityError(
                "that principal already holds that role in that scope") from exc
        except psycopg.errors.ForeignKeyViolation as exc:
            raise SecurityError("no such project") from exc
        return {"id": binding_id, "role": role_slug, "scope": scope_kind,
                "projectId": project_id, "state": "active"}

    def list_role_bindings(self) -> list[RoleBindingRow]:
        rows = self._conn.execute(
            "SELECT b.id, b.role_slug, b.principal_kind, "
            "       COALESCE(u.external_subject, s.slug), b.scope_kind, "
            "       b.project_id, b.state, b.created_at "
            "  FROM clep.role_binding b "
            "  LEFT JOIN clep.app_user u ON u.id = b.app_user_id "
            "  LEFT JOIN clep.service_account s ON s.id = b.service_account_id "
            " WHERE b.organization_id = %s ORDER BY b.created_at DESC, b.id",
            (self._org,)).fetchall()
        return [RoleBindingRow(id=uuid_to_ulid(r[0]), role_slug=r[1],
                               principal_kind=r[2], subject=r[3] or "unknown",
                               scope_kind=r[4],
                               project_id=uuid_to_ulid(r[5]) if r[5] else None,
                               state=r[6], created_at=r[7])
                for r in rows]

    def revoke_role_binding(self, binding_id: str) -> bool:
        """The last administrative binding is refused by the store, not here.

        `psycopg.errors.RestrictViolation` is the trigger in
        `12-identity-and-access.sql` speaking; it is translated rather than
        caught-and-ignored, so the reason reaches the caller intact.
        """
        try:
            row = self._conn.execute(
                "UPDATE clep.role_binding SET state = 'revoked', "
                "       revoked_at = now() "
                " WHERE organization_id = %s AND id = %s AND state = 'active' "
                "RETURNING id", (self._org, ulid_to_uuid(binding_id))).fetchone()
        except psycopg.errors.RestrictViolation as exc:
            raise SecurityError(str(exc).strip().splitlines()[0]) from exc
        return row is not None

    def roles(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT r.slug, r.display_name, r.description, "
            "       array_agg(p.permission ORDER BY p.permission) "
            "  FROM clep.role r "
            "  JOIN clep.role_permission p ON p.role_slug = r.slug "
            " GROUP BY r.slug, r.display_name, r.description ORDER BY r.slug"
        ).fetchall()
        return [{"slug": r[0], "displayName": r[1], "description": r[2],
                 "permissions": list(r[3])} for r in rows]

    def authorization_for(self, principal_kind: str,
                          subject_uuid: uuid.UUID) -> Authorization:
        column = ("app_user_id" if principal_kind == "user"
                  else "service_account_id")
        rows = self._conn.execute(
            f"SELECT b.role_slug, b.scope_kind, b.project_id, "
            f"       array_agg(p.permission) "
            f"  FROM clep.role_binding b "
            f"  JOIN clep.role_permission p ON p.role_slug = b.role_slug "
            f" WHERE b.organization_id = %s AND b.state = 'active' "
            f"   AND b.principal_kind = %s AND b.{column} = %s "
            f" GROUP BY b.id, b.role_slug, b.scope_kind, b.project_id",
            (self._org, principal_kind, subject_uuid)).fetchall()
        return Authorization(grants=tuple(
            Grant(role_slug=r[0], scope_kind=r[1],
                  project_id=uuid_to_ulid(r[2]) if r[2] else None,
                  permissions=frozenset(r[3])) for r in rows))

    # ---- governance policy ----------------------------------------------
    def retention_policy(self) -> RetentionPolicyRow | None:
        row = self._conn.execute(
            "SELECT decision_retention_days, content_retention_days, "
            "       audit_retention_days, updated_at FROM clep.retention_policy "
            " WHERE organization_id = %s", (self._org,)).fetchone()
        return RetentionPolicyRow(*row) if row else None

    def set_retention_policy(self, *, decision_days: int, content_days: int,
                             audit_days: int, actor_id: str
                             ) -> RetentionPolicyRow:
        """The audit floor is enforced by the store (I-34).

        This method does not check it. That is the point: a second writer that
        skipped this method would have to satisfy the same constraint, and a
        floor only the service layer knows about is a floor a second writer
        walks straight through.
        """
        # Read before writing. A CHECK violation aborts the transaction, so a
        # message that asked the database for the floor *after* the failure
        # would raise InFailedSqlTransaction and replace a clear refusal with a
        # confusing one — which is exactly what the first version of this did.
        floor = self.audit_retention_floor()
        try:
            with self._conn.transaction():
                self._conn.execute(
                    "INSERT INTO clep.retention_policy (id, organization_id, "
                    "decision_retention_days, content_retention_days, "
                    "audit_retention_days, updated_by) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (organization_id) DO UPDATE SET "
                    "  decision_retention_days = EXCLUDED.decision_retention_days, "
                    "  content_retention_days = EXCLUDED.content_retention_days, "
                    "  audit_retention_days = EXCLUDED.audit_retention_days, "
                    "  updated_by = EXCLUDED.updated_by, updated_at = now()",
                    (uuid.uuid4(), self._org, decision_days, content_days,
                     audit_days, ulid_to_uuid(actor_id)))
        except psycopg.errors.CheckViolation as exc:
            # The nested transaction is a savepoint, so the caller's transaction
            # survives the refusal and can still be read from and committed.
            raise SecurityError(
                f"the retention policy was refused by the store: audit "
                f"retention may not fall below the platform floor "
                f"({floor} days), and no tenant policy may lower it") from exc
        policy = self.retention_policy()
        assert policy is not None
        return policy

    def audit_retention_floor(self) -> int:
        return self._conn.execute(
            "SELECT clep.audit_retention_floor_days()").fetchone()[0]

    def usage_limit(self) -> UsageLimitRow:
        row = self._conn.execute(
            "SELECT requests_per_minute, runs_per_period, period_days "
            "  FROM clep.usage_limit WHERE organization_id = %s",
            (self._org,)).fetchone()
        return UsageLimitRow(*row) if row else DEFAULT_USAGE_LIMIT

    def set_usage_limit(self, *, requests_per_minute: int, runs_per_period: int,
                        period_days: int, actor_id: str) -> UsageLimitRow:
        try:
            with self._conn.transaction():
                self._conn.execute(
                    "INSERT INTO clep.usage_limit (id, organization_id, "
                    "requests_per_minute, runs_per_period, period_days, "
                    "updated_by) VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (organization_id) DO UPDATE SET "
                    "  requests_per_minute = EXCLUDED.requests_per_minute, "
                    "  runs_per_period = EXCLUDED.runs_per_period, "
                    "  period_days = EXCLUDED.period_days, "
                    "  updated_by = EXCLUDED.updated_by, updated_at = now()",
                    (uuid.uuid4(), self._org, requests_per_minute,
                     runs_per_period, period_days, ulid_to_uuid(actor_id)))
        except psycopg.errors.CheckViolation as exc:
            raise SecurityError(
                "a usage limit must be positive; zero would deny everything "
                "and there is no value meaning unlimited") from exc
        return self.usage_limit()

    def consume_run_quota(self, *, at: date | None = None) -> tuple[bool, int, int]:
        """Count one run against the tenant's quota.

        Returns `(allowed, used, limit)`. The counter is incremented only when
        the run is allowed, and the increment and the check are one statement:
        two concurrent submissions that each read-then-wrote would both see room
        that only one of them had.
        """
        limit = self.usage_limit()
        moment = at or _now().date()
        period_start = moment - timedelta(
            days=(moment - date(1970, 1, 1)).days % limit.period_days)
        row = self._conn.execute(
            "INSERT INTO clep.quota_consumption (id, organization_id, "
            "period_start, runs_started) VALUES (%s, %s, %s, 1) "
            "ON CONFLICT (organization_id, period_start) DO UPDATE SET "
            "  runs_started = clep.quota_consumption.runs_started + 1, "
            "  updated_at = now() "
            "WHERE clep.quota_consumption.runs_started < %s "
            "RETURNING runs_started",
            (uuid.uuid4(), self._org, period_start,
             limit.runs_per_period)).fetchone()
        if row is None:
            used = self._conn.execute(
                "SELECT runs_started FROM clep.quota_consumption "
                " WHERE organization_id = %s AND period_start = %s",
                (self._org, period_start)).fetchone()
            return False, (used[0] if used else limit.runs_per_period), \
                limit.runs_per_period
        return True, row[0], limit.runs_per_period


def _looks_like_ulid(value: str) -> bool:
    from clep.identity import is_ulid
    return is_ulid(value)


def authenticate(runtime_dsn: str, presented: str) -> tuple[Principal, Authorization]:
    """Turn a presented credential into a principal, or refuse it.

    The session is opened on the organization the credential names, and the key
    is looked up inside it. A credential naming an organization that does not
    own it finds nothing, because row-level security hides the row — so the
    organization is established by the lookup succeeding rather than trusted
    because it was presented (ADR-019 rule 3).

    Nothing is returned on the failure path, so an attempt that names the wrong
    organization discloses nothing it could not have guessed.
    """
    parsed = creds.parse(presented)
    org_uuid = ulid_to_uuid(parsed.organization_id)
    with tenant_session(runtime_dsn, org_uuid) as conn:
        row = conn.execute(
            "SELECT principal_kind, app_user_id, service_account_id, verifier, "
            "       salt, kdf_iterations, expires_at, revoked_at "
            "  FROM clep.api_key WHERE organization_id = %s AND id = %s",
            (str(org_uuid), ulid_to_uuid(parsed.key_id))).fetchone()
        if row is None:
            raise AuthenticationError("no such key in the named organization")
        kind, user_id, account_id, verifier, salt, iterations, expires, revoked = row
        if revoked is not None:
            raise AuthenticationError("key revoked")
        if expires is not None and expires <= _now():
            raise AuthenticationError("key expired")
        if not creds.verify(parsed.secret, bytes(salt), bytes(verifier), iterations):
            raise AuthenticationError("secret does not verify")
        subject_uuid = user_id if kind == "user" else account_id
        repo = SecurityRepository(conn, str(org_uuid))
        authorization = repo.authorization_for(kind, subject_uuid)
        # The principal's subject is the identity the audit trail records. It is
        # the row id rather than a display name, because a display name changes
        # and an audit trail that follows it stops joining.
        principal = Principal(organization_id=str(org_uuid),
                              subject=uuid_to_ulid(subject_uuid), kind=kind,
                              api_key_id=parsed.key_id)
    return principal, authorization
