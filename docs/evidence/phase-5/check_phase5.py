"""Phase 5 comprehensive validation, with regression over every earlier phase.

Phase 5 is the first phase whose output executes. The checks therefore split in
two: the ones that read artifacts, as before, and the ones that RUN something and
believe the exit code. Where both were possible, this file runs the thing.

Earlier phase gates are re-evaluated against their OWN trees, in throwaway
worktrees, because a phase-boundary assertion becomes historical the moment the
next phase starts. The Phase 4 gate asserts that two ADRs are undecided; they are
now decided, and re-running that gate against today's tree would report a failure
that is really a success.

Usage: python docs/evidence/phase-5/check_phase5.py <repo_root>
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
    # These two came from the Phase 3 and Phase 4 gates. This checker was
    # written without them and passed a tree those gates failed, which is a
    # reminder that a later validator inherits nothing automatically.
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


def run(cmd, cwd=None, timeout=1800):
    p = subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def text_files(base: Path):
    """Skip hidden DIRECTORIES, scan hidden FILES.

    The earlier gates skipped anything whose first path component began with a
    dot, which excluded tool directories — and also excluded `.env.example`, a
    committed file whose entire purpose is to show configuration. A credential
    left there would never have been scanned.
    """
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
# `-p no:warnings` keeps interpreter and virtual-environment paths out of the
# recorded output. A local path is not evidence, and one of them contained a
# tool directory name that the attribution scan then flagged in its own record.
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
tables = re.search(r"(\d+) table\(s\) parsed", out)
add("P-2", "PASS" if code == 0 else "FAIL",
    f"schema conformance: exit {code}; {tables.group(1) if tables else '?'} tables "
    f"including the Phase 5 execution tables")

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

# ============================ P-5, P-6 earlier gates, at the trees they mean
def gate_at_its_own_tree(cid, grep, script, label):
    """Re-run an earlier gate against the history it was written for.

    Two things have to be reproduced, not one.

    The TREE, because a phase-boundary assertion becomes historical the moment
    the next unit starts: the spike gate asserts that no implementation exists,
    and Phase 5 is the implementation.

    The HISTORY, because these gates also scan every blob on every ref. A git
    worktree shares the object database with the repository that created it, so
    a gate run in one sees blobs from commits made after it — and reports a
    failure that is really just the future arriving. An isolated clone, reset to
    the commit and pruned of everything unreachable from it, is the only way the
    scan means what it meant when it was written.
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
    # EVERY ref, not just main. A clone creates a local branch for whatever HEAD
    # pointed at, which is whichever branch the work happens to be on — and one
    # surviving ref keeps the whole later history reachable, leaving the prune
    # below with nothing to do and the scan seeing the future again.
    refs = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes"],
        cwd=str(tree), capture_output=True, text=True).stdout.split()
    for ref in refs:
        subprocess.run(["git", "update-ref", "-d", ref], cwd=str(tree),
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
    c, o = run([PY, str(tree / script), str(tree)], cwd=tree)
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

# ========================================== P-7 Phase 1 milestone validators
m1 = []
for script, label in ((("docs/evidence/M1.1/check_m11.py"), "M1.1 documents"),
                      (("docs/evidence/M1.2/check_m12.py"), "M1.2 competitive"),
                      (("docs/evidence/M1.3/check_m13.py"), "M1.3 requirements")):
    p = ROOT / script
    if p.exists():
        c, o = run([PY, script, "."])
        s = re.search(r"SUMMARY: (.*)", o)
        m1.append(f"{label}: exit {c} {s.group(1) if s else ''}")
add("P-7", "PASS" if all("exit 0" in x for x in m1) and m1 else "FAIL",
    "Phase 1 milestone validators re-run. " + "; ".join(m1))

# ===================================== P-8 the contract leads, not follows
sys.path.insert(0, str(ROOT / "src"))
contract_defects = []
try:
    from clep.api import contract as _contract
    ops = _contract.operations(str(ROOT))
    if len(ops) != 13:
        contract_defects.append(f"expected 13 declared operations, found {len(ops)}")
    generated = list(ROOT.glob("**/openapi_generated*")) + \
        [p for p in ROOT.glob("src/**/*.py") if "openapi.json" in p.read_text("utf-8")
         and "write_text" in p.read_text("utf-8")]
    if generated:
        contract_defects.append(f"something writes the contract: {generated}")
except Exception as e:
    contract_defects.append(f"{type(e).__name__}: {e}")
add("P-8", "PASS" if not contract_defects else "FAIL",
    f"contract declares {len(ops) if 'ops' in dir() else '?'} operations and is read, "
    f"never written, by the implementation", contract_defects)

# ============================ P-9 schema, contract and code share vocabularies
vocab_defects = []
sql = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted((ROOT / "docs/data/schema").glob("*.sql")))
sql = re.sub(r"--[^\n]*", "", sql)


def ddl_enum(constraint):
    m = re.search(rf"CONSTRAINT\s+{constraint}\s+CHECK\s*\((.*?)\)\s*[,)]", sql, re.S)
    if not m:
        return set()
    return set(re.findall(r"'([a-z_]+)'", m.group(1)))


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
    ]
    for label, ddl, api in pairs:
        if not ddl:
            vocab_defects.append(f"{label}: no constraint found in the schema")
        elif ddl != api:
            vocab_defects.append(f"{label}: schema {sorted(ddl)} != contract {sorted(api)}")
    # The code must not restate a vocabulary either.
    from clep.evaluators.sdk import RESOLUTIONS
    if set(RESOLUTIONS) != set(_contract.enum_of("SampleResolution", str(ROOT))):
        vocab_defects.append("the evaluator SDK's resolutions disagree with the contract")
except Exception as e:
    vocab_defects.append(f"{type(e).__name__}: {e}")
add("P-9", "PASS" if not vocab_defects else "FAIL",
    f"vocabularies compared across schema, contract and code: {len(pairs) + 1}; "
    f"disagreements: {len(vocab_defects)}", vocab_defects)

# ================================== P-10 every dependency carries a justification
pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
deps = set(re.findall(r'"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?[><=!]', pyproject))
justified = (ROOT / "docs/dependencies.md").read_text(encoding="utf-8")
unjustified = sorted(d for d in deps if f"`{d}`" not in justified)
add("P-10", "PASS" if not unjustified else "FAIL",
    f"{len(deps)} declared dependencies, each with a recorded reason and a rejected "
    f"alternative; unjustified: {unjustified or 'none'}")

# ===================================== P-11 no undecided ADR blocks this phase
adr_dir = ROOT / "docs/adr"
adrs = sorted(adr_dir.glob("ADR-*.md"))
undecided = [p.name for p in adrs if "NOT DECIDED" in p.read_text(encoding="utf-8")]
add("P-11", "PASS" if len(adrs) == 13 and not undecided else "FAIL",
    f"{len(adrs)} ADRs; undecided: {undecided or 'none'}")

# ================== P-12 idempotency on every externally visible effect table
effect_tables = {"run_sample": "uq_run_sample__idempotency_key",
                 "sample_cost": "uq_sample_cost__idempotency_key",
                 "run": "uq_run__idempotency_key"}
missing_keys = [t for t, c in effect_tables.items() if c not in sql]
add("P-12", "PASS" if not missing_keys else "FAIL",
    f"every externally visible effect carries a unique idempotency key "
    f"({len(effect_tables)} tables) - the ADR-001 spike's binding output; "
    f"missing: {missing_keys or 'none'}")

# ========================== P-13 row-level security on every tenant-scoped table
tables = dict(re.findall(r"CREATE TABLE clep\.(\w+)\s*\((.*?)\n\);", sql, re.S))
tenant_scoped = [t for t in tables if t != "organization"]
rls_defects = []
for t in tenant_scoped:
    if not re.search(rf"ALTER TABLE clep\.{t}\s+ENABLE ROW LEVEL SECURITY", sql):
        rls_defects.append(f"{t} has no ENABLE")
    if not re.search(rf"ALTER TABLE clep\.{t}\s+FORCE\s+ROW LEVEL SECURITY", sql):
        rls_defects.append(f"{t} has no FORCE; the owner would bypass every policy")
add("P-13", "PASS" if not rls_defects else "FAIL",
    f"{len(tenant_scoped)} tenant-scoped tables, each with ENABLE and FORCE",
    rls_defects)

# ======================= P-14 composite FK targets have a matching unique key
fk_targets = set(re.findall(
    r"REFERENCES\s+clep\.(\w+)\s*\(\s*organization_id\s*,\s*id\s*\)", sql))
has_uq = {t for t, body in tables.items()
          if re.search(r"UNIQUE\s*\(\s*organization_id\s*,\s*id\s*\)", body)}
missing_uq = sorted(fk_targets - has_uq)
add("P-14", "PASS" if not missing_uq else "FAIL",
    f"{len(fk_targets)} composite foreign-key targets, each with a matching unique "
    f"constraint - the defect executing the DDL exposed; missing: {missing_uq or 'none'}")

# ============================== P-15 the schema is the migration set, not a copy
mig = (ROOT / "src/clep/db/migrations.py").read_text(encoding="utf-8")
copies = [str(p.relative_to(ROOT)) for p in ROOT.glob("src/**/*.sql")]
add("P-15", "PASS" if "docs" in mig and "schema" in mig and not copies else "FAIL",
    f"migrations are applied from docs/data/schema/ with no second copy of the DDL; "
    f"copies found: {copies or 'none'}")

# ============================================================ P-16 secrets
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
# credentials, and cannot be removed: they are on `main` at origin, and rewriting
# published history is forbidden. They are named by content hash so the exception
# cannot silently widen — a changed file gets a new hash and fails again.
#
#   0ec5878  spike-sprint/common.py  — a throwaway local password in a DSN for a
#            spike container that no longer exists. A real defect: "it is only
#            the local one" is exactly how a real one gets committed. The working
#            tree no longer contains it.
#   42de37b  spike-sprint/spike_provider_abstraction.py — a local variable named
#            for a secret, holding a deliberately fake canary planted to prove
#            the leak detector could detect anything. Never a credential. The
#            variable is now named `canary`, which is what it always was.
#
# This comment deliberately does not reproduce the flagged string. An earlier
# draft did, and the checker flagged its own documentation.
#
# Neither grants access to anything. Both are disclosed rather than suppressed.
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
add("P-16", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} undisclosed match(es), "
    f"{len(set(disclosed))} disclosed and unremovable from published history",
    sec + blob_sec + sorted(set(disclosed)))

# ======================================================== P-17 attribution
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
add("P-17", "PASS" if not att and not msg_hits else "FAIL",
    f"governed scope: {len(att)} file match(es), {len(msg_hits)} history match(es)",
    att + msg_hits)

# ======================================================= P-18 git identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()) - {""})
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()) - {""})
add("P-18", "PASS" if len(authors) == 1 and len(committers) == 1 else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ================================================== P-19 canonical document
# The requirement is that the canonical specification never leaves this machine
# through git. Until Phase 5 finalization this check asked `ls-tree main`, which
# reads only main's CURRENT tree — it would have passed a document that was
# committed to main and deleted a commit later, and it never looked at the other
# seven local branches at all. Reachability is the property that matters, so
# reachability is what is measured.
#
# One local branch does carry it. `milestone/M1.1-product-definition` is a
# superseded chain of `wip(M1.1)` commits, squashed into the grandfathered
# 6adfbab before anything was published; origin holds `main` and nothing else,
# so the document is absent from published history. It is disclosed here by ref
# AND blob hash, the same way the two unremovable spike blobs are, so that the
# exception cannot silently widen: a second copy, a different blob, or the same
# blob on any other ref fails this check.
DISCLOSED_LOCAL = {
    ("refs/heads/milestone/M1.1-product-definition",
     "af23db348a2aa115f95253a79a116e48b0798b40"):
        "superseded pre-publication WIP branch; never pushed, origin has main only",
}
docx = list(ROOT.glob("*.docx"))
tracked = [d for d in docx if git("ls-files", d.name).strip()]
ignored = all(git("check-ignore", d.name).strip() for d in docx) if docx else False

refs = [r for r in git("for-each-ref", "--format=%(refname)",
                       "refs/heads", "refs/remotes").split() if r]
published, undisclosed, disclosed = [], [], []
for ref in refs:
    for line in git("rev-list", "--objects", ref).splitlines():
        sha, _, path = line.partition(" ")
        if not re.search(r"\.docx?$", path, re.I):
            continue
        where = f"{ref}: {path} ({sha[:7]})"
        if ref.startswith("refs/remotes/"):
            published.append(where)          # published: never permissible
        elif (ref, sha) in DISCLOSED_LOCAL:
            disclosed.append(f"{where} - {DISCLOSED_LOCAL[(ref, sha)]}")
        else:
            undisclosed.append(where)
ok = bool(docx) and not tracked and ignored and not published and not undisclosed
add("P-19", "PASS" if ok else "FAIL",
    f"canonical document local={bool(docx)} tracked={bool(tracked)} "
    f"ignored={ignored} refs_scanned={len(refs)} "
    f"reachable_from_published={len(published)} "
    f"reachable_from_local_undisclosed={len(undisclosed)} "
    f"disclosed_local_only={len(disclosed)}",
    published + undisclosed + disclosed)

# ============================================================ P-20 hygiene
tracked_files = [f for f in git("ls-files").splitlines() if f]
dirty = [l for l in git("status", "--porcelain").splitlines() if l]
strays = [f for f in tracked_files
          if re.search(r"(\.orig|\.rej|\.bak|~|\.DS_Store|Thumbs\.db)$", f)]
add("P-20", "PASS" if not dirty and not strays else "FAIL",
    f"{len(tracked_files)} tracked file(s); stray: {len(strays)}; "
    f"clean tree: {not dirty}", dirty[:6] + strays)

# =========================================== P-21 phase boundary not overrun
later_phase = []
for pattern, label in ((("docs/**/ADR-01[4-9]*.md"), "ADRs beyond the recorded set"),
                       (("src/clep/regression/**"), "regression engine (Phase 7)"),
                       (("src/clep/registry/**"), "prompt/model registry (Phase 6)"),
                       (("src/clep/dashboards/**"), "dashboards (Phase 11)")):
    hits = [str(p.relative_to(ROOT)) for p in ROOT.glob(pattern)]
    if hits:
        later_phase.append(f"{label}: {hits[:2]}")
add("P-21", "PASS" if not later_phase else "FAIL",
    f"Phase 6+ artifact classes checked: 4; artifacts Phase 5 must not contain: "
    f"{len(later_phase)}", later_phase)

# =============================================================== output
counts = {}
for r in results:
    counts[r["status"]] = counts.get(r["status"], 0) + 1
print("-" * 78)
print("SUMMARY: " + json.dumps(counts))
sys.exit(1 if counts.get("FAIL") else 0)
