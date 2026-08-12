"""Minting and verifying a credential (ADR-019).

The secret exists in exactly two places over its lifetime: the response that
issued it, and the caller's own configuration. It is never in this repository's
store, its logs, or its reports — I-2 and `REQ-N-SEC-5`.

What the store holds is a verifier: PBKDF2-HMAC-SHA256 over a per-key salt, at a
work factor recorded beside it rather than compiled in, so that raising the
factor later does not invalidate keys already issued (ADR-019 rule 6).

Nothing here touches the database. Verification is a pure function of what was
presented and what was stored, which is what lets every property below be tested
without one.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass, field

from clep.identity import ULID_LENGTH, is_ulid, new_ulid

#: Crockford base32 again, and for the same reason `identity.py` chose it: I, L,
#: O and U are absent, so a human retyping a credential from a terminal cannot
#: turn it into a different one.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

#: 160 bits. Long enough that the work factor below is defence in depth over an
#: already-uniform secret rather than the thing standing between an attacker and
#: the key — which is the reasoning ADR-019 gives for PBKDF2 over a memory-hard
#: derivation.
SECRET_BITS = 160
SECRET_LENGTH = SECRET_BITS // 5

KDF = "pbkdf2_sha256"

#: OWASP's 2023 floor for PBKDF2-HMAC-SHA256. The schema requires at least
#: 100 000 so that a verifier can never degenerate into a plain hash; this is the
#: value new keys are actually issued at, and it is stored per key.
KDF_ITERATIONS = 210_000

SALT_BYTES = 16
PREFIX = "clep"

#: `clep_<organization>_<key>_<secret>`. The two identifiers route the lookup;
#: the organization is proven rather than trusted, because the key is resolved
#: inside the tenant context it names and row-level security hides it anywhere
#: else (ADR-019 rule 3).
_PRESENTED = re.compile(
    rf"^{PREFIX}_([{_ALPHABET}]{{{ULID_LENGTH}}})_([{_ALPHABET}]{{{ULID_LENGTH}}})"
    rf"_([{_ALPHABET}]{{{SECRET_LENGTH}}})$")


class CredentialError(ValueError):
    """Raised for anything a caller presented that is not a credential.

    Deliberately one class with one message shape. ADR-019 rule 11: malformed,
    unknown, revoked, expired and wrong-secret must be indistinguishable to the
    caller, and the surest way to keep them indistinguishable is to have nothing
    to distinguish.
    """


@dataclass(frozen=True)
class MintedKey:
    """What issuing a key produces. `secret` leaves this process once."""
    organization_id: str
    key_id: str
    salt: bytes = field(repr=False)
    verifier: bytes = field(repr=False)
    kdf: str = KDF
    kdf_iterations: int = KDF_ITERATIONS
    secret: str = field(repr=False, default="")

    @property
    def presented(self) -> str:
        """The single string the caller keeps. Never reconstructable later: it
        needs `secret`, and `secret` is not stored."""
        return f"{PREFIX}_{self.organization_id}_{self.key_id}_{self.secret}"

    def __str__(self) -> str:
        # This object reaches log lines and exception context, which is exactly
        # where REQ-N-SEC-5 is lost. `repr=False` covers repr(); this covers the
        # f-string that does not call it.
        return (f"MintedKey(organization_id={self.organization_id!r}, "
                f"key_id={self.key_id!r})")


@dataclass(frozen=True)
class PresentedCredential:
    organization_id: str
    key_id: str
    secret: str = field(repr=False, default="")

    def __str__(self) -> str:
        return (f"PresentedCredential(organization_id={self.organization_id!r}, "
                f"key_id={self.key_id!r})")


def new_secret() -> str:
    """160 bits from the OS, rendered in the retypable alphabet.

    Built from `os.urandom` directly rather than from `random`: the `random`
    module is seeded and reproducible, which is a property this project wants
    almost everywhere else and must not have here.
    """
    value = int.from_bytes(os.urandom(SECRET_BITS // 8), "big")
    out = []
    for _ in range(SECRET_LENGTH):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def derive(secret: str, salt: bytes, iterations: int = KDF_ITERATIONS) -> bytes:
    if iterations < 100_000:
        # The same floor the schema enforces, stated here too: a derivation
        # called with a small factor would produce a verifier the store would
        # then refuse, and failing at the call site says why.
        raise CredentialError(
            f"refusing to derive a verifier at {iterations} iterations; the "
            f"floor is 100000 and a lower factor is a plain hash with extra "
            f"steps")
    return hashlib.pbkdf2_hmac("sha256", secret.encode("utf-8"), salt, iterations)


def mint(organization_id: str, key_id: str | None = None,
         iterations: int = KDF_ITERATIONS) -> MintedKey:
    """A fresh credential. The only place a secret comes into existence."""
    if not is_ulid(organization_id):
        raise CredentialError("an organization identifier is required")
    key_id = key_id or new_ulid()
    secret = new_secret()
    salt = os.urandom(SALT_BYTES)
    return MintedKey(organization_id=organization_id, key_id=key_id, salt=salt,
                     verifier=derive(secret, salt, iterations), kdf=KDF,
                     kdf_iterations=iterations, secret=secret)


def parse(presented: str) -> PresentedCredential:
    """Split a presented credential, or refuse it.

    Refusing here rather than looking it up is what stops a malformed string
    becoming a database round trip, and a database round trip becoming a way to
    measure which identifiers exist.
    """
    if not isinstance(presented, str):
        raise CredentialError("no credential presented")
    match = _PRESENTED.match(presented.strip())
    if not match:
        raise CredentialError("not a credential")
    organization_id, key_id, secret = match.groups()
    if not is_ulid(organization_id) or not is_ulid(key_id):
        # The regex fixes the alphabet and the length; `is_ulid` rejects the one
        # remaining malformed case, a 26-character string whose leading
        # character overflows 128 bits.
        raise CredentialError("not a credential")
    return PresentedCredential(organization_id=organization_id, key_id=key_id,
                               secret=secret)


def verify(secret: str, salt: bytes, verifier: bytes,
           iterations: int) -> bool:
    """Constant-time (ADR-019 rule 5).

    `hmac.compare_digest` and never `==`: the built-in comparison returns as
    soon as two bytes differ, which is a measurable channel that reveals the
    stored verifier one byte at a time.
    """
    try:
        candidate = derive(secret, salt, iterations)
    except CredentialError:
        return False
    return hmac.compare_digest(candidate, bytes(verifier))
