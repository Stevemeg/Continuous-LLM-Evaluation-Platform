"""Prove the Phase 10 checks can fail.

Same harness as Phases 8 and 9: plant a violation, run the fast half of the
validator, inspect the check that should catch it, restore from HEAD.

Usage: python docs/evidence/phase-10/selftest_phase10.py <repo_root>
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
FAST = pathlib.Path(tempfile.gettempdir()) / "clep_check10_fast.py"

SCHEMA = ROOT / "docs/data/schema/10-release-and-schedules.sql"
SCHEMA9 = ROOT / "docs/data/schema/09-rag-and-agent-evaluation.sql"
RUNNER = ROOT / "src/clep/orchestration/runner.py"
RUN_REPO = ROOT / "src/clep/orchestration/repository.py"
PANEL = ROOT / "src/clep/judges/panel.py"
CODES = ROOT / "src/clep/cli/exit_codes.py"
CLI = ROOT / "src/clep/cli/main.py"
E2E = ROOT / "docs/evidence/phase-10/real_end_to_end.py"
PHASE7 = ROOT / "docs/evidence/phase-7/check_phase7.py"


def rebuild_fast():
    src = (ROOT / "docs/evidence/phase-10/check_phase10.py").read_text("utf-8")
    slow_start = src.index("# ===================================================== P-1 ")
    slow_end = src.index("# ===================================== P-11 the contract leads")
    src = src[:slow_start] + src[slow_end:]
    scan_start = src.index("# ============================================================ P-20 secrets")
    scan_end = src.index("# ============ P-26 every earlier gate is reachable")
    FAST.write_text(src[:scan_start] + src[scan_end:], encoding="utf-8")


def restore():
    subprocess.run(["git", "checkout", "--", "docs/data/schema", "src/clep",
                    "docs/api/openapi.json", "docs/evidence", "docs/architecture",
                    "pyproject.toml"], cwd=str(ROOT), capture_output=True)


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


# ----------------------------------------------------- P-19a the harness
case("P-19a", "the evaluators lose their way into the run loop",
     lambda: plant(RUNNER, "                 analysis_repository=None, judge_panel=None,",
                   "                 judge_panel=None,"))

case("P-19a", "a second sample context appears in the loop",
     lambda: plant(RUNNER, "        results = self._gateway.invoke_all"
                           if False else
                           "    def _evaluate(self, sample: SampleContext):",
                   "    def _stray(self, example, output, tier):\n"
                   "        return SampleContext(example_id=example.id,\n"
                   "                             prompt=example.prompt,\n"
                   "                             output=output)\n\n"
                   "    def _evaluate(self, sample: SampleContext):"))

case("P-19a", "the loop starts judging a candidate that failed",
     lambda: plant(RUNNER,
                   "        if self._panel is None or not candidate_outcome.succeeded:",
                   "        if self._panel is None:"))

# ---------------------------------------------- P-19b judge persistence
case("P-19b", "only scoring judgements are written",
     lambda: plant(PANEL, "            prompt, _ = render_prompt(judge, sample)",
                   "            if not vote.is_scoring:\n"
                   "                continue\n"
                   "            prompt, _ = render_prompt(judge, sample)"))

case("P-19b", "the panel stops writing through the repository",
     lambda: plant(PANEL, "            self._repo.record_judgement(",
                   "            _ = lambda **kw: None  # noqa: E731\n"
                   "            _("))

# ------------------------------------------- P-19c an immutable sample
case("P-19c", "the runtime role is granted UPDATE on a resolved sample",
     lambda: plant(ROOT / "docs/data/schema/05-run-and-execution.sql",
                   "GRANT SELECT, INSERT ON clep.run_sample TO clep_runtime;",
                   "GRANT SELECT, INSERT, UPDATE ON clep.run_sample TO clep_runtime;"))

case("P-19c", "truncation goes back to being patched in afterwards",
     lambda: plant(ROOT / "src/clep/rag/repository.py",
                   "        return len(trajectory.steps)",
                   "        self._conn.execute(\n"
                   "            \"UPDATE clep.run_sample SET trajectory_truncated = %s\",\n"
                   "            (trajectory.truncated,))\n"
                   "        return len(trajectory.steps)"))

# ------------------------------------------------- P-19d the exit codes
case("P-19d", "an abstention starts letting the pipeline through",
     lambda: plant(CODES, "INSUFFICIENT_EVIDENCE = 70",
                   "INSUFFICIENT_EVIDENCE = 0"))

case("P-19d", "a blocking outcome loses its distinct code",
     lambda: plant(CODES, "APPROVAL_REQUIRED = 75", "APPROVAL_REQUIRED = 1"))

case("P-19d", "an unknown outcome starts passing",
     lambda: plant(CODES, "    return BY_OUTCOME.get(outcome, PLATFORM_FAILURE)",
                   "    return BY_OUTCOME.get(outcome, PASS)"))

case("P-19d", "a warning starts blocking, overriding the policy",
     lambda: plant(CODES, '    "warning": PASS,', '    "warning": HARD_FAIL,'))

case("P-19d", "the CLI acquires an override for abstentions",
     lambda: plant(CLI, '    gate.add_argument("--project", required=True)',
                   '    gate.add_argument("--ignore-abstentions", action="store_true")\n'
                   '    gate.add_argument("--project", required=True)'))

# -------------------------------------------------------- P-19e the CLI
case("P-19e", "the CLI grows a subcommand that changes something",
     lambda: plant(CLI, '    analysis = sub.add_parser("analysis", help="evidence for one sample")',
                   '    sub.add_parser("rollback", help="roll back a release")\n'
                   '    analysis = sub.add_parser("analysis", help="evidence for one sample")'))

case("P-19e", "the CLI starts deciding for itself",
     lambda: plant(CLI, "from clep.cli import exit_codes",
                   "from clep.cli import exit_codes\n"
                   "from clep.judges.consensus import reach_consensus  # noqa: F401"))

# ------------------------------------------------- P-19f advice, not action
case("P-19f", "the store learns to record having acted",
     lambda: plant(SCHEMA, "    observed_by           uuid NOT NULL,",
                   "    applied_at            timestamptz,\n"
                   "    observed_by           uuid NOT NULL,"))

case("P-19f", "a schedule may be created without a budget",
     lambda: plant(SCHEMA, "    budget_limit             numeric(18, 9) NOT NULL,",
                   "    budget_limit             numeric(18, 9),"))

case("P-19f", "a schedule may carry a zero budget",
     lambda: plant(SCHEMA, "    CONSTRAINT ck_evaluation_schedule__budget_is_positive",
                   "    CONSTRAINT ck_evaluation_schedule__budget_removed"))

case("P-19f", "an observation becomes editable after the outcome is known",
     lambda: plant(SCHEMA, "CREATE TRIGGER trg_release_observation__immutable",
                   "CREATE TRIGGER trg_release_observation__disabled"))

# --------------------------------------------- P-19g the end-to-end evidence
case("P-19g", "the real runner acquires a fallback",
     lambda: plant(E2E, '        print(f"REFUSING: unreachable models {unreachable}. This script "',
                   '        print(f"continuing anyway at {unreachable}. This script "'))

case("P-19g", "the deterministic end-to-end test starts needing a live model",
     lambda: plant(ROOT / "tests/test_end_to_end.py",
                   'ANSWER = "Paris is the capital of France."',
                   'JUDGE_URL = "http://localhost:8101/v1"\n'
                   'ANSWER = "Paris is the capital of France."'))

# ----------------------------------------------------------- P-26 closure
case("P-26", "an earlier gate stops re-running the one before it",
     lambda: plant(PHASE7, "docs/evidence/phase-6/check_phase6.py",
                   "docs/evidence/phase-6/check_phase6_skipped.py"))


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
