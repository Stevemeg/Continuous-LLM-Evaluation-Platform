-- =============================================================================
-- 07 — Baselines, paired comparisons, quality-gate policies and decisions
--
-- Phase 7. ADDs only; files 01–06 are applied and recorded by SHA-256.
--
-- Three properties are load-bearing and none of them are application concerns.
--
--   1. A gate decision is audit-class (ADR-011, retention table R-4): it is
--      never updated and never deleted. An exception does not edit a decision,
--      it is recorded against one — a decision that could be rewritten after
--      the fact is not evidence that a release was justified.
--   2. The statistical parameters ADR-007 deliberately refused to set live on
--      the policy version, where a human chose them and the choice is versioned.
--      They are not defaults in code, because a default is a value nobody chose
--      applied to every tenant.
--   3. Comparability is a precondition, not a warning (REQ-F-08-8). The
--      constraint is that a comparison names the evaluator version it compared;
--      the engine refuses to pair scores from two different ones.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- baseline (REQ-F-01-2, REQ-X-4)
--
-- A baseline is an approved *run*, not a copy of its numbers. Copying would
-- create a second source of truth that cannot be re-derived and would survive
-- the erasure of the content it summarised.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.baseline (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    project_id          uuid NOT NULL,
    run_id              uuid NOT NULL,
    suite_version_id    uuid NOT NULL,
    dataset_version_id  uuid NOT NULL,
    label               text,
    state               text NOT NULL DEFAULT 'pending_approval',
    identity_digest     text NOT NULL,
    created_by          uuid NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    approved_by         uuid,
    approved_at         timestamptz,
    superseded_at       timestamptz,

    CONSTRAINT uq_baseline__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_baseline__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT fk_baseline__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id),
    CONSTRAINT fk_baseline__suite_version
        FOREIGN KEY (organization_id, suite_version_id)
        REFERENCES clep.suite_version (organization_id, id),
    CONSTRAINT fk_baseline__dataset_version
        FOREIGN KEY (organization_id, dataset_version_id)
        REFERENCES clep.dataset_version (organization_id, id),
    CONSTRAINT uq_baseline__run UNIQUE (organization_id, run_id),
    CONSTRAINT ck_baseline__state
        CHECK (state IN ('pending_approval', 'approved', 'superseded')),
    CONSTRAINT ck_baseline__approval_recorded
        CHECK ((state = 'pending_approval')
               OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)),
    CONSTRAINT ck_baseline__superseded_at_matches_state
        CHECK ((state = 'superseded') = (superseded_at IS NOT NULL)),
    CONSTRAINT ck_baseline__identity_digest_form
        CHECK (identity_digest ~ '^sha256:[0-9a-f]{64}$')
);

-- REQ-F-09-7: a caller evaluates against "the approved baseline" without
-- restating it, which is only unambiguous if there is exactly one per scope.
CREATE UNIQUE INDEX uq_baseline__one_approved_per_scope
    ON clep.baseline (organization_id, project_id, suite_version_id)
    WHERE state = 'approved';

COMMENT ON COLUMN clep.baseline.identity_digest IS
    'The Phase 6 run identity digest, copied at approval so a later comparison '
    'can detect that the run it points at no longer measures what was approved.';

-- -----------------------------------------------------------------------------
-- gate_policy / gate_policy_version (REQ-F-09-3, REQ-F-09-8)
--
-- The policy version carries the parameters ADR-007 left open. Confidence level,
-- resample count and bootstrap seed are recorded per version, so a decision made
-- last month can be re-derived exactly under the rules that applied then.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.gate_policy (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    project_id       uuid NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gate_policy__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_gate_policy__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_gate_policy__project_slug
        UNIQUE (organization_id, project_id, slug)
);

CREATE TABLE clep.gate_policy_version (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    gate_policy_id      uuid NOT NULL,
    version_number      integer NOT NULL,
    content_digest      text NOT NULL,
    state               text NOT NULL DEFAULT 'draft',
    confidence_level    numeric(9, 6) NOT NULL,
    resample_count      integer NOT NULL,
    bootstrap_seed      bigint NOT NULL,
    created_by          uuid NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    published_at        timestamptz,

    CONSTRAINT uq_gate_policy_version__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_gate_policy_version__policy
        FOREIGN KEY (organization_id, gate_policy_id)
        REFERENCES clep.gate_policy (organization_id, id),
    CONSTRAINT uq_gate_policy_version__policy_version
        UNIQUE (organization_id, gate_policy_id, version_number),
    CONSTRAINT ck_gate_policy_version__state CHECK (state IN ('draft', 'published')),
    CONSTRAINT ck_gate_policy_version__published_at_matches_state
        CHECK ((state = 'published') = (published_at IS NOT NULL)),
    CONSTRAINT ck_gate_policy_version__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_gate_policy_version__version_number_positive
        CHECK (version_number > 0),
    -- A confidence level outside (0,1) is not a confidence level, and a
    -- resample count of zero produces an interval over nothing.
    CONSTRAINT ck_gate_policy_version__confidence_level
        CHECK (confidence_level > 0 AND confidence_level < 1),
    CONSTRAINT ck_gate_policy_version__resample_count_positive
        CHECK (resample_count > 0)
);

-- -----------------------------------------------------------------------------
-- gate_criterion (REQ-F-08-1, REQ-F-08-3, REQ-F-09-3)
--
-- One row per metric the policy governs. `source` is where the paired values
-- come from; `dimension` is what the criterion is about. They are separate
-- because a quality criterion and a cost criterion are both paired per example,
-- and conflating the two would have made cost a special case.
--
-- precision_threshold and minimum_sample_size are per metric and nullable, which
-- is ADR-007's position expressed as a column: the value is not known until real
-- data has been measured, and a policy that omits it gets `insufficient_evidence`
-- rather than a number this schema invented.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.gate_criterion (
    id                        uuid PRIMARY KEY,
    organization_id           uuid NOT NULL,
    gate_policy_version_id    uuid NOT NULL,
    metric_key                text NOT NULL,
    dimension                 text NOT NULL,
    source                    text NOT NULL,
    direction                 text NOT NULL,
    precision_threshold       numeric(18, 9),
    minimum_sample_size       integer,
    absolute_floor            numeric(18, 9),
    relative_tolerance        numeric(18, 9),
    on_regression             text NOT NULL,
    on_insufficient_evidence  text NOT NULL,
    on_not_comparable         text NOT NULL,
    created_at                timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gate_criterion__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_gate_criterion__policy_version
        FOREIGN KEY (organization_id, gate_policy_version_id)
        REFERENCES clep.gate_policy_version (organization_id, id),
    CONSTRAINT uq_gate_criterion__version_metric
        UNIQUE (organization_id, gate_policy_version_id, metric_key),
    CONSTRAINT ck_gate_criterion__dimension
        CHECK (dimension IN ('quality', 'cost', 'latency', 'safety',
                             'judge_agreement', 'task_specific')),
    CONSTRAINT ck_gate_criterion__source
        CHECK (source IN ('evaluator', 'cost', 'latency', 'judge_agreement')),
    CONSTRAINT ck_gate_criterion__direction
        CHECK (direction IN ('higher_is_better', 'lower_is_better')),
    CONSTRAINT ck_gate_criterion__on_regression
        CHECK (on_regression IN ('hard_fail', 'warning', 'approval_required')),
    -- Not `pass`. REQ-F-08-4 makes "we could not tell" distinct from "nothing
    -- changed", and a policy that could map abstention to pass would erase that
    -- distinction at exactly the point where it matters.
    CONSTRAINT ck_gate_criterion__on_insufficient_evidence
        CHECK (on_insufficient_evidence IN ('hard_fail', 'warning', 'approval_required')),
    CONSTRAINT ck_gate_criterion__on_not_comparable
        CHECK (on_not_comparable IN ('hard_fail', 'warning', 'approval_required')),
    CONSTRAINT ck_gate_criterion__precision_threshold_positive
        CHECK (precision_threshold IS NULL OR precision_threshold > 0),
    CONSTRAINT ck_gate_criterion__minimum_sample_size_positive
        CHECK (minimum_sample_size IS NULL OR minimum_sample_size > 0),
    CONSTRAINT ck_gate_criterion__relative_tolerance_not_negative
        CHECK (relative_tolerance IS NULL OR relative_tolerance >= 0)
);

-- -----------------------------------------------------------------------------
-- gate_decision (REQ-F-09-4, REQ-F-09-8, REQ-N-COMP-1)
--
-- Audit class. No UPDATE and no DELETE is granted to the runtime role anywhere
-- in this file, and the trigger below refuses both regardless of grants.
--
-- `evaluated_outcome` is what the evidence produced. It never changes. An
-- exception is a separate, later, audited act recorded against this row, and the
-- outcome a reader sees is derived from both — never by overwriting the first.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.gate_decision (
    id                          uuid PRIMARY KEY,
    organization_id             uuid NOT NULL,
    project_id                  uuid NOT NULL,
    candidate_run_id            uuid NOT NULL,
    baseline_id                 uuid,
    gate_policy_version_id      uuid NOT NULL,
    evaluated_outcome           text NOT NULL,
    statistical_method_version  text NOT NULL,
    evidence_digest             text NOT NULL,
    decided_by                  uuid NOT NULL,
    decided_at                  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gate_decision__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_gate_decision__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT fk_gate_decision__candidate_run
        FOREIGN KEY (organization_id, candidate_run_id)
        REFERENCES clep.run (organization_id, id),
    CONSTRAINT fk_gate_decision__baseline
        FOREIGN KEY (organization_id, baseline_id)
        REFERENCES clep.baseline (organization_id, id),
    CONSTRAINT fk_gate_decision__policy_version
        FOREIGN KEY (organization_id, gate_policy_version_id)
        REFERENCES clep.gate_policy_version (organization_id, id),
    -- `exception_applied` is absent: it is derived from a live exception, never
    -- stored, because storing it would mean editing a decision after the fact.
    CONSTRAINT ck_gate_decision__evaluated_outcome
        CHECK (evaluated_outcome IN ('pass', 'hard_fail', 'warning',
                                     'approval_required', 'insufficient_evidence',
                                     'not_comparable')),
    -- A decision without a baseline can only be one thing, and saying so here
    -- stops a missing baseline from being reported as a quality verdict.
    CONSTRAINT ck_gate_decision__baseline_required_unless_not_comparable
        CHECK (baseline_id IS NOT NULL OR evaluated_outcome = 'not_comparable'),
    CONSTRAINT ck_gate_decision__evidence_digest_form
        CHECK (evidence_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_gate_decision__method_version_form
        CHECK (statistical_method_version ~ '^[a-z0-9-]+/[0-9]+$')
);

CREATE INDEX ix_gate_decision__candidate_run
    ON clep.gate_decision (organization_id, candidate_run_id);

-- -----------------------------------------------------------------------------
-- comparison (REQ-F-08-2, REQ-F-08-6, REQ-F-08-7)
--
-- One row per metric per decision. `result_kind` keeps deterministic evaluator
-- results and probabilistic judge results structurally separate: a reader who
-- groups by it cannot accidentally average a rubric score into an exact-match
-- rate, and a report that shows them in one column is a defect this column makes
-- visible.
--
-- Every numeric here is exact numeric, never floating point: a gate decision
-- must not turn on binary representation error.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.comparison (
    id                          uuid PRIMARY KEY,
    organization_id             uuid NOT NULL,
    gate_decision_id            uuid NOT NULL,
    metric_key                  text NOT NULL,
    result_kind                 text NOT NULL,
    evaluator_version_id        uuid,
    classification              text NOT NULL,
    sample_size                 integer NOT NULL,
    baseline_mean               numeric(18, 9),
    candidate_mean              numeric(18, 9),
    mean_difference             numeric(18, 9),
    interval_lower              numeric(18, 9),
    interval_upper              numeric(18, 9),
    confidence_level            numeric(9, 6),
    effect_size                 numeric(18, 9),
    minimum_sample_size         integer,
    statistical_method_version  text NOT NULL,
    abstention_reason           text,
    not_comparable_reason       text,
    created_at                  timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_comparison__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_comparison__gate_decision
        FOREIGN KEY (organization_id, gate_decision_id)
        REFERENCES clep.gate_decision (organization_id, id),
    CONSTRAINT uq_comparison__decision_metric
        UNIQUE (organization_id, gate_decision_id, metric_key),
    -- Not a composite key carrying organization_id, unlike every other foreign
    -- key here. evaluator_version is one of the enumerated global-scope
    -- exceptions (P-4): a builtin evaluator has no organization_id, so the
    -- composite reference P-5 requires cannot be formed. The plain key still
    -- closes the dangling-reference case — a comparison cannot cite a version
    -- that does not exist. It does not by itself stop a citation of another
    -- tenant's custom version; the engine reads the evaluator from the run's own
    -- suite, so no path constructs one, and expressing the rest in the store
    -- would mean duplicating a dual-scoped key. Recorded as a limit rather than
    -- left to be discovered.
    CONSTRAINT fk_comparison__evaluator_version
        FOREIGN KEY (evaluator_version_id)
        REFERENCES clep.evaluator_version (id),
    CONSTRAINT ck_comparison__result_kind
        CHECK (result_kind IN ('deterministic_evaluator', 'probabilistic_judge',
                               'operational')),
    CONSTRAINT ck_comparison__classification
        CHECK (classification IN ('regression', 'improvement', 'no_change',
                                  'insufficient_evidence', 'not_comparable')),
    CONSTRAINT ck_comparison__sample_size_not_negative CHECK (sample_size >= 0),
    -- An interval is both ends or neither, and the lower end is not above the
    -- upper one. A half-recorded interval reads as a valid one.
    CONSTRAINT ck_comparison__interval_complete
        CHECK ((interval_lower IS NULL) = (interval_upper IS NULL)
               AND (interval_lower IS NULL) = (confidence_level IS NULL)),
    CONSTRAINT ck_comparison__interval_ordered
        CHECK (interval_lower IS NULL OR interval_lower <= interval_upper),
    CONSTRAINT ck_comparison__confidence_level
        CHECK (confidence_level IS NULL
               OR (confidence_level > 0 AND confidence_level < 1)),
    -- REQ-F-08-4: an abstention that does not say why is indistinguishable from
    -- a verdict that was never computed.
    CONSTRAINT ck_comparison__abstention_reason_present
        CHECK ((classification = 'insufficient_evidence') = (abstention_reason IS NOT NULL)),
    CONSTRAINT ck_comparison__not_comparable_reason_present
        CHECK ((classification = 'not_comparable') = (not_comparable_reason IS NOT NULL)),
    -- REQ-F-08-8 again, at the store: a comparison that names no evaluator
    -- version cannot be a scored-evaluator comparison.
    CONSTRAINT ck_comparison__evaluator_version_matches_kind
        CHECK (result_kind = 'operational' OR evaluator_version_id IS NOT NULL)
);

-- -----------------------------------------------------------------------------
-- gate_criterion_result (REQ-F-09-4)
--
-- The exact evidence behind the decision, one row per criterion evaluated, with
-- the rule that fired named rather than implied. A report that says "failed"
-- without saying which rule failed is not the machine-readable evidence the
-- requirement asks for.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.gate_criterion_result (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    gate_decision_id    uuid NOT NULL,
    gate_criterion_id   uuid NOT NULL,
    comparison_id       uuid,
    verdict             text NOT NULL,
    rule_fired          text NOT NULL,
    detail              text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_gate_criterion_result__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_gate_criterion_result__decision
        FOREIGN KEY (organization_id, gate_decision_id)
        REFERENCES clep.gate_decision (organization_id, id),
    CONSTRAINT fk_gate_criterion_result__criterion
        FOREIGN KEY (organization_id, gate_criterion_id)
        REFERENCES clep.gate_criterion (organization_id, id),
    CONSTRAINT fk_gate_criterion_result__comparison
        FOREIGN KEY (organization_id, comparison_id)
        REFERENCES clep.comparison (organization_id, id),
    CONSTRAINT uq_gate_criterion_result__decision_criterion
        UNIQUE (organization_id, gate_decision_id, gate_criterion_id),
    CONSTRAINT ck_gate_criterion_result__verdict
        CHECK (verdict IN ('pass', 'hard_fail', 'warning', 'approval_required',
                           'insufficient_evidence', 'not_comparable')),
    CONSTRAINT ck_gate_criterion_result__rule_fired
        CHECK (rule_fired IN ('interval', 'absolute_floor', 'relative_tolerance',
                              'minimum_sample', 'precision_unset', 'comparability',
                              'no_signal'))
);

-- -----------------------------------------------------------------------------
-- policy_exception (REQ-F-09-6)
--
-- Audit class. Actor, justification and expiry are all NOT NULL because an
-- exception without any one of them is an unaccountable override.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.policy_exception (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    gate_decision_id  uuid NOT NULL,
    actor_id          uuid NOT NULL,
    justification     text NOT NULL,
    expires_at        timestamptz NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_policy_exception__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_policy_exception__decision
        FOREIGN KEY (organization_id, gate_decision_id)
        REFERENCES clep.gate_decision (organization_id, id),
    -- The contract requires at least 20 characters. "ok" is not a justification,
    -- and the store is where that stops being a suggestion.
    CONSTRAINT ck_policy_exception__justification_substantive
        CHECK (length(btrim(justification)) >= 20),
    CONSTRAINT ck_policy_exception__expires_after_creation
        CHECK (expires_at > created_at)
);

CREATE INDEX ix_policy_exception__decision
    ON clep.policy_exception (organization_id, gate_decision_id);

-- -----------------------------------------------------------------------------
-- Immutability of audit-class rows and of published policy versions
-- -----------------------------------------------------------------------------
CREATE FUNCTION clep.refuse_change_to_audit_record() RETURNS trigger
    LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        'REQ-N-COMP-1: % is audit-class; row % may not be modified or deleted',
        TG_TABLE_NAME, COALESCE(OLD.id, NEW.id)
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER trg_gate_decision__immutable
    BEFORE UPDATE OR DELETE ON clep.gate_decision
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_comparison__immutable
    BEFORE UPDATE OR DELETE ON clep.comparison
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_gate_criterion_result__immutable
    BEFORE UPDATE OR DELETE ON clep.gate_criterion_result
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

CREATE TRIGGER trg_policy_exception__immutable
    BEFORE UPDATE OR DELETE ON clep.policy_exception
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

-- A published policy version, or one any decision was made under, is frozen for
-- the same reason a prompt version is: the decision cites it, and a citation
-- that can be edited afterwards proves nothing.
CREATE FUNCTION clep.refuse_change_to_frozen_policy() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    subject_id        uuid := COALESCE(OLD.id, NEW.id);
    blocking_decision uuid;
BEGIN
    SELECT d.id INTO blocking_decision
    FROM clep.gate_decision d
    WHERE d.gate_policy_version_id = subject_id
    LIMIT 1;

    IF blocking_decision IS NOT NULL THEN
        RAISE EXCEPTION
            'REQ-F-09-8: gate policy version % governs decision % and is immutable',
            subject_id, blocking_decision
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF OLD.state = 'published' THEN
        RAISE EXCEPTION
            'a published gate policy version is immutable; % may not be modified '
            'or deleted', subject_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_gate_policy_version__immutable
    BEFORE UPDATE OR DELETE ON clep.gate_policy_version
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_frozen_policy();

-- A criterion belongs to its version. Freezing the version and leaving its
-- criteria editable would freeze the wrapper and not the contents.
CREATE FUNCTION clep.refuse_change_to_frozen_criterion() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    subject_id  uuid := COALESCE(OLD.id, NEW.id);
    version_id  uuid := COALESCE(OLD.gate_policy_version_id,
                                 NEW.gate_policy_version_id);
    frozen      boolean;
BEGIN
    SELECT (v.state = 'published')
             OR EXISTS (SELECT 1 FROM clep.gate_decision d
                        WHERE d.gate_policy_version_id = v.id)
    INTO frozen
    FROM clep.gate_policy_version v
    WHERE v.id = version_id;

    IF frozen THEN
        RAISE EXCEPTION
            'criterion % belongs to a frozen gate policy version and is immutable',
            subject_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_gate_criterion__immutable
    BEFORE UPDATE OR DELETE ON clep.gate_criterion
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_frozen_criterion();

-- -----------------------------------------------------------------------------
-- Row-level security. Every table in this file is tenant-scoped.
-- -----------------------------------------------------------------------------
ALTER TABLE clep.baseline               ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.baseline               FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.baseline
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.gate_policy            ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.gate_policy            FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.gate_policy
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.gate_policy_version    ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.gate_policy_version    FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.gate_policy_version
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.gate_criterion         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.gate_criterion         FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.gate_criterion
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.gate_decision          ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.gate_decision          FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.gate_decision
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.comparison             ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.comparison             FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.comparison
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.gate_criterion_result  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.gate_criterion_result  FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.gate_criterion_result
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.policy_exception       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.policy_exception       FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.policy_exception
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- -----------------------------------------------------------------------------
-- Runtime grants.
--
-- No DELETE anywhere. UPDATE only where a state machine requires it: a baseline
-- is approved and later superseded, and a draft policy version is published.
-- The audit-class tables are INSERT and SELECT only, and their triggers refuse
-- the rest even if a grant were added by mistake.
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT, UPDATE ON clep.baseline              TO clep_runtime;
GRANT SELECT, INSERT         ON clep.gate_policy           TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.gate_policy_version   TO clep_runtime;
GRANT SELECT, INSERT         ON clep.gate_criterion        TO clep_runtime;
GRANT SELECT, INSERT         ON clep.gate_decision         TO clep_runtime;
GRANT SELECT, INSERT         ON clep.comparison            TO clep_runtime;
GRANT SELECT, INSERT         ON clep.gate_criterion_result TO clep_runtime;
GRANT SELECT, INSERT         ON clep.policy_exception      TO clep_runtime;
