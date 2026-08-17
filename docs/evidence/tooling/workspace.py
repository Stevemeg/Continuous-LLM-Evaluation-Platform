"""Temporary-workspace containment and reclamation for the evidence validators.

Why this exists, precisely. Every phase gate re-runs the gate before it inside an
isolated clone, and each clone is a `tempfile.mkdtemp` discarded afterwards with
`shutil.rmtree(..., ignore_errors=True)`. On Windows git marks every packfile
read-only; `rmtree` raises `PermissionError` on a read-only file, and
`ignore_errors=True` throws that exception away without reporting it. The
directory survives, still holding the pack. Measured before this module existed:
936 abandoned workspaces, 6,899 files, 362.35 MB, every leaked file a packfile.

That is two failures, not one, and they need different fixes.

*Removal* is wrong because it suppresses the error rather than clearing the
attribute that caused it. `remove_tree` clears the read-only bit and retries, and
raises when a path genuinely cannot be removed instead of reporting a success it
did not achieve. Silence was the whole defect; a cleanup that cannot say it
failed will fail for 936 runs and nobody will hear about it.

*Containment* is the harder half. The gates that leak are published evidence from
earlier phases, and editing the present copy of one would change nothing about
what executes: a phase gate re-runs its predecessor from the predecessor's own
tree, so the text that runs is the text that was committed then. What can be
changed is the environment they are executed in. `contained_env` points a child's
TMPDIR, TEMP and TMP at a directory this process owns, so every `mkdtemp`
anywhere in the nested chain lands inside it -- at any depth, in any script, with
no historical file touched. The leak still happens. It now happens somewhere that
gets deleted.

`sweep` reclaims what earlier runs already abandoned. It is deliberately narrow:
only direct children of the system temporary directory, only names carrying a
prefix discovered from the repository's own validators rather than a hand-written
list, only entries left untouched long enough that no live run could own them,
and never a path that resolves outside the temporary directory or inside the
repository.

Usage: python docs/evidence/tooling/workspace.py <repo_root> [--all] [--dry-run]
Exits non-zero if any workspace resisted removal.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

# How long a workspace must have lain untouched before a sweep will treat it as
# abandoned. A gate running in another process writes into its workspace
# continuously, so this is what stops a sweep deleting a live run's clone --
# including a run started by somebody else on the same machine.
QUIET_SECONDS = 30 * 60

# A prefix shorter than this is too broad to authorise deleting anything: the
# system temporary directory belongs to the whole machine, not to this project.
MIN_PREFIX = 4

_PREFIX_RX = re.compile(r"""mkdtemp\(\s*prefix\s*=\s*["']([A-Za-z0-9_.\-]+)["']""")

# `onerror` is deprecated from 3.12 and `onexc` does not exist before it. Both
# hand the callback (function, path, exception-ish), so one handler serves both.
_RMTREE_HANDLER = "onexc" if sys.version_info >= (3, 12) else "onerror"


def discover_prefixes(root: Path) -> set[str]:
    """The prefixes the repository's own validators pass to `mkdtemp`.

    Derived, never listed. A validator added later is covered without anybody
    remembering to extend a constant here, and -- the direction that actually
    matters -- a prefix no validator uses cannot linger in a list and go on
    authorising the deletion of directories that were never ours.
    """
    found: set[str] = set()
    for path in sorted(Path(root).glob("docs/evidence/**/*.py")):
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found.update(_PREFIX_RX.findall(body))
    return {p for p in found if len(p) >= MIN_PREFIX}


def _make_writable(path) -> None:
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass


def _retry_writable(func, path, _exc) -> None:
    """rmtree error handler: clear the attribute that blocked it, then retry once.

    If the retry raises, the exception leaves `rmtree` and reaches the backoff in
    `remove_tree`. That is the intended path for an open handle, which no
    attribute change will fix and which usually clears on its own.
    """
    _make_writable(path)
    func(path)


def remove_tree(path: Path, attempts: int = 5) -> None:
    """Remove a directory tree, or raise saying why it could not be removed.

    Two obstacles, distinguished because they need opposite treatment. A
    read-only file -- git marks packfiles 444 -- fails `unlink` on Windows no
    matter how many times it is retried; the attribute has to be cleared, which
    is the handler's job. An open handle -- an antivirus scanner mid-scan, a git
    process that has not quite exited -- fails identically but clears on its own,
    which is the backoff's job. Neither is silenced.
    """
    path = Path(path)
    if not path.exists():
        return
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path, **{_RMTREE_HANDLER: _retry_writable})
            return
        except OSError as exc:
            last = exc
            time.sleep(0.1 * (2 ** attempt))
    raise OSError(f"{path} could not be removed after {attempts} attempts: {last}")


@contextmanager
def workspace(prefix: str = "clep-work-"):
    """A temporary directory that is genuinely removed on the way out."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    try:
        yield path
    finally:
        remove_tree(path)


def contained_env(base: Path, env: dict | None = None) -> dict:
    """A child environment whose temporary directory is `base`.

    `tempfile.gettempdir` consults TMPDIR, then TEMP, then TMP. Setting all three
    means a child process -- and every process it starts, to any depth -- creates
    its temporary workspaces inside `base` whatever its own code says. That is
    what makes an earlier phase's leaking gate containable without editing it.
    """
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    out = dict(os.environ if env is None else env)
    out.update(TMPDIR=str(base), TEMP=str(base), TMP=str(base))
    return out


def tree_bytes(path: Path) -> int:
    total = 0
    for p in Path(path).rglob("*"):
        try:
            if p.is_file() and not p.is_symlink():
                total += p.stat().st_size
        except OSError:
            continue
    return total


def sweep(root: Path, quiet_seconds: int = QUIET_SECONDS, dry_run: bool = False):
    """Reclaim abandoned validator workspaces from the system temporary directory.

    Returns (reclaimed_names, bytes_freed, residue). `residue` is the honest half:
    a workspace that resisted removal, or one refused because it did not resolve
    where it claimed to. An empty residue is the only clean result.
    """
    tmp = Path(tempfile.gettempdir()).resolve()
    repo = Path(root).resolve()
    prefixes = tuple(sorted(discover_prefixes(repo)))
    reclaimed: list[str] = []
    residue: list[str] = []
    freed = 0
    if not prefixes:
        return reclaimed, freed, ["no mkdtemp prefix found in docs/evidence; refusing "
                                  "to sweep on an empty pattern set"]
    now = time.time()
    for entry in sorted(tmp.iterdir()):
        if entry.is_symlink() or not entry.is_dir() or not entry.name.startswith(prefixes):
            continue
        try:
            resolved = entry.resolve(strict=True)
        except OSError:
            continue
        # A junction pointing at the working tree is how a cleanup becomes a
        # data-loss incident. Anything that does not resolve to a direct child of
        # the temporary directory, or that lands on or inside the repository, is
        # refused and reported rather than removed.
        if (resolved.parent != tmp or resolved == repo
                or repo in resolved.parents or resolved in repo.parents):
            residue.append(f"{entry.name}: refused, resolves to {resolved}")
            continue
        try:
            age = now - entry.stat().st_mtime
        except OSError:
            continue
        if age < quiet_seconds:
            continue  # young enough that a run in another process may own it
        size = tree_bytes(entry)
        if not dry_run:
            try:
                remove_tree(entry)
            except OSError as exc:
                residue.append(f"{entry.name}: {exc}")
                continue
        reclaimed.append(entry.name)
        freed += size
    return reclaimed, freed, residue


def _main(argv: list[str]) -> int:
    flags = {a for a in argv[1:] if a.startswith("-")}
    positional = [a for a in argv[1:] if not a.startswith("-")]
    root = Path(positional[0] if positional else ".").resolve()
    quiet = 0 if "--all" in flags else QUIET_SECONDS
    reclaimed, freed, residue = sweep(root, quiet_seconds=quiet,
                                      dry_run="--dry-run" in flags)
    verb = "would reclaim" if "--dry-run" in flags else "reclaimed"
    print(f"prefixes derived from the repository: "
          f"{', '.join(sorted(discover_prefixes(root))) or 'none'}")
    print(f"temporary directory: {Path(tempfile.gettempdir()).resolve()}")
    print(f"{verb}: {len(reclaimed)} workspace(s), {freed} byte(s) "
          f"({freed / 1048576:.2f} MB)")
    for r in residue:
        print(f"  RESIDUE {r}")
    print(f"residue: {len(residue)}")
    return 1 if residue else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(_main(sys.argv))
