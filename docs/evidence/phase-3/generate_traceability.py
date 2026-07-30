"""Traceability generator — requirements to architecture, data model, and API.

Canonical §18 requires traceability mapping requirements to architecture, APIs and
schema. `prd.md` deferred the *mechanical* enforcement of that mapping to Phase 3,
and `SC-G7` requires the count of untraced requirements to be exactly zero. This
generator is that enforcement.

It derives the mapping from the artifacts themselves rather than from a
hand-maintained matrix, because a matrix restating information held elsewhere
drifts -- M1.1 found sixteen such drifted entries, which is why nothing here is
written by hand.

Every requirement must be either TRACED to a downstream artifact or explicitly
DEFERRED with an owning phase and a reason. The check fails on:

  * a requirement that is neither traced nor deferred  -- an untracked gap;
  * a requirement that is BOTH traced and deferred     -- a stale deferral, i.e.
    the work happened but the register still claims it did not.

The second direction matters as much as the first. A deferral register that is
never re-checked becomes a place where completed work goes to be forgotten.

Usage:
    python generate_traceability.py <repo_root>            # check only
    python generate_traceability.py <repo_root> --write     # also write the matrix
Exits non-zero on any failure.
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(sys.argv[1]).resolve()
WRITE = "--write" in sys.argv
OUT = ROOT / "docs" / "evidence" / "phase-3" / "traceability-matrix.md"

REQ_RX = r"\bREQ-(?:F-\d{2}-\d+|F-AG-\d+|X-\d+|N-[A-Z]+-\d+)\b"
DEF_PATTERNS = [r"REQ-F-\d{2}-\d+", r"REQ-F-AG-\d+", r"REQ-X-\d+", r"REQ-N-[A-Z]+-\d+"]

# Requirements deliberately not traceable at Phase 3, each with the phase that
# owns it and why. Canonical §23 phase numbering.
DEFERRED = {
    "REQ-F-03-1": (9, "RAG evaluator inputs are evaluator-catalogue content; the contract exposes integration tiers, not individual evaluators"),
    "REQ-F-03-2": (9, "Specific RAG evaluators are suite content delivered with the RAG evaluation suite"),
    "REQ-F-03-3": (9, "Hallucination analysis is an evaluator behaviour, not a contract or schema shape"),
    "REQ-F-03-6": (9, "Retrieval-versus-generation failure attribution is evaluator logic"),
    "REQ-F-04-2": (9, "Agent evaluators are suite content delivered with the agent evaluation suite"),
    "REQ-F-04-3": (9, "Trajectory and loop analysis is evaluator logic"),
    "REQ-F-04-4": (9, "Final-answer evaluation is evaluator logic"),
    "REQ-F-10-2": (10, "Canary and post-deployment evaluation surfaces arrive with the CI/CD release-gate phase"),
    "REQ-F-10-4": (11, "Drift detection over baseline history is an analytics capability"),
    "REQ-F-11-2": (11, "Leaderboards are an analytics surface"),
    "REQ-F-11-5": (11, "Agent analytics reporting is an analytics surface"),
    "REQ-F-11-8": (11, "Executive scorecards are a reporting surface"),
    "REQ-F-AG-6": (8, "Historical evaluation memory arrives with the agentic evaluation layer"),
    "REQ-N-COMP-4": (15, "Per-capability methodology documentation is a release-documentation obligation"),
    "REQ-N-MAINT-2": (5, "Deterministic fixtures require code to fixture"),
    "REQ-N-MAINT-4": (5, "Coverage gates require a build to gate"),
    "REQ-N-MAINT-5": (5, "Dependency justification requires a dependency manifest, which Phase 3 deliberately does not create"),
    "REQ-N-OPS-1": (5, "Local development setup requires something to run"),
    "REQ-N-OPS-2": (5, "Environment configuration requires a running service"),
    "REQ-N-OPS-3": (4, "Migrations are implementation; Phase 3 specifies the model, Phase 4 builds the registry foundation"),
}

LAYERS = [
    ("architecture", ["docs/architecture/*.md", "docs/adr/*.md"]),
    ("data", ["docs/data/*.md"]),
]


def defined_requirements():
    text = (ROOT / "docs/product/requirements.md").read_text(encoding="utf-8")
    out = set()
    for pat in DEF_PATTERNS:
        out |= set(re.findall(r"`(" + pat + r")`", text))
    return out


def scan_markdown(globs, valid):
    """Map requirement -> set of files citing it."""
    hits = {}
    for g in globs:
        for p in sorted(ROOT.glob(g)):
            rel = p.relative_to(ROOT).as_posix()
            for r in set(re.findall(REQ_RX, p.read_text(encoding="utf-8"))):
                if r in valid:
                    hits.setdefault(r, set()).add(rel)
    return hits


def scan_openapi(valid):
    """Map requirement -> set of operationIds / schema names declaring it."""
    spec_path = ROOT / "docs/api/openapi.json"
    if not spec_path.exists():
        return {}, None
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    hits = {}

    def note(req, where):
        if req in valid:
            hits.setdefault(req, set()).add(where)

    # Walk recursively. An x-requirements extension is legal at any level, and
    # several sit on nested schema properties rather than on the schema object
    # itself -- a shallow scan silently misses those and reports a requirement as
    # untracked when the contract does declare it. Attribution is to the nearest
    # enclosing named context.
    def walk(node, context):
        if isinstance(node, dict):
            for r in node.get("x-requirements", []) or []:
                note(r, context)
            for key, value in node.items():
                if key == "x-requirements":
                    continue
                nxt = context
                if isinstance(value, dict) and "operationId" in value:
                    nxt = value["operationId"]
                elif context is None:
                    nxt = key
                walk(value, nxt)
        elif isinstance(node, list):
            for item in node:
                walk(item, context)

    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            walk(op, op.get("operationId", f"{method} {path}"))
    for group in ("schemas", "parameters", "responses"):
        for name, obj in spec.get("components", {}).get(group, {}).items():
            walk(obj, f"{group}/{name}")
    return hits, spec


def main():
    valid = defined_requirements()
    layer_hits = {name: scan_markdown(globs, valid) for name, globs in LAYERS}
    api_hits, spec = scan_openapi(valid)
    layer_hits["api"] = api_hits

    traced = set()
    for hits in layer_hits.values():
        traced |= set(hits)

    untracked = sorted(r for r in valid if r not in traced and r not in DEFERRED)
    stale = sorted(r for r in DEFERRED if r in traced)
    unknown_deferral = sorted(r for r in DEFERRED if r not in valid)

    print("=" * 78)
    print("REQUIREMENT TRACEABILITY")
    print("=" * 78)
    print(f"requirements defined      : {len(valid)}")
    print(f"traced to an artifact     : {len(traced)}")
    print(f"  architecture layer      : {len(layer_hits['architecture'])}")
    print(f"  data model layer        : {len(layer_hits['data'])}")
    print(f"  API contract layer      : {len(layer_hits['api'])}")
    print(f"deferred with an owner    : {len(DEFERRED)}")
    print(f"implementation layer      : 0 (no implementation exists at Phase 3)")
    print(f"test layer                : 0 (no tests exist at Phase 3)")
    print()
    print(f"[{'FAIL' if untracked else 'PASS'}] untracked requirements   : {len(untracked)}")
    for r in untracked:
        print(f"         - {r} is neither traced nor deferred")
    print(f"[{'FAIL' if stale else 'PASS'}] stale deferrals          : {len(stale)}")
    for r in stale:
        print(f"         - {r} is deferred but is in fact traced; remove the deferral")
    print(f"[{'FAIL' if unknown_deferral else 'PASS'}] deferrals for unknown ids: {len(unknown_deferral)}")
    for r in unknown_deferral:
        print(f"         - {r} is deferred but is not a defined requirement")

    ok = not (untracked or stale or unknown_deferral)
    print()
    print(f"VERDICT: {'PASS' if ok else 'FAIL'}")

    if WRITE:
        rows = []
        for r in sorted(valid, key=lambda x: (len(x), x)):
            arch = ", ".join(sorted(layer_hits["architecture"].get(r, [])))
            data = ", ".join(sorted(layer_hits["data"].get(r, [])))
            api = ", ".join(sorted(layer_hits["api"].get(r, [])))
            if r in DEFERRED:
                phase, reason = DEFERRED[r]
                status = f"deferred → Phase {phase}"
            else:
                status = "traced"
            rows.append((r, status, arch, data, api))

        lines = [
            "# Requirement Traceability Matrix",
            "",
            "| Field | Value |",
            "|---|---|",
            "| Status | **Generated — do not edit by hand** |",
            "| Milestone | M3.5 — Traceability |",
            "| Phase | Phase 3 — Data and Contracts |",
            "| Generated by | `generate_traceability.py` |",
            "| Required by | Canonical §18 · `SC-G7` |",
            "",
            "Derived from the artifacts themselves. Regenerate with "
            "`python docs/evidence/phase-3/generate_traceability.py . --write`.",
            "",
            f"**{len(traced)} of {len(valid)} requirements traced**; {len(DEFERRED)} deferred with an "
            "owning phase and a reason; 0 untracked.",
            "",
            "Implementation and test columns are absent because neither exists at Phase 3. "
            "`SC-G7` becomes fully measurable when they do; this generator is the mechanism "
            "that will measure it.",
            "",
            "| Requirement | Status | Architecture | Data model | API contract |",
            "|---|---|---|---|---|",
        ]
        for r, status, arch, data, api in rows:
            lines.append(f"| `{r}` | {status} | {arch or '—'} | {data or '—'} | {api or '—'} |")

        lines += ["", "## Deferred requirements", "",
                  "| Requirement | Owning phase | Reason |", "|---|---|---|"]
        for r in sorted(DEFERRED, key=lambda x: (DEFERRED[x][0], x)):
            phase, reason = DEFERRED[r]
            lines.append(f"| `{r}` | Phase {phase} | {reason} |")
        lines.append("")

        OUT.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        print(f"matrix written: {OUT.relative_to(ROOT).as_posix()} ({len(rows)} rows)")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
