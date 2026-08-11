"""The CLI a CI job runs, and the exit code it hands back.

The exit code is the whole interface to a pipeline, so most of what matters
here is which outcomes block and which do not. The rest is that the CLI decides
nothing: it calls the same service the HTTP API calls and renders the answer.
"""
from __future__ import annotations

import io
import json
from decimal import Decimal

import pytest

from clep.cli import exit_codes
from clep.cli.main import main
from clep.db.session import tenant_session
from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid
from tests.conftest import requires_postgres
from tests.test_end_to_end import (approved_baseline_from, build_examples,  # noqa: F401
                                   examples_with_evidence, execute_run,
                                   published_policy, _metric_key_of)

pytestmark = [pytest.mark.integration, requires_postgres]


def run_cli(argv, dsn, organization):
    out, err = io.StringIO(), io.StringIO()
    code = main(["--dsn", dsn, "--organization", organization, *argv],
                out=out, err=err)
    return code, out.getvalue(), err.getvalue()


# ------------------------------------------------------------- the mapping
def test_only_pass_and_warning_let_a_pipeline_through():
    """The policy chose `warning` so it would not block. Everything else does."""
    assert exit_codes.for_outcome("pass") == 0
    assert exit_codes.for_outcome("warning") == 0
    for blocking in ("hard_fail", "approval_required", "insufficient_evidence",
                     "not_comparable"):
        assert exit_codes.for_outcome(blocking) != 0
        assert exit_codes.blocks(blocking)


def test_an_abstention_blocks_rather_than_passing():
    """The failure this whole product exists to prevent: shipping on 'we could
    not tell'. A gate that exited 0 here would be green within a week and
    unread within two."""
    assert exit_codes.for_outcome("insufficient_evidence") != 0
    assert exit_codes.for_outcome("not_comparable") != 0


def test_every_blocking_outcome_is_distinguishable():
    """So a pipeline can route an approval to a human rather than treat it as a
    defect."""
    blocking = {o: exit_codes.for_outcome(o)
                for o in ("hard_fail", "approval_required",
                          "insufficient_evidence", "not_comparable")}
    assert len(set(blocking.values())) == len(blocking)


def test_an_unknown_outcome_is_a_platform_failure_not_a_pass():
    """A new gate outcome must not ship everything until someone notices."""
    assert exit_codes.for_outcome("invented_later") == exit_codes.PLATFORM_FAILURE


def test_the_contracts_outcomes_are_all_mapped():
    from clep.api import contract
    declared = set(contract.enum_of("GateOutcome"))
    assert declared <= set(exit_codes.BY_OUTCOME), \
        f"unmapped gate outcomes: {sorted(declared - set(exit_codes.BY_OUTCOME))}"


def test_there_is_no_flag_that_turns_an_abstention_into_a_pass():
    """A team that wants to proceed anyway records a policy exception, which is
    audited, expires and names who decided."""
    import inspect
    from clep.cli import main as module
    source = inspect.getsource(module)
    for escape in ("--ignore-abstentions", "--allow-abstain", "--force",
                   "--no-fail"):
        assert escape not in source


# ---------------------------------------------------------------- behaviour
def test_the_cli_refuses_without_a_tenant_or_a_dsn():
    out, err = io.StringIO(), io.StringIO()
    assert main(["--dsn", "", "gate", "--project", new_ulid(), "--run",
                 new_ulid(), "--policy", new_ulid(), "--baseline", new_ulid()],
                out=out, err=err) == exit_codes.PLATFORM_FAILURE
    assert "runtime DSN" in err.getvalue()


def test_a_malformed_identifier_is_refused_before_anything_is_queried(
        migrated_database, seeded):
    code, _, err = run_cli(
        ["gate", "--project", seeded["project"], "--run", "not-a-ulid",
         "--policy", new_ulid(), "--baseline", new_ulid()],
        migrated_database, seeded["organization"])
    assert code == exit_codes.PLATFORM_FAILURE
    assert "--run is not a well-formed identifier" in err


def test_a_gate_run_reports_and_exits_on_the_outcome(
        migrated_database, seeded, examples_with_evidence):
    examples = build_examples(examples_with_evidence)
    baseline_run, _ = execute_run(migrated_database, seeded, examples,
                                  key="cli-baseline")
    candidate_run, _ = execute_run(migrated_database, seeded, examples,
                                   key="cli-candidate")
    baseline_id = approved_baseline_from(migrated_database, seeded, baseline_run)
    policy_version_id = published_policy(migrated_database, seeded,
                                         _metric_key_of(migrated_database, seeded))

    code, out, err = run_cli(
        ["gate", "--project", seeded["project"], "--run", candidate_run,
         "--policy", policy_version_id, "--baseline", baseline_id],
        migrated_database, seeded["organization"])
    assert err == ""
    assert "gate:" in out
    assert "evidence: sha256:" in out
    # Two identical runs: whatever the outcome, the code must agree with it.
    outcome = out.splitlines()[0].split(": ", 1)[1]
    assert code == exit_codes.for_outcome(outcome)


def test_json_output_is_the_decision_a_machine_can_read(
        migrated_database, seeded, examples_with_evidence):
    examples = build_examples(examples_with_evidence)
    baseline_run, _ = execute_run(migrated_database, seeded, examples,
                                  key="cli-json-b")
    candidate_run, _ = execute_run(migrated_database, seeded, examples,
                                   key="cli-json-c")
    baseline_id = approved_baseline_from(migrated_database, seeded, baseline_run)
    policy_version_id = published_policy(migrated_database, seeded,
                                         _metric_key_of(migrated_database, seeded))
    code, out, _ = run_cli(
        ["--format", "json", "gate", "--project", seeded["project"],
         "--run", candidate_run, "--policy", policy_version_id,
         "--baseline", baseline_id],
        migrated_database, seeded["organization"])
    body = json.loads(out)
    assert body["gateEvidenceDigest"].startswith("sha256:")
    assert code == exit_codes.for_outcome(body["evaluatedOutcome"])

    # And the decision is fetchable afterwards, by a later job.
    again, fetched, _ = run_cli(
        ["--format", "json", "decision", "--id", body["id"]],
        migrated_database, seeded["organization"])
    assert json.loads(fetched)["gateEvidenceDigest"] == body["gateEvidenceDigest"]
    assert again == code


def test_markdown_output_is_the_report_a_person_reads(
        migrated_database, seeded, examples_with_evidence):
    examples = build_examples(examples_with_evidence)
    baseline_run, _ = execute_run(migrated_database, seeded, examples,
                                  key="cli-md-b")
    candidate_run, _ = execute_run(migrated_database, seeded, examples,
                                   key="cli-md-c")
    baseline_id = approved_baseline_from(migrated_database, seeded, baseline_run)
    policy_version_id = published_policy(migrated_database, seeded,
                                         _metric_key_of(migrated_database, seeded))
    _, out, _ = run_cli(
        ["--format", "markdown", "gate", "--project", seeded["project"],
         "--run", candidate_run, "--policy", policy_version_id,
         "--baseline", baseline_id],
        migrated_database, seeded["organization"])
    assert out.lstrip().startswith("#")


def test_an_unknown_decision_is_a_platform_failure_not_a_silent_pass(
        migrated_database, seeded):
    code, _, err = run_cli(["decision", "--id", new_ulid()],
                           migrated_database, seeded["organization"])
    assert code == exit_codes.PLATFORM_FAILURE
    assert "no such decision" in err


def test_analysis_reports_the_evidence_for_one_sample(
        migrated_database, seeded, examples_with_evidence):
    examples = build_examples(examples_with_evidence)
    run_id, _ = execute_run(migrated_database, seeded, examples, key="cli-an")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        sample = uuid_to_ulid(conn.execute(
            "SELECT id FROM clep.run_sample WHERE run_id = %s "
            "ORDER BY sample_index DESC LIMIT 1",
            (ulid_to_uuid(run_id),)).fetchone()[0])

    code, out, _ = run_cli(["analysis", "--sample", sample],
                           migrated_database, seeded["organization"])
    assert code == exit_codes.PASS
    assert "retrieved: 2 passage(s), 1 cited" in out
    assert "trajectory: 3 step(s), truncated=False" in out
    # The third example was labelled as needing a passage retrieval never
    # returned, and the CLI says which.
    assert "required but missing:" in out
    assert "-missing" in out


def test_the_cli_changes_nothing_in_production():
    """REQ-F-10-3, structurally: there is no subcommand that could."""
    from clep.cli.main import build_parser
    parser = build_parser()
    actions = [a for a in parser._subparsers._group_actions[0].choices]
    assert sorted(actions) == ["analysis", "decision", "gate"]
    for forbidden in ("deploy", "rollback", "promote", "approve", "delete"):
        assert forbidden not in actions


# ------------------------------------------------------- the installed script
def test_the_distribution_declares_the_console_script_a_pipeline_invokes():
    """`clep`, not `python -m clep.cli.main`.

    A CI job runs a command on its PATH. This asserts the declaration that puts
    it there against the *installed* metadata rather than against the source, so
    a `[project.scripts]` entry deleted from `pyproject.toml` fails here.
    `docs/evidence/phase-11/ci_execution.py` proves the other half — that the
    declaration produces a working executable from a clean checkout in an
    isolated environment.
    """
    from importlib.metadata import entry_points
    declared = {e.name: e.value
                for e in entry_points(group="console_scripts")
                if e.name == "clep"}
    assert declared == {"clep": "clep.cli.main:main"}


def test_the_declared_entry_point_loads_and_is_the_callable_it_names():
    from importlib.metadata import entry_points
    entry = next(e for e in entry_points(group="console_scripts")
                 if e.name == "clep")
    loaded = entry.load()
    assert loaded is main
    assert callable(loaded)
