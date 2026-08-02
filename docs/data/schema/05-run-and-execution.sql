-- =============================================================================
-- 05 — Runs, samples, cost ledger and checkpoints
--
-- Phase 5. Unlike files 01–04 this one is applied: the migration runner reads
-- every file in this directory in order, so the specification and the migration
-- are the same artifact and cannot drift apart.
--
-- The unique constraints on idempotency_key are not defensive programming. The
-- ADR-001 spike crashed a worker between committing a sample's side effect and
-- recording its completion, and BOTH candidate engines then billed that sample
-- twice. Neither engine provides exactly-once effects. These constraints are
-- what makes REQ-N-REL-2 true, and I-21 with it.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- run
--
-- Completeness is deliberately not a boolean and deliberately not the same thing
-- as execution state. A run that finished executing may have finished in any of
-- five ways, only one of which is success. Reproducibility is stored rather than
-- inferred at replay time, because content can be erased after the fact.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.run (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    project_id            uuid NOT NULL,
    suite_version_id      uuid NOT NULL,
    dataset_version_id    uuid NOT NULL,
    identity_digest       text NOT NULL,
    integration_tier      text NOT NULL,
    execution_state       text NOT NULL,
    completeness          text,
    incomplete_reason     text,
    reproducibility       text NOT NULL DEFAULT 'reproducible',
    budget_limit          numeric(18, 9),
    budget_currency       char(3),
    correlation_id        text,
    idempotency_key       text NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    frozen_at             timestamptz NOT NULL DEFAULT now(),
    started_at            timestamptz,
    completed_at          timestamptz,

    CONSTRAINT uq_run__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_run__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT fk_run__suite_version
        FOREIGN KEY (organization_id, suite_version_id)
        REFERENCES clep.suite_version (organization_id, id),
    CONSTRAINT fk_run__dataset_version
        FOREIGN KEY (organization_id, dataset_version_id)
        REFERENCES clep.dataset_version (organization_id, id),
    CONSTRAINT uq_run__idempotency_key UNIQUE (organization_id, project_id, idempotency_key),
    CONSTRAINT ck_run__identity_digest CHECK (identity_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_run__integration_tier
        CHECK (integration_tier IN ('full', 'partial', 'output_only')),
    CONSTRAINT ck_run__execution_state
        CHECK (execution_state IN ('queued', 'running', 'terminal')),
    CONSTRAINT ck_run__completeness
        CHECK (completeness IS NULL OR completeness IN
               ('complete', 'partial', 'exhausted', 'cancelled', 'rejected')),
    CONSTRAINT ck_run__reproducibility
        CHECK (reproducibility IN ('reproducible', 'auditable')),
    -- A terminal run has decided how it ended; a non-terminal run has not.
    CONSTRAINT ck_run__completeness_matches_state
        CHECK ((execution_state = 'terminal') = (completeness IS NOT NULL)),
    -- REQ-X-1: anything other than `complete` must say why, in the record itself.
    CONSTRAINT ck_run__incomplete_reason_present
        CHECK (completeness IS NULL OR completeness = 'complete'
               OR incomplete_reason IS NOT NULL),
    CONSTRAINT ck_run__completed_at_matches_state
        CHECK ((execution_state = 'terminal') = (completed_at IS NOT NULL)),
    CONSTRAINT ck_run__budget_pair
        CHECK ((budget_limit IS NULL) = (budget_currency IS NULL))
);

CREATE INDEX ix_run__project_created ON clep.run (organization_id, project_id, created_at DESC);

ALTER TABLE clep.run ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.run FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.run
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- -----------------------------------------------------------------------------
-- run_candidate
--
-- Two or more candidates compare configurations within one run. REQ-F-02-6
-- requires one candidate's provider failure to leave its siblings valid, which
-- is why resolution lives on the sample rather than on the run.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.run_candidate (
    id                      uuid PRIMARY KEY,
    organization_id         uuid NOT NULL,
    run_id                  uuid NOT NULL,
    label                   text NOT NULL,
    model_configuration_id  uuid NOT NULL,
    prompt_version_id       uuid,
    endpoint_kind           text NOT NULL,

    CONSTRAINT uq_run_candidate__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_run_candidate__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_run_candidate__label UNIQUE (organization_id, run_id, label),
    -- REQ-F-02-4: self-hosted endpoints are first-class, not a special case.
    CONSTRAINT ck_run_candidate__endpoint_kind
        CHECK (endpoint_kind IN ('hosted', 'self_hosted'))
);

ALTER TABLE clep.run_candidate ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.run_candidate FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.run_candidate
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- -----------------------------------------------------------------------------
-- run_sample
--
-- One row per candidate per example. `resolution` carries six values and only
-- one of them permits a score: an unscored sample must never be readable as a
-- zero, which would silently drag an average down and look like a regression.
--
-- idempotency_key is derived from run identity plus candidate plus example. Its
-- uniqueness is what makes redelivery safe.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.run_sample (
    id                      uuid PRIMARY KEY,
    organization_id         uuid NOT NULL,
    run_id                  uuid NOT NULL,
    run_candidate_id        uuid NOT NULL,
    example_id              uuid NOT NULL,
    sample_index            integer NOT NULL,
    example_content_digest  text,
    resolution              text NOT NULL,
    score                   numeric(18, 9),
    is_served_from_cache    boolean NOT NULL DEFAULT false,
    failure_kind            text,
    idempotency_key         text NOT NULL,
    scored_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_run_sample__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_run_sample__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_run_sample__candidate
        FOREIGN KEY (organization_id, run_candidate_id)
        REFERENCES clep.run_candidate (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_run_sample__example
        FOREIGN KEY (organization_id, example_id)
        REFERENCES clep.example (organization_id, id),
    CONSTRAINT uq_run_sample__idempotency_key UNIQUE (organization_id, idempotency_key),
    CONSTRAINT ck_run_sample__resolution
        CHECK (resolution IN ('scored', 'failed', 'timed_out', 'abstained',
                              'unavailable', 'truncated')),
    -- Only `scored` carries a number. Everything else carries none, ever.
    CONSTRAINT ck_run_sample__score_only_when_scored
        CHECK ((resolution = 'scored') = (score IS NOT NULL)),
    -- The five provider failure modes the ADR-003 spike established, including
    -- the fifth it discovered: exhausted quota is terminal, not a rate limit.
    CONSTRAINT ck_run_sample__failure_kind
        CHECK (failure_kind IS NULL OR failure_kind IN
               ('provider_outage', 'provider_rate_limited', 'provider_malformed',
                'model_unavailable', 'quota_exhausted', 'evaluator_error',
                'budget_exhausted', 'cancelled')),
    CONSTRAINT ck_run_sample__failure_kind_matches_resolution
        CHECK (resolution <> 'scored' OR failure_kind IS NULL)
);

CREATE INDEX ix_run_sample__run ON clep.run_sample (organization_id, run_id, sample_index);

ALTER TABLE clep.run_sample ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.run_sample FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.run_sample
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- -----------------------------------------------------------------------------
-- sample_cost
--
-- REQ-F-07-6 requires accounting per sample, and REQ-N-REL-2 requires it to be
-- counted once. The unique idempotency key is the mechanism; the ADR-001 spike
-- is the reason. Without it, a worker that dies in the wrong millisecond bills
-- the tenant twice and no error is raised anywhere.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.sample_cost (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    run_id              uuid NOT NULL,
    run_sample_id       uuid NOT NULL,
    prompt_tokens       integer NOT NULL,
    completion_tokens   integer NOT NULL,
    cost_amount         numeric(18, 9) NOT NULL,
    cost_currency       char(3) NOT NULL,
    idempotency_key     text NOT NULL,
    recorded_at         timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_sample_cost__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_sample_cost__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_sample_cost__sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_sample_cost__idempotency_key UNIQUE (organization_id, idempotency_key),
    CONSTRAINT ck_sample_cost__tokens_not_negative
        CHECK (prompt_tokens >= 0 AND completion_tokens >= 0),
    CONSTRAINT ck_sample_cost__amount_not_negative CHECK (cost_amount >= 0)
);

CREATE INDEX ix_sample_cost__run ON clep.sample_cost (organization_id, run_id);

ALTER TABLE clep.sample_cost ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.sample_cost FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.sample_cost
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- -----------------------------------------------------------------------------
-- run_checkpoint
--
-- The task queue does not know where a run got to, so the application must. The
-- advance is monotonic: a redelivered job that has already been overtaken must
-- not drag the marker backwards.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.run_checkpoint (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    run_id                uuid NOT NULL,
    last_completed_index  integer NOT NULL DEFAULT -1,
    attempt_count         integer NOT NULL DEFAULT 0,
    updated_at            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_run_checkpoint__org_id UNIQUE (organization_id, id),
    CONSTRAINT uq_run_checkpoint__run UNIQUE (organization_id, run_id),
    CONSTRAINT fk_run_checkpoint__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT ck_run_checkpoint__index_not_below_start
        CHECK (last_completed_index >= -1)
);

ALTER TABLE clep.run_checkpoint ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.run_checkpoint FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.run_checkpoint
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- -----------------------------------------------------------------------------
-- evaluator_outcome
--
-- One row per evaluator per sample. An evaluator that could not run at this
-- integration tier records `unavailable` (REQ-F-03-4): the platform never
-- approximates a result it could not obtain.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.evaluator_outcome (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    run_sample_id         uuid NOT NULL,
    evaluator_version_id  uuid NOT NULL,
    resolution            text NOT NULL,
    score                 numeric(18, 9),
    unavailable_reason    text,
    duration_ms           integer NOT NULL DEFAULT 0,
    recorded_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_evaluator_outcome__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_evaluator_outcome__sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_evaluator_outcome__sample_evaluator
        UNIQUE (organization_id, run_sample_id, evaluator_version_id),
    CONSTRAINT ck_evaluator_outcome__resolution
        CHECK (resolution IN ('scored', 'failed', 'timed_out', 'abstained',
                              'unavailable', 'truncated')),
    CONSTRAINT ck_evaluator_outcome__score_only_when_scored
        CHECK ((resolution = 'scored') = (score IS NOT NULL)),
    CONSTRAINT ck_evaluator_outcome__unavailable_reason
        CHECK (resolution <> 'unavailable' OR unavailable_reason IS NOT NULL)
);

ALTER TABLE clep.evaluator_outcome ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.evaluator_outcome FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.evaluator_outcome
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- -----------------------------------------------------------------------------
-- Runtime grants. No UPDATE or DELETE on anything the audit trail depends on.
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON clep.run TO clep_runtime;
GRANT SELECT, INSERT ON clep.run_candidate TO clep_runtime;
GRANT SELECT, INSERT ON clep.run_sample TO clep_runtime;
GRANT SELECT, INSERT ON clep.sample_cost TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.run_checkpoint TO clep_runtime;
GRANT SELECT, INSERT ON clep.evaluator_outcome TO clep_runtime;
