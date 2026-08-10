"""One evaluation, all the way through, against a real database.

The Phase 9 review carried a gap forward: the RAG and agent evaluators existed
and were validated in isolation, but nothing had driven them through the
platform's own execution path. Calling the evaluator library directly and
calling the result an end-to-end run is exactly what this file must not do.

So everything below goes through `RunExecutor` — the same loop the worker
drives — with a real `ProviderGateway`, real repositories, and a real
PostgreSQL. The only stub is the HTTP opener underneath the provider adapter,
because a deterministic test cannot depend on a model being installed; the real
models are exercised separately and recorded in
`docs/evidence/phase-10/`.

The flow this proves, in one test:

    dataset examples with retrieval and a trajectory
      -> run through RunExecutor
      -> deterministic RAG and agent evaluators
      -> a judge ensemble, executed and persisted
      -> per-sample scores
      -> baseline comparison
      -> gate policy decision
      -> evidence in the store
      -> read back through the analysis surface
"""
from __future__ import annotations

import io
import json
import urllib.error
import uuid
from decimal import Decimal

import psycopg
import pytest

from clep.config import ProviderEndpoint
from clep.db.session import tenant_session
from clep.evaluators.agent import register_agent_evaluators
from clep.evaluators.rag import register_rag_evaluators
from clep.evaluators.sdk import EvaluatorRegistry, RetrievedContext
from clep.evaluators.trajectory import ToolCall, ingest
from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid
from clep.judges.consensus import Ensemble
from clep.judges.panel import JudgePanel
from clep.judges.repository import JudgeRepository
from clep.judges.sdk import JudgeVersion
from clep.orchestration.repository import RunRepository
from clep.orchestration.runner import Candidate, Example, RunExecutor
from clep.providers.gateway import Price, PriceBook, ProviderGateway
from clep.providers.openai_compatible import OpenAICompatibleAdapter
from clep.rag.repository import AnalysisRepository, digest_of
from tests.conftest import MIGRATION_DSN, requires_postgres

pytestmark = [pytest.mark.integration, requires_postgres]

ANSWER = "Paris is the capital of France."


class _Body(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_for(replies):
    """An HTTP opener that answers differently per model.

    Stubbed at the transport, not above it: everything from
    `OpenAICompatibleAdapter` upwards is the production code path, including
    the parse that rejects a reply it cannot read.
    """
    def _open(request, timeout=None):
        body = json.loads(request.data.decode())
        text = replies.get(body["model"], ANSWER)
        return _Body(json.dumps({
            "choices": [{"message": {"content": text}}],
            "model": body["model"],
            "usage": {"prompt_tokens": 11, "completion_tokens": 7,
                      "total_tokens": 18},
        }).encode())
    return _open


@pytest.fixture
def examples_with_evidence(seeded):
    """Three examples carrying retrieval, citations and a trajectory."""
    ids = []
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        for ordinal in range(3):
            example_id = new_ulid()
            conn.execute(
                "INSERT INTO clep.example (id, organization_id, "
                "dataset_version_id, ordinal, split) VALUES (%s,%s,%s,%s,'test')",
                (ulid_to_uuid(example_id), seeded["organization"],
                 ulid_to_uuid(seeded["dataset_version"]), 10 + ordinal))
            conn.execute(
                "INSERT INTO clep.example_content (id, organization_id, "
                "example_id, content_digest, payload_ref, byte_size) "
                "VALUES (%s,%s,%s,%s,'s3://bucket/e',64)",
                (uuid.uuid4(), seeded["organization"], ulid_to_uuid(example_id),
                 digest_of(f"example-{ordinal}")))
            ids.append(example_id)
    return ids


def build_examples(example_ids):
    out = []
    for ordinal, example_id in enumerate(example_ids):
        contexts = (RetrievedContext(f"{example_id}-c1", "France's capital is Paris.", 0),
                    RetrievedContext(f"{example_id}-c2", "Lyon is a French city.", 1))
        trajectory = ingest([
            ToolCall(0, "search", {"q": "capital of France"}, "hit"),
            ToolCall(1, "open", {"id": "1"}, "", failed=True, error="404"),
            ToolCall(2, "answer", {"text": "Paris"}, "ok")],
            final_answer=ANSWER)
        # The third example is missing a required passage, so the run produces
        # a retrieval-stage failure to attribute rather than a uniform pass.
        required = ((f"{example_id}-c1",) if ordinal < 2
                    else (f"{example_id}-c1", f"{example_id}-missing"))
        out.append(Example(
            id=example_id, prompt="What is the capital of France?",
            expected="Paris", content_digest=digest_of(f"example-{ordinal}"),
            contexts=contexts, citations=(f"{example_id}-c1",),
            required_context_ids=required, trajectory=trajectory,
            tool_schemas={"search": {"required": ["q"], "properties": {"q": {}}},
                          "open": {"required": ["id"], "properties": {"id": {}}},
                          "answer": {"required": ["text"],
                                     "properties": {"text": {}}}},
            expected_tools=("search", "answer")))
    return out


def registry_with_everything():
    registry = EvaluatorRegistry()
    register_rag_evaluators(registry)
    register_agent_evaluators(registry)
    return registry


def published_judges(dsn, seeded, second_configuration):
    """Two published judge versions on two model configurations."""
    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = JudgeRepository(conn, seeded["organization"])
        judge_id = repo.create_judge(project_id=seeded["project"],
                                     slug="helpfulness", display_name="H")
        ids, judges = {}, []
        for slug, configuration in (("alpha", seeded["model_configuration"]),
                                    ("beta", second_configuration)):
            version = repo.add_version(
                judge_id=judge_id, model_configuration_id=configuration,
                rubric=f"Score {slug}.", created_by="tester")
            repo.publish_version(version.id)
            judge = JudgeVersion(slug=slug, version="1", model=f"model-{slug}",
                                 endpoint_name=slug, rubric=f"Score {slug}.",
                                 max_tokens=16)
            ids[judge.version_key] = version.id
            judges.append(judge)
        ensemble = repo.create_ensemble(
            project_id=seeded["project"], slug="panel",
            judge_version_ids=[ids[j.version_key] for j in judges],
            agreement_threshold=Decimal("0.30"), minimum_scoring_votes=2,
            created_by="tester")
    return tuple(judges), ids, ensemble.id


@pytest.fixture
def second_configuration(seeded):
    configuration_id = new_ulid()
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO clep.model_configuration (id, organization_id, model_id, "
            "version_number, output_affecting_parameters, content_digest, "
            "is_deterministic, state, created_by, published_at) "
            "VALUES (%s,%s,%s,7,'{}',%s,true,'published',%s, now())",
            (ulid_to_uuid(configuration_id), seeded["organization"],
             ulid_to_uuid(seeded["model"]), digest_of("second"), uuid.uuid4()))
    return configuration_id


def execute_run(dsn, seeded, examples, *, key, judges=None, judge_ids=None,
                ensemble_id=None, judge_replies=None):
    """A real run through RunExecutor, with everything wired."""
    registry = registry_with_everything()
    evaluator_ids = {k: seeded["evaluator_version"] for k in registry.keys()}
    prices = PriceBook({"candidate-model": Price(Decimal("0.001"),
                                                 Decimal("0.002"))})
    replies = {"candidate-model": ANSWER}
    replies.update(judge_replies or {})
    adapters = {"main": OpenAICompatibleAdapter(
        ProviderEndpoint(name="main", base_url="http://endpoint.invalid/v1",
                         api_key=""), opener=opener_for(replies))}
    if judges:
        for judge in judges:
            adapters[judge.endpoint_name] = OpenAICompatibleAdapter(
                ProviderEndpoint(name=judge.endpoint_name,
                                 base_url="http://endpoint.invalid/v1",
                                 api_key=""), opener=opener_for(replies))
            prices.declare(judge.model, Price(Decimal("0"), Decimal("0")))
    gateway = ProviderGateway(adapters, prices)

    with tenant_session(dsn, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        analysis = AnalysisRepository(conn, seeded["organization"])
        for example in examples:
            analysis.record_required_context(example.id,
                                             example.required_context_ids)
        run_id = repo.create_run(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "1" * 64, integration_tier="full",
            idempotency_key=key)
        candidate_id = repo.add_candidate(
            run_id, label="a",
            model_configuration_id=seeded["model_configuration"],
            endpoint_kind="hosted")

        panel = None
        if judges:
            panel = JudgePanel(
                ensemble=Ensemble(judges=judges,
                                  agreement_threshold=Decimal("0.30"),
                                  minimum_scoring_votes=2),
                ensemble_id=ensemble_id, judge_version_ids=judge_ids,
                gateway=gateway,
                repository=JudgeRepository(conn, seeded["organization"]),
                project_id=seeded["project"])

        executor = RunExecutor(repo, gateway, registry,
                               evaluator_ids=evaluator_ids,
                               analysis_repository=analysis, judge_panel=panel)
        outcome = executor.execute(
            run_id, examples,
            [Candidate(id=candidate_id, label="a", model="candidate-model",
                       endpoint_name="main")],
            integration_tier="full")
    return run_id, outcome


# ============================================================ the end-to-end
def test_a_run_drives_the_phase_9_evaluators_and_records_their_evidence(
        migrated_database, seeded, examples_with_evidence):
    """M10.1 and M10.3. Nothing here calls an evaluator directly."""
    examples = build_examples(examples_with_evidence)
    run_id, outcome = execute_run(migrated_database, seeded, examples, key="e2e-1")

    assert outcome.completeness == "complete"
    assert outcome.samples_recorded == 3
    # Nine evaluators, three samples: the RAG and agent evaluators ran through
    # the loop rather than beside it.
    assert outcome.evaluator_outcomes == 27
    assert outcome.retrieval_recorded == 6
    assert outcome.trajectory_steps_recorded == 9

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        analysis = AnalysisRepository(conn, seeded["organization"])
        sample_id = uuid_to_ulid(conn.execute(
            "SELECT id FROM clep.run_sample WHERE run_id = %s "
            "ORDER BY sample_index LIMIT 1",
            (ulid_to_uuid(run_id),)).fetchone()[0])
        body = analysis.analysis(sample_id)

    # Read back: what the run wrote is what the surface returns.
    assert [c["contextRef"] for c in body["retrievedContexts"]] == [
        f"{examples[0].id}-c1", f"{examples[0].id}-c2"]
    assert body["retrievedContexts"][0]["cited"] is True
    assert body["retrievedContexts"][1]["cited"] is False
    assert body["retrievedContexts"][0]["required"] is True
    assert body["requiredContextRefs"] == [f"{examples[0].id}-c1"]
    assert [s["tool"] for s in body["trajectory"]] == ["search", "open", "answer"]
    assert body["trajectory"][1]["failed"] is True
    assert body["trajectoryTruncated"] is False
    # The passage itself is not in the store; only its digest.
    assert body["retrievedContexts"][0]["contentDigest"] == \
        digest_of("France's capital is Paris.")


def test_the_missing_required_passage_survives_into_the_read_back(
        migrated_database, seeded, examples_with_evidence):
    """REQ-F-03-6 end to end: the third example was labelled as needing a
    passage retrieval never returned, and the evidence says so."""
    examples = build_examples(examples_with_evidence)
    run_id, _ = execute_run(migrated_database, seeded, examples, key="e2e-2")

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        analysis = AnalysisRepository(conn, seeded["organization"])
        sample_id = uuid_to_ulid(conn.execute(
            "SELECT id FROM clep.run_sample WHERE run_id = %s "
            "ORDER BY sample_index DESC LIMIT 1",
            (ulid_to_uuid(run_id),)).fetchone()[0])
        body = analysis.analysis(sample_id)

    retrieved = {c["contextRef"] for c in body["retrievedContexts"]}
    assert f"{examples[2].id}-missing" in body["requiredContextRefs"]
    assert f"{examples[2].id}-missing" not in retrieved


def test_judges_run_inside_the_loop_and_land_in_the_store(
        migrated_database, seeded, examples_with_evidence, second_configuration):
    """M10.2. The Phase 9 experiment wrote files; this writes rows."""
    judges, judge_ids, ensemble_id = published_judges(
        migrated_database, seeded, second_configuration)
    examples = build_examples(examples_with_evidence)
    run_id, outcome = execute_run(
        migrated_database, seeded, examples, key="e2e-3", judges=judges,
        judge_ids=judge_ids, ensemble_id=ensemble_id,
        judge_replies={"model-alpha": "SCORE: 0.9", "model-beta": "SCORE: 0.8"})

    assert outcome.panel.judgements == 6
    assert outcome.panel.scored == 6
    assert outcome.panel.agreements == 3
    assert outcome.panel.escalations == 0

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        runs = conn.execute(
            "SELECT count(*) FROM clep.judge_run WHERE run_id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0]
        votes = conn.execute(
            "SELECT count(*) FROM clep.judge_vote v JOIN clep.judge_run r "
            "ON r.id = v.judge_run_id WHERE r.run_id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0]
        consensus = conn.execute(
            "SELECT state, verdict, disagreement_measured FROM "
            "clep.consensus_result WHERE run_id = %s LIMIT 1",
            (ulid_to_uuid(run_id),)).fetchone()
    assert (runs, votes) == (6, 6)
    assert consensus[0] == "agreed"
    assert consensus[1] == Decimal("0.850000000")
    assert consensus[2] is True


def test_a_disagreeing_panel_escalates_and_the_escalation_is_persisted(
        migrated_database, seeded, examples_with_evidence, second_configuration):
    judges, judge_ids, ensemble_id = published_judges(
        migrated_database, seeded, second_configuration)
    examples = build_examples(examples_with_evidence)
    run_id, outcome = execute_run(
        migrated_database, seeded, examples, key="e2e-4", judges=judges,
        judge_ids=judge_ids, ensemble_id=ensemble_id,
        judge_replies={"model-alpha": "SCORE: 0.1", "model-beta": "SCORE: 0.9"})

    assert outcome.panel.escalations == 3
    assert outcome.panel.agreements == 0
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        open_escalations = conn.execute(
            "SELECT count(*) FROM clep.escalation e JOIN clep.consensus_result c "
            "ON c.id = e.consensus_result_id WHERE c.run_id = %s AND "
            "e.state = 'open'", (ulid_to_uuid(run_id),)).fetchone()[0]
        verdicts = conn.execute(
            "SELECT count(*) FROM clep.consensus_result WHERE run_id = %s "
            "AND verdict IS NOT NULL", (ulid_to_uuid(run_id),)).fetchone()[0]
    assert open_escalations == 3
    assert verdicts == 0, "an escalated judgement must carry no number"


def test_an_unreadable_judge_reply_is_recorded_as_unreadable(
        migrated_database, seeded, examples_with_evidence, second_configuration):
    """The real models did this constantly. The store must show it rather than
    show an ensemble that looks unanimous because a dissenter crashed."""
    judges, judge_ids, ensemble_id = published_judges(
        migrated_database, seeded, second_configuration)
    examples = build_examples(examples_with_evidence)
    run_id, outcome = execute_run(
        migrated_database, seeded, examples, key="e2e-5", judges=judges,
        judge_ids=judge_ids, ensemble_id=ensemble_id,
        judge_replies={"model-alpha": "0.9", "model-beta": "SCORE: 0.8"})

    assert outcome.panel.unreadable == 3
    assert outcome.panel.scored == 3
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        failed = conn.execute(
            "SELECT count(*) FROM clep.judge_run WHERE run_id = %s "
            "AND resolution = 'failed'", (ulid_to_uuid(run_id),)).fetchone()[0]
        votes = conn.execute(
            "SELECT count(*) FROM clep.judge_vote v JOIN clep.judge_run r "
            "ON r.id = v.judge_run_id WHERE r.run_id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0]
        state = conn.execute(
            "SELECT state, escalation_reason FROM clep.consensus_result "
            "WHERE run_id = %s LIMIT 1", (ulid_to_uuid(run_id),)).fetchone()
    assert failed == 3
    assert votes == 3, "an unreadable judgement has no score row to read as zero"
    assert state == ("escalated", "insufficient_scoring_votes")


def test_a_failed_candidate_is_not_judged(
        migrated_database, seeded, examples_with_evidence, second_configuration):
    """A judgement of nothing would be a score against a provider outage."""
    judges, judge_ids, ensemble_id = published_judges(
        migrated_database, seeded, second_configuration)
    examples = build_examples(examples_with_evidence)

    def failing_opener(request, timeout=None):
        raise urllib.error.URLError(ConnectionRefusedError("refused"))

    registry = registry_with_everything()
    adapters = {"main": OpenAICompatibleAdapter(
        ProviderEndpoint(name="main", base_url="http://endpoint.invalid/v1",
                         api_key=""), opener=failing_opener)}
    for judge in judges:
        adapters[judge.endpoint_name] = OpenAICompatibleAdapter(
            ProviderEndpoint(name=judge.endpoint_name,
                             base_url="http://endpoint.invalid/v1", api_key=""),
            opener=opener_for({"model-alpha": "SCORE: 0.9",
                               "model-beta": "SCORE: 0.9"}))
    gateway = ProviderGateway(adapters, PriceBook())

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = RunRepository(conn, seeded["organization"])
        run_id = repo.create_run(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "2" * 64, integration_tier="full",
            idempotency_key="e2e-6")
        candidate_id = repo.add_candidate(
            run_id, label="a",
            model_configuration_id=seeded["model_configuration"],
            endpoint_kind="hosted")
        panel = JudgePanel(
            ensemble=Ensemble(judges=judges, agreement_threshold=Decimal("0.30"),
                              minimum_scoring_votes=2),
            ensemble_id=ensemble_id, judge_version_ids=judge_ids,
            gateway=gateway,
            repository=JudgeRepository(conn, seeded["organization"]),
            project_id=seeded["project"])
        executor = RunExecutor(repo, gateway, registry, judge_panel=panel)
        outcome = executor.execute(
            run_id, examples,
            [Candidate(id=candidate_id, label="a", model="candidate-model",
                       endpoint_name="main")], integration_tier="full")

    assert outcome.samples_failed == 3
    assert outcome.panel.judgements == 0
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        assert conn.execute(
            "SELECT count(*) FROM clep.judge_run WHERE run_id = %s",
            (ulid_to_uuid(run_id),)).fetchone()[0] == 0
