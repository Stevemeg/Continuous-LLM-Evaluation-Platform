"""Probe schema 08 against a real database. Assume nothing; make it refuse.

Two passes, for the reason Phase 7 established. The first writes as the runtime
role, which is how the application writes, and exercises the constraints and the
grants together. The second writes as a superuser, where no grant and no
row-level-security policy applies, so the only thing left that can refuse is the
trigger — and the trigger is what the schema comments claim will hold "even if a
grant were widened by mistake".

Usage: python docs/evidence/phase-8/probe_judge_schema.py
Requires the compose stack up and the schema applied.
"""
from __future__ import annotations

import sys
import uuid

import psycopg

sys.path.insert(0, "src")
from clep.db.session import tenant_session  # noqa: E402

MIGRATION = "postgresql://postgres@localhost:5439/clep"
RUNTIME = "postgresql://clep_app@localhost:5439/clep"

org = uuid.uuid4()
ids = {k: uuid.uuid4() for k in
       ("project", "dataset", "dataset_version", "example", "suite",
        "suite_version", "run", "candidate", "sample", "provider", "model",
        "config", "config2", "judge", "version", "version2", "ensemble",
        "judge_run", "consensus", "escalation", "plan", "amendment")}
D = {n: "sha256:" + n * 64 for n in "abcdef"}
ACTOR = uuid.uuid4()

results = []
trigger_results = []


def rt():
    return tenant_session(RUNTIME, str(org))


def probe(label, fn, *, expect_refusal=True):
    try:
        fn()
        ok, detail = (not expect_refusal), "accepted"
    except Exception as exc:  # noqa: BLE001 - the message is the evidence
        ok, detail = expect_refusal, str(exc).strip().splitlines()[0][:120]
    results.append((ok, label, detail))
    print(f"[{'OK  ' if ok else 'MISS'}] {label}: {detail}")


def write(statement, params=()):
    """Run one statement as the runtime role, in its own tenant session."""
    def _run():
        with rt() as conn:
            conn.execute(statement, params)
    return _run


# ------------------------------------------------------------------- seeding
def seed():
    with psycopg.connect(MIGRATION, autocommit=True) as c:
        c.execute("INSERT INTO clep.organization (id, slug, display_name)"
                  " VALUES (%s,%s,'Probe')", (org, f"org-{org.hex[:8]}"))
        c.execute("INSERT INTO clep.project (id, organization_id, slug, display_name)"
                  " VALUES (%s,%s,'p','P')", (ids["project"], org))
        c.execute("INSERT INTO clep.dataset (id, organization_id, project_id, slug,"
                  " display_name) VALUES (%s,%s,%s,'d','D')",
                  (ids["dataset"], org, ids["project"]))
        c.execute("INSERT INTO clep.dataset_version (id, organization_id, dataset_id,"
                  " version_number, content_digest, schema_ref, state)"
                  " VALUES (%s,%s,%s,1,%s,'schema://x/v1','draft')",
                  (ids["dataset_version"], org, ids["dataset"], D["a"]))
        c.execute("INSERT INTO clep.example (id, organization_id, dataset_version_id,"
                  " ordinal, split) VALUES (%s,%s,%s,0,'test')",
                  (ids["example"], org, ids["dataset_version"]))
        c.execute("INSERT INTO clep.benchmark_suite (id, organization_id, project_id,"
                  " slug, display_name, owner_actor_id) VALUES (%s,%s,%s,'s','S',%s)",
                  (ids["suite"], org, ids["project"], ACTOR))
        c.execute("INSERT INTO clep.suite_version (id, organization_id,"
                  " benchmark_suite_id, version_number, content_digest,"
                  " owner_actor_id) VALUES (%s,%s,%s,1,%s,%s)",
                  (ids["suite_version"], org, ids["suite"], D["b"], ACTOR))
        c.execute("INSERT INTO clep.provider (id, organization_id, slug,"
                  " display_name, endpoint_kind)"
                  " VALUES (%s,%s,'pv','PV','hosted')",
                  (ids["provider"], org))
        c.execute("INSERT INTO clep.model (id, organization_id, provider_id,"
                  " model_identifier, display_name)"
                  " VALUES (%s,%s,%s,'m','M')",
                  (ids["model"], org, ids["provider"]))
        for n, key in ((1, "config"), (2, "config2")):
            c.execute("INSERT INTO clep.model_configuration (id, organization_id,"
                      " model_id, version_number, output_affecting_parameters,"
                      " content_digest, is_deterministic, state, created_by,"
                      " published_at) VALUES (%s,%s,%s,%s,'{}',%s,true,"
                      "'published',%s, now())",
                      (ids[key], org, ids["model"], n, D["c"], ACTOR))
        c.execute("INSERT INTO clep.run (id, organization_id, project_id,"
                  " dataset_version_id, suite_version_id, identity_digest,"
                  " execution_state, completeness, reproducibility,"
                  " integration_tier, idempotency_key, completed_at)"
                  " VALUES (%s,%s,%s,%s,%s,%s,'terminal','complete','reproducible',"
                  "'full','probe-run', now())",
                  (ids["run"], org, ids["project"], ids["dataset_version"],
                   ids["suite_version"], D["d"]))
        c.execute("INSERT INTO clep.run_candidate (id, organization_id, run_id,"
                  " label, model_configuration_id, endpoint_kind)"
                  " VALUES (%s,%s,%s,'c',%s,'hosted')",
                  (ids["candidate"], org, ids["run"], ids["config"]))
        c.execute("INSERT INTO clep.run_sample (id, organization_id, run_id,"
                  " run_candidate_id, example_id, sample_index, resolution, score,"
                  " idempotency_key)"
                  " VALUES (%s,%s,%s,%s,%s,0,'scored',0.5,'probe-s')",
                  (ids["sample"], org, ids["run"], ids["candidate"],
                   ids["example"]))


def seed_judges():
    with rt() as conn:
        conn.execute("INSERT INTO clep.judge_definition (id, organization_id,"
                     " project_id, slug, display_name) VALUES (%s,%s,%s,'j','J')",
                     (ids["judge"], org, ids["project"]))
        for key, config, n in ((ids["version"], ids["config"], 1),
                               (ids["version2"], ids["config2"], 2)):
            conn.execute("INSERT INTO clep.judge_version (id, organization_id,"
                         " judge_definition_id, model_configuration_id,"
                         " version_number, rubric_digest, content_digest, state,"
                         " created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,'draft',%s)",
                         (key, org, ids["judge"], config, n, D["e"], D["f"], ACTOR))
        conn.execute("INSERT INTO clep.judge_ensemble (id, organization_id,"
                     " project_id, slug, agreement_threshold, content_digest,"
                     " created_by) VALUES (%s,%s,%s,'panel',0.2,%s,%s)",
                     (ids["ensemble"], org, ids["project"], D["e"], ACTOR))
        for version in (ids["version"], ids["version2"]):
            conn.execute("INSERT INTO clep.judge_ensemble_member (id,"
                         " organization_id, judge_ensemble_id, judge_version_id)"
                         " VALUES (%s,%s,%s,%s)",
                         (uuid.uuid4(), org, ids["ensemble"], version))
        conn.execute("UPDATE clep.judge_version SET state='published',"
                     " published_at=now() WHERE id=%s", (ids["version"],))


seed()
seed_judges()
print("seeded")

# ======================================================= judges and versions
probe("a rubric digest that is not a sha256 is refused",
      write("INSERT INTO clep.judge_version (id, organization_id,"
            " judge_definition_id, model_configuration_id, version_number,"
            " rubric_digest, content_digest, state, created_by)"
            " VALUES (%s,%s,%s,%s,9,'not-a-digest',%s,'draft',%s)",
            (uuid.uuid4(), org, ids["judge"], ids["config"], D["f"], ACTOR)))

probe("a published judge version cannot be edited",
      write("UPDATE clep.judge_version SET rubric_digest=%s WHERE id=%s",
            (D["a"], ids["version"])))

probe("a draft judge version CAN be edited",
      write("UPDATE clep.judge_version SET rubric_digest=%s WHERE id=%s",
            (D["a"], ids["version2"])), expect_refusal=False)

probe("an ensemble threshold above 1 is refused",
      write("INSERT INTO clep.judge_ensemble (id, organization_id, project_id,"
            " slug, agreement_threshold, content_digest, created_by)"
            " VALUES (%s,%s,%s,'bad',1.5,%s,%s)",
            (uuid.uuid4(), org, ids["project"], D["e"], ACTOR)))

probe("a minimum scoring-vote count below two is refused",
      write("INSERT INTO clep.judge_ensemble (id, organization_id, project_id,"
            " slug, minimum_scoring_votes, content_digest, created_by)"
            " VALUES (%s,%s,%s,'bad2',1,%s,%s)",
            (uuid.uuid4(), org, ids["project"], D["e"], ACTOR)))

# ================================================== judgements and their votes
JUDGEMENT = ("INSERT INTO clep.judge_run (id, organization_id, run_id,"
             " run_sample_id, judge_version_id, resolution, latency_ms,"
             " prompt_digest, idempotency_key) VALUES (%s,%s,%s,%s,%s,%s,10,%s,%s)")
write(JUDGEMENT, (ids["judge_run"], org, ids["run"], ids["sample"],
                  ids["version"], "scored", D["a"], "probe-j1"))()

probe("a judgement cannot be recorded twice under one idempotency key",
      write(JUDGEMENT, (uuid.uuid4(), org, ids["run"], ids["sample"],
                        ids["version2"], "scored", D["a"], "probe-j1")))

probe("a cost with no currency is refused",
      write("INSERT INTO clep.judge_run (id, organization_id, run_id,"
            " run_sample_id, judge_version_id, resolution, cost, prompt_digest,"
            " idempotency_key) VALUES (%s,%s,%s,%s,%s,'scored',0.01,%s,'probe-j2')",
            (uuid.uuid4(), org, ids["run"], ids["sample"], ids["version2"],
             D["a"])))

probe("a judgement is immutable",
      write("UPDATE clep.judge_run SET resolution='abstained' WHERE id=%s",
            (ids["judge_run"],)))

probe("a judgement cannot be deleted",
      write("DELETE FROM clep.judge_run WHERE id=%s", (ids["judge_run"],)))

probe("a score outside [0, 1] is refused",
      write("INSERT INTO clep.judge_vote (id, organization_id, judge_run_id, score)"
            " VALUES (%s,%s,%s,1.5)", (uuid.uuid4(), org, ids["judge_run"])))

write("INSERT INTO clep.judge_vote (id, organization_id, judge_run_id, score)"
      " VALUES (%s,%s,%s,0.8)", (uuid.uuid4(), org, ids["judge_run"]))()

probe("a judgement cannot carry two scores",
      write("INSERT INTO clep.judge_vote (id, organization_id, judge_run_id, score)"
            " VALUES (%s,%s,%s,0.2)", (uuid.uuid4(), org, ids["judge_run"])))

# ============================================================ consensus, I-22
CONSENSUS = ("INSERT INTO clep.consensus_result (id, organization_id, run_id,"
             " run_sample_id, judge_ensemble_id, state, disagreement,"
             " disagreement_measured, method_version, verdict, confidence,"
             " escalation_reason, scoring_vote_count)"
             " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'range-disagreement/1',%s,NULL,%s,2)")


def consensus(state, disagreement, measured, verdict, reason,
              row_id=None, sample=None):
    return write(CONSENSUS, (row_id or uuid.uuid4(), org, ids["run"],
                             sample or ids["sample"], ids["ensemble"], state,
                             disagreement, measured, verdict, reason))


probe("an agreed result with no verdict is refused",
      consensus("agreed", "0.05", True, None, None))

probe("an escalated result carrying a verdict is refused",
      consensus("escalated", "0.80", True, "0.5", "disagreement_above_threshold"))

probe("an escalated result with no reason is refused",
      consensus("escalated", "0.80", True, None, None))

probe("an unmeasured disagreement of zero is refused",
      consensus("escalated", "0", False, None, "insufficient_scoring_votes"))

probe("an unknown escalation reason is refused",
      consensus("escalated", "0.80", True, None, "we_felt_like_it"))

probe("a confidence on an unmeasured disagreement is refused",
      write("INSERT INTO clep.consensus_result (id, organization_id, run_id,"
            " run_sample_id, judge_ensemble_id, state, disagreement,"
            " disagreement_measured, method_version, confidence,"
            " escalation_reason, scoring_vote_count)"
            " VALUES (%s,%s,%s,%s,%s,'escalated',1,false,'x/1',0.5,"
            "'insufficient_scoring_votes',1)",
            (uuid.uuid4(), org, ids["run"], ids["sample"], ids["ensemble"])))

consensus("escalated", "0.80", True, None, "disagreement_above_threshold",
          row_id=ids["consensus"])()

probe("a consensus result is immutable",
      write("UPDATE clep.consensus_result SET disagreement=0.1 WHERE id=%s",
            (ids["consensus"],)))

probe("an ensemble that has judged cannot be recomposed",
      write("UPDATE clep.judge_ensemble SET agreement_threshold=0.9 WHERE id=%s",
            (ids["ensemble"],)))

# A judge version that is NOT already a member, so the refusal comes from the
# trigger rather than from the uniqueness constraint. The first version of this
# probe reused an existing member and was satisfied by a duplicate-key error,
# which proves nothing about whether a used ensemble can grow.
ids["version3"] = uuid.uuid4()
write("INSERT INTO clep.judge_version (id, organization_id, judge_definition_id,"
      " model_configuration_id, version_number, rubric_digest, content_digest,"
      " state, created_by) VALUES (%s,%s,%s,%s,3,%s,%s,'draft',%s)",
      (ids["version3"], org, ids["judge"], ids["config2"], D["e"], D["f"],
       ACTOR))()

probe("a new member cannot be added to an ensemble that has judged",
      write("INSERT INTO clep.judge_ensemble_member (id, organization_id,"
            " judge_ensemble_id, judge_version_id) VALUES (%s,%s,%s,%s)",
            (uuid.uuid4(), org, ids["ensemble"], ids["version3"])))

# An ensemble that has judged nothing, kept for the superuser pass below. The
# runtime role has no UPDATE grant on ensembles at all, so the positive case
# cannot be shown here — and that is exactly how the defect the superuser pass
# found stayed hidden.
ids["unused"] = uuid.uuid4()
write("INSERT INTO clep.judge_ensemble (id, organization_id, project_id, slug,"
      " agreement_threshold, content_digest, created_by)"
      " VALUES (%s,%s,%s,'unused',0.2,%s,%s)",
      (ids["unused"], org, ids["project"], D["e"], ACTOR))()

probe("the runtime role cannot edit an ensemble at all",
      write("UPDATE clep.judge_ensemble SET agreement_threshold=0.3 WHERE id=%s",
            (ids["unused"],)))

# ============================================================== escalation
write("INSERT INTO clep.escalation (id, organization_id, project_id,"
      " consensus_result_id, reason) VALUES (%s,%s,%s,%s,"
      "'disagreement_above_threshold')",
      (ids["escalation"], org, ids["project"], ids["consensus"]))()

probe("an open escalation cannot carry half a review",
      write("UPDATE clep.escalation SET reviewed_by=%s WHERE id=%s",
            (ACTOR, ids["escalation"])))

REVIEW = ("UPDATE clep.escalation SET state='reviewed', reviewed_by=%s,"
          " reviewed_at=now(), review_outcome='ok', justification='j'"
          " WHERE id=%s")

probe("an escalation CAN be reviewed once",
      write(REVIEW, (ACTOR, ids["escalation"])), expect_refusal=False)

probe("an escalation cannot be reviewed twice",
      write(REVIEW, (ACTOR, ids["escalation"])))

probe("an escalation cannot be deleted",
      write("DELETE FROM clep.escalation WHERE id=%s", (ids["escalation"],)))

# =================================================================== plans
write("INSERT INTO clep.evaluation_plan (id, organization_id, project_id, state,"
      " objective, suite_version_id, estimated_cost, content_digest, created_by)"
      " VALUES (%s,%s,%s,'draft','o',%s,0.01,%s,%s)",
      (ids["plan"], org, ids["project"], ids["suite_version"], D["a"], ACTOR))()
write("INSERT INTO clep.plan_step (id, organization_id, evaluation_plan_id,"
      " plan_digest, step_order, kind, subject)"
      " VALUES (%s,%s,%s,%s,0,'score_candidate','c')",
      (uuid.uuid4(), org, ids["plan"], D["a"]))()
write("INSERT INTO clep.plan_amendment (id, organization_id, evaluation_plan_id,"
      " actor_id, note, prior_digest) VALUES (%s,%s,%s,%s,'note',%s)",
      (ids["amendment"], org, ids["plan"], ACTOR, D["a"]))()

probe("a plan whose estimate exceeds its budget is refused",
      write("INSERT INTO clep.evaluation_plan (id, organization_id, project_id,"
            " state, objective, suite_version_id, budget_limit, budget_currency,"
            " estimated_cost, content_digest, created_by)"
            " VALUES (%s,%s,%s,'draft','o',%s,0.01,'USD',5.00,%s,%s)",
            (uuid.uuid4(), org, ids["project"], ids["suite_version"], D["a"],
             ACTOR)))

probe("an accepted plan with no acceptor is refused",
      write("INSERT INTO clep.evaluation_plan (id, organization_id, project_id,"
            " state, objective, suite_version_id, estimated_cost, content_digest,"
            " created_by) VALUES (%s,%s,%s,'accepted','o',%s,0,%s,%s)",
            (uuid.uuid4(), org, ids["project"], ids["suite_version"], D["a"],
             ACTOR)))

probe("an amendment is immutable once written",
      write("UPDATE clep.plan_amendment SET note='different' WHERE id=%s",
            (ids["amendment"],)))

probe("a draft plan CAN be accepted",
      write("UPDATE clep.evaluation_plan SET state='accepted', accepted_by=%s,"
            " accepted_at=now() WHERE id=%s", (ACTOR, ids["plan"])),
      expect_refusal=False)

probe("an accepted plan cannot gain a step",
      write("INSERT INTO clep.plan_step (id, organization_id, evaluation_plan_id,"
            " plan_digest, step_order, kind, subject)"
            " VALUES (%s,%s,%s,%s,9,'evaluate_gate','g')",
            (uuid.uuid4(), org, ids["plan"], D["a"])))

probe("an accepted plan cannot be reopened",
      write("UPDATE clep.evaluation_plan SET state='draft' WHERE id=%s",
            (ids["plan"],)))

# ================================================== bounded reasoning history
write("INSERT INTO clep.reasoning_trace (id, organization_id, evaluation_plan_id,"
      " state, max_iterations, budget, timeout_ms, stopped_because)"
      " VALUES (%s,%s,%s,'accepted',4,0.5,30000,'accepted at iteration 0')",
      (ids["amendment"], org, ids["plan"]))()

probe("a trace with no stated bound is refused",
      write("INSERT INTO clep.reasoning_trace (id, organization_id,"
            " evaluation_plan_id, state, max_iterations, budget, timeout_ms,"
            " stopped_because) VALUES (%s,%s,%s,'accepted',0,0.5,30000,'x')",
            (uuid.uuid4(), org, ids["plan"])))

probe("a trace that does not say why it stopped is refused",
      write("INSERT INTO clep.reasoning_trace (id, organization_id,"
            " evaluation_plan_id, state, max_iterations, budget, timeout_ms,"
            " stopped_because) VALUES (%s,%s,%s,'accepted',4,0.5,30000,'')",
            (uuid.uuid4(), org, ids["plan"])))

probe("a rejected attempt with neither critique nor error is refused",
      write("INSERT INTO clep.reasoning_attempt (id, organization_id,"
            " reasoning_trace_id, attempt_index, accepted, critique)"
            " VALUES (%s,%s,%s,0,false,'')",
            (uuid.uuid4(), org, ids["amendment"])))

probe("a reasoning trace is immutable",
      write("UPDATE clep.reasoning_trace SET state='failed' WHERE id=%s",
            (ids["amendment"],)))

bad = [r for r in results if not r[0]]
print(f"\nprobes: {len(results) - len(bad)}/{len(results)} behaved as required")

# =============================================================================
# The same claims again, as a superuser: no grant, no policy, only the trigger
# left to refuse.
# =============================================================================
print()


def trigger_probe(label, statement, params=(), *, expect_refusal=True):
    try:
        with psycopg.connect(MIGRATION, autocommit=True) as c:
            c.execute(statement, params)
        ok, detail = (not expect_refusal), "accepted"
    except Exception as exc:  # noqa: BLE001
        ok, detail = expect_refusal, str(exc).strip().splitlines()[0][:120]
    trigger_results.append((ok, label, detail))
    print(f"[{'OK  ' if ok else 'MISS'}] {label}: {detail}")


trigger_probe("judge_run UPDATE, as superuser",
              "UPDATE clep.judge_run SET latency_ms = 1 WHERE id = %s",
              (ids["judge_run"],))
trigger_probe("judge_run DELETE, as superuser",
              "DELETE FROM clep.judge_run WHERE id = %s", (ids["judge_run"],))
trigger_probe("judge_vote UPDATE, as superuser",
              "UPDATE clep.judge_vote SET score = 0.1 WHERE judge_run_id = %s",
              (ids["judge_run"],))
trigger_probe("consensus_result UPDATE, as superuser",
              "UPDATE clep.consensus_result SET disagreement = 0.1 WHERE id = %s",
              (ids["consensus"],))
trigger_probe("consensus_result DELETE, as superuser",
              "DELETE FROM clep.consensus_result WHERE id = %s",
              (ids["consensus"],))
trigger_probe("published judge_version UPDATE, as superuser",
              "UPDATE clep.judge_version SET rubric_digest = %s WHERE id = %s",
              (D["a"], ids["version"]))
trigger_probe("used judge_ensemble UPDATE, as superuser",
              "UPDATE clep.judge_ensemble SET agreement_threshold = 0.9"
              " WHERE id = %s", (ids["ensemble"],))
trigger_probe("new member into a used ensemble, as superuser",
              "INSERT INTO clep.judge_ensemble_member (id, organization_id,"
              " judge_ensemble_id, judge_version_id) VALUES (%s,%s,%s,%s)",
              (uuid.uuid4(), org, ids["ensemble"], ids["version3"]))
# The positive case, and the one that catches an over-eager trigger. The first
# version of this function raised a plpgsql error for every ensemble update
# whatsoever, which looked like correct enforcement from outside and would have
# made an uncalibrated threshold impossible to correct.
trigger_probe("an ensemble that has judged nothing CAN be corrected, as superuser",
              "UPDATE clep.judge_ensemble SET agreement_threshold = 0.3"
              " WHERE id = %s", (ids["unused"],), expect_refusal=False)
trigger_probe("reviewed escalation UPDATE, as superuser",
              "UPDATE clep.escalation SET review_outcome = 'changed' WHERE id = %s",
              (ids["escalation"],))
trigger_probe("accepted plan step INSERT, as superuser",
              "INSERT INTO clep.plan_step (id, organization_id,"
              " evaluation_plan_id, plan_digest, step_order, kind, subject)"
              " VALUES (%s,%s,%s,%s,7,'evaluate_gate','g')",
              (uuid.uuid4(), org, ids["plan"], D["a"]))
trigger_probe("accepted plan reopened, as superuser",
              "UPDATE clep.evaluation_plan SET state = 'draft' WHERE id = %s",
              (ids["plan"],))
trigger_probe("plan_amendment UPDATE, as superuser",
              "UPDATE clep.plan_amendment SET note = 'different' WHERE id = %s",
              (ids["amendment"],))
trigger_probe("reasoning_trace DELETE, as superuser",
              "DELETE FROM clep.reasoning_trace WHERE id = %s", (ids["amendment"],))

bad_triggers = [r for r in trigger_results if not r[0]]
print(f"\ntrigger probes: {len(trigger_results) - len(bad_triggers)}/"
      f"{len(trigger_results)} behaved as required with no grant in the way "
      f"(thirteen refusals and one permitted correction)")
sys.exit(1 if bad or bad_triggers else 0)
