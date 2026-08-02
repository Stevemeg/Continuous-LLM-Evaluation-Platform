"""Technology Spike Sprint validation.

The sprint's deliverable is two decisions. A decision is only as good as the
correspondence between what the ADR claims and what the run actually produced, so
the checks below compare the ADR prose against the machine-readable results
rather than against a reader's memory of them.

That correspondence is the failure mode worth guarding. Numbers in prose drift:
a run is repeated, a figure improves, the ADR is not updated, and the record now
argues for a decision on evidence that no longer exists.

Usage: python docs/evidence/spike-sprint/check_spike_sprint.py <repo_root>
Exits non-zero on any FAIL.
"""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
EV = ROOT / "docs" / "evidence" / "spike-sprint"
ADR = ROOT / "docs" / "adr"
rows = []


def add(cid, status, detail):
    rows.append((cid, status, detail))
    print(f"[{status:<7}] {cid:<6} {detail}")


def load(name):
    return json.loads((EV / name).read_text(encoding="utf-8"))


def text(p):
    return p.read_text(encoding="utf-8")


# --------------------------------------------------------------- S-1 evidence
try:
    s1 = load("s1-results.json")
except Exception as e:
    add("S-1", "FAIL", f"cannot read s1-results.json: {e}")
    s1 = []

a_trials = [r for r in s1 if r["regime"] == "A"]
b_trials = [r for r in s1 if r["regime"] == "B"]

clean_a = [r for r in a_trials
           if r["samples_lost"] == 0 and r["samples_recomputed"] == 0
           and r["cost_double_counted"] == 0]
add("S-1a", "PASS" if a_trials and len(clean_a) == len(a_trials) else "FAIL",
    f"regime A: {len(clean_a)}/{len(a_trials)} trials satisfied every zero-condition "
    f"- the result the spike went on to show establishes nothing")

# Regime B is the decisive one: BOTH candidates must have failed identically,
# otherwise the ADR's central claim is not what the run produced.
naive = [r for r in b_trials if r["mode"] == "naive"]
idem = [r for r in b_trials if r["mode"] == "idempotent"]
naive_bad = [r for r in naive if r["cost_double_counted"] == 1 and r["cost_units"] == 410]
idem_ok = [r for r in idem if r["cost_double_counted"] == 0 and r["cost_units"] == 400]
add("S-1b", "PASS" if len(naive_bad) == 2 and len(naive) == 2 else "FAIL",
    f"regime B, naive ledger: {len(naive_bad)}/{len(naive)} candidates double-counted "
    f"cost (410 against an expected 400) - neither engine gives exactly-once effects")
add("S-1c", "PASS" if len(idem_ok) == 2 and len(idem) == 2 else "FAIL",
    f"regime B, idempotent ledger: {len(idem_ok)}/{len(idem)} candidates recorded cost "
    f"exactly once - the application key is what satisfies REQ-N-REL-2, not the engine")

recomputed = [r for r in b_trials if r["samples_recomputed"] >= 1]
add("S-1d", "PASS" if len(recomputed) == len(b_trials) and b_trials else "FAIL",
    f"regime B: {len(recomputed)}/{len(b_trials)} candidates recomputed a completed "
    f"sample - the zero-condition ADR-001 reclassified")

lost = [r for r in s1 if r["samples_lost"] != 0]
add("S-1e", "PASS" if not lost else "FAIL",
    f"no run lost a sample under any fault, in either regime: {len(s1) - len(lost)}/{len(s1)}")

# Every trial, not one of them. A dict keyed by candidate keeps only the last row
# and would pass while the rest of the file disagreed with it - which is exactly
# what this check's own self-test caught it doing.
EXPECT_BESPOKE = {"C1": 0, "C2": 22}
bespoke = {c: sorted({r["bespoke_loc"] for r in s1 if r["candidate"] == c})
           for c in EXPECT_BESPOKE}
consistent = all(v == [EXPECT_BESPOKE[c]] for c, v in bespoke.items())
add("S-1f", "PASS" if consistent else "FAIL",
    f"bespoke state-management lines, counted from tags in the source and agreeing "
    f"across every trial: C1={bespoke['C1']} C2={bespoke['C2']}")

dup = {r["duplicate_submission"] for r in s1}
add("S-1g", "PASS" if all("rejected" in d for d in dup) else "FAIL",
    f"duplicate submission of the same work unit rejected in every run "
    f"({len(dup)} distinct rejection reason(s))")

# --------------------------------------------------------------- S-2 evidence
try:
    s2 = load("s2-results.json")
except Exception as e:
    add("S-2", "FAIL", f"cannot read s2-results.json: {e}")
    s2 = {}

fails = s2.get("failures", [])
correct = {ap: sum(1 for r in fails if r["approach"] == ap and r["correct"])
           for ap in "ABC"}
add("S-2a", "PASS" if correct.get("B") == 4 else "FAIL",
    f"approach B distinguished {correct.get('B')}/4 failure modes")
add("S-2b", "PASS" if correct.get("A", 9) < 4 and correct.get("C", 9) < 4 else "FAIL",
    f"the aggregation library did not: A={correct.get('A')}/4 C={correct.get('C')}/4")

# The collision analysis, recomputed here rather than trusted from the summary.
collisions = {}
for ap in "ABC":
    sig = {}
    for r in fails:
        if r["approach"] == ap:
            sig.setdefault(json.dumps(r["signals"], sort_keys=True), []).append(r["mode"])
    collisions[ap] = [v for v in sig.values() if len(v) > 1]
ac_collide = all(any(set(c) == {"malformed response", "outage"} for c in collisions[ap])
                 for ap in "AC")
add("S-2c", "PASS" if ac_collide and not collisions["B"] else "FAIL",
    f"outage and malformed response carry identical structured signals for A and C "
    f"({ac_collide}); approach B has {len(collisions['B'])} collision(s)")

structural = [r for r in fails if r["approach"] == "B" and r["correct"]
              and r.get("structural")]
add("S-2d", "PASS" if len(structural) == 4 else "FAIL",
    f"approach B's four mappings rest on structural signals, not message text: "
    f"{len(structural)}/4")

iso = s2.get("isolation", [])
add("S-2e", "PASS" if iso and all(r["isolated"] for r in iso) else "FAIL",
    f"a failing evaluation candidate left its siblings valid in "
    f"{sum(1 for r in iso if r['isolated'])}/{len(iso)} approaches")

st = s2.get("credential_selftest", {})
add("S-2f", "PASS" if st.get("detector_works") else "FAIL",
    f"the credential leak detector was proven able to fail: planted on "
    f"{len(st.get('surfaces', []))} surfaces, detected on {len(st.get('detected_on', []))}")

dbg = {r["approach"]: r["debug_logging"] for r in s2.get("credential", [])
       if "debug_logging" in r}
leaks = {ap for ap, v in dbg.items() if "LEAKED=none" not in v}
add("S-2g", "PASS" if leaks == {"A", "C"} else "FAIL",
    f"with debug logging enabled the credential appeared for approaches "
    f"{sorted(leaks) or 'none'} and not for {sorted(set('ABC') - leaks)}")

usage = [r for r in s2.get("usage", []) if r.get("retrievable")]
add("S-2h", "PASS" if usage and all(r["reconciles"] for r in usage) else "FAIL",
    f"per-call usage reconciled against provider-reported usage on every endpoint "
    f"actually reached: {sum(1 for r in usage if r['reconciles'])}/{len(usage)}")

rp = s2.get("real_providers", [])
amb = s2.get("status_code_ambiguity", {})
add("S-2i", "PASS" if amb else "FAIL",
    f"a single semantic condition arrived under different HTTP statuses across real "
    f"providers: {amb or 'none observed'}")

# --------------------------------------------------------------- ADR currency
a1 = text(ADR / "ADR-001-durable-execution.md")
a3 = text(ADR / "ADR-003-provider-abstraction.md")
readme = text(ADR / "README.md")

undecided = [n for n, t in (("ADR-001", a1), ("ADR-003", a3))
             if "NOT DECIDED" in t or "gated on spike" in t.split("## Alternatives")[0]]
add("S-3a", "PASS" if not undecided else "FAIL",
    f"both spike-gated ADRs now record a decision; still undecided: {undecided or 'none'}")

accepted = all("**Accepted — decided on executed spike evidence**" in t
               for t in (a1, a3))
add("S-3b", "PASS" if accepted else "FAIL",
    "both ADRs carry the accepted-on-evidence status")

add("S-3c", "PASS" if "Proposed — gated on spike. NOT DECIDED" not in readme else "FAIL",
    "the ADR index no longer advertises either ADR as undecided")

# Every ADR file must appear in the index, or the index is not an index.
adr_files = sorted(p.name for p in ADR.glob("ADR-*.md"))
missing = [f for f in adr_files if f not in readme]
add("S-3d", "PASS" if not missing else "FAIL",
    f"{len(adr_files)} ADR files, all listed in the index; missing: {missing or 'none'}")

# --------------------------------------------------- prose against the numbers
def cites(t, *needles):
    return [n for n in needles if n not in t]

miss1 = cites(a1, "410", "400", "22 tagged lines", "0 tagged lines")
add("S-4a", "PASS" if not miss1 else "FAIL",
    f"ADR-001 quotes the measured figures; absent from the prose: {miss1 or 'none'}")

miss3 = cites(a3, "2 / 4", "4 / 4", "3 / 4", "16,973", "4,540", "429", "401")
add("S-4b", "PASS" if not miss3 else "FAIL",
    f"ADR-003 quotes the measured figures; absent from the prose: {miss3 or 'none'}")

# A claim in either ADR that the run does not support is worse than no claim.
c1_lat = {r["resume_latency_s"] for r in a_trials if r["candidate"] == "C1"}
add("S-4c", "PASS" if "9.67" in a1 and abs(sorted(c1_lat)[len(c1_lat) // 2] - 9.67) < 0.2
    else "FAIL",
    f"ADR-001's quoted median resume latency matches the recorded trials: {sorted(c1_lat)}")

# --------------------------------------------------------------- hygiene
secrets = [(r"sk-[A-Za-z0-9]{16,}", "provider key"),
           (r"gh[pousr]_[A-Za-z0-9]{16,}", "forge token"),
           (r"AKIA[0-9A-Z]{16}", "cloud key id"),
           (r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "private key")]
hits = []
for p in sorted(EV.rglob("*")):
    if not p.is_file():
        continue
    try:
        t = p.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        continue
    for rx, lb in secrets:
        if re.search(rx, t):
            hits.append(f"{p.name}:{lb}")
add("S-5a", "PASS" if not hits else "FAIL",
    f"no credential pattern in any spike artifact ({len(list(EV.glob('*')))} files); "
    f"hits: {hits or 'none'}")

expected_files = ["spike_durable_execution.py", "spike_resume_latency.py",
                  "spike_provider_abstraction.py", "common.py", "crash.py",
                  "cand_temporal_worker.py", "cand_arq_worker.py", "port.py",
                  "adapters.py", "stub_provider.py", "leak_probe.py",
                  "s1-output.txt", "s1b-output.txt", "s2-output.txt",
                  "s1-results.json", "s1b-results.json", "s2-results.json",
                  "README.md", "environment.md"]
absent = [f for f in expected_files if not (EV / f).exists()]
add("S-5b", "PASS" if not absent else "FAIL",
    f"every spike artifact is committed, so the runs can be re-executed and "
    f"challenged; absent: {absent or 'none'}")

# The sprint closes ADRs. It must not have started implementing against them.
impl = []
for pat in ("src/**/*.py", "app/**/*.py", "pyproject.toml", "requirements*.txt",
            "setup.py", "tests/**/*.py"):
    impl += [str(p.relative_to(ROOT)) for p in ROOT.glob(pat)]
add("S-5c", "PASS" if not impl else "FAIL",
    f"the sprint closed decisions without beginning implementation: "
    f"{len(impl)} implementation artifact(s) found {impl[:3]}")

summary = {}
for _, s, _ in rows:
    summary[s] = summary.get(s, 0) + 1
print("-" * 78)
print("SUMMARY: " + json.dumps(summary))
sys.exit(1 if summary.get("FAIL") else 0)
