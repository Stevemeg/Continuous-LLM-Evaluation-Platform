"""Phase 11 comprehensive validation, with regression over every earlier phase.

Phase 11 makes the scheduled-execution model execute, proves the CLI as an
installed console script, and adds the reporting layer. The failures that matter
are a schedule that is stored and never fires, a CLI that only works because the
repository happens to be the working directory, a figure that cannot name the
samples behind it, a mean over a cancelled run presented as a mean, a leaderboard
with no benchmark, a drift threshold the platform invented for itself, and an
alert that quietly acquires a way to act. Each of those looks like working
software.

The frame — the isolated-clone gate runner, the security sweeps, the reachability
closure — is spliced from the Phase 10 validator rather than copied, because two
copies of a gate drift and only one of them gets reviewed.

Earlier phase gates are re-evaluated against their OWN trees, in isolated clones
pruned to their own history, for the reasons Phases 5 to 8 established. Only the
immediately preceding gate is invoked directly; `P-26` derives the rest and does
not trust this comment.

The Phase 11 checks are deliberately behavioural. Four checks across three
phases have now been lost to source-text matching, so where a property can be
exercised it is exercised: the leaderboard is asked for one without a benchmark
and must refuse before it queries anything, the drift detector is given a
one-baseline history and must abstain, the scorecard is rendered over incomplete
evidence and must still carry the sentence.

Usage: python docs/evidence/phase-11/check_phase11.py <repo_root>
Exits non-zero on any FAIL.
"""
from __future__ import annotations

import inspect as _inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from decimal import Decimal
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
    f"Phase 11 schedule-candidate and alert tables")

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


# ====================== P-5 the preceding gate, at its own tree, at the trees and histories they mean
def gate_at_its_own_tree(cid, grep, script, label, timeout=2400):
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
    c, o = run([PY, str(tree / script), str(tree)], cwd=tree, env=env,
               timeout=timeout)
    summary = re.search(r"SUMMARY: (.*)", o)
    fails = [l.strip()[:150] for l in o.splitlines() if l.startswith("[FAIL")]
    shutil.rmtree(work, ignore_errors=True)
    add(cid, "PASS" if c == 0 else "FAIL",
        f"{label} gate re-evaluated against its own history ({sha[:8]}), in an "
        f"isolated clone pruned to {remaining} reachable commit(s): exit {c} "
        f"{summary.group(1) if summary else ''}", fails)


# Only the immediately preceding gate is invoked here. Each phase gate re-runs
# the gates before it, so naming them all again duplicates the entire chain and
# the work compounds with every phase. What makes the shorter form sufficient is
# P-26, which derives the closure from the invocation paths each gate actually
# contains and requires every validator in the repository to be reachable.
gate_at_its_own_tree("P-5", "phase-10)",
                     "docs/evidence/phase-10/check_phase10.py", "Phase 10",
                     timeout=10800)

# ========================================== P-6 Phase 1 milestone validators
m1 = []
for script, label in (("docs/evidence/M1.1/check_m11.py", "M1.1 documents"),
                      ("docs/evidence/M1.2/check_m12.py", "M1.2 competitive"),
                      ("docs/evidence/M1.3/check_m13.py", "M1.3 requirements")):
    if (ROOT / script).exists():
        c, o = run([PY, script, "."])
        s = re.search(r"SUMMARY: (.*)", o)
        m1.append(f"{label}: exit {c} {s.group(1) if s else ''}")
add("P-6", "PASS" if all("exit 0" in x for x in m1) and m1 else "FAIL",
    "Phase 1 milestone validators re-run. " + "; ".join(m1))

# ===================================== P-11 the contract leads, not follows
sys.path.insert(0, str(ROOT / "src"))
contract_defects = []
ops = []
try:
    from clep.api import contract as _contract
    ops = _contract.operations(str(ROOT))
    if len(ops) != 52:
        contract_defects.append(f"expected 52 declared operations, found {len(ops)}")
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
                "alertRuleId": "createAlertRule",
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
    # Phase 11's own rule: every analytics operation declares which requirement
    # it realises. Traceability is derived from these, so an operation with no
    # x-requirements is an operation the matrix cannot see.
    for (method, path), operation in ops.items():
        if "analytics" in operation.get("tags", []) \
                and not operation.get("x-requirements"):
            contract_defects.append(
                f"{operation['operationId']} declares no requirement")
except Exception as e:
    contract_defects.append(f"{type(e).__name__}: {e}")
add("P-11", "PASS" if not contract_defects else "FAIL",
    f"contract declares {len(ops)} operations, is read and never written, every "
    f"identifier it accepts can be created through it, and every analytics "
    f"operation names its requirement", contract_defects)

# ============================ P-12 schema, contract and code share vocabularies
vocab_defects = []
sql = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted((ROOT / "docs/data/schema").glob("*.sql")))
sql_no_comments = re.sub(r"--[^\n]*", "", sql)


def ddl_enum(constraint):
    """The EFFECTIVE constraint, which is the last one declared.

    Files are ADD-only and applied in order, so a constraint replaced by a later
    file appears twice. Taking the first match would read the superseded version
    and report agreement with a vocabulary the database no longer enforces.
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
        ("hallucination finding", ddl_enum("ck_hallucination_finding__finding"),
         set(_contract.enum_of("HallucinationFinding", str(ROOT)))),
        ("attribution stage", ddl_enum("ck_stage_attribution__stage"),
         set(_contract.enum_of("AttributionStage", str(ROOT)))),
        ("run trigger", ddl_enum("ck_run__trigger_kind"),
         set(_contract.enum_of("RunTrigger", str(ROOT)))),
        ("remediation kind",
         ddl_enum("ck_release_observation__recommendation"),
         set(_contract.enum_of("RemediationKind", str(ROOT)))),
        ("schedule state", ddl_enum("ck_evaluation_schedule__state"),
         set(_contract.enum_of("ScheduleState", str(ROOT)))),
        # Phase 11.
        ("schedule trigger", ddl_enum("ck_evaluation_schedule__trigger_kind"),
         set(_contract.enum_of("ScheduleTrigger", str(ROOT)))),
        ("alert dimension", ddl_enum("ck_alert_rule__dimension"),
         set(_contract.enum_of("AlertDimension", str(ROOT)))),
        ("alert direction", ddl_enum("ck_alert_rule__direction"),
         set(_contract.enum_of("MetricDirection", str(ROOT)))),
        ("alert rule state", ddl_enum("ck_alert_rule__state"),
         set(_contract.enum_of("ScheduleState", str(ROOT)))),
        ("alert evidence completeness",
         ddl_enum("ck_alert_event__evidence_completeness"),
         set(_contract.enum_of("Completeness", str(ROOT)))),
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
    from clep.cli.exit_codes import BY_OUTCOME
    if set(BY_OUTCOME) != set(_contract.enum_of("GateOutcome", str(ROOT))):
        vocab_defects.append(
            "the CLI's exit-code map and the contract's gate outcomes disagree")
    from clep.rag.attribution import STAGES
    if set(STAGES) != set(_contract.enum_of("AttributionStage", str(ROOT))):
        vocab_defects.append("the attribution stages disagree with the contract")
    from clep.rag.hallucination import FINDINGS
    if set(FINDINGS) != set(_contract.enum_of("HallucinationFinding", str(ROOT))):
        vocab_defects.append("the hallucination findings disagree with the contract")
    from clep.regression.engine import SOURCES_WITH_SIGNAL, SOURCES_WITHOUT_SIGNAL
    if set(SOURCES_WITH_SIGNAL) | set(SOURCES_WITHOUT_SIGNAL) != \
            set(_contract.enum_of("CriterionSource", str(ROOT))):
        vocab_defects.append(
            "a criterion source exists that the engine neither measures nor "
            "declares signal-less; it would fall through to a branch that was "
            "written for something else")
    # Phase 11's own vocabularies, in code.
    from clep.orchestration.schedules import SCHEDULE_TRIGGERS
    if set(SCHEDULE_TRIGGERS) != set(_contract.enum_of("ScheduleTrigger", str(ROOT))):
        vocab_defects.append("the schedule triggers disagree with the contract")
    from clep.analytics.alerts import DIMENSIONS, DIRECTIONS
    if set(DIMENSIONS) != set(_contract.enum_of("AlertDimension", str(ROOT))):
        vocab_defects.append("the alert dimensions disagree with the contract")
    if set(DIRECTIONS) != set(_contract.enum_of("MetricDirection", str(ROOT))):
        vocab_defects.append("the alert directions disagree with the contract")
    from clep.analytics.drift import VERDICTS, POSITIONS
    if set(VERDICTS) != set(_contract.enum_of("DriftVerdict", str(ROOT))):
        vocab_defects.append("the drift verdicts disagree with the contract")
    if set(POSITIONS) != set(_contract.enum_of("DriftPosition", str(ROOT))):
        vocab_defects.append("the drift positions disagree with the contract")
    from clep.orchestration.releases import BY_OUTCOME as ADVICE
    if set(ADVICE) != set(BY_OUTCOME):
        vocab_defects.append(
            "a gate outcome a pipeline can act on has no recommendation for an "
            "operator, or the reverse")
    if set(ADVICE.values()) - set(_contract.enum_of("RemediationKind", str(ROOT))):
        vocab_defects.append("a recommendation is not one the contract declares")
except Exception as e:
    vocab_defects.append(f"{type(e).__name__}: {e}")
add("P-12", "PASS" if not vocab_defects else "FAIL",
    f"vocabularies compared across schema, contract and code: {len(pairs) + 16}; "
    f"disagreements: {len(vocab_defects)}", vocab_defects)

# ================================== P-13 every dependency carries a justification
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
deps = set(re.findall(r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?[><=!]', pyproject))
justified = (ROOT / "docs/dependencies.md").read_text(encoding="utf-8")
unjustified = sorted(d for d in deps if f"`{d}`" not in justified)
add("P-13", "PASS" if not unjustified else "FAIL",
    f"{len(deps)} declared dependencies, each with a recorded reason and a rejected "
    f"alternative; unjustified: {unjustified or 'none'}. Phase 11 added none — "
    f"the cron parser and the analytics are the standard library and SQL.")

# ===================================== P-14 no undecided ADR blocks this phase
adr_dir = ROOT / "docs/adr"
adrs = sorted(adr_dir.glob("ADR-*.md"))
undecided = [p.name for p in adrs if "NOT DECIDED" in p.read_text(encoding="utf-8")]
listed = (adr_dir / "README.md").read_text(encoding="utf-8")
unlisted = [p.name for p in adrs if p.name not in listed]
add("P-14", "PASS" if len(adrs) == 18 and not undecided and not unlisted else "FAIL",
    f"{len(adrs)} ADRs; undecided: {undecided or 'none'}; absent from the index: "
    f"{unlisted or 'none'}", undecided + unlisted)

# ================== P-15 idempotency on every externally visible effect table
effect_tables = {"run_sample": "uq_run_sample__idempotency_key",
                 "sample_cost": "uq_sample_cost__idempotency_key",
                 "run": "uq_run__idempotency_key",
                 "judge_run": "uq_judge_run__idempotency_key",
                 # Phase 11: a firing is an effect a reader acts on, so
                 # evaluating the same run twice must not produce two.
                 "alert_event": "uq_alert_event__rule_run"}
# Word-bounded: a constraint renamed to `uq_alert_event__rule_run_removed`
# still contains the name a substring search looks for, and the store would no
# longer enforce anything.
missing_keys = [t for t, c in effect_tables.items()
                if not re.search(rf"CONSTRAINT {c}\b", sql)]
add("P-15", "PASS" if not missing_keys else "FAIL",
    f"every externally visible effect carries a unique idempotency key "
    f"({len(effect_tables)} tables, including a judgement, which costs money, "
    f"and an alert firing); missing: {missing_keys or 'none'}")

# ========================== P-16 row-level security on every tenant-scoped table
tables = dict(re.findall(r"CREATE TABLE clep\.(\w+)\s*\((.*?)\n\);", sql, re.S))
tenant_scoped = [t for t in tables if t != "organization"]

#: Everything schema 11 adds. Named once and used by the checks below.
PHASE11_TABLES = ("evaluation_schedule_candidate", "alert_rule", "alert_event")
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
phase11 = [t for t in tables if t in PHASE11_TABLES]
plain_fks = []
for t in phase11:
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
    f"constraint; {len(phase11)} Phase 11 tables, every reference carrying the "
    f"tenant; missing: {missing_uq or 'none'}", missing_uq + plain_fks)

# ============================== P-18 the schema is the migration set, not a copy
mig = (ROOT / "src/clep/db/migrations.py").read_text(encoding="utf-8")
copies = [str(p.relative_to(ROOT)) for p in ROOT.glob("src/**/*.sql")]
add("P-18", "PASS" if "docs" in mig and "schema" in mig and not copies else "FAIL",
    f"migrations are applied from docs/data/schema/ with no second copy of the DDL; "
    f"copies found: {copies or 'none'}")

# ========== P-27a the schedule trigger is the queue's, not a caller's
#
# The heart of M11.1. A stored schedule and a CRUD surface over it are not
# REQ-F-10-1; neither is a callback someone invokes by hand. This check asserts
# the wiring — a cron job registered on the worker ADR-001 selected — and that
# the integration test does not call the sweep itself.
trigger_defects = []
try:
    import inspect

    from arq.cron import CronJob

    from clep.orchestration import scheduler as _scheduler
    from clep.orchestration import worker as _worker

    if not hasattr(_worker.WorkerSettings, "cron_jobs"):
        trigger_defects.append(
            "the worker registers no cron jobs; nothing fires a schedule")
    else:
        jobs = list(_worker.WorkerSettings.cron_jobs)
        if not jobs or not all(isinstance(j, CronJob) for j in jobs):
            trigger_defects.append("the worker's cron_jobs are not CronJobs")
        if not any(j.coroutine is _scheduler.sweep_schedules for j in jobs):
            trigger_defects.append(
                "the sweep is not the coroutine the cron fires")
    produced = _worker.sweep_cron(1)
    if not isinstance(produced, CronJob):
        trigger_defects.append("sweep_cron does not produce a cron job")
    if produced.run_at_startup:
        trigger_defects.append(
            "the sweep runs at startup; a redeploy would fire every schedule "
            "whose minute it happened to land in, which is a redeploy that "
            "looks like a cadence")
    # And the test that proves it must not shortcut the trigger. The names are
    # imported there — the worker registers them — so what is forbidden is a
    # CALL, which is why each pattern carries its opening parenthesis.
    proof = (ROOT / "tests/test_scheduled_execution.py").read_text("utf-8")
    for shortcut in ("sweep_tenant(", "sweep_schedules(", "enqueue_job(",
                     "execute_scheduled_run(", "evaluate_run("):
        if shortcut in proof:
            trigger_defects.append(
                f"the scheduled-execution test calls {shortcut} itself; the "
                f"trigger must be the worker's")
    # `\b` rather than a substring: `_Worker(` contains `Worker(` and would
    # satisfy a naive check while starting something else entirely.
    if not re.search(r"\bWorker\(", proof) or "sweep_cron(" not in proof:
        trigger_defects.append(
            "the scheduled-execution test does not start a real worker with "
            "the real cron factory")
except Exception as e:
    trigger_defects.append(f"{type(e).__name__}: {e}")
add("P-27a", "PASS" if not trigger_defects else "FAIL",
    f"the sweep is fired by the task queue's own cron, and the test that proves "
    f"it drives nothing itself; defects: {len(trigger_defects)}", trigger_defects)

# ========== P-27b a trigger has an identity, and a duplicate costs nothing
identity_defects = []
try:
    from datetime import datetime, timedelta, timezone

    from clep.orchestration.schedules import (CadenceError, parse_cadence,
                                              trigger_key)

    moment = datetime(2026, 8, 11, 10, 5, tzinfo=timezone.utc)
    if trigger_key("S", moment) != trigger_key("S", moment.replace(second=59)):
        identity_defects.append(
            "the trigger key varies within a minute; two sweeps in the same "
            "minute would create two runs")
    if trigger_key("S", moment) == trigger_key("S", moment + timedelta(minutes=1)):
        identity_defects.append("the trigger key does not distinguish minutes")
    elsewhere = moment.astimezone(timezone(timedelta(hours=9)))
    if trigger_key("S", elsewhere) != trigger_key("S", moment):
        identity_defects.append(
            "the trigger key depends on the reporter's zone")
    # A cadence the platform cannot read is refused, not defaulted.
    for unreadable in ("* * * *", "@hourly", "every 5 minutes", "60 * * * *",
                       "*/0 * * * *", ""):
        try:
            parse_cadence(unreadable)
            identity_defects.append(
                f"{unreadable!r} was accepted as a cadence; a standing order "
                f"that silently never fires is indistinguishable from one that "
                f"works")
        except CadenceError:
            pass
    if parse_cadence("*/15 * * * *").matches(moment.replace(minute=7)):
        identity_defects.append("a cadence fires in a minute it does not name")
    if not parse_cadence("*/15 * * * *").matches(moment.replace(minute=15)):
        identity_defects.append("a cadence does not fire in a minute it names")
except Exception as e:
    identity_defects.append(f"{type(e).__name__}: {e}")
add("P-27b", "PASS" if not identity_defects else "FAIL",
    f"a trigger is identified by its schedule and its UTC minute, and an "
    f"unreadable cadence is refused; defects: {len(identity_defects)}",
    identity_defects)

# ========== P-27c advice, and no way to act on it
advice_defects = []
try:
    from clep.cli.exit_codes import BY_OUTCOME as CODES
    from clep.orchestration.releases import (BY_OUTCOME as ADVICE,
                                             ReleaseObservationError,
                                             recommendation_for)

    if set(CODES) != set(ADVICE):
        advice_defects.append(
            f"outcomes a pipeline can act on but an operator cannot: "
            f"{sorted(set(CODES) - set(ADVICE))}")
    for abstention in ("insufficient_evidence", "not_comparable"):
        if recommendation_for(abstention) == "none":
            advice_defects.append(
                f"{abstention} recommends nothing; a gate that could not tell "
                f"must not read as a gate that approved")
    try:
        recommendation_for("an_outcome_added_later")
        advice_defects.append(
            "an unmapped outcome defaults to a recommendation; a state nobody "
            "has considered would be reported as needing no attention")
    except ReleaseObservationError:
        pass
    # And the store still cannot record having acted.
    observation = tables.get("release_observation", "")
    for actuation in ("applied", "executed", "rolled_back", "target_url",
                      "endpoint", "webhook"):
        if re.search(rf"^\s{{4}}\w*{actuation}\w*\s", observation, re.M):
            advice_defects.append(f"release_observation carries {actuation}")
    for table in ("alert_rule", "alert_event"):
        body = tables.get(table, "")
        if not body:
            advice_defects.append(f"there is no {table} table")
        for actuation in ("webhook", "endpoint", "delivered", "notified",
                          "acknowledged", "recipient", "channel", "retry"):
            if re.search(rf"^\s{{4}}\w*{actuation}\w*\s", body, re.M):
                advice_defects.append(
                    f"{table} carries {actuation}; REQ-F-10-3 forbids the "
                    f"platform from acting, and a column recording that it did "
                    f"is a column that expects it to")
    if not re.search(r"CREATE TRIGGER trg_alert_event__immutable", sql):
        advice_defects.append(
            "an alert firing can be edited; evidence rewritten once the "
            "outcome is known is a retelling")
    if re.search(r"GRANT[^;]*\b(UPDATE|DELETE)\b[^;]*clep\.alert_event\b", sql):
        advice_defects.append("the runtime role may amend an alert firing")
except Exception as e:
    advice_defects.append(f"{type(e).__name__}: {e}")
add("P-27c", "PASS" if not advice_defects else "FAIL",
    f"REQ-F-10-3 across the release and alert surfaces: advice with no "
    f"actuation, abstentions that do not read as approval, and firings that "
    f"cannot be amended; defects: {len(advice_defects)}", advice_defects)

# ========== P-27d REQ-F-10-5, exercised rather than read
budget_defects = []
try:
    from clep.agents.planner import PlanInputs, draft_plan, validate

    over = validate(draft_plan(
        PlanInputs(objective="scheduled", suite_version_id="S",
                   dataset_version_ids=("D",), candidate_labels=("a",),
                   budget=Decimal("0.000000001")), sample_count=100))
    if "exceeds the budget of" not in over:
        budget_defects.append(
            "an over-budget plan validates; REQ-F-10-5 skips such a run rather "
            "than partially executing one")
    under = validate(draft_plan(
        PlanInputs(objective="scheduled", suite_version_id="S",
                   dataset_version_ids=("D",), candidate_labels=("a",),
                   budget=Decimal("1000")), sample_count=100))
    if under:
        budget_defects.append(f"a plan inside its budget was refused: {under}")
    # The schedule cannot be created without one.
    schedule = tables.get("evaluation_schedule", "")
    if "budget_limit             numeric(18, 9) NOT NULL" not in schedule:
        budget_defects.append("a schedule can be created without a budget")
    if "ck_evaluation_schedule__budget_is_positive" not in sql:
        budget_defects.append("a schedule may carry a zero budget")
    # And the sweep must consult the planner rather than a second cost model.
    # Identity, not a name search: a module can hold the name `draft_plan` and
    # have it bound to something else entirely, which is what a source scan
    # would report as compliance.
    if getattr(_scheduler, "draft_plan", None) is not draft_plan:
        budget_defects.append(
            "the sweep's draft_plan is not the planner's; a second cost model "
            "disagrees with the first eventually")
    if getattr(_scheduler, "validate", None) is not validate:
        budget_defects.append("the sweep's validate is not the planner's critic")
except Exception as e:
    budget_defects.append(f"{type(e).__name__}: {e}")
add("P-27d", "PASS" if not budget_defects else "FAIL",
    f"REQ-F-10-5: the planner refuses an over-budget run and the schedule "
    f"cannot exist without a budget; defects: {len(budget_defects)}",
    budget_defects)

# ========== P-28 the CLI, as an installed console script
ci_defects = []
try:
    evidence_path = ROOT / "docs/evidence/phase-11/ci-execution-output.json"
    body = json.loads(evidence_path.read_text(encoding="utf-8"))
    if not body.get("ok"):
        ci_defects.append("the recorded CI run did not pass")
    if "not hosted CI" not in body.get("environment", ""):
        ci_defects.append(
            "the CI evidence does not label itself as a local environment")
    installation = body.get("installation", {})
    if installation.get("entry_points") != ["clep=clep.cli.main:main"]:
        ci_defects.append(
            f"the installed console script is {installation.get('entry_points')}")
    package_file = str(installation.get("package_file", ""))
    if "site-packages" not in package_file:
        ci_defects.append(
            f"the package resolved to {package_file}, not to the isolated "
            f"environment; the evidence would be about the development tree")
    expected = {"successful evaluation": 0, "blocking evaluation": 1,
                "abstention blocks": 70,
                "malformed input is a platform failure": 78}
    for label, code_ in expected.items():
        step = body.get("steps", {}).get(label)
        if step is None:
            ci_defects.append(f"no recorded step for {label!r}")
        elif step["exitCode"] != code_:
            ci_defects.append(
                f"{label} exited {step['exitCode']}, expected {code_}")
    repeat = body.get("steps", {}).get("blocking evaluation, re-run")
    first = body.get("steps", {}).get("blocking evaluation")
    if not repeat or not first or repeat["exitCode"] != first["exitCode"]:
        ci_defects.append("the re-run did not reproduce the same exit code")
    # The declaration itself, checked against the installed metadata rather
    # than against pyproject.toml, which is what a pipeline never reads.
    from importlib.metadata import entry_points
    # A set, not a list: an editable install can leave more than one metadata
    # copy on the path, and how many the development environment happens to
    # carry is not what this check is about. What it targets is the value the
    # name resolves to.
    declared = {e.value for e in entry_points(group="console_scripts")
                if e.name == "clep"}
    if declared != {"clep.cli.main:main"}:
        ci_defects.append(f"installed console_scripts entry: {sorted(declared)}")
    runner = (ROOT / "docs/evidence/phase-11/ci_execution.py").read_text("utf-8")
    if "REFUSING" not in runner:
        ci_defects.append(
            "the CI runner has no refusal path; evidence describing an "
            "evaluation that never ran is worse than none")
except FileNotFoundError as e:
    ci_defects.append(f"CI evidence missing: {e}")
except Exception as e:
    ci_defects.append(f"{type(e).__name__}: {e}")
add("P-28", "PASS" if not ci_defects else "FAIL",
    f"the CLI installs from a clean checkout into an isolated environment, "
    f"resolves as a console script, and hands a pipeline the exit code the "
    f"gate decided; defects: {len(ci_defects)}", ci_defects)

# ========== P-29a analytics are derived, never stored
derived_defects = []
try:
    from clep.analytics import repository as _analytics

    writes = re.findall(r"(INSERT INTO|UPDATE|DELETE FROM)\s+clep\.(\w+)",
                        _inspect.getsource(_analytics))
    if writes:
        derived_defects.append(
            f"the analytics repository writes: {writes}; a stored aggregate is "
            f"a figure whose provenance is a previous computation")
    # No rollup table exists to write to, either.
    for table in tables:
        if re.search(r"(_daily|_rollup|_aggregate|_summary|_snapshot)$", table):
            derived_defects.append(f"{table} looks like a stored aggregate")
    # Alerting writes exactly two tables and no others.
    from clep.analytics import alerts as _alerts
    alert_writes = {t for _, t in re.findall(
        r"(INSERT INTO|UPDATE)\s+clep\.(\w+)", _inspect.getsource(_alerts))}
    if alert_writes - {"alert_rule", "alert_event"}:
        derived_defects.append(
            f"alerting writes tables beyond its own: {sorted(alert_writes)}")
except Exception as e:
    derived_defects.append(f"{type(e).__name__}: {e}")
add("P-29a", "PASS" if not derived_defects else "FAIL",
    f"every analytics figure is computed on read; the package writes nothing "
    f"and no rollup table exists; defects: {len(derived_defects)}",
    derived_defects)

# ========== P-29b REQ-F-11-7, exercised: an incomplete figure says so
completeness_defects = []
try:
    from clep.analytics.completeness import COMPLETE, INCOMPLETE, completeness_of

    whole = completeness_of(contributing_runs=2, incomplete_runs=0,
                            observations=20, unresolved_observations=0)
    if whole.state != COMPLETE or whole.reason is not None:
        completeness_defects.append("a whole figure is marked incomplete")
    partial = completeness_of(contributing_runs=2, incomplete_runs=1,
                              observations=20, unresolved_observations=0)
    if partial.state != INCOMPLETE or "did not finish complete" not in partial.reason:
        completeness_defects.append(
            "a figure over an unfinished run is not marked, or does not say why")
    dropped = completeness_of(contributing_runs=1, incomplete_runs=0,
                              observations=8, unresolved_observations=2)
    if dropped.state != INCOMPLETE or "counted as zero" not in dropped.reason:
        completeness_defects.append(
            "samples excluded from a mean are not reported")
    empty = completeness_of(contributing_runs=0, incomplete_runs=0,
                            observations=0, unresolved_observations=0)
    if empty.state != INCOMPLETE:
        completeness_defects.append(
            "a figure with no observations reads as complete; 'we measured "
            "nothing' and 'we found nothing wrong' are the two answers "
            "REQ-F-08-4 keeps apart")
    if "state" not in whole.as_dict():
        completeness_defects.append("the marking does not serialise")
except Exception as e:
    completeness_defects.append(f"{type(e).__name__}: {e}")
add("P-29b", "PASS" if not completeness_defects else "FAIL",
    f"REQ-F-11-7 exercised: every way evidence can be incomplete is marked and "
    f"explained; defects: {len(completeness_defects)}", completeness_defects)

# ========== P-29c REQ-F-11-2, exercised: no benchmark, no ranking
leaderboard_defects = []
try:
    from clep.analytics.repository import AnalyticsError, AnalyticsRepository

    class _RefusingConnection:
        """Any query at all is a failure: the refusal must come first."""

        def execute(self, *args, **kwargs):
            raise AssertionError(
                "the leaderboard queried the store before refusing a request "
                "with no benchmark")

    repository = AnalyticsRepository(_RefusingConnection(), "00000000-0000-0000"
                                                            "-0000-000000000000")
    for absent in (None, ""):
        try:
            repository.leaderboard("01ARZ3NDEKTSV4RRFFQ69G5FAV",
                                   suite_version_id=absent)
            leaderboard_defects.append(
                f"a leaderboard was produced with suite_version_id={absent!r}; "
                f"REQ-F-11-2 forbids a global ranking")
        except AnalyticsError:
            pass
except Exception as e:
    leaderboard_defects.append(f"{type(e).__name__}: {e}")
add("P-29c", "PASS" if not leaderboard_defects else "FAIL",
    f"REQ-F-11-2 exercised: a leaderboard without a named benchmark is refused "
    f"before any query runs; defects: {len(leaderboard_defects)}",
    leaderboard_defects)

# ========== P-29d REQ-F-10-4, exercised: history, and no invented threshold
drift_defects = []
try:
    from clep.analytics import drift as _drift

    def _history(*values):
        return [_drift.HistoryPoint(run_id=f"R{i}", baseline_id=f"B{i}",
                                    baseline_state="approved",
                                    mean_score=Decimal(v), observations=10,
                                    approved_at=None)
                for i, v in enumerate(values)]

    try:
        _drift.classify(metric_key="m", run_id="R", current_value=Decimal("0.5"),
                        current_observations=10, history=_history("0.8", "0.8"),
                        minimum_history=1, tolerance=Decimal("0.1"))
        drift_defects.append(
            "a minimum history of one was accepted; that is the single prior "
            "run REQ-F-10-4 exists to prevent")
    except _drift.DriftError:
        pass
    one = _drift.classify(metric_key="m", run_id="R",
                          current_value=Decimal("0.2"),
                          current_observations=10, history=_history("0.8"),
                          minimum_history=2, tolerance=Decimal("0.01"))
    if one.verdict != _drift.INSUFFICIENT_HISTORY:
        drift_defects.append(
            f"one baseline produced {one.verdict}; a history of one is not a "
            f"history")
    unconfigured = _drift.classify(
        metric_key="m", run_id="R", current_value=Decimal("0.2"),
        current_observations=10, history=_history("0.8", "0.81", "0.79"),
        minimum_history=None, tolerance=None)
    if unconfigured.verdict != _drift.INSUFFICIENT_CONFIGURATION:
        drift_defects.append(
            f"drift was classified as {unconfigured.verdict} with no configured "
            f"tolerance; the platform invented a threshold nobody calibrated")
    if unconfigured.position != _drift.BELOW_RANGE:
        drift_defects.append(
            "the position relative to the observed range was not reported; it "
            "costs no calibration and is the one fact available")
    # The median, not the most recent baseline: a detector reading the last run
    # alone would call this stable.
    configured = _drift.classify(
        metric_key="m", run_id="R", current_value=Decimal("0.80"),
        current_observations=10,
        history=_history("0.80", "0.81", "0.79", "0.50"),
        minimum_history=3, tolerance=Decimal("0.05"))
    if configured.verdict != _drift.STABLE:
        drift_defects.append(
            f"a value matching the historical median was classified "
            f"{configured.verdict}; the comparison is not against the history")
except Exception as e:
    drift_defects.append(f"{type(e).__name__}: {e}")
add("P-29d", "PASS" if not drift_defects else "FAIL",
    f"REQ-F-10-4 exercised: a history of one abstains, an unconfigured "
    f"tolerance abstains, and the comparison is against the history rather "
    f"than the last run; defects: {len(drift_defects)}", drift_defects)

# ========== P-29e REQ-F-11-8, exercised: the report keeps its qualifications
scorecard_defects = []
try:
    from datetime import datetime as _dt, timezone as _tz

    from clep.analytics import scorecard as _scorecard
    from clep.analytics.completeness import completeness_of as _mark
    from clep.analytics.repository import (Distribution, OperationalAnalytics,
                                           TrendPoint)

    incomplete = _mark(contributing_runs=1, incomplete_runs=1, observations=4,
                       unresolved_observations=6)
    empty = Distribution(measured=0, minimum=None, maximum=None,
                         quantiles={q: None for q in (Decimal("0.5"),
                                                      Decimal("0.95"))})
    card = _scorecard.Scorecard(
        project_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", suite_version_id=None,
        window_days=7, generated_at=_dt(2026, 8, 11, tzinfo=_tz.utc),
        trend=(TrendPoint(run_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", metric_key="m",
                          mean_score=Decimal("0.5"), observations=4,
                          unresolved=6, trigger="schedule",
                          run_completeness="cancelled", is_baseline=False,
                          created_at=_dt(2026, 8, 11, tzinfo=_tz.utc),
                          completeness=incomplete),),
        leaderboard=(),
        operational=OperationalAnalytics(
            model_latency_ms=empty, evaluator_latency_ms=empty,
            successful_tasks=0, prompt_tokens=0, completion_tokens=0,
            cost_total=Decimal(0), cost_currency=None,
            tokens_per_successful_task=None, cost_per_successful_task=None,
            run_ids=(), completeness=incomplete),
        judges=type("J", (), dict(
            judgements=0, scored=0, abstained=0, failed=0, consensus_results=0,
            agreed=0, escalated=0, disagreement_measured=0,
            mean_disagreement=None, escalation_reasons={}, calibration=(),
            run_ids=(), completeness=incomplete))(),
        agents=type("A", (), dict(
            samples_with_trajectory=0, completed_tasks=0,
            truncated_trajectories=0, tool_calls=0, failed_tool_calls=0,
            tool_success_rate=None, samples_with_loops=0,
            samples_with_retries=0, trajectory_failures=0, by_tool=(),
            run_ids=(), completeness=incomplete))(),
        drift=(), alerts=(), alert_rules=0)

    text = _scorecard.human_readable(card)
    machine = _scorecard.machine_readable(card)
    if incomplete.reason.split(";")[0].strip() not in text:
        scorecard_defects.append(
            "the human report drops the incompleteness sentence; REQ-F-11-8 "
            "requires the qualifications to survive the simplification")
    if "## What this does not tell you" not in text:
        scorecard_defects.append(
            "the report omits what the platform has not established")
    for limitation in _scorecard.UNCALIBRATED:
        if limitation not in text:
            scorecard_defects.append(f"the report omits: {limitation[:60]}…")
    if not machine.get("notEstablished"):
        scorecard_defects.append(
            "the machine-readable scorecard carries no limitations")
    if machine["qualityTrend"][0]["completeness"]["state"] != "incomplete":
        scorecard_defects.append(
            "a figure over a cancelled run is not marked in the export")
    if "observations" not in str(machine["qualityTrend"][0]):
        scorecard_defects.append("a reported figure carries no observation count")
except Exception as e:
    scorecard_defects.append(f"{type(e).__name__}: {e}")
add("P-29e", "PASS" if not scorecard_defects else "FAIL",
    f"REQ-F-11-8 exercised: rendered over incomplete evidence, both "
    f"representations keep the qualification and state what is unestablished; "
    f"defects: {len(scorecard_defects)}", scorecard_defects)

# ========== P-30 REQ-F-11-9, exercised: a rule decides, and fires on nothing else
alert_defects = []
try:
    from clep.analytics.alerts import AlertRuleRow, OPERATIONAL_METRICS

    higher = AlertRuleRow(id="R", project_id="P", slug="s", display_name="s",
                          dimension="quality", metric_key="m",
                          direction="higher_is_better",
                          threshold=Decimal("0.5"), minimum_sample_size=5,
                          state="active")
    lower = AlertRuleRow(id="R", project_id="P", slug="s", display_name="s",
                         dimension="latency", metric_key="model_latency_p95_ms",
                         direction="lower_is_better", threshold=Decimal("500"),
                         minimum_sample_size=5, state="active")
    if not higher.breached_by(Decimal("0.4")):
        alert_defects.append("a quality floor does not fire below its threshold")
    if higher.breached_by(Decimal("0.5")):
        alert_defects.append(
            "a value exactly at the threshold fires; a rule written as 'alert "
            "below 0.8' that fires at 0.8 is a rule its author will disable")
    if not lower.breached_by(Decimal("501")):
        alert_defects.append("a latency ceiling does not fire above its threshold")
    if lower.breached_by(Decimal("499")):
        alert_defects.append("a latency ceiling fires below its threshold")
    if not set(OPERATIONAL_METRICS) >= {"cost", "latency"}:
        alert_defects.append(
            "cost and latency rules are not constrained to figures the platform "
            "computes; a rule on a figure that does not exist never fires and "
            "never says why")
    # And every figure a rule may name is one the analytics actually produce.
    from clep.analytics.repository import AnalyticsRepository as _AR
    produced = _inspect.getsource(_AR.figures_for_run)
    for dimension, names in OPERATIONAL_METRICS.items():
        for name in names:
            if f'"{name}"' not in produced:
                alert_defects.append(
                    f"a {dimension} rule may name {name!r}, which the analytics "
                    f"never compute")
except Exception as e:
    alert_defects.append(f"{type(e).__name__}: {e}")
add("P-30", "PASS" if not alert_defects else "FAIL",
    f"REQ-F-11-9 exercised: a rule fires strictly on the wrong side of its "
    f"threshold, and can only name figures the platform computes; defects: "
    f"{len(alert_defects)}", alert_defects)

# ============================================================ P-20 secrets
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
add("P-20", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} undisclosed match(es), "
    f"{len(set(disclosed))} disclosed and unremovable from published history",
    sec + blob_sec + sorted(set(disclosed)))

# ======================================================== P-21 attribution
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
add("P-21", "PASS" if not att and not msg_hits else "FAIL",
    f"governed scope: {len(att)} file match(es), {len(msg_hits)} history match(es)",
    att + msg_hits)

# ======================================================= P-22 git identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()) - {""})
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()) - {""})
add("P-22", "PASS" if len(authors) == 1 and len(committers) == 1 else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ================================================== P-23 canonical document
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
add("P-23", "PASS" if ok else "FAIL",
    f"canonical document local={bool(docx)} tracked={bool(tracked)} "
    f"ignored={ignored} refs_scanned={len(refs)} "
    f"reachable_from_published={len(published)} "
    f"reachable_from_local_undisclosed={len(undisclosed)} "
    f"disclosed_local_only={len(disclosed_local)}",
    published + undisclosed + disclosed_local)

# ============================================================ P-24 hygiene
tracked_files = [f for f in git("ls-files").splitlines() if f]
dirty = [l for l in git("status", "--porcelain").splitlines() if l]
strays = [f for f in tracked_files
          if re.search(r"(\.orig|\.rej|\.bak|~|\.DS_Store|Thumbs\.db)$", f)]
add("P-24", "PASS" if not dirty and not strays else "FAIL",
    f"{len(tracked_files)} tracked file(s); stray: {len(strays)}; "
    f"clean tree: {not dirty}", dirty[:6] + strays)

# =========================================== P-25 phase boundary not overrun
later_phase = []
for pattern, label in (("docs/**/ADR-019*.md", "ADRs beyond the recorded set"),
                       ("docs/**/ADR-02*.md", "ADRs beyond the recorded set"),
                       ("src/clep/rbac/**", "enterprise RBAC (Phase 12)"),
                       ("src/clep/telemetry/**", "observability (Phase 13)"),
                       ("infra/**", "infrastructure (Phase 14)"),
                       ("terraform/**", "infrastructure (Phase 14)")):
    hits = [str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)]
    if hits:
        later_phase.append(f"{label}: {hits[:2]}")
add("P-25", "PASS" if not later_phase else "FAIL",
    f"Phase 12+ artifact classes checked: 6; artifacts Phase 11 must "
    f"not contain: {len(later_phase)}", later_phase)

# ============ P-26 every earlier gate is reachable from this one, by derivation
edges = {}
all_validators = sorted(
    p.relative_to(ROOT).as_posix()
    for p in ROOT.glob("docs/evidence/**/check_*.py"))
for path in all_validators:
    body = (ROOT / path).read_text(encoding="utf-8")
    edges[path] = {m for m in re.findall(r"docs/evidence/[\w.\-]+/check_\w+\.py", body)
                   if m != path}
root = "docs/evidence/phase-11/check_phase11.py"
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
add("P-26", "PASS" if not unreachable and not missing_files else "FAIL",
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
