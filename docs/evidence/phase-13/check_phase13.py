"""Phase 13 comprehensive validation, with regression over every earlier phase.

Phase 13 is the phase that makes the platform observable, and the failures that
matter are all failures of *evidence about evidence*. A metric class declared and
never emitted. A correlation chain that propagates in unit tests and has a gap in
the middle. A label that carries a run identifier in one code path nobody reads.
A log line that carries a judge rationale on the one branch a test never took. An
SLO target that was chosen and then justified. Every one of those looks like
working observability, and most of them look like working observability with
tests.

So the checks here are executed. Metric classes are counted from what a driven
platform emitted, not from what the catalogue declares. Cardinality is counted
after varying the thing that would break it. The correlation chain is recovered
from the database after the scope has closed. The adapter-excluded build is
actually built. The three places where inspection is genuinely the right method —
that no vendor telemetry package is imported by the core, that `pyproject.toml`
declares telemetry as an extra and not as a dependency, and that two copies of
one vocabulary agree — say so and say why.

The frame is spliced from the Phase 12 validator rather than copied, with one
change: `gate_at_its_own_tree` now contains its nested gate's temporary
directory. Earlier phases' gates leak workspaces they cannot be edited to stop
leaking, and a closure run starts a chain of them.

Usage: python docs/evidence/phase-13/check_phase13.py <repo_root>
Exits non-zero on any FAIL.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(ROOT / "docs" / "evidence" / "tooling"))
import workspace as W  # noqa: E402

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

#: Carried from Phase 12 unchanged. Each was established as synthetic and is
#: retained because removing it means rewriting published history.
DISCLOSED_BLOBS: dict[str, str] = {}


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


def pytest_run(target, extra=(), timeout=2400):
    return run([PY, "-m", "pytest", target, "-q", "-p", "no:warnings",
                "--color=no", "--no-cov", *extra], timeout=timeout)


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


def read(rel: str) -> str:
    p = ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ===================================================== P-1 the suite executes
code, out = run([PY, "-m", "pytest", "-q", "-p", "no:warnings", "--color=no",
                 "--cov", "--cov-report=term"], timeout=3600)
passed = re.search(r"(\d+) passed", out)
skipped = re.search(r"(\d+) skipped", out)
cov = re.search(r"Total coverage: ([\d.]+)%", out)
gate = re.search(r"Required test coverage of ([\d.]+)% reached", out)
add("P-1", "PASS" if code == 0 else "FAIL",
    f"test suite: exit {code}; {passed.group(1) if passed else '?'} passed; "
    f"{skipped.group(1) if skipped else '0'} skipped; coverage "
    f"{cov.group(1) if cov else '?'}% against a "
    f"{gate.group(1) if gate else '?'}% gate",
    [] if code == 0 else out.strip().splitlines()[-8:])

# =================================================== P-2 schema conformance
code, out = run([PY, "docs/evidence/phase-4/check_schema_conformance.py", "."])
tables = re.search(r"(\d+) table\(s\) parsed", out)
add("P-2", "PASS" if code == 0 else "FAIL",
    f"schema conformance: exit {code}; "
    f"{tables.group(1) if tables else '?'} tables including the Phase 13 "
    f"correlation column and its indexes")

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


# ====================== P-5 the preceding gate, at its own tree and history
def gate_at_its_own_tree(cid, grep, script, label, timeout=2400):
    """Re-run an earlier gate against the history it was written for.

    Three conditions carried from Phase 12, each learned by getting it wrong: an
    isolated clone rather than a worktree, `main` created at the target commit
    because a gate that searches history needs one, and PYTHONPATH at the
    clone's `src` because the package is installed editable against the working
    tree.

    One condition added here. The nested gate's temporary directory is contained
    (`docs/evidence/tooling/workspace.py`), because every earlier gate discards
    its own clone with `ignore_errors=True` and leaks the read-only packfiles —
    936 workspaces, 362 MB, measured at the start of this phase. Those gates
    cannot usefully be edited: each runs from its own committed tree. What can be
    changed is the environment they run in, so `TMPDIR`/`TEMP`/`TMP` point inside
    a directory this function removes, and the whole nested chain is reclaimed
    at any depth.
    """
    sha = git("rev-list", "-1", f"--grep={grep}", "HEAD").strip()
    if not sha:
        add(cid, "FAIL", f"could not locate the {label} commit")
        return
    system_tmp = Path(tempfile.gettempdir()).resolve()
    before_leak = {p.name for p in system_tmp.iterdir()
                   if p.name.startswith("clep-gate-")}
    with W.workspace("clep-gate13-") as work:
        tree = work / "tree"
        contained = work / "tmp"
        subprocess.run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT),
                        str(tree)], capture_output=True, text=True)
        subprocess.run(["git", "checkout", "--quiet", "--detach", sha],
                       cwd=str(tree), capture_output=True, text=True)
        refs = subprocess.run(
            ["git", "for-each-ref", "--format=%(refname)", "refs/heads",
             "refs/remotes"],
            cwd=str(tree), capture_output=True, text=True).stdout.split()
        for ref in refs:
            subprocess.run(["git", "update-ref", "-d", ref], cwd=str(tree),
                           capture_output=True, text=True)
        subprocess.run(["git", "branch", "--quiet", "main", sha], cwd=str(tree),
                       capture_output=True, text=True)
        for cmd in (["remote", "remove", "origin"],
                    ["reflog", "expire", "--expire=now", "--all"],
                    ["gc", "--prune=now", "--quiet"]):
            subprocess.run(["git", *cmd], cwd=str(tree), capture_output=True,
                           text=True)
        remaining = subprocess.run(["git", "rev-list", "--all", "--count"],
                                   cwd=str(tree), capture_output=True,
                                   text=True).stdout.strip()
        docx = next(ROOT.glob("*.docx"), None)
        if docx:
            import shutil
            shutil.copy2(docx, tree / docx.name)
        env = W.contained_env(contained,
                              dict(os.environ, PYTHONPATH=str(tree / "src")))
        c, o = run([PY, str(tree / script), str(tree)], cwd=tree, env=env,
                   timeout=timeout)
        summary = re.search(r"SUMMARY: (.*)", o)
        fails = [l.strip()[:150] for l in o.splitlines() if l.startswith("[FAIL")]
        contained_count = len(list(contained.rglob("*"))) if contained.exists() else 0
    after_leak = {p.name for p in system_tmp.iterdir()
                  if p.name.startswith("clep-gate-")}
    leaked = sorted(after_leak - before_leak)
    ok = c == 0 and not leaked
    add(cid, "PASS" if ok else "FAIL",
        f"{label} gate re-evaluated against its own history ({sha[:8]}), in an "
        f"isolated clone pruned to {remaining} reachable commit(s): exit {c} "
        f"{summary.group(1) if summary else ''}; nested workspaces contained "
        f"({contained_count} path(s) inside the contained root, removed with it); "
        f"system temporary directory gained {len(leaked)} clep-gate-* "
        f"directory(ies)",
        fails + [f"leaked: {n}" for n in leaked])


gate_at_its_own_tree("P-5", "phase-12)",
                     "docs/evidence/phase-12/check_phase12.py", "Phase 12",
                     timeout=21600)

# ============================ P-6 the correlation chain, recovered end to end
code, out = pytest_run("tests/test_correlation_chain.py", ("-s",), timeout=1200)
present = set(re.findall(r"PRESENT\s+(\w+)", out))
absent = set(re.findall(r"ABSENT\s+(\w+)", out))
expected_present = {"run", "work_unit", "model_call", "evaluator_invocation",
                    "judge_invocation", "gate_decision", "audit_event"}
chain_ok = code == 0 and present == expected_present and absent == {"artifact"}
add("P-6", "PASS" if chain_ok else "FAIL",
    f"REQ-N-OBS-1: one identifier recovered from the store after the scope "
    f"closed, at {len(present)} hop(s): {sorted(present)}; absent: "
    f"{sorted(absent) or 'none'} (the artifact hop has no writer — D-7, D-3, "
    f"Phase 14); exit {code}",
    [] if chain_ok else out.strip().splitlines()[-8:])

# ============================= P-7 the nine metric classes, proven by emission
code, out = pytest_run("tests/test_metric_emission.py", timeout=900)
add("P-7", "PASS" if code == 0 else "FAIL",
    f"REQ-N-OBS-2 and REQ-N-OBS-4: nine classes counted from what a driven "
    f"platform emitted, and series counted after varying tenants, runs and "
    f"correlations: exit {code}",
    [] if code == 0 else out.strip().splitlines()[-8:])

# ================== P-8 cardinality is bounded in the core, not in a backend
sys.path.insert(0, str(ROOT / "src"))
card_defects = []
try:
    for mod in list(sys.modules):
        if mod.startswith("clep"):
            del sys.modules[mod]
    from clep.telemetry import CATALOGUE, METRIC_CLASSES, CardinalityError, Telemetry
    if CATALOGUE.missing_classes():
        card_defects.append(f"classes with no declared metric: "
                            f"{CATALOGUE.missing_classes()}")
    if len(METRIC_CLASSES) != 9:
        card_defects.append(f"{len(METRIC_CLASSES)} metric classes, expected 9")
    ceiling = CATALOGUE.max_series()
    forbidden = {"organization_id", "org", "tenant", "tenant_id", "project_id",
                 "run_id", "sample_id", "candidate_id", "correlation_id",
                 "principal_id", "user", "evaluator_version_id"}
    for spec in CATALOGUE:
        overlap = set(spec.labels) & forbidden
        if overlap:
            card_defects.append(f"{spec.name} declares identifier label(s) {overlap}")
    # The refusal itself, exercised rather than read.
    t = Telemetry()
    try:
        t.observe("clep_run_terminal_total", 1, completeness="complete",
                  run_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
        card_defects.append("an undeclared identifier label was accepted")
    except CardinalityError:
        pass
except Exception as e:  # noqa: BLE001
    card_defects.append(f"{type(e).__name__}: {e}")
    ceiling = -1
add("P-8", "PASS" if not card_defects else "FAIL",
    f"REQ-N-OBS-4 in the core: {len(list(CATALOGUE)) if not card_defects else '?'} "
    f"declared metrics across 9 classes, whole-catalogue series ceiling "
    f"{ceiling}, computable because no label is unbounded; the refusal fires "
    f"against a run identifier", card_defects)

# ======================== P-9 the log surface, driven with hostile values
code, out = pytest_run("tests/test_log_redaction.py", timeout=600)
n = re.search(r"(\d+) passed", out)
add("P-9", "PASS" if code == 0 else "FAIL",
    f"REQ-N-SEC-5 and REQ-N-PRIV-2: {n.group(1) if n else '?'} adversarial "
    f"cases — credentials in exception messages, nested containers, helpful "
    f"__str__, the event name itself; every DS class the taxonomy forbids on "
    f"the log surface; capture explicit, audited and time-bounded: exit {code}",
    [] if code == 0 else out.strip().splitlines()[-8:])

# ============================= P-10 the adapter-excluded build, actually built
code, out = run([PY, "docs/evidence/phase-13/prove_adapter_excluded.py", "."],
                timeout=3600)
summary = re.search(r"SUMMARY: (\S+)", out)
add("P-10", "PASS" if code == 0 else "FAIL",
    f"ADR-009 rule 3 and REQ-N-OBS-3: a fresh environment, `pip install .` with "
    f"no extras, no telemetry distribution present, core imports, application "
    f"constructs, evaluation runs, cardinality still refuses, OTLP refuses "
    f"clearly: {summary.group(1) if summary else '?'}, exit {code}",
    [] if code == 0 else [l for l in out.splitlines() if l.startswith("[FAIL")])

# ============ P-11 no vendor telemetry package is imported by core or domain
vendor = re.compile(r"^\s*(?:from|import)\s+(opentelemetry|prometheus_client|"
                    r"datadog|newrelic|langsmith|phoenix|arize)\b", re.M)
vendor_hits = []
for p in sorted((ROOT / "src" / "clep").rglob("*.py")):
    rel = p.relative_to(ROOT).as_posix()
    body = p.read_text(encoding="utf-8")
    for m in vendor.finditer(body):
        line = body[:m.start()].count("\n") + 1
        # Inside a function body is permitted, and is the mechanism ADR-022
        # rule 2 relies on; at module scope it makes the extra mandatory.
        indented = m.group(0).startswith((" ", "\t"))
        if not indented or "backends/" not in rel:
            vendor_hits.append(f"{rel}:{line}: {m.group(1)} at "
                               f"{'module scope' if not indented else 'function scope'}")
add("P-11", "PASS" if not vendor_hits else "FAIL",
    f"ADR-022 rule 1: no vendor telemetry import outside "
    f"src/clep/telemetry/backends/, and none at module scope even there — "
    f"static inspection is the correct method here, because the property is "
    f"the absence of an import and executing it would prove less",
    vendor_hits)

# ================= P-12 telemetry is an optional extra, not a dependency
pyproject = read("pyproject.toml")
runtime_block = re.search(r"^dependencies = \[(.*?)\]", pyproject, re.S | re.M)
extras_block = re.search(r"^otel = \[(.*?)\]", pyproject, re.S | re.M)
extra_defects = []
if runtime_block and re.search(r"opentelemetry|prometheus", runtime_block.group(1)):
    extra_defects.append("a telemetry distribution is a runtime dependency; "
                         "REQ-N-OBS-3's build would then be the special one")
if not extras_block:
    extra_defects.append("no `otel` extra is declared")
elif not re.search(r"opentelemetry-sdk", extras_block.group(1)):
    extra_defects.append("the `otel` extra does not name opentelemetry-sdk")
runtime_count = len(re.findall(r'"[^"]+"', runtime_block.group(1))) if runtime_block else 0
add("P-12", "PASS" if not extra_defects else "FAIL",
    f"ADR-022 rule 2: {runtime_count} runtime dependency(ies), none of them "
    f"telemetry; the `otel` extra is declared separately, so the default build "
    f"is the adapter-excluded one", extra_defects)

# ================ P-13 one vocabulary, not two: catalogue against the schema
vocab_defects = []
try:
    from clep.evaluators.sdk import RESOLUTIONS as SDK_RESOLUTIONS
    from clep.judges.consensus import ESCALATION_REASONS as SDK_REASONS
    from clep.judges.consensus import STATES as SDK_STATES
    from clep.providers.port import TAXONOMY
    from clep.telemetry import catalog as C

    schema_text = "\n".join(
        (ROOT / "docs" / "data" / "schema" / f).read_text(encoding="utf-8")
        for f in sorted(os.listdir(ROOT / "docs" / "data" / "schema"))
        if f.endswith(".sql"))

    def schema_enum(constraint: str) -> set[str]:
        m = re.search(constraint + r".*?IN\s*\((.*?)\)", schema_text, re.S)
        return set(re.findall(r"'([a-z_]+)'", m.group(1))) if m else set()

    pairs = [
        ("resolutions", set(C.RESOLUTIONS), set(SDK_RESOLUTIONS)),
        ("consensus states", set(C.CONSENSUS_STATES), set(SDK_STATES)),
        ("escalation reasons", set(C.ESCALATION_REASONS), set(SDK_REASONS)),
        ("provider outcomes", set(C.PROVIDER_OUTCOMES) - {"ok"},
         {f.kind for f in TAXONOMY}),
        ("completeness", set(C.COMPLETENESS),
         schema_enum("ck_run__completeness")),
        ("execution states", set(C.EXECUTION_STATES),
         schema_enum("ck_run__execution_state")),
        ("gate verdicts", set(C.GATE_VERDICTS),
         schema_enum("ck_gate_decision__evaluated_outcome")),
        ("invocation outcomes", set(C.INVOCATION_OUTCOMES),
         schema_enum("ck_evaluator_invocation__outcome")),
        ("endpoint kinds", set(C.ENDPOINT_KINDS),
         schema_enum("ck_provider_endpoint__kind")),
    ]
    for name, mine, theirs in pairs:
        if not theirs:
            vocab_defects.append(f"{name}: could not read the other side; a "
                                 f"comparison against nothing always agrees")
        elif mine != theirs:
            vocab_defects.append(
                f"{name}: catalogue {sorted(mine)} != source {sorted(theirs)}")
except Exception as e:  # noqa: BLE001
    vocab_defects.append(f"{type(e).__name__}: {e}")
add("P-13", "PASS" if not vocab_defects else "FAIL",
    f"the catalogue restates 9 vocabularies that already exist in the schema "
    f"and in domain modules, to avoid an import cycle; each is compared as a "
    f"set against its source, so a member added on one side and not the other "
    f"fails here rather than silently refusing a legal value", vocab_defects)

# =========================== P-14 cost reconciliation, and what it cannot do
code, out = pytest_run("tests/test_cost_reconciliation.py", timeout=900)
add("P-14", "PASS" if code == 0 else "FAIL",
    f"REQ-N-COST-1 and REQ-N-COST-3: every attributed amount recomputed from "
    f"recorded usage; a deliberately wrong price required to disagree; an "
    f"unpriced model reported rather than counted as agreeing; a plan with no "
    f"history not estimated: exit {code}",
    [] if code == 0 else out.strip().splitlines()[-8:])

# ================================ P-15 budget exhaustion, by fault injection
code, out = pytest_run("tests/test_orchestration.py", ("-k", "exhausted"),
                       timeout=900)
n = re.search(r"(\d+) passed", out)
add("P-15", "PASS" if code == 0 and n and int(n.group(1)) > 0 else "FAIL",
    f"REQ-N-COST-2: budget exhaustion is a defined terminal outcome reached by "
    f"driving a run past its limit, not an incident: "
    f"{n.group(1) if n else '0'} case(s), exit {code}")

# ===================== P-16 the SLO measurement ran, and the targets cite it
code, out = pytest_run("tests/test_slo_measurement.py", ("-s",), timeout=900)
targets = read("docs/evidence/phase-13/slo-targets.md")
raw = read("docs/evidence/phase-13/slo-measurement.txt")
slo_defects = []
if code != 0:
    slo_defects.append(f"the measurement did not run: exit {code}")
if not raw.strip():
    slo_defects.append("no raw measurement output is committed")
unset = targets.count("TARGET NOT YET SET")
if unset < 3:
    slo_defects.append(f"{unset} indicator(s) recorded as TARGET NOT YET SET; "
                       f"three are blocked and must say so")
for blocker in ("hosted provider", "production traffic"):
    if blocker not in targets:
        slo_defects.append(f"a blocker is not named: {blocker!r}")
# Every published number must appear in the committed raw output.
for number in re.findall(r"\| (?:p50|p95|Maximum) \| ([\d.]+) ms \|", targets):
    if number not in raw:
        slo_defects.append(f"the published figure {number} ms is not in the "
                           f"committed measurement output")
add("P-16", "PASS" if not slo_defects else "FAIL",
    f"REQ-N-REL-5 and ADR-023: the measurement executed (exit {code}); every "
    f"published figure appears in the committed raw output; {unset} indicator(s) "
    f"carry TARGET NOT YET SET with a named blocker", slo_defects)

# ================== P-17 the operational surface, and the boundary it holds
code, out = pytest_run("tests/test_ops_surface.py", timeout=600)
add("P-17", "PASS" if code == 0 else "FAIL",
    f"ADR-024: an unguarded route on the tenant application still refuses to "
    f"start; the operational endpoints are absent from the tenant contract; "
    f"liveness consults no dependency; 100 correlations render as one series; "
    f"no tenant identity reaches /metrics, /ready or /health: exit {code}",
    [] if code == 0 else out.strip().splitlines()[-8:])

# ============ P-18 the tenant route guard is unchanged and has no exemption
guard = read("src/clep/api/app.py")
guard_defects = []
if "_assert_every_route_is_guarded(app)" not in guard:
    guard_defects.append("create_app no longer asserts every route is guarded")
if re.search(r"(?i)(exempt|allowlist|allow_list|skip_guard|unguarded_ok)", guard):
    guard_defects.append("an exemption mechanism appears in the tenant application")
for path in ('"/metrics"', '"/health"', '"/ready"'):
    if f"@app.get({path})" in guard:
        guard_defects.append(f"{path} is registered on the tenant application")
add("P-18", "PASS" if not guard_defects else "FAIL",
    "ADR-020 rule 6 survives Phase 13: the import-time assertion is still "
    "called, no exemption mechanism exists, and no operational endpoint was "
    "added to the tenant application", guard_defects)

# ===================== P-19 the three ADRs exist, are accepted, and are cited
adr_defects = []
for number, slug in ((22, "telemetry-port"), (23, "slo-derivation"),
                     (24, "operational-surface")):
    body = read(f"docs/adr/ADR-0{number}-{slug}.md")
    if not body:
        adr_defects.append(f"ADR-0{number} is missing")
        continue
    if "**Accepted**" not in body:
        adr_defects.append(f"ADR-0{number} is not Accepted")
    if "## Alternatives considered" not in body:
        adr_defects.append(f"ADR-0{number} records no rejected alternative")
    if f"ADR-0{number}" not in read("docs/adr/README.md"):
        adr_defects.append(f"ADR-0{number} is not in the index")
if "**Accepted**" not in read("docs/adr/ADR-009-observability-core.md"):
    adr_defects.append("ADR-009 is no longer Accepted; it must be implemented, "
                       "not amended")
add("P-19", "PASS" if not adr_defects else "FAIL",
    "ADR-022, ADR-023 and ADR-024 are Accepted, each records what it rejected, "
    "each is indexed, and ADR-009 is unchanged", adr_defects)

# ==================== P-20 the workspace cleanup, and the leak it closed
code, out = run([PY, "docs/evidence/phase-13/prove_workspace_cleanup.py", "."],
                timeout=900)
summary = re.search(r"SUMMARY: (\S+)", out)
add("P-20", "PASS" if code == 0 else "FAIL",
    f"the validator's own workspace hygiene: the original defect reproduced "
    f"against the original code (W-1), the fix removes the same tree, a "
    f"transient handle is ridden out, a permanent one is reported rather than "
    f"discarded, containment holds two process levels down, and a junction into "
    f"the repository is refused: {summary.group(1) if summary else '?'}, "
    f"exit {code}",
    [] if code == 0 else [l for l in out.splitlines() if l.startswith("[FAIL")])

# ========================= P-21 tracked debt is carried, and D-7 is recorded
debt = read("docs/architecture/tracked-debt.md")
debt_defects = []
entries = dict(re.findall(r"^## (D-\d+) — (.+)$", debt, re.M))
for required in ("D-3", "D-4", "D-5", "D-6", "D-7"):
    if required not in entries:
        debt_defects.append(f"{required} is not a register entry")
for entry in entries:
    section = debt.split(f"## {entry} ")[1].split("\n## ")[0]
    for field in ("Owning phase", "Status"):
        if field not in section:
            debt_defects.append(f"{entry} does not record its {field}")
d7 = debt.split("## D-7 ")[1] if "## D-7 " in debt else ""
if "REQ-N-OBS-1" not in d7 or "Phase 14" not in d7:
    debt_defects.append("D-7 does not name REQ-N-OBS-1 and its owning phase")
add("P-21", "PASS" if not debt_defects else "FAIL",
    f"{len(entries)} debt entries, each naming its owner and status; D-7 "
    f"records that REQ-N-OBS-1's artifact hop has no writer and is Phase 14's, "
    f"alongside the still-open D-3, D-4, D-5 and D-6", debt_defects)

# ========================================= P-22 no risk closed by assertion
risk_defects = []
strategy = read("docs/architecture/observability-strategy.md")
if "TARGET NOT YET SET" not in strategy:
    risk_defects.append("observability-strategy.md §5 claims every target is set")
threat = read("docs/architecture/threat-model.md")
if "SR-7" not in threat:
    risk_defects.append("SR-7 has left the threat model")
catalog_body = read("src/clep/telemetry/catalog.py")
panel_body = read("src/clep/judges/panel.py")
if "calibrat" not in panel_body.lower():
    risk_defects.append("the judge panel does not state that emitting "
                        "calibration telemetry is not calibration")
add("P-22", "PASS" if not risk_defects else "FAIL",
    "no carried risk is closed by this phase asserting it: three SLO targets "
    "remain unset with blockers, SR-7 remains in the threat model, and judge "
    "calibration is explicitly not claimed where the telemetry is emitted",
    risk_defects)

# ============================================================ P-23 secrets
sec, nfiles, nbin = [], 0, 0
for p, rel in text_files(ROOT):
    nfiles += 1
    try:
        text = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        nbin += 1
        continue
    for rx, label in SECRETS:
        for m in re.finditer(rx, text):
            sec.append(f"{rel.as_posix()}:{text[:m.start()].count(chr(10)) + 1}: {label}")
blob_sec, disclosed = [], []
for line in git("rev-list", "--objects", "--all").splitlines():
    parts = line.split(" ", 1)
    if len(parts) != 2 or not parts[1].endswith((".py", ".md", ".sql", ".toml",
                                                 ".json", ".yml", ".yaml", ".txt")):
        continue
    body = git("cat-file", "-p", parts[0])
    for rx, label in SECRETS:
        if re.search(rx, body):
            if parts[0] in DISCLOSED_BLOBS:
                disclosed.append(f"{parts[1]}: {DISCLOSED_BLOBS[parts[0]]}")
            else:
                blob_sec.append(f"{parts[1]}: {label}")
add("P-23", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} undisclosed match(es). The Phase 13 "
    f"redaction tests contain credential-shaped inputs by necessity and assemble "
    f"every one from parts, with a test that each still matches the detector",
    sec + blob_sec + sorted(set(disclosed)))

# ======================================================== P-24 attribution
att = []
for p, rel in text_files(ROOT):
    try:
        t_ = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for rx in ATT:
        if re.search(rx, t_, re.I):
            att.append(f"{rel.as_posix()}: {rx}")
msgs = git("log", "--all", "--format=%B%an%ae%cn%ce")
msg_hits = [rx for rx in ATT if re.search(rx, msgs, re.I)]
add("P-24", "PASS" if not att and not msg_hits else "FAIL",
    f"governed scope: {len(att)} file match(es), {len(msg_hits)} history match(es)",
    att + msg_hits)

# ======================================================= P-25 git identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()) - {""})
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()) - {""})
add("P-25", "PASS" if len(authors) == 1 and len(committers) == 1 else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ================================================== P-26 canonical document
docx = list(ROOT.glob("*.docx"))
tracked = [d for d in docx if git("ls-files", d.name).strip()]
ignored = all(git("check-ignore", d.name).strip() for d in docx) if docx else False
refs = [r for r in git("for-each-ref", "--format=%(refname)").splitlines() if r]
published = []
for ref in refs:
    for line in git("ls-tree", "-r", "--name-only", ref).splitlines():
        if line.endswith(".docx"):
            published.append(f"{ref}: {line}")
ok = bool(docx) and not tracked and ignored and not published
add("P-26", "PASS" if ok else "FAIL",
    f"canonical document local={bool(docx)} tracked={bool(tracked)} "
    f"ignored={ignored} refs_scanned={len(refs)} "
    f"reachable_from_any_ref={len(published)}", published)

# ============================================================ P-27 hygiene
tracked_files = [f for f in git("ls-files").splitlines() if f]
dirty = [l for l in git("status", "--porcelain").splitlines() if l]
strays = [f for f in tracked_files
          if re.search(r"(\.orig|\.rej|\.bak|~|\.DS_Store|Thumbs\.db)$", f)]
add("P-27", "PASS" if not dirty and not strays else "FAIL",
    f"{len(tracked_files)} tracked file(s); stray: {len(strays)}; "
    f"clean tree: {not dirty}", dirty[:6] + strays)

# =========================================== P-28 phase boundary not overrun
later_phase = []
for pattern, label in (("docs/**/ADR-025*.md", "ADRs beyond the recorded set"),
                       ("docs/**/ADR-02[6-9]*.md", "ADRs beyond the recorded set"),
                       ("docs/data/schema/14-*.sql", "Phase 14 schema"),
                       ("docs/evidence/phase-14/**", "Phase 14 evidence"),
                       ("docs/evidence/phase-15/**", "Phase 15 evidence"),
                       ("infra/**", "deployment (Phase 14)"),
                       ("Dockerfile", "deployment (Phase 14)"),
                       ("README.md", "the final README (Phase 15)")):
    hits = [str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)]
    if hits:
        later_phase.append(f"{label}: {hits[:2]}")
add("P-28", "PASS" if not later_phase else "FAIL",
    f"Phase 14+ artifact classes checked: 8; artifacts Phase 13 must not "
    f"contain: {len(later_phase)}", later_phase)

# ============ P-29 every earlier gate is reachable from this one, by derivation
edges = {}
all_validators = sorted(
    p.relative_to(ROOT).as_posix()
    for p in ROOT.glob("docs/evidence/**/check_*.py"))
for path in all_validators:
    body = (ROOT / path).read_text(encoding="utf-8")
    edges[path] = {m for m in re.findall(r"docs/evidence/[\w.\-]+/check_\w+\.py", body)
                   if m != path}
this = "docs/evidence/phase-13/check_phase13.py"
reachable, frontier = {this}, [this]
while frontier:
    current = frontier.pop()
    for target in edges.get(current, ()):
        if target not in reachable:
            reachable.add(target)
            frontier.append(target)
unreachable = sorted(set(all_validators) - reachable)
missing_files = sorted({t for targets in edges.values() for t in targets}
                       - set(all_validators))
add("P-29", "PASS" if not unreachable and not missing_files else "FAIL",
    f"{len(all_validators)} validators in the repository, {len(reachable)} "
    f"reachable from this gate by traversing the invocation paths each one "
    f"actually contains; orphaned: {unreachable or 'none'}",
    unreachable + [f"invoked but absent: {m}" for m in missing_files])

# ==================================================== P-30 dependency scan
code, out = run([PY, "docs/evidence/phase-12/dependency_scan.py", "."],
                timeout=900)
add("P-30", "PASS" if code == 0 else "FAIL",
    f"REQ-N-SEC-7: the advisory database queried for the versions actually "
    f"installed, failing closed when unreachable: exit {code}",
    [] if code == 0 else out.strip().splitlines()[-6:])

# ===================================================================== done
passed_n = sum(1 for r in results if r["status"] == "PASS")
print(f"\nSUMMARY: {passed_n}/{len(results)} PASS")
failed = [r["id"] for r in results if r["status"] != "PASS"]
if failed:
    print(f"FAILED: {', '.join(failed)}")
raise SystemExit(0 if not failed else 1)
