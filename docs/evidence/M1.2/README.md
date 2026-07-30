# Validation Evidence — M1.2

Milestone: **M1.2 — Competitive Analysis and Product Positioning**
Phase: 1 — Product Foundation

This directory holds the evidence produced by executing M1.2's validation. Nothing here is asserted; every result was produced by a command that can be re-run.

## Contents

| File | What it is |
|---|---|
| `check_m12.py` | Validation checker, exactly as executed. Re-runnable: `python docs/evidence/M1.2/check_m12.py .` from the repository root. Exits non-zero on any FAIL |
| `validation-output.txt` | Verbatim output of `check_m12.py` against the M1.2 deliverables |
| `sources.md` | Source register: every external source, its URL, its retrieval date, and what it is relied on to establish |

## What the checker verifies

| Check | Acceptance criterion |
|---|---|
| `AC-1` | Both deliverable documents and the source register exist, with sizes reported |
| `AC-2` | Every competitor in the analysed set is actually analysed |
| `AC-3` | Every verified claim cites a source identifier |
| `AC-4` | Source-register integrity: no claim cites an undefined source, and no registered source goes uncited |
| `AC-5` | No factual claim about a named competitor lacks both a marker and a source |
| `AC-6` | No fabricated figures, and no unqualified comparative claims |
| `AC-7` | No premature technology decision about this system |
| `AC-8` | No assistant-tool attribution |
| `AC-9` | No secrets |
| `AC-10` | Every relative link resolves |
| `AC-11` | No unqualified claim that a named competitor lacks a capability |
| `AC-12` | Every positioning pillar has a proof obligation, and every obligation is unmeasured |
| `AC-13` | Every competitive risk referenced by the positioning document is defined in the analysis |
| `PLACEHOLDER` | No `TODO`, `FIXME`, `TBD`, or filler text |

## The two checks that carry this milestone

The milestone's central risk is fabrication. A competitive analysis is unusually easy to write convincingly and falsely, because the subjects are absent and cannot object. Prose review does not reliably catch it. Two checks make the failure modes mechanical.

**`AC-3` and `AC-5` — no uncited claim.** Any factual statement about a competitor must carry a marker, and any verified statement must name a source that exists in the register. `AC-4` closes the loop from the other direction: a source cited but not registered fails, and a source registered but never cited fails as an orphan.

**`AC-11` — no unqualified absence claim.** This is the check that matters most, and it exists because of something that happened during the research rather than because of a theoretical concern.

LangSmith's product overview page does not mention RBAC, SSO, self-hosting, or audit controls. Its administration overview documents all four. Had the analysis drawn a conclusion from the first page, it would have asserted four false weaknesses and then built positioning on them.

Documentation silence therefore cannot establish that a product lacks a capability. `AC-11` fails any line asserting that a named competitor does not do something, unless the line is explicitly qualified as an evidence gap or as a statement about a retrieved page. The scoping decisions — why statements about what a page *mentions* are permitted, and why bare "without" is not matched — are recorded in the checker's own comments next to the pattern they govern.

## Adjudication rules

Two checks admit narrowly stated exceptions, applied mechanically rather than by hand:

- **`AC-3`** exempts only the reading-conventions legend, identified by the definitional phrasing in its own row. Nothing is exempted on the basis of punctuation — see the self-test note below.
- **`AC-6`** separates figures from comparisons. Percentages, multipliers, currency, pricing, adoption counts, and market-share figures fail unconditionally, because a document that measures nothing has no legitimate use for one. Comparative phrasings are adjudicated as legitimate only when the line negates, prohibits, or records the absence of such a claim, which is what the prohibited-claims table in `positioning.md` does throughout.

## Self-tests

A check that has never failed has not been shown to work. Six violations were planted one at a time, each targeting a different check, and each was confirmed to fail the build before being removed:

| Planted violation | Caught by |
|---|---|
| A claim that a named competitor lacks a capability | `AC-11` |
| A verified claim with no source identifier | `AC-3` |
| A percentage accuracy claim | `AC-6` |
| A citation of a source not in the register | `AC-4` |
| An unmarked, uncited competitor capability claim | `AC-5` |
| A technology decision about this system | `AC-7` |

After each restoration the checker returned to `PASS`, and both documents were confirmed byte-identical to their pre-test state.

### One self-test finding, recorded because it changed the checker

The first run of the self-test **defeated `AC-3`**. An earlier revision exempted any backtick-wrapped bare marker from the citation requirement, reasoning that such a form referred to the marker rather than making a claim. A planted claim written in exactly that form passed the check.

The exemption was removed and the single line of prose that had relied on it was reworded. No exemption now depends on how a claim is punctuated. This is recorded because the check would otherwise have shipped with a loophole that the recorded `PASS` would have concealed, and because it is the direct justification for the adjudication rule above.

## Scope of this evidence

This evidence covers M1.2 only. M1.2 produces documentation; it produces no application code. There is accordingly no type checking, linting, architecture-boundary checking, test coverage, or performance measurement here — those gates apply from the first implementation milestone onward.

**No performance, quality, accuracy, latency, cost, pricing, or adoption figure for any product appears in this milestone's deliverables, including for this product.** Nothing is built, nothing was measured, and no competitor was installed, purchased, or run. Every claim about a competitor is a claim about what that vendor documents about itself, on the date recorded in `sources.md`.
