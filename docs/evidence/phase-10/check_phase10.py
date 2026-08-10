"""Phase 10 comprehensive validation, with regression over every earlier phase.

Phase 10 connects the parts, and adds the one interface a pipeline actually
reads: an exit code. The failures that matter are a gate that exits zero when it
measured nothing, an evaluator wired up beside the harness rather than through
it, a judgement that reached a verdict without being written down, and a
recommendation the platform could act on by itself. Each of those looks like
working software.

The frame below — the isolated-clone gate runner, the security sweeps, the
reachability closure — is spliced from the Phase 8 validator rather than copied,
because two copies of a gate drift and only one of them gets reviewed.

Earlier phase gates are re-evaluated against their OWN trees, in isolated clones
pruned to their own history, for the reasons Phases 5, 6 and 7 established.

Usage: python docs/evidence/phase-10/check_phase10.py <repo_root>
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
    f"Phase 10 schedule and release-observation tables")

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


# Only the immediately preceding gate is invoked here, and that is a change
# from Phases 7 to 9, which each named every earlier gate directly.
#
# The reason is arithmetic. Each phase gate re-runs the gates before it, so
# naming them all again at the top duplicates the entire chain: Phase 10's first
# attempt ran the Spike Sprint, Phase 4, 5, 6, 7 and 8 gates directly and then
# started Phase 9's gate, which runs all six again — and the subprocess timed
# out at forty minutes with the work still compounding. Phases 11 to 15 would
# each make it worse.
#
# What makes the shorter form sufficient is P-26, which does not assume the
# chain and does not trust this comment: it derives the closure from the
# invocation paths each gate actually contains and requires every validator in
# the repository to be reachable. Coverage is therefore still total, and it is
# still checked — by the mechanism built for exactly that, rather than by
# repeating the work at every level.
gate_at_its_own_tree("P-5", "phase-9)",
                     "docs/evidence/phase-9/check_phase9.py", "Phase 9",
                     timeout=7200)

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
    if len(ops) != 40:
        contract_defects.append(f"expected 40 declared operations, found {len(ops)}")
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
except Exception as e:
    vocab_defects.append(f"{type(e).__name__}: {e}")
add("P-12", "PASS" if not vocab_defects else "FAIL",
    f"vocabularies compared across schema, contract and code: {len(pairs) + 10}; "
    f"disagreements: {len(vocab_defects)}", vocab_defects)

# ================================== P-13 every dependency carries a justification
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
deps = set(re.findall(r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?[><=!]', pyproject))
justified = (ROOT / "docs/dependencies.md").read_text(encoding="utf-8")
unjustified = sorted(d for d in deps if f"`{d}`" not in justified)
add("P-13", "PASS" if not unjustified else "FAIL",
    f"{len(deps)} declared dependencies, each with a recorded reason and a rejected "
    f"alternative; unjustified: {unjustified or 'none'}. Phase 10 added none — "
    f"the CLI is argparse, which is the standard library.")

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
                 "judge_run": "uq_judge_run__idempotency_key"}
missing_keys = [t for t, c in effect_tables.items() if c not in sql]
add("P-15", "PASS" if not missing_keys else "FAIL",
    f"every externally visible effect carries a unique idempotency key "
    f"({len(effect_tables)} tables, including a judgement, which costs money); "
    f"missing: {missing_keys or 'none'}")

# ========================== P-16 row-level security on every tenant-scoped table
tables = dict(re.findall(r"CREATE TABLE clep\.(\w+)\s*\((.*?)\n\);", sql, re.S))
tenant_scoped = [t for t in tables if t != "organization"]

#: Everything schema 10 adds. Named once and used by the checks below.
PHASE10_TABLES = ("evaluation_schedule", "release_observation")
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
phase10 = [t for t in tables if t in PHASE10_TABLES]
plain_fks = []
for t in phase10:
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
    f"constraint; {len(phase10)} Phase 10 tables, every reference carrying the "
    f"tenant; missing: {missing_uq or 'none'}", missing_uq + plain_fks)

# ============================== P-18 the schema is the migration set, not a copy
mig = (ROOT / "src/clep/db/migrations.py").read_text(encoding="utf-8")
copies = [str(p.relative_to(ROOT)) for p in ROOT.glob("src/**/*.sql")]
add("P-18", "PASS" if "docs" in mig and "schema" in mig and not copies else "FAIL",
    f"migrations are applied from docs/data/schema/ with no second copy of the DDL; "
    f"copies found: {copies or 'none'}")

# ========== P-19a the evaluators run through the harness, not beside it
#
# The Phase 9 review's Risk 5. Calling the evaluator library directly and
# calling the result an end-to-end run is precisely what this check exists to
# refuse — so it is asserted on the run loop's own wiring, not on a test's.
harness_defects = []
try:
    import inspect

    from clep.orchestration import runner as _runner

    signature = inspect.signature(_runner.RunExecutor.__init__)
    for parameter in ("analysis_repository", "judge_panel"):
        if parameter not in signature.parameters:
            harness_defects.append(
                f"RunExecutor takes no {parameter}; whatever drives the Phase 9 "
                f"evaluators is doing it outside the execution path")
    source = inspect.getsource(_runner)
    # One SampleContext construction. There were two before Phase 10 and they
    # had already drifted in which fields they passed.
    constructions = source.count("SampleContext(")
    if constructions != 1:
        harness_defects.append(
            f"{constructions} SampleContext constructions in the run loop; an "
            f"evaluator could see one thing while scoring and another while "
            f"being recorded")
    for field in ("contexts", "citations", "required_context_ids",
                  "agent_trajectory", "tool_schemas", "expected_tools"):
        if field not in source:
            harness_defects.append(f"the loop does not pass {field} to evaluators")
    # A failed candidate is not judged: a judgement of nothing would be a score
    # against a provider outage.
    # The whole method, not a fixed window of it. The first version of this
    # read the first 400 characters, which the docstring filled, and reported a
    # guard that is present and tested as missing.
    judge_method = source.split("    def _judge")[1].split("\n    def ")[0]
    if "candidate_outcome.succeeded" not in judge_method:
        harness_defects.append(
            "the loop judges a candidate that failed; a judgement of nothing "
            "is a score against a provider outage")
    from clep.evaluators.rag import register_rag_evaluators
    from clep.evaluators.agent import register_agent_evaluators
    from clep.evaluators.sdk import EvaluatorRegistry
    registry = EvaluatorRegistry()
    register_rag_evaluators(registry)
    register_agent_evaluators(registry)
    if len(registry) != 9:
        harness_defects.append(
            f"{len(registry)} evaluators registered, expected the four RAG and "
            f"five agent evaluators Phase 9 delivered")
except Exception as e:
    harness_defects.append(f"{type(e).__name__}: {e}")
add("P-19a", "PASS" if not harness_defects else "FAIL",
    f"the run loop drives the Phase 9 evaluators, builds one sample context, "
    f"and does not judge a candidate that failed; defects: "
    f"{len(harness_defects)}", harness_defects)

# ============ P-19b judge results are persisted, through the repository
persistence_defects = []
try:
    import inspect

    from clep.judges import panel as _panel

    source = inspect.getsource(_panel)
    if "record_judgement" not in source or "record_consensus" not in source:
        persistence_defects.append(
            "the panel does not write through JudgeRepository; the Phase 9 "
            "experiment wrote files and the review asked for rows")
    # Every judgement, whatever it resolved to. Recording only the successes
    # would make an ensemble look unanimous because its dissenters crashed.
    judge_block = source.split("def judge")[1]
    if judge_block.index("record_judgement") > judge_block.index("reach_consensus"):
        persistence_defects.append(
            "consensus is reached before the judgements are written; a crash "
            "between the two would leave a verdict with no evidence")
    # Exercised, not read. A source search for "if vote.is_scoring" missed a
    # plant that wrote "if not vote.is_scoring" — the same string-matching
    # weakness that has now cost four checks across three phases.
    from decimal import Decimal as _D

    from clep.evaluators.sdk import SampleContext as _Sample
    from clep.judges.consensus import Ensemble as _Ensemble
    from clep.judges.sdk import JudgeVersion as _JV, Vote as _Vote

    class _RecordingRepository:
        def __init__(self):
            self.judgements, self.consensus = [], []

        def record_judgement(self, **kw):
            self.judgements.append(kw)
            return "recorded"

        def record_consensus(self, **kw):
            self.consensus.append(kw)
            return "recorded"

    class _ScriptedGateway:
        """One judge answers, one does not. Both must be written down."""

        def invoke(self, invocation):
            from clep.providers.gateway import CandidateOutcome
            from clep.providers.port import CompletionResult, Usage
            text = ("SCORE: 0.5" if invocation.request.model == "answers"
                    else "I would rather not")
            return CandidateOutcome(
                invocation.candidate_label,
                result=CompletionResult(text=text, model=invocation.request.model,
                                        usage=Usage(1, 1, 2), endpoint_name="e",
                                        endpoint_kind="hosted"))

    answering = _JV(slug="a", version="1", model="answers", endpoint_name="ea",
                    rubric="r")
    refusing = _JV(slug="b", version="1", model="refuses", endpoint_name="eb",
                   rubric="r")
    recorder = _RecordingRepository()
    _panel.JudgePanel(
        ensemble=_Ensemble(judges=(answering, refusing),
                           agreement_threshold=_D("0.2"),
                           minimum_scoring_votes=2),
        ensemble_id="E", judge_version_ids={answering.version_key: "VA",
                                            refusing.version_key: "VB"},
        gateway=_ScriptedGateway(), repository=recorder, project_id="P",
    ).judge(run_id="R", run_sample_id="S",
            sample=_Sample(example_id="x", prompt="p", output="o"),
            outcome=_panel.PanelOutcome())
    if len(recorder.judgements) != 2:
        persistence_defects.append(
            f"{len(recorder.judgements)} of 2 judgements were written; an "
            f"ensemble whose dissenters crashed would look unanimous")
    if len(recorder.consensus) != 1:
        persistence_defects.append("the consensus was not written")
    # The store keeps the two apart, so an unscored judgement has no row to
    # read as a zero.
    if "score" in tables.get("judge_run", "").split("CONSTRAINT")[0].replace(
            "resolution", ""):
        persistence_defects.append("judge_run carries a score column")
    if not re.search(r"CREATE TABLE clep\.judge_vote", sql):
        persistence_defects.append("there is no separate table for a judge score")
except Exception as e:
    persistence_defects.append(f"{type(e).__name__}: {e}")
add("P-19b", "PASS" if not persistence_defects else "FAIL",
    f"judgements are persisted through the repository, all of them, before a "
    f"verdict is reached; defects: {len(persistence_defects)}",
    persistence_defects)

# ============ P-19c a resolved sample is immutable, and truncation obeys it
immutable_sample_defects = []
try:
    import inspect

    from clep.orchestration import repository as _run_repo
    from clep.rag import repository as _analysis_repo

    if re.search(r"GRANT[^;]*\bUPDATE\b[^;]*clep\.run_sample\b", sql):
        immutable_sample_defects.append(
            "the runtime role is granted UPDATE on run_sample; I-18 makes a "
            "resolved sample immutable")
    analysis_source = inspect.getsource(_analysis_repo)
    if "UPDATE clep.run_sample" in analysis_source:
        immutable_sample_defects.append(
            "the analysis repository updates run_sample; the store refuses it, "
            "and a fact known at insert has no business arriving as an amendment")
    if "trajectory_truncated" not in inspect.getsource(_run_repo):
        immutable_sample_defects.append(
            "truncation is not written when the sample is written")
    if not re.search(r"ALTER TABLE clep\.run_sample\s+ADD COLUMN "
                     r"trajectory_truncated", sql):
        immutable_sample_defects.append(
            "the sample does not record whether its trajectory was cut")
except Exception as e:
    immutable_sample_defects.append(f"{type(e).__name__}: {e}")
add("P-19c", "PASS" if not immutable_sample_defects else "FAIL",
    f"a resolved sample is immutable and truncation is written with it; "
    f"defects: {len(immutable_sample_defects)}", immutable_sample_defects)

# ================ P-19d the exit code a pipeline acts on
exit_defects = []
try:
    from clep.cli import exit_codes as _codes

    declared = set(_contract.enum_of("GateOutcome", str(ROOT)))
    unmapped = sorted(declared - set(_codes.BY_OUTCOME))
    if unmapped:
        exit_defects.append(
            f"gate outcomes with no exit code: {unmapped}; an unmapped outcome "
            f"would reach the fallback and mean something nobody chose")
    # The one that matters. A gate that exits zero when it could not measure
    # anything is green within a week and unread within two.
    for blocking in ("hard_fail", "approval_required", "insufficient_evidence",
                     "not_comparable"):
        if _codes.for_outcome(blocking) == 0:
            exit_defects.append(
                f"{blocking} lets a pipeline through; shipping on 'we could not "
                f"tell' is the failure this product exists to prevent")
    for passing in ("pass", "warning"):
        if _codes.for_outcome(passing) != 0:
            exit_defects.append(
                f"{passing} blocks; the policy chose it precisely so it would "
                f"not, and overriding that here is the wrong place")
    codes = {o: _codes.for_outcome(o)
             for o in ("hard_fail", "approval_required", "insufficient_evidence",
                       "not_comparable")}
    if len(set(codes.values())) != len(codes):
        exit_defects.append(
            f"blocking outcomes share exit codes {codes}; a pipeline cannot "
            f"route an approval to a human and a defect to an engineer")
    if _codes.for_outcome("an_outcome_added_later") == 0:
        exit_defects.append(
            "an unknown outcome passes; a new gate outcome would ship "
            "everything until someone noticed")
    # No escape hatch. A team that wants to proceed records an exception, which
    # is audited, expires and names who decided.
    cli_source = (ROOT / "src/clep/cli/main.py").read_text(encoding="utf-8")
    for escape in ("--ignore-abstentions", "--allow-abstain", "--no-fail",
                   "--force"):
        if escape in cli_source:
            exit_defects.append(f"the CLI offers {escape}")
except Exception as e:
    exit_defects.append(f"{type(e).__name__}: {e}")
add("P-19d", "PASS" if not exit_defects else "FAIL",
    f"every contract outcome maps to an exit code, abstentions block, blocking "
    f"outcomes are distinguishable, and there is no override; defects: "
    f"{len(exit_defects)}", exit_defects)

# ============== P-19e the CLI decides nothing and changes nothing
cli_defects = []
try:
    from clep.cli.main import build_parser

    parser = build_parser()
    commands = sorted(parser._subparsers._group_actions[0].choices)
    if commands != ["analysis", "decision", "gate"]:
        cli_defects.append(f"unexpected subcommands: {commands}")
    # REQ-F-10-3, structurally: no subcommand that could change anything.
    for forbidden in ("deploy", "rollback", "promote", "approve", "delete",
                      "publish"):
        if forbidden in commands:
            cli_defects.append(
                f"`clep {forbidden}` exists; REQ-F-10-3 forbids the product "
                f"from autonomously changing a production system")
    source = (ROOT / "src/clep/cli/main.py").read_text(encoding="utf-8")
    # A CLI that evaluated anything itself would be a second decision path, and
    # the two would disagree eventually.
    for reimplementation in ("reach_consensus", "run_evaluator", "compare(",
                             "_apply_thresholds"):
        if reimplementation in source:
            cli_defects.append(
                f"the CLI calls {reimplementation}; a second decision path "
                f"disagrees with the first eventually")
except Exception as e:
    cli_defects.append(f"{type(e).__name__}: {e}")
add("P-19e", "PASS" if not cli_defects else "FAIL",
    f"the CLI reports and exits: three read-only subcommands, no evaluation of "
    f"its own; defects: {len(cli_defects)}", cli_defects)

# ========== P-19f REQ-F-10-3 in the schema: advice, with no way to act
advice_defects = []
try:
    body = tables.get("release_observation", "")
    if not body:
        advice_defects.append("there is no release observation table")
    # A schema able to record having changed a production system is a schema
    # that expects to.
    for actuation in ("applied", "executed", "rolled_back", "target_url",
                      "endpoint", "webhook"):
        if re.search(rf"^\s{{4}}\w*{actuation}\w*\s", body, re.M):
            advice_defects.append(
                f"release_observation carries {actuation}; REQ-F-10-3 forbids "
                f"the platform from acting, and a column recording that it did "
                f"is a column that expects it to")
    if "ck_release_observation__recommendation" not in sql:
        advice_defects.append("a recommendation can be anything at all")
    if not re.search(r"CREATE TRIGGER trg_release_observation__immutable", sql):
        advice_defects.append(
            "an observation can be edited; advice rewritten after the outcome "
            "is known is hindsight")
    if "none" not in ddl_enum("ck_release_observation__recommendation"):
        advice_defects.append(
            "there is no way to record that nothing needs doing, so every "
            "observation must recommend something")
    # REQ-F-10-5: a schedule without a budget is an unbounded standing order.
    schedule = tables.get("evaluation_schedule", "")
    if "budget_limit             numeric(18, 9) NOT NULL" not in schedule:
        advice_defects.append("a schedule can be created without a budget")
    if "ck_evaluation_schedule__budget_is_positive" not in sql:
        advice_defects.append(
            "a schedule may carry a zero budget, which skips every run and "
            "reads as a schedule that silently does nothing")
except Exception as e:
    advice_defects.append(f"{type(e).__name__}: {e}")
add("P-19f", "PASS" if not advice_defects else "FAIL",
    f"REQ-F-10-3 and REQ-F-10-5 in the store: advice with no actuation, and no "
    f"unbudgeted standing order; defects: {len(advice_defects)}", advice_defects)

# ========== P-19g the end-to-end evidence exists, and is what it claims
evidence_defects = []
try:
    real = ROOT / "docs/evidence/phase-10/real-end-to-end-output.json"
    body = json.loads(real.read_text(encoding="utf-8"))
    counts = body["counts"]
    for table, least in (("judge_run", 6), ("judge_vote", 1),
                         ("consensus_result", 2), ("retrieved_context", 2),
                         ("trajectory_step", 2), ("required_context", 1)):
        if counts.get(table, 0) < least:
            evidence_defects.append(
                f"the real run persisted {counts.get(table, 0)} {table} rows, "
                f"expected at least {least}")
    if not body.get("decision"):
        evidence_defects.append("the real run reached no gate decision")
    if body.get("comparisons", 0) < 1:
        evidence_defects.append("the real gate decision cites no comparison")
    analysis = body.get("analysis") or {}
    if not analysis.get("retrievedContexts"):
        evidence_defects.append("nothing was read back out of the store")
    runner = (ROOT / "docs/evidence/phase-10/real_end_to_end.py").read_text("utf-8")
    if "REFUSING" not in runner:
        evidence_defects.append(
            "the runner has no refusal path; a run against an unreachable model "
            "must be recorded as not having happened")
    for fallback in ("StubProvider", "FakeAdapter", "_fake_reply"):
        if fallback in runner:
            evidence_defects.append(f"the runner can fall back to {fallback}")
    # And the deterministic half, which must not need a model at all.
    suite = (ROOT / "tests/test_end_to_end.py").read_text(encoding="utf-8")
    if "localhost:81" in suite:
        evidence_defects.append(
            "the deterministic end-to-end test reaches a live model; it must "
            "cover the persistence path without one")
except FileNotFoundError as e:
    evidence_defects.append(f"end-to-end evidence missing: {e}")
except Exception as e:
    evidence_defects.append(f"{type(e).__name__}: {e}")
add("P-19g", "PASS" if not evidence_defects else "FAIL",
    f"the end-to-end evidence: a real run persisted and read back, and a "
    f"deterministic one needing no model; defects: {len(evidence_defects)}",
    evidence_defects)

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
                       ("src/clep/dashboards/**", "dashboards (Phase 11)"),
                       ("src/clep/rbac/**", "enterprise RBAC (Phase 12)"),
                       ("src/clep/telemetry/**", "observability (Phase 13)")):
    hits = [str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)]
    if hits:
        later_phase.append(f"{label}: {hits[:2]}")
add("P-25", "PASS" if not later_phase else "FAIL",
    f"Phase 11+ artifact classes checked: 5; artifacts Phase 10 must "
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
root = "docs/evidence/phase-10/check_phase10.py"
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
