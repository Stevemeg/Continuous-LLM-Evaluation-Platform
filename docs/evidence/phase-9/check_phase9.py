"""Phase 9 comprehensive validation, with regression over every earlier phase.

Phase 9 evaluates retrieval and agents, where the failure that matters is a
number that looks like a measurement. A groundedness score computed from word
overlap. A retrieval hit rate that is 1.0 because nothing said what was
required. A truncated trajectory scored as a completed one. A hallucination
"score" that cannot say whether the evidence was absent or contradictory. Each
reads as a result and is an artefact of the arithmetic.

The frame below — the isolated-clone gate runner, the security sweeps, the
reachability closure — is spliced from the Phase 8 validator rather than copied,
because two copies of a gate drift and only one of them gets reviewed.

Earlier phase gates are re-evaluated against their OWN trees, in isolated clones
pruned to their own history, for the reasons Phases 5, 6 and 7 established.

Usage: python docs/evidence/phase-9/check_phase9.py <repo_root>
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
    f"Phase 9 retrieval, trajectory and analysis tables")

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


# ================ P-5..P-9b earlier gates, at the trees and histories they mean
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
gate_at_its_own_tree("P-9b", "phase-8)",
                     "docs/evidence/phase-8/check_phase8.py", "Phase 8")

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
    if len(ops) != 37:
        contract_defects.append(f"expected 37 declared operations, found {len(ops)}")
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
    f"vocabularies compared across schema, contract and code: {len(pairs) + 9}; "
    f"disagreements: {len(vocab_defects)}", vocab_defects)

# ================================== P-13 every dependency carries a justification
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
deps = set(re.findall(r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?[><=!]', pyproject))
justified = (ROOT / "docs/dependencies.md").read_text(encoding="utf-8")
unjustified = sorted(d for d in deps if f"`{d}`" not in justified)
add("P-13", "PASS" if not unjustified else "FAIL",
    f"{len(deps)} declared dependencies, each with a recorded reason and a rejected "
    f"alternative; unjustified: {unjustified or 'none'}. Phase 9 added none — "
    f"the real-model validation runs against a container, not a library.")

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

#: Everything schema 09 adds. Named once and used by the checks below, because
#: separate copies of the same list drift.
PHASE9_TABLES = ("required_context", "retrieved_context", "sample_citation",
                 "trajectory_step", "hallucination_finding",
                 "stage_attribution")
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
phase9 = [t for t in tables if t in PHASE9_TABLES]
plain_fks = []
for t in phase9:
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
    f"constraint; {len(phase9)} Phase 9 tables, every reference carrying the "
    f"tenant; missing: {missing_uq or 'none'}", missing_uq + plain_fks)

# ============================== P-18 the schema is the migration set, not a copy
mig = (ROOT / "src/clep/db/migrations.py").read_text(encoding="utf-8")
copies = [str(p.relative_to(ROOT)) for p in ROOT.glob("src/**/*.sql")]
add("P-18", "PASS" if "docs" in mig and "schema" in mig and not copies else "FAIL",
    f"migrations are applied from docs/data/schema/ with no second copy of the DDL; "
    f"copies found: {copies or 'none'}")

# ============= P-19a REQ-F-03-2: the split between computed and judged
#
# The failure this catches is a number that looks like a measurement. Word
# overlap named `groundedness` would pass every schema check, populate every
# report, and mean nothing — canonical §25 rejects claiming a metric no executed
# measurement produced, and this is where that would happen.
split_defects = []
try:
    from clep.evaluators import rag as _rag
    from clep.evaluators.sdk import EvaluatorRegistry

    registry = EvaluatorRegistry()
    _rag.register_rag_evaluators(registry)
    computed = {r.split("@")[0] for r in registry.keys()}
    judged = set(_rag.RAG_RUBRICS)

    if judged != {"context_relevance", "faithfulness", "groundedness",
                  "answer_relevance"}:
        split_defects.append(
            f"the judged four are not the four REQ-F-03-2 names: {sorted(judged)}")
    trespass = sorted(judged & computed)
    if trespass:
        split_defects.append(
            f"{trespass} is registered as a deterministic evaluator; a semantic "
            f"judgement computed by arithmetic is a metric nobody measured")
    for name in judged:
        if any(name in key for key in registry.keys()):
            split_defects.append(f"an evaluator's name contains {name!r}")
    if not computed >= {"retrieval_hit_rate", "citation_validity"}:
        split_defects.append(
            f"the deterministic side is missing: {sorted(computed)}")
except Exception as e:
    split_defects.append(f"{type(e).__name__}: {e}")
add("P-19a", "PASS" if not split_defects else "FAIL",
    f"REQ-F-03-2 divides along the REQ-F-08-6 line: retrieval and citations "
    f"computed, the four semantic judgements left to the ensemble; defects: "
    f"{len(split_defects)}", split_defects)

# ============= P-19b REQ-F-03-1: an unlabelled example is not a passing one
retrieval_defects = []
try:
    from decimal import Decimal

    from clep.evaluators.sdk import (RetrievedContext, SampleContext,
                                     run_evaluator)

    def sample(**over):
        base = dict(example_id="x", prompt="q", output="a",
                    integration_tier="full",
                    contexts=(RetrievedContext("c1", "alpha", 0),
                              RetrievedContext("c2", "beta", 1)))
        base.update(over)
        return SampleContext(**base)

    hit = registry.get("retrieval_hit_rate@1.0.0")
    present = registry.get("required_context_present@1.0.0")

    # The defect this check exists for: the first implementation computed the
    # hit rate over the passages that came BACK, so a required passage the
    # retriever missed could not appear in it and every sample scored 1.0.
    unlabelled = run_evaluator(hit, sample())
    if unlabelled.resolution != "abstained":
        retrieval_defects.append(
            f"an example with no required-context label resolved "
            f"{unlabelled.resolution} rather than abstaining; scoring it would "
            f"report the absence of a question as a good answer")
    partial = run_evaluator(hit, sample(required_context_ids=("c1", "c3")))
    if partial.score != Decimal("0.500000000"):
        retrieval_defects.append(
            f"a retrieval that missed one of two required passages scored "
            f"{partial.score}, not 0.5")
    if "c3" not in partial.detail:
        retrieval_defects.append("the missing passage is not named in the detail")
    missed = run_evaluator(present, sample(required_context_ids=("c9",)))
    if missed.score != 0:
        retrieval_defects.append("a missing required passage did not score zero")

    # A citation naming nothing retrieved is visible, in the type and the store.
    if sample(citations=("c1", "c9")).unresolved_citations != ("c9",):
        retrieval_defects.append("an unresolvable citation is not reported")
    if "fk_sample_citation__retrieved_context" not in sql:
        retrieval_defects.append(
            "the store lets a citation name a passage that was never retrieved")
    if not re.search(r"CREATE TABLE clep\.required_context", sql):
        retrieval_defects.append(
            "what retrieval was required is not recorded anywhere, so "
            "REQ-F-03-6 attribution can never be made")
except Exception as e:
    retrieval_defects.append(f"{type(e).__name__}: {e}")
add("P-19b", "PASS" if not retrieval_defects else "FAIL",
    f"REQ-F-03-1: required context is a property of the example, an unlabelled "
    f"example abstains, and a citation cannot name what was not retrieved; "
    f"defects: {len(retrieval_defects)}", retrieval_defects)

# ================== P-19c ADR-018: two orthogonal judgements, narrow parse
hallucination_defects = []
try:
    import inspect
    from decimal import Decimal

    from clep.rag import hallucination as _h

    class _Verdict:
        def __init__(self, value):
            self.verdict = Decimal(value) if value is not None else None

    def finding(support, contradiction, st="0.7", ct="0.5"):
        return _h.analyse_claim(
            "c", support=_Verdict(support), contradiction=_Verdict(contradiction),
            support_threshold=Decimal(st) if st else None,
            contradiction_threshold=Decimal(ct) if ct else None).finding

    # The requirement IS the distinction. A silent passage scores low on both,
    # which a signed single score cannot represent at all.
    if finding("0.9", "0.0") != _h.GROUNDED:
        hallucination_defects.append("a supported claim is not grounded")
    if finding("0.1", "0.0") != _h.UNSUPPORTED:
        hallucination_defects.append(
            "a claim the passage is silent on is not reported unsupported; the "
            "distinction REQ-F-03-3 asks for has collapsed")
    if finding("0.1", "0.9") != _h.CONTRADICTED:
        hallucination_defects.append("a denied claim is not reported contradicted")
    if finding("0.95", "0.9") != _h.CONTRADICTED:
        hallucination_defects.append(
            "contradiction does not outrank support; a claim that is partly "
            "supported and denied is being reported as grounded")
    # Thresholds unset, and an escalation, both refuse.
    if finding("0.9", "0.0", st=None) != _h.NOT_ANALYSABLE:
        hallucination_defects.append(
            "an unset threshold produced a finding; ADR-018 declines to invent one")
    if finding(None, "0.1") != _h.NOT_ANALYSABLE:
        hallucination_defects.append(
            "an escalated judgement was read as a low score, which answers a "
            "question that was explicitly deferred to a human")

    # And the reason ADR-018 exists: the parse stays narrow.
    source = inspect.getsource(_h)
    for widening in ("parse_reply", "re.compile", "GROUNDED_TOKEN"):
        if widening in source:
            hallucination_defects.append(
                f"{widening} appears in the analysis; the judge vocabulary is "
                f"being widened, which reopens the Phase 8 injection surface")
    if "not_analysable" not in ddl_enum("ck_hallucination_finding__finding"):
        hallucination_defects.append("the store cannot record an unanalysable claim")
    if not re.search(r"ck_hallucination_finding__reached_findings_show_their_working",
                     sql):
        hallucination_defects.append(
            "the store permits a classification with no scores or thresholds "
            "behind it, which nobody could re-derive")
except Exception as e:
    hallucination_defects.append(f"{type(e).__name__}: {e}")
add("P-19c", "PASS" if not hallucination_defects else "FAIL",
    f"ADR-018: unsupported and contradicted stay distinct, contradiction "
    f"outranks support, thresholds stay unset, and the judge's vocabulary is "
    f"not widened; defects: {len(hallucination_defects)}", hallucination_defects)

# ============ P-19d REQ-F-03-6: retrieval outranks generation, and silence wins
attribution_defects = []
try:
    from clep.rag.attribution import (GENERATION, NEITHER, NOT_ATTRIBUTABLE,
                                      RETRIEVAL, attribute)

    unlabelled = attribute(sample(), faithfulness=_Verdict("0.1"),
                           faithfulness_threshold=Decimal("0.7"))
    if unlabelled.stage != NOT_ATTRIBUTABLE:
        attribution_defects.append(
            "an unlabelled example was attributed to a stage; without the label "
            "a retriever that missed the evidence and a generator that ignored "
            "it are indistinguishable")
    missing = attribute(sample(required_context_ids=("c1", "c9")),
                        faithfulness=_Verdict("0.0"),
                        faithfulness_threshold=Decimal("0.7"))
    if missing.stage != RETRIEVAL:
        attribution_defects.append(
            "an unfaithful answer with a missing required passage was blamed on "
            "the generator, which sends someone to fix the wrong component")
    generated = attribute(sample(required_context_ids=("c1",)),
                          faithfulness=_Verdict("0.2"),
                          faithfulness_threshold=Decimal("0.7"))
    if generated.stage != GENERATION:
        attribution_defects.append(
            "complete evidence and an unfaithful answer was not attributed to "
            "the generator")
    clean = attribute(sample(required_context_ids=("c1",)),
                      faithfulness=_Verdict("0.9"),
                      faithfulness_threshold=Decimal("0.7"))
    if clean.stage != NEITHER:
        attribution_defects.append("a sample where nothing failed was blamed on "
                                   "something")
    escalated = attribute(sample(required_context_ids=("c1",)),
                          faithfulness=_Verdict(None),
                          faithfulness_threshold=Decimal("0.7"))
    if escalated.stage != NOT_ATTRIBUTABLE:
        attribution_defects.append("an escalated faithfulness judgement was read "
                                   "as a low score")
    for result in (unlabelled, missing, generated, clean, escalated):
        if not result.reason:
            attribution_defects.append("an attribution states no grounds")
    if not re.search(r"ck_stage_attribution__retrieval_names_what_was_missing", sql):
        attribution_defects.append(
            "the store permits a retrieval attribution that does not say what "
            "was missing, which is an assertion rather than a finding")
except Exception as e:
    attribution_defects.append(f"{type(e).__name__}: {e}")
add("P-19d", "PASS" if not attribution_defects else "FAIL",
    f"REQ-F-03-6: retrieval outranks generation, an unlabelled example is not "
    f"attributed, and every attribution states its grounds; defects: "
    f"{len(attribution_defects)}", attribution_defects)

# ========== P-19e REQ-F-04-5: a prefix is not a run, and is not scored as one
truncation_defects = []
try:
    from clep.evaluators.agent import register_agent_evaluators
    from clep.evaluators.trajectory import (MAX_TRAJECTORY_STEPS, ToolCall,
                                            Trajectory, TrajectoryError, ingest)

    agents = EvaluatorRegistry()
    register_agent_evaluators(agents)

    def agent_sample(trajectory, **over):
        base = dict(example_id="x", prompt="q", output="", expected="42",
                    integration_tier="full", agent_trajectory=trajectory)
        base.update(over)
        return SampleContext(**base)

    long_run = [ToolCall(i, "search", {"q": str(i)}) for i in range(9)]
    truncated = ingest(long_run, limit=3)
    if not truncated.truncated or len(truncated.steps) != 3:
        truncation_defects.append("ingest did not bound and mark the trajectory")
    try:
        Trajectory(steps=tuple(ToolCall(i, "a", {"i": i})
                               for i in range(MAX_TRAJECTORY_STEPS + 1)))
        truncation_defects.append(
            "a trajectory past the ingest bound can be constructed directly, "
            "which routes around the bound entirely")
    except TrajectoryError:
        pass

    # The questions a prefix cannot answer are refused...
    for name in ("task_success@1.0.0", "no_non_terminating_loop@1.0.0"):
        outcome = run_evaluator(agents.get(name), agent_sample(truncated))
        if outcome.resolution != "truncated":
            truncation_defects.append(
                f"{name} answered {outcome.resolution} on a truncated "
                f"trajectory; a prefix cannot show that a run completed or that "
                f"a loop ended")
        if outcome.score is not None:
            truncation_defects.append(f"{name} scored a truncated trajectory")
    # ...and the ones it can answer still are.
    looping = ingest([ToolCall(i, "search", {"q": "x"}) for i in range(9)], limit=4)
    loop = run_evaluator(agents.get("no_non_terminating_loop@1.0.0"),
                         agent_sample(looping))
    if loop.resolution != "scored" or loop.score != 0:
        truncation_defects.append(
            "a loop already visible inside the prefix was refused rather than "
            "reported; truncation should refuse only what it must")
    # Added by ALTER, because file 05 is applied and recorded by its SHA-256.
    # Looked for in the whole schema rather than in run_sample's CREATE TABLE
    # body, which is where the first version of this check looked and failed
    # against a correct schema.
    if not re.search(r"ALTER TABLE clep\.run_sample\s+ADD COLUMN "
                     r"trajectory_truncated", sql):
        truncation_defects.append(
            "truncation is not recorded on the sample, so nothing downstream "
            "can tell a prefix from a complete run")
except Exception as e:
    truncation_defects.append(f"{type(e).__name__}: {e}")
add("P-19e", "PASS" if not truncation_defects else "FAIL",
    f"REQ-F-04-5: the ingest bound cannot be routed around, a prefix is not "
    f"scored as a run, and what a prefix does show is still reported; defects: "
    f"{len(truncation_defects)}", truncation_defects)

# ======= P-19f REQ-F-04-2/3/4: computed where computable, and never combined
agent_defects = []
try:
    schemas = {"search": {"required": ["q"], "properties": {"q": {}}},
               "answer": {"required": ["text"], "properties": {"text": {}}}}
    good = ingest([ToolCall(0, "search", {"q": "x"}, "hit"),
                   ToolCall(1, "answer", {"text": "42"}, "ok")],
                  final_answer="42")

    # Abstention rather than a default score, everywhere the input is absent.
    for name, missing in (("tool_call_validity@1.0.0", {"tool_schemas": {}}),
                          ("tool_selection_correctness@1.0.0",
                           {"expected_tools": ()}),
                          ("task_success@1.0.0", {"expected": None})):
        fields = dict(tool_schemas=schemas, expected_tools=("search", "answer"))
        fields.update(missing)
        outcome = run_evaluator(agents.get(name), agent_sample(good, **fields))
        if outcome.resolution != "abstained":
            agent_defects.append(
                f"{name} produced {outcome.resolution} with its input absent "
                f"rather than abstaining")

    # Recall alone would reward an agent that calls everything.
    everything = ingest([ToolCall(0, "search", {"q": "x"}),
                         ToolCall(1, "spurious", {}),
                         ToolCall(2, "answer", {"text": "42"})])
    loose = run_evaluator(agents.get("tool_selection_correctness@1.0.0"),
                          agent_sample(everything, expected_tools=("search",
                                                                   "answer")))
    tight = run_evaluator(agents.get("tool_selection_correctness@1.0.0"),
                          agent_sample(good, expected_tools=("search", "answer")))
    if not loose.score < tight.score:
        agent_defects.append(
            "calling a tool the task did not need costs nothing; recall alone "
            "would give an agent that calls everything a perfect score")

    # Recovery is not demonstrated by never failing.
    clean = run_evaluator(agents.get("recovery_after_failure@1.0.0"),
                          agent_sample(good))
    if clean.resolution != "abstained":
        agent_defects.append(
            "an agent that never hit an error was scored for recovery, which "
            "rewards the absence of a test rather than passing one")

    # REQ-F-04-4: nothing combines route quality with answer quality.
    for combined in ("agent_score", "overall", "composite", "trajectory_quality"):
        if any(combined in key for key in agents.keys()):
            agent_defects.append(
                f"an evaluator named {combined!r} exists; REQ-F-04-4 requires "
                f"final-answer quality reported separately from the trajectory")
except Exception as e:
    agent_defects.append(f"{type(e).__name__}: {e}")
add("P-19f", "PASS" if not agent_defects else "FAIL",
    f"REQ-F-04-2/3/4: agent signals are computed, abstain when their input is "
    f"absent, penalise spurious tools, and are never combined into one number; "
    f"defects: {len(agent_defects)}", agent_defects)

# ============== P-19g the Phase 9 store: audit-class, and no second copy
store_defects = []
try:
    for table in ("hallucination_finding", "stage_attribution",
                  "retrieved_context", "trajectory_step"):
        if not re.search(rf"CREATE TRIGGER trg_{table}__immutable", sql):
            store_defects.append(f"{table} has no immutability trigger")
    for statement in re.findall(r"GRANT[^;]*;", sql, re.S):
        if not re.search(r"\bDELETE\b", statement):
            continue
        for table in PHASE9_TABLES:
            if re.search(rf"clep\.{table}\b", statement):
                store_defects.append(f"DELETE is granted on {table}")
    # Third-party content is referenced, never re-copied: erasure has to reach
    # it once, not twice.
    for table, column in (("retrieved_context", "content_digest"),
                          ("trajectory_step", "result_digest")):
        body = tables.get(table, "")
        if column not in body:
            store_defects.append(f"{table} does not carry {column}")
        if re.search(r"^\s{4}(text|content|passage|body)\s+text", body, re.M):
            store_defects.append(
                f"{table} stores the content itself; erasing the example would "
                f"leave a second copy behind (REQ-N-PRIV-4)")
    if "ON DELETE CASCADE" not in tables.get("retrieved_context", ""):
        store_defects.append(
            "erasing a run sample does not reach its retrieved passages")
except Exception as e:
    store_defects.append(f"{type(e).__name__}: {e}")
add("P-19g", "PASS" if not store_defects else "FAIL",
    f"the Phase 9 store: findings immutable, no DELETE granted, third-party "
    f"content held by digest and reached by cascade; defects: "
    f"{len(store_defects)}", store_defects)

# ========== P-19h real-model evidence is present, honest, and separated
real_defects = []
try:
    evidence = ROOT / "docs/evidence/phase-9/real-model-evidence.md"
    text = evidence.read_text(encoding="utf-8")
    output = ROOT / "docs/evidence/phase-9/real-model-output.json"
    rows = json.loads(output.read_text(encoding="utf-8"))

    if len(rows) < 5:
        real_defects.append(f"only {len(rows)} real judgements recorded")
    scoring = [v for r in rows for v in r["votes"] if v["resolution"] == "scored"]
    measured = [r for r in rows if r["disagreement_measured"]]
    agreed = [r for r in rows if r["state"] == "agreed"]
    escalated = [r for r in rows if r["state"] == "escalated"]
    if not scoring:
        real_defects.append("no real vote was scored")
    if not measured:
        real_defects.append(
            "no real judgement produced a measured disagreement, so the "
            "consensus measure itself was never exercised against a model")
    if not agreed or not escalated:
        real_defects.append(
            "the real run did not produce both an agreement and an escalation, "
            "so the branch that decides between them is unexercised")
    # The evidence must state its gaps rather than imply completeness.
    for required in ("Evidence gaps", "Not exercised", "No credential"):
        if required not in text:
            real_defects.append(f"the evidence does not state: {required!r}")
    # And it must not claim what it did not do.
    for overclaim in ("fully validated", "production-ready", "proves the judges"):
        if overclaim in text.lower():
            real_defects.append(f"the evidence overclaims: {overclaim!r}")
    runner = (ROOT / "docs/evidence/phase-9/real_model_run.py").read_text("utf-8")
    if "REFUSING" not in runner:
        real_defects.append(
            "the runner has no refusal path; a run against an unreachable model "
            "must be recorded as not having happened rather than falling back")
    for fallback in ("StubProvider", "FakeAdapter", "if not reachable: return _fake"):
        if fallback in runner:
            real_defects.append(f"the runner can fall back to {fallback}")
except FileNotFoundError as e:
    real_defects.append(f"real-model evidence missing: {e}")
except Exception as e:
    real_defects.append(f"{type(e).__name__}: {e}")
add("P-19h", "PASS" if not real_defects else "FAIL",
    f"real-model evidence: recorded, exercising both agreement and escalation, "
    f"stating its gaps, with no fallback to a stub; defects: {len(real_defects)}",
    real_defects)

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
                       ("src/clep/cli/**", "CI/CD command line (Phase 10)"),
                       ("src/clep/dashboards/**", "dashboards (Phase 11)"),
                       ("src/clep/rbac/**", "enterprise RBAC (Phase 12)"),
                       ("src/clep/telemetry/**", "observability (Phase 13)")):
    hits = [str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)]
    if hits:
        later_phase.append(f"{label}: {hits[:2]}")
add("P-25", "PASS" if not later_phase else "FAIL",
    f"Phase 10+ artifact classes checked: 6; artifacts Phase 9 must not "
    f"contain: {len(later_phase)}", later_phase)

# ============ P-26 every earlier gate is reachable from this one, by derivation
edges = {}
all_validators = sorted(
    p.relative_to(ROOT).as_posix()
    for p in ROOT.glob("docs/evidence/**/check_*.py"))
for path in all_validators:
    body = (ROOT / path).read_text(encoding="utf-8")
    edges[path] = {m for m in re.findall(r"docs/evidence/[\w.\-]+/check_\w+\.py", body)
                   if m != path}
root = "docs/evidence/phase-9/check_phase9.py"
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
