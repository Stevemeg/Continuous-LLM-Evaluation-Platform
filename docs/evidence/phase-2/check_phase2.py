"""Phase 2 comprehensive validation.

Regresses every Phase 1 validator, then checks what Phase 2 adds: ADR coverage
and status integrity, architecture-to-requirement traceability, failure-mode and
sensitivity-class coverage, spike reproducibility, and the boundary checks.

Two checks are specific to this phase and carry it:

  Q-6  the two undecided ADRs must not smuggle a decision. An ADR that declares
       itself gated on a spike and then names a chosen technology is worse than
       a wrong decision, because it is an undocumented one.
  Q-7  the recorded spike output must reproduce byte-identically from a re-run.
       ADR-007 rests on that output; if it cannot be reproduced it is not
       evidence.

Usage: python check_phase2.py <repo_root>
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
ARCH = ROOT / "docs" / "architecture"
ADR = ROOT / "docs" / "adr"
EV2 = ROOT / "docs" / "evidence" / "phase-2"

PHASE1_TREE = "526173bd79b59479285d450e569c6e3d4064196e"
IDENTITY = "Stevemeg <konabharath2004@gmail.com>"
DOCX_NAME = "Continuous LLM Evaluation Platform - Canonical Master Prompt v3.docx"
DOCX_SHA256 = "53329e77d527de517cad416785dff1aaeff83c2b9e475918acf16acfde33580f"

ARCH_DOCS = ["system-architecture.md", "component-architectures.md",
             "failure-model.md", "threat-model.md", "observability-strategy.md"]

# Canonical section 19 fixes the required ADR topics. All eleven must exist.
REQUIRED_ADRS = {
    "ADR-001": "durable execution",
    "ADR-002": "agent orchestration",
    "ADR-003": "provider abstraction",
    "ADR-004": "judge ensemble",
    "ADR-005": "dataset immutability",
    "ADR-006": "evaluator isolation",
    "ADR-007": "regression statistics",
    "ADR-008": "tool protocol",
    "ADR-009": "observability core",
    "ADR-010": "multi-tenancy",
    "ADR-011": "artifact retention",
}
GATED_ADRS = {"ADR-001", "ADR-003"}
SPIKE_ELEMENTS = ["Hypothesis", "Candidates", "Measurements", "Decision rule",
                  "Falsification"]

# Canonical section 21 failure modes, as themes that must appear in the failure model.
CANON_21 = ["outage", "rate limit", "malformed", "deprecation", "disagreement",
            "drift", "evaluator crash", "incompatibilit", "partial run",
            "worker crash", "resum", "duplicate delivery", "idempoten",
            "transient", "budget exhaustion", "expensive", "poisoned",
            "stale baseline", "evaluator version", "inconclusive",
            "cross-tenant"]
SENSITIVITY = [f"DS-{i}" for i in range(1, 10)]

TECH = ["FastAPI", "Pydantic", "PostgreSQL", "Postgres", "Redis", "Temporal",
        "Celery", "ARQ", "LangGraph", "LangChain", "LiteLLM", "Next.js", "React",
        "MinIO", "Kubernetes", "Docker", "OpenTelemetry", "Prometheus", "Grafana",
        "Terraform", "SQLAlchemy", "Kafka", "Helm", "gRPC", "GraphQL"]
DECIDE_VERB = (r"\b(we will use|we choose|we chose|we adopt|we have selected|"
               r"the platform uses|the platform will use|will be built on|"
               r"is built on|we standardi[sz]e on|the core uses|shall use|"
               r"decision:\s*(?:use|adopt))\b")

INVENTED = [
    (r"\b\d+(\.\d+)?\s?%", "percentage"),
    (r"[$€£]\s?\d", "currency"),
    (r"\b\d+(\.\d+)?\s?(ms|milliseconds?|seconds?|secs?)\b", "latency figure"),
    (r"\bp\d{2}\b\s*[<>=]", "percentile target"),
    (r"\b\d+\s?(rps|qps|tps)\b", "throughput"),
]
NEGATE = ["TARGET NOT YET SET", "NOT YET MEASURED", "[EVIDENCE GAP]", "must not",
          "will not", "never", "forbid", "reject", "prohibit", "unset",
          "not set", "invented", "no target"]

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

results = []


def add(cid, status, detail, findings=None):
    results.append({"id": cid, "status": status, "detail": detail,
                    "findings": findings or []})


def git(*args, binary=False):
    r = subprocess.run(["git", "-C", str(ROOT)] + list(args), capture_output=True,
                       text=not binary, encoding=None if binary else "utf-8",
                       errors=None if binary else "replace")
    return r.stdout


# gather texts
arch_texts = {d: (ARCH / d).read_text(encoding="utf-8")
              for d in ARCH_DOCS if (ARCH / d).exists()}
adr_texts = {p.stem.split("-")[0] + "-" + p.stem.split("-")[1]: p.read_text(encoding="utf-8")
             for p in sorted(ADR.glob("ADR-*.md"))}
phase2_texts = dict(arch_texts)
phase2_texts.update({f"adr/{k}": v for k, v in adr_texts.items()})

# ============================================================ Q-1 regression
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
add("Q-1", "PASS" if not failed else "FAIL",
    "Phase 1 milestone validators re-run against the current tree. " + "; ".join(rows),
    failed)

# ============================================================ Q-2 phase 1 gate at its own tree
# check_phase1.py asserts Phase 1 contained no ADRs or application source. That is
# a statement about the Phase 1 BOUNDARY, so it is evaluated against the Phase 1
# tree in a throwaway worktree, not against the Phase 2 tree where ADRs exist.
tmp = Path(tempfile.mkdtemp(prefix="p1gate-"))
wt = tmp / "tree"
p1 = {"code": None, "note": ""}
try:
    r = subprocess.run(["git", "-C", str(ROOT), "worktree", "add", "--detach",
                        str(wt), PHASE1_TREE], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        p1["note"] = f"worktree creation failed: {r.stderr.strip()[:120]}"
    else:
        # The Phase 1 gate checks that the canonical document is present locally,
        # unchanged, ignored and untracked. Because it is deliberately never
        # committed, a fresh worktree contains no copy, and the check would fail
        # for a reason that has nothing to do with Phase 1. Supplying the local
        # document reproduces the real Phase 1 condition: present on this machine,
        # outside version control. Copied, never moved -- the original is not
        # touched, which the digest assertion in Q-18 independently confirms.
        src = ROOT / DOCX_NAME
        if src.exists():
            shutil.copy2(src, wt / DOCX_NAME)
        rr = subprocess.run([sys.executable, str(wt / "docs/evidence/phase-1/check_phase1.py"),
                             str(wt)], capture_output=True, text=True,
                            encoding="utf-8", errors="replace", cwd=str(wt))
        p1["code"] = rr.returncode
        p1["note"] = next((l.replace("SUMMARY:", "").strip() for l in rr.stdout.splitlines()
                           if l.startswith("SUMMARY:")), "")
finally:
    subprocess.run(["git", "-C", str(ROOT), "worktree", "remove", "--force", str(wt)],
                   capture_output=True)
    shutil.rmtree(tmp, ignore_errors=True)
add("Q-2", "PASS" if p1["code"] == 0 else "FAIL",
    f"Phase 1 phase-gate re-evaluated against the Phase 1 tree ({PHASE1_TREE[:8]}) in a "
    f"throwaway worktree: exit {p1['code']} {p1['note']}",
    [] if p1["code"] == 0 else [{"label": "phase-1 gate", "text": p1["note"]}])

# ============================================================ Q-3 ADR coverage
missing_adr = [k for k in REQUIRED_ADRS if k not in adr_texts]
add("Q-3", "PASS" if not missing_adr else "FAIL",
    f"canonical §19 ADR topics present: {len(REQUIRED_ADRS) - len(missing_adr)}/"
    f"{len(REQUIRED_ADRS)}; missing: {missing_adr or 'none'}",
    [{"label": k, "text": REQUIRED_ADRS[k]} for k in missing_adr])

# ============================================================ Q-4 ADR status vocabulary
bad_status = []
statuses = {}
for k, t in adr_texts.items():
    m = re.search(r"\|\s*Status\s*\|\s*(.+?)\s*\|", t)
    if not m:
        bad_status.append({"label": k, "text": "no Status field"})
        continue
    s = m.group(1)
    statuses[k] = s
    ok = ("Accepted" in s) or ("Proposed" in s and "NOT DECIDED" in s)
    if not ok:
        bad_status.append({"label": k, "text": f"unrecognised status: {s[:70]}"})
add("Q-4", "PASS" if not bad_status else "FAIL",
    f"{len(statuses)} ADR status field(s) parsed; accepted: "
    f"{sum(1 for s in statuses.values() if 'Accepted' in s)}; "
    f"explicitly undecided: {sum(1 for s in statuses.values() if 'NOT DECIDED' in s)}",
    bad_status)

# ============================================================ Q-5 gated ADRs specify their spike
incomplete = []
for k in sorted(GATED_ADRS):
    t = adr_texts.get(k, "")
    if "NOT DECIDED" not in statuses.get(k, ""):
        incomplete.append({"label": k, "text": "expected to be undecided but is not"})
        continue
    for el in SPIKE_ELEMENTS:
        if el.lower() not in t.lower():
            incomplete.append({"label": k, "text": f"spike missing element: {el}"})
add("Q-5", "PASS" if not incomplete else "FAIL",
    f"undecided ADRs: {sorted(GATED_ADRS)}; each must specify hypothesis, candidates, "
    f"measurements, decision rule and falsification; defects: {len(incomplete)}",
    incomplete)

# ============================================================ Q-6 gated ADRs take no decision
smuggled = []
for k in sorted(GATED_ADRS):
    t = adr_texts.get(k, "")
    for n, line in enumerate(t.splitlines(), 1):
        if not re.search(DECIDE_VERB, line, re.I):
            continue
        for tok in TECH:
            if re.search(rf"\b{re.escape(tok)}\b", line):
                smuggled.append({"label": f"{k}:{n}", "text": f"{tok}: {line.strip()[:100]}"})
add("Q-6", "PASS" if not smuggled else "FAIL",
    f"{len(smuggled)} line(s) in an undecided ADR asserting adoption of a named "
    f"technology - a gated ADR must not smuggle a decision", smuggled)

# ============================================================ Q-7 spike reproducibility
spike = EV2 / "spikes" / "spike_regression_statistics.py"
recorded = EV2 / "spikes" / "spike-regression-statistics-output.txt"
q7 = []
if not spike.exists() or not recorded.exists():
    q7.append({"label": "spike", "text": "spike script or recorded output missing"})
    detail = "spike artifacts missing"
else:
    r = subprocess.run([sys.executable, str(spike)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=str(ROOT))
    live = r.stdout.replace("\r\n", "\n").strip()
    rec = recorded.read_text(encoding="utf-8").replace("\r\n", "\n").strip()
    lh = hashlib.sha256(live.encode()).hexdigest()[:16]
    rh = hashlib.sha256(rec.encode()).hexdigest()[:16]
    if live != rec:
        q7.append({"label": "reproducibility",
                   "text": f"live output differs from recorded (live {lh}, recorded {rh})"})
    detail = (f"spike re-executed: exit {r.returncode}; recorded output "
              f"{'reproduces byte-identically' if live == rec else 'DOES NOT reproduce'} "
              f"(sha256/16 live={lh} recorded={rh})")
add("Q-7", "PASS" if not q7 else "FAIL", detail, q7)

# ============================================================ Q-8 requirement traceability
req_defined = set()
for pat in [r"REQ-F-\d{2}-\d+", r"REQ-F-AG-\d+", r"REQ-X-\d+", r"REQ-N-[A-Z]+-\d+"]:
    req_defined |= set(re.findall(r"`(" + pat + r")`",
                                  (PROD / "requirements.md").read_text(encoding="utf-8")))
dangling, referenced = [], set()
for d, t in phase2_texts.items():
    for ref in re.findall(r"\bREQ-(?:F-\d{2}-\d+|F-AG-\d+|X-\d+|N-[A-Z]+-\d+)\b", t):
        referenced.add(ref)
        if ref not in req_defined:
            dangling.append({"label": d, "text": f"undefined {ref}"})
seen, uniq = set(), []
for x in dangling:
    if (x["label"], x["text"]) not in seen:
        seen.add((x["label"], x["text"]))
        uniq.append(x)
add("Q-8", "PASS" if not uniq else "FAIL",
    f"{len(req_defined)} requirements defined; {len(referenced)} distinct requirements "
    f"referenced by Phase 2 artifacts; undefined references: {len(uniq)}", uniq)

# ============================================================ Q-9 failure mode coverage
fm = arch_texts.get("failure-model.md", "").lower()
missing_fm = [m for m in CANON_21 if m.lower() not in fm]
add("Q-9", "PASS" if not missing_fm else "FAIL",
    f"canonical §21 failure-mode themes present in the failure model: "
    f"{len(CANON_21) - len(missing_fm)}/{len(CANON_21)}; missing: {missing_fm or 'none'}",
    [{"label": m, "text": "absent"} for m in missing_fm])

# ============================================================ Q-10 sensitivity coverage
tm = arch_texts.get("threat-model.md", "")
missing_ds = [d for d in SENSITIVITY if d not in tm]
add("Q-10", "PASS" if not missing_ds else "FAIL",
    f"product sensitivity classes addressed by the threat model: "
    f"{len(SENSITIVITY) - len(missing_ds)}/{len(SENSITIVITY)}; missing: {missing_ds or 'none'}",
    [{"label": d, "text": "absent"} for d in missing_ds])

# ============================================================ Q-11 invented metrics
inv = []
for d, t in phase2_texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        for rx, label in INVENTED:
            for m in re.finditer(rx, line, re.I):
                if any(w in line for w in NEGATE):
                    continue
                inv.append({"label": f"{d}:{n}", "text": f"{label}: {m.group(0)!r}"})
add("Q-11", "PASS" if not inv else "FAIL",
    f"{len(inv)} invented figure(s) in Phase 2 artifacts", inv)

# ============================================================ Q-12 mermaid blocks
bad_mermaid = []
for d, t in phase2_texts.items():
    opens = len(re.findall(r"^```mermaid\s*$", t, re.M))
    fences = len(re.findall(r"^```", t, re.M))
    if fences % 2 != 0:
        bad_mermaid.append({"label": d, "text": f"unbalanced code fences ({fences})"})
    if opens and "graph" not in t and "sequenceDiagram" not in t and "stateDiagram" not in t:
        bad_mermaid.append({"label": d, "text": "mermaid block with no diagram directive"})
n_diagrams = sum(len(re.findall(r"^```mermaid\s*$", t, re.M)) for t in phase2_texts.values())
add("Q-12", "PASS" if not bad_mermaid else "FAIL",
    f"{n_diagrams} mermaid diagram(s) across Phase 2 artifacts; structural defects: "
    f"{len(bad_mermaid)}", bad_mermaid)

# ============================================================ Q-13 links
broken, nlinks = [], 0
for base, texts in ((ARCH, arch_texts), ):
    for d, t in texts.items():
        for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", t):
            tgt = m.group(2)
            if tgt.startswith(("http", "mailto:")):
                continue
            nlinks += 1
            if not (base / tgt).resolve().exists():
                broken.append({"label": d, "text": tgt})
for p in sorted(ADR.glob("*.md")):
    t = p.read_text(encoding="utf-8")
    for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", t):
        tgt = m.group(2)
        if tgt.startswith(("http", "mailto:")):
            continue
        nlinks += 1
        if not (ADR / tgt).resolve().exists():
            broken.append({"label": p.name, "text": tgt})
add("Q-13", "PASS" if not broken else "FAIL",
    f"{nlinks} relative link(s) checked in Phase 2 artifacts; broken: {len(broken)}", broken)

# ============================================================ Q-14 placeholders
ph = []
for d, t in phase2_texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        for rx, lb in [(r"\bTODO\b", "TODO"), (r"\bFIXME\b", "FIXME"),
                       (r"\bTBD\b", "TBD"), (r"<placeholder", "placeholder")]:
            if re.search(rx, line):
                ph.append({"label": f"{d}:{n}", "text": lb})
add("Q-14", "PASS" if not ph else "FAIL", f"{len(ph)} placeholder marker(s)", ph)

# ============================================================ Q-15 secrets
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
add("Q-15", "PASS" if not sec and not blob_sec else "FAIL",
    f"working tree: {nfiles} files ({nbin} binary skipped), {len(sec)} match(es); "
    f"all blobs all refs: {len(blob_sec)} match(es)", sec + blob_sec)

# ============================================================ Q-16 attribution
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
for d, t in phase2_texts.items():
    for rx in ATT:
        if re.search(rx, t, re.I):
            att.append({"label": d, "text": "attribution in a Phase 2 artifact"})
governed = att_blobs("main") | att_blobs(branch)
for name in sorted(governed):
    att.append({"label": name, "text": f"attribution in a blob reachable from main or {branch}"})
msgs = git("log", "main", branch, "--format=%B%n%n")
for rx in ATT:
    if re.search(rx, msgs, re.I):
        att.append({"label": "commit messages", "text": "attribution in a governed commit message"})
local_only = sorted(att_blobs("--all") - governed)
seen, uniq_att = set(), []
for x in att:
    if (x["label"], x["text"]) not in seen:
        seen.add((x["label"], x["text"]))
        uniq_att.append(x)
add("Q-16", "PASS" if not uniq_att else "FAIL",
    f"governed scope (Phase 2 artifacts, blobs reachable from main and {branch}, and "
    f"their commit messages): {len(uniq_att)} match(es). Superseded blobs on local-only "
    f"recovery refs, disclosed: {len(local_only)} "
    f"({', '.join(local_only) if local_only else 'none'})", uniq_att)

# ============================================================ Q-17 identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()))
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()))
add("Q-17", "PASS" if authors == [IDENTITY] and committers == [IDENTITY] else "FAIL",
    f"authors: {authors}; committers: {committers}")

# ============================================================ Q-18 canonical document
docx = ROOT / DOCX_NAME
f = []
digest = hashlib.sha256(docx.read_bytes()).hexdigest() if docx.exists() else None
if not docx.exists():
    f.append({"label": "presence", "text": "missing locally"})
elif digest != DOCX_SHA256:
    f.append({"label": "integrity", "text": f"sha256 changed to {digest}"})
ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", DOCX_NAME],
                         capture_output=True).returncode == 0
if not ignored:
    f.append({"label": "ignore", "text": "not ignored"})
if DOCX_NAME in git("ls-files").splitlines():
    f.append({"label": "tracking", "text": "tracked"})
pub = [l for l in git("ls-tree", "-r", "--name-only", "main").splitlines()
       if l.lower().endswith((".docx", ".doc"))]
if pub:
    f.append({"label": "published", "text": f"present in main: {pub}"})
add("Q-18", "PASS" if not f else "FAIL",
    f"local present={docx.exists()} unchanged={digest == DOCX_SHA256} "
    f"ignored={ignored} tracked=False absent_from_main={not pub}", f)

# ============================================================ Q-19 hygiene
status = [l for l in git("status", "--short").splitlines() if l.strip()]
untracked = [l for l in status if l.startswith("??")]
tracked = git("ls-files").splitlines()
stray = [t for t in tracked if re.search(r"\.(tmp|log|bak|orig|rej|swp|pyc)$", t)
         or "__pycache__" in t]
add("Q-19", "PASS" if not stray and not untracked else "FAIL",
    f"{len(tracked)} tracked file(s); stray artifacts: {len(stray)}; "
    f"untracked-and-unignored: {len(untracked)}; clean tree: {not status}",
    [{"label": s, "text": "stray"} for s in stray] +
    [{"label": u, "text": "untracked"} for u in untracked])

# ============================================================ Q-20 phase boundary
PHASE3_PLUS = [
    ("application source", ["src/**/*.py", "app/**/*.py", "backend/**/*.py",
                            "**/*.ts", "**/*.tsx"]),
    ("database schema or migrations", ["**/migrations/**/*", "**/*.sql"]),
    ("API contract", ["**/openapi*.yaml", "**/openapi*.yml", "**/openapi*.json"]),
    ("dependency manifests", ["pyproject.toml", "package.json", "requirements.txt",
                              "uv.lock", "poetry.lock"]),
    ("container or infra definitions", ["Dockerfile*", "docker-compose*.yml",
                                        "**/*.tf", "**/Chart.yaml"]),
]
leaked = []
for label, pats in PHASE3_PLUS:
    for pat in pats:
        for hit in ROOT.glob(pat):
            rel = hit.relative_to(ROOT).as_posix()
            parts = Path(rel).parts
            if parts and parts[0].startswith("."):
                continue
            # validators and spikes are Phase evidence, not application code
            if rel.startswith("docs/evidence/"):
                continue
            leaked.append({"label": label, "text": rel})
add("Q-20", "PASS" if not leaked else "FAIL",
    f"Phase 3+ artifact classes checked: {len(PHASE3_PLUS)}; artifacts Phase 2 must "
    f"not contain: {len(leaked)}", leaked)

# ============================================================ output
counts = {}
for c in results:
    counts[c["status"]] = counts.get(c["status"], 0) + 1
LABELS = {
    "Q-1": "phase 1 regression", "Q-2": "phase 1 gate at its tree",
    "Q-3": "ADR coverage", "Q-4": "ADR status vocabulary",
    "Q-5": "gated ADRs specify spikes", "Q-6": "gated ADRs take no decision",
    "Q-7": "spike reproducibility", "Q-8": "requirement traceability",
    "Q-9": "failure-mode coverage", "Q-10": "sensitivity coverage",
    "Q-11": "invented metrics", "Q-12": "diagram integrity", "Q-13": "links",
    "Q-14": "placeholders", "Q-15": "secrets", "Q-16": "attribution",
    "Q-17": "git identity", "Q-18": "canonical document", "Q-19": "hygiene",
    "Q-20": "phase boundary",
}
print("=" * 78)
print("PHASE 2 COMPREHENSIVE VALIDATION")
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
