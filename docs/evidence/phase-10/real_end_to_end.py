"""One complete evaluation against real models, through the platform.

The Phase 9 experiment proved the judge layer could execute against a real
model, but drove the library directly and wrote files. The Phase 9 review
carried two things forward: an end-to-end run through the actual harness, and
judge results persisted through the repository.

This does both, at once, against real self-hosted models:

    a dataset version and examples carrying retrieval and a trajectory
      -> a run through RunExecutor, the loop the worker drives
      -> real candidate generation through OpenAICompatibleAdapter
      -> the deterministic RAG and agent evaluators
      -> a real judge ensemble through JudgePanel
      -> every judgement, vote, verdict and escalation in PostgreSQL
      -> a baseline, a published gate policy, a gate decision
      -> the decision and its evidence read back out of the store
      -> both report representations

Nothing here is stubbed. The provider is three `llama.cpp` servers, the store is
the real PostgreSQL, and every write goes through the same repositories the
application uses. No credential is used, invented or required, and no hosted
provider is billed.

What it does not prove is unchanged from Phase 9: small local models are not
models anyone would judge with, and this validates the **path**, not the
quality of any judgement.

Usage: python docs/evidence/phase-10/real_end_to_end.py
Requires the three servers and the compose stack. If a model is unreachable the
script exits non-zero rather than falling back to anything.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, "src")

from clep.api.gate_service import GateService                     # noqa: E402
from clep.config import ProviderEndpoint                          # noqa: E402
from clep.db import migrations, provision                         # noqa: E402
from clep.db.session import tenant_session                        # noqa: E402
from clep.evaluators.agent import register_agent_evaluators       # noqa: E402
from clep.evaluators.rag import register_rag_evaluators           # noqa: E402
from clep.evaluators.sdk import EvaluatorRegistry, RetrievedContext  # noqa: E402
from clep.evaluators.trajectory import ToolCall, ingest           # noqa: E402
from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid    # noqa: E402
from clep.judges.consensus import Ensemble                        # noqa: E402
from clep.judges.panel import JudgePanel                          # noqa: E402
from clep.judges.repository import JudgeRepository                # noqa: E402
from clep.judges.sdk import JudgeVersion                          # noqa: E402
from clep.orchestration.repository import RunRepository           # noqa: E402
from clep.orchestration.runner import Candidate, Example, RunExecutor  # noqa: E402
from clep.providers.gateway import Price, PriceBook, ProviderGateway   # noqa: E402
from clep.providers.openai_compatible import OpenAICompatibleAdapter   # noqa: E402
from clep.rag.repository import AnalysisRepository, digest_of     # noqa: E402
from clep.regression.repository import RegressionRepository       # noqa: E402

MIGRATION = "postgresql://postgres@localhost:5439/clep"
RUNTIME = "postgresql://clep_app@localhost:5439/clep"

JUDGES = (("qwen", "http://localhost:8101/v1",
           "/models/qwen2.5-0.5b-instruct-q4_k_m.gguf"),
          ("smollm", "http://localhost:8102/v1",
           "/models/smollm2-360m-instruct-q8_0.gguf"),
          ("llama", "http://localhost:8103/v1",
           "/models/llama-3.2-1b-instruct-q4_k_m.gguf"))

#: The candidate under evaluation is itself a real model. Phase 9 judged fixed
#: answers; here the thing being judged was generated too.
CANDIDATE = ("candidate", "http://localhost:8101/v1",
             "/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")

RUBRIC = ("Decide how strongly the PASSAGE supports the ANSWER to the QUESTION.\n"
          "1.0 = the passage states or directly entails the answer.\n"
          "0.5 = the passage is silent: it neither supports nor denies it.\n"
          "0.0 = the passage denies the answer or states the opposite.\n"
          "You must give a number. Only ABSTAIN if the answer field is empty. "
          "Never explain.")

QUESTIONS = [
    ("What is the capital of France?", "Paris",
     "France is a country in western Europe. Its capital is Paris."),
    ("How many legs does a spider have?", "Eight",
     "Spiders are arachnids. All arachnids have eight legs."),
    ("What is the capital of Australia?", "Canberra",
     "The capital of Australia is Canberra, not Sydney."),
]


def reachable(url):
    try:
        with urllib.request.urlopen(url.replace("/v1", "/health"),
                                    timeout=10) as response:
            return response.status == 200
    except (urllib.error.URLError, OSError):
        return False


def seed(org):
    """A tenant with a dataset, suite, model and examples, through migration."""
    import psycopg
    ids = {k: new_ulid() for k in
           ("project", "dataset", "dataset_version", "suite", "suite_version",
            "provider", "model", "configuration", "configuration2",
            "configuration3")}
    ids["examples"] = [new_ulid() for _ in QUESTIONS]
    u, D = ulid_to_uuid, "sha256:" + "a" * 64
    with psycopg.connect(MIGRATION, autocommit=True) as c:
        c.execute("INSERT INTO clep.organization (id, slug, display_name)"
                  " VALUES (%s,%s,'E2E')", (org, f"org-{org.hex[:8]}"))
        c.execute("INSERT INTO clep.project (id, organization_id, slug,"
                  " display_name) VALUES (%s,%s,'p','P')", (u(ids["project"]), org))
        c.execute("INSERT INTO clep.dataset (id, organization_id, project_id,"
                  " slug, display_name) VALUES (%s,%s,%s,'d','D')",
                  (u(ids["dataset"]), org, u(ids["project"])))
        c.execute("INSERT INTO clep.dataset_version (id, organization_id,"
                  " dataset_id, version_number, content_digest, schema_ref,"
                  " state) VALUES (%s,%s,%s,1,%s,'schema://x/v1','draft')",
                  (u(ids["dataset_version"]), org, u(ids["dataset"]), D))
        for ordinal, example_id in enumerate(ids["examples"]):
            c.execute("INSERT INTO clep.example (id, organization_id,"
                      " dataset_version_id, ordinal, split)"
                      " VALUES (%s,%s,%s,%s,'test')",
                      (u(example_id), org, u(ids["dataset_version"]), ordinal))
            c.execute("INSERT INTO clep.example_content (id, organization_id,"
                      " example_id, content_digest, payload_ref, byte_size)"
                      " VALUES (%s,%s,%s,%s,'s3://b/e',64)",
                      (uuid.uuid4(), org, u(example_id),
                       digest_of(QUESTIONS[ordinal][0])))
        c.execute("INSERT INTO clep.benchmark_suite (id, organization_id,"
                  " project_id, slug, display_name, owner_actor_id)"
                  " VALUES (%s,%s,%s,'s','S',%s)",
                  (u(ids["suite"]), org, u(ids["project"]), uuid.uuid4()))
        c.execute("INSERT INTO clep.suite_version (id, organization_id,"
                  " benchmark_suite_id, version_number, content_digest,"
                  " owner_actor_id) VALUES (%s,%s,%s,1,%s,%s)",
                  (u(ids["suite_version"]), org, u(ids["suite"]), D, uuid.uuid4()))
        c.execute("INSERT INTO clep.provider (id, organization_id, slug,"
                  " display_name, endpoint_kind)"
                  " VALUES (%s,%s,'local','Local','self_hosted')",
                  (u(ids["provider"]), org))
        c.execute("INSERT INTO clep.model (id, organization_id, provider_id,"
                  " model_identifier, display_name)"
                  " VALUES (%s,%s,%s,'gguf','GGUF')",
                  (u(ids["model"]), org, u(ids["provider"])))
        for n, key in ((1, "configuration"), (2, "configuration2"),
                       (3, "configuration3")):
            c.execute("INSERT INTO clep.model_configuration (id,"
                      " organization_id, model_id, version_number,"
                      " output_affecting_parameters, content_digest,"
                      " is_deterministic, state, created_by, published_at)"
                      " VALUES (%s,%s,%s,%s,'{}',%s,true,'published',%s,now())",
                      (u(ids[key]), org, u(ids["model"]), n, D, uuid.uuid4()))
        # One definition and version PER EVALUATOR. The first version of this
        # script gave all nine the same version id, so the gate's lookup by
        # metric name matched every evaluator's outcomes at once and compared a
        # mixture — which produced hard_fail on two identical runs. A metric
        # has to resolve to exactly one evaluator or it is not that metric.
        ids["evaluator_versions"] = {}
        for name in evaluator_names():
            definition_id, version_id = new_ulid(), new_ulid()
            c.execute("INSERT INTO clep.evaluator_definition (id,"
                      " organization_id, scope, slug, display_name)"
                      " VALUES (%s,%s,'custom',%s,%s)",
                      (u(definition_id), org, name, name))
            c.execute("INSERT INTO clep.evaluator_version (id, organization_id,"
                      " evaluator_definition_id, version_number, content_digest,"
                      " input_schema_ref, output_schema_ref,"
                      " declared_permissions, is_deterministic, cost_class)"
                      " VALUES (%s,%s,%s,1,%s,'in','out','none',true,'free')",
                      (u(version_id), org, u(definition_id), D))
            # Bound to the suite version. The gate resolves a metric name
            # through the RUN'S OWN SUITE rather than through anything a caller
            # supplies — a gate that let the caller name the evaluator would let
            # it choose which measurement to be judged on. An unbound evaluator
            # is therefore invisible to the gate, which is what the first
            # version of this seed discovered.
            c.execute("INSERT INTO clep.suite_evaluator (id, organization_id,"
                      " suite_version_id, evaluator_version_id)"
                      " VALUES (%s,%s,%s,%s)",
                      (uuid.uuid4(), org, u(ids["suite_version"]),
                       u(version_id)))
            ids["evaluator_versions"][name] = version_id
    return ids


def evaluator_names():
    registry = EvaluatorRegistry()
    register_rag_evaluators(registry)
    register_agent_evaluators(registry)
    return [key.split("@")[0] for key in registry.keys()]


def examples_for(ids):
    out = []
    for ordinal, (question, answer, passage) in enumerate(QUESTIONS):
        example_id = ids["examples"][ordinal]
        out.append(Example(
            id=example_id, prompt=question, expected=answer,
            content_digest=digest_of(question),
            contexts=(RetrievedContext(f"{example_id}-c1", passage, 0),),
            citations=(f"{example_id}-c1",),
            required_context_ids=(f"{example_id}-c1",),
            trajectory=ingest([
                ToolCall(0, "search", {"q": question}, passage),
                ToolCall(1, "answer", {"text": answer}, "ok")],
                final_answer=answer),
            tool_schemas={"search": {"required": ["q"], "properties": {"q": {}}},
                          "answer": {"required": ["text"],
                                     "properties": {"text": {}}}},
            expected_tools=("search", "answer")))
    return out


def build_gateway(judges):
    adapters, prices = {}, PriceBook()
    for slug, url, model in (CANDIDATE,) + JUDGES:
        adapters[slug] = OpenAICompatibleAdapter(
            ProviderEndpoint(name=slug, base_url=url, api_key="",
                             kind="self_hosted"), timeout=180.0)
        prices.declare(model, Price(Decimal("0"), Decimal("0")))
    return ProviderGateway(adapters, prices)


def run_once(org, ids, judges, judge_ids, ensemble_id, key, lines):
    registry = EvaluatorRegistry()
    register_rag_evaluators(registry)
    register_agent_evaluators(registry)
    gateway = build_gateway(judges)
    examples = examples_for(ids)

    with tenant_session(RUNTIME, str(org)) as conn:
        analysis = AnalysisRepository(conn, str(org))
        for example in examples:
            analysis.record_required_context(example.id,
                                             example.required_context_ids)
        repo = RunRepository(conn, str(org))
        run_id = repo.create_run(
            project_id=ids["project"], suite_version_id=ids["suite_version"],
            dataset_version_id=ids["dataset_version"],
            identity_digest="sha256:" + "b" * 64, integration_tier="full",
            idempotency_key=key)
        candidate_id = repo.add_candidate(
            run_id, label="a", model_configuration_id=ids["configuration"],
            endpoint_kind="self_hosted")
        panel = JudgePanel(
            ensemble=Ensemble(judges=judges, agreement_threshold=Decimal("0.30"),
                              minimum_scoring_votes=2),
            ensemble_id=ensemble_id, judge_version_ids=judge_ids,
            gateway=gateway, repository=JudgeRepository(conn, str(org)),
            project_id=ids["project"], timeout_ms=180_000)
        executor = RunExecutor(repo, gateway, registry,
                               evaluator_ids={
                                   k: ids["evaluator_versions"][k.split("@")[0]]
                                   for k in registry.keys()},
                               analysis_repository=analysis, judge_panel=panel)
        outcome = executor.execute(
            run_id, examples,
            [Candidate(id=candidate_id, label="a", model=CANDIDATE[2],
                       endpoint_name=CANDIDATE[0])],
            integration_tier="full")

    lines.append(f"  run {key}: {outcome.completeness}, "
                 f"{outcome.samples_recorded} samples, "
                 f"{outcome.evaluator_outcomes} evaluator outcomes, "
                 f"{outcome.retrieval_recorded} passages, "
                 f"{outcome.trajectory_steps_recorded} trajectory steps")
    lines.append(f"    judges: {outcome.panel.judgements} judgements, "
                 f"{outcome.panel.scored} scored, "
                 f"{outcome.panel.unreadable} unreadable, "
                 f"{outcome.panel.regenerations} regenerations, "
                 f"{outcome.panel.agreements} agreed, "
                 f"{outcome.panel.escalations} escalated")
    return run_id, outcome


def main() -> int:
    unreachable = [slug for slug, url, _ in (CANDIDATE,) + JUDGES
                   if not reachable(url)]
    if unreachable:
        print(f"REFUSING: unreachable models {unreachable}. This script does "
              f"not fall back to a stub.")
        return 2
    import psycopg
    with psycopg.connect(MIGRATION, autocommit=True) as c:
        migrations.apply(c, migrations.discover(
            migrations.schema_dir(Path("."))))
        provision.ensure_login_roles(c, "e2e-local-only")

    org = uuid.uuid4()
    ids = seed(org)
    lines, started = [], time.time()
    lines.append("REAL END-TO-END RUN — everything through the platform")
    for slug, url, model in (CANDIDATE,) + JUDGES:
        lines.append(f"  {slug:10} {url}  {model}")
    lines.append("")

    with tenant_session(RUNTIME, str(org)) as conn:
        repo = JudgeRepository(conn, str(org))
        judge_id = repo.create_judge(project_id=ids["project"], slug="support",
                                     display_name="Support")
        judge_ids, judges = {}, []
        for (slug, url, model), configuration in zip(
                JUDGES, ("configuration", "configuration2", "configuration3")):
            version = repo.add_version(
                judge_id=judge_id, model_configuration_id=ids[configuration],
                rubric=RUBRIC, created_by="e2e")
            repo.publish_version(version.id)
            judge = JudgeVersion(slug=slug, version="1", model=model,
                                 endpoint_name=slug, rubric=RUBRIC,
                                 max_tokens=24)
            judge_ids[judge.version_key] = version.id
            judges.append(judge)
        ensemble = repo.create_ensemble(
            project_id=ids["project"], slug="panel",
            judge_version_ids=[judge_ids[j.version_key] for j in judges],
            agreement_threshold=Decimal("0.30"), minimum_scoring_votes=2,
            created_by="e2e")
    judges = tuple(judges)

    baseline_run, _ = run_once(org, ids, judges, judge_ids, ensemble.id,
                               "e2e-baseline", lines)
    candidate_run, _ = run_once(org, ids, judges, judge_ids, ensemble.id,
                                "e2e-candidate", lines)

    with tenant_session(RUNTIME, str(org)) as conn:
        regression = RegressionRepository(conn, str(org))
        baseline_id = regression.create_baseline(run_id=baseline_run,
                                                 created_by="e2e",
                                                 label="release-1")
        regression.approve_baseline(baseline_id, approved_by="e2e")
        policy_id = regression.create_gate_policy(
            project_id=ids["project"], slug="release", display_name="Release")
        version_id = regression.add_policy_version(
            policy_id=policy_id, confidence_level=Decimal("0.95"),
            resample_count=200, bootstrap_seed=20260810, created_by="e2e")
        regression.add_criterion(
            version_id, metric_key="citation_validity", dimension="quality",
            source="evaluator", direction="higher_is_better",
            precision_threshold=Decimal("0.5"), on_regression="hard_fail",
            on_insufficient_evidence="warning", on_not_comparable="hard_fail")
        regression.publish_policy_version(version_id)

    service = GateService(RUNTIME)
    decision = service.evaluate_gate(
        organization_id=str(org), project_id=ids["project"],
        candidate_run_id=candidate_run, policy_version_id=version_id,
        baseline_id=baseline_id, actor_id="e2e")
    lines.append("")
    lines.append(f"  gate decision: {decision['evaluatedOutcome']}, "
                 f"{len(decision['comparisons'])} comparison(s), "
                 f"evidence {decision['gateEvidenceDigest'][:23]}...")

    read_back = service.get_decision(str(org), decision["id"])
    human = service.decision_report(str(org), decision["id"])
    lines.append(f"  read back: outcome {read_back['evaluatedOutcome']}, "
                 f"digest matches: "
                 f"{read_back['gateEvidenceDigest'] == decision['gateEvidenceDigest']}")
    lines.append(f"  human report: {len(human.splitlines())} lines")

    with tenant_session(RUNTIME, str(org)) as conn:
        analysis = AnalysisRepository(conn, str(org))
        sample = uuid_to_ulid(conn.execute(
            "SELECT id FROM clep.run_sample WHERE run_id = %s "
            "ORDER BY sample_index LIMIT 1",
            (ulid_to_uuid(candidate_run),)).fetchone()[0])
        body = analysis.analysis(sample)
        counts = {
            table: conn.execute(
                f"SELECT count(*) FROM clep.{table}").fetchone()[0]
            for table in ("judge_run", "judge_vote", "consensus_result",
                          "escalation", "retrieved_context", "trajectory_step",
                          "sample_citation", "required_context")}
    lines.append("")
    lines.append("  persisted rows: " + ", ".join(
        f"{k}={v}" for k, v in sorted(counts.items())))
    lines.append(f"  sample analysis read back: "
                 f"{len(body['retrievedContexts'])} passage(s), "
                 f"{len(body['trajectory'])} step(s), "
                 f"truncated={body['trajectoryTruncated']}")
    lines.append("")
    lines.append(f"  elapsed: {time.time() - started:.1f}s")
    lines.append("")
    lines.append("Every generation above came from a real model. Every row was "
                 "written by the platform's own repositories. No stub was used "
                 "and no credential was required.")

    out = Path("docs/evidence/phase-10/real-end-to-end-output.txt")
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps({"decision": decision["evaluatedOutcome"],
                    "comparisons": len(decision["comparisons"]),
                    "counts": counts, "analysis": body}, indent=2, default=str)
        + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
