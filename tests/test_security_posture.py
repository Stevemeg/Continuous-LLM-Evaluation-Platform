"""Properties of the whole surface rather than of one module.

`REQ-F-12-1` (organizations and projects are scopes on every record),
`REQ-F-12-5` (no request in one tenant's context reads another's),
`REQ-F-12-8` (governance is never withheld by configuration),
`REQ-N-SEC-1` (every authorization decision is enforced server-side) and
`REQ-N-SEC-7` (the dependency scan has a failure policy that fails closed).

These are the assertions that go stale silently. A route added next year with no
guard, a flag added to make a test easier, a scanner quietly changed to pass when
it cannot reach its source — each of those breaks a requirement while every
module-level test keeps passing. So each one is derived from the artifact rather
than restated: the routes come from the application FastAPI actually built, the
tables from the live catalogue, and the scan policy from executing the scanner
against a source that is not there.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from clep.api import contract
from clep.security.rbac import PERMISSIONS

ROOT = Path(__file__).resolve().parents[1]


def _application():
    """Every route this platform can serve, built the way a deployment does."""
    from clep.api.app import create_app

    class Service:
        def __getattr__(self, _name):
            return lambda *a, **k: None

    return create_app(Service(), Service(), Service(), Service(), Service(),
                      Service(), authenticator=lambda token: None,
                      security_service=Service())


# ------------------------------------------------- REQ-N-SEC-1, REQ-F-12-8
def test_every_route_carries_an_authorization_guard():
    """Derived from the routes FastAPI registered, not from the source text.

    Nine checks in this project have been lost to string matching. This one
    walks `app.routes` and looks for the guard's marker on each route's
    dependencies, so a route added without `Depends(_guard(...))` fails here and
    at import.
    """
    app = _application()
    unguarded = []
    for route in app.routes:
        if not getattr(route, "methods", None):
            continue
        marked = any(hasattr(sub.call, "__clep_permission__")
                     for sub in route.dependant.dependencies
                     if sub.call is not None)
        if not marked:
            unguarded.append(f"{sorted(route.methods)} {route.path}")
    assert unguarded == []


def test_every_route_requires_a_permission_the_platform_recognises():
    app = _application()
    required = set()
    for route in app.routes:
        if not getattr(route, "methods", None):
            continue
        for sub in route.dependant.dependencies:
            permission = getattr(sub.call, "__clep_permission__", None)
            if permission:
                required.add(permission)
    assert required, "no route declared a permission at all"
    assert required <= set(PERMISSIONS)


def test_the_permission_a_route_enforces_is_the_one_its_contract_declares():
    """One source of truth. A route that enforced something other than what the
    contract published would make the contract a description of an intention."""
    app = _application()
    disagreements = []
    for route in app.routes:
        if not getattr(route, "methods", None):
            continue
        for method in route.methods:
            if method not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                continue
            declared = contract.operation_for(method, route.path).get(
                "x-permission")
            for sub in route.dependant.dependencies:
                enforced = getattr(sub.call, "__clep_permission__", None)
                if enforced and enforced != declared:
                    disagreements.append(
                        f"{method} {route.path}: enforces {enforced!r}, "
                        f"contract declares {declared!r}")
    assert disagreements == []


def test_an_application_that_cannot_verify_a_credential_does_not_start():
    """`REQ-F-12-8`: there is no configuration in which authentication is off.
    The absence is asserted, because a flag added for convenience is exactly how
    governance becomes tier-gated."""
    from clep.api.app import create_app
    with pytest.raises(ValueError, match="requires an authenticator"):
        create_app(object())


def test_no_setting_anywhere_turns_authorization_off():
    """A deliberately textual check, and the exception that proves the rule.

    Behavioural testing cannot prove the *absence* of a bypass — you can only
    exercise the ones you thought of. What can be checked mechanically is that
    no environment variable, flag or keyword with a disabling name exists in the
    package at all, which is where such a thing would have to live to be
    reachable from a deployment.
    """
    forbidden = re.compile(
        r"(?i)(disable|skip|bypass|no)[_-]?(auth|authz|authorization|rbac|"
        r"permission|tenant)", re.I)
    offenders = []
    for path in sorted((ROOT / "src" / "clep").rglob("*.py")):
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), 1):
            if forbidden.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(ROOT).as_posix()}:{number}")
    assert offenders == [], f"a disabling switch may exist: {offenders}"


# -------------------------------------------------- REQ-F-12-1, REQ-F-12-5
def test_every_record_the_platform_creates_resolves_to_an_organization():
    """`REQ-F-12-1` at the storage level, over the live catalogue.

    A table without `organization_id` is a record that belongs to no tenant, and
    the only ones permitted are the tenant root itself and the enumerated global
    exception of ADR-010 rule 4.
    """
    sql = "\n".join(p.read_text(encoding="utf-8")
                    for p in sorted((ROOT / "docs/data/schema").glob("*.sql")))
    bodies = dict(re.findall(r"CREATE TABLE clep\.(\w+)\s*\((.*?)\n\);", sql, re.S))
    from tests.test_tenant_isolation import GLOBAL
    without = [name for name, body in bodies.items()
               if not re.search(r"^\s+organization_id\s+uuid", body, re.M)]
    assert set(without) == {"organization"} | set(GLOBAL)


def test_a_project_is_a_scope_and_not_a_label():
    """`REQ-F-12-1` again: a project-scoped record names its project through a
    foreign key that carries the tenant, so a record cannot be filed under
    another tenant's project."""
    sql = "\n".join(p.read_text(encoding="utf-8")
                    for p in sorted((ROOT / "docs/data/schema").glob("*.sql")))
    bodies = dict(re.findall(r"CREATE TABLE clep\.(\w+)\s*\((.*?)\n\);", sql, re.S))
    plain = [name for name, body in bodies.items()
             if re.search(r"^\s+project_id\s+uuid", body, re.M)
             and not re.search(r"FOREIGN KEY \(organization_id, project_id\)",
                               body)]
    assert plain == []


# ------------------------------------------------------------ REQ-N-SEC-7
def test_the_dependency_scan_fails_when_it_cannot_reach_its_advisory_source():
    """The failure policy that matters most, executed.

    A scanner that passes when it cannot reach its source reports "no known
    vulnerabilities" for a run in which it looked at nothing — a green tick with
    no evidence behind it, which is worse than no report.
    """
    import subprocess
    import sys
    scanner = ROOT / "docs/evidence/phase-12/dependency_scan.py"
    unreachable = dict(**{"PATH": ""},
                       PYTHONPATH=str(ROOT / "src"),
                       # A proxy that does not exist, so every request fails at
                       # once rather than after a network timeout.
                       HTTP_PROXY="http://127.0.0.1:1",
                       HTTPS_PROXY="http://127.0.0.1:1",
                       SYSTEMROOT=__import__("os").environ.get("SYSTEMROOT", ""))
    result = subprocess.run([sys.executable, str(scanner), str(ROOT)],
                            capture_output=True, text=True, env=unreachable,
                            timeout=180)
    assert result.returncode != 0, \
        "the scan passed without reaching an advisory source"
    assert "VERDICT: FAIL" in result.stdout


def test_the_scan_policy_is_stated_where_a_reader_will_find_it():
    """Inspection, and stated as such. `REQ-N-SEC-7` requires *a defined failure
    policy*; a policy that only exists as behaviour is one nobody can review
    before it fires."""
    text = (ROOT / "docs/evidence/phase-12/dependency_scan.py").read_text("utf-8")
    for outcome in ("advisory source unreachable", "failure policy"):
        assert outcome.lower() in text.lower()
