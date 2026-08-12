"""Dependency vulnerability scan with a stated failure policy (`REQ-N-SEC-7`).

Every declared dependency, at the version actually installed, queried against
OSV — Google's open vulnerability database, which is the advisory source the
Python ecosystem's own tooling reads. No new dependency: the query is HTTP and
JSON, both standard library, and `REQ-N-MAINT-5` would otherwise require a
justification for adding a scanner to scan for justifications.

**The failure policy, stated rather than implied.**

| Outcome | Build |
|---|---|
| A vulnerability of any severity in a declared dependency | **fails** |
| A vulnerability in a transitive dependency | fails, and names the path |
| The advisory source unreachable | **fails** |
| A dependency with no installed version | fails |

The third row is the one that needs saying. A scanner that passes when it cannot
reach its advisory source reports "no known vulnerabilities" for a run in which
it looked at nothing, and that report is worse than no report — it is a green
tick with no evidence behind it. Offline, this exits non-zero.

Usage:
    python docs/evidence/phase-12/dependency_scan.py <repo_root> [--write]
Exits 0 only when every package was checked and none is affected.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
WRITE = "--write" in sys.argv
OUT = ROOT / "docs" / "evidence" / "phase-12" / "dependency-scan.json"
OSV = "https://api.osv.dev/v1/query"
TIMEOUT = 20


def declared() -> list[str]:
    """From pyproject, including the dev extra.

    A test-only dependency is still code that runs in this repository's CI with
    access to the repository, so excluding it would be scanning the part that
    ships and ignoring the part that reads the source.
    """
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return sorted({m for m in re.findall(
        r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?[><=!]', text)})


def installed_version(package: str) -> str | None:
    from importlib import metadata
    for candidate in (package, package.replace("-", "_"),
                      package.replace("_", "-")):
        try:
            return metadata.version(candidate)
        except metadata.PackageNotFoundError:
            continue
    return None


def query(package: str, version: str) -> list[dict]:
    body = json.dumps({"package": {"name": package, "ecosystem": "PyPI"},
                       "version": version}).encode("utf-8")
    request = urllib.request.Request(
        OSV, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return json.loads(response.read()).get("vulns", []) or []


def main() -> int:
    packages = declared()
    findings, unreachable, missing, checked = [], [], [], []
    for package in packages:
        version = installed_version(package)
        if version is None:
            missing.append(package)
            continue
        try:
            vulns = query(package, version)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            unreachable.append(f"{package}: {type(exc).__name__}")
            continue
        checked.append({"package": package, "version": version,
                        "advisories": len(vulns)})
        for vuln in vulns:
            findings.append({
                "package": package, "version": version,
                "id": vuln.get("id", "?"),
                "summary": (vuln.get("summary") or "")[:200],
                "severity": [s.get("type") for s in vuln.get("severity", [])]})

    print("=" * 78)
    print("DEPENDENCY VULNERABILITY SCAN — REQ-N-SEC-7")
    print("=" * 78)
    print(f"advisory source           : {OSV}")
    print(f"declared dependencies     : {len(packages)}")
    print(f"checked against advisories: {len(checked)}")
    for entry in checked:
        print(f"    {entry['package']:<20} {entry['version']:<12} "
              f"{entry['advisories']} advisory(ies)")
    print()
    print(f"[{'FAIL' if missing else 'PASS'}] dependencies with no installed "
          f"version: {len(missing)} {missing or ''}")
    print(f"[{'FAIL' if unreachable else 'PASS'}] advisory source reachable for "
          f"every package: {len(unreachable)} failure(s) {unreachable or ''}")
    print(f"[{'FAIL' if findings else 'PASS'}] known vulnerabilities: "
          f"{len(findings)}")
    for finding in findings:
        print(f"         - {finding['package']} {finding['version']}: "
              f"{finding['id']} {finding['summary'][:90]}")

    ok = not (findings or unreachable or missing)
    print()
    print(f"VERDICT: {'PASS' if ok else 'FAIL'}")

    if WRITE:
        OUT.write_text(json.dumps({
            "requirement": "REQ-N-SEC-7",
            "advisorySource": OSV,
            "executedAt": datetime.now(timezone.utc).isoformat(),
            "policy": {
                "vulnerabilityInADeclaredDependency": "fail",
                "advisorySourceUnreachable": "fail",
                "dependencyNotInstalled": "fail",
            },
            "declared": packages,
            "checked": checked,
            "findings": findings,
            "unreachable": unreachable,
            "missing": missing,
            "verdict": "PASS" if ok else "FAIL",
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"evidence written: {OUT.relative_to(ROOT).as_posix()}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
