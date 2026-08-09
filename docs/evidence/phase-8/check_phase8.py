"""Phase 8 comprehensive validation, with regression over every earlier phase.

Phase 8 adds reasoning to a platform whose whole argument is that it does not
guess. The checks that matter most are the ones that would catch a judge layer
which *looks* rigorous: an ensemble that cannot disagree, a disagreement measure
that reports zero when it measured nothing, a regeneration loop that quietly
re-asks a judge until the number improves, a bound with a default nobody chose,
a plan that can be edited after somebody signed it. None of those announce
themselves.

Earlier phase gates are re-evaluated against their OWN trees, in isolated clones
pruned to their own history, for the reasons Phases 5, 6 and 7 established.

Usage: python docs/evidence/phase-8/check_phase8.py <repo_root>
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
    f"Phase 8 judge, plan and reasoning tables")

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


# ==================== P-5..P-9 earlier gates, at the trees and histories they mean
def gate_at_its_own_tree(cid, grep, script, label):
    """Re-run an earlier gate against the history it was written for.

    Three conditions, each learned by getting it wrong: an isolated clone rather
    than a worktree, because a worktree shares the object database and a blob
    scan run in one sees commits made after it; `main` RESET to the target commit
    rather than deleted, because a gate that searches history needs a `main`; and
    PYTHONPATH at the clone's `src`, because the package is installed editable
    against the working tree and without it the clone's tests import the present.
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
gate_at_its_own_tree("P-8", "phase-6)",
                     "docs/evidence/phase-6/check_phase6.py", "Phase 6")
gate_at_its_own_tree("P-9", "phase-7)",
                     "docs/evidence/phase-7/check_phase7.py", "Phase 7")

# ========================================= P-10 Phase 1 milestone validators
m1 = []
for script, label in (("docs/evidence/M1.1/check_m11.py", "M1.1 documents"),
                      ("docs/evidence/M1.2/check_m12.py", "M1.2 competitive"),
                      ("docs/evidence/M1.3/check_m13.py", "M1.3 requirements")):
    if (ROOT / script).exists():
        c, o = run([PY, script, "."])
        s = re.search(r"SUMMARY: (.*)", o)
        m1.append(f"{label}: exit {c} {s.group(1) if s else ''}")
add("P-10", "PASS" if all("exit 0" in x for x in m1) and m1 else "FAIL",
    "Phase 1 milestone validators re-run. " + "; ".join(m1))

# ===================================== P-11 the contract leads, not follows
sys.path.insert(0, str(ROOT / "src"))
contract_defects = []
ops = []
try:
    from clep.api import contract as _contract
    ops = _contract.operations(str(ROOT))
    if len(ops) != 36:
        contract_defects.append(f"expected 36 declared operations, found {len(ops)}")
    generated = list(ROOT.glob("**/openapi_generated*")) + \
        [p for p in ROOT.glob("src/**/*.py") if "openapi.json" in p.read_text("utf-8")
         and "write_text" in p.read_text("utf-8")]
    if generated:
        contract_defects.append(f"something writes the contract: {generated}")
    # The defect class Phases 6 and 7 each found: a request field naming
    # something no operation can create. Derived from the request schemas rather
    # than listed, so a new one cannot be added without being creatable.
    ids = {_contract.operation_id(m, p, str(ROOT)) for m, p in ops}
    creators = {"judgeVersionId": "addJudgeVersion",
                "judgeEnsembleId": "createJudgeEnsemble",
                "evaluationPlanId": "createEvaluationPlan",
                "gatePolicyVersionId": "addGatePolicyVersion",
                "baselineId": "createBaseline", "judgeId": "createJudge",
                "promptVersionId": "addPromptVersion",
                "datasetVersionId": "createDatasetVersion",
                "runId": "createRun", "candidateRunId": "createRun",
                "gateDecisionId": "evaluateGate",
                "gatePolicyId": "createGatePolicy", "promptId": "createPrompt"}
    schemas = _contract.load(str(ROOT))["components"]["schemas"]
    cited = set()
    for name, declared in schemas.items():
        if not name.endswith("Request"):
            continue
        for field in declared.get("properties", {}):
            if field.endswith("Ids"):
                cited.add(field[:-1])
            elif field.endswith("Id"):
                cited.add(field)
    for field in sorted(cited & set(creators)):
        if creators[field] not in ids:
            contract_defects.append(
                f"{field} can be cited but not created; the contract declares a "
                f"reference to something no operation produces")
except Exception as e:
    contract_defects.append(f"{type(e).__name__}: {e}")
add("P-11", "PASS" if not contract_defects else "FAIL",
    f"contract declares {len(ops)} operations, is read and never written, and "
    f"every identifier it accepts can be created through it", contract_defects)

# ============================ P-12 schema, contract and code share vocabularies
vocab_defects = []
sql = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted((ROOT / "docs/data/schema").glob("*.sql")))
sql_no_comments = re.sub(r"--[^\n]*", "", sql)


def ddl_enum(constraint):
    """The EFFECTIVE constraint, which is the last one declared.

    Files are ADD-only and applied in order, so a constraint replaced by a later
    file — as `ck_run_identity_component__kind` is, to admit `judge_version` —
    appears twice. Taking the first match would read the superseded version and
    report agreement with a vocabulary the database no longer enforces.
    """
    found = re.findall(rf"CONSTRAINT\s+{constraint}\s+CHECK\s*\((.*?)\)\s*[,)]",
                       sql_no_comments, re.S)
    return set(re.findall(r"'([a-z_]+)'", found[-1])) if found else set()


pairs = []
try:
    pairs = [
        ("run resolution", ddl_enum("ck_run_sample__resolution"),
         set(_contract.enum_of("SampleResolution", str(ROOT)))),
        ("judge resolution", ddl_enum("ck_judge_run__resolution"),
         set(_contract.enum_of("SampleResolution", str(ROOT)))),
        ("run completeness", ddl_enum("ck_run__completeness"),
         set(_contract.enum_of("Completeness", str(ROOT)))),
        ("integration tier", ddl_enum("ck_run__integration_tier"),
         set(_contract.enum_of("IntegrationTier", str(ROOT)))),
        ("identity component kind", ddl_enum("ck_run_identity_component__kind"),
         set(_contract.enum_of("IdentityComponentKind", str(ROOT)))),
        ("criterion source", ddl_enum("ck_gate_criterion__source"),
         set(_contract.enum_of("CriterionSource", str(ROOT)))),
        ("comparison classification", ddl_enum("ck_comparison__classification"),
         set(_contract.enum_of("Classification", str(ROOT)))),
        ("consensus state", ddl_enum("ck_consensus_result__state"),
         set(_contract.enum_of("ConsensusState", str(ROOT)))),
        ("escalation reason", ddl_enum("ck_consensus_result__escalation_reason"),
         set(_contract.enum_of("EscalationReason", str(ROOT)))),
        ("escalation state", ddl_enum("ck_escalation__state"),
         set(_contract.enum_of("EscalationState", str(ROOT)))),
        ("plan state", ddl_enum("ck_evaluation_plan__state"),
         set(_contract.enum_of("PlanState", str(ROOT)))),
        ("plan step kind", ddl_enum("ck_plan_step__kind"),
         set(_contract.enum_of("PlanStepKind", str(ROOT)))),
        ("reasoning state", ddl_enum("ck_reasoning_trace__state"),
         set(_contract.enum_of("ReasoningState", str(ROOT)))),
    ]
    for label, ddl, api in pairs:
        if not ddl:
            vocab_defects.append(f"{label}: no constraint found in the schema")
        elif ddl != api:
            vocab_defects.append(f"{label}: schema {sorted(ddl)} != contract {sorted(api)}")

    from clep.experiments.identity import CAPTURED_KINDS
    if set(CAPTURED_KINDS) != ddl_enum("ck_run_identity_component__kind"):
        vocab_defects.append(
            "the identity module's component kinds disagree with the schema")
    from clep.agents.sdk import STATES as REASONING_STATES
    if set(REASONING_STATES) != set(_contract.enum_of("ReasoningState", str(ROOT))):
        vocab_defects.append("the reasoning states disagree with the contract")
    from clep.judges.consensus import ESCALATION_REASONS, STATES as CONSENSUS_STATES
    if set(CONSENSUS_STATES) != set(_contract.enum_of("ConsensusState", str(ROOT))):
        vocab_defects.append("the consensus states disagree with the contract")
    if set(ESCALATION_REASONS) != set(_contract.enum_of("EscalationReason", str(ROOT))):
        vocab_defects.append("the escalation reasons disagree with the contract")
    from clep.agents.planner import PLAN_STATES, STEP_KINDS
    if set(PLAN_STATES) != set(_contract.enum_of("PlanState", str(ROOT))):
        vocab_defects.append("the plan states disagree with the contract")
    if set(STEP_KINDS) != set(_contract.enum_of("PlanStepKind", str(ROOT))):
        vocab_defects.append("the plan step kinds disagree with the contract")
    from clep.regression.engine import SOURCES_WITH_SIGNAL, SOURCES_WITHOUT_SIGNAL
    if set(SOURCES_WITH_SIGNAL) | set(SOURCES_WITHOUT_SIGNAL) != \
            set(_contract.enum_of("CriterionSource", str(ROOT))):
        vocab_defects.append(
            "a criterion source exists that the engine neither measures nor "
            "declares signal-less; it would fall through to a branch that was "
            "written for something else")
except Exception as e:
    vocab_defects.append(f"{type(e).__name__}: {e}")
add("P-12", "PASS" if not vocab_defects else "FAIL",
    f"vocabularies compared across schema, contract and code: {len(pairs) + 7}; "
    f"disagreements: {len(vocab_defects)}", vocab_defects)

# ================================== P-13 every dependency carries a justification
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
deps = set(re.findall(r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?[><=!]', pyproject))
justified = (ROOT / "docs/dependencies.md").read_text(encoding="utf-8")
unjustified = sorted(d for d in deps if f"`{d}`" not in justified)
add("P-13", "PASS" if not unjustified else "FAIL",
    f"{len(deps)} declared dependencies, each with a recorded reason and a rejected "
    f"alternative; unjustified: {unjustified or 'none'}. Phase 8 added none — "
    f"ADR-002 declined the agent framework and the orchestration is project code.")

# ===================================== P-14 no undecided ADR blocks this phase
adr_dir = ROOT / "docs/adr"
adrs = sorted(adr_dir.glob("ADR-*.md"))
undecided = [p.name for p in adrs if "NOT DECIDED" in p.read_text(encoding="utf-8")]
listed = (adr_dir / "README.md").read_text(encoding="utf-8")
unlisted = [p.name for p in adrs if p.name not in listed]
add("P-14", "PASS" if len(adrs) == 17 and not undecided and not unlisted else "FAIL",
    f"{len(adrs)} ADRs; undecided: {undecided or 'none'}; absent from the index: "
    f"{unlisted or 'none'}", undecided + unlisted)

# ================== P-15 idempotency on every externally visible effect table
effect_tables = {"run_sample": "uq_run_sample__idempotency_key",
                 "sample_cost": "uq_sample_cost__idempotency_key",
                 "run": "uq_run__idempotency_key",
                 "judge_run": "uq_judge_run__idempotency_key"}
missing_keys = [t for t, c in effect_tables.items() if c not in sql]
add("P-15", "PASS" if not missing_keys else "FAIL",
    f"every externally visible effect carries a unique idempotency key "
    f"({len(effect_tables)} tables, including a judgement, which costs money); "
    f"missing: {missing_keys or 'none'}")

# ========================== P-16 row-level security on every tenant-scoped table
tables = dict(re.findall(r"CREATE TABLE clep\.(\w+)\s*\((.*?)\n\);", sql, re.S))
tenant_scoped = [t for t in tables if t != "organization"]
rls_defects = []
for t in tenant_scoped:
    if not re.search(rf"ALTER TABLE clep\.{t}\s+ENABLE ROW LEVEL SECURITY", sql):
        rls_defects.append(f"{t} has no ENABLE")
    if not re.search(rf"ALTER TABLE clep\.{t}\s+FORCE\s+ROW LEVEL SECURITY", sql):
        rls_defects.append(f"{t} has no FORCE; the owner would bypass every policy")
add("P-16", "PASS" if not rls_defects else "FAIL",
    f"{len(tenant_scoped)} tenant-scoped tables, each with ENABLE and FORCE",
    rls_defects)

# ======================= P-17 composite FK targets have a matching unique key
fk_targets = set(re.findall(
    r"REFERENCES\s+clep\.(\w+)\s*\(\s*organization_id\s*,\s*id\s*\)", sql))
has_uq = {t for t, body in tables.items()
          if re.search(r"UNIQUE\s*\(\s*organization_id\s*,\s*id\s*\)", body)}
missing_uq = sorted(fk_targets - has_uq)
phase8 = [t for t in tables if t in (
    "judge_definition", "judge_version", "judge_ensemble",
    "judge_ensemble_member", "judge_run", "judge_vote", "consensus_result",
    "escalation", "evaluation_plan", "plan_step", "plan_amendment",
    "reasoning_trace", "reasoning_attempt")]
plain_fks = []
for t in phase8:
    body = tables[t]
    for column in re.findall(r"^\s{4}(\w+_id)\s+uuid", body, re.M):
        if column in ("id", "organization_id"):
            continue
        if column in ("created_by", "accepted_by", "reviewed_by", "actor_id"):
            continue  # actors are not rows in this schema
        if not re.search(rf"FOREIGN KEY \(organization_id, {column}\)", body):
            plain_fks.append(f"{t}.{column} is not a tenant-carrying foreign key")
add("P-17", "PASS" if not missing_uq and not plain_fks else "FAIL",
    f"{len(fk_targets)} composite foreign-key targets, each with a matching unique "
    f"constraint; {len(phase8)} Phase 8 tables, every reference carrying the "
    f"tenant; missing: {missing_uq or 'none'}", missing_uq + plain_fks)

# ============================== P-18 the schema is the migration set, not a copy
mig = (ROOT / "src/clep/db/migrations.py").read_text(encoding="utf-8")
copies = [str(p.relative_to(ROOT)) for p in ROOT.glob("src/**/*.sql")]
add("P-18", "PASS" if "docs" in mig and "schema" in mig and not copies else "FAIL",
    f"migrations are applied from docs/data/schema/ with no second copy of the DDL; "
    f"copies found: {copies or 'none'}")

# ================= P-19 a judgement is audit-class in the STORE
#
# A score's evidence is the judgement behind it. Evidence that can be edited
# afterwards is not evidence, so the triggers matter as well as the grants: a
# grant is one migration away from being widened.
audit_class = ("judge_run", "judge_vote", "consensus_result", "plan_amendment",
               "reasoning_trace", "reasoning_attempt")
immutability = []
for table in audit_class:
    if not re.search(rf"CREATE TRIGGER trg_{table}__immutable", sql):
        immutability.append(f"{table} has no immutability trigger")
    if re.search(rf"GRANT[^;]*\b(UPDATE|DELETE)\b[^;]*clep\.{table}\b", sql):
        immutability.append(f"the runtime role is granted UPDATE or DELETE on {table}")
for table, trigger in (("judge_version", "trg_judge_version__immutable"),
                       ("judge_ensemble", "trg_judge_ensemble__immutable_once_used"),
                       ("judge_ensemble_member",
                        "trg_judge_ensemble_member__immutable_once_used"),
                       ("plan_step", "trg_plan_step__frozen_with_its_plan"),
                       ("escalation", "trg_escalation__reviewed_once"),
                       ("evaluation_plan", "trg_evaluation_plan__settles_once")):
    if not re.search(rf"CREATE TRIGGER {trigger}", sql):
        immutability.append(f"{table} may change after something has cited it")
if re.search(r"GRANT[^;]*\bDELETE\b[^;]*clep\.\w+", sql):
    immutability.append("something in the schema grants DELETE")
add("P-19", "PASS" if not immutability else "FAIL",
    f"judgement evidence is immutable in the store: {len(audit_class)} audit-class "
    f"tables plus six freeze triggers; defects: {len(immutability)}", immutability)

# ======================= P-20 ADR-017 is implemented as decided, not as read
consensus_defects = []
try:
    import inspect
    from decimal import Decimal

    from clep.judges import consensus as _consensus
    from clep.judges.sdk import JudgeVersion, Vote

    signature = inspect.signature(_consensus.Ensemble.__init__)
    for parameter in ("agreement_threshold", "minimum_scoring_votes"):
        if signature.parameters[parameter].default is not inspect.Parameter.empty:
            consensus_defects.append(
                f"{parameter} has a default; ADR-004 left it unset deliberately "
                f"and ADR-017 declined to invent it")

    def judge(slug, model):
        return JudgeVersion(slug=slug, version="1", model=model,
                            endpoint_name=f"e-{model}", rubric="r")

    a, b, c = judge("a", "m1"), judge("b", "m2"), judge("c", "m3")

    def vote(j, score=None, resolution="scored"):
        return Vote(judge=j, resolution=resolution,
                    score=Decimal(str(score)) if score is not None else None)

    def room(threshold="0.20", minimum=None, judges=(a, b, c)):
        return _consensus.Ensemble(
            judges=judges,
            agreement_threshold=Decimal(threshold) if threshold else None,
            minimum_scoring_votes=minimum)

    # 1. Composition rules are enforced at construction, not checked later.
    twin = judge("a2", "m1")
    for judges, why in (((a,), "an ensemble of one was accepted"),
                        ((a, twin), "one model configuration repeated was accepted"),
                        ((a, twin, b), "a configuration holding a majority was accepted")):
        try:
            _consensus.Ensemble(judges=judges, agreement_threshold=Decimal("0.2"),
                                minimum_scoring_votes=None)
            consensus_defects.append(why)
        except _consensus.ConsensusError:
            pass

    # 2. Disagreement is the range: one dissenter is not diluted by agreement.
    wide = _consensus.reach_consensus(
        room(threshold="1"), [vote(a, "0.10"), vote(b, "0.90"), vote(c, "0.85")])
    if wide.disagreement != Decimal("0.80"):
        consensus_defects.append(
            f"disagreement {wide.disagreement} is not the range of the votes; a "
            f"measure that shrinks as agreeable judges are added can be bought off")

    # 3. Fewer than two scoring votes reports maximum, never zero.
    thin = _consensus.reach_consensus(
        room(), [vote(a, "0.9"), vote(b, resolution="failed"),
                 vote(c, resolution="abstained")])
    if thin.disagreement != Decimal(1) or thin.disagreement_measured:
        consensus_defects.append(
            "a single scoring vote did not report maximum unmeasured "
            "disagreement; one opinion would be read as perfect consensus")
    if thin.state != "escalated":
        consensus_defects.append("a single scoring vote produced a verdict")

    # 4. Above the threshold escalates and produces no number.
    escalated = _consensus.reach_consensus(
        room(threshold="0.05"), [vote(a, "0.10"), vote(b, "0.90"), vote(c, "0.50")])
    if escalated.state != "escalated" or escalated.verdict is not None:
        consensus_defects.append(
            "a spread above the threshold produced a verdict; averaging is what "
            "REQ-F-AG-4 exists to prevent")

    # 5. No threshold, no verdict.
    unset = _consensus.reach_consensus(
        room(threshold=None), [vote(a, "0.80"), vote(b, "0.80"), vote(c, "0.80")])
    if unset.state != "escalated" or \
            unset.escalation_reason != _consensus.NO_THRESHOLD:
        consensus_defects.append(
            "an ensemble with no configured threshold produced a verdict; the "
            "value ADR-004 declined to set has been invented somewhere")

    # 6. A non-scoring vote is not a zero.
    with_abstention = _consensus.reach_consensus(
        room(), [vote(a, "0.80"), vote(b, "0.85"), vote(c, resolution="abstained")])
    if with_abstention.state != "agreed":
        consensus_defects.append(
            "an abstaining judge dragged the range to the floor; REQ-X-8 says an "
            "unscored judgement is not a zero")

    # 7. Escalation is terminal: consensus cannot obtain another vote.
    if set(inspect.signature(_consensus.reach_consensus).parameters) != \
            {"ensemble", "votes"}:
        consensus_defects.append(
            "reach_consensus can reach something other than the votes it was "
            "given; retrying until judges agree is ADR-004 D-4's anti-pattern")
except Exception as e:
    consensus_defects.append(f"{type(e).__name__}: {e}")
add("P-20", "PASS" if not consensus_defects else "FAIL",
    f"ADR-017 enforced mechanically: composition refused at construction, "
    f"disagreement is the range, unmeasured reports maximum, no threshold means "
    f"no verdict, escalation is terminal; defects: {len(consensus_defects)}",
    consensus_defects)

# ================ P-21 REQ-F-AG-5: the bounds are real and the history is kept
bounds_defects = []
try:
    import inspect
    from decimal import Decimal

    from clep.agents.sdk import (ACCEPTED, BUDGET_EXHAUSTED, Bounds,
                                 DEADLINE_EXCEEDED, ITERATIONS_EXHAUSTED,
                                 Proposal, run_bounded)

    signature = inspect.signature(Bounds.__init__)
    for parameter in ("max_iterations", "budget", "timeout_ms"):
        if signature.parameters[parameter].default is not inspect.Parameter.empty:
            bounds_defects.append(
                f"{parameter} has a default; a bound nobody chose governs every "
                f"reasoning loop in the product")

    class Clock:
        def __init__(self):
            self.seconds = 0.0

        def __call__(self):
            return self.seconds

    # Iterations.
    calls = []

    def counting(index, critique):
        calls.append(index)
        return Proposal(value=f"draft-{index}")

    exhausted = run_bounded(counting, lambda v: "no",
                            Bounds(max_iterations=3, budget=Decimal("10"),
                                   timeout_ms=100_000))
    if exhausted.state != ITERATIONS_EXHAUSTED or len(calls) != 3:
        bounds_defects.append(
            f"the iteration limit was not honoured: {len(calls)} attempts, state "
            f"{exhausted.state}")
    if exhausted.value is not None:
        bounds_defects.append(
            "an exhausted loop returned a value; an unfinished result that looks "
            "usable is how one gets used")
    if len(exhausted.attempts) != 3 or any(not a.critique for a in exhausted.attempts):
        bounds_defects.append(
            "the rejected iterations were not retained with their critiques; "
            "REQ-F-AG-5 asks for the full history and those are the useful part")

    # Budget, tested before the attempt rather than after it.
    spent = run_bounded(lambda i, c: Proposal(value=i, cost=Decimal("0.4")),
                        lambda v: "no",
                        Bounds(max_iterations=10, budget=Decimal("1"),
                               timeout_ms=100_000))
    if spent.state != BUDGET_EXHAUSTED or spent.iterations != 3:
        bounds_defects.append(
            f"the budget did not stop the loop where it should: {spent.state}, "
            f"{spent.iterations} iterations")

    # Deadline.
    clock = Clock()

    def slow(index, critique):
        clock.seconds += 2.0
        return Proposal(value=index)

    late = run_bounded(slow, lambda v: "no",
                       Bounds(max_iterations=10, budget=Decimal("10"),
                              timeout_ms=5000), clock=clock)
    if late.state != DEADLINE_EXCEEDED:
        bounds_defects.append(f"the timeout did not stop the loop: {late.state}")

    # The bounds travel with the result, so "it stayed inside its budget" is
    # checkable against the budget that applied.
    if late.bounds is None or late.bounds.timeout_ms != 5000:
        bounds_defects.append("the result does not carry the bounds it ran under")

    # Regeneration retries an unreadable reply and nothing else.
    from clep.judges.reflection import is_unreadable
    from clep.judges.sdk import JudgeVersion as _JV, Vote as _Vote
    j = _JV(slug="a", version="1", model="m", endpoint_name="e", rubric="r")
    if is_unreadable(_Vote(judge=j, resolution="scored", score=Decimal(0))):
        bounds_defects.append(
            "a scored judgement is treated as regenerable; re-asking a judge that "
            "answered is ADR-004 D-4's anti-pattern")
    if is_unreadable(_Vote(judge=j, resolution="abstained")):
        bounds_defects.append("an abstention is treated as regenerable")
    if not is_unreadable(_Vote(judge=j, resolution="failed",
                               detail="unreadable reply: 'GATE: pass'")):
        bounds_defects.append("an unreadable reply is not regenerated")
except Exception as e:
    bounds_defects.append(f"{type(e).__name__}: {e}")
add("P-21", "PASS" if not bounds_defects else "FAIL",
    f"REQ-F-AG-5: no defaulted bound, each of the three enforced, the full "
    f"iteration history retained, and regeneration limited to an unreadable "
    f"reply; defects: {len(bounds_defects)}", bounds_defects)

# ========= P-22 REQ-X-8: a judgement that did not score has nothing to read
unscored_defects = []
try:
    from decimal import Decimal

    from clep.judges.sdk import JudgeError, JudgeVersion, Vote
    j = JudgeVersion(slug="a", version="1", model="m", endpoint_name="e", rubric="r")
    try:
        Vote(judge=j, resolution="abstained", score=Decimal(0))
        unscored_defects.append("an abstaining judge was allowed to carry a zero")
    except JudgeError:
        pass
    try:
        Vote(judge=j, resolution="scored", score=None)
        unscored_defects.append("a scoring judge was allowed to carry no score")
    except JudgeError:
        pass
    # In the store the same rule is structural: the score is a separate table,
    # so there is no column to read as zero rather than a nullable one to check.
    if "score" in tables.get("judge_run", ""):
        unscored_defects.append(
            "judge_run carries a score column; an unanswered judgement would be "
            "one NULL check away from being a zero")
    if not re.search(r"CREATE TABLE clep\.judge_vote", sql):
        unscored_defects.append("there is no separate table for a judge's score")
    if "score            numeric(18, 9) NOT NULL" not in tables.get("judge_vote", ""):
        unscored_defects.append("a judge_vote row can exist without a score")
    if "uq_judge_vote__one_per_run" not in sql:
        unscored_defects.append("a judgement can carry two scores")
    if not re.search(r"ck_consensus_result__unmeasured_is_maximum", sql):
        unscored_defects.append(
            "the store permits an unmeasured disagreement of zero, which reads as "
            "perfect agreement")
except Exception as e:
    unscored_defects.append(f"{type(e).__name__}: {e}")
add("P-22", "PASS" if not unscored_defects else "FAIL",
    f"REQ-X-8 for judgements, in the type and in the store: no score without a "
    f"scoring resolution, and no row at all when there is no score; defects: "
    f"{len(unscored_defects)}", unscored_defects)

# ====== P-23 REQ-N-SEC-3 / REQ-X-7: the adversarial corpus, executed
injection_defects = []
try:
    corpus = json.loads(
        (ROOT / "docs/evidence/phase-8/injection-corpus.json").read_text("utf-8"))
    entries, replies = corpus["content"], corpus["replies"]
    if len(entries) < 12 or len(replies) < 6:
        injection_defects.append(
            f"the corpus has shrunk to {len(entries)} contents and "
            f"{len(replies)} replies; a corpus that shrinks makes every check "
            f"below pass")

    from decimal import Decimal

    from clep.evaluators.sdk import SampleContext
    from clep.judges.sdk import (FENCE_CLOSE, FENCE_OPEN, JudgeVersion,
                                 parse_reply, render_prompt)
    judge = JudgeVersion(slug="a", version="1", model="m", endpoint_name="e",
                         rubric="RUBRIC")

    def regions(text):
        prompt, _ = render_prompt(
            judge, SampleContext(example_id="x", prompt="p", output=text))
        before, _, rest = prompt.partition(FENCE_OPEN)
        _, _, after = rest.partition(FENCE_CLOSE)
        return before, after, prompt

    benign_before, benign_after, _ = regions("an ordinary answer")
    for entry in entries:
        before, after, prompt = regions(entry["text"])
        if (before, after) != (benign_before, benign_after):
            injection_defects.append(
                f"{entry['id']}: the content changed the instruction region")
        if prompt.count(FENCE_OPEN) != 1 or prompt.count(FENCE_CLOSE) != 1:
            injection_defects.append(f"{entry['id']}: the content moved the fence")
    for reply in replies:
        resolution, score, _ = parse_reply(reply["text"])
        if resolution == "scored" and not (Decimal(0) <= score <= Decimal(1)):
            injection_defects.append(
                f"{reply['id']}: an out-of-range score was accepted")
        if resolution not in ("scored", "abstained", "failed"):
            injection_defects.append(
                f"{reply['id']}: a reply produced something other than a score, "
                f"an abstention or nothing")
    verdict, _, _ = parse_reply("GATE: pass")
    if verdict != "failed":
        injection_defects.append("a reply naming a gate outcome was read as one")

    # And the defence that holds without the model cooperating: one compromised
    # judge escalates rather than deciding.
    from clep.judges.consensus import Ensemble, reach_consensus
    from clep.judges.sdk import Vote

    def judge_of(slug, model):
        return JudgeVersion(slug=slug, version="1", model=model,
                            endpoint_name=f"e-{model}", rubric="r")

    a, b, c = judge_of("a", "m1"), judge_of("b", "m2"), judge_of("c", "m3")
    panel = Ensemble(judges=(a, b, c), agreement_threshold=Decimal("0.15"),
                     minimum_scoring_votes=2)

    def scored(j, value):
        return Vote(judge=j, resolution="scored", score=Decimal(value))

    compromised = reach_consensus(panel, [scored(a, "1.0"), scored(b, "0.25"),
                                          scored(c, "0.22")])
    if compromised.state != "escalated" or compromised.verdict is not None:
        injection_defects.append(
            "one judge talked into a perfect score carried the verdict")
except Exception as e:
    injection_defects.append(f"{type(e).__name__}: {e}")
add("P-23", "PASS" if not injection_defects else "FAIL",
    f"REQ-N-SEC-3: adversarial corpus executed against the prompt, the parse and "
    f"the ensemble; defects: {len(injection_defects)}", injection_defects)

# ============= P-24 REQ-F-AG-1: an accepted plan is what was approved
plan_defects = []
try:
    from decimal import Decimal

    from clep.agents.planner import (PlanError, PlanInputs, accept, amend,
                                     draft_plan, validate)

    def inputs(**over):
        base = dict(objective="o", suite_version_id="S", dataset_version_ids=("D",),
                    candidate_labels=("cand",))
        base.update(over)
        return PlanInputs(**base)

    good = draft_plan(inputs())
    if validate(good):
        plan_defects.append(f"a straightforward plan does not validate: "
                            f"{validate(good)}")
    accepted = accept(good, "reviewer")
    if accepted.state != "accepted" or not accepted.accepted_by:
        plan_defects.append("acceptance did not record who accepted")
    try:
        amend(accepted, note="one more thing", actor="reviewer")
        plan_defects.append(
            "an accepted plan was amended; the record of what was approved can "
            "be changed after the fact")
    except PlanError:
        pass
    over_budget = draft_plan(inputs(budget=Decimal("0.0001")), sample_count=1000)
    if "exceeds the budget" not in validate(over_budget):
        plan_defects.append("an over-budget plan validates; REQ-F-10-5 requires it "
                            "to be refused rather than partially executed")
    try:
        accept(over_budget, "reviewer")
        plan_defects.append("an invalid plan was accepted")
    except PlanError:
        pass
    gate_without_policy = draft_plan(inputs(gate_policy_version_id="G"))
    if "no baseline" not in validate(gate_without_policy):
        plan_defects.append("a gate step with no baseline validates; a gate "
                            "decision is a comparison, not a measurement")
    if not re.search(r"ck_evaluation_plan__estimate_within_budget", sql):
        plan_defects.append("the store permits a plan whose estimate exceeds its "
                            "budget")
    if not re.search(r"ck_evaluation_plan__acceptance_is_recorded", sql):
        plan_defects.append("the store permits an accepted plan with no acceptor")
except Exception as e:
    plan_defects.append(f"{type(e).__name__}: {e}")
add("P-24", "PASS" if not plan_defects else "FAIL",
    f"REQ-F-AG-1: a plan validates before it can be accepted, records who "
    f"accepted it, and cannot be amended afterwards; defects: {len(plan_defects)}",
    plan_defects)

# ========== P-25 a judge version is part of run identity and pins comparability
identity_defects = []
try:
    from clep.experiments.identity import CAPTURED_KINDS, IDENTITY_KINDS
    from clep.regression.comparability import (IGNORED_KINDS, PINNED_KINDS,
                                               VARYING_KINDS, assess)
    if "judge_version" not in IDENTITY_KINDS:
        identity_defects.append(
            "a judge version is not part of run identity; ADR-004 D-5 says it is")
    if "judge_version" not in PINNED_KINDS:
        identity_defects.append(
            "a judge version change does not invalidate comparability; "
            "REQ-F-08-8 names judges before it names evaluators")
    unclassified = set(CAPTURED_KINDS) - set(PINNED_KINDS) - set(VARYING_KINDS) \
        - set(IGNORED_KINDS)
    if unclassified:
        identity_defects.append(
            f"identity kinds nothing has an opinion about: {sorted(unclassified)}; "
            f"they would be dropped from the comparability decision silently")

    # Behavioural: two identities differing only in judge version are refused.
    import types
    from clep.experiments.identity import Component, digest_of

    def identity(judge_ref):
        return types.SimpleNamespace(components=[
            Component(kind="dataset_version", ref="D", digest=digest_of("D")),
            Component(kind="suite_version", ref="S", digest=digest_of("S")),
            Component(kind="evaluator_version", ref="E", digest=digest_of("E")),
            Component(kind="integration_tier", ref="full", digest=digest_of("full")),
            Component(kind="judge_version", ref=judge_ref,
                      digest=digest_of(judge_ref))])

    if assess(identity("J1"), identity("J1")).comparable is not True:
        identity_defects.append("two identical identities were not comparable")
    verdict = assess(identity("J1"), identity("J2"))
    if verdict.comparable is not False:
        identity_defects.append(
            "a changed judge version was reported comparable; the requirement is "
            "to invalidate rather than warn")
    elif "judge_version" not in (verdict.reason() or ""):
        identity_defects.append("the reason does not name the judge version")
except Exception as e:
    identity_defects.append(f"{type(e).__name__}: {e}")
add("P-25", "PASS" if not identity_defects else "FAIL",
    f"ADR-004 D-5 and REQ-F-08-8: a judge version is captured, pinned, and "
    f"invalidates a comparison when it changes; defects: {len(identity_defects)}",
    identity_defects)

# ================== P-26 tracked architectural debt is carried, not dropped
debt_defects = []
debt_path = ROOT / "docs/architecture/tracked-debt.md"
try:
    debt = debt_path.read_text(encoding="utf-8")
    entries = re.findall(r"^## (D-\d+) — (.+)$", debt, re.M)
    if not entries:
        debt_defects.append("the register has no entries at all")
    for entry, _ in entries:
        section = debt.split(f"## {entry}")[1].split("\n## ")[0]
        for required in ("Owning phase", "Status"):
            if required not in section:
                debt_defects.append(f"{entry} does not record its {required}")
    # D-1 is carried from Phase 7 by the review verdict, and must not vanish
    # while the structure that caused it is unchanged.
    comparison_body = tables.get("comparison", "")
    still_plain = ("evaluator_version_id" in comparison_body
                   and "FOREIGN KEY (organization_id, evaluator_version_id)"
                   not in comparison_body)
    if still_plain and "D-1" not in debt:
        debt_defects.append(
            "comparison.evaluator_version_id still does not carry the tenant, and "
            "the debt entry recording that has been removed")
    if not still_plain and "Status | **Open**" in debt.split("## D-2")[0]:
        debt_defects.append(
            "D-1 is recorded as open but the structure it describes has changed; "
            "close it with the fix rather than leaving it stale")
except FileNotFoundError:
    debt_defects.append(f"{debt_path} does not exist; accepted debt with no "
                        f"register is forgotten debt")
except Exception as e:
    debt_defects.append(f"{type(e).__name__}: {e}")
add("P-26", "PASS" if not debt_defects else "FAIL",
    f"tracked architectural debt: {len(re.findall(r'^## D-', debt_path.read_text(encoding='utf-8'), re.M)) if debt_path.exists() else 0} "
    f"entries, each naming an owning phase and a status; defects: {len(debt_defects)}",
    debt_defects)

# ============== P-27 the Phase 8 tables store no credential-shaped column
phase8_tables = ("judge_definition", "judge_version", "judge_ensemble",
                 "judge_ensemble_member", "judge_run", "judge_vote",
                 "consensus_result", "escalation", "evaluation_plan",
                 "plan_step", "plan_amendment", "reasoning_trace",
                 "reasoning_attempt")
leaky = []
for t in phase8_tables:
    body = tables.get(t, "")
    for column in re.findall(r"^\s{4}(\w+)\s+", body, re.M):
        if any(w in column for w in ("key", "secret", "token", "password",
                                     "credential", "url")):
            if column not in ("idempotency_key",):
                leaky.append(f"{t}.{column}")
# A judge runs against a model configuration, which is where an endpoint lives.
# A rubric is content and is digested here rather than stored, so a prompt
# containing a credential is not copied into the judgement record.
if "rubric  " in tables.get("judge_version", ""):
    leaky.append("judge_version stores the rubric text rather than its digest")
add("P-27", "PASS" if not leaky else "FAIL",
    f"{len(phase8_tables)} Phase 8 tables carry no endpoint or credential column; "
    f"found: {leaky or 'none'}", leaky)

# ============================================================ P-28 secrets
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
add("P-28", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} undisclosed match(es), "
    f"{len(set(disclosed))} disclosed and unremovable from published history",
    sec + blob_sec + sorted(set(disclosed)))

# ======================================================== P-29 attribution
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
add("P-29", "PASS" if not att and not msg_hits else "FAIL",
    f"governed scope: {len(att)} file match(es), {len(msg_hits)} history match(es)",
    att + msg_hits)

# ======================================================= P-30 git identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()) - {""})
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()) - {""})
add("P-30", "PASS" if len(authors) == 1 and len(committers) == 1 else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ================================================== P-31 canonical document
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
add("P-31", "PASS" if ok else "FAIL",
    f"canonical document local={bool(docx)} tracked={bool(tracked)} "
    f"ignored={ignored} refs_scanned={len(refs)} "
    f"reachable_from_published={len(published)} "
    f"reachable_from_local_undisclosed={len(undisclosed)} "
    f"disclosed_local_only={len(disclosed_local)}",
    published + undisclosed + disclosed_local)

# ============================================================ P-32 hygiene
tracked_files = [f for f in git("ls-files").splitlines() if f]
dirty = [l for l in git("status", "--porcelain").splitlines() if l]
strays = [f for f in tracked_files
          if re.search(r"(\.orig|\.rej|\.bak|~|\.DS_Store|Thumbs\.db)$", f)]
add("P-32", "PASS" if not dirty and not strays else "FAIL",
    f"{len(tracked_files)} tracked file(s); stray: {len(strays)}; "
    f"clean tree: {not dirty}", dirty[:6] + strays)

# =========================================== P-33 phase boundary not overrun
later_phase = []
for pattern, label in (("docs/**/ADR-01[89]*.md", "ADRs beyond the recorded set"),
                       ("docs/**/ADR-02*.md", "ADRs beyond the recorded set"),
                       ("src/clep/rag/**", "RAG evaluation suites (Phase 9)"),
                       ("src/clep/cli/**", "CI/CD command line (Phase 10)"),
                       ("src/clep/dashboards/**", "dashboards (Phase 11)"),
                       ("src/clep/rbac/**", "enterprise RBAC (Phase 12)"),
                       ("src/clep/telemetry/**", "observability (Phase 13)")):
    hits = [str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)]
    if hits:
        later_phase.append(f"{label}: {hits[:2]}")
add("P-33", "PASS" if not later_phase else "FAIL",
    f"Phase 9+ artifact classes checked: 7; artifacts Phase 8 must not contain: "
    f"{len(later_phase)}", later_phase)

# ============ P-34 every earlier gate is reachable from this one, by derivation
edges = {}
all_validators = sorted(
    p.relative_to(ROOT).as_posix()
    for p in ROOT.glob("docs/evidence/**/check_*.py"))
for path in all_validators:
    body = (ROOT / path).read_text(encoding="utf-8")
    edges[path] = {m for m in re.findall(r"docs/evidence/[\w.\-]+/check_\w+\.py", body)
                   if m != path}
root = "docs/evidence/phase-8/check_phase8.py"
reachable, frontier = {root}, [root]
while frontier:
    current = frontier.pop()
    for target in edges.get(current, ()):
        if target not in reachable:
            reachable.add(target)
            frontier.append(target)
unreachable = sorted(set(all_validators) - reachable)
missing_files = sorted({t for targets in edges.values() for t in targets}
                       - set(all_validators))
add("P-34", "PASS" if not unreachable and not missing_files else "FAIL",
    f"{len(all_validators)} validators in the repository, {len(reachable)} "
    f"reachable from this gate; orphaned: {unreachable or 'none'}",
    unreachable + [f"invoked but absent: {m}" for m in missing_files])

# ------------------------------------------------------------------ summary
print("-" * 78)
counts = {}
for r in results:
    counts[r["status"]] = counts.get(r["status"], 0) + 1
print("SUMMARY: " + json.dumps(counts, sort_keys=True))
sys.exit(1 if counts.get("FAIL") else 0)
