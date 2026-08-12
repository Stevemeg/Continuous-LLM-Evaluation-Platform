"""Prove the Phase 12 checks can fail.

Same harness as Phases 8 to 11: plant a violation, run the fast half of the
validator, inspect the check that should catch it, restore from HEAD, and verify
the restoration rather than assume it.

Nothing is enumerated. The restore reverts and removes whatever the working tree
holds, so a plant on a new path cannot leak the way a listed set of paths would
allow. The run refuses to start on a dirty tree, so any untracked file afterwards
is this script's.

Usage: python docs/evidence/phase-12/selftest_phase12.py <repo_root>
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PY = os.environ.get("CLEP_TEST_PYTHON", sys.executable)
FAST = pathlib.Path(tempfile.gettempdir()) / "clep_check12_fast.py"

SCHEMA = ROOT / "docs/data/schema/12-identity-and-access.sql"
SCHEMA04 = ROOT / "docs/data/schema/04-artifacts-and-audit.sql"
CREDENTIALS = ROOT / "src/clep/security/credentials.py"
RBAC = ROOT / "src/clep/security/rbac.py"
GRANTS = ROOT / "src/clep/security/grants.py"
LIMITS = ROOT / "src/clep/security/limits.py"
PRIVACY = ROOT / "src/clep/security/privacy.py"
ERASURE = ROOT / "src/clep/security/erasure.py"
CONFIG = ROOT / "src/clep/config.py"
APP = ROOT / "src/clep/api/app.py"
AUDIT = ROOT / "src/clep/api/audit.py"
SECURITY_SERVICE = ROOT / "src/clep/api/security_service.py"
JUDGE_SDK = ROOT / "src/clep/judges/sdk.py"
EVALUATOR_SDK = ROOT / "src/clep/evaluators/sdk.py"
CONTRACT = ROOT / "docs/api/openapi.json"
SCAN_EVIDENCE = ROOT / "docs/evidence/phase-12/dependency-scan.json"
DEBT = ROOT / "docs/architecture/tracked-debt.md"
PHASE11 = ROOT / "docs/evidence/phase-11/check_phase11.py"


def rebuild_fast():
    """The validator without the slow half.

    P-1 runs the whole suite and P-5 re-runs an entire earlier gate in an
    isolated clone; between them they are most of an hour, and neither is what
    any plant here targets. The blob scan goes too, for the same reason: it
    walks every object in history and no plant touches history.
    """
    src = (ROOT / "docs/evidence/phase-12/check_phase12.py").read_text("utf-8")
    slow_start = src.index("# ===================================================== P-1 ")
    slow_end = src.index("# ===================================== P-11 the contract leads")
    src = src[:slow_start] + src[slow_end:]
    scan_start = src.index("# ============================================================ P-20 secrets")
    scan_end = src.index("# ============ P-26 every earlier gate is reachable")
    FAST.write_text(src[:scan_start] + src[scan_end:], encoding="utf-8")


def restore():
    """Revert every modification and remove everything a plant created.

    Derived rather than listed. `-fd` and not `-fdx`: ignored paths are not this
    script's business, and a virtualenv or a coverage file is not a plant.
    """
    subprocess.run(["git", "checkout", "--", "."], cwd=str(ROOT),
                   capture_output=True)
    subprocess.run(["git", "clean", "-fdq"], cwd=str(ROOT), capture_output=True)


def dirty() -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                          capture_output=True, text=True).stdout.strip()


def status_of(check_id: str) -> str:
    out = subprocess.run([PY, str(FAST), str(ROOT)], cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout
    for line in out.splitlines():
        m = re.match(r"\[(\w+)\s*\]\s+(\S+)", line)
        if m and m.group(2) == check_id:
            return m.group(1)
    return "MISSING"


def plant(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"planting anchor not found in {path.name}: {old[:60]}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


def plant_json(path: pathlib.Path, mutate) -> None:
    body = json.loads(path.read_text(encoding="utf-8"))
    mutate(body)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


CASES = []


def case(check_id, label, mutate):
    CASES.append((check_id, label, mutate))


# ------------------------------------------------------ P-31 the credential
case("P-31", "the verifier becomes a plain hash",
     lambda: plant(CREDENTIALS, "KDF_ITERATIONS = 210_000",
                   "KDF_ITERATIONS = 210_000\nKDF_ITERATIONS = 1000"))

case("P-31", "the secret starts appearing in the key's repr",
     lambda: plant(CREDENTIALS, "    secret: str = field(repr=False, default=\"\")",
                   "    secret: str = \"\""))

case("P-31", "comparison stops being constant-time",
     lambda: plant(CREDENTIALS, "    return hmac.compare_digest(candidate, bytes(verifier))",
                   "    return candidate == bytes(verifier)\n"
                   "    return hmac.compare_digest(candidate, bytes(verifier))"))

case("P-31", "the work factor stops being checked at derivation",
     lambda: plant(CREDENTIALS, "    if iterations < 100_000:", "    if False:"))

case("P-31", "a malformed credential starts parsing",
     lambda: plant(CREDENTIALS, "    if not match:\n        raise CredentialError(\"not a credential\")",
                   "    if not match:\n"
                   "        return PresentedCredential(organization_id='x',\n"
                   "                                   key_id='y', secret='z')"))

case("P-31", "every key starts sharing one salt",
     lambda: plant(CREDENTIALS, "    salt = os.urandom(SALT_BYTES)",
                   "    salt = b'0' * SALT_BYTES"))

# ------------------------------------------------------- P-32 the guard
case("P-32", "a route stops carrying its guard",
     lambda: plant(APP, '    @app.get("/roles")\n    def list_roles(\n'
                        '            principal: TenantPrincipal = Depends(_guard("GET", "/roles"))):',
                   '    @app.get("/roles")\n    def list_roles(\n'
                   '            principal: TenantPrincipal = None):'))

case("P-32", "a route enforces something the contract does not declare",
     lambda: plant(APP, 'Depends(_guard("GET", "/api-keys"))',
                   'Depends(_guard("GET", "/roles"))'))

case("P-32", "an application may be built with no authenticator",
     lambda: plant(APP, "    if authenticator is None:\n        raise ValueError(",
                   "    if False:\n        raise ValueError("))

case("P-32", "the guard stops requiring a permission from the contract",
     lambda: plant(APP, "        if not permission:\n            raise contract.ContractError(",
                   "        permission = permission or 'run:read'\n"
                   "        if False:\n            raise contract.ContractError("))

# ------------------------------------------------------- P-33 the decision
case("P-33", "an empty authorization starts permitting things",
     lambda: plant(RBAC, "        for grant in self.grants:",
                   "        return True\n        for grant in self.grants:"))

case("P-33", "a project-scoped grant widens to the organization",
     lambda: plant(RBAC, "            if project_id is not None and grant.project_id == project_id:",
                   "            if True:"))

case("P-33", "an unrecognised permission starts returning a verdict",
     lambda: plant(RBAC, "        if permission not in PERMISSIONS:", "        if False:"))

# ------------------------------------------------------------ P-34 the audit
case("P-34", "the runtime role is granted the ability to edit an audit event",
     lambda: plant(SCHEMA04, "GRANT SELECT, INSERT ON clep.audit_event TO clep_runtime;",
                   "GRANT SELECT, INSERT, UPDATE ON clep.audit_event TO clep_runtime;"))

case("P-34", "the audit writer stops recording a justification",
     lambda: plant(AUDIT, "         justification, target_content_digest))",
                   "         None, None))"))

case("P-34", "the audit cursor goes back to comparing the identifier alone",
     lambda: plant(SECURITY_SERVICE,
                   '"   AND (%s::uuid IS NULL OR (occurred_at, id) < "',
                   '"   AND (%s::uuid IS NULL OR id < "'))

# ----------------------------------------------------------- P-35 the erasure
case("P-35", "content is destroyed before the runs are demoted",
     lambda: plant(ERASURE, '    _advance(conn, org, erasure_id, "demoting")',
                   '    _advance(conn, org, erasure_id, "zdemoting")'))

case("P-35", "erasure starts destroying gate evidence",
     lambda: plant(ERASURE, "        \"   AND artifact_class <> 'gate_evidence' AND erased_at IS NULL \"\n"
                            "        \"RETURNING id\", (audit_id, org, live)).fetchall())",
                   "        \"   AND erased_at IS NULL \"\n"
                   "        \"RETURNING id\", (audit_id, org, live)).fetchall())"))

case("P-35", "runs stop being demoted at all",
     lambda: plant(ERASURE, "        \"UPDATE clep.run SET reproducibility = 'auditable' \"",
                   "        \"UPDATE clep.run SET completeness = completeness \""))

case("P-35", "the store stops requiring verification before completion",
     lambda: plant(SCHEMA04, "CONSTRAINT ck_erasure_request__verified_on_completion",
                   "CONSTRAINT ck_erasure_request__verified_on_completion_removed"))

case("P-35", "verification counts the update rather than what survives",
     lambda: plant(ERASURE, '"   AND payload_ref IS NOT NULL", (org, live)).fetchone()[0]',
                   '"   AND erased_at IS NULL", (org, live)).fetchone()[0]'))

# ------------------------------------------------------------ P-36 the limiter
case("P-36", "the limiter starts failing open",
     lambda: plant(LIMITS, "            raise LimiterUnavailable(",
                   "            return Verdict(True, 0.0)\n"
                   "            raise LimiterUnavailable("))

case("P-36", "every tenant shares one bucket",
     lambda: plant(LIMITS, 'f"{self._prefix}:{organization_id}"',
                   'f"{self._prefix}:shared"'))

case("P-36", "the bucket stops emptying",
     lambda: plant(LIMITS, "if tokens >= 1 then\n    tokens = tokens - 1\n    allowed = 1\nend",
                   "if tokens >= 0 then\n    allowed = 1\nend"))

case("P-36", "a refusal stops saying when to try again",
     lambda: plant(LIMITS, "        return Verdict(\n            False, remaining,",
                   "        return Verdict(\n            False, remaining, \"\") if True else Verdict(\n"
                   "            False, remaining,"))

# ------------------------------------------------------------- P-37 the grant
case("P-37", "an ungranted evaluator starts running anyway",
     lambda: plant(EVALUATOR_SDK, "    if withheld:", "    if False:"))

case("P-37", "the default grant starts permitting everything",
     lambda: plant(GRANTS, "        return capability in self.capabilities",
                   "        return True"))

case("P-37", "an unenforceable capability becomes grantable",
     lambda: plant(GRANTS, "        if unknown:\n            raise GrantError(",
                   "        if False:\n            raise GrantError("))

case("P-37", "a refused evaluator starts carrying a score",
     lambda: plant(EVALUATOR_SDK, '        return EvaluatorOutcome(\n            "unavailable",\n'
                                  '            unavailable_reason=(\n'
                                  '                f"{registration.version_key} declares capability "',
                   '        return EvaluatorOutcome(\n            "scored", Decimal(0),\n'
                   '            unavailable_reason=(\n'
                   '                f"{registration.version_key} declares capability "'))

# ----------------------------------------------------------- P-38 the redaction
case("P-38", "the judge prompt stops being redacted",
     lambda: plant(JUDGE_SDK, "        return redact_credentials(cleaned), changed",
                   "        return cleaned, changed"))

case("P-38", "a provider key stops being a shape worth removing",
     lambda: plant(PRIVACY, '    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "provider key"),', ""))

case("P-38", "a provider credential becomes reportable",
     lambda: plant(PRIVACY, 'DataClass("DS-7", "provider credential", judge=False, report=False,',
                   'DataClass("DS-7", "provider credential", judge=True, report=True,'))

case("P-38", "evaluated content becomes loggable",
     lambda: plant(PRIVACY,
                   'DataClass("DS-3", "retrieved context", judge=True, report=True, log=False),',
                   'DataClass("DS-3", "retrieved context", judge=True, report=True, log=True),'))

case("P-38", "withheld content starts reproducing what it withheld",
     lambda: plant(PRIVACY, '        return f"[withheld: {declared.code} {declared.label}]"',
                   "        return text"))

# ---------------------------------------------------------- P-39 the transport
case("P-39", "an unencrypted connection is accepted in production",
     lambda: plant(CONFIG, "        if not secure:", "        if False:"))

case("P-39", "sslmode=prefer starts counting as encryption",
     lambda: plant(CONFIG, '        secure = ("sslmode=require" in lowered',
                   '        secure = ("sslmode=" in lowered or "sslmode=require" in lowered'))

case("P-39", "every environment becomes a local one",
     lambda: plant(CONFIG, 'LOCAL_ENVIRONMENTS = ("local", "test")',
                   'LOCAL_ENVIRONMENTS = ("local", "test", "production")'))

# ------------------------------------------------------------- P-40 the scan
case("P-40", "the recorded scan starts carrying a finding",
     lambda: plant_json(SCAN_EVIDENCE, lambda b: b["findings"].append(
         {"package": "example", "version": "1.0", "id": "GHSA-planted",
          "summary": "planted", "severity": []})))

case("P-40", "the recorded scan could not reach its source",
     lambda: plant_json(SCAN_EVIDENCE,
                        lambda b: b["unreachable"].append("example: URLError")))

case("P-40", "the policy starts passing when the source is unreachable",
     lambda: plant_json(SCAN_EVIDENCE, lambda b: b["policy"].__setitem__(
         "advisorySourceUnreachable", "warn")))

case("P-40", "the scan quietly stops covering a declared dependency",
     lambda: plant_json(SCAN_EVIDENCE, lambda b: b.__setitem__(
         "declared", [d for d in b["declared"] if d != "psycopg"])))

# --------------------------------------------------------------- P-41 the debt
case("P-41", "the comparison guard is removed",
     lambda: plant(SCHEMA, "CREATE TRIGGER trg_comparison__evaluator_version_is_reachable",
                   "CREATE TRIGGER trg_comparison__disabled"))

case("P-41", "the invocation guard is removed, reintroducing D-1's shape",
     lambda: plant(SCHEMA, "CREATE TRIGGER trg_evaluator_invocation__evaluator_version_is_reachable",
                   "CREATE TRIGGER trg_evaluator_invocation__disabled"))

case("P-41", "an open debt disappears from the register instead of being closed",
     lambda: plant(DEBT, "## D-5 — `REQ-F-12-1` names three scopes",
                   "## D-omitted — "))

# ------------------------------------------------------- P-11, P-12, P-16, P-17
case("P-11", "an operation stops declaring the permission it requires",
     lambda: plant_json(CONTRACT, lambda b: b["paths"]["/api-keys"]["get"].pop(
         "x-permission")))

case("P-11", "an operation starts taking a tenant in its path",
     lambda: plant_json(CONTRACT, lambda b: b["paths"].__setitem__(
         "/organizations/{organizationId}/api-keys",
         b["paths"]["/api-keys"])))

case("P-12", "the code's permissions drift from the contract",
     lambda: plant(RBAC, '    "audit:read", "credential:manage", "role:grant",',
                   '    "audit:read", "credential:manage",'))

case("P-12", "the store's permission vocabulary drifts from the contract",
     lambda: plant(SCHEMA, "        'audit:read', 'credential:manage', 'role:grant',",
                   "        'audit:read', 'credential:manage', 'role:grant', 'run:purge',"))

case("P-16", "a Phase 12 table loses its non-nullable tenant",
     lambda: plant(SCHEMA, "    organization_id       uuid NOT NULL REFERENCES clep.organization (id),\n"
                           "    principal_kind        text NOT NULL,",
                   "    organization_id       uuid REFERENCES clep.organization (id),\n"
                   "    principal_kind        text NOT NULL,"))

case("P-16", "a Phase 12 table loses FORCE row-level security",
     lambda: plant(SCHEMA, "ALTER TABLE clep.api_key FORCE  ROW LEVEL SECURITY;", ""))

case("P-17", "a Phase 12 foreign key stops carrying the tenant",
     lambda: plant(SCHEMA, "    CONSTRAINT fk_role_binding__project\n"
                           "        FOREIGN KEY (organization_id, project_id)",
                   "    CONSTRAINT fk_role_binding__project\n"
                   "        FOREIGN KEY (project_id, organization_id)"))

case("P-15", "a tenant's quota period loses its one-row-per-period key",
     lambda: plant(SCHEMA, "CONSTRAINT uq_quota_consumption__organization_period",
                   "CONSTRAINT uq_quota_consumption__organization_period_removed"))

case("P-14", "an ADR goes missing from the index",
     lambda: plant(ROOT / "docs/adr/README.md",
                   "| [ADR-020](ADR-020-authorization-model.md)", "| [ADR-020x]("))

# ----------------------------------------------------------- P-26 closure
case("P-26", "an earlier gate stops re-running the one before it",
     lambda: plant(PHASE11, "docs/evidence/phase-10/check_phase10.py",
                   "docs/evidence/phase-10/check_phase10_skipped.py"))


def main() -> int:
    rebuild_fast()
    if dirty():
        print("REFUSING: the tree is dirty; a restore would discard uncommitted work")
        print(dirty())
        return 2

    caught, leaked = 0, []
    for check_id, label, mutate in CASES:
        try:
            mutate()
            status = status_of(check_id)
        finally:
            restore()
        survivors = dirty()
        if survivors:
            leaked.append(f"{check_id} {label}: {survivors.splitlines()[:3]}")
        ok = status == "FAIL"
        caught += ok
        print(f"[{'CAUGHT' if ok else 'MISSED':<7}] {check_id:<6} {label} "
              f"({'reported FAIL' if ok else 'reported ' + status})")

    print(f"\nself-test: {caught}/{len(CASES)} planted violations caught")
    if leaked:
        print(f"RESTORATION FAILED: {len(leaked)} plant(s) survived")
        for line in leaked:
            print(f"  - {line}")
        return 1
    print("restoration verified: the working tree matches HEAD after every case")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
