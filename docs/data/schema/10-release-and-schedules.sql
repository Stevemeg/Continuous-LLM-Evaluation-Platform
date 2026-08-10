-- =============================================================================
-- 10 — Scheduled evaluation, post-deployment observation, recommendations
--
-- Phase 10. ADDs only, plus one column on `run` for why a run happened.
--
-- Three properties.
--
--   1. A schedule carries a budget, and it is NOT NULL. REQ-F-10-5 skips a run
--      whose estimate exceeds its budget rather than partially executing one,
--      and a standing order with no budget is the one arrangement where that
--      protection cannot apply.
--   2. A recommendation is advice. There is no column here naming an action the
--      platform would take, no target endpoint, and no "applied" flag —
--      REQ-F-10-3 forbids the product from autonomously changing a production
--      system, and a schema that could record having done so is a schema that
--      expects to.
--   3. Why a run happened is part of the run. A canary run and a pull-request
--      run are the same measurement asked for different reasons, and a reader
--      comparing them needs to know which is which.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- run.trigger (REQ-F-10-1, REQ-F-10-2)
--
-- Added by ALTER: file 05 is applied and recorded by its SHA-256. Defaulted to
-- `manual` so existing rows keep a true statement about themselves — every run
-- recorded before this column existed was started by a person.
-- -----------------------------------------------------------------------------
ALTER TABLE clep.run
    ADD COLUMN trigger_kind text NOT NULL DEFAULT 'manual';

ALTER TABLE clep.run
    ADD CONSTRAINT ck_run__trigger_kind
    CHECK (trigger_kind IN ('manual', 'schedule', 'pull_request',
                            'post_deployment', 'canary'));

-- -----------------------------------------------------------------------------
-- evaluation_schedule (REQ-F-10-1, REQ-F-10-5)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.evaluation_schedule (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    project_id               uuid NOT NULL,
    suite_version_id         uuid NOT NULL,
    gate_policy_version_id   uuid,
    baseline_id              uuid,
    cadence                  text NOT NULL,
    budget_limit             numeric(18, 9) NOT NULL,
    budget_currency          text NOT NULL,
    state                    text NOT NULL DEFAULT 'active',
    created_by               uuid NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    paused_at                timestamptz,
    last_run_id              uuid,

    CONSTRAINT uq_evaluation_schedule__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_evaluation_schedule__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT fk_evaluation_schedule__suite_version
        FOREIGN KEY (organization_id, suite_version_id)
        REFERENCES clep.suite_version (organization_id, id),
    CONSTRAINT fk_evaluation_schedule__policy_version
        FOREIGN KEY (organization_id, gate_policy_version_id)
        REFERENCES clep.gate_policy_version (organization_id, id),
    CONSTRAINT fk_evaluation_schedule__baseline
        FOREIGN KEY (organization_id, baseline_id)
        REFERENCES clep.baseline (organization_id, id),
    CONSTRAINT fk_evaluation_schedule__last_run
        FOREIGN KEY (organization_id, last_run_id)
        REFERENCES clep.run (organization_id, id),
    CONSTRAINT ck_evaluation_schedule__state CHECK (state IN ('active', 'paused')),
    CONSTRAINT ck_evaluation_schedule__paused_at_matches_state
        CHECK ((state = 'paused') = (paused_at IS NOT NULL)),
    CONSTRAINT ck_evaluation_schedule__cadence_is_present
        CHECK (length(cadence) > 0),
    -- REQ-F-10-5. A positive budget, not merely a non-null one: zero would
    -- skip every run and read as a schedule that silently does nothing.
    CONSTRAINT ck_evaluation_schedule__budget_is_positive
        CHECK (budget_limit > 0),
    CONSTRAINT ck_evaluation_schedule__currency_form
        CHECK (length(budget_currency) = 3)
);

COMMENT ON COLUMN clep.evaluation_schedule.budget_limit IS
    'NOT NULL and positive. REQ-F-10-5 skips a run whose estimate exceeds its '
    'budget rather than partially executing one, and an unbudgeted standing '
    'order is the one arrangement where that protection cannot apply.';

-- -----------------------------------------------------------------------------
-- release_observation (REQ-F-10-2, REQ-F-10-3)
--
-- Audit-class: what was observed about a live system, and what was advised. It
-- is never edited, because a recommendation that could be rewritten after the
-- outcome is known is not advice, it is hindsight.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.release_observation (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    project_id            uuid NOT NULL,
    run_id                uuid NOT NULL,
    gate_decision_id      uuid,
    trigger_kind          text NOT NULL,
    recommendation        text NOT NULL,
    rationale             text NOT NULL,
    observed_by           uuid NOT NULL,
    observed_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_release_observation__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_release_observation__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT fk_release_observation__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id),
    CONSTRAINT fk_release_observation__gate_decision
        FOREIGN KEY (organization_id, gate_decision_id)
        REFERENCES clep.gate_decision (organization_id, id),
    CONSTRAINT uq_release_observation__one_per_run
        UNIQUE (organization_id, run_id),
    -- Only the two triggers that describe a system already live. A manual or
    -- pull-request run is an evaluation before release, not an observation of
    -- one after it.
    CONSTRAINT ck_release_observation__trigger_kind
        CHECK (trigger_kind IN ('post_deployment', 'canary')),
    CONSTRAINT ck_release_observation__recommendation
        CHECK (recommendation IN ('rollback', 'investigate', 'hold_release',
                                  'none')),
    CONSTRAINT ck_release_observation__rationale_is_present
        CHECK (length(rationale) > 0)
);

COMMENT ON TABLE clep.release_observation IS
    'REQ-F-10-3. There is deliberately no column recording an action taken: no '
    'target endpoint, no applied flag, no rollback timestamp. The platform '
    'recommends and a person acts, and a schema able to record otherwise is a '
    'schema that expects to.';

CREATE TRIGGER trg_release_observation__immutable
    BEFORE UPDATE OR DELETE ON clep.release_observation
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

-- =============================================================================
-- Row-level security.
-- =============================================================================
ALTER TABLE clep.evaluation_schedule   ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.evaluation_schedule   FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.evaluation_schedule
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.release_observation   ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.release_observation   FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.release_observation
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- =============================================================================
-- Runtime grants. A schedule is paused and records its last run, so it takes
-- UPDATE; an observation is audit-class and takes none.
-- =============================================================================
GRANT SELECT, INSERT, UPDATE ON clep.evaluation_schedule TO clep_runtime;
GRANT SELECT, INSERT         ON clep.release_observation TO clep_runtime;
