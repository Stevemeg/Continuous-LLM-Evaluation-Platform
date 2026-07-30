-- =============================================================================
-- 04 — Evaluation artifact management and audit
--
-- SPECIFICATION ARTIFACT. Implements artifact-model.md and ADR-013, plus the
-- audit integrity domain from ADR-011 and invariants I-33 through I-35.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- audit_event — the integrity domain (I-33, I-34, REQ-N-COMP-3)
--
-- N-12: audit tables are prefixed and grouped separately. The runtime role is
-- granted INSERT and SELECT only; UPDATE and DELETE are never granted, so an
-- actor cannot remove the record of their own action.
--
-- Retention is independent of tenant policy (I-34) and is enforced operationally
-- rather than by a column: nothing here expresses a tenant-settable expiry,
-- because offering one would invite lowering the floor.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.audit_event (
    id                     uuid PRIMARY KEY,
    organization_id        uuid NOT NULL REFERENCES clep.organization (id),
    actor_id               uuid NOT NULL,
    action                 text NOT NULL,
    target_type            text NOT NULL,
    target_id              uuid,
    target_content_digest  text,
    justification          text,
    occurred_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_audit_event__digest_form
        CHECK (target_content_digest IS NULL
               OR target_content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_audit_event__action_not_blank
        CHECK (length(btrim(action)) > 0)
);

CREATE INDEX ix_audit_event__organization_occurred_at
    ON clep.audit_event (organization_id, occurred_at DESC);
CREATE INDEX ix_audit_event__organization_target
    ON clep.audit_event (organization_id, target_type, target_id);

COMMENT ON TABLE clep.audit_event IS
    'Append-only. I-35: a governed action that cannot emit its audit event does '
    'not complete, so an unaudited action is indistinguishable from a concealed '
    'one and must not be possible.';

-- example_content.erasure_audit_id names the record that authorised an erasure.
-- Declared here because audit_event must exist first.
ALTER TABLE clep.example_content
    ADD CONSTRAINT fk_example_content__erasure_audit
    FOREIGN KEY (erasure_audit_id) REFERENCES clep.audit_event (id);

-- -----------------------------------------------------------------------------
-- artifact — metadata only; payloads live in the object store (ADR-013 O-1)
--
-- artifact_class carries the retention regime. gate_evidence is the single
-- audit-class class and is therefore never erasable; every other class is
-- content class and is destroyed on an erasure request.
--
-- source_content_digest is rule A-4: every content-derived artifact names the
-- example content it derives from, which is what makes REQ-N-PRIV-4 erasure an
-- indexed lookup rather than a scan of every artifact in the tenant.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.artifact (
    id                     uuid PRIMARY KEY,
    organization_id        uuid NOT NULL REFERENCES clep.organization (id),
    artifact_class         text NOT NULL,
    content_digest         text NOT NULL,
    payload_ref            text,
    byte_size              bigint NOT NULL,
    source_content_digest  text,
    correlation_id         text NOT NULL,
    erased_at              timestamptz,
    erasure_audit_id       uuid REFERENCES clep.audit_event (id),
    created_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_artifact__class
        CHECK (artifact_class IN ('input_snapshot', 'candidate_output',
                                  'retrieved_context', 'trajectory',
                                  'judge_rationale', 'evaluator_detail',
                                  'gate_evidence')),
    CONSTRAINT ck_artifact__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_artifact__source_digest_form
        CHECK (source_content_digest IS NULL
               OR source_content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_artifact__byte_size_non_negative
        CHECK (byte_size >= 0),
    CONSTRAINT ck_artifact__erasure_consistent
        CHECK ((erased_at IS NULL) = (payload_ref IS NOT NULL)),
    CONSTRAINT ck_artifact__erasure_audited
        CHECK ((erased_at IS NULL) = (erasure_audit_id IS NULL)),
    -- gate evidence is audit class and derived: it holds no erasable content and
    -- is therefore never erased and never derived from example content
    CONSTRAINT ck_artifact__gate_evidence_not_erasable
        CHECK (artifact_class <> 'gate_evidence'
               OR (erased_at IS NULL AND source_content_digest IS NULL)),
    -- content-derived classes must name their source so erasure can find them
    CONSTRAINT ck_artifact__content_classes_name_their_source
        CHECK (artifact_class = 'gate_evidence' OR source_content_digest IS NOT NULL),
    CONSTRAINT uq_artifact__organization_digest
        UNIQUE (organization_id, content_digest)
);

CREATE INDEX ix_artifact__organization_source_digest
    ON clep.artifact (organization_id, source_content_digest);
CREATE INDEX ix_artifact__organization_class
    ON clep.artifact (organization_id, artifact_class);

COMMENT ON CONSTRAINT ck_artifact__gate_evidence_not_erasable ON clep.artifact IS
    'Gate evidence must be simultaneously permanent (REQ-N-COMP-1) and free of '
    'erasable content (REQ-F-05-8). Embedding content would make it both '
    'undeletable and erasable, which is unsatisfiable. The constraint makes the '
    'contradiction impossible to introduce.';

COMMENT ON CONSTRAINT uq_artifact__organization_digest ON clep.artifact IS
    'Content addressing is scoped per tenant (ADR-013 O-2, rule A-7). Identical '
    'content in two tenants is stored twice, forgoing deduplication to avoid a '
    'shared object governed by two tenants deletion policies.';

-- -----------------------------------------------------------------------------
-- artifact_reference — reference counting for safe deletion (ADR-013 O-4)
--
-- Content addressing deduplicates within a tenant, so an artifact may be reached
-- by several producers. Deleting on the first removal would destroy an object
-- another run legitimately holds; the object is destroyed only when its last
-- reference goes.
--
-- referencing_type is text rather than a foreign key because the referencing
-- tables (run_sample, gate_decision) arrive with the evaluation harness in a
-- later phase. Forward-declaring them here would be specifying out of phase.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.artifact_reference (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    artifact_id       uuid NOT NULL,
    referencing_type  text NOT NULL,
    referencing_id    uuid NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_artifact_reference__artifact
        FOREIGN KEY (organization_id, artifact_id)
        REFERENCES clep.artifact (organization_id, id),
    CONSTRAINT ck_artifact_reference__referencing_type
        CHECK (referencing_type IN ('run_sample', 'gate_decision', 'dataset_version')),
    CONSTRAINT uq_artifact_reference__artifact_referencer
        UNIQUE (organization_id, artifact_id, referencing_type, referencing_id)
);

CREATE INDEX ix_artifact_reference__organization_artifact
    ON clep.artifact_reference (organization_id, artifact_id);

ALTER TABLE clep.artifact
    ADD CONSTRAINT uq_artifact__organization_id_id UNIQUE (organization_id, id);

-- -----------------------------------------------------------------------------
-- erasure_request — foreground, audited, verified (ADR-013 O-5)
--
-- Object-store lifecycle rules are not erasure: they have no completion signal,
-- no audit record and no per-object confirmation. REQ-N-PRIV-3 requires deletion
-- executable within a defined period and auditable, so erasure is a tracked
-- operation with an explicit state.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.erasure_request (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL REFERENCES clep.organization (id),
    requested_by_actor_id uuid NOT NULL,
    justification         text NOT NULL,
    state                 text NOT NULL DEFAULT 'accepted',
    is_override_used      boolean NOT NULL DEFAULT false,
    target_count          integer NOT NULL,
    verified_count        integer,
    audit_event_id        uuid NOT NULL REFERENCES clep.audit_event (id),
    requested_at          timestamptz NOT NULL DEFAULT now(),
    completed_at          timestamptz,
    CONSTRAINT ck_erasure_request__state
        CHECK (state IN ('accepted', 'demoting', 'destroying', 'verifying',
                         'completed', 'failed')),
    CONSTRAINT ck_erasure_request__completed_at_matches_state
        CHECK ((state IN ('completed', 'failed')) = (completed_at IS NOT NULL)),
    CONSTRAINT ck_erasure_request__verified_on_completion
        CHECK (state <> 'completed' OR verified_count = target_count),
    CONSTRAINT ck_erasure_request__target_count_positive
        CHECK (target_count > 0)
);

COMMENT ON CONSTRAINT ck_erasure_request__verified_on_completion ON clep.erasure_request IS
    'REQ-N-PRIV-4: an erasure is complete only when every targeted derivative is '
    'verified destroyed. Reporting completion without verification would tell a '
    'data subject their content was removed when it may not have been.';

COMMENT ON COLUMN clep.erasure_request.state IS
    'Ordered: demote before destroy. Destroying first would leave a window in '
    'which a run still claims reproducibility whose content is already gone.';

-- =============================================================================
-- Row-level security (ADR-012 D-1, D-2)
-- =============================================================================
ALTER TABLE clep.audit_event         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.audit_event         FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.artifact            ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.artifact            FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.artifact_reference  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.artifact_reference  FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.erasure_request     ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.erasure_request     FORCE  ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON clep.audit_event
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.artifact
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.artifact_reference
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.erasure_request
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

GRANT SELECT, INSERT, UPDATE, DELETE ON
    clep.artifact, clep.artifact_reference, clep.erasure_request
TO clep_runtime;

-- Audit is append-only for the runtime role. UPDATE and DELETE are deliberately
-- never granted (I-33): an actor must not be able to remove the record of their
-- own action.
GRANT SELECT, INSERT ON clep.audit_event TO clep_runtime;
