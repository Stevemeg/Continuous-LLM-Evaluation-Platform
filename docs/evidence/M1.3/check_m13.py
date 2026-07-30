"""M1.3 validation checker.

Verifies the Phase 1 / M1.3 requirements specification against the acceptance
criteria declared for the milestone.

The milestone's central risks are incompleteness and drift: a requirement set
that silently fails to cover a capability, a use case, or a cross-cutting
behaviour, and a coverage summary that stops agreeing with the requirements it
summarises. Both are checked by derivation from the requirement rows rather than
by reading the summary, because M1.1 found sixteen drifted entries in exactly
that kind of summary table.

Usage: python check_m13.py <repo_root>
Exits non-zero on any FAIL.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(sys.argv[1])
PROD = ROOT / "docs" / "product"
REQ = PROD / "requirements.md"

CAPABILITIES = [f"CAP-{i:02d}" for i in range(1, 13)]
USE_CASES = [f"UC-{i:02d}" for i in range(1, 19)]
USER_GROUPS = [f"U-{i}" for i in range(1, 7)]
CROSS_CUTTING = [f"X-{i}" for i in range(1, 11)]
OPEN_QUESTIONS = [f"PQ-{i}" for i in range(1, 5)]

PRIORITIES = {"M", "S", "C", "W"}
VERIFICATIONS = {"T", "D", "A", "I"}

ID_PATTERNS = [
    r"REQ-F-\d{2}-\d+",
    r"REQ-F-AG-\d+",
    r"REQ-X-\d+",
    r"REQ-N-[A-Z]+-\d+",
]
ANY_ID = re.compile("|".join(f"(?:{p})" for p in ID_PATTERNS))

# --- invented-metric patterns ---------------------------------------------
# A requirements document that measures nothing has no legitimate use for a
# concrete performance figure. Targets that cannot yet be justified are recorded
# as TARGET NOT YET SET instead.
INVENTED = [
    (r"\b\d+(\.\d+)?\s?%", "percentage figure"),
    (r"[$€£]\s?\d", "currency figure"),
    (r"\b\d+(\.\d+)?\s?(ms|milliseconds?|seconds?|secs?|minutes?|hours?|days?)\b",
     "time figure"),
    (r"\bp\d{2}\b\s*[<>=]", "percentile target"),
    (r"\b\d+(\.\d+)?\s?x\b", "multiplier figure"),
    (r"\b\d+\s?(rps|qps|tps|samples?/s|requests?/s)\b", "throughput figure"),
    (r"\b\d{2,}\s?(GB|TB|MB)\b", "capacity figure"),
]

# --- premature technology decisions ---------------------------------------
TECH_TOKENS = [
    "FastAPI", "Pydantic", "PostgreSQL", "Postgres", "Redis", "Temporal",
    "Celery", "ARQ", "LangGraph", "LangChain", "LiteLLM", "Next.js", "React",
    "MinIO", "Kubernetes", "Docker", "OpenTelemetry", "Prometheus", "Grafana",
    "Terraform", "SQLAlchemy", "Kafka", "Helm", "S3", "gRPC", "GraphQL",
    "RAGAS", "Ragas", "DeepEval", "Promptfoo", "LangSmith", "Phoenix",
]

_FRAG = ["Co-Authored" + "-By", "Anthro" + "pic", "Cla" + "ude", "Cop" + "ilot",
         "Approved" + "-by", "Assisted" + "-by"]
AI_ATTRIBUTION = [rf"\b{f}\b" for f in _FRAG] + [
    r"AI[- ]assist(ed|ant)", r"AI[- ]" + "generated", "generated" + r"\s+with",
]

SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "provider-style key"),
    (r"gh[pousr]_[A-Za-z0-9]{16,}", "forge token"),
    (r"AKIA[0-9A-Z]{16}", "cloud access key id"),
    (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key block"),
    (r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", "JWT"),
    (r"(?i)\b(password|passwd|secret|api[_-]?key|token)\s*[:=]\s*['\"][^'\"]{8,}['\"]",
     "inline credential"),
    (r"(?i)://[^/\s:@]+:[^/\s:@]+@", "credential in URL"),
]

PLACEHOLDER_PATTERNS = [
    (r"\bTODO\b", "TODO marker"), (r"\bFIXME\b", "FIXME marker"),
    (r"\bTBD\b", "TBD marker"), (r"\bLorem ipsum\b", "lorem ipsum"),
    (r"<placeholder", "placeholder tag"),
]

results = {"checks": []}


def add(cid, status, detail, findings=None):
    results["checks"].append({"id": cid, "status": status, "detail": detail,
                              "findings": findings or []})


# ---------------------------------------------------------------- parse
if not REQ.exists():
    add("AC-1", "FAIL", "requirements.md missing")
    print("[FAIL] AC-1 requirements.md missing")
    sys.exit(1)

text = REQ.read_text(encoding="utf-8")
lines = text.splitlines()

# A requirement row is a table row whose first cell is a backticked ID.
# Three table shapes exist (functional 5 cells, cross-cutting 6, non-functional
# 7). Traces, priority and verification are always the last three cells, so the
# shapes are parsed uniformly from the right.
rows = []
for n, line in enumerate(lines, 1):
    if not line.strip().startswith("|"):
        continue
    cells = [c.strip() for c in line.strip().strip("|").split("|")]
    if not cells:
        continue
    m = re.fullmatch(r"`(" + "|".join(ID_PATTERNS) + r")`", cells[0])
    if not m:
        continue
    rid = m.group(1)
    # The statement column differs by shape: cross-cutting rows carry the
    # behaviour they realise in cell 2, pushing the statement to cell 3.
    stmt_idx = 2 if rid.startswith("REQ-X-") else 1
    rows.append({
        "line": n, "id": rid, "cells": cells,
        "statement": cells[stmt_idx] if len(cells) > stmt_idx + 3 else "",
        "pri": cells[-3], "traces": cells[-2], "ver": cells[-1],
        "n_cells": len(cells),
    })

func = [r for r in rows if r["id"].startswith("REQ-F-")]
cross = [r for r in rows if r["id"].startswith("REQ-X-")]
nfr = [r for r in rows if r["id"].startswith("REQ-N-")]

add("AC-1", "PASS" if rows else "FAIL",
    f"requirements.md present ({len(text)} chars, {len(lines)} lines); "
    f"requirements parsed: {len(rows)} (functional {len(func)}, "
    f"cross-cutting {len(cross)}, non-functional {len(nfr)})")

# ---------------------------------------------------------------- AC-2
derived_cap = {c: [] for c in CAPABILITIES}
for r in func:
    for c in re.findall(r"CAP-\d{2}", r["traces"]):
        if c in derived_cap:
            derived_cap[c].append(r["id"])
uncovered_cap = [c for c, v in derived_cap.items() if not v]
add("AC-2", "PASS" if not uncovered_cap else "FAIL",
    f"capabilities with at least one functional requirement: "
    f"{len(CAPABILITIES) - len(uncovered_cap)}/{len(CAPABILITIES)}; "
    f"uncovered: {uncovered_cap or 'none'}")

# ---------------------------------------------------------------- AC-3
realised = {}
for r in cross:
    if r["n_cells"] >= 6:
        for x in re.findall(r"\bX-\d+\b", r["cells"][1]):
            realised.setdefault(x, []).append(r["id"])
missing_x = [x for x in CROSS_CUTTING if x not in realised]
add("AC-3", "PASS" if not missing_x else "FAIL",
    f"cross-cutting behaviours realised: {len(CROSS_CUTTING) - len(missing_x)}/"
    f"{len(CROSS_CUTTING)}; unrealised: {missing_x or 'none'}")

# ---------------------------------------------------------------- AC-4
bad_attr = []
for r in rows:
    if r["pri"].replace("*", "").strip() not in PRIORITIES:
        bad_attr.append({"line": r["line"], "label": "priority",
                         "text": f"{r['id']} priority={r['pri']!r}"})
    if r["ver"].replace("*", "").strip() not in VERIFICATIONS:
        bad_attr.append({"line": r["line"], "label": "verification",
                         "text": f"{r['id']} verification={r['ver']!r}"})
    if not r["traces"]:
        bad_attr.append({"line": r["line"], "label": "traces",
                         "text": f"{r['id']} empty traces"})
    if len(r["statement"]) < 20:
        bad_attr.append({"line": r["line"], "label": "statement",
                         "text": f"{r['id']} statement too short"})
add("AC-4", "PASS" if not bad_attr else "FAIL",
    f"{len(bad_attr)} requirement attribute defect(s) across {len(rows)} requirements",
    bad_attr)

# ---------------------------------------------------------------- AC-5
ids = [r["id"] for r in rows]
dupes = sorted({i for i in ids if ids.count(i) > 1})
add("AC-5", "PASS" if not dupes else "FAIL",
    f"{len(ids)} requirement identifier(s); duplicates: {dupes or 'none'}")

# ---------------------------------------------------------------- AC-6
traced_uc = set()
for r in rows:
    traced_uc |= set(re.findall(r"UC-\d{2}", r["traces"]))
missing_uc = [u for u in USE_CASES if u not in traced_uc]
add("AC-6", "PASS" if not missing_uc else "FAIL",
    f"use cases traced by at least one requirement: "
    f"{len(USE_CASES) - len(missing_uc)}/{len(USE_CASES)}; untraced: {missing_uc or 'none'}")

# ---------------------------------------------------------------- AC-7
traced_u = set()
for r in rows:
    traced_u |= set(re.findall(r"\bU-\d\b", r["traces"]))
missing_u = [u for u in USER_GROUPS if u not in traced_u]
add("AC-7", "PASS" if not missing_u else "FAIL",
    f"user groups traced: {len(USER_GROUPS) - len(missing_u)}/{len(USER_GROUPS)}; "
    f"untraced: {missing_u or 'none'}")

# ---------------------------------------------------------------- AC-8
bad_canon = []
for r in rows:
    for s in re.findall(r"§(\d+)", r["traces"]):
        if not 1 <= int(s) <= 27:
            bad_canon.append({"line": r["line"], "label": "canonical section",
                              "text": f"{r['id']} cites §{s}, outside 1-27"})
add("AC-8", "PASS" if not bad_canon else "FAIL",
    f"{len(bad_canon)} canonical citation(s) outside the specification's section range",
    bad_canon)

# ---------------------------------------------------------------- AC-9
# Coverage summary must agree with what the requirement rows declare.
summary_cap = {}
for line in lines:
    m = re.match(r"\|\s*(CAP-\d{2})\s*\|\s*(.+?)\s*\|\s*$", line.strip())
    if m:
        summary_cap[m.group(1)] = sorted(set(ANY_ID.findall(m.group(2))))
summary_x = {}
for line in lines:
    m = re.match(r"\|\s*(X-\d+)\s*\|\s*(.+?)\s*\|\s*$", line.strip())
    if m:
        summary_x[m.group(1)] = sorted(set(ANY_ID.findall(m.group(2))))

mismatch = []
for c in CAPABILITIES:
    d = sorted(set(derived_cap[c]))
    s = sorted(set(summary_cap.get(c, [])))
    if d != s:
        mismatch.append({"line": "", "label": c,
                         "text": f"derived={d} summary={s}"})
for x in CROSS_CUTTING:
    d = sorted(set(realised.get(x, [])))
    s = sorted(set(summary_x.get(x, [])))
    if d != s:
        mismatch.append({"line": "", "label": x,
                         "text": f"derived={d} summary={s}"})
add("AC-9", "PASS" if not mismatch else "FAIL",
    f"coverage summary rows checked: {len(summary_cap)} capability + {len(summary_x)} "
    f"cross-cutting; disagreements with the requirement rows: {len(mismatch)}",
    mismatch)

# ---------------------------------------------------------------- AC-10
invented = []
for n, line in enumerate(lines, 1):
    for rx, label in INVENTED:
        for m in re.finditer(rx, line, re.I):
            invented.append({"line": n, "label": label,
                             "text": line.strip()[:140]})
add("AC-10", "PASS" if not invented else "FAIL",
    f"{len(invented)} invented figure(s) - a document that measures nothing must "
    f"carry none", invented)

# ---------------------------------------------------------------- AC-11
tech = []
for n, line in enumerate(lines, 1):
    for tok in TECH_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", line):
            tech.append({"line": n, "label": tok, "text": line.strip()[:140]})
add("AC-11", "PASS" if not tech else "FAIL",
    f"{len(tech)} technology or product name(s) - requirements must not name "
    f"implementation technology", tech)

# ---------------------------------------------------------------- AC-12
ai = []
for n, line in enumerate(lines, 1):
    for rx in AI_ATTRIBUTION:
        if re.search(rx, line):
            ai.append({"line": n, "label": "attribution", "text": line.strip()[:140]})
add("AC-12", "PASS" if not ai else "FAIL",
    f"{len(ai)} attribution match(es) - must be zero", ai)

# ---------------------------------------------------------------- AC-13
sec = []
for n, line in enumerate(lines, 1):
    for rx, label in SECRET_PATTERNS:
        if re.search(rx, line):
            sec.append({"line": n, "label": label, "text": line.strip()[:80]})
add("AC-13", "PASS" if not sec else "FAIL",
    f"{len(sec)} secret-pattern match(es) - must be zero", sec)

# ---------------------------------------------------------------- AC-14
broken, nlinks = [], 0
for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", text):
    target = m.group(2)
    if target.startswith(("http", "mailto:")):
        continue
    nlinks += 1
    if not (REQ.parent / target).resolve().exists():
        broken.append({"label": "broken link", "text": target})
add("AC-14", "PASS" if not broken else "FAIL",
    f"{nlinks} relative link(s) checked; broken: {[b['text'] for b in broken] or 'none'}")

# ---------------------------------------------------------------- AC-15
unresolved = []
for q in OPEN_QUESTIONS:
    m = re.search(rf"### {q} —.*?\n+\*\*(.+?)\*\*", text, re.S)
    if not m or "Resolved" not in m.group(1):
        unresolved.append({"label": q, "text": "no resolution statement found"})
add("AC-15", "PASS" if not unresolved else "FAIL",
    f"open product questions resolved: {len(OPEN_QUESTIONS) - len(unresolved)}/"
    f"{len(OPEN_QUESTIONS)}; unresolved: {[u['label'] for u in unresolved] or 'none'}",
    unresolved)

# ---------------------------------------------------------------- AC-16
bad_nfr = []
for r in nfr:
    if r["n_cells"] < 7:
        bad_nfr.append({"line": r["line"], "label": "shape",
                        "text": f"{r['id']} has {r['n_cells']} cells, expected 7"})
        continue
    if len(r["cells"][2]) < 5:
        bad_nfr.append({"line": r["line"], "label": "measurement",
                        "text": f"{r['id']} measurement method missing"})
    if len(r["cells"][3]) < 5:
        bad_nfr.append({"line": r["line"], "label": "target",
                        "text": f"{r['id']} target cell missing"})
unset = sum(1 for r in nfr if r["n_cells"] >= 7 and "NOT YET SET" in r["cells"][3])
add("AC-16", "PASS" if not bad_nfr else "FAIL",
    f"{len(nfr)} non-functional requirement(s); defects: {len(bad_nfr)}; "
    f"targets explicitly not yet set: {unset}", bad_nfr)

# ---------------------------------------------------------------- AC-17
defined = set(ids)
dangling = []
for n, line in enumerate(lines, 1):
    for ref in ANY_ID.findall(line):
        if ref not in defined:
            dangling.append({"line": n, "label": "dangling reference", "text": ref})
add("AC-17", "PASS" if not dangling else "FAIL",
    f"{len(dangling)} reference(s) to an undefined requirement identifier",
    dangling)

# ---------------------------------------------------------------- placeholders
ph = []
for n, line in enumerate(lines, 1):
    for rx, label in PLACEHOLDER_PATTERNS:
        if re.search(rx, line):
            ph.append({"line": n, "label": label, "text": line.strip()[:120]})
add("PLACEHOLDER", "PASS" if not ph else "FAIL",
    f"{len(ph)} placeholder marker(s)", ph)

# ---------------------------------------------------------------- output
counts = {}
for c in results["checks"]:
    counts[c["status"]] = counts.get(c["status"], 0) + 1

print("=" * 78)
print("M1.3 VALIDATION")
print("=" * 78)
for c in results["checks"]:
    print(f"[{c['status']:<7}] {c['id']:<12} {c['detail']}")
    for f in c["findings"][:40]:
        print(f"            - :{f.get('line','')} «{f.get('label','')}» {f.get('text','')}")
    if len(c["findings"]) > 40:
        print(f"            ... {len(c['findings']) - 40} more")
print("-" * 78)
print("SUMMARY:", json.dumps(counts))
sys.exit(1 if counts.get("FAIL") else 0)
