"""Sensitive-data classes, and redaction on the paths that leave the tenant.

`REQ-N-PRIV-1` requires sensitive data classes to be classified and handled
according to their class. `REQ-N-PRIV-2` requires redaction where content must
not reach a judge, a report, or a log.

The classes are not invented here. The PRD defines `DS-1` through `DS-9` and the
threat model consumes them; this module is the third consumer, and it reads the
same taxonomy rather than starting a fourth.

Two decisions are worth stating because the obvious alternatives are worse.

**Redaction is by class, not by pattern.** A regular expression that hunts for
things that look like credit-card numbers finds some of them, misses others, and
reports a run identifier as a social security number often enough to be
disabled. What this module does instead is take a *declared* class and apply the
handling that class requires. Where a pattern is used at all — the credential
shapes below — it is because a credential has a literal published form and
because `REQ-N-SEC-5` makes a false negative a breach rather than an
inconvenience.

**Redaction is not reversible and does not preserve length.** A redaction that
kept the length of what it removed would leak the length, which for a
short-alphabet secret is most of the search space. The replacement names the
class instead, so a reader of a report knows something was removed and what kind
of thing it was.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: The PRD's taxonomy, with the handling each class requires. `judge` is whether
#: content of this class may be shown to a probabilistic judge; `report` whether
#: it may appear in a rendered report; `log` whether it may be logged.
#:
#: `DS-7` is the only class refused everywhere, and `DS-9` the only one whose
#: concern is integrity rather than confidentiality — which is why it is
#: permitted in a report: an audit record a reader cannot see is an audit record
#: that proves nothing.
@dataclass(frozen=True)
class DataClass:
    code: str
    label: str
    judge: bool
    report: bool
    log: bool


CLASSES = {c.code: c for c in (
    DataClass("DS-1", "golden dataset content", judge=True, report=True,
              log=False),
    DataClass("DS-2", "candidate output", judge=True, report=True, log=False),
    DataClass("DS-3", "retrieved context", judge=True, report=True, log=False),
    DataClass("DS-4", "agent trajectory", judge=True, report=True, log=False),
    DataClass("DS-5", "judge rationale", judge=False, report=True, log=False),
    DataClass("DS-6", "prompt", judge=True, report=True, log=False),
    DataClass("DS-7", "provider credential", judge=False, report=False,
              log=False),
    DataClass("DS-8", "cost and usage record", judge=False, report=True,
              log=True),
    DataClass("DS-9", "audit record", judge=False, report=True, log=True),
)}

SURFACES = ("judge", "report", "log")

#: The shapes `REQ-N-SEC-5` is about, each with a published literal form. This
#: is the one place a pattern is the right tool: a provider key is not a class
#: somebody remembered to declare, it is a string that turns up inside content
#: that was declared as something else entirely.
CREDENTIAL_SHAPES = (
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "provider key"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{16,}"), "forge token"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "cloud key id"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
     "token"),
    (re.compile(r"clep_[0-9A-HJKMNP-TV-Z]{26}_[0-9A-HJKMNP-TV-Z]{26}"
                r"_[0-9A-HJKMNP-TV-Z]{32}"), "platform credential"),
    (re.compile(r"(?i)://[^/\s:@]+:[^/\s:@]+@"), "credential in url"),
)


class PrivacyError(ValueError):
    pass


def classify(code: str) -> DataClass:
    try:
        return CLASSES[code]
    except KeyError:
        raise PrivacyError(
            f"{code!r} is not a declared sensitivity class; the taxonomy is "
            f"{sorted(CLASSES)} and content of an undeclared class cannot be "
            f"handled according to its class") from None


def permitted(code: str, surface: str) -> bool:
    """Whether content of this class may reach this surface at all."""
    if surface not in SURFACES:
        raise PrivacyError(f"unknown surface {surface!r}")
    return getattr(classify(code), surface)


def redact_credentials(text: str) -> str:
    """Remove anything shaped like a credential, whatever class it arrived in.

    Applied to content that is otherwise permitted, because `REQ-N-SEC-5` is
    absolute: a credential must not appear in a report or a log even when the
    surrounding content is perfectly allowed to.
    """
    if not text:
        return text
    for pattern, label in CREDENTIAL_SHAPES:
        text = pattern.sub(f"[redacted: {label}]", text)
    return text


def for_surface(text: str, code: str, surface: str) -> str:
    """What content of this class looks like on this surface.

    Withheld entirely when the class forbids the surface, and credential-scrubbed
    when it does not. The replacement names the class rather than preserving the
    length: a redaction that kept the shape of what it removed would leak the
    shape.
    """
    declared = classify(code)
    if not permitted(code, surface):
        return f"[withheld: {declared.code} {declared.label}]"
    return redact_credentials(text)


def audit_of(text: str, code: str) -> dict:
    """What was removed, without reproducing it.

    A redaction nobody can see the effect of is indistinguishable from content
    that never contained anything — so the count is reported, and the content is
    not.
    """
    declared = classify(code)
    found = []
    for pattern, label in CREDENTIAL_SHAPES:
        hits = len(pattern.findall(text or ""))
        if hits:
            found.append({"kind": label, "occurrences": hits})
    return {"class": declared.code, "label": declared.label,
            "surfaces": {s: permitted(code, s) for s in SURFACES},
            "credentialsRemoved": found}
