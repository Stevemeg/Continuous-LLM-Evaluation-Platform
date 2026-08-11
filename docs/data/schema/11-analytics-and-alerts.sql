-- =============================================================================
-- 11 — Scheduled execution, per-sample model latency, and alert rules
--
-- Phase 11. ADDs only, and deliberately small: the analytics this phase delivers
-- are DERIVED on read from the rows that already exist, never stored as
-- aggregates. `REQ-F-11-6` requires every reported figure to be traceable to the
-- run and the samples that produced it, and a stored aggregate is a figure whose
-- provenance is a previous computation rather than the data.
--
-- What is added here is only what cannot be derived.
--
--   1. A schedule that cannot name its candidates cannot create a run. The
--      contract's `EvaluationScheduleRequest` has taken `candidates` since
--      Phase 3; there was nowhere to put them, so a schedule was a record of an
--      intention rather than something executable.
--   2. Why a schedule runs. `release_observation` admits only `post_deployment`
--      and `canary`, so a schedule that observes a live system has to say so —
--      otherwise REQ-F-10-2 is unreachable through the scheduler and the
--      constraint reads as an accident.
--   3. Model-call latency, which nothing recorded. `REQ-F-11-3` asks for latency
--      distributions including tail latency; `evaluator_outcome.duration_ms` is
--      evaluation latency and answers a different question.
--   4. Alert rules and their firings. A rule is configuration and a firing is a
--      historical fact; neither is derivable from evaluation results.
--
-- An alert follows `REQ-F-10-3` exactly as a release observation does: there is
-- no column naming a delivery channel, an endpoint, or an acknowledgement. A
-- firing is a record that a condition held.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- evaluation_schedule.trigger_kind (REQ-F-10-1, REQ-F-10-2)
--
-- A subset of `RunTrigger`, not the whole vocabulary: a standing order cannot be
-- `manual`, and a pull request is not a schedule. Defaulted to `schedule` so the
-- rows written before this column existed keep a true statement about themselves.
-- -----------------------------------------------------------------------------
ALTER TABLE clep.evaluation_schedule
    ADD COLUMN trigger_kind text NOT NULL DEFAULT 'schedule';

ALTER TABLE clep.evaluation_schedule
    ADD CONSTRAINT ck_evaluation_schedule__trigger_kind
    CHECK (trigger_kind IN ('schedule', 'post_deployment', 'canary'));

COMMENT ON COLUMN clep.evaluation_schedule.trigger_kind IS
    'Why this standing order exists. Only a post_deployment or canary schedule '
    'produces a release observation, because only those two describe a system '
    'that is already live.';

-- -----------------------------------------------------------------------------
-- evaluation_schedule_candidate (REQ-F-10-1)
--
-- What the schedule evaluates. Shaped like `run_candidate` because it becomes
-- one: the scheduler copies these onto each run it creates, so that what a
-- reader sees in the run is what the schedule said, and a later edit to the
-- schedule cannot rewrite what a past run measured.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.evaluation_schedule_candidate (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    evaluation_schedule_id   uuid NOT NULL,
    label                    text NOT NULL,
    model_configuration_id   uuid NOT NULL,
    prompt_version_id        uuid,
    endpoint_kind            text NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_evaluation_schedule_candidate__org_id
        UNIQUE (organization_id, id),
    CONSTRAINT fk_evaluation_schedule_candidate__schedule
        FOREIGN KEY (organization_id, evaluation_schedule_id)
        REFERENCES clep.evaluation_schedule (organization_id, id),
    CONSTRAINT fk_evaluation_schedule_candidate__model_configuration
        FOREIGN KEY (organization_id, model_configuration_id)
        REFERENCES clep.model_configuration (organization_id, id),
    CONSTRAINT fk_evaluation_schedule_candidate__prompt_version
        FOREIGN KEY (organization_id, prompt_version_id)
        REFERENCES clep.prompt_version (organization_id, id),
    -- One label per schedule, for the same reason a run has one: two candidates
    -- sharing a label produce samples that cannot be told apart afterwards.
    CONSTRAINT uq_evaluation_schedule_candidate__label
        UNIQUE (organization_id, evaluation_schedule_id, label),
    CONSTRAINT ck_evaluation_schedule_candidate__label_is_present
        CHECK (length(label) > 0),
    CONSTRAINT ck_evaluation_schedule_candidate__endpoint_kind
        CHECK (endpoint_kind IN ('hosted', 'self_hosted'))
);

-- -----------------------------------------------------------------------------
-- run_sample.model_latency_ms (REQ-F-11-3)
--
-- Written when the sample is written, never patched in afterwards: a resolved
-- sample is immutable (I-18) and the runtime role has no UPDATE grant on it. The
-- gateway is the sole egress to a provider (ADR-003), so it is the only place
-- that can time the call without a second clock disagreeing with the first.
--
-- Nullable, because a sample recorded without one — a redelivery, or a run from
-- before this column existed — must read as "not measured" rather than as zero.
-- -----------------------------------------------------------------------------
ALTER TABLE clep.run_sample
    ADD COLUMN model_latency_ms integer;

ALTER TABLE clep.run_sample
    ADD CONSTRAINT ck_run_sample__model_latency_non_negative
    CHECK (model_latency_ms IS NULL OR model_latency_ms >= 0);

COMMENT ON COLUMN clep.run_sample.model_latency_ms IS
    'Wall-clock duration of the provider call this sample came from, measured '
    'at the gateway. REQ-F-11-3. Distinct from evaluator_outcome.duration_ms, '
    'which is evaluation latency and answers a different question.';

-- -----------------------------------------------------------------------------
-- alert_rule (REQ-F-11-9)
--
-- A condition someone chose, on one of the three dimensions the requirement
-- names. The threshold is NOT NULL and the direction is explicit, because an
-- alert that does not know which way is bad cannot decide anything — the same
-- reason `clep.threshold` carries a direction per metric.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.alert_rule (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    project_id          uuid NOT NULL,
    slug                text NOT NULL,
    display_name        text NOT NULL,
    dimension           text NOT NULL,
    metric_key          text NOT NULL,
    direction           text NOT NULL,
    threshold           numeric(18, 9) NOT NULL,
    minimum_sample_size integer NOT NULL,
    state               text NOT NULL DEFAULT 'active',
    created_by          uuid NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    paused_at           timestamptz,

    CONSTRAINT uq_alert_rule__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_alert_rule__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_alert_rule__project_slug
        UNIQUE (organization_id, project_id, slug),
    CONSTRAINT ck_alert_rule__dimension
        CHECK (dimension IN ('quality', 'cost', 'latency')),
    CONSTRAINT ck_alert_rule__direction
        CHECK (direction IN ('higher_is_better', 'lower_is_better')),
    CONSTRAINT ck_alert_rule__state CHECK (state IN ('active', 'paused')),
    CONSTRAINT ck_alert_rule__paused_at_matches_state
        CHECK ((state = 'paused') = (paused_at IS NOT NULL)),
    CONSTRAINT ck_alert_rule__slug_is_present CHECK (length(slug) > 0),
    CONSTRAINT ck_alert_rule__metric_key_is_present CHECK (length(metric_key) > 0),
    -- REQ-F-08-3 applied to alerting. A rule that fires on one sample is a rule
    -- that pages someone about noise, and a minimum of zero is that rule.
    CONSTRAINT ck_alert_rule__minimum_sample_size_positive
        CHECK (minimum_sample_size > 0)
);

COMMENT ON TABLE clep.alert_rule IS
    'REQ-F-11-9. A condition on quality, cost or latency. There is deliberately '
    'no delivery column: a rule states what is worth knowing, not who to wake.';

-- -----------------------------------------------------------------------------
-- alert_event (REQ-F-11-9, REQ-F-11-7)
--
-- Audit-class: the condition held, at this value, against this run. Never
-- edited, for the reason a release observation is never edited — a firing
-- rewritten after the outcome is known is not a record, it is a retelling.
--
-- `evidence_completeness` is the run's own completeness, carried onto the alert
-- rather than left behind. `REQ-F-11-7` requires a figure computed from
-- incomplete data to be marked as incomplete in EVERY view it appears in, and an
-- alert is a view.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.alert_event (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    alert_rule_id         uuid NOT NULL,
    run_id                uuid NOT NULL,
    observed_value        numeric(18, 9) NOT NULL,
    threshold             numeric(18, 9) NOT NULL,
    sample_size           integer NOT NULL,
    evidence_completeness text NOT NULL,
    detail                text NOT NULL,
    fired_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_alert_event__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_alert_event__rule
        FOREIGN KEY (organization_id, alert_rule_id)
        REFERENCES clep.alert_rule (organization_id, id),
    CONSTRAINT fk_alert_event__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id),
    -- One firing per rule per run. Evaluating the same run twice — a redelivery,
    -- a re-read, a second sweep — must not produce a second alert, and the store
    -- is what guarantees it rather than the caller remembering.
    CONSTRAINT uq_alert_event__rule_run UNIQUE (organization_id, alert_rule_id, run_id),
    CONSTRAINT ck_alert_event__sample_size_positive CHECK (sample_size > 0),
    CONSTRAINT ck_alert_event__evidence_completeness
        CHECK (evidence_completeness IN ('complete', 'partial', 'exhausted',
                                         'cancelled', 'rejected')),
    CONSTRAINT ck_alert_event__detail_is_present CHECK (length(detail) > 0)
);

COMMENT ON TABLE clep.alert_event IS
    'REQ-F-11-9 and REQ-F-11-7. A condition held, at a value, over a stated '
    'sample size, against evidence of a stated completeness. There is no column '
    'recording a notification sent or an action taken.';

CREATE TRIGGER trg_alert_event__immutable
    BEFORE UPDATE OR DELETE ON clep.alert_event
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE INDEX ix_alert_event__organization_rule_fired
    ON clep.alert_event (organization_id, alert_rule_id, fired_at DESC);

-- =============================================================================
-- Row-level security.
-- =============================================================================
ALTER TABLE clep.evaluation_schedule_candidate ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.evaluation_schedule_candidate FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.evaluation_schedule_candidate
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.alert_rule  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.alert_rule  FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.alert_rule
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.alert_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.alert_event FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.alert_event
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- =============================================================================
-- Runtime grants. A rule is paused, so it takes UPDATE; a firing and a
-- schedule's candidate set are written once and never amended.
-- =============================================================================
GRANT SELECT, INSERT         ON clep.evaluation_schedule_candidate TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.alert_rule  TO clep_runtime;
GRANT SELECT, INSERT         ON clep.alert_event TO clep_runtime;
