# Validation Evidence — M1.1

Milestone: **M1.1 — Product Definition, Personas, and Use Cases**
Phase: 1 — Product Foundation

This directory holds the evidence produced by executing M1.1's validation. Nothing here is asserted; every result was produced by a command that can be re-run.

## Contents

| File | What it is |
|---|---|
| `check_m11.py` | Document validation checker, exactly as executed. Re-runnable: `python docs/evidence/M1.1/check_m11.py .` from the repository root |
| `validation-output.txt` | Verbatim output of `check_m11.py` against the M1.1 deliverables |
| `check_crossrefs.py` | Cross-document consistency checker. Re-runnable: `python docs/evidence/M1.1/check_crossrefs.py .` Exits non-zero on any inconsistency |
| `crossref-output.txt` | Verbatim output of `check_crossrefs.py` |
| `security-and-integrity.txt` | Verbatim output of the canonical-specification integrity check, the secret scans over the working tree and every blob in the committed tree, and the commit attribution audit |

## The canonical specification is not in this repository

The canonical master specification governs the project, but it is authoritative *input* rather than a distributable project artifact, so it is held locally and excluded from version control by an ignore rule. Every document in `docs/` cites it by section number; none embeds it. `security-and-integrity.txt` records its digest, confirms the local file was never modified, and confirms no `.docx` blob exists in the committed tree.

## What the checker verifies

| Check | Acceptance criterion |
|---|---|
| `AC-1` | All five deliverable documents exist, with sizes reported |
| `AC-2` | Every canonical capability `CAP-01`…`CAP-12` appears in the use cases |
| `AC-3` | Every canonical user group `U-1`…`U-6` has a persona |
| `AC-4` | The PRD cites canonical sections 2, 3, and 4 |
| `AC-6` | No unsupported claims or invented metrics |
| `AC-7` | No premature technology or architecture decisions |
| `AC-8` | No assistant-tool attribution anywhere |
| `AC-9` | No secrets |
| `PLACEHOLDER` | No `TODO`, `FIXME`, `TBD`, or filler text |
| `AC-10` | Every relative link between documents resolves |
| `AC-11` | Canonical section 25 anti-patterns are reflected in the non-goals |

## Adjudication rules

`AC-6` and `AC-7` use pattern matching, which produces matches that are legitimate on inspection. Rather than adjudicating those by hand and asking a reviewer to trust the result, the two permitted exceptions are encoded in the checker and applied mechanically:

- **R1 — canonical citation.** The matching line contains a `[CANON §` citation marker. Quoting a forbidden practice in order to forbid it is not committing it. This covers, for example, the non-goals document quoting canonical section 25's own examples of prohibited claims and prohibited infrastructure.
- **R2 — declared-unvalidated target.** The matching line is a success-criteria table row explicitly marked `NOT YET MEASURED`. Such a row states a target, and that document's banner declares that no figure in it is a measured result.

Any match not covered by R1 or R2 is reported as **outstanding** and fails the check. At the recorded run, outstanding matches were **zero** in both categories.

## A note on the checker's own content

`check_m11.py` assembles its assistant-attribution search terms from string fragments rather than writing them as literals. Written as literals, the checker would introduce into this repository the exact strings it exists to forbid, and a scan would report a match against the checker's own source. Assembling them keeps the repository literally clean while the check remains fully effective — as demonstrated by the self-test below.

## Cross-document consistency

Each use case declares its own personas and capabilities in its section header. Five summary tables restate that information: the use-case index, the capability coverage table, the persona coverage table, the coverage confirmation table in `personas.md`, and each persona's own use-case list.

Summary tables that restate information held elsewhere drift silently, and this set did. Inspection during milestone validation found **sixteen mismatches**. The section headers are now the single source of truth, every table has been reconciled against them, and `check_crossrefs.py` makes future drift a failure rather than a discovery.

## Self-tests

A check that has never failed has not been shown to work. Both checkers were deliberately made to fail and then restored:

1. A violation planted in `docs/product/non-goals.md`:
   ```
   [FAIL   ] AC-8         1 AI-attribution match(es) — must be zero
   ```
2. A use-case identifier removed from a list in `docs/product/personas.md`:
   ```
   FAIL — 1 inconsistency(ies):
     U-6: missing=['UC-07'] extra=[]
   ```

Both files were restored and both checks returned to `PASS`. The results recorded in `validation-output.txt` and `crossref-output.txt` are therefore real passes, not checks that cannot fail.

## Scope of this evidence

This evidence covers M1.1 only. M1.1 produces documentation; it produces no application code. Accordingly there is no type checking, linting, architecture-boundary checking, test coverage, or performance measurement in this directory — those gates apply from the first implementation milestone onward and would be meaningless here.

**No performance, quality, accuracy, latency, or cost figure for the system appears in this directory, because no part of the system has been built.**
