# Validation Evidence — Phase 2

Phase: **Phase 2 — Architecture**
Milestones: M2.1 through M2.6

## Contents

| File | What it is |
|---|---|
| `check_phase2.py` | Phase validator, exactly as executed. Re-runnable: `python docs/evidence/phase-2/check_phase2.py .` Exits non-zero on any FAIL |
| `validation-output.txt` | Verbatim output of `check_phase2.py` |
| `spikes/spike_regression_statistics.py` | The ADR-007 spike, exactly as executed. Standard library only, deterministic under seed `20260730` |
| `spikes/spike-regression-statistics-output.txt` | Verbatim spike output. `Q-7` asserts this reproduces byte-identically from a re-run |

## What the validator checks

| Check | What it establishes |
|---|---|
| `Q-1` | Every Phase 1 milestone validator still passes against the current tree |
| `Q-2` | The Phase 1 phase-gate still passes **against the Phase 1 tree** |
| `Q-3` | All eleven canonical §19 ADR topics exist |
| `Q-4` | Every ADR status is from the permitted vocabulary |
| `Q-5` | Each undecided ADR fully specifies its spike |
| `Q-6` | No undecided ADR smuggles a decision |
| `Q-7` | The recorded spike output reproduces byte-identically |
| `Q-8` | Every requirement referenced by a Phase 2 artifact is defined |
| `Q-9` | Every canonical §21 failure mode appears in the failure model |
| `Q-10` | Every product sensitivity class is addressed by the threat model |
| `Q-11` | No invented figure in any Phase 2 artifact |
| `Q-12` | Diagram blocks are structurally sound |
| `Q-13` | Every relative link resolves |
| `Q-14` | No placeholder text |
| `Q-15` | No secret in the working tree or any git blob |
| `Q-16` | No attribution in the governed scope |
| `Q-17` | Sole-contributor identity across all history |
| `Q-18` | Canonical document local, unchanged, ignored, untracked, absent from published history |
| `Q-19` | Repository hygiene |
| `Q-20` | Phase 2 did not implement Phase 3+ scope |

## The three checks specific to this phase

**`Q-6` — an undecided ADR must not smuggle a decision.** ADR-001 and ADR-003 declare themselves gated on spikes. An ADR that says it is undecided and then names a chosen technology is worse than a wrong decision, because the decision becomes real without ever being reviewed. The check fails any line in a gated ADR that asserts adoption of a named technology.

**`Q-7` — spike reproducibility.** ADR-007 is decided on measured evidence, so that evidence must be reproducible or it is not evidence. The validator re-executes the spike and compares the output byte-for-byte against the recorded file. The spike is seeded and uses only the standard library precisely so this check is possible.

**`Q-2` — the Phase 1 gate at its own tree.** `check_phase1.py` asserts Phase 1 contained no decision records and no application source. Phase 2 legitimately adds decision records, so run against the Phase 2 tree that assertion now fails — correctly, since it is a statement about the Phase 1 *boundary*. It is therefore evaluated in a throwaway worktree checked out at the Phase 1 commit.

That worktree needs one accommodation, recorded because it looks like a loophole and is not: the canonical specification is deliberately never committed, so a fresh checkout contains no copy and the Phase 1 gate's canonical-document check would fail for a reason unrelated to Phase 1. The local document is copied in to reproduce the real Phase 1 condition — present on this machine, outside version control. It is copied, never moved, and `Q-18` independently asserts the original is unchanged.

## The spike, and the defect it found in itself

The ADR-007 spike compared three regression-comparison method classes against simulated data with known true effects.

**Before its results were usable it had to be corrected.** The first score model drew baselines near the upper bound of the metric range. Candidate scores are baseline plus effect plus noise, clipped to the valid range, so positive noise was clipped at the ceiling while negative noise was not. That induced a systematically negative mean paired difference even when the true effect was zero — the null hypothesis was not null, and every method's regression rate was inflated.

The model was re-centred and a null-calibration check added, which now runs and reports before any verdict rate is printed. Measured null mean paired difference is `-0.00029` with a clipping rate of 18 in 2000.

**The corrected result contradicted the intuition the spike started from.** The expectation was that the naive threshold method would be discredited by its false-positive rate. It was not: at the largest sample size it produced the *fewest* false positives of the three methods. What actually disqualifies it is that it cannot express uncertainty and cannot abstain — and that its behaviour is governed by an arbitrary constant, so at small samples it fires on pure noise in roughly one run in six while remaining structurally blind to any real regression smaller than its threshold.

Had the conclusion been written before the data was read, it would have been wrong in a way no reviewer could have caught from the ADR alone.

## Manual inspection performed in addition to these checks

- **Requirement coverage was counted, not assumed.** 108 of 150 requirements are referenced by Phase 2 artifacts. The 42 that are not were inspected individually: they are analytics presentation behaviour, local development and operability, maintainability gates, and documentation obligations — none requiring an architectural decision beyond a container that already exists. Five requirements initially found to be covered in substance but not cited by identifier had citations added, which is what moved the count from 103.
- **The deterministic-versus-reasoning boundary was cross-checked** between the system architecture and ADR-004 for consistency. Both state that the gate verdict is never produced by reasoning.
- **Every `TARGET NOT YET SET` in the observability strategy** was checked to name what will be measured, so the gap is a pending measurement rather than an open question.
- **The capability count was re-checked** against the PRD after the Phase 1 lesson that produced a heading referencing capabilities that do not exist.

## Scope

Phase 2 produces architecture documentation, decision records, and one executed spike. It produces no application code, so there is no type checking, linting, boundary enforcement, or coverage measurement here.

**No performance, cost, or capacity target for this system is set anywhere in Phase 2.** The only measured figures in the phase are the spike's own verdict rates, which describe the behaviour of statistical methods on simulated data, not the behaviour of this system.
