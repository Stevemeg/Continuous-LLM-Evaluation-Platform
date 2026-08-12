"""Application service for credentials, bindings, governance policy and audit.

Same rule as every other service here: each method opens a tenant-bound session
and the organization arrives from the verified principal, never from the request.

Every governed action writes its audit event inside the same transaction
(`REQ-X-5`, I-35). That includes the ones an operator would rather not have
recorded — a revocation, an override, a refusal — because those are the events
an auditor asks about.

Two methods deliberately do not follow the pattern. `record_denial` and
`record_authentication_failure` are called from the ingress guard, on a path
where the request is being refused. They open their own short transaction: the
refusal has no other transaction to join, and an audit write that failed to
happen because there was nothing to attach it to is exactly the gap I-35 forbids.
"""
from __future__ import annotations

from datetime import datetime

from clep.api import audit
from clep.db.session import tenant_session
from clep.identity import is_ulid, ulid_to_uuid, uuid_to_ulid
from clep.security import erasure
from clep.security.repository import (SecurityError, SecurityRepository,
                                      authenticate)


class SecurityService:
    def __init__(self, runtime_dsn: str):
        self._dsn = runtime_dsn

    # ---- authentication, as the ingress sees it -------------------------
    def authenticator(self):
        """The callable `create_app` requires.

        Returned as a bound closure rather than exposing the module function,
        so the application never learns the DSN and cannot open a session of its
        own.
        """
        def verify(presented: str):
            return authenticate(self._dsn, presented)
        return verify

    def limit_for(self, organization_id: str) -> int:
        with tenant_session(self._dsn, organization_id) as conn:
            return SecurityRepository(conn, organization_id) \
                .usage_limit().requests_per_minute

    # ---- principals ------------------------------------------------------
    def create_service_account(self, *, organization_id: str, slug: str,
                               display_name: str, actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            account = SecurityRepository(conn, organization_id) \
                .create_service_account(slug=slug, display_name=display_name,
                                        actor_id=actor_id)
            audit.record(conn, organization_id, actor_id,
                         "service_account.created", "service_account",
                         account["id"])
            return account

    # ---- credentials -----------------------------------------------------
    def issue_api_key(self, *, organization_id: str, principal_kind: str,
                      subject_id: str, display_name: str, actor_id: str,
                      expires_at: datetime | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = SecurityRepository(conn, organization_id)
            key_id, presented = repo.issue_api_key(
                principal_kind=principal_kind, subject_id=subject_id,
                display_name=display_name, actor_id=actor_id,
                expires_at=expires_at)
            audit.record(conn, organization_id, actor_id, "api_key.issued",
                         "api_key", key_id)
            issued = _one(repo.list_api_keys(), key_id)
            # The only moment the secret exists outside the caller's own
            # configuration. It is assembled here and nowhere else, and nothing
            # in this process retains it.
            return {"apiKey": issued, "credential": presented}

    def list_api_keys(self, *, organization_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            return {"items": [_present_key(k) for k in
                              SecurityRepository(conn, organization_id)
                              .list_api_keys()]}

    def rotate_api_key(self, *, organization_id: str, key_id: str,
                       actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = SecurityRepository(conn, organization_id)
            if not _exists(repo.list_api_keys(), key_id):
                return None
            new_key_id, presented = repo.rotate_api_key(key_id,
                                                        actor_id=actor_id)
            audit.record(conn, organization_id, actor_id, "api_key.rotated",
                         "api_key", key_id,
                         justification=f"replaced by {new_key_id}")
            audit.record(conn, organization_id, actor_id, "api_key.issued",
                         "api_key", new_key_id)
            return {"apiKey": _one(repo.list_api_keys(), new_key_id),
                    "credential": presented}

    def revoke_api_key(self, *, organization_id: str, key_id: str,
                       actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = SecurityRepository(conn, organization_id)
            if not repo.revoke_api_key(key_id):
                return None
            audit.record(conn, organization_id, actor_id, "api_key.revoked",
                         "api_key", key_id)
            return _one(repo.list_api_keys(), key_id)

    # ---- authorization ---------------------------------------------------
    def list_roles(self, *, organization_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            return {"items": SecurityRepository(conn, organization_id).roles()}

    def create_role_binding(self, *, organization_id: str, role: str,
                            principal_kind: str, subject_id: str, scope: str,
                            project_id: str | None, actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = SecurityRepository(conn, organization_id)
            binding = repo.create_role_binding(
                role_slug=role, principal_kind=principal_kind,
                subject_id=subject_id, scope_kind=scope, project_id=project_id,
                actor_id=actor_id)
            audit.record(conn, organization_id, actor_id,
                         "role_binding.created", "role_binding", binding["id"],
                         justification=f"{role} to {principal_kind} "
                                       f"{subject_id} at {scope} scope")
            return _one(repo.list_role_bindings(), binding["id"],
                        present=_present_binding)

    def list_role_bindings(self, *, organization_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            return {"items": [_present_binding(b) for b in
                              SecurityRepository(conn, organization_id)
                              .list_role_bindings()]}

    def revoke_role_binding(self, *, organization_id: str, binding_id: str,
                            actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = SecurityRepository(conn, organization_id)
            if not repo.revoke_role_binding(binding_id):
                return None
            audit.record(conn, organization_id, actor_id,
                         "role_binding.revoked", "role_binding", binding_id)
            return _one(repo.list_role_bindings(), binding_id,
                        present=_present_binding)

    # ---- governance policy ----------------------------------------------
    def retention_policy(self, *, organization_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = SecurityRepository(conn, organization_id)
            floor = repo.audit_retention_floor()
            policy = repo.retention_policy()
            if policy is None:
                # No stored policy is not "no retention". The floor still
                # applies and is reported, so a caller reading this cannot
                # conclude that audit records are deletable.
                return {"decisionRetentionDays": floor,
                        "contentRetentionDays": floor,
                        "auditRetentionDays": floor,
                        "auditRetentionFloorDays": floor}
            return {"decisionRetentionDays": policy.decision_retention_days,
                    "contentRetentionDays": policy.content_retention_days,
                    "auditRetentionDays": policy.audit_retention_days,
                    "auditRetentionFloorDays": floor,
                    "updatedAt": policy.updated_at.isoformat()}

    def set_retention_policy(self, *, organization_id: str, decision_days: int,
                             content_days: int, audit_days: int,
                             actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = SecurityRepository(conn, organization_id)
            policy = repo.set_retention_policy(
                decision_days=decision_days, content_days=content_days,
                audit_days=audit_days, actor_id=actor_id)
            audit.record(conn, organization_id, actor_id,
                         "retention_policy.set", "retention_policy", None,
                         justification=f"decision {policy.decision_retention_days}d, "
                                       f"content {policy.content_retention_days}d, "
                                       f"audit {policy.audit_retention_days}d")
            return {"decisionRetentionDays": policy.decision_retention_days,
                    "contentRetentionDays": policy.content_retention_days,
                    "auditRetentionDays": policy.audit_retention_days,
                    "auditRetentionFloorDays": repo.audit_retention_floor(),
                    "updatedAt": policy.updated_at.isoformat()}

    def usage_limit(self, *, organization_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            limit = SecurityRepository(conn, organization_id).usage_limit()
            return {"requestsPerMinute": limit.requests_per_minute,
                    "runsPerPeriod": limit.runs_per_period,
                    "periodDays": limit.period_days}

    def set_usage_limit(self, *, organization_id: str, requests_per_minute: int,
                        runs_per_period: int, period_days: int,
                        actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = SecurityRepository(conn, organization_id)
            limit = repo.set_usage_limit(
                requests_per_minute=requests_per_minute,
                runs_per_period=runs_per_period, period_days=period_days,
                actor_id=actor_id)
            audit.record(conn, organization_id, actor_id, "usage_limit.set",
                         "usage_limit", None,
                         justification=f"{limit.requests_per_minute}/min, "
                                       f"{limit.runs_per_period} runs per "
                                       f"{limit.period_days}d")
            return {"requestsPerMinute": limit.requests_per_minute,
                    "runsPerPeriod": limit.runs_per_period,
                    "periodDays": limit.period_days}

    def consume_run_quota(self, *, organization_id: str) -> tuple[bool, int, int]:
        with tenant_session(self._dsn, organization_id) as conn:
            return SecurityRepository(conn, organization_id).consume_run_quota()

    # ---- audit and erasure ----------------------------------------------
    def list_audit_events(self, *, organization_id: str, project_id: str,
                          cursor: str | None = None, limit: int = 50) -> dict:
        """Newest first, cursor-paged on `(occurred_at, id)` together.

        The cursor is the last event seen rather than an offset. An offset over
        an append-only table shifts under the reader every time an event is
        written, which on this table is every governed action.

        It compares the **pair**, and that is not fastidiousness. The first
        version of this filtered on `id` alone, which assumed identifiers sort
        in the order events happened. They very nearly do — a ULID leads with a
        millisecond timestamp — and they do not within one millisecond, where
        the remaining 80 bits are random. Every event written by a single
        transaction shares a timestamp, so a page boundary landing inside one
        silently dropped events. A row comparison is correct whatever the low
        bits do.
        """
        with tenant_session(self._dsn, organization_id) as conn:
            reachable = _targets_of_project(conn, organization_id, project_id)
            after = ulid_to_uuid(cursor) if cursor and is_ulid(cursor) else None
            rows = conn.execute(
                "SELECT id, actor_id, action, target_type, target_id, "
                "       target_content_digest, justification, occurred_at "
                "  FROM clep.audit_event "
                " WHERE organization_id = %s "
                "   AND (target_id IS NULL OR target_id = ANY(%s)) "
                "   AND (%s::uuid IS NULL OR (occurred_at, id) < "
                "        (SELECT occurred_at, id FROM clep.audit_event "
                "          WHERE organization_id = %s AND id = %s::uuid)) "
                " ORDER BY occurred_at DESC, id DESC LIMIT %s",
                (organization_id, reachable, after, organization_id, after,
                 limit)).fetchall()
        items = [{"id": uuid_to_ulid(r[0]), "actorId": uuid_to_ulid(r[1]),
                  "action": r[2], "targetType": r[3],
                  "targetId": uuid_to_ulid(r[4]) if r[4] else None,
                  "targetVersionDigest": r[5], "justification": r[6],
                  "occurredAt": r[7].isoformat()} for r in rows]
        return {"items": items,
                "nextCursor": items[-1]["id"] if len(items) == limit else None}

    def request_erasure(self, *, organization_id: str, digests: list[str],
                        justification: str, override_baseline_pin: bool,
                        actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            outcome = erasure.request_erasure(
                conn, organization_id, digests=digests,
                justification=justification, actor_id=actor_id,
                override_baseline_pin=override_baseline_pin)
            return outcome.as_contract()

    # ---- refusals, recorded ---------------------------------------------
    def record_denial(self, *, organization_id: str, actor_id: str,
                      permission: str, target: str) -> None:
        """`REQ-N-SEC-2` and `REQ-X-5`, from the position the platform can
        honestly occupy.

        The platform cannot tell a request for another tenant's object from a
        request for one that does not exist — a 404 that differed from a 403
        would tell an attacker which identifiers are real. So what is recorded
        is what is true: this principal asked for this route under this tenant
        and was refused. It does not claim to have detected a cross-tenant read,
        because the design specifically prevents it from knowing.
        """
        with tenant_session(self._dsn, organization_id) as conn:
            audit.record(conn, organization_id, actor_id, "access.denied",
                         "route", None,
                         justification=f"{permission} required for {target}")

    def record_authentication_failure(self, *, reason: str,
                                      target: str) -> None:
        """Deliberately not written to the audit trail.

        There is no tenant to attribute it to — the credential did not verify,
        so any organization it named is unproven, and writing the row under that
        organization would let anyone with a URL fill another tenant's audit
        trail. The audit store is the one thing `REQ-N-COMP-3` forbids anyone to
        prune, so it must not be writable by an unauthenticated caller (the same
        reasoning as ADR-021 rule 8). It is counted for the operator instead.
        """
        self.authentication_failures = getattr(
            self, "authentication_failures", 0) + 1


def _targets_of_project(conn, organization_id: str, project_id: str) -> list:
    """Every object id in this project that an audit event might name.

    Derived from the tables that carry `project_id`, so a new project-scoped
    table is visible here the moment its rows exist rather than when someone
    remembers to add it.
    """
    tables = [r[0] for r in conn.execute(
        "SELECT c.relname FROM pg_class c "
        "  JOIN pg_namespace n ON n.oid = c.relnamespace "
        "  JOIN pg_attribute a ON a.attrelid = c.oid "
        " WHERE n.nspname = 'clep' AND c.relkind = 'r' "
        "   AND a.attname = 'project_id' AND a.attnum > 0 "
        " ORDER BY c.relname").fetchall()]
    # The project itself. An event naming the project — a role binding scoped to
    # it, a policy set on it — is in the project by any reading, and the loop
    # below cannot find it because `project` has no `project_id` column.
    found: list = [ulid_to_uuid(project_id)]
    for table in tables:
        found.extend(r[0] for r in conn.execute(
            f"SELECT id FROM clep.{table} WHERE organization_id = %s "
            f"AND project_id = %s", (organization_id,
                                     ulid_to_uuid(project_id))).fetchall())
    return found


def _present_key(row) -> dict:
    return {"id": row.id, "displayName": row.display_name,
            "principalKind": row.principal_kind, "subject": row.subject,
            "state": row.state, "createdAt": row.created_at.isoformat(),
            "expiresAt": row.expires_at.isoformat() if row.expires_at else None,
            "revokedAt": row.revoked_at.isoformat() if row.revoked_at else None,
            "revocationReason": row.revocation_reason,
            "rotatedTo": row.rotated_to}


def _present_binding(row) -> dict:
    return {"id": row.id, "role": row.role_slug,
            "principalKind": row.principal_kind, "subject": row.subject,
            "scope": row.scope_kind, "projectId": row.project_id,
            "state": row.state, "createdAt": row.created_at.isoformat()}


def _one(rows, wanted: str, present=_present_key) -> dict:
    for row in rows:
        if row.id == wanted:
            return present(row)
    raise SecurityError(f"{wanted} was written and cannot be read back")


def _exists(rows, wanted: str) -> bool:
    return any(row.id == wanted for row in rows)
