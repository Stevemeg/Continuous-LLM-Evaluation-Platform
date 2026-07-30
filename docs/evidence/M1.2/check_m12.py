"""M1.2 validation checker.

Verifies the Phase 1 / M1.2 competitive analysis and positioning documents
against the acceptance criteria declared for the milestone.

The milestone's central risk is fabrication: inventing competitor capabilities,
weaknesses, pricing, or metrics. Prose review cannot reliably catch that, so the
checks below make the two failure modes mechanical:

  - a factual claim about a competitor that cites no source (AC-3, AC-5)
  - an assertion that a competitor LACKS something, which the available evidence
    can never establish, because documentation silence is not absence (AC-11)

Usage: python check_m12.py <repo_root>
Exits non-zero on any FAIL.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(sys.argv[1])
PROD = ROOT / "docs" / "product"
EVID = ROOT / "docs" / "evidence" / "M1.2"

DOCS = {
    "competitive_analysis": PROD / "competitive-analysis.md",
    "positioning": PROD / "positioning.md",
}
SOURCES = EVID / "sources.md"

# --- claim taxonomy --------------------------------------------------------
MARKERS = ["[VERIFIED", "[OBSERVATION]", "[POSITIONING]", "[ASSUMPTION]",
           "[RECOMMENDATION]", "[EVIDENCE GAP]", "[CANON §"]

# --- AC-2: the competitor set that must be analysed -----------------------
# The first six are named by the canonical specification; the remainder were
# added because the canonical set is weighted toward libraries while the product
# is a platform (see competitive-analysis.md section 1.1).
CANONICAL_COMPETITORS = ["Ragas", "DeepEval", "Promptfoo", "OpenAI Evals",
                         "LangSmith", "Phoenix"]
ADDED_COMPETITORS = ["Langfuse", "Braintrust", "Confident AI"]
COMPETITORS = CANONICAL_COMPETITORS + ADDED_COMPETITORS

# --- AC-6: fabrication patterns -------------------------------------------
# Unconditional: no figure of these shapes may appear at all. There is no
# legitimate reason for one in a document that measures nothing.
HARD_NUMERIC = [
    (r"\b\d+(\.\d+)?\s?%", "percentage figure"),
    (r"\b\d+(\.\d+)?\s?x\b", "multiplier claim"),
    (r"[$€£]\s?\d", "currency figure"),
    (r"\b\d+(\.\d+)?\s?(k|m|bn|billion|million|thousand)\b\s*(users|customers|"
     r"developers|companies|teams|downloads|stars|installs|seats|requests)",
     "adoption figure"),
    (r"\b\d+(\.\d+)?\s?%?\s*(market share|mindshare)", "market-share figure"),
    (r"\b(seats?|per\s+seat|per\s+user|per\s+month|/mo\b|/month\b)\s*[:=]?\s*[$€£]?\s*\d",
     "pricing figure"),
]
# Conditional: legitimate only when the line is prohibiting, negating, or
# recording the absence of such a claim.
SOFT_CLAIM = [
    (r"reduc(e|ed|es|ing)\s+\w+\s+by\b", "reduction claim"),
    (r"improv(e|ed|es|ing)\s+\w+\s+by\b", "improvement claim"),
    (r"\b(faster|cheaper|better|more accurate)\s+than\b", "comparative claim"),
    (r"\bwe (measured|observed|achieved)\b", "measurement claim"),
    (r"\bbenchmark(ed|s)? (show|showed|prove|proves)", "benchmark claim"),
    (r"\bindustry[- ]leading\b|\bbest[- ]in[- ]class\b|\bmarket[- ]leading\b",
     "superlative claim"),
]
NEGATING = ["[EVIDENCE GAP]", "prohibit", "Prohibit", "must not", "will not",
            "never", "reject", "forbid", "false", "unavailable", "unproven",
            "no measured", "No measured", "not verifiable", "withheld",
            "unevidenced", "without reproducible evidence", "cannot be made"]

# --- AC-11: absence assertions --------------------------------------------
# Silence in documentation cannot establish that a product lacks a capability.
# Any such assertion about a named competitor must be qualified.
#
# Two deliberate scoping decisions, so this check stays precise enough to be
# worth enforcing:
#
#   1. "mention" is excluded (see ABSENCE_OK). "X does not mention Y" is a
#      checkable statement about a retrieved page, which is the ONLY form of
#      negative statement this analysis is entitled to make, and the form the
#      rule-1 worked example depends on. The forbidden form is a claim about
#      the product -- "X lacks Y", "X cannot Y" -- which is what remains below.
#   2. Bare "without" is not matched. It is overwhelmingly used in legitimate
#      constructions ("DeepEval runs without it", "without reproducible
#      evidence") and matching it produced only false positives. Absence
#      claims phrased with "without" are instead caught by pairing it with a
#      governance capability noun, which is where such a claim would bite.
ABSENCE = (r"\b(does not|doesn't|do not|don't|lacks?|lacking|cannot|can't|"
           r"has no|have no|provides no|offers no|no support for|unable to|"
           r"fails to|missing|absent|omits)\b"
           r"|\bwithout\s+(RBAC|authentication|versioning|approvals?|audit|"
           r"governance|multi-tenancy|access control|isolation)\b")
ABSENCE_OK = ["mention", "[EVIDENCE GAP]", "not established", "not found", "silence",
              "would be false", "is false", "must not", "will not", "never",
              "prohibit", "Prohibit", "rule 1", "Rule 1", "no conclusion",
              "not proof", "not absence", "not the same", "unknown to this",
              "recorded rather than", "forbids", "unavailable", "not investigated",
              "None of those", "no claim is made", "not asserted", "no cell asserts"]

# --- AC-7: premature technology decisions about OUR system ----------------
TECH_TOKENS = [
    "FastAPI", "Pydantic", "PostgreSQL", "Postgres", "Redis", "Temporal",
    "Celery", "ARQ", "LangGraph", "LangChain", "LiteLLM", "Next.js", "React",
    "MinIO", "Kubernetes", "Docker", "Prometheus", "Grafana", "Terraform",
    "SQLAlchemy", "Kafka", "OpenTelemetry", "Helm",
]
OUR_DECISION = (r"\b(we will use|we choose|we chose|we adopt|we have selected|"
                r"this product uses|the platform uses|the platform will use|"
                r"will be built on|is built on|we standardi[sz]e on|"
                r"the core uses|our stack)\b")

# --- AC-8: attribution ----------------------------------------------------
# Assembled from fragments so this file never writes the strings it forbids.
_FRAG = ["Co-Authored" + "-By", "Anthro" + "pic", "Cla" + "ude", "Cop" + "ilot",
         "noreply@" + "anthro" + "pic", "Approved" + "-by", "Assisted" + "-by"]
AI_ATTRIBUTION = [rf"\b{f}\b" for f in _FRAG] + [
    r"AI[- ]assist(ed|ant)", r"AI[- ]" + "generated", "generated" + r"\s+with",
]

# --- AC-9: secrets --------------------------------------------------------
SECRET_PATTERNS = [
    (r"sk-[A-Za-z0-9]{16,}", "provider-style key"),
    (r"sk-ant-[A-Za-z0-9\-_]{16,}", "prefixed key"),
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
    (r"^\s*\.\.\.\s*$", "ellipsis-only line"), (r"<placeholder", "placeholder tag"),
]

results = {"checks": []}


def add(check_id, status, detail, findings=None):
    results["checks"].append({"id": check_id, "status": status,
                              "detail": detail, "findings": findings or []})


def has_marker(line):
    return any(m in line for m in MARKERS)


def is_table_row(line):
    return line.strip().startswith("|")


# ---------------------------------------------------------------- AC-1
missing = [k for k, p in DOCS.items() if not p.exists()]
present = {k: p for k, p in DOCS.items() if p.exists()}
texts = {k: p.read_text(encoding="utf-8") for k, p in present.items()}
sizes = {k: (len(t), len(t.splitlines())) for k, t in texts.items()}
src_text = SOURCES.read_text(encoding="utf-8") if SOURCES.exists() else ""
add("AC-1", "PASS" if not missing and src_text else "FAIL",
    f"documents present: {sorted(present)}; missing: {missing}; "
    f"source register present: {bool(src_text)}; sizes(chars,lines): {sizes}")

all_lines = [(k, n, l) for k, t in texts.items()
             for n, l in enumerate(t.splitlines(), 1)]

# ---------------------------------------------------------------- AC-2
ca = texts.get("competitive_analysis", "")
uncovered = [c for c in COMPETITORS if c.lower() not in ca.lower()]
add("AC-2", "PASS" if not uncovered else "FAIL",
    f"competitors analysed: {len(COMPETITORS) - len(uncovered)}/{len(COMPETITORS)}; "
    f"absent: {uncovered or 'none'}")

# ---------------------------------------------------------------- AC-3
uncited = []
for k, n, l in all_lines:
    if "[VERIFIED" in l and not re.search(r"S-\d{2}", l):
        # Only the reading-conventions legend is exempt, identified by the
        # definitional phrasing in its own row.
        #
        # An earlier revision also exempted any backtick-wrapped bare
        # `[VERIFIED]`, on the theory that it referred to the marker rather
        # than using it. The self-test defeated that immediately: a planted
        # uncited claim written in exactly that form passed the check. The
        # exemption was removed and the one line of prose that relied on it was
        # reworded, so no exemption now depends on how a claim is punctuated.
        if "Vendor-documented fact" in l or "citing the" in l:
            continue
        uncited.append({"doc": k, "line": n, "label": "VERIFIED without source",
                        "text": l.strip()[:150]})
add("AC-3", "PASS" if not uncited else "FAIL",
    f"{len(uncited)} VERIFIED claim(s) citing no source identifier", uncited)

# ---------------------------------------------------------------- AC-4
defined = set(re.findall(r"\|\s*`(S-\d{2})`\s*\|", src_text))
cited = set()
for k, t in texts.items():
    cited |= set(re.findall(r"S-\d{2}", t))
undefined = sorted(cited - defined)
orphaned = sorted(defined - cited)
add("AC-4", "PASS" if not undefined and not orphaned else "FAIL",
    f"sources defined: {len(defined)}; cited: {len(cited)}; "
    f"cited-but-undefined: {undefined or 'none'}; defined-but-uncited: {orphaned or 'none'}")

# ---------------------------------------------------------------- AC-5
ASSERT_VERB = (r"\b(documents?|documented|provides?|supports?|offers?|includes?|"
               r"describes?|has|have|is licensed|runs?|integrates?|exposes?)\b")
unmarked = []
for k, n, l in all_lines:
    if not re.search(ASSERT_VERB, l):
        continue
    if not any(c.lower() in l.lower() for c in COMPETITORS):
        continue
    if has_marker(l) or re.search(r"S-\d{2}", l) or "not established" in l:
        continue
    if is_table_row(l):
        continue
    unmarked.append({"doc": k, "line": n, "label": "unmarked competitor claim",
                     "text": l.strip()[:150]})
add("AC-5", "PASS" if not unmarked else "FAIL",
    f"{len(unmarked)} competitor factual claim(s) carrying neither a marker nor a source",
    unmarked)

# ---------------------------------------------------------------- AC-6
hard, soft_open, soft_adj = [], [], []
for k, n, l in all_lines:
    for rx, label in HARD_NUMERIC:
        for m in re.finditer(rx, l, re.I):
            hard.append({"doc": k, "line": n, "label": label,
                         "text": l.strip()[:150]})
    for rx, label in SOFT_CLAIM:
        if re.search(rx, l, re.I):
            rec = {"doc": k, "line": n, "label": label, "text": l.strip()[:150]}
            if any(w in l for w in NEGATING):
                soft_adj.append(rec)
            else:
                soft_open.append(rec)
add("AC-6", "PASS" if not hard and not soft_open else "FAIL",
    f"hard numeric/pricing/adoption matches: {len(hard)} (must be zero); "
    f"comparative-claim matches: {len(soft_open) + len(soft_adj)}, of which "
    f"adjudicated as prohibitions or gap statements: {len(soft_adj)}; "
    f"OUTSTANDING: {len(soft_open)}", hard + soft_open)

# ---------------------------------------------------------------- AC-7
tech_open = []
for k, n, l in all_lines:
    if not re.search(OUR_DECISION, l, re.I):
        continue
    for tok in TECH_TOKENS:
        if re.search(rf"\b{re.escape(tok)}\b", l):
            tech_open.append({"doc": k, "line": n, "label": tok,
                              "text": l.strip()[:150]})
add("AC-7", "PASS" if not tech_open else "FAIL",
    f"{len(tech_open)} premature technology decision(s) about this system "
    f"(technology token on a line asserting our own adoption)", tech_open)

# ---------------------------------------------------------------- AC-8
ai = []
for k, n, l in all_lines:
    for rx in AI_ATTRIBUTION:
        if re.search(rx, l):
            ai.append({"doc": k, "line": n, "label": "attribution",
                       "text": l.strip()[:150]})
add("AC-8", "PASS" if not ai else "FAIL",
    f"{len(ai)} attribution match(es) - must be zero", ai)

# ---------------------------------------------------------------- AC-9
sec = []
for k, n, l in all_lines:
    for rx, label in SECRET_PATTERNS:
        if re.search(rx, l):
            sec.append({"doc": k, "line": n, "label": label,
                        "text": l.strip()[:80]})
add("AC-9", "PASS" if not sec else "FAIL",
    f"{len(sec)} secret-pattern match(es) - must be zero", sec)

# ---------------------------------------------------------------- AC-10
broken, nlinks = [], 0
for k, t in texts.items():
    for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", t):
        target = m.group(2)
        if target.startswith(("http", "mailto:")):
            continue
        nlinks += 1
        if not (DOCS[k].parent / target).resolve().exists():
            broken.append({"doc": k, "target": target})
for m in re.finditer(r"\[([^\]]+)\]\(([^)#]+)(#[^)]*)?\)", src_text):
    target = m.group(2)
    if target.startswith(("http", "mailto:")):
        continue
    nlinks += 1
    if not (SOURCES.parent / target).resolve().exists():
        broken.append({"doc": "sources", "target": target})
add("AC-10", "PASS" if not broken else "FAIL",
    f"{nlinks} relative link(s) checked; broken: {broken or 'none'}")

# ---------------------------------------------------------------- AC-11
absence_open = []
for k, n, l in all_lines:
    if not re.search(ABSENCE, l, re.I):
        continue
    if not any(c.lower() in l.lower() for c in COMPETITORS):
        continue
    if any(w in l for w in ABSENCE_OK):
        continue
    absence_open.append({"doc": k, "line": n, "label": "unqualified absence claim",
                         "text": l.strip()[:150]})
add("AC-11", "PASS" if not absence_open else "FAIL",
    f"{len(absence_open)} unqualified claim(s) that a named competitor lacks a "
    f"capability - documentation silence cannot establish absence", absence_open)

# ---------------------------------------------------------------- AC-12
pos = texts.get("positioning", "")
pillars = sorted(set(re.findall(r"\bP-(\d)\b", pos)))
pos_obs = sorted(set(re.findall(r"\bPO-(\d)\b", pos)))
missing_po = [p for p in pillars if p not in pos_obs]
unmeasured = len(re.findall(r"NOT YET MEASURED", pos))
add("AC-12", "PASS" if not missing_po and unmeasured >= len(pillars) else "FAIL",
    f"pillars: {len(pillars)}; proof obligations: {len(pos_obs)}; "
    f"pillars without an obligation: {missing_po or 'none'}; "
    f"NOT YET MEASURED statuses: {unmeasured}")

# ---------------------------------------------------------------- AC-13
risks_defined = set(re.findall(r"\|\s*R-(\d)\s*\|", ca))
risks_cited = set(re.findall(r"\bR-(\d)\b", pos))
dangling = sorted(risks_cited - risks_defined)
add("AC-13", "PASS" if not dangling else "FAIL",
    f"risks defined in analysis: {len(risks_defined)}; referenced by positioning: "
    f"{len(risks_cited)}; referenced but undefined: {dangling or 'none'}")

# ---------------------------------------------------------------- placeholders
ph = []
for k, n, l in all_lines:
    for rx, label in PLACEHOLDER_PATTERNS:
        if re.search(rx, l):
            ph.append({"doc": k, "line": n, "label": label, "text": l.strip()[:120]})
add("PLACEHOLDER", "PASS" if not ph else "FAIL",
    f"{len(ph)} placeholder marker(s)", ph)

# ---------------------------------------------------------------- output
counts = {}
for c in results["checks"]:
    counts[c["status"]] = counts.get(c["status"], 0) + 1

print("=" * 78)
print("M1.2 VALIDATION")
print("=" * 78)
for c in results["checks"]:
    print(f"[{c['status']:<7}] {c['id']:<12} {c['detail']}")
    for f in c["findings"][:40]:
        print(f"            - {f.get('doc','')}:{f.get('line','')} "
              f"«{f.get('label','')}» {f.get('text','')}")
    if len(c["findings"]) > 40:
        print(f"            ... {len(c['findings']) - 40} more")
print("-" * 78)
print("SUMMARY:", json.dumps(counts))
sys.exit(1 if counts.get("FAIL") else 0)
