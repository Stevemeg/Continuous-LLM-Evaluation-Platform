"""Phase 1 comprehensive validation.

Runs every Phase 1 milestone validator, then the phase-level checks that no
single milestone owns: canonical coverage, cross-document identifier integrity,
repository hygiene, canonical-document handling, and the two boundary checks
that Phase 1 did not silently take an architecture decision or implement
Phase 2+ scope.

Usage: python check_phase1.py <repo_root>
Exits non-zero on any FAIL.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1]).resolve()
PROD = ROOT / "docs" / "product"

PUBLISHED_REF = "main"
IDENTITY = "Stevemeg <konabharath2004@gmail.com>"
DOCX_NAME = "Continuous LLM Evaluation Platform - Canonical Master Prompt v3.docx"
DOCX_SHA256 = "53329e77d527de517cad416785dff1aaeff83c2b9e475918acf16acfde33580f"

M1_DOCS = ["prd.md", "personas.md", "use-cases.md", "success-criteria.md", "non-goals.md"]
M2_DOCS = ["competitive-analysis.md", "positioning.md"]
M3_DOCS = ["requirements.md"]
ALL_DOCS = M1_DOCS + M2_DOCS + M3_DOCS

VALIDATORS = [
    ("M1.1 document validation", "docs/evidence/M1.1/check_m11.py"),
    ("M1.1 cross-document consistency", "docs/evidence/M1.1/check_crossrefs.py"),
    ("M1.2 competitive analysis & positioning", "docs/evidence/M1.2/check_m12.py"),
    ("M1.3 requirements", "docs/evidence/M1.3/check_m13.py"),
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


texts = {}
for d in ALL_DOCS:
    p = PROD / d
    if p.exists():
        texts[d] = p.read_text(encoding="utf-8")

# ============================================================ P-1 validators
rows, failed = [], []
for name, rel in VALIDATORS:
    p = ROOT / rel
    if not p.exists():
        failed.append({"label": name, "text": f"{rel} missing"})
        continue
    r = subprocess.run([sys.executable, str(p), str(ROOT)], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    summary = ""
    for line in r.stdout.splitlines():
        if line.startswith("SUMMARY:"):
            summary = line.replace("SUMMARY:", "").strip()
        if line.startswith("PASS —") or line.startswith("PASS -"):
            summary = summary or "PASS"
    rows.append((name, rel, r.returncode, summary))
    if r.returncode != 0:
        failed.append({"label": name, "text": f"exit {r.returncode}"})
detail = "; ".join(f"{n}: exit {c} {s}" for n, _, c, s in rows)
add("P-1", "PASS" if not failed else "FAIL",
    f"{len(rows)} milestone validator(s) executed. {detail}", failed)

# ============================================================ P-2 canonical coverage
# Canonical section 23 defines Phase 1; section 18 names the documents.
CANON_PHASE1 = {
    "product requirements (PRD)": ["prd.md"],
    "personas": ["personas.md"],
    "use cases": ["use-cases.md"],
    "measurable success criteria": ["success-criteria.md"],
    "competitor research": ["competitive-analysis.md"],
    "SRS / functional and non-functional requirements": ["requirements.md"],
    "product positioning": ["positioning.md"],
    "explicit non-goals": ["non-goals.md"],
}
missing_canon = []
for item, docs in CANON_PHASE1.items():
    for d in docs:
        if d not in texts:
            missing_canon.append({"label": item, "text": f"{d} absent"})
add("P-2", "PASS" if not missing_canon else "FAIL",
    f"canonical Phase 1 requirements with a resulting artifact: "
    f"{len(CANON_PHASE1) - len(missing_canon)}/{len(CANON_PHASE1)}", missing_canon)

# ============================================================ P-3 identifier integrity
# Every identifier referenced anywhere in Phase 1 must be defined by the document
# that owns it. This is the cross-document check no milestone validator can make.
defined = {
    "CAP": {f"CAP-{i:02d}" for i in range(1, 13)},
    "UC": {f"UC-{i:02d}" for i in range(1, 19)},
    "U": {f"U-{i}" for i in range(1, 7)},
    "X": {f"X-{i}" for i in range(1, 11)},
}
defined["SC"] = set(re.findall(r"SC-[A-Z]\d+", texts.get("success-criteria.md", "")))
defined["REQ"] = set()
for pat in [r"REQ-F-\d{2}-\d+", r"REQ-F-AG-\d+", r"REQ-X-\d+", r"REQ-N-[A-Z]+-\d+"]:
    defined["REQ"] |= set(re.findall(r"`(" + pat + r")`", texts.get("requirements.md", "")))
defined["P"] = set(re.findall(r"\bP-(\d)\b", texts.get("positioning.md", "")))
defined["S"] = set(re.findall(r"`(S-\d{2})`", (ROOT / "docs/evidence/M1.2/sources.md").read_text(encoding="utf-8")
                              if (ROOT / "docs/evidence/M1.2/sources.md").exists() else ""))

REF_PATTERNS = {
    "CAP": r"\bCAP-\d{2}\b",
    "UC": r"\bUC-\d{2}\b",
    "X": r"(?<![A-Za-z-])X-\d+\b",
    "SC": r"\bSC-[A-Z]\d+\b",
    "REQ": r"\bREQ-(?:F-\d{2}-\d+|F-AG-\d+|X-\d+|N-[A-Z]+-\d+)\b",
}
dangling = []
for d, t in texts.items():
    for kind, rx in REF_PATTERNS.items():
        for m in re.finditer(rx, t):
            ref = m.group(0)
            if ref not in defined[kind]:
                dangling.append({"label": f"{d}", "text": f"undefined {ref}"})
# de-duplicate
seen, uniq = set(), []
for x in dangling:
    k = (x["label"], x["text"])
    if k not in seen:
        seen.add(k)
        uniq.append(x)
add("P-3", "PASS" if not uniq else "FAIL",
    f"identifier references checked across {len(texts)} documents; "
    f"CAP {len(defined['CAP'])}, UC {len(defined['UC'])}, U {len(defined['U'])}, "
    f"X {len(defined['X'])}, SC {len(defined['SC'])}, REQ {len(defined['REQ'])} defined; "
    f"undefined references: {len(uniq)}", uniq)

# ============================================================ P-4 links
broken, nlinks = [], 0
for d, t in texts.items():
    for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", t):
        target = m.group(2)
        if target.startswith(("http", "mailto:")):
            continue
        nlinks += 1
        if not (PROD / target).resolve().exists():
            broken.append({"label": d, "text": target})
for ev in ROOT.glob("docs/evidence/*/*.md"):
    t = ev.read_text(encoding="utf-8")
    for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", t):
        target = m.group(2)
        if target.startswith(("http", "mailto:")):
            continue
        nlinks += 1
        if not (ev.parent / target).resolve().exists():
            broken.append({"label": str(ev.relative_to(ROOT)), "text": target})
add("P-4", "PASS" if not broken else "FAIL",
    f"{nlinks} relative link(s) checked across product and evidence documents; "
    f"broken: {len(broken)}", broken)

# ============================================================ P-5 placeholders
ph = []
for d, t in texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        for rx, label in [(r"\bTODO\b", "TODO"), (r"\bFIXME\b", "FIXME"),
                          (r"\bTBD\b", "TBD"), (r"\bLorem ipsum\b", "lorem"),
                          (r"<placeholder", "placeholder")]:
            if re.search(rx, line):
                ph.append({"label": f"{d}:{n}", "text": label})
add("P-5", "PASS" if not ph else "FAIL", f"{len(ph)} placeholder marker(s)", ph)

# ============================================================ P-6 unsupported claims
# Comparative or measurement claims are legitimate only where the line negates,
# prohibits, quotes the canonical specification, or records an evidence gap.
CLAIMS = [
    (r"reduc(e|ed|es|ing)\s+\w+\s+by\b", "reduction claim"),
    (r"improv(e|ed|es|ing)\s+\w+\s+by\b", "improvement claim"),
    (r"\b(faster|cheaper|better|more accurate)\s+than\b", "comparative claim"),
    (r"\bwe (measured|observed|achieved)\b", "measurement claim"),
    (r"\bbenchmark(ed|s)? (show|showed|prove|proves)\b", "benchmark claim"),
    (r"\bindustry[- ]leading\b|\bbest[- ]in[- ]class\b", "superlative"),
    (r"\bproduction[- ]ready\b|\benterprise[- ]grade\b", "readiness claim"),
]
NEGATE = ["[EVIDENCE GAP]", "[CANON §", "NOT YET MEASURED", "TARGET NOT YET SET",
          "prohibit", "Prohibit", "must not", "will not", "never", "reject",
          "forbid", "false", "unproven", "unavailable", "withheld", "unevidenced",
          "without reproducible evidence", "not verifiable", "no measured",
          "No measured", "cannot be made", "not asserted", "would be false"]
claims_open = []
for d, t in texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        for rx, label in CLAIMS:
            if re.search(rx, line, re.I) and not any(w in line for w in NEGATE):
                claims_open.append({"label": f"{d}:{n}", "text": f"{label}: {line.strip()[:110]}"})
add("P-6", "PASS" if not claims_open else "FAIL",
    f"{len(claims_open)} outstanding unsupported-claim match(es)", claims_open)

# ============================================================ P-7 invented metrics
METRICS = [
    (r"\b\d+(\.\d+)?\s?%", "percentage"),
    (r"[$€£]\s?\d", "currency"),
    (r"\b\d+(\.\d+)?\s?(ms|milliseconds?|seconds?|secs?)\b", "latency figure"),
    (r"\bp\d{2}\b\s*[<>=]", "percentile target"),
    (r"\b\d+(\.\d+)?\s?x\b", "multiplier"),
    (r"\b\d+\s?(rps|qps|tps)\b", "throughput"),
]
metric_open = []
for d, t in texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        for rx, label in METRICS:
            for m in re.finditer(rx, line, re.I):
                if any(w in line for w in NEGATE):
                    continue
                metric_open.append({"label": f"{d}:{n}", "text": f"{label}: {m.group(0)!r} in {line.strip()[:90]}"})
add("P-7", "PASS" if not metric_open else "FAIL",
    f"{len(metric_open)} outstanding invented-metric match(es)", metric_open)

# ============================================================ P-8 secrets
SECRETS = [
    (r"sk-[A-Za-z0-9]{16,}", "provider key"),
    (r"gh[pousr]_[A-Za-z0-9]{16,}", "forge token"),
    (r"AKIA[0-9A-Z]{16}", "cloud key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "JWT"),
    (r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]", "inline credential"),
    (r"(?i)://[^/\s:@]+:[^/\s:@]+@", "credential in URL"),
]
sec_hits, nfiles, nbin = [], 0, 0
for p in sorted(ROOT.rglob("*")):
    if not p.is_file() or ".git" in p.parts:
        continue
    nfiles += 1
    try:
        t = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        nbin += 1
        continue
    for rx, label in SECRETS:
        if re.search(rx, t):
            sec_hits.append({"label": str(p.relative_to(ROOT)), "text": label})
blob_sec = []
for line in git("rev-list", "--objects", "--all").splitlines():
    parts = line.split(maxsplit=1)
    sha = parts[0]
    if git("cat-file", "-t", sha).strip() != "blob":
        continue
    raw = git("cat-file", "blob", sha, binary=True)
    try:
        t = raw.decode("utf-8")
    except UnicodeDecodeError:
        continue
    for rx, label in SECRETS:
        if re.search(rx, t):
            blob_sec.append({"label": parts[1] if len(parts) > 1 else sha[:8], "text": label})
add("P-8", "PASS" if not sec_hits and not blob_sec else "FAIL",
    f"working tree: {nfiles} files scanned ({nbin} binary skipped), {len(sec_hits)} match(es); "
    f"all git blobs across all refs: {len(blob_sec)} match(es)", sec_hits + blob_sec)

# ============================================================ P-9 attribution
_F = ["Co-Authored" + "-By", "Anthro" + "pic", "Cla" + "ude", "Cop" + "ilot",
      "Approved" + "-by", "Assisted" + "-by", "Reviewed" + "-by"]
ATT = [rf"\b{f}\b" for f in _F] + [r"AI[- ]assist(ed|ant)", r"AI[- ]" + "generated",
                                   "generated" + r"\s+with"]
# Scope matters here, and collapsing it would misreport the result.
#
# The zero-tolerance requirement applies to what is published and to what is
# being proposed for publication: the product documents, the published ref, and
# the phase branch. Superseded blobs on a purely local recovery branch are a
# different thing -- they are unreachable from anything published, cannot be
# pushed without an explicit push of that branch, and exist only because the
# granular M1.1 history was deliberately retained locally after the squash.
# They are reported separately and disclosed rather than being either hidden or
# counted as a published-history failure.
def blobs_of(ref):
    found = {}
    for line in git("rev-list", "--objects", ref).splitlines():
        parts = line.split(maxsplit=1)
        sha = parts[0]
        if git("cat-file", "-t", sha).strip() != "blob":
            continue
        try:
            t = git("cat-file", "blob", sha, binary=True).decode("utf-8")
        except UnicodeDecodeError:
            continue
        name = parts[1] if len(parts) > 1 else sha[:8]
        for rx in ATT:
            if re.search(rx, t, re.I):
                found[(name, sha)] = True
    return found


att = []
for d, t in texts.items():
    for rx in ATT:
        if re.search(rx, t, re.I):
            att.append({"label": d, "text": "attribution in product document"})

phase_ref = git("rev-parse", "--abbrev-ref", "HEAD").strip()
governed = blobs_of(PUBLISHED_REF)
governed.update(blobs_of(phase_ref))
for (name, sha) in sorted(governed):
    att.append({"label": name, "text": f"attribution in a blob reachable from "
                                       f"{PUBLISHED_REF} or {phase_ref}"})

msgs = git("log", PUBLISHED_REF, phase_ref, "--format=%B%n%n")
for rx in ATT:
    if re.search(rx, msgs, re.I):
        att.append({"label": "commit messages", "text": "attribution in a governed commit message"})

all_blobs = blobs_of("--all")
local_only = sorted({n for (n, s) in all_blobs} - {n for (n, s) in governed})

seen, uniq_att = set(), []
for x in att:
    k = (x["label"], x["text"])
    if k not in seen:
        seen.add(k)
        uniq_att.append(x)
add("P-9", "PASS" if not uniq_att else "FAIL",
    f"governed scope (product documents, blobs reachable from {PUBLISHED_REF} and "
    f"{phase_ref}, and their commit messages): {len(uniq_att)} match(es). "
    f"Superseded blobs on local-only recovery refs, disclosed and unpublishable: "
    f"{len(local_only)} ({', '.join(local_only) if local_only else 'none'})",
    uniq_att)

# ============================================================ P-10 git identity
authors = sorted(set(git("log", "--all", "--format=%an <%ae>").splitlines()))
committers = sorted(set(git("log", "--all", "--format=%cn <%ce>").splitlines()))
ident_ok = authors == [IDENTITY] and committers == [IDENTITY]
add("P-10", "PASS" if ident_ok else "FAIL",
    f"authors: {authors}; committers: {committers}; expected sole identity: [{IDENTITY!r}]")

# ============================================================ P-11 hygiene
tracked = git("ls-files").splitlines()
status = [l for l in git("status", "--short").splitlines() if l.strip()]
untracked_unignored = [l for l in status if l.startswith("??")]
stray = [t for t in tracked if re.search(r"\.(tmp|log|bak|orig|rej|swp|pyc)$", t)
         or "__pycache__" in t or t.startswith(".venv")]
add("P-11", "PASS" if not stray and not untracked_unignored else "FAIL",
    f"{len(tracked)} tracked file(s); stray build/scratch artifacts tracked: {len(stray)}; "
    f"untracked-and-unignored paths: {len(untracked_unignored)}; "
    f"working tree clean: {not status}",
    [{"label": s, "text": "stray"} for s in stray] +
    [{"label": u, "text": "untracked"} for u in untracked_unignored])

# ============================================================ P-12 canonical docx
docx = ROOT / DOCX_NAME
findings = []
if not docx.exists():
    findings.append({"label": "presence", "text": "canonical document missing locally"})
    digest = size = None
else:
    raw = docx.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    size = len(raw)
    if digest != DOCX_SHA256:
        findings.append({"label": "integrity", "text": f"sha256 changed: {digest}"})
ignored = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", DOCX_NAME],
                         capture_output=True).returncode == 0
if not ignored:
    findings.append({"label": "ignore", "text": "not covered by an ignore rule"})
if DOCX_NAME in tracked:
    findings.append({"label": "tracking", "text": "still tracked by git"})
pub_docx = [l for l in git("ls-tree", "-r", "--name-only", PUBLISHED_REF).splitlines()
            if l.lower().endswith((".docx", ".doc"))]
if pub_docx:
    findings.append({"label": "published history", "text": f"present in {PUBLISHED_REF}: {pub_docx}"})
allref_docx = [l.split(maxsplit=1)[1] for l in git("rev-list", "--objects", PUBLISHED_REF).splitlines()
               if len(l.split(maxsplit=1)) > 1 and l.split(maxsplit=1)[1].lower().endswith((".docx", ".doc"))]
if allref_docx:
    findings.append({"label": "published objects", "text": f"docx blob reachable from {PUBLISHED_REF}"})
add("P-12", "PASS" if not findings else "FAIL",
    f"local: present={docx.exists()} size={size} sha256={(digest or '')[:16]}… "
    f"unchanged={digest == DOCX_SHA256}; ignored={ignored}; tracked={DOCX_NAME in tracked}; "
    f"absent from {PUBLISHED_REF}={not pub_docx and not allref_docx}", findings)

# ============================================================ P-13 no silent architecture decision
OUR_DECISION = (r"\b(we will use|we choose|we chose|we adopt|we have selected|"
                r"this product uses|the platform uses|the platform will use|"
                r"will be built on|is built on|we standardi[sz]e on|the core uses|"
                r"our stack|shall use|shall be implemented (?:in|with|using))\b")
TECH = ["FastAPI", "Pydantic", "PostgreSQL", "Postgres", "Redis", "Temporal", "Celery",
        "ARQ", "LangGraph", "LangChain", "LiteLLM", "Next.js", "React", "MinIO",
        "Kubernetes", "Docker", "OpenTelemetry", "Prometheus", "Grafana", "Terraform",
        "SQLAlchemy", "Kafka", "Helm", "gRPC", "GraphQL"]
arch = []
for d, t in texts.items():
    for n, line in enumerate(t.splitlines(), 1):
        if not re.search(OUR_DECISION, line, re.I):
            continue
        for tok in TECH:
            if re.search(rf"\b{re.escape(tok)}\b", line):
                arch.append({"label": f"{d}:{n}", "text": f"{tok}: {line.strip()[:110]}"})
add("P-13", "PASS" if not arch else "FAIL",
    f"{len(arch)} line(s) asserting adoption of a named technology for this system",
    arch)

# ============================================================ P-14 no Phase 2+ scope
PHASE2_PATTERNS = [
    ("application source", ["src/**/*.py", "app/**/*.py", "backend/**/*.py",
                            "**/*.ts", "**/*.tsx", "**/*.js"]),
    ("architecture decision records", ["docs/adr/**/*", "adr/**/*", "**/ADR-*.md"]),
    ("database schema or migrations", ["**/migrations/**/*", "**/*.sql", "**/alembic.ini"]),
    ("API contract", ["**/openapi*.yaml", "**/openapi*.yml", "**/openapi*.json"]),
    ("dependency manifests", ["pyproject.toml", "package.json", "requirements.txt",
                              "uv.lock", "poetry.lock"]),
    ("container or infra definitions", ["Dockerfile*", "docker-compose*.yml",
                                        "**/*.tf", "**/Chart.yaml"]),
]
leaked = []
for label, pats in PHASE2_PATTERNS:
    for pat in pats:
        for hit in ROOT.glob(pat):
            rel = hit.relative_to(ROOT).as_posix()
            parts = Path(rel).parts
            # Skip hidden top-level directories: version-control internals and
            # machine-local editor or tool state are not application artifacts.
            # Expressed as a general rule rather than by naming a tool, so this
            # file does not itself contain the strings the attribution scan
            # forbids -- which is exactly what it did on the first run.
            if parts and parts[0].startswith("."):
                continue
            # the milestone validators are Phase 1 evidence, not application code
            if rel.startswith("docs/evidence/"):
                continue
            leaked.append({"label": label, "text": rel})
add("P-14", "PASS" if not leaked else "FAIL",
    f"Phase 2+ artifact classes checked: {len(PHASE2_PATTERNS)}; "
    f"artifacts found that Phase 1 must not contain: {len(leaked)}", leaked)

# ============================================================ output
counts = {}
for c in results:
    counts[c["status"]] = counts.get(c["status"], 0) + 1

print("=" * 78)
print("PHASE 1 COMPREHENSIVE VALIDATION")
print("=" * 78)
LABELS = {
    "P-1": "milestone validators", "P-2": "canonical coverage",
    "P-3": "identifier integrity", "P-4": "links", "P-5": "placeholders",
    "P-6": "unsupported claims", "P-7": "invented metrics", "P-8": "secrets",
    "P-9": "attribution", "P-10": "git identity", "P-11": "repository hygiene",
    "P-12": "canonical document", "P-13": "architecture boundary",
    "P-14": "phase boundary",
}
for c in results:
    print(f"[{c['status']:<7}] {c['id']:<6} {LABELS.get(c['id'], ''):<24} {c['detail']}")
    for f in c["findings"][:25]:
        print(f"            - «{f.get('label','')}» {f.get('text','')}")
    if len(c["findings"]) > 25:
        print(f"            ... {len(c['findings']) - 25} more")
print("-" * 78)
print("SUMMARY:", json.dumps(counts))
sys.exit(1 if counts.get("FAIL") else 0)
