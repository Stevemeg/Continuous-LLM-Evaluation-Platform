"""Analytics, against the database that holds the evidence.

A fake would report whatever it was told to, and the properties worth testing
here are properties of data: that a mean excludes samples that were never
scored, that a leaderboard cannot be produced without a benchmark, that a figure
over a cancelled run says so, and that none of it crosses a tenant boundary.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep.analytics.completeness import COMPLETE, INCOMPLETE, completeness_of
from clep.analytics.repository import AnalyticsError, AnalyticsRepository
from clep.db.session import tenant_session
from tests.conftest import requires_postgres
from tests.test_end_to_end import (examples_with_evidence,  # noqa: F401
                                   second_configuration)
from tests.test_regression import build_run, examples, _slug  # noqa: F401

pytestmark = [pytest.mark.integration, requires_postgres]

TEN = [Decimal("0.80"), Decimal("0.82"), Decimal("0.79"), Decimal("0.81"),
       Decimal("0.83"), Decimal("0.80"), Decimal("0.78"), Decimal("0.82"),
       Decimal("0.81"), Decimal("0.79")]
LATENCIES = [10, 20, 30, 40, 50, 60, 70, 80, 90, 1000]


# ==================================================== completeness, no database
def test_a_figure_over_whole_runs_and_scored_samples_is_complete():
    marked = completeness_of(contributing_runs=2, incomplete_runs=0,
                             observations=20, unresolved_observations=0)
    assert marked.state == COMPLETE
    assert marked.reason is None
    assert marked.is_complete


def test_an_unfinished_run_makes_the_figure_incomplete_and_says_which():
    marked = completeness_of(contributing_runs=3, incomplete_runs=1,
                             observations=20, unresolved_observations=0)
    assert marked.state == INCOMPLETE
    assert "1 of 3 contributing run(s) did not finish complete" in marked.reason


def test_unscored_samples_are_excluded_and_the_exclusion_is_reported():
    marked = completeness_of(contributing_runs=1, incomplete_runs=0,
                             observations=8, unresolved_observations=2)
    assert marked.state == INCOMPLETE
    assert "excluded rather than counted as zero" in marked.reason


def test_a_figure_with_no_observations_is_incomplete_not_healthy():
    """"We measured nothing" and "we measured and found nothing wrong" are the
    two answers REQ-F-08-4 keeps apart, and an empty view that reads as healthy
    is how the distinction gets lost outside the gate."""
    marked = completeness_of(contributing_runs=0, incomplete_runs=0,
                             observations=0, unresolved_observations=0)
    assert marked.state == INCOMPLETE
    assert "nothing behind this figure" in marked.reason


# ============================================================ REQ-F-11-1 trends
def test_a_trend_has_one_point_per_run_per_metric_oldest_first(
        migrated_database, seeded, examples):
    first = build_run(migrated_database, seeded, examples, TEN, key="tr-1")
    second = build_run(migrated_database, seeded, examples, TEN, key="tr-2")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        points = AnalyticsRepository(conn, seeded["organization"]).quality_trend(
            seeded["project"])
    assert [p.run_id for p in points] == [first, second]
    assert {p.metric_key for p in points} == {_slug(seeded)}
    assert points[0].observations == 10
    assert points[0].mean_score == sum(TEN) / Decimal(10)
    assert points[0].completeness.state == COMPLETE


def test_a_trend_point_names_the_run_and_the_observations_behind_it(
        migrated_database, seeded, examples):
    """REQ-F-11-6. A figure with a timestamp and no evidence is not traceable."""
    run_id = build_run(migrated_database, seeded, examples, TEN, key="tr-3")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        point = AnalyticsRepository(conn, seeded["organization"]).quality_trend(
            seeded["project"])[0]
    assert point.run_id == run_id
    assert point.observations == 10
    assert point.completeness.observations == 10


def test_a_sample_that_was_never_scored_lowers_the_count_and_marks_the_figure(
        migrated_database, seeded, examples):
    """REQ-F-08-5 and REQ-F-11-7 together: excluded, not zeroed, and said so."""
    scores = TEN[:8] + [None, None]
    build_run(migrated_database, seeded, examples, scores, key="tr-4")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        point = AnalyticsRepository(conn, seeded["organization"]).quality_trend(
            seeded["project"])[0]
    assert point.observations == 8
    assert point.unresolved == 2
    assert point.mean_score == sum(TEN[:8]) / Decimal(8)
    assert point.completeness.state == INCOMPLETE


def test_a_figure_over_a_cancelled_run_says_so(migrated_database, seeded,
                                               examples):
    build_run(migrated_database, seeded, examples, TEN, key="tr-5",
              completeness="cancelled")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        point = AnalyticsRepository(conn, seeded["organization"]).quality_trend(
            seeded["project"])[0]
    assert point.run_completeness == "cancelled"
    assert point.completeness.state == INCOMPLETE
    assert "did not finish complete" in point.completeness.reason


def test_an_approved_baseline_is_flagged_in_the_same_series(
        migrated_database, seeded, examples):
    """REQ-F-11-1's baseline-versus-candidate reading, without a second call."""
    from tests.test_regression import approved_baseline
    baseline_run = build_run(migrated_database, seeded, examples, TEN,
                             key="tr-b")
    approved_baseline(migrated_database, seeded, baseline_run)
    candidate_run = build_run(migrated_database, seeded, examples, TEN,
                              key="tr-c")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        points = AnalyticsRepository(conn, seeded["organization"]).quality_trend(
            seeded["project"])
    flags = {p.run_id: p.is_baseline for p in points}
    assert flags[baseline_run] is True
    assert flags[candidate_run] is False
    # And the counts are untouched by the baseline join.
    assert all(p.observations == 10 for p in points)


def test_a_trend_never_crosses_a_tenant_boundary(migrated_database, seeded,
                                                 second_organization, examples):
    build_run(migrated_database, seeded, examples, TEN, key="tr-iso")
    with tenant_session(migrated_database, second_organization) as conn:
        points = AnalyticsRepository(conn, second_organization).quality_trend(
            seeded["project"])
    assert points == []


# ======================================================= REQ-F-11-2 leaderboard
def test_a_leaderboard_without_a_benchmark_is_refused(migrated_database, seeded):
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = AnalyticsRepository(conn, seeded["organization"])
        with pytest.raises(AnalyticsError, match="global ranking"):
            repo.leaderboard(seeded["project"], suite_version_id=None)
        with pytest.raises(AnalyticsError):
            repo.leaderboard(seeded["project"], suite_version_id="")


def test_a_leaderboard_ranks_within_its_benchmark_and_names_its_runs(
        migrated_database, seeded, examples):
    first = build_run(migrated_database, seeded, examples, TEN, key="lb-1")
    second = build_run(migrated_database, seeded, examples,
                       [s - Decimal("0.2") for s in TEN], key="lb-2")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        entries = AnalyticsRepository(conn, seeded["organization"]).leaderboard(
            seeded["project"], suite_version_id=seeded["suite_version"])
    assert len(entries) == 1, "one model configuration was evaluated"
    entry = entries[0]
    assert entry.metric_key == _slug(seeded)
    assert entry.observations == 20
    assert sorted(entry.run_ids) == sorted([first, second])
    assert entry.model_identifier == "m"
    assert entry.provider_slug == "stub"


def test_a_leaderboard_over_another_benchmark_reports_nothing(
        migrated_database, seeded, examples):
    from clep.identity import new_ulid
    build_run(migrated_database, seeded, examples, TEN, key="lb-3")
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        entries = AnalyticsRepository(conn, seeded["organization"]).leaderboard(
            seeded["project"], suite_version_id=new_ulid())
    assert entries == []


# ======================================================= REQ-F-11-3 operational
def test_latency_reports_the_tail_not_only_the_middle(migrated_database, seeded,
                                                      examples):
    build_run(migrated_database, seeded, examples, TEN, key="op-1",
              latencies=LATENCIES, costs=[Decimal("0.01")] * 10)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        figures = AnalyticsRepository(conn, seeded["organization"]).operational(
            seeded["project"])
    latency = figures.model_latency_ms
    assert latency.measured == 10
    assert latency.minimum == 10
    assert latency.maximum == 1000
    # Discrete percentiles: every value is an observation that happened.
    assert latency.quantiles[Decimal("0.5")] in LATENCIES
    assert latency.quantiles[Decimal("0.95")] == 1000
    assert latency.quantiles[Decimal("0.95")] > latency.quantiles[Decimal("0.5")]


def test_cost_and_tokens_are_divided_by_tasks_that_succeeded(
        migrated_database, seeded, examples):
    """A run that failed half its calls must not look cheaper per task."""
    scores = TEN[:5] + [None] * 5
    build_run(migrated_database, seeded, examples, scores, key="op-2",
              latencies=LATENCIES, costs=[Decimal("0.02")] * 10)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        figures = AnalyticsRepository(conn, seeded["organization"]).operational(
            seeded["project"])
    assert figures.successful_tasks == 5
    # Only the successful samples' costs are summed.
    assert figures.cost_total == Decimal("0.10")
    assert figures.cost_per_successful_task == Decimal("0.020000000")
    assert figures.tokens_per_successful_task == Decimal("15.000")
    assert figures.completeness.state == INCOMPLETE


def test_evaluator_latency_is_reported_apart_from_model_latency(
        migrated_database, seeded, examples):
    build_run(migrated_database, seeded, examples, TEN, key="op-3",
              latencies=LATENCIES, durations=[5] * 10)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        figures = AnalyticsRepository(conn, seeded["organization"]).operational(
            seeded["project"])
    assert figures.evaluator_latency_ms.measured == 10
    assert figures.evaluator_latency_ms.maximum == 5
    assert figures.model_latency_ms.maximum == 1000


def test_operational_figures_over_no_run_are_empty_and_marked(
        migrated_database, seeded):
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        figures = AnalyticsRepository(conn, seeded["organization"]).operational(
            seeded["project"])
    assert figures.successful_tasks == 0
    assert figures.cost_per_successful_task is None
    assert figures.completeness.state == INCOMPLETE


# ===================================================== REQ-F-11-4 and REQ-F-11-5
def test_judge_and_agent_analytics_read_a_real_run(
        migrated_database, seeded, examples_with_evidence, second_configuration):
    """One run through the harness, then both analytics surfaces over it."""
    from tests.test_end_to_end import (build_examples, execute_run,
                                       published_judges)
    judges, judge_ids, ensemble_id = published_judges(
        migrated_database, seeded, second_configuration)
    run_examples = build_examples(examples_with_evidence)
    execute_run(migrated_database, seeded, run_examples, key="an-1",
                judges=judges, judge_ids=judge_ids, ensemble_id=ensemble_id,
                judge_replies={"model-alpha": "SCORE: 0.9",
                               "model-beta": "SCORE: 0.8"})

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        repo = AnalyticsRepository(conn, seeded["organization"])
        judge_figures = repo.judge_analytics(seeded["project"])
        agent_figures = repo.agent_analytics(seeded["project"])

    # Judges: six judgements over three samples, all scored, all agreed.
    assert judge_figures.judgements == 6
    assert judge_figures.scored == 6
    assert judge_figures.consensus_results == 3
    assert judge_figures.agreed == 3
    assert judge_figures.escalated == 0
    assert judge_figures.disagreement_measured == 3
    assert judge_figures.mean_disagreement is not None
    assert judge_figures.calibration, "no per-judge calibration was reported"

    # Agents: three steps per sample, one of which fails, and no loop.
    assert agent_figures.samples_with_trajectory == 3
    assert agent_figures.tool_calls == 9
    assert agent_figures.failed_tool_calls == 3
    assert agent_figures.tool_success_rate == Decimal("0.666666667")
    assert agent_figures.samples_with_loops == 0
    assert agent_figures.samples_with_retries == 0
    assert agent_figures.truncated_trajectories == 0
    assert {t["tool"] for t in agent_figures.by_tool} == {"search", "open",
                                                          "answer"}


def test_an_escalating_panel_shows_up_as_disagreement_not_as_agreement(
        migrated_database, seeded, examples_with_evidence, second_configuration):
    from tests.test_end_to_end import (build_examples, execute_run,
                                       published_judges)
    judges, judge_ids, ensemble_id = published_judges(
        migrated_database, seeded, second_configuration)
    execute_run(migrated_database, seeded, build_examples(examples_with_evidence),
                key="an-2", judges=judges, judge_ids=judge_ids,
                ensemble_id=ensemble_id,
                judge_replies={"model-alpha": "SCORE: 0.1",
                               "model-beta": "SCORE: 0.9"})
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        figures = AnalyticsRepository(conn, seeded["organization"]
                                      ).judge_analytics(seeded["project"])
    assert figures.agreed == 0
    assert figures.escalated == 3
    assert figures.escalation_reasons.get("disagreement_above_threshold") == 3


def test_a_loop_is_an_identical_call_repeated_back_to_back(
        migrated_database, seeded, examples_with_evidence):
    """The definition the agent evaluator uses, applied to the stored steps."""
    from clep.evaluators.trajectory import ToolCall, ingest
    from clep.orchestration.runner import Example
    from tests.test_end_to_end import execute_run

    looping = ingest([ToolCall(0, "search", {"q": "x"}, "hit"),
                      ToolCall(1, "search", {"q": "x"}, "hit"),
                      ToolCall(2, "answer", {"text": "Paris"}, "ok")],
                     final_answer="Paris is the capital of France.")
    retrying = ingest([ToolCall(0, "search", {"q": "y"}, "hit"),
                       ToolCall(1, "answer", {"text": "Paris"}, "ok"),
                       ToolCall(2, "search", {"q": "y"}, "hit")],
                      final_answer="Paris is the capital of France.")
    plain = ingest([ToolCall(0, "search", {"q": "z"}, "hit"),
                    ToolCall(1, "answer", {"text": "Paris"}, "ok")],
                   final_answer="Paris is the capital of France.")
    trajectories = [looping, retrying, plain]
    run_examples = [
        Example(id=example_id, prompt="What is the capital of France?",
                expected="Paris", trajectory=trajectories[index],
                tool_schemas={"search": {"required": ["q"],
                                         "properties": {"q": {}}},
                              "answer": {"required": ["text"],
                                         "properties": {"text": {}}}},
                expected_tools=("search", "answer"))
        for index, example_id in enumerate(examples_with_evidence)]
    execute_run(migrated_database, seeded, run_examples, key="an-loop")

    with tenant_session(migrated_database, seeded["organization"]) as conn:
        figures = AnalyticsRepository(conn, seeded["organization"]
                                      ).agent_analytics(seeded["project"])
    # The looping sample repeated a call back to back; the retrying one
    # repeated a call but not consecutively. Both count as retries; only the
    # first is a loop.
    assert figures.samples_with_loops == 1
    assert figures.samples_with_retries == 2


def test_agent_analytics_never_crosses_a_tenant_boundary(
        migrated_database, seeded, second_organization, examples_with_evidence):
    from tests.test_end_to_end import build_examples, execute_run
    execute_run(migrated_database, seeded, build_examples(examples_with_evidence),
                key="an-iso")
    with tenant_session(migrated_database, second_organization) as conn:
        figures = AnalyticsRepository(conn, second_organization).agent_analytics(
            seeded["project"])
    assert figures.tool_calls == 0
    assert figures.run_ids == ()


