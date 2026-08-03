# ADR-014 — What a run identity captures, and what enters its digest

| Field | Value |
|---|---|
| Status | **Accepted** |
| Milestone | M6.3 |
| Canonical basis | §9 (immutable run identity/configuration snapshot), §6, §23 Phase 6 |
| Constrained by | [ADR-005](ADR-005-dataset-immutability.md), [ADR-011](ADR-011-artifact-retention.md), [ADR-012](ADR-012-primary-datastore.md) |
| Requirements | `REQ-F-07-1`, `REQ-F-07-3`, `REQ-F-01-3`, `REQ-F-01-4`, `REQ-F-02-2` |

## Context

`REQ-F-07-1` lists what a run identity must capture: dataset version, prompt or system version, model and provider configuration, evaluator and judge versions, seeds where relevant, environment metadata, and timestamps.

It does not say what the identity *digest* is computed over, and the two are not the same question. The digest is what every comparability claim rests on — `REQ-F-01-3` compares a candidate against a baseline "using the same dataset version and the same evaluator/judge versions", and `REQ-F-01-4` requires the platform to **refuse** a comparison when they differ. Whatever enters the digest defines what "the same" means.

Phase 5 shipped a placeholder: a digest over a few identifiers, with none of the components stored. That is enough to compare two digests and nothing else.

## Decision

**Capture every component listed by `REQ-F-07-1` as an individual row. Derive the digest from the subset that determines what was measured. Capture environment metadata and exclude it from the digest.**

| # | Rule | Basis |
|---|---|---|
| I-1 | Components are stored individually in `run_identity_component`, keyed by (kind, ref), with a content digest each. | `REQ-F-07-3` requires naming the elements that could not be reconstructed; a hash cannot be decomposed |
| I-2 | The identity digest is derived from the components, never stored independently of them. | A digest that can disagree with the components it claims to summarise is worse than no digest |
| I-3 | Identity-bearing kinds: dataset version, prompt version, model configuration, system version, evaluator version, suite version, integration tier, seed. | These are what `REQ-F-01-4` must be able to detect a difference in |
| I-4 | **Environment metadata is captured and excluded from the digest.** | See below |
| I-5 | Components carry the referenced object's *content* digest, not only its identifier. | Two runs naming the same version measured the same thing only if that version's content is the same |
| I-6 | The identity is frozen before the run row exists. A run naming a component that is absent is refused, not recorded. | A run whose identity is partly unknown is not reproducible, and recording it as though it were is the failure `REQ-F-07-1` exists to prevent |
| I-7 | The canonical form is sorted, with explicit separators and ASCII escaping. | The digest must be reproducible in another process, on another machine, in another release |

## Why the environment is captured but excluded (I-4)

This is the decision the requirement leaves open, and both readings are defensible until you follow them through.

**If environment metadata entered the digest**, two runs of the same prompt, on the same dataset, with the same evaluators, executed on two machines with different interpreter patch versions would have different identities. `REQ-F-01-3` — evaluate a candidate against a baseline using the same dataset and evaluator versions — could then never be satisfied by any pair of runs not executed on the same host. Baselines would expire on upgrade. In a platform whose entire purpose is comparing a change against a baseline recorded weeks earlier, that is not a conservative choice; it is a broken one.

**If environment metadata were not captured at all**, `REQ-F-07-1` would be unmet, and a reproduction could not report environment drift — which `REQ-F-07-3` asks for and the schema's `environment_changed` gap reason exists to hold.

So it is captured, reported on every reproduction attempt as a gap a reviewer can weigh, and kept out of the digest. The captured description is deliberately coarse — interpreter version, implementation, operating system — because the full installed package set would change on every unrelated upgrade and turn the signal into noise.

## Consequences

- Reproduction can name exactly which element moved, rather than reporting that a digest differs. `REQ-F-07-3` becomes answerable.
- A change to a version's *content* under an unchanged identifier is detectable (I-5). Phase 6's immutability triggers make this rare; a restored backup could still produce it.
- More storage per run: one row per component instead of one column. This is small against `run_sample`, and it is the difference between an identity that can be inspected and one that can only be compared.
- An environment difference never invalidates a comparison. If a future phase finds that a specific environment element does change results, it must be promoted to an identity-bearing component — which is a change to this ADR, not an implementation detail.

## What would falsify this

Evidence that a captured-but-excluded environment element materially changes evaluation outcomes on the same inputs. That would make I-4 wrong for that element, and it would have to move into `IDENTITY_KINDS` with the baseline-expiry cost accepted deliberately rather than discovered.
