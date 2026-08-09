"""Run identity: what a run measured, captured before it runs.

`REQ-F-07-1` requires an immutable identity comprising dataset version, prompt or
system version, model and provider configuration, evaluator and judge versions,
seeds where relevant, environment metadata, and timestamps.

Phase 5 stored a digest and nothing else. A digest can tell you that two runs
measured the same thing; it cannot tell you *what* they measured, and
`REQ-F-07-3` requires re-running from the captured identity and naming every
element that could not be reconstructed. So the components are stored as rows and
the digest is derived from them, never the other way round.

The decision that matters here is which captured components enter the digest.
Environment metadata is captured and deliberately excluded — see ADR-014. Two
runs of the same prompt, dataset and evaluators on two different machines are the
same measurement; if the interpreter version entered the digest they would have
different identities and `REQ-F-01-3` could never be satisfied by any pair of
runs that were not executed on the same host.
"""
from __future__ import annotations

import hashlib
import json
import platform
import sys
from dataclasses import dataclass, field

#: Components that determine what a run measured. Changing any of these makes it
#: a different measurement, so a comparison across them is invalid.
IDENTITY_KINDS = (
    "dataset_version",
    "prompt_version",
    "model_configuration",
    "system_version",
    "evaluator_version",
    # ADR-004 D-5. A judge version is part of what a run measured, exactly as
    # an evaluator version is: REQ-F-08-8 invalidates comparability when either
    # changes, rather than warning about it.
    "judge_version",
    "suite_version",
    "integration_tier",
    "seed",
)

#: Captured for the record and for reproduction, excluded from the digest.
#: Present in the schema's component_kind constraint like any other kind.
CONTEXT_KINDS = ("environment",)

CAPTURED_KINDS = IDENTITY_KINDS + CONTEXT_KINDS


class IdentityError(ValueError):
    pass


def digest_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, order=True)
class Component:
    """One named element of a run identity.

    `ref` identifies the thing (a ULID, a tier name, a seed value). `digest`
    pins its content, so a version that was replaced in place — which the Phase 6
    triggers now forbid, but which a restored backup could still produce — is
    detectable at reproduction time rather than invisible.
    """
    kind: str
    ref: str
    digest: str

    def __post_init__(self):
        if self.kind not in CAPTURED_KINDS:
            raise IdentityError(
                f"{self.kind!r} is not a component kind the schema permits "
                f"({', '.join(CAPTURED_KINDS)}); the CHECK constraint would "
                f"reject it on insert and the digest would already be wrong")
        if not self.digest.startswith("sha256:") or len(self.digest) != 71:
            raise IdentityError(
                f"component {self.kind}/{self.ref} has digest {self.digest!r}, "
                f"which is not the sha256:<64 hex> form the schema requires")


@dataclass(frozen=True)
class RunIdentity:
    components: tuple[Component, ...] = field(default_factory=tuple)

    @property
    def identity_components(self) -> tuple[Component, ...]:
        return tuple(c for c in self.components if c.kind in IDENTITY_KINDS)

    def canonical_form(self) -> str:
        """A stable text encoding of the identity-bearing components.

        Sorted, with explicit separators, and never a Python dict dumped in
        insertion order: the digest has to be reproducible in another process, on
        another machine, in another release, or it is not identity. `ensure_ascii`
        keeps a prompt containing non-Latin text from encoding differently under
        a different default.
        """
        payload = [[c.kind, c.ref, c.digest] for c in sorted(self.identity_components)]
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"),
                          sort_keys=True)

    def digest(self) -> str:
        if not self.identity_components:
            # A digest over nothing is a constant, and a constant identity would
            # make every empty run "the same measurement" as every other.
            raise IdentityError(
                "a run identity needs at least one identity-bearing component; "
                "a digest over an empty set is the same for every run")
        return digest_of(self.canonical_form())

    def kinds(self) -> set[str]:
        return {c.kind for c in self.components}

    def require(self, *kinds: str) -> None:
        missing = [k for k in kinds if k not in self.kinds()]
        if missing:
            raise IdentityError(
                f"run identity is missing required component(s): "
                f"{', '.join(missing)}")


class IdentityBuilder:
    """Accumulates components, refusing a contradiction rather than overwriting.

    A builder that silently replaced a component would let a caller record two
    different dataset versions for one run and keep whichever arrived last —
    producing a digest that describes neither.
    """

    def __init__(self) -> None:
        self._by_key: dict[tuple[str, str], Component] = {}

    def add(self, kind: str, ref: str, digest: str) -> "IdentityBuilder":
        component = Component(kind=kind, ref=str(ref), digest=digest)
        existing = self._by_key.get((component.kind, component.ref))
        if existing is not None and existing != component:
            raise IdentityError(
                f"component {kind}/{ref} was already captured with digest "
                f"{existing.digest} and is now claimed to be {digest}; a run "
                f"identity cannot hold both")
        self._by_key[(component.kind, component.ref)] = component
        return self

    def add_literal(self, kind: str, value: str) -> "IdentityBuilder":
        """For components whose value *is* their content: a tier, a seed."""
        return self.add(kind, str(value), digest_of(str(value)))

    def add_environment(self) -> "IdentityBuilder":
        """Captured under REQ-F-07-1, excluded from the digest by ADR-014.

        Deliberately coarse. The interpreter and platform are what make a
        reproduction attempt worth flagging as environment drift; the full
        package set would change on every unrelated upgrade and turn the signal
        into noise.
        """
        description = json.dumps(
            {"python": platform.python_version(),
             "implementation": sys.implementation.name,
             "system": platform.system()},
            ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        return self.add("environment", description, digest_of(description))

    def build(self) -> RunIdentity:
        return RunIdentity(components=tuple(sorted(self._by_key.values())))
