"""Structured logs: correlated, and unable to carry what must not be logged.

`observability-strategy.md` §4 states four rules. Three are easy and one is not.

Structured and carrying correlation identifiers is easy. Log retention
independent of audit retention is a deployment property. No credential in any
field is *nearly* easy — `REQ-N-SEC-5` has a literal published form to match, and
Phase 12 already wrote the matcher.

The fourth is the one that needs a mechanism: **no `DS-1`–`DS-5` content at
default verbosity.** The strategy explains why it is hard, and the explanation is
worth having next to the code: logging a judge rationale to debug a scoring
anomaly writes `DS-5` content — which quotes `DS-1` to `DS-3` verbatim — into a
store whose retention and access controls were designed for logs, not for
customer data. Nobody does this maliciously. Somebody does it at 2am with a
production incident open, and it is the single most plausible way this platform
leaks a customer's data.

So the rule is not "remember not to log content". It is that **content of a class
the taxonomy forbids on the log surface cannot be logged**, because `Classified`
values are resolved through `clep.security.privacy` on the way out and come back
as `[withheld: DS-5 judge rationale]`.

Nothing here re-implements redaction. `privacy.py` owns the taxonomy and the
credential shapes; this module is its fourth consumer, and a second matcher would
be a second thing to keep correct and only one of them would be reviewed.

Two further properties, both because the alternative is worse:

**Every string is credential-scrubbed, whether or not it was classified.** A
provider key does not arrive in a field somebody remembered to declare; it
arrives inside an exception message, a URL, or a response body that was declared
as something else entirely. Scrubbing only declared fields would protect exactly
the fields that were never the problem.

**A log record cannot forge another log record.** Values are serialised as JSON,
so a newline in a field is an escape sequence rather than the start of a line
somebody else wrote.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal

from clep.security.privacy import classify, for_surface, redact_credentials
from clep.telemetry.correlation import current

LEVELS = ("debug", "info", "warning", "error")


@dataclass(frozen=True)
class Classified:
    """A value that knows what class of content it is.

    Wrapping is what makes the log surface refuse it. An unwrapped string is
    credential-scrubbed and logged; a `Classified` string is resolved against the
    taxonomy first, and `DS-1` to `DS-7` do not survive that.
    """
    value: object
    data_class: str

    def __post_init__(self) -> None:
        classify(self.data_class)  # raises on an undeclared class


class ContentCapture:
    """Explicit, audited and time-bounded debug capture of classified content.

    `observability-strategy.md` §4: "Debug-level content capture must be an
    explicit, audited, time-bounded decision." All three words are enforced here
    rather than described.

    *Explicit*: capture is absent unless constructed, and names the classes it
    covers. There is no verbosity level that switches it on as a side effect.

    *Audited*: the audit callback is invoked during construction, so the record
    exists before any content is captured rather than after. A capture whose
    audit write raises does not come into being.

    *Time-bounded*: it carries a deadline, and past the deadline it is inert.
    Nothing has to remember to turn it off — which is the failure mode, since the
    person who turned it on at 2am is asleep by the time it matters.
    """

    def __init__(self, *, actor: str, justification: str, ttl_seconds: float,
                 data_classes, audit, clock=None):
        if not actor or not str(justification).strip():
            raise ValueError(
                "content capture names an actor and a justification, or it does "
                "not happen; an unattributed capture of customer data is the "
                "thing REQ-F-12-4 exists to prevent")
        if ttl_seconds <= 0 or ttl_seconds > 86_400:
            raise ValueError(
                f"content capture is time-bounded: {ttl_seconds}s is not a bound "
                f"between 0 and 86400 (24 hours)")
        self._classes = frozenset(classify(c).code for c in data_classes)
        self._clock = clock or time.time
        self._expires_at = self._clock() + float(ttl_seconds)
        self.actor = actor
        self.justification = str(justification)
        # Before, not after. A capture that recorded its own authorisation
        # afterwards would be missing exactly the ones that failed.
        audit(actor=actor, justification=self.justification,
              data_classes=sorted(self._classes),
              expires_at=self._expires_at)

    def covers(self, data_class: str) -> bool:
        return (data_class in self._classes
                and self._clock() < self._expires_at)

    @property
    def expired(self) -> bool:
        return self._clock() >= self._expires_at


def _sanitise(value, capture: ContentCapture | None):
    """One value, made safe to log. Recursive, because nesting is where it hides."""
    if isinstance(value, Classified):
        if capture is not None and capture.covers(value.data_class):
            # Permitted, and still credential-scrubbed: REQ-N-SEC-5 is absolute
            # and does not relax because somebody authorised content capture.
            return redact_credentials(str(value.value))
        return for_surface(str(value.value), value.data_class, "log")
    if isinstance(value, str):
        return redact_credentials(value)
    if isinstance(value, dict):
        return {str(k): _sanitise(v, capture) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitise(v, capture) for v in value]
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    # Anything else becomes text, and text is scrubbed. An exception is the
    # common case here, and an exception's message is where a credential most
    # often turns up wearing something else's clothes.
    return redact_credentials(str(value))


class StructuredLogger:
    """The platform's log surface. JSON records, one per line."""

    def __init__(self, sink=None, *, capture: ContentCapture | None = None,
                 name: str = "clep"):
        self._sink = sink if sink is not None else logging.getLogger(name).info
        self._capture = capture

    def log(self, level: str, event: str, **fields) -> dict:
        if level not in LEVELS:
            raise ValueError(f"unknown log level {level!r}; levels are {LEVELS}")
        correlation = current()
        record = {
            "level": level,
            "event": redact_credentials(str(event)),
            # The identifier belongs here. observability-strategy.md §3 puts
            # tenant, project and run identifiers on traces and logs precisely
            # because they must not be metric labels, and this is the other half
            # of that sentence.
            "correlationId": correlation.correlation_id if correlation else None,
            "inboundReference": correlation.inbound_reference if correlation else None,
        }
        for key, value in fields.items():
            record[key] = _sanitise(value, self._capture)
        # JSON, so a newline inside a value is an escape sequence rather than
        # the beginning of a log line the value's author wrote.
        self._sink(json.dumps(record, default=str, sort_keys=True))
        return record

    def debug(self, event: str, **fields) -> dict:
        return self.log("debug", event, **fields)

    def info(self, event: str, **fields) -> dict:
        return self.log("info", event, **fields)

    def warning(self, event: str, **fields) -> dict:
        return self.log("warning", event, **fields)

    def error(self, event: str, **fields) -> dict:
        return self.log("error", event, **fields)


class ListSink:
    """Collects records so a test can read what was actually written."""

    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, line: str) -> None:
        self.lines.append(line)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)
