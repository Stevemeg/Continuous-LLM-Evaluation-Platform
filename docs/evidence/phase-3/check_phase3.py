"""Phase 3 comprehensive validation.

Regresses earlier phases, then checks what Phase 3 adds: domain coverage, data
standards, contract validity, and traceability.

The checks that carry this phase are structural assertions on the contract. A
prose statement that platform failure is never a quality verdict can drift from
the schema that implements it; an assertion that `GateOutcome` has no
`platform_failure` member cannot.

Usage: python check_phase3.py <repo_root>
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
PROD = ROOT / "docs" / "product"
DATA = ROOT / "docs" / "data"
API = ROOT / "docs" / "api"
EV3 = ROOT / "docs" / "evidence" / "phase-3"

PHASE2_TREE = "16894ab622578827760b509d5b941e8e3296bdc7"
IDENTITY = "Stevemeg <konabharath2004@gmail.com>"
DOCX_NAME = "Continuous LLM Evaluation Platform - Canonical Master Prompt v3.docx"
DOCX_SHA256 = "53329e77d527de517cad416785dff1aaeff83c2b9e475918acf16acfde33580f"

DATA_DOCS = ["domain-model.md", "data-model.md", "artifact-model.md"]

# Canonical §17 data model domains, as themes that must appear in the domain model.
CANON_17 = ["Organization", "Membership", "Role", "ServiceAccount", "ApiKey",
            "Project", "Environment", "Application",
            "Dataset", "DatasetVersion", "Example", "Label", "Lineage",
            "Prompt", "PromptVersion", "Model", "Provider", "ModelConfiguration",
            "BenchmarkSuite", "SuiteVersion", "EvaluatorDefinition", "EvaluatorVersion",
            "EvaluationPlan", "Run", "RunSample", "Metric",
            "JudgeRun", "JudgeVote", "ConsensusResult",
            "Baseline", "ComparisonResult", "GatePolicy", "GateDecision", "PolicyException",
            "Experiment", "Comparison", "ScheduledWorkflow",
            "Trace", "ToolCall", "ModelCall", "CostRecord",
            "AlertRule", "Report", "AuditEvent"]

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
    (r"\b\d+(\.\d+)?\s?(ms|milliseconds?|seconds?|secs?)\b", "latency figure"),
    (r"\b\d+\s?(rps|qps|tps)\b", "throughput"),
]
NEGATE = ["TARGET NOT YET SET", "NOT YET MEASURED", "must not", "will not", "never",
          "forbid", "reject", "unset", "not set", "invented", "no target",
          "latencyMs", "pattern"]

results = []


def add(cid, status, detail, findings=None):
    results.append({"id": cid, "status": status, "detail": detail, "findings": findings or []})


def git(*a, binary=False):
    r = subprocess.run(["git", "-C", str(ROOT)] + list(a), capture_output=True,
                       text=not binary, encoding=None if binary else "utf-8",
                       errors=None if binary else "replace")
    return r.stdout


data_texts = {d: (DATA / d).read_text(encoding="utf-8") for d in DATA_DOCS if (DATA / d).exists()}
api_design = (API / "api-design.md").read_text(encoding="utf-8") if (API / "api-design.md").exists() else ""
phase3_texts = dict(data_texts)
if api_design:
    phase3_texts["api-design.md"] = api_design

spec_path = API / "openapi.json"
spec = json.loads(spec_path.read_text(encoding="utf-8")) if spec_path.exists() else None

# ============================================================ S-1 phase 1 regression
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
add("S-1", "PASS" if not failed else "FAIL",
    "Phase 1 milestone validators re-run. " + "; ".join(rows), failed)

# ============================================================ S-2 phase 2 gate at its tree
tmp = Path(tempfile.mkdtemp(prefix="p2gate-"))
wt = tmp / "tree"
p2 = {"code": None, "note": ""}
try:
    r = subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach", str(wt), PHASE2_TREE],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        p2["note"] = f"worktree creation failed: {r.stderr.strip()[:120]}"
    else:
        src = ROOT / DOCX_NAME
        if src.exists():
            shutil.copy2(src, wt / DOCX_NAME)
        rr = subprocess.run([sys.executable, str(wt / "docs/evidence/phase-2/check_phase2.py"), str(wt)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", cwd=str(wt))
        p2["code"] = rr.returncode
        p2["note"] = next((l.replace("SUMMARY:", "").strip() for l in rr.stdout.splitlines()
                           if l.startswith("SUMMARY:")), "")
finally:
    subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)], capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
add("S-2", "PASS" if p2["code"] == 0 else "FAIL",
    f"Phase 2 phase-gate re-evaluated against the Phase 2 tree ({PHASE2_TREE[:8]}) in a "
    f"throwaway worktree: exit {p2['code']} {p2['note']}",
    [] if p2["code"] == 0 else [{"label": "phase-2 gate", "text": str(p2["note"])}])

# ============================================================ S-3 traceability
r = subprocess.run([sys.executable, str(EV3 / "generate_traceability.py"), str(ROOT)],
                   capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
tr_lines = [l.strip() for l in r.stdout.splitlines() if l.strip().startswith(("requirements defined",
            "traced to an artifact", "deferred with an owner"))]
add("S-3", "PASS" if r.returncode == 0 else "FAIL",
    f"traceability generator: exit {r.returncode}. " + "; ".join(tr_lines),
    [] if r.returncode == 0 else [{"label": "traceability", "text": "untracked or stale entries"}])

# ============================================================ S-4 matrix is current
matrix = EV3 / "traceability-matrix.md"
m_ok, m_note = False, "matrix missing"
if matrix.exists():
    before = matrix.read_bytes()
    subprocess.run([sys.executable, str(EV3 / "generate_traceability.py"), str(ROOT), "--write"],
                   capture_output=True, cwd=str(ROOT))
    after = matrix.read_bytes()
    m_ok = before == after
    m_note = "regenerates identically" if m_ok else "STALE - regeneration changes it"
    if not m_ok:
        matrix.write_bytes(after)
add("S-4", "PASS" if m_ok else "FAIL",
    f"generated traceability matrix {m_note}",
    [] if m_ok else [{"label": "matrix", "text": m_note}])

# ============================================================ S-5 canonical §17 coverage
dm = data_texts.get("domain-model.md", "")
missing_17 = [e for e in CANON_17 if e not in dm]
add("S-5", "PASS" if not missing_17 else "FAIL",
    f"canonical §17 entities present in the domain model: "
    f"{len(CANON_17) - len(missing_17)}/{len(CANON_17)}; missing: {missing_17 or 'none'}",
    [{"label": e, "text": "absent"} for e in missing_17])

# ============================================================ S-6 data standards present
dmod = data_texts.get("data-model.md", "")
required_sections = {
    "ERD": "erDiagram",
    "naming standards": "N-12",
    "tenancy rules": "P-7",
    "retention standards": "R-6",
    "data-volume model": "REQ-N-PERF-3",
    "accounting complexity analysis": "REQ-N-PERF-4",
}
missing_sec = [k for k, v in required_sections.items() if v not in dmod]
add("S-6", "PASS" if not missing_sec else "FAIL",
    f"required data-model sections present: {len(required_sections) - len(missing_sec)}/"
    f"{len(required_sections)}; missing: {missing_sec or 'none'}",
    [{"label": k, "text": "absent"} for k in missing_sec])

# ============================================================ S-7 invariant integrity
inv_defined = set(re.findall(r"^\| (I-\d+) \|", dm, re.M)) | set(re.findall(r"\*\*Invariant (I-\d+)\*\*", dm))
inv_ref = set()
for t in phase3_texts.values():
    inv_ref |= set(re.findall(r"\bI-\d+\b", t))
dangling_inv = sorted(inv_ref - inv_defined)
add("S-7", "PASS" if not dangling_inv else "FAIL",
    f"invariants defined: {len(inv_defined)}; referenced: {len(inv_ref)}; "
    f"referenced but undefined: {dangling_inv or 'none'}",
    [{"label": i, "text": "undefined"} for i in dangling_inv])

# ============================================================ S-8 OpenAPI validity
f8 = []
if spec is None:
    f8.append({"label": "spec", "text": "openapi.json missing"})
    detail8 = "spec missing"
else:
    if not str(spec.get("openapi", "")).startswith("3.1"):
        f8.append({"label": "version", "text": f"openapi {spec.get('openapi')}"})
    refs = set()

    def collect(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "$ref" and isinstance(v, str):
                    refs.add(v)
                else:
                    collect(v)
        elif isinstance(n, list):
            for i in n:
                collect(i)
    collect(spec)
    for ref in sorted(refs):
        if not ref.startswith("#/"):
            f8.append({"label": ref, "text": "non-local $ref"})
            continue
        cur = spec
        for part in ref[2:].split("/"):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                f8.append({"label": ref, "text": "unresolved $ref"})
                break
    ops = [(p, m, o) for p, pi in spec.get("paths", {}).items() for m, o in pi.items()
           if m in ("get", "post", "put", "patch", "delete")]
    ids = [o.get("operationId") for _, _, o in ops]
    for i in {x for x in ids if ids.count(x) > 1}:
        f8.append({"label": str(i), "text": "duplicate operationId"})
    for p, m, o in ops:
        if not o.get("operationId"):
            f8.append({"label": f"{m} {p}", "text": "missing operationId"})
        if "401" not in o.get("responses", {}):
            f8.append({"label": o.get("operationId", p), "text": "no 401 response declared"})
    if "bearerAuth" not in spec.get("components", {}).get("securitySchemes", {}):
        f8.append({"label": "security", "text": "no security scheme"})
    detail8 = (f"openapi {spec.get('openapi')}; {len(ops)} operations; "
               f"{len(spec.get('components', {}).get('schemas', {}))} schemas; "
               f"{len(refs)} $refs; defects: {len(f8)}")
add("S-8", "PASS" if not f8 else "FAIL", detail8, f8)

# ============================================================ S-9 contract structural guarantees
# These are the checks that carry the phase. Prose can drift from the schema; an
# assertion about enum membership cannot.
f9 = []
if spec:
    sch = spec.get("components", {}).get("schemas", {})

    def enum_of(name):
        return set(sch.get(name, {}).get("enum", []) or [])

    gate = enum_of("GateOutcome")
    if "platform_failure" in gate:
        f9.append({"label": "GateOutcome", "text": "contains platform_failure - a platform "
                                                   "failure must not be representable as a gate outcome"})
    if not {"pass", "hard_fail", "insufficient_evidence", "not_comparable"} <= gate:
        f9.append({"label": "GateOutcome", "text": f"missing required members: {gate}"})

    comp = enum_of("Completeness")
    if comp != {"complete", "partial", "exhausted", "cancelled", "rejected"}:
        f9.append({"label": "Completeness", "text": f"expected five states, got {sorted(comp)}"})

    cls = enum_of("Classification")
    if not {"insufficient_evidence", "not_comparable"} <= cls:
        f9.append({"label": "Classification", "text": "insufficient_evidence and not_comparable "
                                                      "must both be first-class outcomes"})

    prob = sch.get("Problem", {})
    if "platform_failure" not in (prob.get("properties", {}).get("category", {}).get("enum", []) or []):
        f9.append({"label": "Problem", "text": "category must be able to express platform_failure"})

    repro = enum_of("Reproducibility")
    if repro != {"reproducible", "auditable"}:
        f9.append({"label": "Reproducibility", "text": f"expected two states, got {sorted(repro)}"})

    sr = enum_of("SampleResolution")
    if "scored" not in sr or len(sr) < 3:
        f9.append({"label": "SampleResolution", "text": "must distinguish scored from non-scored outcomes"})

    ui = sch.get("UncertaintyInterval", {})
    if not {"lower", "upper", "confidenceLevel"} <= set(ui.get("required", []) or []):
        f9.append({"label": "UncertaintyInterval", "text": "bounds and confidence level must be required"})

    cr = sch.get("ComparisonResult", {})
    if "statisticalMethodVersion" not in (cr.get("required", []) or []):
        f9.append({"label": "ComparisonResult", "text": "statistical method version must be required"})

    pe = sch.get("PolicyException", {})
    if not {"actorId", "justification", "expiresAt"} <= set(pe.get("required", []) or []):
        f9.append({"label": "PolicyException", "text": "actor, justification and expiry must all be required"})

    dec = sch.get("Decimal", {})
    if dec.get("type") != "string":
        f9.append({"label": "Decimal", "text": "money and token quantities must not be JSON numbers"})

    # idempotency required on the two mutating entry points
    for opid in ("createRun", "evaluateGate"):
        found = False
        for p, pi in spec.get("paths", {}).items():
            for m, o in pi.items():
                if isinstance(o, dict) and o.get("operationId") == opid:
                    for prm in o.get("parameters", []):
                        if prm.get("$ref", "").endswith("/idempotencyKey"):
                            found = True
        if not found:
            f9.append({"label": opid, "text": "Idempotency-Key not required"})
else:
    f9.append({"label": "spec", "text": "missing"})
add("S-9", "PASS" if not f9 else "FAIL",
    f"structural contract guarantees checked: 11; violations: {len(f9)}", f9)

# ============================================================ S-10 requirement references
req_defined = set()
for pat in [r"REQ-F-\d{2}-\d+", r"REQ-F-AG-\d+", r"REQ-X-\d+", r"REQ-N-[A-Z]+-\d+"]:
    req_defined |= set(re.findall(r"`(" + pat + r")`",
                                 (PROD / "requirements.md").read_text(encoding="utf-8")))
dangling = []
for d, t in phase3_texts.items():
    for ref in set(re.findall(r"\bREQ-(?:F-\d{2}-\d+|F-AG-\d+|X-\d+|N-[A-Z]+-\d+)\b", t)):
        if ref not in req_defined:
            dangling.append({"label": d, "text": f"undefined {ref}"})
if spec:
    api_reqs = set()

    def w2(n):
        if isinstance(n, dict):
            for k, v in n.items():
                if k == "x-requirements":
                    api_reqs.update(v or [])
                else:
                    w2(v)
        elif isinstance(n, list):
            for i in n:
                w2(i)
    w2(spec)
    for ref in sorted(api_reqs - req_defined):
        dangling.append({"label": "openapi.json", "text": f"undefined {ref}"})
add("S-10", "PASS" if not dangling else "FAIL",
    f"requirement references in Phase 3 artifacts; undefined: {len(dangling)}", dangling)

# ============================================================ S-11 invented metrics
inv = []
for d, t in phase3_texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        for rx, lb in INVENTED:
            for m in re.finditer(rx, line, re.I):
                if any(w in line for w in NEGATE):
                    continue
                inv.append({"label": f"{d}:{n}", "text": f"{lb}: {m.group(0)!r}"})
add("S-11", "PASS" if not inv else "FAIL", f"{len(inv)} invented figure(s)", inv)

# ============================================================ S-12 links
broken, nlinks = [], 0
for base, texts in ((DATA, data_texts), (API, {"api-design.md": api_design} if api_design else {})):
    for d, t in texts.items():
        for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", t):
            tgt = m.group(2)
            if tgt.startswith(("http", "mailto:")):
                continue
            nlinks += 1
            if not (base / tgt).resolve().exists():
                broken.append({"label": d, "text": tgt})
add("S-12", "PASS" if not broken else "FAIL",
    f"{nlinks} relative link(s) checked; broken: {len(broken)}", broken)

# ============================================================ S-13 placeholders
ph = []
for d, t in phase3_texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        for rx, lb in [(r"\bTODO\b", "TODO"), (r"\bFIXME\b", "FIXME"), (r"\bTBD\b", "TBD")]:
            if re.search(rx, line):
                ph.append({"label": f"{d}:{n}", "text": lb})
add("S-13", "PASS" if not ph else "FAIL", f"{len(ph)} placeholder marker(s)", ph)

# ============================================================ S-14 secrets
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
add("S-14", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} match(es)", sec + blob_sec)

# ============================================================ S-15 attribution
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
for d, t in phase3_texts.items():
    for rx in ATT:
        if re.search(rx, t, re.I):
            att.append({"label": d, "text": "attribution in a Phase 3 artifact"})
if spec_path.exists():
    st = spec_path.read_text(encoding="utf-8")
    for rx in ATT:
        if re.search(rx, st, re.I):
            att.append({"label": "openapi.json", "text": "attribution in the contract"})
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
add("S-15", "PASS" if not uniq else "FAIL",
    f"governed scope: {len(uniq)} match(es). Superseded blobs on local-only recovery refs, "
    f"disclosed: {len(local_only)} ({', '.join(local_only) if local_only else 'none'})", uniq)

# ============================================================ S-16 identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()))
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()))
add("S-16", "PASS" if authors == [IDENTITY] and committers == [IDENTITY] else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ============================================================ S-17 canonical document
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
add("S-17", "PASS" if not f17 else "FAIL",
    f"local present={docx.exists()} unchanged={digest == DOCX_SHA256} ignored={ignored} "
    f"tracked=False absent_from_main={not pub}", f17)

# ============================================================ S-18 hygiene
status = [l for l in git("status", "--short").splitlines() if l.strip()]
untracked = [l for l in status if l.startswith("??")]
tracked = git("ls-files").splitlines()
stray = [t for t in tracked if re.search(r"\.(tmp|log|bak|orig|rej|swp|pyc)$", t)
         or "__pycache__" in t]
add("S-18", "PASS" if not stray and not untracked else "FAIL",
    f"{len(tracked)} tracked file(s); stray: {len(stray)}; untracked-and-unignored: "
    f"{len(untracked)}; clean tree: {not status}",
    [{"label": s, "text": "stray"} for s in stray] +
    [{"label": u, "text": "untracked"} for u in untracked])

# ============================================================ S-19 phase boundary
# Phase 3 specifies; it does not implement. An OpenAPI contract and a mermaid ERD
# are specifications. Application source, migrations and dependency manifests are not.
PHASE4_PLUS = [
    ("application source", ["src/**/*.py", "app/**/*.py", "backend/**/*.py", "**/*.ts", "**/*.tsx"]),
    ("database migrations", ["**/migrations/**/*", "**/alembic.ini", "**/*.sql"]),
    ("dependency manifests", ["pyproject.toml", "package.json", "requirements.txt",
                              "uv.lock", "poetry.lock", "setup.py"]),
    ("container or infra definitions", ["Dockerfile*", "docker-compose*.yml", "**/*.tf",
                                        "**/Chart.yaml"]),
    ("test suites", ["tests/**/*", "**/test_*.py", "**/*_test.py"]),
]
leaked = []
for label, pats in PHASE4_PLUS:
    for pat in pats:
        for hit in ROOT.glob(pat):
            rel = hit.relative_to(ROOT).as_posix()
            parts = Path(rel).parts
            if parts and parts[0].startswith("."):
                continue
            if rel.startswith("docs/evidence/"):
                continue
            leaked.append({"label": label, "text": rel})
add("S-19", "PASS" if not leaked else "FAIL",
    f"Phase 4+ artifact classes checked: {len(PHASE4_PLUS)}; artifacts Phase 3 must not "
    f"contain: {len(leaked)}", leaked)

# ============================================================ output
counts = {}
for c in results:
    counts[c["status"]] = counts.get(c["status"], 0) + 1
LABELS = {
    "S-1": "phase 1 regression", "S-2": "phase 2 gate at its tree",
    "S-3": "traceability", "S-4": "matrix currency", "S-5": "canonical §17 coverage",
    "S-6": "data standards", "S-7": "invariant integrity", "S-8": "OpenAPI validity",
    "S-9": "contract guarantees", "S-10": "requirement references",
    "S-11": "invented metrics", "S-12": "links", "S-13": "placeholders",
    "S-14": "secrets", "S-15": "attribution", "S-16": "git identity",
    "S-17": "canonical document", "S-18": "hygiene", "S-19": "phase boundary",
}
print("=" * 78)
print("PHASE 3 COMPREHENSIVE VALIDATION")
print("=" * 78)
for c in results:
    print(f"[{c['status']:<7}] {c['id']:<5} {LABELS.get(c['id'], ''):<26} {c['detail']}")
    for x in c["findings"][:25]:
        print(f"            - «{x.get('label','')}» {x.get('text','')}")
    if len(c["findings"]) > 25:
        print(f"            ... {len(c['findings']) - 25} more")
print("-" * 78)
print("SUMMARY:", json.dumps(counts))
sys.exit(1 if counts.get("FAIL") else 0)
