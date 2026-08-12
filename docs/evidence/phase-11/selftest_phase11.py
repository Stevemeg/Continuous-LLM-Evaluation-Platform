"""Prove the Phase 11 checks can fail.

Same harness as Phases 8 to 10: plant a violation, run the fast half of the
validator, inspect the check that should catch it, restore from HEAD.

Two things are stricter than Phase 10's.

**Restoration removes untracked files too.** Phase 10 restored with
`git checkout -- .`, which reverts modifications and leaves anything a plant
created. Nothing created files then; a plant that did would have survived
silently, which is exactly how the earlier leak happened. `git clean -fd` is
added, and it is safe because the run refuses to start on a dirty tree — so any
untracked file afterwards is this script's.

**The restoration is verified rather than assumed.** After every case the tree
is compared with HEAD, and the run fails if anything survived. A self-test that
leaves a mutation behind is worse than no self-test: it plants the defect it was
written to catch.

Nothing here is enumerated. The restore reverts and removes whatever the working
tree holds, so a plant on a new path cannot leak the way a listed set of paths
would allow.

Usage: python docs/evidence/phase-11/selftest_phase11.py <repo_root>
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
PY = os.environ.get("CLEP_TEST_PYTHON", sys.executable)
FAST = pathlib.Path(tempfile.gettempdir()) / "clep_check11_fast.py"

SCHEMA = ROOT / "docs/data/schema/11-analytics-and-alerts.sql"
SCHEMA10 = ROOT / "docs/data/schema/10-release-and-schedules.sql"
WORKER = ROOT / "src/clep/orchestration/worker.py"
SCHEDULER = ROOT / "src/clep/orchestration/scheduler.py"
SCHEDULES = ROOT / "src/clep/orchestration/schedules.py"
RELEASES = ROOT / "src/clep/orchestration/releases.py"
ANALYTICS = ROOT / "src/clep/analytics/repository.py"
COMPLETENESS = ROOT / "src/clep/analytics/completeness.py"
DRIFT = ROOT / "src/clep/analytics/drift.py"
ALERTS = ROOT / "src/clep/analytics/alerts.py"
SCORECARD = ROOT / "src/clep/analytics/scorecard.py"
CI_EVIDENCE = ROOT / "docs/evidence/phase-11/ci-execution-output.json"
PHASE10 = ROOT / "docs/evidence/phase-10/check_phase10.py"
E2E_TEST = ROOT / "tests/test_scheduled_execution.py"
CONTRACT = ROOT / "docs/api/openapi.json"


def rebuild_fast():
    src = (ROOT / "docs/evidence/phase-11/check_phase11.py").read_text("utf-8")
    slow_start = src.index("# ===================================================== P-1 ")
    slow_end = src.index("# ===================================== P-11 the contract leads")
    src = src[:slow_start] + src[slow_end:]
    scan_start = src.index("# ============================================================ P-20 secrets")
    scan_end = src.index("# ============ P-26 every earlier gate is reachable")
    FAST.write_text(src[:scan_start] + src[scan_end:], encoding="utf-8")


def restore():
    """Revert every modification and remove everything a plant created.

    Derived rather than listed. `-fd` and not `-fdx`: ignored paths are not this
    script's business, and a virtualenv or a coverage file is not a plant.
    """
    subprocess.run(["git", "checkout", "--", "."], cwd=str(ROOT),
                   capture_output=True)
    subprocess.run(["git", "clean", "-fdq"], cwd=str(ROOT), capture_output=True)


def dirty() -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                          capture_output=True, text=True).stdout.strip()


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


def plant_json(path: pathlib.Path, mutate) -> None:
    body = json.loads(path.read_text(encoding="utf-8"))
    mutate(body)
    path.write_text(json.dumps(body, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")


CASES = []


def case(check_id, label, mutate):
    CASES.append((check_id, label, mutate))


# ------------------------------------------------- P-27a the trigger is real
case("P-27a", "the worker stops registering the sweep on its cron",
     lambda: plant(WORKER, "    cron_jobs = [sweep_cron()]", "    cron_jobs = []"))

case("P-27a", "the sweep fires on every worker restart",
     lambda: plant(WORKER, "                run_at_startup=False, unique=True",
                   "                run_at_startup=True, unique=True"))

case("P-27a", "the end-to-end test drives the sweep itself",
     lambda: plant(E2E_TEST, "    task = asyncio.create_task(worker.async_run())",
                   "    from clep.orchestration.scheduler import sweep_tenant\n"
                   "    _ = sweep_tenant\n"
                   "    task = asyncio.create_task(worker.async_run())"))

case("P-27a", "the end-to-end test stops starting a worker at all",
     lambda: plant(E2E_TEST, "    worker = Worker(", "    worker = _Worker("))

# --------------------------------------------- P-27b the trigger's identity
case("P-27b", "a trigger key varies within its own minute",
     lambda: plant(SCHEDULES, "%Y-%m-%dT%H:%MZ", "%Y-%m-%dT%H:%M:%SZ"))

case("P-27b", "a cadence with the wrong number of fields is accepted",
     lambda: plant(SCHEDULES, "    if len(fields) != 5:", "    if False:"))

case("P-27b", "a minute outside the range is accepted",
     lambda: plant(SCHEDULES, "        if start < low or stop > high or start > stop:",
                   "        if False:"))

# ------------------------------------------------- P-27c advice, not action
case("P-27c", "an alert firing records having been delivered",
     lambda: plant(SCHEMA, "    fired_at              timestamptz NOT NULL DEFAULT now(),",
                   "    delivered_at          timestamptz,\n"
                   "    fired_at              timestamptz NOT NULL DEFAULT now(),"))

case("P-27c", "an alert firing becomes editable after the outcome is known",
     lambda: plant(SCHEMA, "CREATE TRIGGER trg_alert_event__immutable",
                   "CREATE TRIGGER trg_alert_event__disabled"))

case("P-27c", "the runtime role may amend a firing",
     lambda: plant(SCHEMA, "GRANT SELECT, INSERT         ON clep.alert_event TO clep_runtime;",
                   "GRANT SELECT, INSERT, UPDATE ON clep.alert_event TO clep_runtime;"))

case("P-27c", "an abstention starts reading as nothing to do",
     lambda: plant(RELEASES, '    "insufficient_evidence": INVESTIGATE,',
                   '    "insufficient_evidence": NONE,'))

case("P-27c", "an unmapped gate outcome defaults to advice",
     lambda: plant(RELEASES, "        return BY_OUTCOME[outcome]",
                   "        return BY_OUTCOME.get(outcome, NONE)"))

case("P-27c", "a release observation learns to record having acted",
     lambda: plant(SCHEMA10, "    observed_by           uuid NOT NULL,",
                   "    applied_at            timestamptz,\n"
                   "    observed_by           uuid NOT NULL,"))

# ------------------------------------------------------ P-27d the cost bound
case("P-27d", "an over-budget plan starts validating",
     lambda: plant(ROOT / "src/clep/agents/planner.py",
                   "    if plan.inputs.budget is not None and plan.estimated_cost > plan.inputs.budget:",
                   "    if False:"))

case("P-27d", "the sweep stops consulting the planner's estimate",
     lambda: plant(SCHEDULER, "from clep.agents.planner import PlanInputs, draft_plan, validate",
                   "from clep.agents.planner import PlanInputs\n"
                   "draft_plan = validate = None  # noqa: E305"))

case("P-27d", "a schedule may be created without a budget",
     lambda: plant(SCHEMA10, "    budget_limit             numeric(18, 9) NOT NULL,",
                   "    budget_limit             numeric(18, 9),"))

# ----------------------------------------------------- P-28 the CI evidence
case("P-28", "the recorded CI run stops blocking on a regression",
     lambda: plant_json(CI_EVIDENCE, lambda b: b["steps"]
                        ["blocking evaluation"].__setitem__("exitCode", 0)))

case("P-28", "an abstention starts letting the recorded pipeline through",
     lambda: plant_json(CI_EVIDENCE, lambda b: b["steps"]
                        ["abstention blocks"].__setitem__("exitCode", 0)))

case("P-28", "the CI evidence starts claiming a hosted run",
     lambda: plant_json(CI_EVIDENCE,
                        lambda b: b.__setitem__("environment", "GitHub Actions")))

case("P-28", "the installed package resolves to the working tree",
     lambda: plant_json(CI_EVIDENCE, lambda b: b["installation"].__setitem__(
         "package_file", str(ROOT / "src/clep/__init__.py"))))

# ------------------------------------------- P-29a analytics are derived
case("P-29a", "the analytics repository starts writing an aggregate",
     lambda: plant(ANALYTICS, "        return figures",
                   "        self._conn.execute(\n"
                   "            \"INSERT INTO clep.alert_event (id) VALUES (%s)\",\n"
                   "            (None,))\n"
                   "        return figures"))

case("P-29a", "a stored rollup table appears in the schema",
     lambda: plant(SCHEMA, "CREATE TABLE clep.alert_rule (",
                   "CREATE TABLE clep.quality_daily_rollup (\n"
                   "    id               uuid PRIMARY KEY,\n"
                   "    organization_id  uuid NOT NULL,\n"
                   "    mean_score       numeric(18, 9)\n"
                   ");\n\n"
                   "CREATE TABLE clep.alert_rule ("))

# ------------------------------------------ P-29b incompleteness is marked
case("P-29b", "a figure with no observations starts reading as healthy",
     lambda: plant(COMPLETENESS, '        reasons.append("no observation in this window produced a score, so "',
                   '        reasons.append("") if False else None\n'
                   '        _unused = ("no observation in this window produced a score, so "'))

case("P-29b", "samples excluded from a mean stop being reported",
     lambda: plant(COMPLETENESS, "    if unresolved_observations:", "    if False:"))

case("P-29b", "a figure over an unfinished run stops being marked",
     lambda: plant(COMPLETENESS, "    if incomplete_runs:", "    if False:"))

# ------------------------------------------- P-29c the benchmark is required
case("P-29c", "a leaderboard may be asked for without a benchmark",
     lambda: plant(ANALYTICS, "        if not suite_version_id:\n            raise AnalyticsError(",
                   "        if False:\n            raise AnalyticsError("))

# --------------------------------------------------- P-29d drift discipline
case("P-29d", "drift starts comparing against a single prior run",
     lambda: plant(DRIFT, "MINIMUM_HISTORY_FLOOR = 2", "MINIMUM_HISTORY_FLOOR = 1"))

case("P-29d", "the platform invents a drift tolerance for itself",
     lambda: plant(DRIFT, "    if tolerance is None or minimum_history is None:",
                   "    tolerance = tolerance if tolerance is not None else Decimal('0.05')\n"
                   "    minimum_history = minimum_history or 2\n"
                   "    if False:"))

case("P-29d", "the position relative to the observed range stops being reported",
     lambda: plant(DRIFT, "    position = None\n    if values:",
                   "    position = None\n    if False:"))

# ------------------------------------------- P-29e the report keeps its caveats
case("P-29e", "the executive report drops what is not established",
     lambda: plant(SCORECARD, "    for limitation in UNCALIBRATED:\n"
                              "        out.append(f\"- {limitation}\")",
                   "    for limitation in ():\n"
                   "        out.append(f\"- {limitation}\")"))

case("P-29e", "the incompleteness sentences are tidied out of the report",
     lambda: plant(SCORECARD, "    if not reasons:\n        return []",
                   "    if reasons or not reasons:\n        return []"))

case("P-29e", "the machine-readable export loses its limitations",
     lambda: plant(SCORECARD, '        "notEstablished": list(UNCALIBRATED),',
                   '        "notEstablished": [],'))

# ------------------------------------------------------------- P-30 alerting
case("P-30", "a rule starts firing at exactly its threshold",
     lambda: plant(ALERTS, "            return value < self.threshold",
                   "            return value <= self.threshold"))

case("P-30", "a rule may name a latency figure nothing computes",
     lambda: plant(ALERTS, '              "model_latency_maximum_ms", "evaluator_latency_p95_ms"),',
                   '              "model_latency_maximum_ms", "evaluator_latency_p95_ms",\n'
                   '              "model_latency_p999_ms"),'))

# ------------------------------------------------------------ P-15 and P-17
case("P-15", "an alert firing loses its one-per-run key",
     lambda: plant(SCHEMA, "CONSTRAINT uq_alert_event__rule_run UNIQUE",
                   "CONSTRAINT uq_alert_event__rule_run_removed UNIQUE"))

case("P-17", "a Phase 11 foreign key stops carrying the tenant",
     lambda: plant(SCHEMA, "    CONSTRAINT fk_alert_event__run\n"
                           "        FOREIGN KEY (organization_id, run_id)",
                   "    CONSTRAINT fk_alert_event__run\n"
                   "        FOREIGN KEY (run_id, organization_id)"))

# ------------------------------------------------------------ P-11 and P-12
case("P-11", "an analytics operation stops declaring its requirement",
     lambda: plant(CONTRACT, '        "operationId": "getQualityTrend",\n'
                             '        "summary": "Quality over time, with baselines flagged",',
                   '        "operationId": "getQualityTrend",\n'
                   '        "x-requirements-removed": [],\n'
                   '        "summary": "Quality over time, with baselines flagged",'))

case("P-12", "the code's schedule triggers drift from the contract",
     lambda: plant(SCHEDULES, 'SCHEDULE_TRIGGERS = ("schedule", "post_deployment", "canary")',
                   'SCHEDULE_TRIGGERS = ("schedule", "post_deployment")'))

case("P-12", "the alert dimensions drift from the contract",
     lambda: plant(ALERTS, 'DIMENSIONS = (QUALITY, COST, LATENCY)',
                   'DIMENSIONS = (QUALITY, COST)'))

# ----------------------------------------------------------- P-26 closure
case("P-26", "an earlier gate stops re-running the one before it",
     lambda: plant(PHASE10, "docs/evidence/phase-9/check_phase9.py",
                   "docs/evidence/phase-9/check_phase9_skipped.py"))


def main() -> int:
    rebuild_fast()
    if dirty():
        print("REFUSING: the tree is dirty; a restore would discard uncommitted work")
        print(dirty())
        return 2

    caught, leaked = 0, []
    for check_id, label, mutate in CASES:
        try:
            mutate()
            status = status_of(check_id)
        finally:
            restore()
        survivors = dirty()
        if survivors:
            leaked.append(f"{check_id} {label}: {survivors.splitlines()[:3]}")
        ok = status == "FAIL"
        caught += ok
        print(f"[{'CAUGHT' if ok else 'MISSED':<7}] {check_id:<6} {label} "
              f"({'reported FAIL' if ok else 'reported ' + status})")

    print(f"\nself-test: {caught}/{len(CASES)} planted violations caught")
    if leaked:
        print(f"RESTORATION FAILED: {len(leaked)} plant(s) survived")
        for line in leaked:
            print(f"  - {line}")
        return 1
    print("restoration verified: the working tree matches HEAD after every case")
    return 0 if caught == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
