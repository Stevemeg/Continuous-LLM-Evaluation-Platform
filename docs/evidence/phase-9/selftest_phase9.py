"""Prove the Phase 9 checks can fail.

A check that has never failed has not been shown to work. Each violation below
is planted in the working tree, the fast half of the validator is run, the check
that should catch it is inspected, and the tree is restored from HEAD — which is
safe only because Phase 9 is committed before this runs.

The slow halves are excluded for the reasons the Phase 8 self-test gives: the
suite and the six earlier gates are exercised by the full run, and the
whole-history blob scan would be walked once per plant.

Usage: python docs/evidence/phase-9/selftest_phase9.py <repo_root>
"""
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PY = os.environ.get("CLEP_TEST_PYTHON", sys.executable)
FAST = pathlib.Path(tempfile.gettempdir()) / "clep_check9_fast.py"

SCHEMA = ROOT / "docs/data/schema/09-rag-and-agent-evaluation.sql"
RAG = ROOT / "src/clep/evaluators/rag.py"
AGENT = ROOT / "src/clep/evaluators/agent.py"
TRAJECTORY = ROOT / "src/clep/evaluators/trajectory.py"
HALLUCINATION = ROOT / "src/clep/rag/hallucination.py"
ATTRIBUTION = ROOT / "src/clep/rag/attribution.py"
EVIDENCE = ROOT / "docs/evidence/phase-9/real-model-evidence.md"
RUNNER = ROOT / "docs/evidence/phase-9/real_model_run.py"
PHASE6 = ROOT / "docs/evidence/phase-6/check_phase6.py"


def rebuild_fast():
    src = (ROOT / "docs/evidence/phase-9/check_phase9.py").read_text(encoding="utf-8")
    slow_start = src.index("# ===================================================== P-1 ")
    slow_end = src.index("# ===================================== P-11 the contract leads")
    src = src[:slow_start] + src[slow_end:]
    scan_start = src.index("# ============================================================ P-20 secrets")
    scan_end = src.index("# ============ P-26 every earlier gate is reachable")
    FAST.write_text(src[:scan_start] + src[scan_end:], encoding="utf-8")


def restore():
    subprocess.run(["git", "checkout", "--", "docs/data/schema", "src/clep",
                    "docs/api/openapi.json", "docs/evidence", "docs/architecture"],
                   cwd=str(ROOT), capture_output=True)


def status_of(check_id: str) -> str:
    out = subprocess.run([PY, str(FAST), str(ROOT)], cwd=str(ROOT),
                         capture_output=True, text=True, encoding="utf-8",
                         errors="replace").stdout
    for line in out.splitlines():
        m = re.match(r"\[(\w+)\s*\]\s+(\S+)", line)
        if m and m.group(2) == check_id:
            return m.group(1)
    return "MISSING"


def plant(path: pathlib.Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"planting anchor not found in {path.name}: {old[:60]}"
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


CASES = []


def case(check_id, label, mutate):
    CASES.append((check_id, label, mutate))


# ------------------------------------------- P-19a the computed/judged split
case("P-19a", "a semantic judgement becomes a deterministic evaluator",
     lambda: plant(RAG, 'class CitationCoverage:\n    """',
                   'class Groundedness:\n'
                   '    name = "groundedness"\n'
                   '    version = "1.0.0"\n'
                   '    requires_tier = "partial"\n\n'
                   '    def evaluate(self, sample):\n'
                   '        return scored(1)\n\n\n'
                   'class CitationCoverage:\n    """'))

case("P-19a", "a judged rubric quietly disappears",
     lambda: plant(RAG, '    "groundedness":', '    "groundedness_removed":'))

# ------------------------------------------------ P-19b retrieval as a fact
case("P-19b", "an unlabelled example scores instead of abstaining",
     lambda: plant(RAG,
                   "        required = set(sample.required_context_ids)\n"
                   "        if not required:",
                   "        required = set(sample.required_context_ids)\n"
                   "        if False:"))

case("P-19b", "the hit rate is computed over what came back",
     lambda: plant(RAG, "        found = required & set(sample.context_by_id())",
                   "        found = required"))

case("P-19b", "a citation stops having to name something retrieved",
     lambda: plant(SCHEMA, "    CONSTRAINT fk_sample_citation__retrieved_context",
                   "    CONSTRAINT fk_sample_citation__removed_for_selftest"))

# ------------------------------------------------------- P-19c ADR-018
case("P-19c", "unsupported and contradicted collapse into one finding",
     lambda: plant(HALLUCINATION,
                   "    if support_score >= support_threshold:\n"
                   "        return ClaimAnalysis(claim=claim, finding=GROUNDED,",
                   "    if True:\n"
                   "        return ClaimAnalysis(claim=claim, finding=GROUNDED,"))

case("P-19c", "support starts outranking contradiction",
     lambda: plant(HALLUCINATION,
                   "    if contradiction_score >= contradiction_threshold:",
                   "    if False and contradiction_score >= contradiction_threshold:"))

case("P-19c", "an escalated judgement is read as a low score",
     lambda: plant(HALLUCINATION,
                   "    if support_score is None or contradiction_score is None:",
                   "    if False:"))

case("P-19c", "a hallucination threshold acquires a default",
     lambda: plant(HALLUCINATION,
                   "    if support_threshold is None or contradiction_threshold is None:",
                   "    support_threshold = support_threshold or Decimal('0.7')\n"
                   "    contradiction_threshold = contradiction_threshold or Decimal('0.5')\n"
                   "    if False:"))

# -------------------------------------------------- P-19d stage attribution
case("P-19d", "generation starts outranking retrieval",
     lambda: plant(ATTRIBUTION, "    missing = missing_required(sample)\n"
                                "    if missing:",
                   "    missing = missing_required(sample)\n"
                   "    if False:"))

case("P-19d", "an unlabelled example gets attributed anyway",
     lambda: plant(ATTRIBUTION, "    if not sample.required_context_ids:",
                   "    if False:"))

case("P-19d", "the store stops requiring a retrieval failure to name what was missing",
     lambda: plant(SCHEMA,
                   "    CONSTRAINT ck_stage_attribution__retrieval_names_what_was_missing",
                   "    CONSTRAINT ck_stage_attribution__removed_for_selftest"))

# ------------------------------------------------------- P-19e truncation
case("P-19e", "a truncated trajectory is scored as a completed run",
     lambda: plant(AGENT, "        if trajectory.truncated:\n"
                          "            return EvaluatorOutcome(\n"
                          '                "truncated",\n'
                          '                detail="the trajectory was truncated on ingest; whether the "',
                   "        if False:\n"
                   "            return EvaluatorOutcome(\n"
                   '                "truncated",\n'
                   '                detail="the trajectory was truncated on ingest; whether the "'))

case("P-19e", "the ingest bound can be routed around",
     lambda: plant(TRAJECTORY, "        if len(self.steps) > MAX_TRAJECTORY_STEPS:",
                   "        if False:"))

case("P-19e", "truncation stops being recorded on the sample",
     lambda: plant(SCHEMA, "ALTER TABLE clep.run_sample\n    ADD COLUMN trajectory_truncated",
                   "ALTER TABLE clep.run_sample\n    ADD COLUMN trajectory_was_cut"))

# ------------------------------------------------------ P-19f agent signals
case("P-19f", "tool selection stops penalising spurious tools",
     lambda: plant(AGENT, "        union = expected | used",
                   "        union = expected"))

case("P-19f", "recovery is credited to an agent that never failed",
     lambda: plant(AGENT, "        if recovered is None:\n"
                          '            return abstained("no step failed, so recovery was never exercised")',
                   "        if recovered is None:\n"
                   "            return scored(1)"))

case("P-19f", "an evaluator combines route quality with answer quality",
     lambda: plant(AGENT, 'class TaskSuccess:', 'class AgentScoreOverall:\n'
                   '    name = "agent_score"\n'
                   '    version = "1.0.0"\n'
                   '    requires_tier = "full"\n\n'
                   '    def evaluate(self, sample):\n'
                   '        return scored(1)\n\n\n'
                   'class TaskSuccess:'))

# --------------------------------------------------------- P-19g the store
case("P-19g", "a hallucination finding becomes editable",
     lambda: plant(SCHEMA, "CREATE TRIGGER trg_hallucination_finding__immutable",
                   "CREATE TRIGGER trg_hallucination_finding__disabled_for_selftest"))

case("P-19g", "the runtime role is granted DELETE on retrieved context",
     lambda: plant(SCHEMA, "GRANT SELECT, INSERT ON clep.retrieved_context     TO clep_runtime;",
                   "GRANT SELECT, INSERT, DELETE ON clep.retrieved_context TO clep_runtime;"))

case("P-19g", "a retrieved passage is copied into the store rather than referenced",
     lambda: plant(SCHEMA, "    content_digest    text NOT NULL,\n    payload_ref       text,",
                   "    content_digest    text NOT NULL,\n    text              text,\n"
                   "    payload_ref       text,"))

# ------------------------------------------------- P-19h real-model evidence
case("P-19h", "the real-model evidence stops stating its gaps",
     lambda: plant(EVIDENCE, "## Evidence gaps, stated", "## Notes"))

case("P-19h", "the real-model evidence overclaims",
     lambda: plant(EVIDENCE, "## The finding that matters most",
                   "The judge layer is production-ready.\n\n"
                   "## The finding that matters most"))

case("P-19h", "the runner acquires a fallback to a stub",
     lambda: plant(RUNNER, '        print(f"REFUSING: no real model reachable at {unreachable}. This script "',
                   '        print(f"falling back at {unreachable}. This script "'))

# ------------------------------------------------------ P-26 the closure
case("P-26", "an earlier gate stops re-running the one before it",
     lambda: plant(PHASE6, "docs/evidence/phase-5/check_phase5.py",
                   "docs/evidence/phase-5/check_phase5_skipped.py"))


def main() -> int:
    rebuild_fast()
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                           capture_output=True, text=True).stdout.strip()
    if dirty:
        print("REFUSING: the tree is dirty; a restore would discard uncommitted work")
        print(dirty)
        return 2

    caught = 0
    for check_id, label, mutate in CASES:
        try:
            mutate()
            status = status_of(check_id)
        finally:
            restore()
        ok = status == "FAIL"
        caught += ok
        print(f"[{'CAUGHT' if ok else 'MISSED':<7}] {check_id:<6} {label} "
              f"({'reported FAIL' if ok else 'reported ' + status})")

    print(f"\nself-test: {caught}/{len(CASES)} planted violations caught")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
