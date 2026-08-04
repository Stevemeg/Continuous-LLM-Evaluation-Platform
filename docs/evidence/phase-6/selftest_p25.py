"""Prove P-25 still fails when it should, now that its allowlist is empty.

Deleting the disclosed branch could have turned a check that was passing for a
real reason into a check that passes because nothing is left to look at. This
runs the SHIPPED P-25 source — sliced out of check_phase6.py by its own section
markers, never copied — against three purpose-built repositories.
"""
from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKER = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    "docs/evidence/phase-6/check_phase6.py")
SRC = CHECKER.read_text(encoding="utf-8")
BLOCK = SRC.split("P-25 canonical document")[1].split("P-26 hygiene")[0]
BLOCK = BLOCK.split("\n", 1)[1].rsplit("# ", 1)[0]


def sh(*args, cwd):
    return subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)


def run_p25(root: Path):
    captured = {}

    def add(cid, status, detail, defects=None):
        captured.update(id=cid, status=status, detail=detail,
                        defects=list(defects or []))

    def git(*args, cwd=None):
        return subprocess.run(["git", *args], cwd=str(cwd or root),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace").stdout

    ns = {"ROOT": root, "git": git, "add": add, "re": re, "Path": Path,
          "subprocess": subprocess}
    exec(compile(BLOCK, str(CHECKER), "exec"), ns)
    return captured


def scratch(with_docx_branch: bool, published: bool = False) -> Path:
    root = Path(tempfile.mkdtemp(prefix="p25-"))
    sh("git", "init", "--quiet", "-b", "main", cwd=root)
    sh("git", "config", "user.email", "t@example.invalid", cwd=root)
    sh("git", "config", "user.name", "T", cwd=root)
    (root / ".gitignore").write_text("*.docx\n", encoding="utf-8")
    (root / "README.md").write_text("scratch\n", encoding="utf-8")
    sh("git", "add", ".gitignore", "README.md", cwd=root)
    sh("git", "commit", "--quiet", "-m", "base", cwd=root)
    # The local canonical document: present, ignored, never tracked.
    (root / "canonical.docx").write_bytes(b"PK\x03\x04 not really a document")
    if with_docx_branch:
        sh("git", "checkout", "--quiet", "-b", "wip", cwd=root)
        sh("git", "add", "-f", "canonical.docx", cwd=root)
        sh("git", "commit", "--quiet", "-m", "wip: carries the document", cwd=root)
        sha = sh("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
        sh("git", "checkout", "--quiet", "main", cwd=root)
        # Checking out `main` deletes a file tracked only on `wip`. Restore it so
        # the ONLY difference from the clean case is what a ref can reach.
        (root / "canonical.docx").write_bytes(b"PK\x03\x04 not really a document")
        if published:
            sh("git", "branch", "-D", "wip", cwd=root)
            sh("git", "update-ref", "refs/remotes/origin/main", sha, cwd=root)
    return root


CASES = [
    ("clean: document local, ignored, on no ref", scratch(False), "PASS"),
    ("planted: document reachable from a local branch", scratch(True), "FAIL"),
    ("planted: document reachable from a published ref",
     scratch(True, published=True), "FAIL"),
]

caught = 0
for label, root, expected in CASES:
    got = run_p25(root)
    ok = got["status"] == expected
    caught += ok
    print(f"[{'CAUGHT' if ok else 'MISSED':<7}] {label}")
    print(f"           expected {expected}, got {got['status']}: {got['detail']}")
    for d in got["defects"]:
        print(f"           - {d}")

print(f"\nP-25 self-test: {caught}/{len(CASES)} cases behaved as required")
sys.exit(0 if caught == len(CASES) else 1)
