"""Prove the Phase 8 checks can fail.

A check that has never failed has not been shown to work. Each violation below
is planted in the working tree, the *fast* half of the validator is run, the
check that should catch it is inspected, and the tree is restored from HEAD —
which is safe only because Phase 8 is committed before this runs.

The slow checks (the suite, and the five earlier gates re-run at their own
histories) are excluded deliberately: they are exercised by the full run, and
including them would turn a one-minute proof into a many-hour one for no
additional evidence about the Phase 8 rules.

Usage: python docs/evidence/phase-8/selftest_phase8.py <repo_root>
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
FAST = pathlib.Path(tempfile.gettempdir()) / "clep_check8_fast.py"

SCHEMA = ROOT / "docs/data/schema/08-judges-plans-and-memory.sql"
CONSENSUS = ROOT / "src/clep/judges/consensus.py"
JUDGE_SDK = ROOT / "src/clep/judges/sdk.py"
REFLECTION = ROOT / "src/clep/judges/reflection.py"
AGENT_SDK = ROOT / "src/clep/agents/sdk.py"
PLANNER = ROOT / "src/clep/agents/planner.py"
IDENTITY = ROOT / "src/clep/experiments/identity.py"
COMPARABILITY = ROOT / "src/clep/regression/comparability.py"
CONTRACT = ROOT / "docs/api/openapi.json"
DEBT = ROOT / "docs/architecture/tracked-debt.md"
CORPUS = ROOT / "docs/evidence/phase-8/injection-corpus.json"
PHASE5 = ROOT / "docs/evidence/phase-5/check_phase5.py"


def rebuild_fast():
    src = (ROOT / "docs/evidence/phase-8/check_phase8.py").read_text(encoding="utf-8")
    start = src.index("# ===================================================== P-1 ")
    end = src.index("# ===================================== P-11 the contract leads")
    FAST.write_text(src[:start] + src[end:], encoding="utf-8")


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


# ---------------------------------------------------------------- P-19 store
case("P-19", "a judgement loses its immutability trigger",
     lambda: plant(SCHEMA, "CREATE TRIGGER trg_judge_run__immutable",
                   "CREATE TRIGGER trg_judge_run__disabled_for_selftest"))

case("P-19", "the runtime role is granted UPDATE on a consensus result",
     lambda: plant(SCHEMA, "GRANT SELECT, INSERT         ON clep.consensus_result",
                   "GRANT SELECT, INSERT, UPDATE ON clep.consensus_result"))

case("P-19", "an escalation becomes reviewable twice",
     lambda: plant(SCHEMA, "CREATE TRIGGER trg_escalation__reviewed_once",
                   "CREATE TRIGGER trg_escalation__reviewed_often"))

# ------------------------------------------------------------- P-20 ADR-017
case("P-20", "the agreement threshold acquires a default",
     lambda: plant(CONSENSUS, "    agreement_threshold: Decimal | None\n",
                   "    agreement_threshold: Decimal | None = Decimal('0.2')\n"))

case("P-20", "the ensemble stops refusing one repeated configuration",
     lambda: plant(CONSENSUS, "        if len(distinct) < 2:",
                   "        if False and len(distinct) < 2:"))

case("P-20", "a majority configuration is allowed to outvote the rest",
     lambda: plant(CONSENSUS,
                   "            if configurations.count(configuration) * 2 > len(configurations):",
                   "            if False:"))

case("P-20", "an unmeasured disagreement is reported as zero",
     lambda: plant(CONSENSUS, "MAXIMUM_DISAGREEMENT = Decimal(1)",
                   "MAXIMUM_DISAGREEMENT = Decimal(0)"))

case("P-20", "an ensemble with no threshold starts agreeing",
     lambda: plant(CONSENSUS, "    if ensemble.agreement_threshold is None:",
                   "    if False and ensemble.agreement_threshold is None:"))

case("P-20", "disagreement becomes a mean deviation rather than the range",
     lambda: plant(CONSENSUS, "    disagreement = scores[-1] - scores[0]",
                   "    disagreement = (scores[-1] - scores[0]) / Decimal(len(scores))"))

# --------------------------------------------------------- P-21 the bounds
case("P-21", "a reasoning bound acquires a default",
     lambda: plant(AGENT_SDK, "    max_iterations: int\n",
                   "    max_iterations: int = 5\n"))

case("P-21", "the budget stops being checked before an attempt",
     lambda: plant(AGENT_SDK, "        if spent >= bounds.budget:",
                   "        if False and spent >= bounds.budget:"))

case("P-21", "rejected iterations stop being retained",
     lambda: plant(AGENT_SDK,
                   "        attempts.append(Attempt(index=index, value=proposal.value, accepted=accepted,",
                   "        accepted and attempts.append(Attempt(index=index, value=proposal.value, accepted=accepted,"))

case("P-21", "regeneration starts re-asking a judge that already answered",
     lambda: plant(REFLECTION,
                   'return vote.resolution == "failed" and vote.detail.startswith(UNREADABLE)',
                   'return vote.resolution != "abstained"'))

# ----------------------------------------------------------- P-22 REQ-X-8
case("P-22", "a judge that did not score is allowed to carry a zero",
     lambda: plant(JUDGE_SDK, '        if (self.resolution == "scored") != (self.score is not None):',
                   '        if False:'))

case("P-22", "the score moves onto the attempt as a nullable column",
     lambda: plant(SCHEMA, "    resolution            text NOT NULL,\n    latency_ms",
                   "    resolution            text NOT NULL,\n    score                 numeric(18, 9),\n    latency_ms"))

# -------------------------------------------------------- P-23 containment
case("P-23", "the fence stops being neutralised in untrusted content",
     lambda: plant(JUDGE_SDK, "        if token in text:",
                   "        if False and token in text:"))

case("P-23", "the reply parse widens to accept anything with a number",
     lambda: plant(JUDGE_SDK, r'_SCORE = re.compile(r"^\s*SCORE:\s*(0(?:\.\d+)?|1(?:\.0+)?)\s*$")',
                   r'_SCORE = re.compile(r"(0(?:\.\d+)?|1(?:\.0+)?)")'))

case("P-23", "the adversarial corpus is quietly emptied",
     lambda: plant(CORPUS, '"content": [', '"content": [], "unused": ['))

# ------------------------------------------------------------- P-24 plans
case("P-24", "an accepted plan becomes amendable again",
     lambda: plant(PLANNER, "    if plan.state != DRAFT:\n        raise PlanError(\n            f\"this plan is {plan.state}; an accepted plan is the record of what \"",
                   "    if False:\n        raise PlanError(\n            f\"this plan is {plan.state}; an accepted plan is the record of what \""))

case("P-24", "an over-budget plan stops being refused",
     lambda: plant(PLANNER,
                   "    if plan.inputs.budget is not None and plan.estimated_cost > plan.inputs.budget:",
                   "    if False:"))

# ---------------------------------------------------------- P-25 identity
case("P-25", "a judge version stops pinning comparability",
     lambda: plant(COMPARABILITY, '"judge_version", "integration_tier")',
                   '"integration_tier")'))

case("P-25", "a judge version leaves run identity",
     lambda: plant(IDENTITY, '    "judge_version",\n', ''))

# -------------------------------------------------------------- P-26 debt
case("P-26", "the tracked debt register is quietly emptied",
     lambda: plant(DEBT, "## D-1 — `comparison.evaluator_version_id` does not carry the tenant",
                   "### Formerly D-1"))

case("P-26", "a debt entry loses its owning phase",
     lambda: plant(DEBT, "| Owning phase | The tenancy and authentication phase (Phase 12) |",
                   "| Someday | maybe |"))

# ------------------------------------------------------ P-34 the closure
case("P-34", "an earlier gate stops re-running the one before it",
     lambda: plant(PHASE5, "docs/evidence/phase-4/check_phase4.py",
                   "docs/evidence/phase-4/check_phase4_skipped.py"))


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
