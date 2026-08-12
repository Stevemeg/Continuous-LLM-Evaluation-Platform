"""The permission boundary custom evaluators and tools run inside (ADR-006).

`REQ-N-SEC-4` requires an explicit permission boundary for custom evaluators and
tool integrations. `REQ-F-12-9` requires every such invocation to be
permission-scoped, schema-validated, audited, and isolated from other tenants'
data. ADR-006 rule 3 makes it deny-by-default: an evaluator receives only what
was explicitly granted.

The SDK has always delivered part of this by construction. An evaluator is handed
a `SampleContext` and nothing else — no database handle, no gateway, no
configuration, no credentials — so there is no ambient authority to withdraw.
What was missing is the other half: an evaluator that *declares* it needs
something must be refused when nothing granted it, rather than running and
discovering the absence by failing in the middle.

**What this is not.** ADR-006 rule 4 requires isolation enforced outside the
evaluator's own process boundary, and this is not that. A capability declared and
granted here is a statement about what the platform permitted, checked before the
code runs; it is not a sandbox that stops Python code from opening a socket. The
mechanism rule 4 asks for depends on the deployment model and is Phase 14's. This
module is deliberately narrow so that the difference stays visible: it decides
and records, and it does not claim to contain.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: The closed vocabulary. Deliberately small: a capability nobody can grant is a
#: capability nobody has to reason about, and each of these names something an
#: evaluator could use to reach beyond the sample it is scoring.
CAPABILITIES = ("network", "filesystem", "subprocess", "tenant_data")

#: What an evaluator gets when nobody said otherwise (ADR-006 rule 3).
DENY_ALL: "Grant"


class GrantError(ValueError):
    """A capability outside the vocabulary. Raised rather than ignored: an
    unknown capability silently dropped from a grant is a capability the
    evaluator believes it has."""


@dataclass(frozen=True)
class Grant:
    """What one invocation is permitted, and for whom.

    `organization_id` is part of the grant rather than context around it, which
    is ADR-006 rule 5: an invocation is scoped to one tenant's data and
    cross-tenant reach is not expressible in the interface. There is no grant
    that names two tenants and no way to construct one.
    """
    organization_id: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self):
        unknown = set(self.capabilities) - set(CAPABILITIES)
        if unknown:
            raise GrantError(
                f"unknown capability {sorted(unknown)}; the vocabulary is "
                f"{list(CAPABILITIES)} and a capability outside it cannot be "
                f"enforced, only believed")

    def permits(self, capability: str) -> bool:
        if capability not in CAPABILITIES:
            raise GrantError(f"unknown capability {capability!r}")
        return capability in self.capabilities

    def withheld(self, required) -> tuple[str, ...]:
        """Everything the evaluator asked for and did not get."""
        return tuple(sorted(c for c in required if not self.permits(c)))

    @property
    def recorded(self) -> str:
        """What goes in `evaluator_invocation.granted_permissions`.

        The **grant**, not the declaration. Recording what the evaluator asked
        for would record an intention; recording what it was given records the
        boundary the code actually ran inside, which is the thing an auditor is
        asking about.
        """
        return ",".join(sorted(self.capabilities)) or "none"


DENY_ALL = Grant()


def grant_for(organization_id: str, capabilities=()) -> Grant:
    return Grant(organization_id=str(organization_id),
                 capabilities=frozenset(capabilities))


def parse_declared(declared: str | None) -> tuple[str, ...]:
    """Read `evaluator_version.declared_permissions`.

    The column has existed since Phase 4 and has been written and never read.
    `'none'` and an empty value both mean the evaluator asked for nothing, which
    is the common case and the one that must not accidentally mean *everything*.
    """
    if not declared or declared.strip().lower() in ("none", "-"):
        return ()
    names = tuple(sorted({part.strip() for part in declared.split(",")
                          if part.strip()}))
    unknown = set(names) - set(CAPABILITIES)
    if unknown:
        raise GrantError(
            f"evaluator declares unknown capability {sorted(unknown)}; a "
            f"declaration the platform cannot enforce must be refused rather "
            f"than approximated")
    return names
