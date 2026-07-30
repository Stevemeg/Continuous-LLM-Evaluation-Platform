"""M1.1 cross-document consistency checker.

Each use case declares its own personas and capabilities in its section header.
Those declarations are the single source of truth. Four summary tables restate
that information:

  1. the use-case index table in use-cases.md
  2. the capability coverage table in use-cases.md
  3. the persona coverage table in use-cases.md
  4. the coverage confirmation table in personas.md
  5. the per-persona "Use cases" list in personas.md

Summary tables restating information stored elsewhere drift silently. This
checker derives the truth from the section headers and fails if any table
disagrees.

Usage: python docs/evidence/M1.1/check_crossrefs.py <repo_root>
"""
import io
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")
root = sys.argv[1] if len(sys.argv) > 1 else "."
uc = io.open(f"{root}/docs/product/use-cases.md", encoding="utf-8").read()
pe = io.open(f"{root}/docs/product/personas.md", encoding="utf-8").read()

# --- ground truth: parse each use case's own header ------------------------
truth_u, truth_c, truth_by_uc = {}, {}, {}
blocks = re.split(r"\n## (UC-\d\d) — ", uc)
for i in range(1, len(blocks), 2):
    ucid, body = blocks[i], blocks[i + 1]
    head = body.split("**Trigger.**")[0]
    us = {f"U-{u}" for u in re.findall(r"\bU-(\d)\b", head)}
    cs = {f"CAP-{c}" for c in re.findall(r"\bCAP-(\d\d)\b", head)}
    truth_by_uc[ucid] = (us, cs)
    for u in us:
        truth_u.setdefault(u, set()).add(ucid)
    for c in cs:
        truth_c.setdefault(c, set()).add(ucid)

fmt = lambda s: ", ".join(sorted(s))
failures = []


def compare(label, declared, actual):
    if declared != actual:
        failures.append(
            f"{label}: missing={sorted(actual - declared)} extra={sorted(declared - actual)}")
        return f"MISMATCH {label}"
    return f"ok {label}"


print("=" * 78)
print("M1.1 CROSS-DOCUMENT CONSISTENCY")
print("=" * 78)
print(f"use cases parsed: {len(truth_by_uc)}")
print(f"personas referenced: {len(truth_u)}   capabilities referenced: {len(truth_c)}")

# --- 1. use-case index table ----------------------------------------------
print("\n--- use-case index table (capabilities column) ---")
idx = {}
for m in re.finditer(r"\|\s*\[(UC-\d\d)\]\([^)]*\)\s*\|[^|]*\|[^|]*\|[^|]*\|([^|]*)\|", uc):
    idx[m.group(1)] = set(re.findall(r"CAP-\d\d", m.group(2)))
if not idx:
    failures.append("use-case index table: parsed nothing")
    print("  !! PARSED NOTHING")
for ucid in sorted(truth_by_uc):
    print("  " + compare(f"index {ucid}", idx.get(ucid, set()), truth_by_uc[ucid][1]))

# --- 2. capability coverage table -----------------------------------------
print("\n--- capability coverage table (use-cases.md) ---")
capt = {m.group(1): set(re.findall(r"UC-\d\d", m.group(2)))
        for m in re.finditer(r"\|\s*\*\*(CAP-\d\d)\*\*[^|]*\|([^|]*)\|", uc)}
if not capt:
    failures.append("capability coverage table: parsed nothing")
    print("  !! PARSED NOTHING")
for c in sorted(truth_c):
    print("  " + compare(c, capt.get(c, set()), truth_c[c]))

# --- 3. persona coverage table (use-cases.md) ------------------------------
print("\n--- persona coverage table (use-cases.md) ---")
pu = {f"U-{m.group(1)}": set(re.findall(r"UC-\d\d", m.group(2)))
      for m in re.finditer(r"\|\s*\*\*U-(\d)\*\*\s*\w+\s*\|([^|]*)\|", uc)}
if not pu:
    failures.append("persona coverage table (use-cases.md): parsed nothing")
    print("  !! PARSED NOTHING")
for u in sorted(truth_u):
    print("  " + compare(u, pu.get(u, set()), truth_u[u]))

# --- 4. coverage confirmation table (personas.md) --------------------------
print("\n--- coverage confirmation table (personas.md) ---")
pc = {f"U-{m.group(1)}": set(re.findall(r"UC-\d\d", m.group(2)))
      for m in re.finditer(r"\|\s*U-(\d)\s[^|]*\|[^|]*\|([^|]*)\|", pe)}
if not pc:
    failures.append("coverage confirmation table (personas.md): parsed nothing")
    print("  !! PARSED NOTHING")
for u in sorted(truth_u):
    print("  " + compare(u, pc.get(u, set()), truth_u[u]))

# --- 5. per-persona "Use cases" list (personas.md) -------------------------
print("\n--- per-persona 'Use cases' list (personas.md) ---")
pp = {f"U-{m.group(1)}": set(re.findall(r"UC-\d\d", m.group(2)))
      for m in re.finditer(r"## U-(\d) —.*?### Use cases\s*\n([^\n]*)", pe, re.S)}
if not pp:
    failures.append("per-persona use-case list (personas.md): parsed nothing")
    print("  !! PARSED NOTHING")
for u in sorted(truth_u):
    print("  " + compare(u, pp.get(u, set()), truth_u[u]))

print("\n" + "-" * 78)
if failures:
    print(f"FAIL — {len(failures)} inconsistency(ies):")
    for f in failures:
        print("  " + f)
    sys.exit(1)
print("PASS — every summary table agrees with the use-case section headers.")
