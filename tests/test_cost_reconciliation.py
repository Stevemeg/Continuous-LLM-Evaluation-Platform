"""REQ-N-COST-1 and REQ-N-COST-3, against a real run and a real store.

The reconciliation here recomputes every attributed amount from the token counts
the provider reported and the declared price, and requires them to agree. It
does **not** compare against a provider's billing record, because none exists in
this repository — see `PROVIDER_RECONCILIATION_BLOCKER`, which this file asserts
is still the honest description of what is missing.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.analytics.cost import (PROVIDER_RECONCILIATION_BLOCKER, estimate_plan,
                                 reconcile)
from clep.db.session import tenant_session
from clep.identity import ulid_to_uuid
from clep.providers.gateway import Price, PriceBook
from tests.conftest import requires_postgres
from tests.test_end_to_end import (  # noqa: F401 - fixtures used by name
    build_examples, examples_with_evidence, execute_run, second_configuration)

pytestmark = [pytest.mark.integration, requires_postgres]

#: The price `execute_run` declares for the candidate model. Restated here so a
#: mismatch between the run's prices and the reconciliation's is a test failure
#: rather than a silent agreement between two copies of the same mistake.
CANDIDATE_PRICE = Price(Decimal("0.001"), Decimal("0.002"))


def _price_book(identifier):
    return PriceBook({identifier: CANDIDATE_PRICE})


def _model_identifier(dsn, seeded):
    """The provider's model string as the store holds it.

    Worth a note: the seeded fixture records `m` here while the run prices
    `candidate-model`, because the fixture never had to make them agree. In a
    real deployment they must -- the price lookup is keyed on this column, and a
    mismatch records every call as unpriced rather than failing loudly.
    """
    with tenant_session(dsn, seeded["organization"]) as conn:
        return conn.execute(
            "SELECT model_identifier FROM clep.model WHERE id = %s",
            (ulid_to_uuid(seeded["model"]),)).fetchone()[0]


def test_every_attributed_cost_recomputes_from_its_recorded_usage(
        migrated_database, seeded, examples_with_evidence):
    examples = build_examples(examples_with_evidence)
    run_id, outcome = execute_run(migrated_database, seeded, examples,
                                  key="cost-reconcile")
    assert outcome.samples_recorded == 3

    identifier = _model_identifier(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reconcile(conn, seeded["organization"], run_id,
                           _price_book(identifier))

    assert result.rows == 3, "no cost rows to reconcile; the test proves nothing"
    assert result.checked == 3
    assert result.unpriced_models == ()
    assert result.agrees, [
        (d.sample_id, str(d.attributed), str(d.recomputed))
        for d in result.discrepancies]
    assert result.attributed_total == result.recomputed_total
    assert result.attributed_total > 0


def test_a_price_that_changed_after_the_fact_is_caught(
        migrated_database, seeded, examples_with_evidence):
    """The defect reconciliation exists to find. Recomputing with a different
    price must disagree — otherwise the reconciliation is comparing a number
    with itself and would agree no matter what was stored."""
    examples = build_examples(examples_with_evidence)
    run_id, _ = execute_run(migrated_database, seeded, examples,
                            key="cost-reprice")
    identifier = _model_identifier(migrated_database, seeded)
    wrong = PriceBook({identifier: Price(Decimal("0.009"), Decimal("0.009"))})

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reconcile(conn, seeded["organization"], run_id, wrong)

    assert not result.agrees
    assert len(result.discrepancies) == 3
    assert all(d.difference != 0 for d in result.discrepancies)


def test_an_unpriced_model_is_reported_rather_than_counted_as_agreeing(
        migrated_database, seeded, examples_with_evidence):
    examples = build_examples(examples_with_evidence)
    run_id, _ = execute_run(migrated_database, seeded, examples,
                            key="cost-unpriced")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reconcile(conn, seeded["organization"], run_id, PriceBook())

    assert result.rows == 3
    assert result.checked == 0
    assert result.unpriced_models != ()
    # It "agrees" only because nothing was checkable, and the counts say so.
    assert result.agrees and result.checked == 0


def test_the_provider_reported_total_is_absent_rather_than_zero(
        migrated_database, seeded, examples_with_evidence):
    """A zero would enter the comparison and make attributed cost look wrong by
    exactly its own value. ADR-023 rule 3: absent, with a named blocker."""
    examples = build_examples(examples_with_evidence)
    run_id, _ = execute_run(migrated_database, seeded, examples, key="cost-absent")
    identifier = _model_identifier(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        result = reconcile(conn, seeded["organization"], run_id, _price_book(identifier))
    assert result.provider_reported_total is None
    assert "hosted provider" in PROVIDER_RECONCILIATION_BLOCKER
    assert "no credential" in PROVIDER_RECONCILIATION_BLOCKER


# ----------------------------------------------------- REQ-N-COST-3
def test_a_plan_for_a_model_with_no_history_is_not_estimated(
        migrated_database, seeded):
    """The honest answer to "how much will this cost" for a model nobody has
    run. Declining is still actionable: the caller learns the call count."""
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        estimate = estimate_plan(conn, seeded["organization"], example_count=100,
                                 model_identifiers=["never-run-before"],
                                 price_book=PriceBook())
    assert estimate.model_calls == 100
    assert not estimate.estimable
    assert estimate.estimated_cost is None
    assert "no recorded history" in estimate.basis
    assert "invent" in estimate.basis


def test_a_plan_is_estimated_from_this_tenants_own_recorded_history(
        migrated_database, seeded, examples_with_evidence):
    """Measured, not chosen. The mean comes from sample_cost rows this test
    caused to exist, so the estimate is derived from data rather than a
    plausible-sounding constant."""
    examples = build_examples(examples_with_evidence)
    execute_run(migrated_database, seeded, examples, key="cost-history")
    identifier = _model_identifier(migrated_database, seeded)

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        estimate = estimate_plan(conn, seeded["organization"], example_count=1000,
                                 model_identifiers=[identifier],
                                 price_book=_price_book(identifier),
                                 budget_limit=Decimal("0.01"))
    assert estimate.model_calls == 1000
    assert estimate.estimable
    assert estimate.estimated_cost > 0
    assert "mean recorded cost per call" in estimate.basis
    # A thousand calls against a budget of one cent is the "unexpectedly
    # expensive plan" REQ-N-COST-3 asks to be detectable before execution.
    assert estimate.exceeds_budget is True


def test_a_plan_within_budget_is_not_flagged(
        migrated_database, seeded, examples_with_evidence):
    examples = build_examples(examples_with_evidence)
    execute_run(migrated_database, seeded, examples, key="cost-within")
    identifier = _model_identifier(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        estimate = estimate_plan(conn, seeded["organization"], example_count=1,
                                 model_identifiers=[identifier],
                                 price_book=_price_book(identifier),
                                 budget_limit=Decimal("1000"))
    assert estimate.exceeds_budget is False


def test_no_budget_means_no_verdict_rather_than_a_default_one(
        migrated_database, seeded, examples_with_evidence):
    examples = build_examples(examples_with_evidence)
    execute_run(migrated_database, seeded, examples, key="cost-nobudget")
    identifier = _model_identifier(migrated_database, seeded)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        estimate = estimate_plan(conn, seeded["organization"], example_count=1,
                                 model_identifiers=[identifier],
                                 price_book=_price_book(identifier))
    assert estimate.exceeds_budget is None
