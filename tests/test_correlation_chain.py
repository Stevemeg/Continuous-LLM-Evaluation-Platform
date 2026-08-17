"""REQ-N-OBS-1, demonstrated: one identifier, recovered across the chain.

The requirement's verification method is Demonstration, and the thing being
demonstrated is not that each hop propagates — unit tests would show that, and
would show it for a chain with a gap in the middle just as convincingly. What is
demonstrated here is that **one identifier, chosen at ingress, is recoverable
afterwards at every hop, from the durable record, by a single query.**

So this file reuses the real end-to-end harness rather than building a parallel
one: real repositories, a real `RunExecutor`, a real judge panel, a real gate
service, a real PostgreSQL. The only stub is the HTTP opener under the provider
adapter, exactly as in `test_end_to_end.py`, because a deterministic test cannot
depend on a model being installed.

The artifact hop is asserted **absent**, deliberately. `clep.artifact` has no
writer anywhere in `src/clep` and cannot get one in this phase: a non-erased
artifact must carry a `payload_ref`, which is an object-store reference, and the
object-store adapter is `D-3` — Phase 14. Asserting its absence is what keeps
this file honest when the hop arrives: the test will fail, and somebody will have
to look at it.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.analytics.correlation_chain import CorrelationChainRepository
from clep.api import audit
from clep.api.gate_service import GateService
from clep.db.session import tenant_session
from clep.identity import is_ulid, ulid_to_uuid
from clep.orchestration.repository import RunRepository
from clep.telemetry import RecordingBackend, Telemetry, correlated, current_id
from tests.conftest import requires_postgres
from tests.test_end_to_end import (  # noqa: F401 - fixtures used by name
    approved_baseline_from, build_examples, examples_with_evidence,
    execute_run, published_judges, published_policy, second_configuration,
    _metric_key_of)

pytestmark = [pytest.mark.integration, requires_postgres]

JUDGE_REPLIES = {"model-alpha": "SCORE: 0.9", "model-beta": "SCORE: 0.8"}


def test_one_identifier_is_recovered_at_every_hop_that_has_a_writer(
        migrated_database, seeded, examples_with_evidence, second_configuration):
    examples = build_examples(examples_with_evidence)
    telemetry = Telemetry(RecordingBackend())
    judges, judge_ids, ensemble_id = published_judges(
        migrated_database, seeded, second_configuration)

    # ---------------------------------------------------- ingress, once
    # Everything below happens inside ONE correlation scope, which is what an
    # HTTP request opens. The identifier is never passed as an argument to any
    # of it; each hop takes it from the ambient scope or from the run row.
    with correlated() as scope:
        correlation_id = scope.correlation_id
        assert is_ulid(correlation_id)

        baseline_run, _ = execute_run(
            migrated_database, seeded, examples, key="chain-baseline",
            judges=judges, judge_ids=judge_ids, ensemble_id=ensemble_id,
            judge_replies=JUDGE_REPLIES)
        candidate_run, outcome = execute_run(
            migrated_database, seeded, examples, key="chain-candidate",
            judges=judges, judge_ids=judge_ids, ensemble_id=ensemble_id,
            judge_replies=JUDGE_REPLIES)
        assert outcome.samples_scored == 3
        assert outcome.panel.judgements == 6

        baseline_id = approved_baseline_from(migrated_database, seeded,
                                             baseline_run)
        metric_key = _metric_key_of(migrated_database, seeded)
        policy_version_id = published_policy(migrated_database, seeded,
                                             metric_key)

        decision = GateService(migrated_database, telemetry=telemetry).evaluate_gate(
            organization_id=seeded["organization"],
            project_id=seeded["project"], candidate_run_id=candidate_run,
            policy_version_id=policy_version_id, baseline_id=baseline_id,
            actor_id="tester")
        assert decision is not None

        # An audit event written inside the same scope, as a governed action
        # would be. Not passed the identifier either — `audit.record` reads it
        # from the scope, which is why thirty call sites did not have to change.
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            audit.record(conn, seeded["organization"], "tester",
                         "correlation.demonstrated", "run", candidate_run)

    # ------------------------------------- recovery, outside the scope
    # Outside the `with`. Nothing ambient is left; the chain is reconstructed
    # from the store alone, which is the "months later" condition
    # observability-strategy.md §2 imposes.
    assert current_id() is None
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        chain = CorrelationChainRepository(conn, seeded["organization"]) \
            .chain_for(correlation_id)

    print(f"\ncorrelation_id: {chain.correlation_id}")
    for hop in chain.hops:
        print(f"  {'PRESENT' if hop.present else 'ABSENT ':<8} {hop.name:<22} "
              f"{len(hop.rows):>3} row(s)  [{hop.reached_by}]")

    assert set(chain.present_hops) == {
        "run", "work_unit", "model_call", "evaluator_invocation",
        "judge_invocation", "gate_decision", "audit_event"}
    # The one hop with no writer, and the reason it has none is recorded in the
    # hop itself rather than in a comment somebody may not read.
    assert chain.absent_hops == ("artifact",)
    assert "D-3" in dict((h.name, h.reached_by) for h in chain.hops)["artifact"]

    # Both runs reached the chain from one identifier, and the gate decision
    # found through them is the decision the service returned.
    assert len(chain.hops[0].rows) == 2
    assert {r["id"] for r in chain.hops[0].rows} == {baseline_run, candidate_run}
    assert decision["id"] in {r["id"] for r in
                              dict((h.name, h.rows) for h in chain.hops)["gate_decision"]}


def test_a_run_that_began_without_a_request_still_gets_a_chain(
        migrated_database, seeded, examples_with_evidence):
    """The scheduler case. No request, so no ambient correlation — and a chain
    that started at whichever hop the platform happened to be looking at would
    not be a chain. Execution adopts one and writes it where it is durable."""
    examples = build_examples(examples_with_evidence)
    assert current_id() is None

    run_id, outcome = execute_run(migrated_database, seeded, examples,
                                  key="chain-unattended")
    assert outcome.samples_scored == 3

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        row = conn.execute(
            "SELECT correlation_id FROM clep.run WHERE organization_id = %s "
            "AND id = %s",
            (seeded["organization"], ulid_to_uuid(run_id))).fetchone()
        adopted = row[0]
        assert is_ulid(adopted)
        chain = CorrelationChainRepository(conn, seeded["organization"]) \
            .chain_for(adopted)

    assert "run" in chain.present_hops
    assert "evaluator_invocation" in chain.present_hops


def test_a_redelivered_run_keeps_the_correlation_the_first_delivery_established(
        migrated_database, seeded, examples_with_evidence):
    """Adoption is conditional on NULL. Replacing an identifier on redelivery
    would orphan every record already written under the old one."""
    examples = build_examples(examples_with_evidence)
    with correlated() as first:
        run_id, _ = execute_run(migrated_database, seeded, examples,
                                key="chain-redelivery")
        established = first.correlation_id

    # A second delivery, under a different ambient correlation.
    with correlated() as second:
        assert second.correlation_id != established
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            repo = RunRepository(conn, seeded["organization"])
            in_force = repo.adopt_correlation(run_id, second.correlation_id)

    assert in_force == established, (
        "a redelivery replaced the correlation the first delivery established")


def test_a_tenant_cannot_read_another_tenants_chain(
        migrated_database, seeded, examples_with_evidence, second_organization):
    """The identifier is returned to clients in a response header, so knowing
    one must not be enough to read the records behind it. Row-level security
    does the refusing; this asserts it actually does."""
    examples = build_examples(examples_with_evidence)
    with correlated() as scope:
        execute_run(migrated_database, seeded, examples, key="chain-isolation")

    with tenant_session(migrated_database, second_organization) as conn:
        chain = CorrelationChainRepository(conn, second_organization) \
            .chain_for(scope.correlation_id)
    assert chain.present_hops == (), (
        "another tenant recovered a chain from an identifier they merely knew")
