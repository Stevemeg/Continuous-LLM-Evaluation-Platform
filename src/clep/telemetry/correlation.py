"""The correlation identifier, and the context that carries it.

ADR-022 rule 4, which is ADR-009 rule 4 made concrete: the identifier is produced
by the core, so the chain survives removing every adapter. Nothing here imports a
telemetry package, and nothing here needs one.

The identifier is a ULID, like every other identifier in this project. It carries
48 bits of timestamp and 80 bits of randomness, and it carries **no tenant
identity** — which is a requirement, not an accident. An identifier that encoded
the organization would be a tenant discriminator travelling through logs, metric
exemplars and any exporter a deployment happens to attach.

A caller may present `x-correlation-id`. It is **recorded, not adopted**. A value
the client chooses is a value the client can collide with somebody else's, on
purpose or by accident, and an identifier a tenant controls is one a tenant can
use to make two runs look like one. Keeping it as `inbound_reference` preserves
the client's ability to join its logs to ours without letting it choose our
identity — and, because it is untrusted text that ends up in logs and in the
store, it is length-bounded and stripped of control characters here, at the one
place it enters the system.
"""
from __future__ import annotations

import contextvars
import re
from contextlib import contextmanager
from dataclasses import dataclass

from clep.identity import new_ulid

#: Long enough for any sane client identifier, short enough that a megabyte of
#: header does not reach the store or a log line.
MAX_INBOUND_REFERENCE = 200

#: Anything that is not printable ASCII. Newlines and carriage returns are the
#: ones that matter: a log line is terminated by one, so a header containing one
#: is a forged second log entry. Stripped rather than escaped, because there is
#: no legitimate client identifier that needs them.
_UNPRINTABLE = re.compile(r"[^\x20-\x7e]")


@dataclass(frozen=True)
class Correlation:
    """One request's identity as it travels the chain.

    `correlation_id` is ours and is the identifier `REQ-N-OBS-1` asks to be
    recoverable across the seven hops. `inbound_reference` is whatever the caller
    claimed, sanitised, and is never used as an identity by anything.
    """
    correlation_id: str
    inbound_reference: str | None = None


def sanitize_inbound(value: str | None) -> str | None:
    """Untrusted client text, made safe to store and to log. Never an identity."""
    if value is None:
        return None
    cleaned = _UNPRINTABLE.sub("", str(value)).strip()[:MAX_INBOUND_REFERENCE]
    return cleaned or None


def new_correlation(inbound_reference: str | None = None) -> Correlation:
    return Correlation(correlation_id=new_ulid(),
                       inbound_reference=sanitize_inbound(inbound_reference))


_CURRENT: contextvars.ContextVar[Correlation | None] = contextvars.ContextVar(
    "clep_correlation", default=None)


def current() -> Correlation | None:
    """The correlation in scope, or None outside one.

    Returning None rather than inventing one is deliberate. A module that
    silently manufactures an identifier when it finds none produces a chain with
    a gap in it that looks like a chain, and `REQ-N-OBS-1` is exactly the
    requirement that gap would defeat.
    """
    return _CURRENT.get()


def current_id() -> str | None:
    c = _CURRENT.get()
    return c.correlation_id if c else None


@contextmanager
def correlated(correlation: Correlation | str | None = None, *,
               inbound_reference: str | None = None):
    """Enter a correlation scope.

    A `contextvars.ContextVar` rather than a thread local, because the API is
    async and a thread local is shared by every task on the loop. Restored by
    token on the way out, so nesting is safe and a scope cannot leak into the
    caller's.

    Passing a bare string is how a worker resumes a correlation that began in an
    HTTP request in another process: the identifier crosses the boundary as data
    in the job payload, and the context is rebuilt from it here.
    """
    if correlation is None:
        correlation = new_correlation(inbound_reference)
    elif isinstance(correlation, str):
        correlation = Correlation(correlation_id=correlation,
                                  inbound_reference=sanitize_inbound(inbound_reference))
    token = _CURRENT.set(correlation)
    try:
        yield correlation
    finally:
        _CURRENT.reset(token)
