"""Phase 7 comprehensive validation, with regression over every earlier phase.

Phase 7 decides whether software ships. The checks that matter most are the ones
that would catch a gate which looks like it is working: a threshold applied in the
wrong order, an abstention quietly counted as a pass, a decision that can be
edited after the fact, a statistical parameter that acquired a default nobody
chose. None of those announce themselves, and each produces release decisions
that read as perfectly reasonable.

Earlier phase gates are re-evaluated against their OWN trees, in isolated clones
pruned to their own history, for the reasons Phase 5 and Phase 6 established: a
phase-boundary assertion becomes historical the moment the next phase starts, a
worktree shares the object database so a blob scan sees the future, `main` must
be reset rather than deleted so the re-run gate can find its own commits, and
PYTHONPATH must point at the clone or the editable install imports the present.

Usage: python docs/evidence/phase-7/check_phase7.py <repo_root>
Exits non-zero on any FAIL.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PY = os.environ.get("CLEP_TEST_PYTHON", sys.executable)
results = []

_F = ["Co-Authored" + "-By", "Anthro" + "pic", "Cla" + "ude", "Cop" + "ilot",
      "Approved" + "-by", "Assisted" + "-by", "Reviewed" + "-by"]
ATT = [rf"\b{f}\b" for f in _F] + [r"AI[- ]assist(ed|ant)", r"AI[- ]" + "generated",
                                   "generated" + r"\s+with"]
SECRETS = [
    (r"sk-[A-Za-z0-9]{16,}", "provider key"),
    (r"gh[pousr]_[A-Za-z0-9]{16,}", "forge token"),
    (r"AKIA[0-9A-Z]{16}", "cloud key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "JWT"),
    (r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
     "inline credential"),
    (r"(?i)://[^/\s:@]+:[^/\s:@]+@", "credential in URL"),
]


def add(cid, status, detail, defects=None):
    results.append({"id": cid, "status": status, "detail": detail})
    print(f"[{status:<7}] {cid:<6} {detail}")
    for d in (defects or [])[:6]:
        print(f"           - {d}")


def git(*args, cwd=None):
    return subprocess.run(["git", *args], cwd=str(cwd or ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def run(cmd, cwd=None, timeout=2400, env=None):
    p = subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout, env=env)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def text_files(base: Path):
    """Skip hidden DIRECTORIES, scan hidden FILES — `.env.example` is committed
    and its whole purpose is to show configuration."""
    for p in sorted(base.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(base)
        if any(part.startswith(".") for part in rel.parts[:-1]):
            continue
        if "__pycache__" in rel.parts or rel.suffix in (".pyc",):
            continue
        yield p, rel


# ===================================================== P-1 the suite executes
code, out = run([PY, "-m", "pytest", "-q", "-p", "no:warnings", "--color=no",
                 "--cov", "--cov-report=term"])
passed = re.search(r"(\d+) passed", out)
cov = re.search(r"Total coverage: ([\d.]+)%", out)
gate = re.search(r"Required test coverage of ([\d.]+)% reached", out)
add("P-1", "PASS" if code == 0 else "FAIL",
    f"test suite: exit {code}; {passed.group(1) if passed else '?'} passed; "
    f"coverage {cov.group(1) if cov else '?'}% against a "
    f"{gate.group(1) if gate else '?'}% gate",
    [] if code == 0 else out.strip().splitlines()[-8:])

# =================================================== P-2 schema conformance
code, out = run([PY, "docs/evidence/phase-4/check_schema_conformance.py", "."])
tables_parsed = re.search(r"(\d+) table\(s\) parsed", out)
add("P-2", "PASS" if code == 0 else "FAIL",
    f"schema conformance: exit {code}; "
    f"{tables_parsed.group(1) if tables_parsed else '?'} tables including the "
    f"Phase 7 baseline, policy and decision tables")

# ======================================================== P-3 traceability
code, out = run([PY, "docs/evidence/phase-3/generate_traceability.py", "."])
nums = dict(re.findall(r"(traced to an artifact|implementation layer|test layer|"
                       r"deferred with an owner)\s*:\s*(\d+)", out))
add("P-3", "PASS" if code == 0 else "FAIL",
    f"traceability: exit {code}. " + "; ".join(f"{k}: {v}" for k, v in nums.items()))

# ==================================================== P-4 matrix currency
matrix = ROOT / "docs/evidence/phase-3/traceability-matrix.md"
before = matrix.read_text(encoding="utf-8") if matrix.exists() else ""
run([PY, "docs/evidence/phase-3/generate_traceability.py", ".", "--write"])
after = matrix.read_text(encoding="utf-8") if matrix.exists() else ""
add("P-4", "PASS" if before == after else "FAIL",
    "the committed traceability matrix regenerates identically"
    if before == after else "the committed matrix is stale; regenerate it")


# ==================== P-5..P-8 earlier gates, at the trees and histories they mean
def gate_at_its_own_tree(cid, grep, script, label):
    """Re-run an earlier gate against the history it was written for.

    Three conditions, each learned by getting it wrong. An isolated clone rather
    than a worktree, because a worktree shares the object database and a blob
    scan run in one sees commits made after it. `main` RESET to the target commit
    rather than deleted, because a gate that searches history with
    `rev-list --grep ... main` needs a `main`. PYTHONPATH at the clone's `src`,
    because the package is installed editable against the working tree and
    without it the clone's tests import the *present* implementation — which the
    Phase 5 gate duly reported as its own suite failing, a misconfiguration
    reporting a regression.
    """
    sha = git("rev-list", "-1", f"--grep={grep}", "main").strip()
    if not sha:
        add(cid, "FAIL", f"could not locate the {label} commit on main")
        return
    work = Path(tempfile.mkdtemp(prefix="clep-gate-"))
    tree = work / "tree"
    subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT), str(tree)],
                   capture_output=True, text=True)
    subprocess.run(["git", "checkout", "--quiet", "--detach", sha],
                   cwd=str(tree), capture_output=True, text=True)
    refs = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"],
        cwd=str(tree), capture_output=True, text=True).stdout.split()
    for ref in refs:
        subprocess.run(["git", "update-ref", "-d", ref], cwd=str(tree),
                       capture_output=True, text=True)
    subprocess.run(["git", "branch", "--quiet", "main", sha], cwd=str(tree),
                   capture_output=True, text=True)
    for cmd in (["remote", "remove", "origin"],
                ["reflog", "expire", "--expire=now", "--all"],
                ["gc", "--prune=now", "--quiet"]):
        subprocess.run(["git", *cmd], cwd=str(tree), capture_output=True, text=True)
    remaining = subprocess.run(["git", "rev-list", "--all", "--count"], cwd=str(tree),
                               capture_output=True, text=True).stdout.strip()
    docx = next(ROOT.glob("*.docx"), None)
    if docx:
        shutil.copy2(docx, tree / docx.name)
    env = dict(os.environ, PYTHONPATH=str(tree / "src"))
    c, o = run([PY, str(tree / script), str(tree)], cwd=tree, env=env)
    summary = re.search(r"SUMMARY: (.*)", o)
    fails = [l.strip()[:150] for l in o.splitlines() if l.startswith("[FAIL")]
    shutil.rmtree(work, ignore_errors=True)
    add(cid, "PASS" if c == 0 else "FAIL",
        f"{label} gate re-evaluated against its own history ({sha[:8]}), in an "
        f"isolated clone pruned to {remaining} reachable commit(s): exit {c} "
        f"{summary.group(1) if summary else ''}", fails)


gate_at_its_own_tree("P-5", "docs(spike)",
                     "docs/evidence/spike-sprint/check_spike_sprint.py",
                     "Technology Spike Sprint")
gate_at_its_own_tree("P-6", "docs(phase-4)",
                     "docs/evidence/phase-4/check_phase4.py", "Phase 4")
gate_at_its_own_tree("P-7", "phase-5)",
                     "docs/evidence/phase-5/check_phase5.py", "Phase 5")
# "phase-6)" matches the delivery commit and both finalization commits; rev-list
# returns the newest, which is Phase 6 as it was accepted.
gate_at_its_own_tree("P-8", "phase-6)",
                     "docs/evidence/phase-6/check_phase6.py", "Phase 6")

# ========================================== P-9 Phase 1 milestone validators
m1 = []
for script, label in (("docs/evidence/M1.1/check_m11.py", "M1.1 documents"),
                      ("docs/evidence/M1.2/check_m12.py", "M1.2 competitive"),
                      ("docs/evidence/M1.3/check_m13.py", "M1.3 requirements")):
    if (ROOT / script).exists():
        c, o = run([PY, script, "."])
        s = re.search(r"SUMMARY: (.*)", o)
        m1.append(f"{label}: exit {c} {s.group(1) if s else ''}")
add("P-9", "PASS" if all("exit 0" in x for x in m1) and m1 else "FAIL",
    "Phase 1 milestone validators re-run. " + "; ".join(m1))

# ===================================== P-10 the contract leads, not follows
sys.path.insert(0, str(ROOT / "src"))
contract_defects = []
ops = []
try:
    from clep.api import contract as _contract
    ops = _contract.operations(str(ROOT))
    if len(ops) != 24:
        contract_defects.append(f"expected 24 declared operations, found {len(ops)}")
    generated = list(ROOT.glob("**/openapi_generated*")) + \
        [p for p in ROOT.glob("src/**/*.py") if "openapi.json" in p.read_text("utf-8")
         and "write_text" in p.read_text("utf-8")]
    if generated:
        contract_defects.append(f"something writes the contract: {generated}")
    # The defect Phase 7 was written to close: a request field that names
    # something no operation can create.
    ids = {_contract.operation_id(m, p, str(ROOT)) for m, p in ops}
    if "createGatePolicy" not in ids or "addGatePolicyVersion" not in ids:
        contract_defects.append(
            "gatePolicyVersionId can be cited but not created; the contract "
            "declares a reference to something no operation produces")
except Exception as e:
    contract_defects.append(f"{type(e).__name__}: {e}")
add("P-10", "PASS" if not contract_defects else "FAIL",
    f"contract declares {len(ops)} operations, is read and never written, and "
    f"every identifier it accepts can be created through it", contract_defects)

# ============================ P-11 schema, contract and code share vocabularies
vocab_defects = []
sql = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted((ROOT / "docs/data/schema").glob("*.sql")))
sql_no_comments = re.sub(r"--[^\n]*", "", sql)


def ddl_enum(constraint):
    m = re.search(rf"CONSTRAINT\s+{constraint}\s+CHECK\s*\((.*?)\)\s*[,)]",
                  sql_no_comments, re.S)
    return set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()


pairs = []
try:
    pairs = [
        ("run resolution", ddl_enum("ck_run_sample__resolution"),
         set(_contract.enum_of("SampleResolution", str(ROOT)))),
        ("run completeness", ddl_enum("ck_run__completeness"),
         set(_contract.enum_of("Completeness", str(ROOT)))),
        ("integration tier", ddl_enum("ck_run__integration_tier"),
         set(_contract.enum_of("IntegrationTier", str(ROOT)))),
        ("execution state", ddl_enum("ck_run__execution_state"),
         set(_contract.enum_of("ExecutionState", str(ROOT)))),
        ("version state", ddl_enum("ck_prompt_version__state"),
         set(_contract.enum_of("VersionState", str(ROOT)))),
        ("identity component kind", ddl_enum("ck_run_identity_component__kind"),
         set(_contract.enum_of("IdentityComponentKind", str(ROOT)))),
        ("reproduction outcome", ddl_enum("ck_reproduction_attempt__outcome"),
         set(_contract.enum_of("ReproductionOutcome", str(ROOT)))),
        ("reproduction reason", ddl_enum("ck_reproduction_gap__reason"),
         set(_contract.enum_of("ReproductionReason", str(ROOT)))),
        ("criterion dimension", ddl_enum("ck_gate_criterion__dimension"),
         set(_contract.enum_of("CriterionDimension", str(ROOT)))),
        ("criterion source", ddl_enum("ck_gate_criterion__source"),
         set(_contract.enum_of("CriterionSource", str(ROOT)))),
        ("metric direction", ddl_enum("ck_gate_criterion__direction"),
         set(_contract.enum_of("MetricDirection", str(ROOT)))),
        ("criterion action", ddl_enum("ck_gate_criterion__on_regression"),
         set(_contract.enum_of("CriterionAction", str(ROOT)))),
        ("comparison classification", ddl_enum("ck_comparison__classification"),
         set(_contract.enum_of("Classification", str(ROOT)))),
        ("comparison result kind", ddl_enum("ck_comparison__result_kind"),
         set(_contract.enum_of("ComparisonResultKind", str(ROOT)))),
        ("rule fired", ddl_enum("ck_gate_criterion_result__rule_fired"),
         set(_contract.enum_of("RuleFired", str(ROOT)))),
    ]
    for label, ddl, api in pairs:
        if not ddl:
            vocab_defects.append(f"{label}: no constraint found in the schema")
        elif ddl != api:
            vocab_defects.append(f"{label}: schema {sorted(ddl)} != contract {sorted(api)}")
    from clep.evaluators.sdk import RESOLUTIONS
    if set(RESOLUTIONS) != set(_contract.enum_of("SampleResolution", str(ROOT))):
        vocab_defects.append("the evaluator SDK's resolutions disagree with the contract")
    from clep.experiments.identity import CAPTURED_KINDS
    if set(CAPTURED_KINDS) != ddl_enum("ck_run_identity_component__kind"):
        vocab_defects.append(
            "the identity module's component kinds disagree with the schema")
    # The statistics module states the classification vocabulary a fourth time.
    from clep.regression.statistics import CLASSIFICATIONS
    contract_classifications = set(_contract.enum_of("Classification", str(ROOT)))
    if not set(CLASSIFICATIONS) < contract_classifications:
        vocab_defects.append(
            "the statistics module's classifications are not a strict subset of "
            "the contract's; it must not be able to return not_comparable, which "
            "is decided from versions rather than from numbers")
except Exception as e:
    vocab_defects.append(f"{type(e).__name__}: {e}")
add("P-11", "PASS" if not vocab_defects else "FAIL",
    f"vocabularies compared across schema, contract and code: {len(pairs) + 3}; "
    f"disagreements: {len(vocab_defects)}", vocab_defects)

# ================================== P-12 every dependency carries a justification
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
deps = set(re.findall(r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?[><=!]', pyproject))
justified = (ROOT / "docs/dependencies.md").read_text(encoding="utf-8")
unjustified = sorted(d for d in deps if f"`{d}`" not in justified)
add("P-12", "PASS" if not unjustified else "FAIL",
    f"{len(deps)} declared dependencies, each with a recorded reason and a rejected "
    f"alternative; unjustified: {unjustified or 'none'}. Phase 7 added none — the "
    f"bootstrap is the standard library.")

# ===================================== P-13 no undecided ADR blocks this phase
adr_dir = ROOT / "docs/adr"
adrs = sorted(adr_dir.glob("ADR-*.md"))
undecided = [p.name for p in adrs if "NOT DECIDED" in p.read_text(encoding="utf-8")]
add("P-13", "PASS" if len(adrs) == 16 and not undecided else "FAIL",
    f"{len(adrs)} ADRs; undecided: {undecided or 'none'}")

# ================== P-14 idempotency on every externally visible effect table
effect_tables = {"run_sample": "uq_run_sample__idempotency_key",
                 "sample_cost": "uq_sample_cost__idempotency_key",
                 "run": "uq_run__idempotency_key"}
missing_keys = [t for t, c in effect_tables.items() if c not in sql]
add("P-14", "PASS" if not missing_keys else "FAIL",
    f"every externally visible effect carries a unique idempotency key "
    f"({len(effect_tables)} tables); missing: {missing_keys or 'none'}")

# ========================== P-15 row-level security on every tenant-scoped table
tables = dict(re.findall(r"CREATE TABLE clep\.(\w+)\s*\((.*?)\n\);", sql, re.S))
tenant_scoped = [t for t in tables if t != "organization"]
rls_defects = []
for t in tenant_scoped:
    if not re.search(rf"ALTER TABLE clep\.{t}\s+ENABLE ROW LEVEL SECURITY", sql):
        rls_defects.append(f"{t} has no ENABLE")
    if not re.search(rf"ALTER TABLE clep\.{t}\s+FORCE\s+ROW LEVEL SECURITY", sql):
        rls_defects.append(f"{t} has no FORCE; the owner would bypass every policy")
add("P-15", "PASS" if not rls_defects else "FAIL",
    f"{len(tenant_scoped)} tenant-scoped tables, each with ENABLE and FORCE",
    rls_defects)

# ======================= P-16 composite FK targets have a matching unique key
fk_targets = set(re.findall(
    r"REFERENCES\s+clep\.(\w+)\s*\(\s*organization_id\s*,\s*id\s*\)", sql))
has_uq = {t for t, body in tables.items()
          if re.search(r"UNIQUE\s*\(\s*organization_id\s*,\s*id\s*\)", body)}
missing_uq = sorted(fk_targets - has_uq)
add("P-16", "PASS" if not missing_uq else "FAIL",
    f"{len(fk_targets)} composite foreign-key targets, each with a matching unique "
    f"constraint; missing: {missing_uq or 'none'}")

# ============================== P-17 the schema is the migration set, not a copy
mig = (ROOT / "src/clep/db/migrations.py").read_text(encoding="utf-8")
copies = [str(p.relative_to(ROOT)) for p in ROOT.glob("src/**/*.sql")]
add("P-17", "PASS" if "docs" in mig and "schema" in mig and not copies else "FAIL",
    f"migrations are applied from docs/data/schema/ with no second copy of the DDL; "
    f"copies found: {copies or 'none'}")

# ===================== P-18 a gate decision is audit-class in the STORE
#
# REQ-N-COMP-1 and ADR-016. A decision that can be rewritten after the fact is
# not evidence that a release was justified; it is evidence that someone had
# write access. Enforced by triggers as well as grants, because a grant is one
# migration away from being widened and the trigger is the thing that survives it.
audit_class = ("gate_decision", "comparison", "gate_criterion_result",
               "policy_exception")
immutability = []
for table in audit_class:
    if not re.search(rf"CREATE TRIGGER trg_{table}__immutable", sql):
        immutability.append(f"{table} has no immutability trigger")
    if re.search(rf"GRANT[^;]*\b(UPDATE|DELETE)\b[^;]*clep\.{table}\b", sql):
        immutability.append(f"the runtime role is granted UPDATE or DELETE on {table}")
if "refuse_change_to_audit_record" not in sql:
    immutability.append("the audit-class immutability function is absent")
for table in ("gate_policy_version", "gate_criterion"):
    if not re.search(rf"CREATE TRIGGER trg_{table}__immutable", sql):
        immutability.append(f"{table} may change after a decision cites it")
add("P-18", "PASS" if not immutability else "FAIL",
    f"audit-class immutability enforced by the store: {len(audit_class)} tables "
    f"plus the policy version and its criteria; defects: {len(immutability)}",
    immutability)

# ============================ P-19 ADR-007 is implemented as decided, not as read
#
# The four parameters ADR-007 refused to set must have no defaults anywhere. A
# default is a value nobody chose, applied to every tenant, and it would make the
# ADR's most deliberate omission invisible.
stats_defects = []
try:
    import inspect

    from clep.regression import statistics as _stats
    signature = inspect.signature(_stats.compare)
    for parameter in ("confidence_level", "precision_threshold",
                      "minimum_sample_size", "resamples", "seed"):
        if signature.parameters[parameter].default is not inspect.Parameter.empty:
            stats_defects.append(
                f"{parameter} has a default; ADR-007 left it unset deliberately "
                f"and a default sets it for the whole product")
    from decimal import Decimal

    def pairs_of(baseline, candidate):
        return [_stats.Pair(str(i), Decimal(str(b)), Decimal(str(c)))
                for i, (b, c) in enumerate(zip(baseline, candidate))]

    # Deliberately varied. The first version of this check used a constant
    # baseline and a constant candidate, so every paired difference was
    # identical, the interval had zero width, and the width rule below could
    # never fire however broken it was. A check that cannot fail proves nothing.
    base = [0.50 + (i % 7) * 0.05 for i in range(40)]
    worse = [b - 0.20 + ((i % 5) - 2) * 0.06 for i, b in enumerate(base)]
    common = dict(confidence_level=Decimal("0.95"), minimum_sample_size=None,
                  resamples=200, seed=1)
    # The width rule is checked FIRST. An interval that excludes zero but is
    # wider than the configured precision must still abstain: ADR-007's table is
    # ordered, and its spike measured the bootstrap declining nine times in ten
    # at n=20 even when the true effect was large.
    tight = _stats.compare(pairs_of(base, worse), direction="higher_is_better",
                           precision_threshold=Decimal("1"), **common)
    if tight.classification != "regression":
        stats_defects.append("a clear regression was not classified as one")
    wide = _stats.compare(pairs_of(base, worse), direction="higher_is_better",
                          precision_threshold=Decimal("0.001"), **common)
    if wide.classification != "insufficient_evidence":
        stats_defects.append(
            "an interval wider than the precision threshold was classified "
            "anyway; the width rule is not being applied first")
    # Direction is per metric. ADR-007 speaks of an interval below zero as a
    # regression, which holds only where higher is better.
    flipped = _stats.compare(pairs_of(base, worse), direction="lower_is_better",
                             precision_threshold=Decimal("1"), **common)
    if flipped.classification != "improvement":
        stats_defects.append(
            "direction is not applied; a metric where lower is better would "
            "report its improvements as regressions")
    # No precision threshold, no verdict.
    unset = _stats.compare(pairs_of(base, base), direction="higher_is_better",
                           precision_threshold=None, **common)
    if unset.classification != "insufficient_evidence":
        stats_defects.append(
            "a criterion with no precision threshold produced a verdict; the "
            "value ADR-007 declined to set has been invented somewhere")
    if _stats.compare(pairs_of(base, worse), direction="higher_is_better",
                      precision_threshold=Decimal("1"), confidence_level=Decimal("0.95"),
                      minimum_sample_size=None, resamples=200, seed=1) != tight:
        stats_defects.append("the same inputs and seed produced a different interval")
except Exception as e:
    stats_defects.append(f"{type(e).__name__}: {e}")
add("P-19", "PASS" if not stats_defects else "FAIL",
    f"ADR-007 enforced mechanically: no defaulted parameters, width rule first, "
    f"direction applied, no verdict without a precision threshold, and "
    f"reproducible from the seed; defects: {len(stats_defects)}", stats_defects)

# ============================ P-20 ADR-016 composition holds where it is decided
composition_defects = []
try:
    if "pass" in _contract.enum_of("CriterionAction", str(ROOT)):
        composition_defects.append(
            "a policy can map an abstention to a pass, erasing the REQ-F-08-4 "
            "distinction at the point where it costs something")
    if "pass" in ddl_enum("ck_gate_criterion__on_insufficient_evidence"):
        composition_defects.append("the schema permits mapping an abstention to a pass")
    engine_src = (ROOT / "src/clep/regression/engine.py").read_text(encoding="utf-8")
    floor = engine_src.index("absolute_floor")
    tolerance = engine_src.index("relative_tolerance", floor)
    classification = engine_src.index("INSUFFICIENT_EVIDENCE", floor)
    if not floor < classification < tolerance:
        composition_defects.append(
            "the threshold order is not floor, then classification, then "
            "tolerance; ADR-016 makes the order a correctness property")
    if "SEVERITY" not in engine_src:
        composition_defects.append("the decision outcome is not derived from severity")
    from clep.regression.engine import SEVERITY
    if SEVERITY["hard_fail"] <= SEVERITY["warning"]:
        composition_defects.append("a warning outranks a hard fail")
    if "exception_applied" in ddl_enum("ck_gate_decision__evaluated_outcome"):
        composition_defects.append(
            "an exception outcome can be STORED, which means a decision is edited "
            "when one is granted rather than annotated")
except Exception as e:
    composition_defects.append(f"{type(e).__name__}: {e}")
add("P-20", "PASS" if not composition_defects else "FAIL",
    f"ADR-016 enforced mechanically: abstention cannot map to pass, thresholds "
    f"apply in order, severity governs the outcome, and an exception never edits "
    f"a decision; defects: {len(composition_defects)}", composition_defects)

# ================= P-21 REQ-F-08-5: an unscored sample is never a zero
zero_defects = []
try:
    engine_src = (ROOT / "src/clep/regression/engine.py").read_text(encoding="utf-8")
    scored_guards = len(re.findall(r"resolution\s*=\s*'scored'", engine_src))
    if scored_guards < 2:
        zero_defects.append(
            f"the evaluator pairing guards resolution on {scored_guards} side(s); "
            f"both are needed or an example the candidate failed enters the "
            f"comparison")
    for suspicious in (r"or\s+0\b", r"COALESCE\([^)]*,\s*0\s*\)", r"fillna",
                       r"score\s*or\s*Decimal\(0\)"):
        if re.search(suspicious, engine_src):
            zero_defects.append(f"a zero substitution appears in the engine: {suspicious}")
    if not re.search(r"ck_run_sample__score_only_when_scored", sql):
        zero_defects.append("the store permits a score on an unscored sample")
except Exception as e:
    zero_defects.append(f"{type(e).__name__}: {e}")
add("P-21", "PASS" if not zero_defects else "FAIL",
    f"REQ-F-08-5: unscored samples are excluded rather than zeroed, in the engine "
    f"and in the store; defects: {len(zero_defects)}", zero_defects)

# ========== P-22 REQ-F-08-6: deterministic and probabilistic stay separate
separation_defects = []
try:
    if "result_kind" not in tables.get("comparison", ""):
        separation_defects.append(
            "a comparison does not record whether it came from a deterministic "
            "evaluator or a judge, so a report cannot keep them apart")
    report_src = (ROOT / "src/clep/regression/report.py").read_text(encoding="utf-8")
    if "deterministic_evaluator" not in report_src or \
            "probabilistic_judge" not in report_src:
        separation_defects.append("the human report does not separate the two kinds")
    if report_src.count("## ") < 2:
        separation_defects.append("the human report renders one undivided table")
    if "is_deterministic" not in sql:
        separation_defects.append("nothing declares whether an evaluator is deterministic")
except Exception as e:
    separation_defects.append(f"{type(e).__name__}: {e}")
add("P-22", "PASS" if not separation_defects else "FAIL",
    f"REQ-F-08-6: evaluator and judge results separated in the store and in both "
    f"reports; defects: {len(separation_defects)}", separation_defects)

# ============== P-23 the gate tables store no credential-shaped column
gate_tables = ("baseline", "gate_policy", "gate_policy_version", "gate_criterion",
               "gate_decision", "comparison", "gate_criterion_result",
               "policy_exception")
leaky = []
for t in gate_tables:
    body = tables.get(t, "")
    for column in re.findall(r"^\s{4}(\w+)\s+", body, re.M):
        if any(w in column for w in ("key", "secret", "token", "password",
                                     "credential", "url")):
            if column != "metric_key":
                leaky.append(f"{t}.{column}")
add("P-23", "PASS" if not leaky else "FAIL",
    f"{len(gate_tables)} gate tables carry no endpoint or credential column; "
    f"found: {leaky or 'none'}", leaky)

# ============================================================ P-24 secrets
sec, nfiles, nbin = [], 0, 0
for p, rel in text_files(ROOT):
    nfiles += 1
    try:
        t = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        nbin += 1
        continue
    for rx, lb in SECRETS:
        if re.search(rx, t):
            sec.append(f"{rel.as_posix()}: {lb}")
# Two blobs on published history carry credential-SHAPED strings that are not
# credentials and cannot be removed: they are on `main` at origin, and rewriting
# published history is forbidden. Named by content hash so the exception cannot
# silently widen.
DISCLOSED_BLOBS = {
    "0ec58783928780cce1caf9d595decddf3574a54c":
        "spike common.py: local throwaway DSN password, container destroyed",
    "42de37b8a44e0079e835b3bffa45c1e735aeedac":
        "spike provider script: planted leak-detector canary, never a credential",
}
blob_sec, disclosed = [], []
for line in git("rev-list", "--objects", "--all").splitlines():
    parts = line.split(maxsplit=1)
    if len(parts) < 2:
        continue
    raw = subprocess.run(["git", "cat-file", "-t", parts[0]], cwd=str(ROOT),
                         capture_output=True, text=True).stdout.strip()
    if raw != "blob":
        continue
    body = subprocess.run(["git", "cat-file", "blob", parts[0]], cwd=str(ROOT),
                          capture_output=True).stdout
    try:
        t = body.decode("utf-8")
    except UnicodeDecodeError:
        continue
    for rx, lb in SECRETS:
        if re.search(rx, t):
            if parts[0] in DISCLOSED_BLOBS:
                disclosed.append(f"{parts[1]}: {DISCLOSED_BLOBS[parts[0]]}")
            else:
                blob_sec.append(f"{parts[1]}: {lb}")
add("P-24", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} undisclosed match(es), "
    f"{len(set(disclosed))} disclosed and unremovable from published history",
    sec + blob_sec + sorted(set(disclosed)))

# ======================================================== P-25 attribution
att = []
for p, rel in text_files(ROOT):
    try:
        t = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for rx in ATT:
        if re.search(rx, t, re.I):
            att.append(f"{rel.as_posix()}: {rx}")
msgs = git("log", "--all", "--format=%B%an%ae%cn%ce")
msg_hits = [rx for rx in ATT if re.search(rx, msgs, re.I)]
add("P-25", "PASS" if not att and not msg_hits else "FAIL",
    f"governed scope: {len(att)} file match(es), {len(msg_hits)} history match(es)",
    att + msg_hits)

# ======================================================= P-26 git identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()) - {""})
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()) - {""})
add("P-26", "PASS" if len(authors) == 1 and len(committers) == 1 else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ================================================== P-27 canonical document
#
# Reachability, not the tip tree of one branch. The allowlist is empty and is
# meant to stay that way: the one branch that carried the canonical document was
# deleted at Phase 6 finalization after establishing it had never been published.
# Keeping the mechanism with nothing in it makes re-disclosing anything an
# explicit act rather than an omission.
DISCLOSED_LOCAL: dict[tuple[str, str], str] = {}
docx = list(ROOT.glob("*.docx"))
tracked = [d for d in docx if git("ls-files", d.name).strip()]
ignored = all(git("check-ignore", d.name).strip() for d in docx) if docx else False
refs = [r for r in git("for-each-ref", "--format=%(refname)",
                       "refs/heads", "refs/remotes").split() if r]
published, undisclosed, disclosed_local = [], [], []
for ref in refs:
    for line in git("rev-list", "--objects", ref).splitlines():
        sha, _, path = line.partition(" ")
        if not re.search(r"\.docx?$", path, re.I):
            continue
        where = f"{ref}: {path} ({sha[:7]})"
        if ref.startswith("refs/remotes/"):
            published.append(where)
        elif (ref, sha) in DISCLOSED_LOCAL:
            disclosed_local.append(f"{where} - {DISCLOSED_LOCAL[(ref, sha)]}")
        else:
            undisclosed.append(where)
ok = bool(docx) and not tracked and ignored and not published and not undisclosed
add("P-27", "PASS" if ok else "FAIL",
    f"canonical document local={bool(docx)} tracked={bool(tracked)} "
    f"ignored={ignored} refs_scanned={len(refs)} "
    f"reachable_from_published={len(published)} "
    f"reachable_from_local_undisclosed={len(undisclosed)} "
    f"disclosed_local_only={len(disclosed_local)}",
    published + undisclosed + disclosed_local)

# ============================================================ P-28 hygiene
tracked_files = [f for f in git("ls-files").splitlines() if f]
dirty = [l for l in git("status", "--porcelain").splitlines() if l]
strays = [f for f in tracked_files
          if re.search(r"(\.orig|\.rej|\.bak|~|\.DS_Store|Thumbs\.db)$", f)]
add("P-28", "PASS" if not dirty and not strays else "FAIL",
    f"{len(tracked_files)} tracked file(s); stray: {len(strays)}; "
    f"clean tree: {not dirty}", dirty[:6] + strays)

# =========================================== P-29 phase boundary not overrun
later_phase = []
for pattern, label in (("docs/**/ADR-01[7-9]*.md", "ADRs beyond the recorded set"),
                       ("src/clep/agents/**", "agentic evaluation layer (Phase 8)"),
                       ("src/clep/judges/**", "judge ensemble (Phase 8)"),
                       ("src/clep/rag/**", "RAG evaluation suites (Phase 9)"),
                       ("src/clep/cli/**", "CI/CD command line (Phase 10)"),
                       ("src/clep/dashboards/**", "dashboards (Phase 11)")):
    hits = [str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)]
    if hits:
        later_phase.append(f"{label}: {hits[:2]}")
add("P-29", "PASS" if not later_phase else "FAIL",
    f"Phase 8+ artifact classes checked: 6; artifacts Phase 7 must not contain: "
    f"{len(later_phase)}", later_phase)

# ------------------------------------------------------------------ summary
print("-" * 78)
counts = {}
for r in results:
    counts[r["status"]] = counts.get(r["status"], 0) + 1
print("SUMMARY: " + json.dumps(counts, sort_keys=True))
sys.exit(1 if counts.get("FAIL") else 0)
