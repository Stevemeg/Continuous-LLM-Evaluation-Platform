"""The authorization decision (ADR-020).

Three artifacts state the same vocabulary: the CHECK constraint on
`clep.role_permission`, the `Permission` enum in the API contract, and the tuple
below. That is deliberate duplication in three places that cannot drift silently
— the store refuses an unknown permission, the contract refuses to describe one,
and the phase validator compares all three as sets.

The decision itself is small on purpose. What makes RBAC fail in practice is not
a wrong rule; it is a surface nobody attached a rule to, which is why the
enforcement point in `clep.api.app` refuses to start when a route declares no
permission rather than defaulting it to something.
"""
from __future__ import annotations

from dataclasses import dataclass

#: ADR-020 rules 1 and 2: an action on a resource class, never on a route.
#: Routes are renamed and split; the governed action is what `REQ-F-12-2`
#: enumerates and what an auditor asks about.
PERMISSIONS = (
    "run:create", "run:read", "run:cancel", "run:reproduce",
    "dataset:read", "dataset:write", "dataset:approve", "dataset:erase",
    "prompt:read", "prompt:write", "prompt:publish",
    "experiment:write",
    "baseline:create", "baseline:approve",
    "gate:configure", "gate:evaluate", "gate:read", "gate:except",
    "judge:configure", "judge:read", "escalation:review",
    "plan:read", "plan:draft", "plan:accept",
    "memory:read",
    "schedule:write", "release:observe",
    "analytics:read",
    "alert:configure", "alert:read", "alert:evaluate",
    "audit:read", "credential:manage", "role:grant",
    "governance:configure",
)

#: The two scopes of ADR-020 rule 4. Not a tree: a hierarchy would need a
#: resolution order, and a resolution order is where an inherited deny quietly
#: becomes an inherited allow.
SCOPES = ("organization", "project")

#: The permission that must never leave an organization without a holder (I-4).
#: Named once, here, because the store's trigger and this module have to agree
#: on which permission is the administrative one.
ADMINISTRATIVE = "role:grant"


class AuthorizationError(RuntimeError):
    """A refusal. Carries the permission so the audit record can name it; the
    caller is told considerably less (ADR-020 rule 8)."""

    def __init__(self, permission: str, detail: str = ""):
        self.permission = permission
        super().__init__(detail or f"{permission} is required")


@dataclass(frozen=True)
class Grant:
    """One active role binding, reduced to what the decision needs."""
    role_slug: str
    scope_kind: str
    project_id: str | None
    permissions: frozenset[str]


@dataclass(frozen=True)
class Authorization:
    """What a verified principal may do, resolved once at ingress.

    Held as the grants themselves rather than as a flattened permission set,
    because a project-scoped grant answers a different question from an
    organization-scoped one and flattening loses which is which.
    """
    grants: tuple[Grant, ...] = ()

    def allows(self, permission: str, project_id: str | None = None) -> bool:
        """Deny by default (ADR-020 rule 5).

        An organization-scoped grant answers for any project. A project-scoped
        grant answers only for its own, and answers `False` for a request that
        names no project at all — an operation not bound to one project is an
        organization-wide operation, and a grant on a single project is not
        authority over the organization.
        """
        if permission not in PERMISSIONS:
            # An unrecognised permission is a programming error, and defaulting
            # it either way is worse than refusing: `True` opens a surface,
            # `False` hides a typo behind a plausible 403.
            raise AuthorizationError(
                permission, f"{permission!r} is not a permission this platform "
                            f"recognises")
        for grant in self.grants:
            if permission not in grant.permissions:
                continue
            if grant.scope_kind == "organization":
                return True
            if project_id is not None and grant.project_id == project_id:
                return True
        return False

    def require(self, permission: str, project_id: str | None = None) -> None:
        if not self.allows(permission, project_id):
            raise AuthorizationError(permission)

    @property
    def is_empty(self) -> bool:
        return not self.grants
