-- =============================================================================
-- 08 — Judges, consensus, escalation, evaluation plans and bounded reasoning
--
-- Phase 8. ADDs only, plus one constraint replacement that files 01–07 cannot
-- express because run identity gained a component kind after they were sealed.
--
-- Four properties are load-bearing and none of them are application concerns.
--
--   1. A judgement that produced no score has no score row at all. `judge_run`
--      records the attempt; `judge_vote` records the number, and exists only
--      when there is one. REQ-X-8 says an unscored sample must never read as a
--      zero, and the strongest form of that is having nothing to read.
--   2. A consensus result always carries a disagreement measure (I-22). The
--      column is NOT NULL, and a separate flag says whether it was measured or
--      reported at its maximum because fewer than two judges scored — because
--      "they disagreed completely" and "there was nothing to compare" are
--      different facts and one number cannot carry both.
--   3. Escalation is terminal (I-24). An escalation is closed by a recorded
--      human review; the judgement it refers to is never edited, and a second
--      review is refused by a trigger rather than by a convention.
--   4. An accepted plan is frozen. It is the record of what was approved, and
--      an approval that can be edited afterwards records nothing.
--
-- Judges are tenant-scoped throughout, which narrows the "project or global"
-- tenancy the domain model records for `JudgeDefinition`. A judge version binds
-- a rubric to a model configuration, model configurations are tenant data, and
-- a global row cannot carry a composite foreign key into tenant data. The
-- alternative was a plain foreign key that does not carry organization_id —
-- the debt already tracked against `comparison.evaluator_version_id`, which is
-- to be carried rather than multiplied.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- run identity gains a judge version (REQ-F-07-1, ADR-004 D-5)
--
-- Replaced rather than edited in place: file 06 is applied and recorded by its
-- SHA-256, so changing it would change the digest of a migration that has
-- already run everywhere.
-- -----------------------------------------------------------------------------
ALTER TABLE clep.run_identity_component
    DROP CONSTRAINT ck_run_identity_component__kind;
ALTER TABLE clep.run_identity_component
    ADD CONSTRAINT ck_run_identity_component__kind
    CHECK (component_kind IN ('dataset_version', 'prompt_version',
                              'model_configuration', 'system_version',
                              'evaluator_version', 'judge_version', 'seed',
                              'environment', 'suite_version',
                              'integration_tier'));

-- -----------------------------------------------------------------------------
-- judge_definition / judge_version (REQ-F-AG-2, REQ-F-08-8)
--
-- A judge version is immutable and participates in run identity. Publishing
-- freezes it, for the same reason a prompt version freezes: a run that cites it
-- would otherwise change what it measured after the fact.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.judge_definition (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    project_id       uuid NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_judge_definition__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_judge_definition__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_judge_definition__project_slug
        UNIQUE (organization_id, project_id, slug)
);

CREATE TABLE clep.judge_version (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    judge_definition_id      uuid NOT NULL,
    model_configuration_id   uuid NOT NULL,
    version_number           integer NOT NULL,
    rubric_digest            text NOT NULL,
    content_digest           text NOT NULL,
    state                    text NOT NULL DEFAULT 'draft',
    created_by               uuid NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    published_at             timestamptz,

    CONSTRAINT uq_judge_version__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_judge_version__definition
        FOREIGN KEY (organization_id, judge_definition_id)
        REFERENCES clep.judge_definition (organization_id, id),
    CONSTRAINT fk_judge_version__model_configuration
        FOREIGN KEY (organization_id, model_configuration_id)
        REFERENCES clep.model_configuration (organization_id, id),
    CONSTRAINT uq_judge_version__definition_version
        UNIQUE (organization_id, judge_definition_id, version_number),
    CONSTRAINT ck_judge_version__state CHECK (state IN ('draft', 'published')),
    CONSTRAINT ck_judge_version__published_at_matches_state
        CHECK ((state = 'published') = (published_at IS NOT NULL)),
    CONSTRAINT ck_judge_version__rubric_digest_form
        CHECK (rubric_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_judge_version__content_digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$')
);

COMMENT ON COLUMN clep.judge_version.model_configuration_id IS
    'ADR-004 D-1 measures heterogeneity on the model configuration, not on the '
    'judge name. Two judge versions with different slugs and one configuration '
    'are one judge with two names, and the ensemble constraint below rejects '
    'them on this column rather than on the slug.';

-- -----------------------------------------------------------------------------
-- judge_ensemble / judge_ensemble_member (ADR-017 §2, §3)
--
-- The two parameters ADR-004 left open are columns here and are NULLABLE. An
-- ensemble with no agreement threshold escalates every judgement, which is the
-- ADR-007 pattern applied to consensus: the platform abstains until a person
-- supplies the number, rather than inheriting one nobody chose.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.judge_ensemble (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    project_id               uuid NOT NULL,
    slug                     text NOT NULL,
    agreement_threshold      numeric(18, 9),
    minimum_scoring_votes    integer,
    content_digest           text NOT NULL,
    created_by               uuid NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_judge_ensemble__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_judge_ensemble__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_judge_ensemble__project_slug
        UNIQUE (organization_id, project_id, slug),
    CONSTRAINT ck_judge_ensemble__threshold_range
        CHECK (agreement_threshold IS NULL
               OR (agreement_threshold >= 0 AND agreement_threshold <= 1)),
    CONSTRAINT ck_judge_ensemble__minimum_is_at_least_two
        CHECK (minimum_scoring_votes IS NULL OR minimum_scoring_votes >= 2),
    CONSTRAINT ck_judge_ensemble__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$')
);

COMMENT ON COLUMN clep.judge_ensemble.agreement_threshold IS
    'NULL until calibrated against real judge outputs. ADR-004 declined to set '
    'it and ADR-017 declines to invent it; a NULL here escalates every '
    'judgement with `no_threshold_configured` rather than guessing.';

CREATE TABLE clep.judge_ensemble_member (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    judge_ensemble_id   uuid NOT NULL,
    judge_version_id    uuid NOT NULL,
    added_at            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_judge_ensemble_member__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_judge_ensemble_member__ensemble
        FOREIGN KEY (organization_id, judge_ensemble_id)
        REFERENCES clep.judge_ensemble (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_judge_ensemble_member__judge_version
        FOREIGN KEY (organization_id, judge_version_id)
        REFERENCES clep.judge_version (organization_id, id),
    CONSTRAINT uq_judge_ensemble_member__once
        UNIQUE (organization_id, judge_ensemble_id, judge_version_id)
);

-- -----------------------------------------------------------------------------
-- judge_run / judge_vote (REQ-F-AG-3, REQ-X-8, I-23)
--
-- Two tables for one judgement, and the split is the point. `judge_run` is the
-- attempt: it always exists, and it carries the resolution, the latency, the
-- cost and whether the sample content had to be neutralised. `judge_vote` is
-- the score, and it exists only when there is one.
--
-- A schema in which the score were a nullable column on the attempt would make
-- "did not answer" and "answered zero" one NULL check apart. Here they are not
-- expressible as the same thing: an unscored judgement has no vote row, so
-- there is nothing for an aggregate to read as a zero.
--
-- Neither table is `evaluator_outcome` with a flag. REQ-F-08-6 and I-23 require
-- deterministic evaluator results and probabilistic judge votes to be different
-- entities, and they are different tables in different files.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.judge_run (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    run_id                uuid NOT NULL,
    run_sample_id         uuid NOT NULL,
    judge_version_id      uuid NOT NULL,
    resolution            text NOT NULL,
    latency_ms            integer NOT NULL DEFAULT 0,
    cost                  numeric(18, 9),
    currency              text,
    content_neutralised   boolean NOT NULL DEFAULT false,
    prompt_digest         text NOT NULL,
    detail                text,
    idempotency_key       text NOT NULL,
    judged_at             timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_judge_run__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_judge_run__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_judge_run__run_sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_judge_run__judge_version
        FOREIGN KEY (organization_id, judge_version_id)
        REFERENCES clep.judge_version (organization_id, id),
    CONSTRAINT uq_judge_run__idempotency_key
        UNIQUE (organization_id, idempotency_key),
    CONSTRAINT uq_judge_run__sample_judge
        UNIQUE (organization_id, run_sample_id, judge_version_id),
    CONSTRAINT ck_judge_run__resolution
        CHECK (resolution IN ('scored', 'failed', 'timed_out', 'abstained',
                              'unavailable', 'truncated')),
    CONSTRAINT ck_judge_run__cost_and_currency_travel_together
        CHECK ((cost IS NULL) = (currency IS NULL)),
    CONSTRAINT ck_judge_run__prompt_digest_form
        CHECK (prompt_digest ~ '^sha256:[0-9a-f]{64}$')
);

CREATE INDEX ix_judge_run__sample
    ON clep.judge_run (organization_id, run_sample_id);

COMMENT ON COLUMN clep.judge_run.prompt_digest IS
    'The digest of the prompt actually sent, including the fenced untrusted '
    'region. Recorded so that a disputed judgement can be re-derived from what '
    'the judge was asked rather than from what it was meant to be asked.';

CREATE TABLE clep.judge_vote (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    judge_run_id     uuid NOT NULL,
    score            numeric(18, 9) NOT NULL,
    recorded_at      timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_judge_vote__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_judge_vote__judge_run
        FOREIGN KEY (organization_id, judge_run_id)
        REFERENCES clep.judge_run (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_judge_vote__one_per_run
        UNIQUE (organization_id, judge_run_id),
    CONSTRAINT ck_judge_vote__score_range
        CHECK (score >= 0 AND score <= 1)
);

COMMENT ON TABLE clep.judge_vote IS
    'REQ-X-8. A score exists only where a judge produced one. There is no row '
    'for an abstention, a failure or a timeout, so no aggregate can read one '
    'as a zero.';

-- -----------------------------------------------------------------------------
-- consensus_result (I-22, I-24, ADR-017)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.consensus_result (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    run_id                   uuid NOT NULL,
    run_sample_id            uuid NOT NULL,
    judge_ensemble_id        uuid NOT NULL,
    state                    text NOT NULL,
    disagreement             numeric(18, 9) NOT NULL,
    disagreement_measured    boolean NOT NULL,
    method_version           text NOT NULL,
    verdict                  numeric(18, 9),
    confidence               numeric(18, 9),
    escalation_reason        text,
    escalation_detail        text,
    scoring_vote_count       integer NOT NULL,
    reached_at               timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_consensus_result__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_consensus_result__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_consensus_result__run_sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_consensus_result__ensemble
        FOREIGN KEY (organization_id, judge_ensemble_id)
        REFERENCES clep.judge_ensemble (organization_id, id),
    CONSTRAINT uq_consensus_result__sample_ensemble
        UNIQUE (organization_id, run_sample_id, judge_ensemble_id),
    CONSTRAINT ck_consensus_result__state CHECK (state IN ('agreed', 'escalated')),
    CONSTRAINT ck_consensus_result__disagreement_range
        CHECK (disagreement >= 0 AND disagreement <= 1),
    -- I-22 in the store: a verdict exists exactly when the ensemble agreed, and
    -- a disagreement measure exists always.
    CONSTRAINT ck_consensus_result__verdict_matches_state
        CHECK ((state = 'agreed') = (verdict IS NOT NULL)),
    CONSTRAINT ck_consensus_result__escalation_names_its_reason
        CHECK ((state = 'escalated') = (escalation_reason IS NOT NULL)),
    CONSTRAINT ck_consensus_result__escalation_reason
        CHECK (escalation_reason IS NULL OR escalation_reason IN
               ('disagreement_above_threshold', 'no_threshold_configured',
                'insufficient_scoring_votes')),
    -- An unmeasured disagreement is reported at its maximum, never at zero.
    CONSTRAINT ck_consensus_result__unmeasured_is_maximum
        CHECK (disagreement_measured OR disagreement = 1),
    CONSTRAINT ck_consensus_result__confidence_requires_measurement
        CHECK (confidence IS NULL OR disagreement_measured),
    CONSTRAINT ck_consensus_result__vote_count_is_not_negative
        CHECK (scoring_vote_count >= 0)
);

COMMENT ON COLUMN clep.consensus_result.disagreement_measured IS
    'False when fewer than two judges scored. The disagreement column then '
    'holds 1 rather than 0, because a single opinion reported as perfect '
    'agreement is the strongest possible claim on the weakest possible '
    'evidence (ADR-017 §4).';

-- -----------------------------------------------------------------------------
-- escalation (REQ-F-AG-4, REQ-F-12-7)
--
-- Human review as a recorded act. The consensus result is never edited: the
-- review is a state transition on this row, and the trigger below permits
-- exactly one of them.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.escalation (
    id                     uuid PRIMARY KEY,
    organization_id        uuid NOT NULL,
    project_id             uuid NOT NULL,
    consensus_result_id    uuid NOT NULL,
    state                  text NOT NULL DEFAULT 'open',
    reason                 text NOT NULL,
    raised_at              timestamptz NOT NULL DEFAULT now(),
    reviewed_by            uuid,
    reviewed_at            timestamptz,
    review_outcome         text,
    justification          text,

    CONSTRAINT uq_escalation__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_escalation__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT fk_escalation__consensus_result
        FOREIGN KEY (organization_id, consensus_result_id)
        REFERENCES clep.consensus_result (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_escalation__one_per_result
        UNIQUE (organization_id, consensus_result_id),
    CONSTRAINT ck_escalation__state CHECK (state IN ('open', 'reviewed')),
    CONSTRAINT ck_escalation__reason
        CHECK (reason IN ('disagreement_above_threshold',
                          'no_threshold_configured',
                          'insufficient_scoring_votes')),
    -- A review is an actor, a time, an outcome and a justification, or it did
    -- not happen (REQ-F-12-7).
    CONSTRAINT ck_escalation__review_is_complete
        CHECK ((state = 'open')
               OR (reviewed_by IS NOT NULL AND reviewed_at IS NOT NULL
                   AND review_outcome IS NOT NULL AND justification IS NOT NULL)),
    CONSTRAINT ck_escalation__open_carries_no_review
        CHECK (state = 'reviewed'
               OR (reviewed_by IS NULL AND reviewed_at IS NULL
                   AND review_outcome IS NULL AND justification IS NULL))
);

CREATE INDEX ix_escalation__open
    ON clep.escalation (organization_id, project_id, raised_at)
    WHERE state = 'open';

-- -----------------------------------------------------------------------------
-- evaluation_plan / plan_step / plan_amendment (REQ-F-AG-1, REQ-X-9)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.evaluation_plan (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    project_id               uuid NOT NULL,
    state                    text NOT NULL DEFAULT 'draft',
    objective                text NOT NULL,
    suite_version_id         uuid NOT NULL,
    baseline_id              uuid,
    gate_policy_version_id   uuid,
    judge_ensemble_id        uuid,
    budget_limit             numeric(18, 9),
    budget_currency          text,
    estimated_cost           numeric(18, 9) NOT NULL DEFAULT 0,
    content_digest           text NOT NULL,
    created_by               uuid NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    accepted_by              uuid,
    accepted_at              timestamptz,

    CONSTRAINT uq_evaluation_plan__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_evaluation_plan__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT fk_evaluation_plan__suite_version
        FOREIGN KEY (organization_id, suite_version_id)
        REFERENCES clep.suite_version (organization_id, id),
    CONSTRAINT fk_evaluation_plan__baseline
        FOREIGN KEY (organization_id, baseline_id)
        REFERENCES clep.baseline (organization_id, id),
    CONSTRAINT fk_evaluation_plan__gate_policy_version
        FOREIGN KEY (organization_id, gate_policy_version_id)
        REFERENCES clep.gate_policy_version (organization_id, id),
    CONSTRAINT fk_evaluation_plan__judge_ensemble
        FOREIGN KEY (organization_id, judge_ensemble_id)
        REFERENCES clep.judge_ensemble (organization_id, id),
    CONSTRAINT ck_evaluation_plan__state
        CHECK (state IN ('draft', 'accepted', 'rejected')),
    CONSTRAINT ck_evaluation_plan__acceptance_is_recorded
        CHECK ((state <> 'accepted')
               OR (accepted_by IS NOT NULL AND accepted_at IS NOT NULL)),
    CONSTRAINT ck_evaluation_plan__budget_travels_with_its_currency
        CHECK ((budget_limit IS NULL) = (budget_currency IS NULL)),
    -- REQ-F-10-5: a plan whose estimate exceeds its budget is refused, not
    -- started and abandoned halfway.
    CONSTRAINT ck_evaluation_plan__estimate_within_budget
        CHECK (budget_limit IS NULL OR estimated_cost <= budget_limit),
    CONSTRAINT ck_evaluation_plan__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$')
);

-- Steps are append-only and keyed by the digest of the plan revision they
-- belong to. An amendment inserts a new set under a new digest; nothing is
-- deleted, and nothing is granted DELETE. The plan's *current* steps are the
-- ones whose digest matches `evaluation_plan.content_digest`, so the earlier
-- revisions stay readable next to the amendment that replaced them — which is
-- what makes an amendment reviewable rather than merely recorded.
CREATE TABLE clep.plan_step (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    evaluation_plan_id    uuid NOT NULL,
    plan_digest           text NOT NULL,
    step_order            integer NOT NULL,
    kind                  text NOT NULL,
    subject               text NOT NULL,
    detail                text,
    estimated_cost        numeric(18, 9) NOT NULL DEFAULT 0,

    CONSTRAINT uq_plan_step__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_plan_step__plan
        FOREIGN KEY (organization_id, evaluation_plan_id)
        REFERENCES clep.evaluation_plan (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_plan_step__order UNIQUE (organization_id, evaluation_plan_id,
                                           plan_digest, step_order),
    CONSTRAINT ck_plan_step__kind
        CHECK (kind IN ('score_candidate', 'run_evaluator', 'run_ensemble',
                        'compare_to_baseline', 'evaluate_gate')),
    CONSTRAINT ck_plan_step__digest_form
        CHECK (plan_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_plan_step__cost_is_not_negative CHECK (estimated_cost >= 0)
);

CREATE TABLE clep.plan_amendment (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    evaluation_plan_id    uuid NOT NULL,
    actor_id              uuid NOT NULL,
    note                  text NOT NULL,
    prior_digest          text NOT NULL,
    amended_at            timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_plan_amendment__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_plan_amendment__plan
        FOREIGN KEY (organization_id, evaluation_plan_id)
        REFERENCES clep.evaluation_plan (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT ck_plan_amendment__prior_digest_form
        CHECK (prior_digest ~ '^sha256:[0-9a-f]{64}$')
);

COMMENT ON TABLE clep.plan_amendment IS
    'Every human edit, with the digest of what was edited. A plan whose edits '
    'are invisible is reviewable only in the sense that it can be looked at.';

-- -----------------------------------------------------------------------------
-- reasoning_trace / reasoning_attempt (REQ-F-AG-5)
--
-- The complete history of a bounded loop, rejected iterations included. Audit
-- class: written once, never updated, never deleted. A bound that can be
-- rewritten afterwards is not a bound anyone can check was respected.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.reasoning_trace (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    evaluation_plan_id    uuid NOT NULL,
    state                 text NOT NULL,
    max_iterations        integer NOT NULL,
    budget                numeric(18, 9) NOT NULL,
    timeout_ms            integer NOT NULL,
    cost                  numeric(18, 9) NOT NULL DEFAULT 0,
    duration_ms           integer NOT NULL DEFAULT 0,
    stopped_because       text NOT NULL,
    recorded_at           timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_reasoning_trace__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_reasoning_trace__plan
        FOREIGN KEY (organization_id, evaluation_plan_id)
        REFERENCES clep.evaluation_plan (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT ck_reasoning_trace__state
        CHECK (state IN ('accepted', 'iterations_exhausted', 'budget_exhausted',
                         'deadline_exceeded', 'no_progress', 'failed')),
    CONSTRAINT ck_reasoning_trace__bounds_are_stated
        CHECK (max_iterations >= 1 AND budget >= 0 AND timeout_ms >= 1),
    CONSTRAINT ck_reasoning_trace__stopped_because_is_present
        CHECK (length(stopped_because) > 0)
);

CREATE TABLE clep.reasoning_attempt (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    reasoning_trace_id    uuid NOT NULL,
    attempt_index         integer NOT NULL,
    accepted              boolean NOT NULL,
    critique              text NOT NULL DEFAULT '',
    cost                  numeric(18, 9) NOT NULL DEFAULT 0,
    duration_ms           integer NOT NULL DEFAULT 0,
    error                 text,

    CONSTRAINT uq_reasoning_attempt__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_reasoning_attempt__trace
        FOREIGN KEY (organization_id, reasoning_trace_id)
        REFERENCES clep.reasoning_trace (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_reasoning_attempt__index
        UNIQUE (organization_id, reasoning_trace_id, attempt_index),
    CONSTRAINT ck_reasoning_attempt__index_is_not_negative
        CHECK (attempt_index >= 0),
    -- A rejected attempt says why it was rejected, or the history records that
    -- something was refused without recording what was wrong with it.
    CONSTRAINT ck_reasoning_attempt__rejection_carries_a_reason
        CHECK (accepted OR error IS NOT NULL OR length(critique) > 0)
);

-- =============================================================================
-- Immutability
-- =============================================================================

-- A judgement, its votes and its consensus are audit-class: they are the
-- evidence behind a score, and a score whose evidence can be edited afterwards
-- is not evidence. `refuse_change_to_audit_record` is the Phase 7 function,
-- reused rather than restated.
CREATE TRIGGER trg_judge_run__immutable
    BEFORE UPDATE OR DELETE ON clep.judge_run
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_judge_vote__immutable
    BEFORE UPDATE OR DELETE ON clep.judge_vote
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_consensus_result__immutable
    BEFORE UPDATE OR DELETE ON clep.consensus_result
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_plan_amendment__immutable
    BEFORE UPDATE OR DELETE ON clep.plan_amendment
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_reasoning_trace__immutable
    BEFORE UPDATE OR DELETE ON clep.reasoning_trace
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_reasoning_attempt__immutable
    BEFORE UPDATE OR DELETE ON clep.reasoning_attempt
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

-- A published judge version is frozen. It participates in run identity, and a
-- run that cites it would otherwise change what it measured after the fact.
CREATE FUNCTION clep.refuse_change_to_published_judge_version() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    subject_id  uuid := COALESCE(OLD.id, NEW.id);
    citing_run  uuid;
BEGIN
    SELECT r.id INTO citing_run
    FROM clep.judge_run r
    WHERE r.judge_version_id = subject_id
    LIMIT 1;

    IF citing_run IS NOT NULL THEN
        RAISE EXCEPTION
            'ADR-004 D-5: judge version % produced judgement % and is immutable',
            subject_id, citing_run
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.state = 'published' THEN
        RAISE EXCEPTION
            'a published judge version is immutable; % may not be modified or '
            'deleted', subject_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_judge_version__immutable
    BEFORE UPDATE OR DELETE ON clep.judge_version
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_published_judge_version();

-- An ensemble that has judged anything is frozen for the same reason: the
-- composition and the threshold are what the verdicts were reached under.
-- The subject is chosen by table and by operation, not coalesced across both.
-- The first version of this used COALESCE over OLD.judge_ensemble_id and OLD.id,
-- which raises `record "old" has no field "judge_ensemble_id"` on the ensemble
-- table — so every update was refused, including a legitimate change to an
-- ensemble that had never judged, and the refusal looked correct from outside.
-- Found by the superuser probe: under the runtime role the grant refused first
-- and hid it.
CREATE FUNCTION clep.refuse_change_to_used_ensemble() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    subject_id  uuid;
    used_by     uuid;
BEGIN
    IF TG_TABLE_NAME = 'judge_ensemble' THEN
        subject_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.id ELSE NEW.id END;
    ELSE
        subject_id := CASE WHEN TG_OP = 'DELETE' THEN OLD.judge_ensemble_id
                           ELSE NEW.judge_ensemble_id END;
    END IF;

    SELECT c.id INTO used_by
    FROM clep.consensus_result c
    WHERE c.judge_ensemble_id = subject_id
    LIMIT 1;

    IF used_by IS NOT NULL THEN
        RAISE EXCEPTION
            'ensemble % reached consensus % and is immutable; its composition '
            'and threshold are what that verdict was reached under',
            subject_id, used_by
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_judge_ensemble__immutable_once_used
    BEFORE UPDATE OR DELETE ON clep.judge_ensemble
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_used_ensemble();

-- INSERT is guarded on the membership table: adding a judge to an ensemble that
-- has already reached verdicts would change what those verdicts were reached
-- under, without touching a single existing row.
CREATE TRIGGER trg_judge_ensemble_member__immutable_once_used
    BEFORE INSERT OR UPDATE OR DELETE ON clep.judge_ensemble_member
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_used_ensemble();

-- An accepted plan is the record of what was approved. Its steps go with it.
CREATE FUNCTION clep.refuse_change_to_accepted_plan() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    plan_id  uuid;
    frozen   boolean;
BEGIN
    -- OLD is unassigned on INSERT and NEW is unassigned on DELETE, so the row
    -- is chosen by operation rather than coalesced across both.
    IF TG_OP = 'DELETE' THEN
        plan_id := OLD.evaluation_plan_id;
    ELSE
        plan_id := NEW.evaluation_plan_id;
    END IF;

    SELECT p.state <> 'draft' INTO frozen
    FROM clep.evaluation_plan p
    WHERE p.id = plan_id;

    IF frozen THEN
        RAISE EXCEPTION
            'evaluation plan % is no longer a draft; amending it would change '
            'the record of what was approved', plan_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

-- INSERT is guarded too, unlike the audit-class tables. Steps are appended by
-- an amendment, so an accepted plan must refuse new ones as firmly as it
-- refuses edits: appending a step to a plan somebody signed would change what
-- was signed without changing a single existing row.
CREATE TRIGGER trg_plan_step__frozen_with_its_plan
    BEFORE INSERT OR UPDATE OR DELETE ON clep.plan_step
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_accepted_plan();

-- The plan itself may transition draft -> accepted or draft -> rejected, and
-- nothing else. A second transition, or a change to what was accepted, is
-- refused.
CREATE FUNCTION clep.refuse_reopening_a_settled_plan() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.state <> 'draft' THEN
            RAISE EXCEPTION
                'evaluation plan % is %; a settled plan is not deletable',
                OLD.id, OLD.state
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.state <> 'draft' THEN
        RAISE EXCEPTION
            'evaluation plan % is already %; it may not be reopened or edited',
            OLD.id, OLD.state
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.objective <> OLD.objective
       OR NEW.content_digest <> OLD.content_digest
       OR NEW.suite_version_id <> OLD.suite_version_id THEN
        IF NEW.state <> 'draft' THEN
            RAISE EXCEPTION
                'a plan may not change and settle in the same statement; the '
                'digest recorded at acceptance must be the one reviewed'
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_evaluation_plan__settles_once
    BEFORE UPDATE OR DELETE ON clep.evaluation_plan
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_reopening_a_settled_plan();

-- An escalation is reviewed once. I-24: escalation is terminal, and a second
-- review would be a retry wearing a form.
CREATE FUNCTION clep.refuse_second_review() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION
            'escalation % is the record that a human was asked; it is not '
            'deletable', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.state = 'reviewed' THEN
        RAISE EXCEPTION
            'escalation % has already been reviewed by %; a second review '
            'would be a retry, and escalation is terminal (I-24)',
            OLD.id, OLD.reviewed_by
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.consensus_result_id <> OLD.consensus_result_id
       OR NEW.reason <> OLD.reason THEN
        RAISE EXCEPTION
            'an escalation may be reviewed, not repointed; % refers to the '
            'judgement that raised it', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_escalation__reviewed_once
    BEFORE UPDATE OR DELETE ON clep.escalation
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_second_review();

-- =============================================================================
-- Row-level security. Every table in this file is tenant-scoped.
-- =============================================================================
ALTER TABLE clep.judge_definition       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.judge_definition       FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.judge_definition
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.judge_version          ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.judge_version          FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.judge_version
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.judge_ensemble         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.judge_ensemble         FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.judge_ensemble
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.judge_ensemble_member  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.judge_ensemble_member  FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.judge_ensemble_member
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.judge_run              ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.judge_run              FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.judge_run
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.judge_vote             ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.judge_vote             FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.judge_vote
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.consensus_result       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.consensus_result       FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.consensus_result
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.escalation             ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.escalation             FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.escalation
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.evaluation_plan        ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.evaluation_plan        FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.evaluation_plan
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.plan_step              ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.plan_step              FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.plan_step
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.plan_amendment         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.plan_amendment         FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.plan_amendment
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.reasoning_trace        ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.reasoning_trace        FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.reasoning_trace
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.reasoning_attempt      ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.reasoning_attempt      FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.reasoning_attempt
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- =============================================================================
-- Runtime grants.
--
-- No DELETE anywhere. UPDATE only where a state machine requires it: a draft
-- judge version is published, a draft plan is amended and then settled, and an
-- open escalation is reviewed exactly once. Everything that is evidence of a
-- judgement is INSERT and SELECT only, and its trigger refuses the rest even if
-- a grant were widened by mistake.
-- =============================================================================
GRANT SELECT, INSERT         ON clep.judge_definition      TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.judge_version         TO clep_runtime;
GRANT SELECT, INSERT         ON clep.judge_ensemble        TO clep_runtime;
GRANT SELECT, INSERT         ON clep.judge_ensemble_member TO clep_runtime;
GRANT SELECT, INSERT         ON clep.judge_run             TO clep_runtime;
GRANT SELECT, INSERT         ON clep.judge_vote            TO clep_runtime;
GRANT SELECT, INSERT         ON clep.consensus_result      TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.escalation            TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.evaluation_plan       TO clep_runtime;
GRANT SELECT, INSERT         ON clep.plan_step             TO clep_runtime;
GRANT SELECT, INSERT         ON clep.plan_amendment        TO clep_runtime;
GRANT SELECT, INSERT         ON clep.reasoning_trace       TO clep_runtime;
GRANT SELECT, INSERT         ON clep.reasoning_attempt     TO clep_runtime;
