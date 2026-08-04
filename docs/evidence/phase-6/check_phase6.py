"""Phase 6 comprehensive validation, with regression over every earlier phase.

Phase 6 delivers the registry and the reproducible experiment model, so the
checks that matter most are the ones that would catch a *silent* failure: an
identity digest that is stable for the wrong reason, a cache key that omits an
input, a version that can still be edited after a run depended on it. None of
those announce themselves; each produces results that look entirely plausible.

Earlier phase gates are re-evaluated against their OWN trees, in isolated clones
pruned to their own history, because a phase-boundary assertion becomes
historical the moment the next phase starts — and because a worktree shares the
object database, so a blob scan run in one sees the future and reports it as a
failure.

Usage: python docs/evidence/phase-6/check_phase6.py <repo_root>
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


def run(cmd, cwd=None, timeout=1800, env=None):
    p = subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout,
                       env=env)
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
    f"Phase 6 registry and experiment tables")

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


# ==================== P-5..P-7 earlier gates, at the trees and histories they mean
def gate_at_its_own_tree(cid, grep, script, label):
    """Re-run an earlier gate against the history it was written for.

    Phase 5 established that an isolated clone is required rather than a
    worktree, because a worktree shares the object database and a blob scan run
    in one sees commits made after it. Phase 6 found two further conditions,
    both by re-running the *Phase 5* gate — the first earlier gate that is
    itself history-aware and itself runs the test suite.

    `main` is RESET to the target commit rather than deleted. Phase 5's version
    deleted every ref, which pruned correctly but left the re-run gate with no
    `main` to search: its own regression checks look for earlier commits with
    `rev-list --grep ... main`, and reported that it could not find them. A
    single branch pointing at the target keeps history reachable up to that
    commit and no further, which is what the prune is for in the first place.

    PYTHONPATH points at the clone's `src`. The package is installed in editable
    mode against the working tree, so without this the clone's tests import the
    *current* implementation — and the Phase 5 gate duly reported its own suite
    failing against Phase 6 code, which is not a regression, it is a
    misconfiguration reporting one.
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
# "phase-5)" matches both the delivery commit and the finalization commit that
# hardened P-19; rev-list returns the newer, which is Phase 5 as it was accepted.
gate_at_its_own_tree("P-7", "phase-5)",
                     "docs/evidence/phase-5/check_phase5.py", "Phase 5")

# ========================================== P-8 Phase 1 milestone validators
m1 = []
for script, label in ((("docs/evidence/M1.1/check_m11.py"), "M1.1 documents"),
                      (("docs/evidence/M1.2/check_m12.py"), "M1.2 competitive"),
                      (("docs/evidence/M1.3/check_m13.py"), "M1.3 requirements")):
    if (ROOT / script).exists():
        c, o = run([PY, script, "."])
        s = re.search(r"SUMMARY: (.*)", o)
        m1.append(f"{label}: exit {c} {s.group(1) if s else ''}")
add("P-8", "PASS" if all("exit 0" in x for x in m1) and m1 else "FAIL",
    "Phase 1 milestone validators re-run. " + "; ".join(m1))

# ===================================== P-9 the contract leads, not follows
sys.path.insert(0, str(ROOT / "src"))
contract_defects = []
ops = []
try:
    from clep.api import contract as _contract
    ops = _contract.operations(str(ROOT))
    if len(ops) != 20:
        contract_defects.append(f"expected 20 declared operations, found {len(ops)}")
    generated = list(ROOT.glob("**/openapi_generated*")) + \
        [p for p in ROOT.glob("src/**/*.py") if "openapi.json" in p.read_text("utf-8")
         and "write_text" in p.read_text("utf-8")]
    if generated:
        contract_defects.append(f"something writes the contract: {generated}")
except Exception as e:
    contract_defects.append(f"{type(e).__name__}: {e}")
add("P-9", "PASS" if not contract_defects else "FAIL",
    f"contract declares {len(ops)} operations and is read, never written, by the "
    f"implementation", contract_defects)

# ============================ P-10 schema, contract and code share vocabularies
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
    ]
    for label, ddl, api in pairs:
        if not ddl:
            vocab_defects.append(f"{label}: no constraint found in the schema")
        elif ddl != api:
            vocab_defects.append(f"{label}: schema {sorted(ddl)} != contract {sorted(api)}")
    from clep.evaluators.sdk import RESOLUTIONS
    if set(RESOLUTIONS) != set(_contract.enum_of("SampleResolution", str(ROOT))):
        vocab_defects.append("the evaluator SDK's resolutions disagree with the contract")
    # The identity module's own kind list is a third statement of the same
    # vocabulary and must agree with both.
    from clep.experiments.identity import CAPTURED_KINDS
    if set(CAPTURED_KINDS) != ddl_enum("ck_run_identity_component__kind"):
        vocab_defects.append(
            "the identity module's component kinds disagree with the schema; a "
            "kind it accepts that the schema rejects fails on insert, after the "
            "digest has already been computed from it")
except Exception as e:
    vocab_defects.append(f"{type(e).__name__}: {e}")
add("P-10", "PASS" if not vocab_defects else "FAIL",
    f"vocabularies compared across schema, contract and code: {len(pairs) + 2}; "
    f"disagreements: {len(vocab_defects)}", vocab_defects)

# ================================== P-11 every dependency carries a justification
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
deps = set(re.findall(r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?[><=!]', pyproject))
justified = (ROOT / "docs/dependencies.md").read_text(encoding="utf-8")
unjustified = sorted(d for d in deps if f"`{d}`" not in justified)
add("P-11", "PASS" if not unjustified else "FAIL",
    f"{len(deps)} declared dependencies, each with a recorded reason and a rejected "
    f"alternative; unjustified: {unjustified or 'none'}. Phase 6 added none.")

# ===================================== P-12 no undecided ADR blocks this phase
adr_dir = ROOT / "docs/adr"
adrs = sorted(adr_dir.glob("ADR-*.md"))
undecided = [p.name for p in adrs if "NOT DECIDED" in p.read_text(encoding="utf-8")]
add("P-12", "PASS" if len(adrs) == 15 and not undecided else "FAIL",
    f"{len(adrs)} ADRs; undecided: {undecided or 'none'}")

# ================== P-13 idempotency on every externally visible effect table
effect_tables = {"run_sample": "uq_run_sample__idempotency_key",
                 "sample_cost": "uq_sample_cost__idempotency_key",
                 "run": "uq_run__idempotency_key"}
missing_keys = [t for t, c in effect_tables.items() if c not in sql]
add("P-13", "PASS" if not missing_keys else "FAIL",
    f"every externally visible effect carries a unique idempotency key "
    f"({len(effect_tables)} tables); missing: {missing_keys or 'none'}")

# ========================== P-14 row-level security on every tenant-scoped table
tables = dict(re.findall(r"CREATE TABLE clep\.(\w+)\s*\((.*?)\n\);", sql, re.S))
tenant_scoped = [t for t in tables if t != "organization"]
rls_defects = []
for t in tenant_scoped:
    if not re.search(rf"ALTER TABLE clep\.{t}\s+ENABLE ROW LEVEL SECURITY", sql):
        rls_defects.append(f"{t} has no ENABLE")
    if not re.search(rf"ALTER TABLE clep\.{t}\s+FORCE\s+ROW LEVEL SECURITY", sql):
        rls_defects.append(f"{t} has no FORCE; the owner would bypass every policy")
add("P-14", "PASS" if not rls_defects else "FAIL",
    f"{len(tenant_scoped)} tenant-scoped tables, each with ENABLE and FORCE",
    rls_defects)

# ======================= P-15 composite FK targets have a matching unique key
fk_targets = set(re.findall(
    r"REFERENCES\s+clep\.(\w+)\s*\(\s*organization_id\s*,\s*id\s*\)", sql))
has_uq = {t for t, body in tables.items()
          if re.search(r"UNIQUE\s*\(\s*organization_id\s*,\s*id\s*\)", body)}
missing_uq = sorted(fk_targets - has_uq)
add("P-15", "PASS" if not missing_uq else "FAIL",
    f"{len(fk_targets)} composite foreign-key targets, each with a matching unique "
    f"constraint; missing: {missing_uq or 'none'}")

# ============================== P-16 the schema is the migration set, not a copy
mig = (ROOT / "src/clep/db/migrations.py").read_text(encoding="utf-8")
copies = [str(p.relative_to(ROOT)) for p in ROOT.glob("src/**/*.sql")]
add("P-16", "PASS" if "docs" in mig and "schema" in mig and not copies else "FAIL",
    f"migrations are applied from docs/data/schema/ with no second copy of the DDL; "
    f"copies found: {copies or 'none'}")

# ================= P-17 the registry's dangling references are now real keys
#
# Phase 5 carried run_candidate.model_configuration_id and prompt_version_id as
# bare uuids, because the tables they name did not exist. A run could therefore
# claim a configuration that was never registered, and REQ-F-07-1 would be
# recording an identity for something unidentifiable.
dangling = []
for column, target in (("model_configuration_id", "model_configuration"),
                       ("prompt_version_id", "prompt_version"),
                       ("system_version_id", "system_version")):
    if not re.search(rf"fk_run_candidate__{target}", sql):
        dangling.append(f"run_candidate.{column} references nothing")
if not re.search(r"fk_run__experiment", sql):
    dangling.append("run.experiment_id references nothing")
add("P-17", "PASS" if not dangling else "FAIL",
    f"run and run_candidate registry references resolved to real foreign keys: "
    f"4 checked; dangling: {dangling or 'none'}", dangling)

# ========================= P-18 versioned artifacts are immutable in the STORE
#
# REQ-F-01-1 has to hold against something that is not the application. A rule
# enforced only in code is a rule that holds only while every caller remembers.
immutability = []
for table in ("prompt_version", "model_configuration"):
    if not re.search(rf"CREATE TRIGGER trg_{table}__immutable", sql):
        immutability.append(f"{table} has no immutability trigger")
if "refuse_change_to_frozen_version" not in sql:
    immutability.append("the immutability function is absent")
# The rule must be the rule, not a list of columns. An enumeration is wrong the
# moment a column is added, and probing the first draft of this trigger against a
# real database showed exactly that: `body` was not in the list, so a published
# version's text could change while its digest stayed the same.
if re.search(r"NEW\.content_digest\s*<>\s*OLD\.content_digest", sql):
    immutability.append(
        "the immutability trigger enumerates columns; it must refuse any change "
        "to a published row, or the next column added escapes it")
for table in ("prompt_version", "model_configuration", "system_version",
              "run_identity_component", "result_cache", "reproduction_gap"):
    if re.search(rf"GRANT[^;]*DELETE[^;]*clep\.{table}\b", sql):
        immutability.append(f"the runtime role is granted DELETE on {table}")
add("P-18", "PASS" if not immutability else "FAIL",
    f"version immutability enforced by the store: 2 triggers, 6 tables checked "
    f"for a DELETE grant; defects: {len(immutability)}", immutability)

# ============================= P-19 the identity digest excludes the environment
#
# ADR-014. If the environment entered the digest, two runs of the same
# measurement on different hosts would have different identities and REQ-F-01-3
# could never be satisfied across machines. Checked mechanically, because a
# decision recorded only in prose is a decision that drifts.
identity_defects = []
try:
    from clep.experiments.identity import (CONTEXT_KINDS, IDENTITY_KINDS,
                                           IdentityBuilder, digest_of)
    if "environment" in IDENTITY_KINDS:
        identity_defects.append("environment is identity-bearing, contradicting ADR-014")
    if "environment" not in CONTEXT_KINDS:
        identity_defects.append("environment is not captured at all, contradicting "
                                "REQ-F-07-1")
    base = IdentityBuilder().add("dataset_version", "D", digest_of("d")).build()
    with_env = IdentityBuilder().add("dataset_version", "D", digest_of("d"))
    with_env.add_environment()
    built = with_env.build()
    if built.digest() != base.digest():
        identity_defects.append("adding environment metadata changed the digest")
    if len(built.components) <= len(base.components):
        identity_defects.append("environment metadata was not captured")
    # And the digest must still be sensitive to what it does cover.
    other = IdentityBuilder().add("dataset_version", "D", digest_of("other")).build()
    if other.digest() == base.digest():
        identity_defects.append(
            "the digest is insensitive to a component's content, which would make "
            "every comparability claim vacuous")
except Exception as e:
    identity_defects.append(f"{type(e).__name__}: {e}")
add("P-19", "PASS" if not identity_defects else "FAIL",
    f"ADR-014 enforced mechanically: environment captured, excluded from the "
    f"digest, and the digest still content-sensitive; defects: "
    f"{len(identity_defects)}", identity_defects)

# ================================= P-20 the cache cannot change an outcome
cache_defects = []
try:
    from clep.experiments.cache import KEY_FIELDS, cache_key
    sample = {f: "sha256:" + "0" * 64 for f in KEY_FIELDS}
    baseline = cache_key(**sample)
    for field in KEY_FIELDS:
        altered = dict(sample)
        altered[field] = altered[field] + "x"
        if cache_key(**altered) == baseline:
            cache_defects.append(
                f"changing {field} does not change the cache key; a hit would "
                f"answer a different question than the one asked")
    if "refuse_cache_of_nondeterministic_result" not in sql:
        cache_defects.append("the determinism precondition is not enforced by the store")
    if not re.search(r"CREATE TRIGGER trg_result_cache__deterministic_only", sql):
        cache_defects.append("result_cache has no determinism trigger")
    if "is_served_from_cache" not in sql:
        cache_defects.append("nothing records whether a sample was served from cache")
except Exception as e:
    cache_defects.append(f"{type(e).__name__}: {e}")
add("P-20", "PASS" if not cache_defects else "FAIL",
    f"REQ-F-07-4: {len(KEY_FIELDS) if 'KEY_FIELDS' in dir() else '?'} key fields "
    f"each proven to change the key, determinism enforced by trigger, cache "
    f"service recorded; defects: {len(cache_defects)}", cache_defects)

# ====================== P-21 the registry stores no credential-shaped column
#
# A registry row is a thing many people can read. Endpoints and keys are
# deployment configuration and belong in the environment.
registry_tables = ("provider", "model", "model_configuration", "prompt",
                   "prompt_version", "system_definition", "system_version")
leaky = []
for t in registry_tables:
    body = tables.get(t, "")
    for column in re.findall(r"^\s{4}(\w+)\s+", body, re.M):
        if any(w in column for w in ("key", "secret", "token", "password",
                                     "credential", "url")):
            leaky.append(f"{t}.{column}")
add("P-21", "PASS" if not leaky else "FAIL",
    f"{len(registry_tables)} registry tables carry no endpoint or credential "
    f"column; found: {leaky or 'none'}", leaky)

# ============================================================ P-22 secrets
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
# silently widen. This comment deliberately does not reproduce the flagged
# string; an earlier draft did, and the checker flagged its own documentation.
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
add("P-22", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} undisclosed match(es), "
    f"{len(set(disclosed))} disclosed and unremovable from published history",
    sec + blob_sec + sorted(set(disclosed)))

# ======================================================== P-23 attribution
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
add("P-23", "PASS" if not att and not msg_hits else "FAIL",
    f"governed scope: {len(att)} file match(es), {len(msg_hits)} history match(es)",
    att + msg_hits)

# ======================================================= P-24 git identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()) - {""})
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()) - {""})
add("P-24", "PASS" if len(authors) == 1 and len(committers) == 1 else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ================================================== P-25 canonical document
#
# Reachability, not the tip tree of one branch. Phase 5 finalization found the
# earlier form of this check too narrow: `ls-tree main` would pass a document
# committed to main and deleted one commit later, and never looked at the other
# local branches at all.
#
# The allowlist is empty and is meant to stay that way. It held one entry:
# `refs/heads/milestone/M1.1-product-definition` at blob af23db3, a superseded
# pre-publication WIP branch whose seven commits were squashed into 6adfbab
# before anything was pushed. Disclosing it was the right answer while it
# existed; deleting it is a better one, because a disclosed hazard is still a
# hazard — a single `git push --all` would have published the canonical
# specification. The branch was deleted at Phase 6 finalization after verifying
# origin carried `main` alone and no remote-tracking ref for it had ever
# existed. Nothing was lost: every commit on it was superseded on `main`, and
# the document itself is a local file that Git has never tracked.
#
# Keeping the mechanism with nothing in it is deliberate. Re-disclosing
# something has to be an explicit act, and until one is made every `.docx`
# reachable from any ref fails this check.
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
add("P-25", "PASS" if ok else "FAIL",
    f"canonical document local={bool(docx)} tracked={bool(tracked)} "
    f"ignored={ignored} refs_scanned={len(refs)} "
    f"reachable_from_published={len(published)} "
    f"reachable_from_local_undisclosed={len(undisclosed)} "
    f"disclosed_local_only={len(disclosed_local)}",
    published + undisclosed + disclosed_local)

# ============================================================ P-26 hygiene
tracked_files = [f for f in git("ls-files").splitlines() if f]
dirty = [l for l in git("status", "--porcelain").splitlines() if l]
strays = [f for f in tracked_files
          if re.search(r"(\.orig|\.rej|\.bak|~|\.DS_Store|Thumbs\.db)$", f)]
add("P-26", "PASS" if not dirty and not strays else "FAIL",
    f"{len(tracked_files)} tracked file(s); stray: {len(strays)}; "
    f"clean tree: {not dirty}", dirty[:6] + strays)

# =========================================== P-27 phase boundary not overrun
later_phase = []
for pattern, label in ((("docs/**/ADR-01[6-9]*.md"), "ADRs beyond the recorded set"),
                       (("src/clep/regression/**"), "regression engine (Phase 7)"),
                       (("src/clep/gates/**"), "quality-gate policy engine (Phase 7)"),
                       (("src/clep/agents/**"), "agentic evaluation layer (Phase 8)"),
                       (("src/clep/dashboards/**"), "dashboards (Phase 11)")):
    hits = [str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)]
    if hits:
        later_phase.append(f"{label}: {hits[:2]}")
add("P-27", "PASS" if not later_phase else "FAIL",
    f"Phase 7+ artifact classes checked: 5; artifacts Phase 6 must not contain: "
    f"{len(later_phase)}", later_phase)

# ------------------------------------------------------------------ summary
print("-" * 78)
counts = {}
for r in results:
    counts[r["status"]] = counts.get(r["status"], 0) + 1
print("SUMMARY: " + json.dumps(counts, sort_keys=True))
sys.exit(1 if counts.get("FAIL") else 0)
