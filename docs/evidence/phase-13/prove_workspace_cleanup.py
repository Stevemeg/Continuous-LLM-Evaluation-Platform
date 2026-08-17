"""Executed proof that the validator workspace cleanup works, and why it did not.

Six proofs, each one a thing that was asserted about the leak and is now shown.
The first is a control: it reproduces the original defect against the original
code, because a fix for a failure nobody demonstrated is a fix for a guess.

W-1  the defect is real           -- ignore_errors=True leaves a read-only tree
W-2  the fix removes that tree    -- remove_tree clears the attribute and succeeds
W-3  a transient handle is ridden out -- a handle closed during the backoff window
W-4  a permanent handle is reported   -- and named, not silently discarded
W-5  containment survives depth   -- a grandchild process's mkdtemp lands inside
W-6  the sweep refuses a junction -- pointed at the repository, and the tree is intact

Usage: python docs/evidence/phase-13/prove_workspace_cleanup.py <repo_root>
Exits non-zero on any FAIL.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(ROOT / "docs" / "evidence" / "tooling"))
import workspace as W  # noqa: E402

PY = os.environ.get("CLEP_TEST_PYTHON", sys.executable)
SYS_TMP = Path(tempfile.gettempdir()).resolve()
results = []


def add(cid, ok, detail):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL':<4}] {cid:<5} {detail}")


def make_packfile_tree() -> Path:
    """A directory shaped exactly like the ones that leaked: a git pack, read-only.

    Not an approximation. Every one of the 936 abandoned workspaces contained
    nothing but `tree/.git/objects/pack/*.idx`, `*.pack` and `*.rev`, all three
    carrying the read-only attribute git sets on a packfile.
    """
    base = Path(tempfile.mkdtemp(prefix="clep-proof-"))
    pack = base / "tree" / ".git" / "objects" / "pack"
    pack.mkdir(parents=True)
    for suffix in (".idx", ".pack", ".rev"):
        f = pack / f"pack-0123456789abcdef{suffix}"
        f.write_bytes(b"PACK" + b"\0" * 64)
        os.chmod(f, stat.S_IREAD)
    return base


# ============================ W-1 the defect, reproduced against the original code
victim = make_packfile_tree()
shutil.rmtree(victim, ignore_errors=True)
survived = victim.exists()
leftover = [p.name for p in victim.rglob("*") if p.is_file()] if survived else []
add("W-1", survived,
    f"shutil.rmtree(ignore_errors=True) on a read-only packfile tree: directory "
    f"survives={survived}, {len(leftover)} file(s) left behind and no error raised "
    f"-- this is the original defect, reproduced" if survived else
    "the defect did not reproduce; this platform does not exhibit it")

# ================================================ W-2 the fix removes the same tree
try:
    W.remove_tree(victim)
    removed, why = not victim.exists(), ""
except OSError as exc:
    removed, why = False, f" ({exc})"
add("W-2", removed,
    f"workspace.remove_tree on the identical tree: removed={removed}{why}")

# ============================================ W-3 a handle that closes is ridden out
transient = make_packfile_tree()
held = open(transient / "tree" / "held.bin", "wb")
held.write(b"x")
threading.Timer(0.35, held.close).start()
t0 = time.monotonic()
try:
    W.remove_tree(transient)
    ok3, note = not transient.exists(), f"after {time.monotonic() - t0:.2f}s of backoff"
except OSError as exc:
    ok3, note = False, f"gave up: {exc}"
add("W-3", ok3,
    f"a file handle open when removal starts and closed 0.35s later: removed={ok3}, "
    f"{note}")

# ======================================= W-4 a handle that never closes is reported
permanent = make_packfile_tree()
pinned = open(permanent / "tree" / "pinned.bin", "wb")
pinned.write(b"x")
pinned.flush()
raised, message = False, ""
try:
    W.remove_tree(permanent, attempts=3)
except OSError as exc:
    raised, message = True, str(exc)
pinned.close()
# On Windows an open handle blocks deletion, so the honest result is a raised
# error naming the path. On POSIX an unlinked-but-open file is removed normally,
# so removal succeeding there is equally correct. What must never happen on
# either is the original behaviour: failure reported as success.
expected = raised if os.name == "nt" else (raised or not permanent.exists())
add("W-4", expected and (not raised or str(permanent) in message),
    f"a file handle held open throughout: raised={raised}, path named in the "
    f"message={str(permanent) in message}, platform={os.name} -- the failure is "
    f"reported rather than discarded")
W.remove_tree(permanent)

# ==================================== W-5 containment holds through a grandchild
before = {p.name for p in SYS_TMP.iterdir() if p.name.startswith("clep-gate-")}
grandchild = textwrap.dedent("""
    import tempfile, sys
    print(tempfile.mkdtemp(prefix="clep-gate-"))
""")
child = textwrap.dedent(f"""
    import subprocess, sys
    p = subprocess.run([sys.executable, "-c", {grandchild!r}],
                       capture_output=True, text=True)
    sys.stdout.write(p.stdout)
""")
with W.workspace("clep-contain-") as base:
    env = W.contained_env(base)
    proc = subprocess.run([PY, "-c", child], capture_output=True, text=True, env=env)
    made = Path(proc.stdout.strip()) if proc.stdout.strip() else None
    inside = bool(made) and base.resolve() in made.resolve().parents
    depth = "grandchild"
after = {p.name for p in SYS_TMP.iterdir() if p.name.startswith("clep-gate-")}
add("W-5", inside and not (after - before),
    f"a {depth} process two levels down called mkdtemp(prefix='clep-gate-'): it "
    f"landed inside the contained root={inside}; new clep-gate-* directories in "
    f"the system temporary directory: {len(after - before)}")

# =============================== W-6 the sweep refuses a junction into the repository
junction = SYS_TMP / "clep-gate-junction-proof"
made_junction = False
if os.name == "nt" and not junction.exists():
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(ROOT)],
                       capture_output=True, text=True)
    made_junction = junction.exists()
status_before = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                               capture_output=True, text=True).stdout
reclaimed, freed, residue = W.sweep(ROOT, quiet_seconds=0, dry_run=True)
status_after = subprocess.run(["git", "status", "--porcelain"], cwd=str(ROOT),
                              capture_output=True, text=True).stdout
refused = [r for r in residue if "junction-proof" in r]
if made_junction:
    os.rmdir(junction)  # removes the junction itself, never what it points at
add("W-6", bool(refused) and status_before == status_after and ROOT.exists(),
    f"a junction in the temporary directory pointing at the repository: "
    f"created={made_junction}, refused by the sweep={bool(refused)}; repository "
    f"still present={ROOT.exists()}, working tree unchanged="
    f"{status_before == status_after}; the same dry-run sweep sees "
    f"{len(reclaimed)} reclaimable workspace(s), {freed / 1048576:.2f} MB")

print(f"\nSUMMARY: {sum(results)}/{len(results)} PASS")
raise SystemExit(0 if all(results) else 1)
