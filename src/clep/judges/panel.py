"""The ensemble, driven by the run loop and written to the store.

Phase 8 built the judge SDK and the consensus rule; Phase 9 proved they work
against real models but wrote the results to files. This is the production
path: the harness calls `judge`, every judgement lands in `judge_run`, every
score in `judge_vote`, every verdict in `consensus_result`, and every
escalation in `escalation` — through `JudgeRepository`, which is the only thing
that writes them.

Three properties.

**The panel cannot invent a judge.** It is constructed with an `Ensemble` whose
composition the repository already validated against ADR-017 at creation, and
with a gateway. It has no registry to look one up in.

**A judgement is recorded whatever it resolved to.** A failed or abstaining
judge produces a `judge_run` row with no `judge_vote`, which is REQ-X-8 enforced
by absence. Recording only the successes would make an ensemble look unanimous
because the dissenters crashed.

**Regeneration is bounded and optional.** When bounds are supplied an unreadable
reply is re-asked under `regenerate_unreadable`, which retries that one
condition and no other. The vote finally recorded is the last one obtained; when
the loop exhausted its bounds without a readable reply, that is the unreadable
vote, recorded as unreadable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from clep.judges.consensus import reach_consensus
from clep.judges.reflection import is_unreadable, regenerate_unreadable
from clep.judges.sdk import render_prompt, run_judge
from clep.rag.repository import digest_of


@dataclass
class PanelOutcome:
    judgements: int = 0
    scored: int = 0
    unreadable: int = 0
    regenerations: int = 0
    escalations: int = 0
    agreements: int = 0
    cost: Decimal = Decimal(0)
    notes: list = field(default_factory=list)


class JudgePanel:
    """Runs one configured ensemble over samples and persists what it finds."""

    def __init__(self, *, ensemble, ensemble_id: str, judge_version_ids: dict,
                 gateway, repository, project_id: str, bounds=None,
                 timeout_ms: int | None = None):
        #: `judge_version_ids` maps a judge's `version_key` to the stored
        #: judge_version id. Supplied by the caller because judge identity is
        #: registry data and this loop may not invent it — the same rule the
        #: evaluator ids follow.
        missing = [j.version_key for j in ensemble.judges
                   if j.version_key not in judge_version_ids]
        if missing:
            raise ValueError(
                f"no stored judge version for {missing}; a judgement that "
                f"cannot name its judge is not evidence")
        self._ensemble = ensemble
        self._ensemble_id = ensemble_id
        self._ids = dict(judge_version_ids)
        self._gateway = gateway
        self._repo = repository
        self._project_id = project_id
        self._bounds = bounds
        self._timeout_ms = timeout_ms

    def judge(self, *, run_id: str, run_sample_id: str, sample,
              outcome: PanelOutcome) -> object:
        """One sample, every judge, one consensus. Everything written."""
        votes = []
        for judge in self._ensemble.judges:
            vote = run_judge(judge, sample, self._gateway,
                             timeout_ms=self._timeout_ms)
            if is_unreadable(vote) and self._bounds is not None:
                reasoning = regenerate_unreadable(
                    judge, sample, self._gateway, self._bounds,
                    timeout_ms=self._timeout_ms)
                outcome.regenerations += reasoning.iterations
                outcome.cost += reasoning.cost
                if reasoning.value is not None:
                    vote = reasoning.value
            votes.append(vote)

            prompt, _ = render_prompt(judge, sample)
            self._repo.record_judgement(
                run_id=run_id, run_sample_id=run_sample_id,
                judge_version_id=self._ids[judge.version_key], vote=vote,
                prompt_digest=digest_of(prompt),
                idempotency_key=f"{run_sample_id}:{judge.version_key}")
            outcome.judgements += 1
            if vote.is_scoring:
                outcome.scored += 1
            elif is_unreadable(vote):
                outcome.unreadable += 1
            if vote.cost is not None:
                outcome.cost += vote.cost

        consensus = reach_consensus(self._ensemble, votes)
        self._repo.record_consensus(
            run_id=run_id, run_sample_id=run_sample_id,
            ensemble_id=self._ensemble_id, consensus=consensus,
            project_id=self._project_id)
        if consensus.state == "escalated":
            outcome.escalations += 1
        else:
            outcome.agreements += 1
        return consensus
