"""Does the Phase 13 gate actually fail when the property it checks is false?

A validator nobody has seen fail is a validator nobody has calibrated. So every
meaningful check here is put in front of a **real behavioural violation** — the
property broken, not a string the checker searches for — and required to report
FAIL for that check specifically.

Three rules, each earned by a Phase 12 failure:

**The subsets are derived and verified.** Phase 12's `rebuild_fast` excised two
expensive checks and silently took four others with them, so four checks went a
whole run with no plant able to reach them. Here the script is split on its own
check banners, subsets are built by *selecting* ids, and the ids actually present
in a subset are compared against the ids requested. A subset that lost a check
fails before any plant runs.

**Restoration is repository-wide and verified against HEAD.** No list of paths
that somebody has to remember to extend. After every plant, `git status
--porcelain` must be empty; if it is not, the run stops rather than continuing
against a repository that no longer matches what was measured.

**A plant that does not change behaviour is a failed plant.** If a mutation is
applied and the gate still passes, that is reported — it means either the check
cannot see the violation, or the mutation did not create one. Both are defects.

Usage: python docs/evidence/phase-13/selftest_phase13.py <repo_root>
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(ROOT / "docs" / "evidence" / "tooling"))
import workspace as W  # noqa: E402

PY = os.environ.get("CLEP_TEST_PYTHON", sys.executable)
GATE = ROOT / "docs" / "evidence" / "phase-13" / "check_phase13.py"
BANNER = re.compile(r"^# =+ (P-\d+|done)\b", re.M)

#: Checks excluded from planting because each one costs minutes: the full suite,
#: the nested Phase 12 gate, the fresh-environment build, and the network
#: advisory scan. Every one is exercised once by the complete gate; what they do
#: not get is a plant, and that is recorded rather than glossed.
EXPENSIVE = {"P-1", "P-5", "P-10", "P-30"}


def blocks(source: str) -> tuple[str, dict[str, str], str]:
    """(header, {check id: block}, footer). Derived from the banners, not listed."""
    marks = [(m.group(1), m.start()) for m in BANNER.finditer(source)]
    if not marks:
        raise SystemExit("no check banners found; the gate's shape changed")
    header = source[:marks[0][1]]
    found, footer = {}, ""
    for i, (name, start) in enumerate(marks):
        end = marks[i + 1][1] if i + 1 < len(marks) else len(source)
        if name == "done":
            footer = source[start:]
        else:
            found[name] = source[start:end]
    return header, found, footer


def subset(source: str, keep: set[str]) -> tuple[str, set[str]]:
    header, found, footer = blocks(source)
    missing = keep - set(found)
    if missing:
        raise SystemExit(f"requested checks that do not exist: {sorted(missing)}")
    body = "".join(found[name] for name in sorted(keep, key=_order))
    text = header + body + footer
    # The verification Phase 12 did not have: what is actually in the subset,
    # read back out of the generated text, compared with what was asked for.
    present = {m.group(1) for m in re.finditer(r'add\("(P-\d+)"', text)}
    return text, present


def _order(name: str) -> int:
    return int(name.split("-")[1])


def git(*args):
    return subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True,
                          text=True, encoding="utf-8", errors="replace").stdout


def dirty() -> list[str]:
    return [l for l in git("status", "--porcelain").splitlines() if l.strip()]


def run_subset(text: str, work: Path, timeout=2400):
    script = work / "check_subset.py"
    script.write_text(text, encoding="utf-8", newline="\n")
    p = subprocess.run([PY, str(script), str(ROOT)], cwd=str(ROOT),
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    out = (p.stdout or "") + (p.stderr or "")
    failed = set(re.findall(r"^\[FAIL\s*\]\s+(P-\d+)", out, re.M))
    return p.returncode, failed, out


# --------------------------------------------------------------- mutations
def edit(rel: str, old: str, new: str):
    """Replace exact text in a file. Returns a restore callable."""
    path = ROOT / rel
    original = path.read_bytes()
    text = original.decode("utf-8")
    if old not in text:
        raise SystemExit(f"plant target not present in {rel}: {old[:60]!r}")
    path.write_bytes(text.replace(old, new, 1).encode("utf-8"))
    return lambda: path.write_bytes(original)


def create(rel: str, content: str):
    path = ROOT / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")

    def _undo():
        if path.exists():
            path.unlink()
        for parent in path.parents:
            if parent == ROOT:
                break
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    return _undo


PLANTS = [
    # ---------------------------------------------------------- tier A
    # The first version of this plant created a table with no organization_id
    # and was NOT caught — correctly, as it turned out: a table without the
    # column is a global table, which the schema rules permit. The plant was
    # wrong, not the check. This one breaks a rule the checker actually holds.
    ("P-2", "an index is added that does not use the ix_ or uq_ prefix",
     lambda: edit("docs/data/schema/13-observability.sql",
                  "CREATE INDEX ix_run__correlation",
                  "CREATE INDEX run_correlation_idx")),
    ("P-4", "the committed traceability matrix is edited by hand",
     lambda: edit("docs/evidence/phase-3/traceability-matrix.md",
                  "| `REQ-N-OBS-1` |", "| `REQ-N-OBS-1` (hand-edited) |")),
    ("P-8", "a declared metric gains a run identifier label",
     lambda: edit("src/clep/telemetry/catalog.py",
                  'labels={"completeness": COMPLETENESS}),',
                  'labels={"completeness": COMPLETENESS,\n'
                  '                    "run_id": ("a", "b")}),')),
    ("P-11", "a core module imports OpenTelemetry at module scope",
     lambda: edit("src/clep/telemetry/port.py",
                  "import time\nfrom contextlib import contextmanager",
                  "import time\nimport opentelemetry\n"
                  "from contextlib import contextmanager")),
    ("P-12", "telemetry becomes a runtime dependency instead of an extra",
     lambda: edit("pyproject.toml", '    "redis>=5.0",\n]',
                  '    "redis>=5.0",\n    "opentelemetry-sdk>=1.27",\n]')),
    ("P-13", "the catalogue's vocabulary drifts from the schema's",
     lambda: edit("src/clep/telemetry/catalog.py",
                  'COMPLETENESS = ("complete", "partial", "exhausted", '
                  '"cancelled", "rejected")',
                  'COMPLETENESS = ("complete", "partial", "exhausted", '
                  '"cancelled")')),
    ("P-18", "an operational endpoint is registered on the tenant application",
     lambda: edit("src/clep/api/app.py",
                  "    _assert_every_route_is_guarded(app)",
                  '    @app.get("/metrics")\n'
                  "    async def _metrics():\n        return {}\n\n"
                  "    _assert_every_route_is_guarded(app)")),
    ("P-19", "an ADR this phase depends on is no longer Accepted",
     lambda: edit("docs/adr/ADR-022-telemetry-port.md",
                  "| Status | **Accepted** |", "| Status | **Proposed** |")),
    ("P-21", "D-7 is dropped from the tracked-debt register",
     lambda: edit("docs/architecture/tracked-debt.md",
                  "## D-7 — the correlation chain has no artifact hop",
                  "### Formerly D-7 — the correlation chain has no artifact hop")),
    # `edit` replaces one occurrence, which is what exposed the check: it tested
    # presence, so promoting a single blocked target left it satisfied. The
    # check now counts, and this plant promotes exactly one target — the
    # smallest violation that must still be caught.
    ("P-22", "one blocked SLO target is quietly promoted in the strategy",
     lambda: edit("docs/architecture/observability-strategy.md",
                  "TARGET NOT YET SET", "TARGET SET")),
    ("P-23", "a credential-shaped string enters the working tree",
     lambda: create("src/clep/telemetry/_leak.py",
                    'DSN = "postgres://admin:' + 'sup3rs3cret' + '@db.invalid/x"\n')),
    ("P-24", "an attribution trailer enters a governed file",
     lambda: create("docs/evidence/phase-13/_note.md",
                    "Reviewed" + "-by: someone\n")),
    ("P-27", "a stray editor artifact is tracked",
     lambda: create("docs/evidence/phase-13/check_phase13.py.orig", "x\n")),
    ("P-28", "a Phase 14 artifact appears",
     lambda: create("docs/data/schema/14-deployment.sql", "-- Phase 14\n")),
    ("P-29", "a validator exists that no gate invokes",
     lambda: create("docs/evidence/phase-13/check_orphan.py",
                    "print('SUMMARY: 0/0 PASS')\n")),
    # ---------------------------------------------------------- tier B
    ("P-6", "the audit hop stops carrying the correlation identifier",
     lambda: edit("src/clep/api/audit.py",
                  "justification, target_content_digest, current_id()))",
                  "justification, target_content_digest, None))")),
    ("P-7", "the queue-time class loses its only emitter",
     lambda: edit("src/clep/orchestration/worker.py",
                  'telemetry.observe("clep_work_unit_queue_duration_ms",\n'
                  '                          max(0.0, waited), queue=queue)',
                  "pass  # emitter removed")),
    ("P-9", "the log surface stops resolving classified content",
     lambda: edit("src/clep/telemetry/logs.py",
                  "        return for_surface(str(value.value), value.data_class, \"log\")",
                  "        return str(value.value)")),
    ("P-14", "cost reconciliation compares a number with itself",
     lambda: edit("src/clep/analytics/cost.py",
                  "        recomputed = price_book.cost_of(model, prompt_tokens,\n"
                  "                                       completion_tokens).amount",
                  "        recomputed = attributed")),
    ("P-15", "an exhausted budget stops being a terminal outcome",
     lambda: edit("src/clep/orchestration/runner.py",
                  'outcome.completeness = "exhausted"',
                  'outcome.completeness = "complete"')),
    ("P-16", "a published SLO figure no longer appears in the raw output",
     lambda: edit("docs/evidence/phase-13/slo-targets.md",
                  "| p95 | 97.1 ms |", "| p95 | 12.0 ms |")),
    ("P-17", "the operational surface starts leaking a tenant dimension",
     lambda: edit("src/clep/telemetry/exposition.py",
                  "    def event(self, name, attributes, correlation) -> None:",
                  "    def event(self, name, attributes, correlation) -> None:\n"
                  "        if correlation is not None:\n"
                  "            self._series[(name, (('correlation_id',\n"
                  "                correlation.correlation_id),))] = _Series()\n"
                  "            self._specs[name] = type('S', (), {\n"
                  "                'name': name, 'kind': 'counter',\n"
                  "                'description': 'leak', 'unit': '1'})()\n"
                  "        return None\n\n"
                  "    def _unused(self, name, attributes, correlation) -> None:")),
    ("P-20", "the workspace cleanup goes back to discarding its errors",
     lambda: edit("docs/evidence/tooling/workspace.py",
                  "            shutil.rmtree(path, **{_RMTREE_HANDLER: _retry_writable})",
                  "            shutil.rmtree(path, ignore_errors=True)")),
]


def main() -> int:
    source = GATE.read_text(encoding="utf-8")
    _, found, _ = blocks(source)
    all_ids = set(found)
    planted = {cid for cid, _, _ in PLANTS}

    print(f"gate checks        : {len(all_ids)} "
          f"({', '.join(sorted(all_ids, key=_order))})")
    print(f"no plant, too slow : {sorted(EXPENSIVE, key=_order)} — each is run "
          f"once by the complete gate")
    uncovered = sorted(all_ids - EXPENSIVE - planted, key=_order)
    print(f"planted            : {len(planted)}")
    if uncovered:
        print(f"[note] no plant for: {uncovered}")
    unknown = sorted(planted - all_ids, key=_order)
    if unknown:
        print(f"[FAIL] plants target checks the gate does not contain: {unknown}")
        return 1

    if dirty():
        print("[FAIL] the working tree is not clean; a self-test that starts "
              "dirty cannot verify its own restoration")
        return 1

    caught, missed, errors = 0, [], []
    with W.workspace("clep-selftest13-") as work:
        for cid, description, plant in PLANTS:
            # One check per run. A subset carrying its neighbours would cost
            # minutes per plant and prove nothing extra: what is being
            # established is that THIS check sees THIS violation.
            text, present = subset(source, {cid})
            if present != {cid}:
                print(f"[FAIL] the derived subset for {cid} contains "
                      f"{sorted(present)}; the excision lost or gained a check")
                return 1
            restore = None
            try:
                restore = plant()
                code, failed, out = run_subset(text, work)
                if cid in failed:
                    caught += 1
                    print(f"[caught ] {cid:<6} {description}")
                else:
                    missed.append((cid, description))
                    print(f"[MISSED ] {cid:<6} {description} "
                          f"(exit {code}, reported: {sorted(failed) or 'none'})")
            except SystemExit as e:
                errors.append((cid, str(e)))
                print(f"[ERROR  ] {cid:<6} {e}")
            finally:
                if restore is not None:
                    restore()
            leftover = dirty()
            if leftover:
                print(f"[FAIL] restoration incomplete after {cid}: {leftover[:4]}")
                return 1

    print(f"\nrestoration verified against HEAD after every plant: working tree "
          f"clean ({len(dirty())} modified path(s))")
    print(f"SUMMARY: {caught}/{len(PLANTS)} plant(s) caught")
    if missed:
        print("MISSED:")
        for cid, description in missed:
            print(f"  {cid}: {description}")
    for cid, message in errors:
        print(f"ERROR {cid}: {message}")
    return 0 if caught == len(PLANTS) and not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
