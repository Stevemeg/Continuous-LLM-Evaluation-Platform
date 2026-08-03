-- =============================================================================
-- 06 — Prompt/model/system registry, run identity, reproduction and cache
--
-- Phase 6. Applied by the migration runner, like 05: the specification and the
-- migration are one artifact and cannot drift apart.
--
-- This file only ADDS. Files 01–05 are already applied and their contents are
-- recorded by SHA-256; changing one is refused by the runner, so the two columns
-- Phase 5 left dangling — run_candidate.model_configuration_id and
-- prompt_version_id, uuids referencing tables that did not yet exist — acquire
-- their foreign keys here through ALTER rather than by editing history.
--
-- The registry exists to make REQ-F-07-1 possible. A run identity is only
-- meaningful if every element it names is itself a versioned, immutable thing;
-- a digest over mutable rows is a digest over nothing.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- prompt / prompt_version (REQ-F-01-1, REQ-F-01-6)
--
-- No credential, endpoint or key is stored anywhere in this file. The registry
-- records WHAT was used, never what would let someone use it.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.prompt (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    project_id       uuid NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_prompt__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_prompt__project_slug UNIQUE (organization_id, project_id, slug),
    CONSTRAINT uq_prompt__org_id UNIQUE (organization_id, id)
);

-- version_number, content_digest and body are the immutable payload once the
-- version is published. state is a constrained value rather than a boolean (N-7)
-- because a third state (deprecated) is foreseeable and a boolean would have to
-- be replaced rather than extended.
CREATE TABLE clep.prompt_version (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    prompt_id         uuid NOT NULL,
    version_number    integer NOT NULL,
    content_digest    text NOT NULL,
    body              text NOT NULL,
    state             text NOT NULL DEFAULT 'draft',
    created_by        uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    published_at      timestamptz,

    CONSTRAINT fk_prompt_version__prompt
        FOREIGN KEY (organization_id, prompt_id)
        REFERENCES clep.prompt (organization_id, id),
    CONSTRAINT ck_prompt_version__state CHECK (state IN ('draft', 'published')),
    CONSTRAINT ck_prompt_version__published_at_matches_state
        CHECK ((state = 'published') = (published_at IS NOT NULL)),
    CONSTRAINT ck_prompt_version__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_prompt_version__version_number_positive CHECK (version_number > 0),
    CONSTRAINT uq_prompt_version__prompt_version_number
        UNIQUE (organization_id, prompt_id, version_number),
    CONSTRAINT uq_prompt_version__org_id UNIQUE (organization_id, id)
);

COMMENT ON COLUMN clep.prompt_version.created_by IS
    'REQ-F-01-6: who changed the prompt. The when is created_at and the full '
    'history is in audit_event; this column makes the actor available without '
    'a join for the common case.';

-- -----------------------------------------------------------------------------
-- provider / model / model_configuration (REQ-F-02-2, REQ-F-02-4)
--
-- Provider is tenant-scoped rather than global. A self-hosted endpoint belongs
-- to the tenant that runs it (REQ-F-02-4 makes those first-class, not a special
-- case), and a global provider table would put one tenant's private endpoint
-- where another tenant could enumerate it.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.provider (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    endpoint_kind    text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT ck_provider__endpoint_kind
        CHECK (endpoint_kind IN ('hosted', 'self_hosted')),
    CONSTRAINT uq_provider__slug UNIQUE (organization_id, slug),
    CONSTRAINT uq_provider__org_id UNIQUE (organization_id, id)
);

COMMENT ON TABLE clep.provider IS
    'Provider identity for the run record. Deliberately carries no base_url, '
    'api key or credential of any kind: those are deployment configuration, '
    'they are read from the environment, and a registry row is a thing many '
    'people can read.';

CREATE TABLE clep.model (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    provider_id       uuid NOT NULL,
    model_identifier  text NOT NULL,
    display_name      text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_model__provider
        FOREIGN KEY (organization_id, provider_id)
        REFERENCES clep.provider (organization_id, id),
    CONSTRAINT uq_model__provider_identifier
        UNIQUE (organization_id, provider_id, model_identifier),
    CONSTRAINT uq_model__org_id UNIQUE (organization_id, id)
);

-- output_affecting_parameters is named for what it is, not `parameters`: a
-- parameter that cannot change the output does not belong in the run identity,
-- and one that can must never be left out of it. N-11 forbids an unqualified
-- `metadata`/`value` column and the reason applies here — the name has to say
-- which parameters, because the digest depends on the answer.
CREATE TABLE clep.model_configuration (
    id                            uuid PRIMARY KEY,
    organization_id               uuid NOT NULL,
    model_id                      uuid NOT NULL,
    version_number                integer NOT NULL,
    output_affecting_parameters   jsonb NOT NULL,
    content_digest                text NOT NULL,
    seed                          bigint,
    is_deterministic              boolean NOT NULL,
    state                         text NOT NULL DEFAULT 'draft',
    created_by                    uuid NOT NULL,
    created_at                    timestamptz NOT NULL DEFAULT now(),
    published_at                  timestamptz,

    CONSTRAINT fk_model_configuration__model
        FOREIGN KEY (organization_id, model_id)
        REFERENCES clep.model (organization_id, id),
    CONSTRAINT ck_model_configuration__state CHECK (state IN ('draft', 'published')),
    CONSTRAINT ck_model_configuration__published_at_matches_state
        CHECK ((state = 'published') = (published_at IS NOT NULL)),
    CONSTRAINT ck_model_configuration__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_model_configuration__parameters_are_an_object
        CHECK (jsonb_typeof(output_affecting_parameters) = 'object'),
    CONSTRAINT uq_model_configuration__model_version
        UNIQUE (organization_id, model_id, version_number),
    CONSTRAINT uq_model_configuration__org_id UNIQUE (organization_id, id)
);

COMMENT ON COLUMN clep.model_configuration.is_deterministic IS
    'Whether this configuration is claimed to produce the same output for the '
    'same input. Only a deterministic configuration may be cached: caching a '
    'sampled configuration would replace a draw from a distribution with one '
    'fixed draw, which is exactly the outcome change REQ-F-07-4 forbids.';

-- -----------------------------------------------------------------------------
-- system_definition / system_version
--
-- The "system" of Phase 6's title: the whole thing under test, which is a prompt
-- AND a model configuration AND (later) retrieval and tool configuration. A run
-- identity names a system version, so that adding retrieval configuration in
-- Phase 9 extends this table rather than reopening the identity model.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.system_definition (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    project_id       uuid NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    kind             text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_system_definition__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT ck_system_definition__kind
        CHECK (kind IN ('prompt', 'rag', 'agent')),
    CONSTRAINT uq_system_definition__project_slug
        UNIQUE (organization_id, project_id, slug),
    CONSTRAINT uq_system_definition__org_id UNIQUE (organization_id, id)
);

CREATE TABLE clep.system_version (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    system_definition_id     uuid NOT NULL,
    version_number           integer NOT NULL,
    prompt_version_id        uuid,
    model_configuration_id   uuid NOT NULL,
    content_digest           text NOT NULL,
    state                    text NOT NULL DEFAULT 'draft',
    created_by               uuid NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    published_at             timestamptz,

    CONSTRAINT fk_system_version__definition
        FOREIGN KEY (organization_id, system_definition_id)
        REFERENCES clep.system_definition (organization_id, id),
    CONSTRAINT fk_system_version__prompt_version
        FOREIGN KEY (organization_id, prompt_version_id)
        REFERENCES clep.prompt_version (organization_id, id),
    CONSTRAINT fk_system_version__model_configuration
        FOREIGN KEY (organization_id, model_configuration_id)
        REFERENCES clep.model_configuration (organization_id, id),
    CONSTRAINT ck_system_version__state CHECK (state IN ('draft', 'published')),
    CONSTRAINT ck_system_version__published_at_matches_state
        CHECK ((state = 'published') = (published_at IS NOT NULL)),
    CONSTRAINT ck_system_version__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT uq_system_version__definition_version
        UNIQUE (organization_id, system_definition_id, version_number),
    CONSTRAINT uq_system_version__org_id UNIQUE (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- experiment — the reproducible unit above a run (CAP-07)
--
-- A run answers "what happened". An experiment answers "what were we asking",
-- and groups the runs that answered it so a comparison has a subject.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.experiment (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    project_id       uuid NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    hypothesis       text,
    created_by       uuid NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT fk_experiment__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_experiment__project_slug
        UNIQUE (organization_id, project_id, slug),
    CONSTRAINT uq_experiment__org_id UNIQUE (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- run_identity_component — REQ-F-07-1 stored as rows, not as one opaque digest
--
-- Phase 5 recorded run.identity_digest and nothing else. A digest proves two
-- runs measured the same thing; it cannot tell you WHAT they measured, and
-- REQ-F-07-3 requires re-running from the captured identity and reporting the
-- elements that could not be reconstructed. That is impossible from a hash, so
-- the components are stored individually and the digest is derived from them.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.run_identity_component (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    run_id            uuid NOT NULL,
    component_kind    text NOT NULL,
    component_ref     text NOT NULL,
    component_digest  text NOT NULL,
    recorded_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_run_identity_component__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_run_identity_component__run
        FOREIGN KEY (organization_id, run_id)
        REFERENCES clep.run (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT ck_run_identity_component__kind
        CHECK (component_kind IN ('dataset_version', 'prompt_version',
                                  'model_configuration', 'system_version',
                                  'evaluator_version', 'seed', 'environment',
                                  'suite_version', 'integration_tier')),
    CONSTRAINT ck_run_identity_component__digest_form
        CHECK (component_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT uq_run_identity_component__run_kind_ref
        UNIQUE (organization_id, run_id, component_kind, component_ref)
);

-- -----------------------------------------------------------------------------
-- reproduction_attempt / reproduction_gap — REQ-F-07-3
--
-- The requirement is not "re-run it". It is "re-run it and report any element
-- that could not be reconstructed", which means a failed reproduction is a
-- first-class recorded outcome rather than an error, and each missing element is
-- named rather than summarised.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.reproduction_attempt (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    original_run_id  uuid NOT NULL,
    replay_run_id    uuid,
    outcome          text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_reproduction_attempt__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_reproduction_attempt__original
        FOREIGN KEY (organization_id, original_run_id)
        REFERENCES clep.run (organization_id, id),
    CONSTRAINT fk_reproduction_attempt__replay
        FOREIGN KEY (organization_id, replay_run_id)
        REFERENCES clep.run (organization_id, id),
    CONSTRAINT ck_reproduction_attempt__outcome
        CHECK (outcome IN ('reproducible', 'partially_reproducible',
                           'not_reproducible')),
    -- A run that could not be reproduced at all cannot have a replay run: there
    -- was nothing to execute. The converse does NOT hold, and the first version
    -- of this constraint wrongly asserted that it did — an `=` between the two
    -- sides, which forbade the ordinary case of an assessment that concluded
    -- "reproducible" without executing anything. Assessing whether a run can be
    -- reproduced and actually re-running it are separate acts, and only one
    -- direction of the implication is true.
    CONSTRAINT ck_reproduction_attempt__replay_matches_outcome
        CHECK (replay_run_id IS NULL OR outcome <> 'not_reproducible')
);

CREATE TABLE clep.reproduction_gap (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL,
    reproduction_attempt_id  uuid NOT NULL,
    component_kind           text NOT NULL,
    component_ref            text NOT NULL,
    reason                   text NOT NULL,

    CONSTRAINT uq_reproduction_gap__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_reproduction_gap__attempt
        FOREIGN KEY (organization_id, reproduction_attempt_id)
        REFERENCES clep.reproduction_attempt (organization_id, id) ON DELETE CASCADE,
    CONSTRAINT ck_reproduction_gap__reason
        CHECK (reason IN ('component_absent', 'content_erased',
                          'digest_mismatch', 'environment_changed')),
    CONSTRAINT uq_reproduction_gap__attempt_component
        UNIQUE (organization_id, reproduction_attempt_id, component_kind,
                component_ref)
);

-- -----------------------------------------------------------------------------
-- result_cache — REQ-F-07-4, correctness-aware
--
-- cache_key is a digest over every input that can change the output. A cache
-- that keys on less than that returns a result from a different question, and
-- REQ-F-07-4 is then false in the one direction nobody notices.
--
-- Non-deterministic configurations are refused below rather than cached, and
-- run_sample.is_served_from_cache records the fact for every sample.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.result_cache (
    id                      uuid PRIMARY KEY,
    organization_id         uuid NOT NULL,
    cache_key               text NOT NULL,
    model_configuration_id  uuid NOT NULL,
    output_text             text NOT NULL,
    output_digest           text NOT NULL,
    prompt_tokens           integer NOT NULL,
    completion_tokens       integer NOT NULL,
    created_at              timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT uq_result_cache__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_result_cache__model_configuration
        FOREIGN KEY (organization_id, model_configuration_id)
        REFERENCES clep.model_configuration (organization_id, id),
    CONSTRAINT uq_result_cache__key UNIQUE (organization_id, cache_key),
    CONSTRAINT ck_result_cache__key_form
        CHECK (cache_key ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_result_cache__digest_form
        CHECK (output_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_result_cache__tokens_non_negative
        CHECK (prompt_tokens >= 0 AND completion_tokens >= 0)
);

-- -----------------------------------------------------------------------------
-- The two columns Phase 5 could not point at anything
-- -----------------------------------------------------------------------------
ALTER TABLE clep.run_candidate
    ADD CONSTRAINT fk_run_candidate__model_configuration
        FOREIGN KEY (organization_id, model_configuration_id)
        REFERENCES clep.model_configuration (organization_id, id),
    ADD CONSTRAINT fk_run_candidate__prompt_version
        FOREIGN KEY (organization_id, prompt_version_id)
        REFERENCES clep.prompt_version (organization_id, id),
    ADD COLUMN system_version_id uuid,
    ADD CONSTRAINT fk_run_candidate__system_version
        FOREIGN KEY (organization_id, system_version_id)
        REFERENCES clep.system_version (organization_id, id);

ALTER TABLE clep.run
    ADD COLUMN experiment_id uuid,
    ADD CONSTRAINT fk_run__experiment
        FOREIGN KEY (organization_id, experiment_id)
        REFERENCES clep.experiment (organization_id, id);

-- -----------------------------------------------------------------------------
-- Immutability (REQ-F-01-1)
--
-- Three rules, enforced by the store rather than by the application:
--
--   1. A published version cannot be modified in any column, and cannot be
--      deleted. draft -> published is therefore one-way as a consequence rather
--      than as a separate rule.
--   2. A version referenced by a COMPLETED run cannot change or be deleted,
--      whatever its state. This is REQ-F-01-1 verbatim, and it is stricter than
--      rule 1 in the case that matters: the run is the evidence, and evidence
--      whose inputs can still move is not evidence.
--
-- A draft stays mutable, which is the whole point of a draft; the application is
-- granted no DELETE on these tables in any case.
--
-- The function is SECURITY INVOKER, so the lookup runs under row-level security
-- and sees only the acting tenant's runs. That is correct rather than a
-- limitation: a version is tenant-scoped and the composite foreign keys make it
-- impossible for another tenant's run to reference it, so there is nothing
-- outside the current tenant that this query needs to see.
-- -----------------------------------------------------------------------------
CREATE FUNCTION clep.refuse_change_to_frozen_version() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    subject_id   uuid := COALESCE(OLD.id, NEW.id);
    blocking_run uuid;
BEGIN
    IF TG_TABLE_NAME = 'prompt_version' THEN
        SELECT r.id INTO blocking_run
        FROM clep.run_candidate rc
        JOIN clep.run r
          ON r.organization_id = rc.organization_id AND r.id = rc.run_id
        WHERE rc.prompt_version_id = subject_id
          AND r.execution_state = 'terminal'
        LIMIT 1;
    ELSE
        SELECT r.id INTO blocking_run
        FROM clep.run_candidate rc
        JOIN clep.run r
          ON r.organization_id = rc.organization_id AND r.id = rc.run_id
        WHERE rc.model_configuration_id = subject_id
          AND r.execution_state = 'terminal'
        LIMIT 1;
    END IF;

    IF blocking_run IS NOT NULL THEN
        RAISE EXCEPTION
            'REQ-F-01-1: % % is referenced by completed run % and is immutable',
            TG_TABLE_NAME, subject_id, blocking_run
            USING ERRCODE = 'restrict_violation';
    END IF;

    -- Published means immutable, and immutable means every column and the row's
    -- existence, not a list of columns. The first version of this trigger
    -- enumerated content_digest, version_number and published_at, and probing it
    -- against a real database showed the hole immediately: `body` was not in the
    -- list, so a published version's text could be replaced while its digest
    -- stayed the same and the digest quietly stopped describing the content.
    -- A list of columns is wrong the moment a column is added; the rule is not.
    IF OLD.state = 'published' THEN
        RAISE EXCEPTION
            'a published % is immutable; % may not be modified or deleted',
            TG_TABLE_NAME, subject_id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_prompt_version__immutable
    BEFORE UPDATE OR DELETE ON clep.prompt_version
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_frozen_version();

CREATE TRIGGER trg_model_configuration__immutable
    BEFORE UPDATE OR DELETE ON clep.model_configuration
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_frozen_version();

-- -----------------------------------------------------------------------------
-- Cache correctness (REQ-F-07-4)
--
-- A row may only enter the cache if the configuration that produced it claims
-- determinism. Enforced here because the application is not the only thing that
-- can write to this table, and because "we will remember not to" is how caches
-- start returning answers to different questions.
-- -----------------------------------------------------------------------------
CREATE FUNCTION clep.refuse_cache_of_nondeterministic_result() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    deterministic boolean;
BEGIN
    SELECT mc.is_deterministic INTO deterministic
    FROM clep.model_configuration mc
    WHERE mc.organization_id = NEW.organization_id
      AND mc.id = NEW.model_configuration_id;

    IF deterministic IS NOT TRUE THEN
        RAISE EXCEPTION
            'REQ-F-07-4: model configuration % is not deterministic and its '
            'results may not be cached', NEW.model_configuration_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_result_cache__deterministic_only
    BEFORE INSERT OR UPDATE ON clep.result_cache
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_cache_of_nondeterministic_result();

-- -----------------------------------------------------------------------------
-- Row-level security. Every table in this file is tenant-scoped; there is no
-- global-scope exception here, unlike evaluator_definition in file 03.
-- -----------------------------------------------------------------------------
ALTER TABLE clep.prompt                  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.prompt                  FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.prompt
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.prompt_version          ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.prompt_version          FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.prompt_version
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.provider                ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.provider                FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.provider
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.model                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.model                   FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.model
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.model_configuration     ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.model_configuration     FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.model_configuration
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.system_definition       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.system_definition       FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.system_definition
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.system_version          ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.system_version          FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.system_version
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.experiment              ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.experiment              FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.experiment
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.run_identity_component  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.run_identity_component  FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.run_identity_component
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.reproduction_attempt    ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.reproduction_attempt    FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.reproduction_attempt
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.reproduction_gap        ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.reproduction_gap        FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.reproduction_gap
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.result_cache            ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.result_cache            FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.result_cache
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- -----------------------------------------------------------------------------
-- Runtime grants.
--
-- No DELETE anywhere in this file. A registry whose rows can be deleted by the
-- application cannot support REQ-F-01-1, and run identity that can be deleted is
-- not identity. UPDATE is granted only where a lifecycle needs it — publishing a
-- version — and the triggers above constrain what that UPDATE may do.
-- -----------------------------------------------------------------------------
GRANT SELECT, INSERT         ON clep.prompt                 TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.prompt_version         TO clep_runtime;
GRANT SELECT, INSERT         ON clep.provider               TO clep_runtime;
GRANT SELECT, INSERT         ON clep.model                  TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.model_configuration    TO clep_runtime;
GRANT SELECT, INSERT         ON clep.system_definition      TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.system_version         TO clep_runtime;
GRANT SELECT, INSERT         ON clep.experiment             TO clep_runtime;
GRANT SELECT, INSERT         ON clep.run_identity_component TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.reproduction_attempt   TO clep_runtime;
GRANT SELECT, INSERT         ON clep.reproduction_gap       TO clep_runtime;
GRANT SELECT, INSERT         ON clep.result_cache           TO clep_runtime;
