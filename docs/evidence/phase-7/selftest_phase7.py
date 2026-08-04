"""Prove the Phase 7 checks can fail.

A check that has never failed has not been shown to work. Each violation below is
planted in the working tree, the *fast* half of the validator is run, the check
that should catch it is inspected, and the tree is restored from HEAD — which is
safe only because Phase 7 is committed. In Phase 6 it was not, and the restore
reverted three source files that had never been written to disk anywhere else.

The slow checks (the suite, and the four earlier gates re-run at their own
histories) are excluded deliberately: they are exercised by the full run, and
including them would turn a 40-second proof into a 3-hour one for no additional
evidence about the Phase 7 rules.
"""
from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import os
import tempfile

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PY = os.environ.get("CLEP_TEST_PYTHON", sys.executable)
FAST = pathlib.Path(tempfile.gettempdir()) / "clep_check7_fast.py"

SCHEMA = ROOT / "docs/data/schema/07-regression-and-gates.sql"
ENGINE = ROOT / "src/clep/regression/engine.py"
STATS = ROOT / "src/clep/regression/statistics.py"
REPORT = ROOT / "src/clep/regression/report.py"
CONTRACT = ROOT / "docs/api/openapi.json"
PHASE4 = ROOT / "docs/evidence/phase-4/check_phase4.py"


def rebuild_fast():
    src = (ROOT / "docs/evidence/phase-7/check_phase7.py").read_text(encoding="utf-8")
    start = src.index("# ===================================================== P-1 ")
    end = src.index("# ===================================== P-10 the contract leads")
    FAST.write_text(src[:start] + src[end:], encoding="utf-8")


def restore():
    subprocess.run(["git", "checkout", "--", "docs/data/schema", "src/clep",
                    "docs/api/openapi.json", "docs/evidence"],
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


case("P-18", "the gate decision loses its immutability trigger",
     lambda: plant(SCHEMA, "CREATE TRIGGER trg_gate_decision__immutable",
                   "CREATE TRIGGER trg_gate_decision__disabled_for_selftest"))

case("P-18", "the runtime role is granted UPDATE on a decision",
     lambda: plant(SCHEMA, "GRANT SELECT, INSERT         ON clep.gate_decision",
                   "GRANT SELECT, INSERT, UPDATE ON clep.gate_decision"))

case("P-19", "a statistical parameter acquires a default",
     lambda: plant(STATS, "precision_threshold: Decimal | None,",
                   "precision_threshold: Decimal | None = Decimal('0.05'),"))

case("P-19", "the width rule stops being applied first",
     lambda: plant(STATS, "    if interval.width > precision_threshold:",
                   "    if False and interval.width > precision_threshold:"))

case("P-19", "direction stops being applied",
     lambda: plant(STATS, "        worse = direction == HIGHER_IS_BETTER",
                   "        worse = True"))

case("P-20", "the threshold order is reversed",
     lambda: plant(ENGINE, "    if criterion.absolute_floor is not None",
                   "    if criterion.relative_tolerance is None and "
                   "criterion.absolute_floor is not None"))

case("P-21", "the pairing stops requiring a scored baseline sample",
     lambda: plant(ENGINE, "\"AND bo.resolution = 'scored' AND co.resolution = 'scored' \"",
                   "\"AND co.resolution = 'scored' \""))

case("P-22", "the human report stops separating judges from evaluators",
     lambda: plant(REPORT, 'for title, group in (("Deterministic evaluators", deterministic),',
                   'for title, group in (("All metrics", deterministic + judged),'))

case("P-23", "a gate table acquires a credential column",
     lambda: plant(SCHEMA, "    label               text,",
                   "    label               text,\n    webhook_token       text,"))

case("P-30", "an earlier gate stops re-running the one before it",
     lambda: plant(ROOT / "docs/evidence/phase-4/check_phase4.py",
                   "docs/evidence/phase-3/check_phase3.py",
                   "docs/evidence/phase-3/check_phase3_skipped.py"))

case("P-10", "an operation disappears from the contract",
     lambda: plant(CONTRACT, '"operationId": "createGatePolicy"',
                   '"operationId": "createGatePolicyRemoved"'))


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
