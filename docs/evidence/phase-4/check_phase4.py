"""Phase 4 comprehensive validation.

Regresses earlier phases, then checks what Phase 4 adds: the datastore ADRs, the
schema specification, and the lifecycle rules.

The check that carries this phase is T-6, schema/contract vocabulary agreement.
Phase 3 defined enumerations in the API contract and Phase 4 defined the same
enumerations as check constraints. Two definitions of one vocabulary is exactly
the duplication that drifts, so they are compared mechanically rather than
trusted to stay aligned.

Usage: python check_phase4.py <repo_root>
Exits non-zero on any FAIL.
"""
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1]).resolve()
DATA = ROOT / "docs" / "data"
SCHEMA_DIR = DATA / "schema"
ADR = ROOT / "docs" / "adr"
EV3 = ROOT / "docs" / "evidence" / "phase-3"
EV4 = ROOT / "docs" / "evidence" / "phase-4"

PHASE3_TREE = "9674b2c447e07bf543421717e4b140df06858ff0"
IDENTITY = "Stevemeg <konabharath2004@gmail.com>"
DOCX_NAME = "Continuous LLM Evaluation Platform - Canonical Master Prompt v3.docx"
DOCX_SHA256 = "53329e77d527de517cad416785dff1aaeff83c2b9e475918acf16acfde33580f"

REQUIRED_ADRS = [f"ADR-{i:03d}" for i in range(1, 14)]   # 11 canonical + 2 datastore
GATED_ADRS = {"ADR-001", "ADR-003"}

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
INVENTED = [
    (r"\b\d+(\.\d+)?\s?%", "percentage"),
    (r"[$€£]\s?\d", "currency"),
    (r"\b\d+(\.\d+)?\s?(ms|milliseconds?|seconds?)\b", "latency figure"),
]
NEGATE = ["TARGET NOT YET SET", "NOT YET MEASURED", "must not", "will not", "never",
          "forbid", "reject", "unset", "no target", "numeric(", "pattern"]

results = []


def add(cid, status, detail, findings=None):
    results.append({"id": cid, "status": status, "detail": detail, "findings": findings or []})


def git(*a, binary=False):
    r = subprocess.run(["git", "-C", str(ROOT)] + list(a), capture_output=True,
                       text=not binary, encoding=None if binary else "utf-8",
                       errors=None if binary else "replace")
    return r.stdout


phase4_texts = {p.name: p.read_text(encoding="utf-8")
                for p in [DATA / "dataset-lifecycle.md"] if p.exists()}
for p in sorted(ADR.glob("ADR-01[23]*.md")):
    phase4_texts[p.name] = p.read_text(encoding="utf-8")
schema_sql = "\n".join(p.read_text(encoding="utf-8") for p in sorted(SCHEMA_DIR.glob("*.sql")))
spec = json.loads((ROOT / "docs/api/openapi.json").read_text(encoding="utf-8"))

# ============================================================ T-1 phase 1 regression
VALIDATORS = [
    ("M1.1 documents", "docs/evidence/M1.1/check_m11.py"),
    ("M1.1 cross-document", "docs/evidence/M1.1/check_crossrefs.py"),
    ("M1.2 competitive/positioning", "docs/evidence/M1.2/check_m12.py"),
    ("M1.3 requirements", "docs/evidence/M1.3/check_m13.py"),
]
rows, failed = [], []
for name, rel in VALIDATORS:
    r = subprocess.run([sys.executable, str(ROOT / rel), str(ROOT)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    summ = next((l.replace("SUMMARY:", "").strip() for l in r.stdout.splitlines()
                 if l.startswith("SUMMARY:")), "PASS" if r.returncode == 0 else "?")
    rows.append(f"{name}: exit {r.returncode} {summ}")
    if r.returncode != 0:
        failed.append({"label": name, "text": f"exit {r.returncode}"})
add("T-1", "PASS" if not failed else "FAIL",
    "Phase 1 milestone validators re-run. " + "; ".join(rows), failed)

# ============================================================ T-2 phase 3 gate at its tree
tmp = Path(tempfile.mkdtemp(prefix="p3gate-"))
wt = tmp / "tree"
p3 = {"code": None, "note": ""}
try:
    r = subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), PHASE3_TREE],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        p3["note"] = f"worktree creation failed: {r.stderr.strip()[:120]}"
    else:
        src = ROOT / DOCX_NAME
        if src.exists():
            shutil.copy2(src, wt / DOCX_NAME)
        rr = subprocess.run([sys.executable, str(wt / "docs/evidence/phase-3/check_phase3.py"), str(wt)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=str(wt))
        p3["code"] = rr.returncode
        p3["note"] = next((l.replace("SUMMARY:", "").strip() for l in rr.stdout.splitlines()
                           if l.startswith("SUMMARY:")), "")
finally:
    subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
add("T-2", "PASS" if p3["code"] == 0 else "FAIL",
    f"Phase 3 phase-gate re-evaluated against the Phase 3 tree ({PHASE3_TREE[:8]}) in a "
    f"throwaway worktree: exit {p3['code']} {p3['note']}",
    [] if p3["code"] == 0 else [{"label": "phase-3 gate", "text": str(p3["note"])}])

# ============================================================ T-3 schema conformance
r = subprocess.run([sys.executable, str(EV4 / "check_schema_conformance.py"), str(ROOT)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
summ = next((l.replace("SUMMARY:", "").strip() for l in r.stdout.splitlines()
             if l.startswith("SUMMARY:")), "")
add("T-3", "PASS" if r.returncode == 0 else "FAIL",
    f"schema conformance: exit {r.returncode} {summ}",
    [] if r.returncode == 0 else [{"label": "conformance", "text": summ}])

# ============================================================ T-4 traceability
r = subprocess.run([sys.executable, str(EV3 / "generate_traceability.py"), str(ROOT)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
tr = [l.strip() for l in r.stdout.splitlines()
      if l.strip().startswith(("traced to an artifact", "deferred with an owner", "schema layer"))]
add("T-4", "PASS" if r.returncode == 0 else "FAIL",
    f"traceability: exit {r.returncode}. " + "; ".join(tr),
    [] if r.returncode == 0 else [{"label": "traceability", "text": "untracked or stale"}])

# ============================================================ T-5 matrix currency
matrix = EV3 / "traceability-matrix.md"
before = matrix.read_bytes() if matrix.exists() else b""
subprocess.run([sys.executable, str(EV3 / "generate_traceability.py"), str(ROOT), "--write"],
               capture_output=True, cwd=str(ROOT))
after = matrix.read_bytes() if matrix.exists() else b""
m_ok = before == after and before != b""
add("T-5", "PASS" if m_ok else "FAIL",
    f"traceability matrix {'regenerates identically' if m_ok else 'is STALE'}",
    [] if m_ok else [{"label": "matrix", "text": "stale"}])

# ============================================================ T-6 schema/contract vocabulary
# Two definitions of one vocabulary drift. Compare them.
def ddl_enum(constraint):
    m = re.search(rf"CONSTRAINT\s+{constraint}\s+CHECK\s*\((.*?)\)\s*,?\s*\n", schema_sql, re.S)
    if not m:
        return None
    vals = re.findall(r"'([^']+)'", m.group(1))
    return set(vals)


def api_enum(schema_name, prop=None):
    s = spec["components"]["schemas"].get(schema_name, {})
    if prop:
        s = s.get("properties", {}).get(prop, {})
    return set(s.get("enum", []) or [])


PAIRS = [
    ("dataset version state", "ck_dataset_version__state", ("DatasetVersion", "state")),
    ("quality finding kind", "ck_quality_check_result__kind", ("QualityFinding", "kind")),
    ("quality finding severity", "ck_quality_check_result__severity", ("QualityFinding", "severity")),
]
f6 = []
compared = 0
for label, constraint, (sch, prop) in PAIRS:
    d = ddl_enum(constraint)
    a = api_enum(sch, prop)
    if d is None:
        f6.append({"label": label, "text": f"constraint {constraint} not found in schema"})
        continue
    if not a:
        f6.append({"label": label, "text": f"no enum on {sch}.{prop} in the contract"})
        continue
    compared += 1
    if d != a:
        f6.append({"label": label, "text": f"schema {sorted(d)} != contract {sorted(a)}"})
# artifact_class in the schema must cover every class the artifact model names
art = ddl_enum("ck_artifact__class")
expected_art = {"input_snapshot", "candidate_output", "retrieved_context", "trajectory",
                "judge_rationale", "evaluator_detail", "gate_evidence"}
if art != expected_art:
    f6.append({"label": "artifact class", "text": f"schema {sorted(art or [])} != model {sorted(expected_art)}"})
else:
    compared += 1
add("T-6", "PASS" if not f6 else "FAIL",
    f"schema/contract vocabularies compared: {compared}; disagreements: {len(f6)}", f6)

# ============================================================ T-7 ADR set
adr_files = {p.stem.split("-")[0] + "-" + p.stem.split("-")[1]: p.read_text(encoding="utf-8")
             for p in sorted(ADR.glob("ADR-*.md"))}
missing_adr = [a for a in REQUIRED_ADRS if a not in adr_files]
statuses, bad_status = {}, []
for k, t in adr_files.items():
    m = re.search(r"\|\s*Status\s*\|\s*(.+?)\s*\|", t)
    if not m:
        bad_status.append({"label": k, "text": "no Status field"})
        continue
    statuses[k] = m.group(1)
    if not (("Accepted" in m.group(1)) or ("Proposed" in m.group(1) and "NOT DECIDED" in m.group(1))):
        bad_status.append({"label": k, "text": f"unrecognised status: {m.group(1)[:60]}"})
for g in sorted(GATED_ADRS):
    if "NOT DECIDED" not in statuses.get(g, ""):
        bad_status.append({"label": g, "text": "expected to remain undecided"})
add("T-7", "PASS" if not missing_adr and not bad_status else "FAIL",
    f"ADRs present: {len(adr_files)}/{len(REQUIRED_ADRS)}; accepted: "
    f"{sum(1 for s in statuses.values() if 'Accepted' in s)}; still undecided: "
    f"{sum(1 for s in statuses.values() if 'NOT DECIDED' in s)}; missing: {missing_adr or 'none'}",
    [{"label": a, "text": "missing"} for a in missing_adr] + bad_status)

# ============================================================ T-8 new ADRs cite evidence
f8 = []
for a in ("ADR-012", "ADR-013"):
    t = adr_files.get(a, "")
    if "Constrained by" not in t:
        f8.append({"label": a, "text": "does not name the decision that constrains it"})
    if "Alternatives considered" not in t and "Why not" not in t:
        f8.append({"label": a, "text": "records no rejected alternative"})
    if "Consequences" not in t:
        f8.append({"label": a, "text": "records no consequences"})
if "ENABLE ROW LEVEL SECURITY" not in adr_files.get("ADR-012", ""):
    f8.append({"label": "ADR-012", "text": "does not quote the capability evidence it rests on"})
add("T-8", "PASS" if not f8 else "FAIL",
    f"new ADRs checked for constraint, alternatives, consequences and evidence: 2; defects: {len(f8)}", f8)

# ============================================================ T-9 requirement references
req_defined = set()
for pat in [r"REQ-F-\d{2}-\d+", r"REQ-F-AG-\d+", r"REQ-X-\d+", r"REQ-N-[A-Z]+-\d+"]:
    req_defined |= set(re.findall(r"`(" + pat + r")`",
                                 (ROOT / "docs/product/requirements.md").read_text(encoding="utf-8")))
dangling = []
for d, t in list(phase4_texts.items()) + [("schema", schema_sql)]:
    for ref in set(re.findall(r"\bREQ-(?:F-\d{2}-\d+|F-AG-\d+|X-\d+|N-[A-Z]+-\d+)\b", t)):
        if ref not in req_defined:
            dangling.append({"label": d, "text": f"undefined {ref}"})
add("T-9", "PASS" if not dangling else "FAIL",
    f"requirement references in Phase 4 artifacts; undefined: {len(dangling)}", dangling)

# ============================================================ T-10 invariant references
dm = (DATA / "domain-model.md").read_text(encoding="utf-8")
inv_defined = set(re.findall(r"^\| (I-\d+) \|", dm, re.M)) | set(re.findall(r"\*\*Invariant (I-\d+)\*\*", dm))
inv_ref = set()
for t in list(phase4_texts.values()) + [schema_sql]:
    inv_ref |= set(re.findall(r"\bI-\d+\b", t))
dangling_inv = sorted(inv_ref - inv_defined)
add("T-10", "PASS" if not dangling_inv else "FAIL",
    f"invariants referenced by Phase 4 artifacts: {len(inv_ref)}; undefined: {dangling_inv or 'none'}",
    [{"label": i, "text": "undefined"} for i in dangling_inv])

# ============================================================ T-11 invented metrics
inv = []
for d, t in phase4_texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        for rx, lb in INVENTED:
            for m in re.finditer(rx, line, re.I):
                if any(w in line for w in NEGATE):
                    continue
                inv.append({"label": f"{d}:{n}", "text": f"{lb}: {m.group(0)!r}"})
add("T-11", "PASS" if not inv else "FAIL", f"{len(inv)} invented figure(s)", inv)

# ============================================================ T-12 links
broken, nlinks = [], 0
for d, t in phase4_texts.items():
    base = DATA if d.endswith("lifecycle.md") else ADR
    for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", t):
        tgt = m.group(2)
        if tgt.startswith(("http", "mailto:")):
            continue
        nlinks += 1
        if not (base / tgt).resolve().exists():
            broken.append({"label": d, "text": tgt})
add("T-12", "PASS" if not broken else "FAIL",
    f"{nlinks} relative link(s) checked; broken: {len(broken)}", broken)

# ============================================================ T-13 placeholders
ph = []
for d, t in list(phase4_texts.items()) + [("schema", schema_sql)]:
    for n, line in enumerate(t.splitlines(), 1):
        for rx, lb in [(r"\bTODO\b", "TODO"), (r"\bFIXME\b", "FIXME"), (r"\bTBD\b", "TBD")]:
            if re.search(rx, line):
                ph.append({"label": f"{d}:{n}", "text": lb})
add("T-13", "PASS" if not ph else "FAIL", f"{len(ph)} placeholder marker(s)", ph)

# ============================================================ T-14 secrets
sec, nfiles, nbin = [], 0, 0
for p in sorted(ROOT.rglob("*")):
    if not p.is_file() or ".git" in p.parts:
        continue
    nfiles += 1
    try:
        t = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        nbin += 1
        continue
    for rx, lb in SECRETS:
        if re.search(rx, t):
            sec.append({"label": str(p.relative_to(ROOT)), "text": lb})
blob_sec = []
for line in git("rev-list", "--objects", "--all").splitlines():
    parts = line.split(maxsplit=1)
    if git("cat-file", "-t", parts[0]).strip() != "blob":
        continue
    try:
        t = git("cat-file", "blob", parts[0], binary=True).decode("utf-8")
    except UnicodeDecodeError:
        continue
    for rx, lb in SECRETS:
        if re.search(rx, t):
            blob_sec.append({"label": parts[1] if len(parts) > 1 else parts[0][:8], "text": lb})
add("T-14", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} match(es)", sec + blob_sec)

# ============================================================ T-15 attribution
branch = git("rev-parse", "--abbrev-ref", "HEAD").strip()


def att_blobs(ref):
    found = set()
    for line in git("rev-list", "--objects", ref).splitlines():
        parts = line.split(maxsplit=1)
        if git("cat-file", "-t", parts[0]).strip() != "blob":
            continue
        try:
            t = git("cat-file", "blob", parts[0], binary=True).decode("utf-8")
        except UnicodeDecodeError:
            continue
        for rx in ATT:
            if re.search(rx, t, re.I):
                found.add(parts[1] if len(parts) > 1 else parts[0][:8])
    return found


att = []
for d, t in list(phase4_texts.items()) + [("schema", schema_sql)]:
    for rx in ATT:
        if re.search(rx, t, re.I):
            att.append({"label": d, "text": "attribution in a Phase 4 artifact"})
governed = att_blobs("main") | att_blobs(branch)
for name in sorted(governed):
    att.append({"label": name, "text": f"attribution in a blob reachable from main or {branch}"})
msgs = git("log", "main", branch, "--format=%B%n%n")
for rx in ATT:
    if re.search(rx, msgs, re.I):
        att.append({"label": "commit messages", "text": "attribution in a governed commit message"})
local_only = sorted(att_blobs("--all") - governed)
seen, uniq = set(), []
for x in att:
    if (x["label"], x["text"]) not in seen:
        seen.add((x["label"], x["text"]))
        uniq.append(x)
add("T-15", "PASS" if not uniq else "FAIL",
    f"governed scope: {len(uniq)} match(es). Superseded blobs on local-only recovery refs, "
    f"disclosed: {len(local_only)} ({', '.join(local_only) if local_only else 'none'})", uniq)

# ============================================================ T-16 identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()))
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()))
add("T-16", "PASS" if authors == [IDENTITY] and committers == [IDENTITY] else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ============================================================ T-17 canonical document
docx = ROOT / DOCX_NAME
f17 = []
digest = hashlib.sha256(docx.read_bytes()).hexdigest() if docx.exists() else None
if not docx.exists():
    f17.append({"label": "presence", "text": "missing locally"})
elif digest != DOCX_SHA256:
    f17.append({"label": "integrity", "text": f"changed to {digest}"})
ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", DOCX_NAME],
                         capture_output=True).returncode == 0
if not ignored:
    f17.append({"label": "ignore", "text": "not ignored"})
if DOCX_NAME in git("ls-files").splitlines():
    f17.append({"label": "tracking", "text": "tracked"})
pub = [l for l in git("ls-tree", "-r", "--name-only", "main").splitlines()
       if l.lower().endswith((".docx", ".doc"))]
if pub:
    f17.append({"label": "published", "text": "present in main"})
add("T-17", "PASS" if not f17 else "FAIL",
    f"local present={docx.exists()} unchanged={digest == DOCX_SHA256} ignored={ignored} "
    f"tracked=False absent_from_main={not pub}", f17)

# ============================================================ T-18 hygiene
status = [l for l in git("status", "--short").splitlines() if l.strip()]
untracked = [l for l in status if l.startswith("??")]
tracked = git("ls-files").splitlines()
stray = [t for t in tracked if re.search(r"\.(tmp|log|bak|orig|rej|swp|pyc)$", t)
         or "__pycache__" in t]
add("T-18", "PASS" if not stray and not untracked else "FAIL",
    f"{len(tracked)} tracked file(s); stray: {len(stray)}; untracked-and-unignored: "
    f"{len(untracked)}; clean tree: {not status}",
    [{"label": s, "text": "stray"} for s in stray] +
    [{"label": u, "text": "untracked"} for u in untracked])

# ============================================================ T-19 phase boundary
# Phase 4 specifies a schema. It does not implement: no application source, no
# migration chain, no dependency manifest, no tests. Schema DDL under docs/ is a
# specification artifact and is deliberately excluded from the migration pattern.
PHASE5_PLUS = [
    ("application source", ["src/**/*.py", "app/**/*.py", "backend/**/*.py", "**/*.ts", "**/*.tsx"]),
    ("migration chain", ["**/migrations/**/*", "**/alembic.ini", "**/versions/*.py"]),
    ("dependency manifests", ["pyproject.toml", "package.json", "requirements.txt",
                              "uv.lock", "poetry.lock", "setup.py"]),
    ("container or infra definitions", ["Dockerfile*", "docker-compose*.yml", "**/*.tf",
                                        "**/Chart.yaml"]),
    ("test suites", ["tests/**/*", "**/test_*.py", "**/*_test.py"]),
]
leaked = []
for label, pats in PHASE5_PLUS:
    for pat in pats:
        for hit in ROOT.glob(pat):
            rel = hit.relative_to(ROOT).as_posix()
            parts = Path(rel).parts
            if parts and parts[0].startswith("."):
                continue
            if rel.startswith("docs/evidence/") or rel.startswith("docs/data/schema/"):
                continue
            leaked.append({"label": label, "text": rel})
add("T-19", "PASS" if not leaked else "FAIL",
    f"Phase 5+ artifact classes checked: {len(PHASE5_PLUS)}; artifacts Phase 4 must not "
    f"contain: {len(leaked)}", leaked)

# ============================================================ output
counts = {}
for c in results:
    counts[c["status"]] = counts.get(c["status"], 0) + 1
LABELS = {
    "T-1": "phase 1 regression", "T-2": "phase 3 gate at its tree",
    "T-3": "schema conformance", "T-4": "traceability", "T-5": "matrix currency",
    "T-6": "schema/contract vocabulary", "T-7": "ADR set", "T-8": "new ADR quality",
    "T-9": "requirement references", "T-10": "invariant references",
    "T-11": "invented metrics", "T-12": "links", "T-13": "placeholders",
    "T-14": "secrets", "T-15": "attribution", "T-16": "git identity",
    "T-17": "canonical document", "T-18": "hygiene", "T-19": "phase boundary",
}
print("=" * 78)
print("PHASE 4 COMPREHENSIVE VALIDATION")
print("=" * 78)
for c in results:
    print(f"[{c['status']:<7}] {c['id']:<5} {LABELS.get(c['id'], ''):<28} {c['detail']}")
    for x in c["findings"][:25]:
        print(f"            - «{x.get('label','')}» {x.get('text','')}")
    if len(c["findings"]) > 25:
        print(f"            ... {len(c['findings']) - 25} more")
print("-" * 78)
print("SUMMARY:", json.dumps(counts))
sys.exit(1 if counts.get("FAIL") else 0)
