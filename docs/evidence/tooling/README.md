# Shared validator tooling

Code used by more than one phase gate lives here, so that there is one copy to
review rather than one copy per phase to drift.

| Module | What it is for |
|---|---|
| [`workspace.py`](workspace.py) | Temporary-workspace containment and reclamation for the isolated-clone gate runner |

## The rule this exists to enforce

**A validator that starts a nested gate must contain that gate's temporary
directory, and must remove it in a way that can report failure.**

Both halves were violated, and the second violation hid the first.

Every phase gate re-runs the gate before it inside an isolated clone made with
`tempfile.mkdtemp`, and discarded with `shutil.rmtree(..., ignore_errors=True)`.
On Windows git marks every packfile read-only; `rmtree` raises `PermissionError`
on a read-only file, and `ignore_errors=True` discards that exception. The
workspace survived, still holding the pack, and the run reported nothing.
Measured at the start of Phase 13: **936 abandoned workspaces, 6,899 files,
362.35 MB — every leaked file a git packfile.**

The compounding is what made it grow that fast. A closure run does not create one
clone; each gate starts its predecessor, which starts *its* predecessor, so a
single Phase 12 closure creates a chain of them and leaks the whole chain.

## Why the earlier gates were not edited

They could have been, and it would have achieved nothing. A phase gate re-runs
its predecessor **from the predecessor's own tree**, cloned at the predecessor's
own commit — so the text that executes is the text committed then, not the text
in the working tree today. Editing the present copy of `check_phase7.py` does not
change what Phase 13's closure runs.

What can be changed is the environment those gates are executed in.
`contained_env` points a child's `TMPDIR`, `TEMP` and `TMP` at a directory the
calling gate owns, and `tempfile` consults exactly those. Every `mkdtemp` in the
nested chain therefore lands inside that directory, at any depth, in any script,
with no historical file touched — and one robust removal reclaims the chain.

So the fix is applied where the chain is *started*, not where it leaks.

## Proof

[`../phase-13/prove_workspace_cleanup.py`](../phase-13/prove_workspace_cleanup.py),
six executed proofs, raw output in
[`../phase-13/workspace_cleanup.txt`](../phase-13/workspace_cleanup.txt):

| # | Proof |
|---|---|
| `W-1` | The defect reproduced against the original code — `ignore_errors=True` leaves a read-only packfile tree and raises nothing |
| `W-2` | `remove_tree` removes that identical tree |
| `W-3` | A handle open when removal starts and released during the backoff is ridden out |
| `W-4` | A handle held throughout produces a raised error naming the path, rather than a reported success |
| `W-5` | A **grandchild** process's `mkdtemp` lands inside the contained root, and the system temporary directory gains nothing |
| `W-6` | A junction in the temporary directory pointing at the repository is refused, and the working tree is unchanged |

`W-1` is the control. A fix for a failure nobody demonstrated is a fix for a
guess, and this one had already been mis-attributed once — to open file handles,
which the evidence shows it was not.

## What the sweep will not do

`sweep` is narrow on purpose, because it deletes from a directory that belongs to
the whole machine rather than to this project:

- only **direct children** of the system temporary directory;
- only names carrying a prefix **discovered from the repository's own
  validators** by reading their `mkdtemp` calls — never a hand-written list, so a
  prefix nobody uses cannot linger and go on authorising deletions;
- only prefixes of at least four characters;
- only entries untouched for the quiet period, so a run in another process — or
  another person's run on the same machine — is not deleted mid-flight;
- never a path that resolves outside the temporary directory or onto the
  repository. A junction is refused **and reported**, not silently skipped.

`--all` waives only the quiet period, and is for a deliberate reclamation when no
gate is running.
