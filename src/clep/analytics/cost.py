"""Cost attribution, reconciled — and the part of it that cannot be reconciled here.

`REQ-N-COST-1` requires cost to be attributable to tenant, project, run and
candidate, and its verification method is agreement between attributed cost and
**provider-reported** cost. Those are two different claims and this module is
careful to keep them apart, because collapsing them is how an unverified number
acquires a verified reputation.

**What is reconciled here.** Every `sample_cost` row records the token counts the
provider reported and the amount the platform attributed. `reconcile` recomputes
the amount from those recorded tokens and the declared price, and reports any row
where the two disagree. That is a real reconciliation and it catches real
defects: a price changed after the fact, an amount written by something other
than the price book, a rounding path that does not round the way the price book
does.

**What is not reconciled here, and why.** Agreement with the provider's own
billing record requires a provider that issues one. This repository executes
against deterministic local adapters, so there is no invoice to compare against
and no credential that could fetch one. Under ADR-023 rule 3 that is recorded as
a named blocker rather than approximated: an attributed-versus-invoiced figure
computed without an invoice would be attributed-versus-attributed, which agrees
with itself perfectly and means nothing.

No parallel accounting system is built. The amounts stay where Phase 5 put them,
as exact `numeric(18,9)` with their currency, and everything here reads them.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from clep.identity import ulid_to_uuid, uuid_to_ulid

#: Why `REQ-N-COST-1`'s verification cannot be completed in this phase. Named,
#: specific, and not a generality — ADR-023 rule 3 requires a blocker to say what
#: is missing rather than that something is.
PROVIDER_RECONCILIATION_BLOCKER = (
    "Agreement between attributed and provider-reported cost requires a hosted "
    "provider that issues a billing record. Every run in this repository "
    "executes against deterministic local adapters, so no provider-reported "
    "figure exists and no credential in this project could obtain one. "
    "Recomputation from recorded token counts is performed instead and reported "
    "as what it is.")


@dataclass(frozen=True)
class Discrepancy:
    sample_id: str
    attributed: Decimal
    recomputed: Decimal

    @property
    def difference(self) -> Decimal:
        return self.attributed - self.recomputed


@dataclass(frozen=True)
class Reconciliation:
    run_id: str
    rows: int
    checked: int
    unpriced_models: tuple[str, ...]
    discrepancies: tuple[Discrepancy, ...]
    attributed_total: Decimal
    recomputed_total: Decimal

    @property
    def agrees(self) -> bool:
        return not self.discrepancies

    @property
    def provider_reported_total(self):
        """Always None, and deliberately not zero.

        A zero here would enter a comparison and make attributed cost look
        wrong by exactly its own value. Absent is the truth.
        """
        return None


def reconcile(conn, organization_id: str, run_id: str, price_book) -> Reconciliation:
    """Recompute every attributed cost from its recorded usage and declared price."""
    rows = conn.execute(
        "SELECT sc.run_sample_id, sc.prompt_tokens, sc.completion_tokens, "
        "       sc.cost_amount, mc.id, m.model_identifier "
        "FROM clep.sample_cost sc "
        "JOIN clep.run_sample rs "
        "  ON rs.organization_id = sc.organization_id AND rs.id = sc.run_sample_id "
        "JOIN clep.run_candidate rc "
        "  ON rc.organization_id = rs.organization_id AND rc.id = rs.run_candidate_id "
        "JOIN clep.model_configuration mc "
        "  ON mc.organization_id = rc.organization_id "
        " AND mc.id = rc.model_configuration_id "
        "JOIN clep.model m ON m.id = mc.model_id "
        "WHERE sc.organization_id = %s AND sc.run_id = %s "
        "ORDER BY sc.run_sample_id",
        (str(organization_id), ulid_to_uuid(run_id))).fetchall()

    discrepancies, unpriced = [], set()
    attributed_total = recomputed_total = Decimal(0)
    checked = 0
    for sample_id, prompt_tokens, completion_tokens, amount, _, model in rows:
        attributed = Decimal(amount)
        attributed_total += attributed
        if not price_book.has(model):
            # Reported, not assumed to agree. A model whose price is not
            # declared cannot be recomputed, and calling that agreement would
            # make every unpriced model reconcile perfectly.
            unpriced.add(model)
            continue
        recomputed = price_book.cost_of(model, prompt_tokens,
                                       completion_tokens).amount
        recomputed_total += recomputed
        checked += 1
        if recomputed != attributed:
            discrepancies.append(Discrepancy(uuid_to_ulid(sample_id),
                                             attributed, recomputed))
    return Reconciliation(
        run_id=run_id, rows=len(rows), checked=checked,
        unpriced_models=tuple(sorted(unpriced)),
        discrepancies=tuple(discrepancies),
        attributed_total=attributed_total, recomputed_total=recomputed_total)


@dataclass(frozen=True)
class PlanEstimate:
    """`REQ-N-COST-3`: is this plan unexpectedly expensive, before it runs?"""
    model_calls: int
    unpriced_models: tuple[str, ...]
    #: None when no history exists for a model. Not zero, and not a guess: a
    #: token count cannot be known before execution without running the
    #: tokeniser the provider will use, and inventing one would put a fabricated
    #: number in front of a budget decision.
    estimated_cost: Decimal | None
    basis: str
    exceeds_budget: bool | None = None

    @property
    def estimable(self) -> bool:
        return self.estimated_cost is not None


def estimate_plan(conn, organization_id: str, *, example_count: int,
                  model_identifiers, price_book, budget_limit: Decimal | None = None
                  ) -> PlanEstimate:
    """Estimate from this tenant's own recorded history, or decline to estimate.

    The call count is exact — examples times candidates is arithmetic. The cost
    is not, because it depends on token counts that only execution produces. So
    the estimate uses the mean cost per recorded call for the same model, taken
    from `sample_cost`: the tenant's own measured history, not a figure chosen
    because it sounded reasonable.

    Where a model has no history, there is no estimate. That is the honest
    answer to "how much will this cost" for a model nobody has run yet, and it is
    still actionable — the caller learns the call count and which models are
    unaccounted for.
    """
    # `model_identifier` is the provider's own model string, which is what the
    # price book is keyed on. A deployment whose clep.model.model_identifier
    # does not equal the string the adapter reports cannot be costed at all --
    # the price lookup misses and every call is recorded as unpriced.
    idents = list(model_identifiers)
    model_calls = example_count * len(idents)
    unpriced = tuple(sorted(s for s in idents if not price_book.has(s)))

    means, unknown = {}, []
    for ident in set(idents):
        row = conn.execute(
            "SELECT avg(sc.cost_amount), count(*) FROM clep.sample_cost sc "
            "JOIN clep.run_sample rs "
            "  ON rs.organization_id = sc.organization_id AND rs.id = sc.run_sample_id "
            "JOIN clep.run_candidate rc "
            "  ON rc.organization_id = rs.organization_id AND rc.id = rs.run_candidate_id "
            "JOIN clep.model_configuration mc "
            "  ON mc.organization_id = rc.organization_id "
            " AND mc.id = rc.model_configuration_id "
            "JOIN clep.model m ON m.id = mc.model_id "
            "WHERE sc.organization_id = %s AND m.model_identifier = %s",
            (str(organization_id), ident)).fetchone()
        if row and row[1]:
            means[ident] = Decimal(row[0])
        else:
            unknown.append(ident)

    if unknown:
        return PlanEstimate(
            model_calls=model_calls, unpriced_models=unpriced,
            estimated_cost=None,
            basis=(f"no recorded history for {sorted(unknown)}; a token count "
                   f"cannot be known before execution, and this refuses to "
                   f"invent one"))

    estimated = sum((means[s] * Decimal(example_count) for s in idents),
                    Decimal(0))
    return PlanEstimate(
        model_calls=model_calls, unpriced_models=unpriced,
        estimated_cost=estimated,
        basis=(f"mean recorded cost per call for {len(set(idents))} model(s) "
               f"over this tenant's own history, times {example_count} "
               f"example(s)"),
        exceeds_budget=(None if budget_limit is None
                        else estimated > Decimal(budget_limit)))
