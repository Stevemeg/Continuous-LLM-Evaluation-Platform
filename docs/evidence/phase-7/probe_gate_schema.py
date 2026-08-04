"""Probe schema 07 against a real database. Assume nothing; make it refuse.

Two passes. The first writes as the runtime role, which is how the application
writes, and exercises the constraints and the grants together. The second writes
as a superuser, where no grant and no row-level-security policy applies, so the
only thing left that can refuse is the trigger.

The second pass exists because the first is not evidence for what schema 07
claims. Eight of its refusals read `permission denied for table`, which proves
the grants and says nothing about the triggers the file says will hold "even if a
grant were added by mistake".

Usage: python docs/evidence/phase-7/probe_gate_schema.py
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
       ("project", "dataset", "dataset_version", "suite", "suite_version",
        "run", "run2", "policy", "version", "criterion", "baseline", "decision")}

with psycopg.connect(MIGRATION, autocommit=True) as c:
    c.execute("INSERT INTO clep.organization (id, slug, display_name) VALUES (%s,%s,%s)",
              (org, f"org-{org.hex[:8]}", "Probe"))
    c.execute("INSERT INTO clep.project (id, organization_id, slug, display_name)"
              " VALUES (%s,%s,'p','P')", (ids["project"], org))
    c.execute("INSERT INTO clep.dataset (id, organization_id, project_id, slug,"
              " display_name) VALUES (%s,%s,%s,'d','D')",
              (ids["dataset"], org, ids["project"]))
    c.execute("INSERT INTO clep.dataset_version (id, organization_id, dataset_id,"
              " version_number, content_digest, schema_ref, state)"
              " VALUES (%s,%s,%s,1,%s,'schema://x/v1','draft')",
              (ids["dataset_version"], org, ids["dataset"], "sha256:" + "a" * 64))
    c.execute("INSERT INTO clep.benchmark_suite (id, organization_id, project_id, slug,"
              " display_name, owner_actor_id) VALUES (%s,%s,%s,'s','S',%s)",
              (ids["suite"], org, ids["project"], uuid.uuid4()))
    c.execute("INSERT INTO clep.suite_version (id, organization_id, benchmark_suite_id,"
              " version_number, content_digest, owner_actor_id)"
              " VALUES (%s,%s,%s,1,%s,%s)",
              (ids["suite_version"], org, ids["suite"], "sha256:" + "b" * 64,
               uuid.uuid4()))
    for key in ("run", "run2"):
        c.execute("INSERT INTO clep.run (id, organization_id, project_id,"
                  " dataset_version_id, suite_version_id, identity_digest,"
                  " execution_state, completeness, reproducibility, integration_tier,"
                  " idempotency_key, completed_at)"
                  " VALUES (%s,%s,%s,%s,%s,%s,'terminal','complete',"
                  "'reproducible','full',%s, now())",
                  (ids[key], org, ids["project"], ids["dataset_version"],
                   ids["suite_version"], "sha256:" + "c" * 64, f"probe-{key}"))

results = []


def probe(label, fn, *, expect_refusal=True):
    try:
        fn()
        ok = not expect_refusal
        detail = "accepted"
    except Exception as exc:  # noqa: BLE001 - the message is the evidence
        ok = expect_refusal
        detail = str(exc).strip().splitlines()[0][:120]
    results.append((ok, label, detail))
    print(f"[{'OK  ' if ok else 'MISS'}] {label}: {detail}")


def rt():
    return tenant_session(RUNTIME, str(org))


def seed_policy_and_decision():
    with rt() as conn:
        conn.execute("INSERT INTO clep.gate_policy (id, organization_id, project_id,"
                     " slug, display_name) VALUES (%s,%s,%s,'gp','GP')",
                     (ids["policy"], org, ids["project"]))
        conn.execute("INSERT INTO clep.gate_policy_version (id, organization_id,"
                     " gate_policy_id, version_number, content_digest, state,"
                     " confidence_level, resample_count, bootstrap_seed, created_by)"
                     " VALUES (%s,%s,%s,1,%s,'draft',0.95,200,20260804,%s)",
                     (ids["version"], org, ids["policy"], "sha256:" + "d" * 64,
                      uuid.uuid4()))
        conn.execute("INSERT INTO clep.gate_criterion (id, organization_id,"
                     " gate_policy_version_id, metric_key, dimension, source,"
                     " direction, precision_threshold, on_regression,"
                     " on_insufficient_evidence, on_not_comparable)"
                     " VALUES (%s,%s,%s,'exact_match','quality','evaluator',"
                     "'higher_is_better',0.05,'hard_fail','warning','hard_fail')",
                     (ids["criterion"], org, ids["version"]))
        conn.execute("INSERT INTO clep.baseline (id, organization_id, project_id,"
                     " run_id, suite_version_id, dataset_version_id, state,"
                     " identity_digest, created_by, approved_by, approved_at)"
                     " VALUES (%s,%s,%s,%s,%s,%s,'approved',%s,%s,%s,now())",
                     (ids["baseline"], org, ids["project"], ids["run"],
                      ids["suite_version"], ids["dataset_version"],
                      "sha256:" + "c" * 64, uuid.uuid4(), uuid.uuid4()))


seed_policy_and_decision()
print("seeded")

# --- the policy version freezes on publish -----------------------------------
with rt() as conn:
    conn.execute("UPDATE clep.gate_policy_version SET state='published',"
                 " published_at=now() WHERE id=%s", (ids["version"],))


def upd_version():
    with rt() as conn:
        conn.execute("UPDATE clep.gate_policy_version SET resample_count=1 WHERE id=%s",
                     (ids["version"],))


def del_version():
    with rt() as conn:
        conn.execute("DELETE FROM clep.gate_policy_version WHERE id=%s",
                     (ids["version"],))


def upd_criterion():
    with rt() as conn:
        conn.execute("UPDATE clep.gate_criterion SET precision_threshold=99 WHERE id=%s",
                     (ids["criterion"],))


def del_criterion():
    with rt() as conn:
        conn.execute("DELETE FROM clep.gate_criterion WHERE id=%s", (ids["criterion"],))


probe("a published policy version cannot be updated", upd_version)
probe("a published policy version cannot be deleted", del_version)
probe("a criterion of a published version cannot be updated", upd_criterion)
probe("a criterion of a published version cannot be deleted", del_criterion)

# --- the decision is audit-class ---------------------------------------------
with rt() as conn:
    conn.execute("INSERT INTO clep.gate_decision (id, organization_id, project_id,"
                 " candidate_run_id, baseline_id, gate_policy_version_id,"
                 " evaluated_outcome, statistical_method_version, evidence_digest,"
                 " decided_by) VALUES (%s,%s,%s,%s,%s,%s,'hard_fail',"
                 "'paired-bootstrap-percentile/1',%s,%s)",
                 (ids["decision"], org, ids["project"], ids["run2"], ids["baseline"],
                  ids["version"], "sha256:" + "e" * 64, uuid.uuid4()))
    comparison_id = uuid.uuid4()
    conn.execute("INSERT INTO clep.comparison (id, organization_id, gate_decision_id,"
                 " metric_key, result_kind, evaluator_version_id, classification,"
                 " sample_size, statistical_method_version, mean_difference,"
                 " interval_lower, interval_upper, confidence_level)"
                 " VALUES (%s,%s,%s,'exact_match','operational',NULL,'regression',"
                 "40,'paired-bootstrap-percentile/1',-0.15,-0.2,-0.1,0.95)",
                 (comparison_id, org, ids["decision"]))


def upd_decision():
    with rt() as conn:
        conn.execute("UPDATE clep.gate_decision SET evaluated_outcome='pass'"
                     " WHERE id=%s", (ids["decision"],))


def del_decision():
    with rt() as conn:
        conn.execute("DELETE FROM clep.gate_decision WHERE id=%s", (ids["decision"],))


def upd_comparison():
    with rt() as conn:
        conn.execute("UPDATE clep.comparison SET classification='no_change'"
                     " WHERE id=%s", (comparison_id,))


def del_comparison():
    with rt() as conn:
        conn.execute("DELETE FROM clep.comparison WHERE id=%s", (comparison_id,))


probe("a gate decision cannot be updated", upd_decision)
probe("a gate decision cannot be deleted", del_decision)
probe("a comparison cannot be updated", upd_comparison)
probe("a comparison cannot be deleted", del_comparison)


# --- a policy version cited by a decision is frozen even as a draft ----------
def upd_cited_draft():
    v2 = uuid.uuid4()
    with rt() as conn:
        conn.execute("INSERT INTO clep.gate_policy_version (id, organization_id,"
                     " gate_policy_id, version_number, content_digest, state,"
                     " confidence_level, resample_count, bootstrap_seed, created_by)"
                     " VALUES (%s,%s,%s,2,%s,'draft',0.95,200,1,%s)",
                     (v2, org, ids["policy"], "sha256:" + "f" * 64, uuid.uuid4()))
        d2 = uuid.uuid4()
        conn.execute("INSERT INTO clep.gate_decision (id, organization_id, project_id,"
                     " candidate_run_id, baseline_id, gate_policy_version_id,"
                     " evaluated_outcome, statistical_method_version, evidence_digest,"
                     " decided_by) VALUES (%s,%s,%s,%s,%s,%s,'pass',"
                     "'paired-bootstrap-percentile/1',%s,%s)",
                     (d2, org, ids["project"], ids["run"], ids["baseline"], v2,
                      "sha256:" + "a" * 64, uuid.uuid4()))
        conn.execute("UPDATE clep.gate_policy_version SET resample_count=1 WHERE id=%s",
                     (v2,))


probe("a draft policy version already cited by a decision is frozen", upd_cited_draft)


# --- constraint probes --------------------------------------------------------
def second_approved_baseline():
    with rt() as conn:
        conn.execute("INSERT INTO clep.baseline (id, organization_id, project_id,"
                     " run_id, suite_version_id, dataset_version_id, state,"
                     " identity_digest, created_by, approved_by, approved_at)"
                     " VALUES (%s,%s,%s,%s,%s,%s,'approved',%s,%s,%s,now())",
                     (uuid.uuid4(), org, ids["project"], ids["run2"],
                      ids["suite_version"], ids["dataset_version"],
                      "sha256:" + "c" * 64, uuid.uuid4(), uuid.uuid4()))


def approved_without_approver():
    with rt() as conn:
        conn.execute("INSERT INTO clep.baseline (id, organization_id, project_id,"
                     " run_id, suite_version_id, dataset_version_id, state,"
                     " identity_digest, created_by)"
                     " VALUES (%s,%s,%s,%s,%s,%s,'approved',%s,%s)",
                     (uuid.uuid4(), org, ids["project"], ids["run2"],
                      ids["suite_version"], ids["dataset_version"],
                      "sha256:" + "c" * 64, uuid.uuid4()))


def abstain_without_reason():
    with rt() as conn:
        conn.execute("INSERT INTO clep.comparison (id, organization_id,"
                     " gate_decision_id, metric_key, result_kind, classification,"
                     " sample_size, statistical_method_version)"
                     " VALUES (%s,%s,%s,'m2','operational','insufficient_evidence',"
                     "3,'paired-bootstrap-percentile/1')",
                     (uuid.uuid4(), org, ids["decision"]))


def half_an_interval():
    with rt() as conn:
        conn.execute("INSERT INTO clep.comparison (id, organization_id,"
                     " gate_decision_id, metric_key, result_kind, classification,"
                     " sample_size, statistical_method_version, interval_lower)"
                     " VALUES (%s,%s,%s,'m3','operational','no_change',"
                     "40,'paired-bootstrap-percentile/1',-0.1)",
                     (uuid.uuid4(), org, ids["decision"]))


def evaluator_comparison_without_a_version():
    with rt() as conn:
        conn.execute("INSERT INTO clep.comparison (id, organization_id,"
                     " gate_decision_id, metric_key, result_kind, classification,"
                     " sample_size, statistical_method_version)"
                     " VALUES (%s,%s,%s,'m4','deterministic_evaluator','no_change',"
                     "40,'paired-bootstrap-percentile/1')",
                     (uuid.uuid4(), org, ids["decision"]))


def decision_without_baseline():
    with rt() as conn:
        conn.execute("INSERT INTO clep.gate_decision (id, organization_id, project_id,"
                     " candidate_run_id, gate_policy_version_id, evaluated_outcome,"
                     " statistical_method_version, evidence_digest, decided_by)"
                     " VALUES (%s,%s,%s,%s,%s,'hard_fail',"
                     "'paired-bootstrap-percentile/1',%s,%s)",
                     (uuid.uuid4(), org, ids["project"], ids["run"], ids["version"],
                      "sha256:" + "a" * 64, uuid.uuid4()))


def thin_justification():
    with rt() as conn:
        conn.execute("INSERT INTO clep.policy_exception (id, organization_id,"
                     " gate_decision_id, actor_id, justification, expires_at)"
                     " VALUES (%s,%s,%s,%s,'ok', now() + interval '1 day')",
                     (uuid.uuid4(), org, ids["decision"], uuid.uuid4()))


def expiry_in_the_past():
    with rt() as conn:
        conn.execute("INSERT INTO clep.policy_exception (id, organization_id,"
                     " gate_decision_id, actor_id, justification, expires_at)"
                     " VALUES (%s,%s,%s,%s,"
                     "'a justification long enough to be a justification',"
                     " now() - interval '1 day')",
                     (uuid.uuid4(), org, ids["decision"], uuid.uuid4()))


def abstention_on_a_classified_comparison():
    with rt() as conn:
        conn.execute("INSERT INTO clep.comparison (id, organization_id,"
                     " gate_decision_id, metric_key, result_kind, classification,"
                     " sample_size, statistical_method_version, abstention_reason)"
                     " VALUES (%s,%s,%s,'m5','operational','regression',"
                     "40,'paired-bootstrap-percentile/1','because')",
                     (uuid.uuid4(), org, ids["decision"]))


probe("a second approved baseline for one scope is refused", second_approved_baseline)
probe("an approved baseline with no approver is refused", approved_without_approver)
probe("an abstention with no reason is refused", abstain_without_reason)
probe("half an interval is refused", half_an_interval)
probe("an evaluator comparison naming no version is refused",
      evaluator_comparison_without_a_version)
probe("a quality verdict with no baseline is refused", decision_without_baseline)
probe("a two-character justification is refused", thin_justification)
probe("an exception that expired before it existed is refused", expiry_in_the_past)
probe("a classified comparison carrying an abstention reason is refused",
      abstention_on_a_classified_comparison)


# --- and the things that must WORK -------------------------------------------
def valid_exception():
    with rt() as conn:
        conn.execute("INSERT INTO clep.policy_exception (id, organization_id,"
                     " gate_decision_id, actor_id, justification, expires_at)"
                     " VALUES (%s,%s,%s,%s,"
                     "'release blocked by a known flaky judge, ticket QA-1187',"
                     " now() + interval '7 days')",
                     (uuid.uuid4(), org, ids["decision"], uuid.uuid4()))


def not_comparable_without_baseline():
    with rt() as conn:
        conn.execute("INSERT INTO clep.gate_decision (id, organization_id, project_id,"
                     " candidate_run_id, gate_policy_version_id, evaluated_outcome,"
                     " statistical_method_version, evidence_digest, decided_by)"
                     " VALUES (%s,%s,%s,%s,%s,'not_comparable',"
                     "'paired-bootstrap-percentile/1',%s,%s)",
                     (uuid.uuid4(), org, ids["project"], ids["run"], ids["version"],
                      "sha256:" + "a" * 64, uuid.uuid4()))


probe("a well-formed exception is accepted", valid_exception, expect_refusal=False)
probe("a not-comparable decision needs no baseline", not_comparable_without_baseline,
      expect_refusal=False)

bad = [r for r in results if not r[0]]
print(f"\nprobes: {len(results) - len(bad)}/{len(results)} behaved as required")

# =============================================================================
# The same claims again, as a superuser: no grant, no policy, only
# the trigger left to refuse.
# =============================================================================
print()
trigger_results = []


def trigger_probe(label, sql, params=()):
    try:
        with psycopg.connect(MIGRATION, autocommit=True) as c:
            c.execute(sql, params)
        ok, detail = False, "ACCEPTED — the trigger did not fire"
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).strip().splitlines()[0][:110]
        ok = "restrict_violation" in detail or "immutable" in detail \
            or "audit-class" in detail or "REQ-" in detail
    trigger_results.append((ok, label, detail))
    print(f"[{'OK  ' if ok else 'MISS'}] {label}: {detail}")


with psycopg.connect(MIGRATION, autocommit=True) as c:
    decision = c.execute("SELECT id FROM clep.gate_decision LIMIT 1").fetchone()[0]
    comparison = c.execute("SELECT id FROM clep.comparison LIMIT 1").fetchone()[0]
    exception_id = c.execute("SELECT id FROM clep.policy_exception LIMIT 1").fetchone()[0]
    criterion = c.execute("SELECT id FROM clep.gate_criterion LIMIT 1").fetchone()[0]
    version = c.execute("SELECT id FROM clep.gate_policy_version"
                        " WHERE state='published' LIMIT 1").fetchone()[0]

trigger_probe("gate_decision UPDATE, as superuser",
      "UPDATE clep.gate_decision SET evaluated_outcome='pass' WHERE id=%s", (decision,))
trigger_probe("gate_decision DELETE, as superuser",
      "DELETE FROM clep.gate_decision WHERE id=%s", (decision,))
trigger_probe("comparison UPDATE, as superuser",
      "UPDATE clep.comparison SET classification='no_change' WHERE id=%s", (comparison,))
trigger_probe("comparison DELETE, as superuser",
      "DELETE FROM clep.comparison WHERE id=%s", (comparison,))
trigger_probe("policy_exception UPDATE, as superuser",
      "UPDATE clep.policy_exception SET justification='rewritten after the fact,"
      " which is the whole problem' WHERE id=%s", (exception_id,))
trigger_probe("policy_exception DELETE, as superuser",
      "DELETE FROM clep.policy_exception WHERE id=%s", (exception_id,))
trigger_probe("gate_criterion UPDATE on a published version, as superuser",
      "UPDATE clep.gate_criterion SET precision_threshold=99 WHERE id=%s", (criterion,))
trigger_probe("gate_criterion DELETE on a published version, as superuser",
      "DELETE FROM clep.gate_criterion WHERE id=%s", (criterion,))
trigger_probe("gate_policy_version DELETE when published, as superuser",
      "DELETE FROM clep.gate_policy_version WHERE id=%s", (version,))


bad_triggers = [r for r in trigger_results if not r[0]]
print(f"\ntrigger probes: {len(trigger_results) - len(bad_triggers)}/{len(trigger_results)} refused by the store, with no grant in the way")
sys.exit(1 if bad or bad_triggers else 0)
