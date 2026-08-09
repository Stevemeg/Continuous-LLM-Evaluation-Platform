-- =============================================================================
-- 09 — Retrieval, trajectories, hallucination findings and stage attribution
--
-- Phase 9. ADDs only; files 01–08 are applied and recorded by SHA-256.
--
-- Four properties are load-bearing.
--
--   1. What retrieval was *supposed* to find is a property of the EXAMPLE, not
--      of what came back. A required passage the retriever missed is absent
--      from its own output, so a label carried there could never express the
--      case that matters — and that case is the whole of REQ-F-03-6.
--   2. A citation is a foreign key, not a string. An answer citing a passage
--      that was never retrieved is a defect the store can see.
--   3. A truncated trajectory is marked truncated on the run sample, so nothing
--      downstream can read a prefix as a complete run (REQ-F-04-5).
--   4. Retrieved passages and tool results are content from outside the
--      platform. They are stored by digest and reference, never re-copied into
--      a judgement record, so erasing the example erases them once.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- required_context (REQ-F-03-1, REQ-F-03-6)
--
-- The dataset's statement of what the answer needed. On the example, because
-- that is what it is a fact about.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.required_context (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    example_id       uuid NOT NULL,
    context_ref      text NOT NULL,
    note             text,

    CONSTRAINT uq_required_context__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_required_context__example
        FOREIGN KEY (organization_id, example_id)
        REFERENCES clep.example (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_required_context__example_ref
        UNIQUE (organization_id, example_id, context_ref),
    CONSTRAINT ck_required_context__ref_is_present
        CHECK (length(context_ref) > 0)
);

COMMENT ON TABLE clep.required_context IS
    'REQ-F-03-6. Without this an evaluation cannot tell a retriever that missed '
    'the evidence from a generator that ignored it: both look like a wrong '
    'answer with some passages attached.';

-- -----------------------------------------------------------------------------
-- retrieved_context (REQ-F-03-1, REQ-F-03-5)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.retrieved_context (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    run_sample_id     uuid NOT NULL,
    context_ref       text NOT NULL,
    retrieval_rank    integer NOT NULL,
    content_digest    text NOT NULL,
    payload_ref       text,

    CONSTRAINT uq_retrieved_context__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_retrieved_context__run_sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_retrieved_context__sample_ref
        UNIQUE (organization_id, run_sample_id, context_ref),
    CONSTRAINT uq_retrieved_context__sample_rank
        UNIQUE (organization_id, run_sample_id, retrieval_rank),
    CONSTRAINT ck_retrieved_context__rank_is_a_position
        CHECK (retrieval_rank >= 0),
    CONSTRAINT ck_retrieved_context__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$')
);

COMMENT ON COLUMN clep.retrieved_context.content_digest IS
    'The passage is stored by digest and reference, not copied here. It is '
    'third-party content under REQ-F-03-5, and erasing the example that '
    'produced it must not leave a second copy behind (REQ-N-PRIV-4).';

-- -----------------------------------------------------------------------------
-- sample_citation — what the answer claimed to rest on
--
-- The composite foreign key is the point: a citation can only name a passage
-- that this sample actually retrieved. An answer citing a source it cannot have
-- read is not representable.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.sample_citation (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    run_sample_id         uuid NOT NULL,
    retrieved_context_id  uuid NOT NULL,

    CONSTRAINT uq_sample_citation__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_sample_citation__run_sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_sample_citation__retrieved_context
        FOREIGN KEY (organization_id, retrieved_context_id)
        REFERENCES clep.retrieved_context (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_sample_citation__once
        UNIQUE (organization_id, run_sample_id, retrieved_context_id)
);

-- -----------------------------------------------------------------------------
-- trajectory_step (REQ-F-04-1, REQ-F-04-5, REQ-F-04-6)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.trajectory_step (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    run_sample_id     uuid NOT NULL,
    step_order        integer NOT NULL,
    tool              text NOT NULL,
    arguments         jsonb NOT NULL,
    result_digest     text,
    failed            boolean NOT NULL DEFAULT false,
    error             text,

    CONSTRAINT uq_trajectory_step__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_trajectory_step__run_sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_trajectory_step__order
        UNIQUE (organization_id, run_sample_id, step_order),
    CONSTRAINT ck_trajectory_step__order_is_a_position CHECK (step_order >= 0),
    CONSTRAINT ck_trajectory_step__tool_is_named CHECK (length(tool) > 0),
    CONSTRAINT ck_trajectory_step__arguments_are_an_object
        CHECK (jsonb_typeof(arguments) = 'object'),
    -- A failed call records what went wrong, and a call carrying an error did
    -- not succeed. Both directions, because either alone permits a lie.
    CONSTRAINT ck_trajectory_step__failure_carries_its_error
        CHECK (failed = (error IS NOT NULL)),
    CONSTRAINT ck_trajectory_step__result_digest_form
        CHECK (result_digest IS NULL
               OR result_digest ~ '^sha256:[0-9a-f]{64}$')
);

COMMENT ON COLUMN clep.trajectory_step.result_digest IS
    'A tool result is untrusted content (REQ-F-04-6) and is stored by digest '
    'for the same reason a retrieved passage is.';

-- Truncation is a property of the sample's trajectory, recorded where nothing
-- downstream can miss it. REQ-F-04-5: a truncated trajectory is never
-- evaluated as complete, and an evaluator reading these rows must be able to
-- see that it is holding a prefix.
ALTER TABLE clep.run_sample
    ADD COLUMN trajectory_truncated boolean NOT NULL DEFAULT false;

-- -----------------------------------------------------------------------------
-- hallucination_finding (REQ-F-03-3, ADR-018)
--
-- Two orthogonal bounded judgements, one quadrant. `not_analysable` is a
-- first-class finding and carries its reason, because an escalated judgement is
-- not a low score.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.hallucination_finding (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    run_sample_id            uuid NOT NULL,
    claim_ordinal            integer NOT NULL,
    claim_digest             text NOT NULL,
    finding                  text NOT NULL,
    support_score            numeric(18, 9),
    contradiction_score      numeric(18, 9),
    support_threshold        numeric(18, 9),
    contradiction_threshold  numeric(18, 9),
    reason                   text,
    recorded_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_hallucination_finding__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_hallucination_finding__run_sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_hallucination_finding__claim
        UNIQUE (organization_id, run_sample_id, claim_ordinal),
    CONSTRAINT ck_hallucination_finding__finding
        CHECK (finding IN ('grounded', 'unsupported', 'contradicted',
                           'not_analysable')),
    CONSTRAINT ck_hallucination_finding__claim_digest_form
        CHECK (claim_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_hallucination_finding__scores_are_bounded
        CHECK ((support_score IS NULL
                OR (support_score >= 0 AND support_score <= 1))
               AND (contradiction_score IS NULL
                    OR (contradiction_score >= 0 AND contradiction_score <= 1))),
    -- An unanalysable finding says what was missing; a reached one does not
    -- need an excuse.
    CONSTRAINT ck_hallucination_finding__unanalysable_says_why
        CHECK ((finding = 'not_analysable') = (reason IS NOT NULL)),
    -- A reached finding rests on two scores and two thresholds. Recording one
    -- without the other would leave a classification nobody can re-derive.
    CONSTRAINT ck_hallucination_finding__reached_findings_show_their_working
        CHECK (finding = 'not_analysable'
               OR (support_score IS NOT NULL
                   AND contradiction_score IS NOT NULL
                   AND support_threshold IS NOT NULL
                   AND contradiction_threshold IS NOT NULL))
);

-- -----------------------------------------------------------------------------
-- stage_attribution (REQ-F-03-6)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.stage_attribution (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    run_sample_id            uuid NOT NULL,
    stage                    text NOT NULL,
    reason                   text NOT NULL,
    missing_context_refs     text,
    faithfulness_score       numeric(18, 9),
    faithfulness_threshold   numeric(18, 9),
    recorded_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_stage_attribution__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_stage_attribution__run_sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_stage_attribution__one_per_sample
        UNIQUE (organization_id, run_sample_id),
    CONSTRAINT ck_stage_attribution__stage
        CHECK (stage IN ('retrieval', 'generation', 'neither',
                         'not_attributable')),
    CONSTRAINT ck_stage_attribution__states_its_grounds
        CHECK (length(reason) > 0),
    -- A retrieval failure names what was missing. Without that the attribution
    -- is an assertion rather than a finding.
    CONSTRAINT ck_stage_attribution__retrieval_names_what_was_missing
        CHECK (stage <> 'retrieval' OR missing_context_refs IS NOT NULL),
    -- A generation failure rests on a faithfulness verdict below a threshold,
    -- and both are recorded so the call can be re-derived.
    CONSTRAINT ck_stage_attribution__generation_shows_its_working
        CHECK (stage <> 'generation'
               OR (faithfulness_score IS NOT NULL
                   AND faithfulness_threshold IS NOT NULL))
);

-- =============================================================================
-- Immutability. A finding is evidence behind a score and is audit-class.
-- =============================================================================
CREATE TRIGGER trg_hallucination_finding__immutable
    BEFORE UPDATE OR DELETE ON clep.hallucination_finding
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_stage_attribution__immutable
    BEFORE UPDATE OR DELETE ON clep.stage_attribution
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_retrieved_context__immutable
    BEFORE UPDATE OR DELETE ON clep.retrieved_context
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_trajectory_step__immutable
    BEFORE UPDATE OR DELETE ON clep.trajectory_step
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

-- =============================================================================
-- Row-level security. Every table in this file is tenant-scoped.
-- =============================================================================
ALTER TABLE clep.required_context        ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.required_context        FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.required_context
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.retrieved_context       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.retrieved_context       FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.retrieved_context
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.sample_citation         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.sample_citation         FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.sample_citation
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.trajectory_step         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.trajectory_step         FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.trajectory_step
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.hallucination_finding   ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.hallucination_finding   FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.hallucination_finding
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.stage_attribution       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.stage_attribution       FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.stage_attribution
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- =============================================================================
-- Runtime grants. No DELETE anywhere in this file: erasure reaches these rows
-- through ON DELETE CASCADE from the example and the run sample, which is one
-- deletion path rather than two.
-- =============================================================================
GRANT SELECT, INSERT ON clep.required_context      TO clep_runtime;
GRANT SELECT, INSERT ON clep.retrieved_context     TO clep_runtime;
GRANT SELECT, INSERT ON clep.sample_citation       TO clep_runtime;
GRANT SELECT, INSERT ON clep.trajectory_step       TO clep_runtime;
GRANT SELECT, INSERT ON clep.hallucination_finding TO clep_runtime;
GRANT SELECT, INSERT ON clep.stage_attribution     TO clep_runtime;
