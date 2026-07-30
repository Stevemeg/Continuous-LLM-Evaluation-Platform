-- =============================================================================
-- 02 — Organizations, projects, and the Golden Dataset foundation
--
-- SPECIFICATION ARTIFACT. Implements domain-model.md sections 1, 2 and 3, and
-- the invariants I-1, I-5 through I-10.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- organization — the tenant root (I-1)
-- Not tenant-scoped: it *is* the tenant.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.organization (
    id            uuid PRIMARY KEY,
    slug          text NOT NULL,
    display_name  text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_organization__slug UNIQUE (slug)
);

-- -----------------------------------------------------------------------------
-- project — ownership boundary (I-5)
--
-- A project cannot be moved between organizations: moving would silently
-- re-tenant every dependent record. Enforced by making organization_id part of
-- the key that children reference (P-5).
-- -----------------------------------------------------------------------------
CREATE TABLE clep.project (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES clep.organization (id),
    slug             text NOT NULL,
    display_name     text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_project__organization_slug UNIQUE (organization_id, slug),
    -- referenced by composite foreign keys so children cannot cross tenants
    CONSTRAINT uq_project__organization_id_id UNIQUE (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- dataset — logical container
-- -----------------------------------------------------------------------------
CREATE TABLE clep.dataset (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    project_id       uuid NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_dataset__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_dataset__project_slug UNIQUE (organization_id, project_id, slug),
    CONSTRAINT uq_dataset__organization_id_id UNIQUE (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- dataset_version — the immutability boundary (I-6, I-7)
--
-- state is a constrained value, never a boolean (N-7, N-8): a released version
-- differs from a draft in kind, and a third state may be needed later.
--
-- released_at is NULL exactly when state is 'draft'. Enforced rather than
-- documented, because a released version with no release time cannot be audited.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.dataset_version (
    id                uuid PRIMARY KEY,
    organization_id   uuid NOT NULL,
    dataset_id        uuid NOT NULL,
    version_number    integer NOT NULL,
    content_digest    text NOT NULL,
    schema_ref        text NOT NULL,
    state             text NOT NULL DEFAULT 'draft',
    example_count     integer NOT NULL DEFAULT 0,
    created_at        timestamptz NOT NULL DEFAULT now(),
    released_at       timestamptz,
    CONSTRAINT fk_dataset_version__dataset
        FOREIGN KEY (organization_id, dataset_id)
        REFERENCES clep.dataset (organization_id, id),
    CONSTRAINT ck_dataset_version__state
        CHECK (state IN ('draft', 'released')),
    CONSTRAINT ck_dataset_version__released_at_matches_state
        CHECK ((state = 'released') = (released_at IS NOT NULL)),
    CONSTRAINT ck_dataset_version__content_digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_dataset_version__example_count_non_negative
        CHECK (example_count >= 0),
    CONSTRAINT uq_dataset_version__dataset_version_number
        UNIQUE (organization_id, dataset_id, version_number),
    CONSTRAINT uq_dataset_version__organization_id_id UNIQUE (organization_id, id)
);

COMMENT ON COLUMN clep.dataset_version.state IS
    'draft is mutable; released is an immutable snapshot (I-6). The transition '
    'requires a recorded approval (I-7) and is one-way.';

-- -----------------------------------------------------------------------------
-- example — the RECORD. Survives erasure (I-8).
-- -----------------------------------------------------------------------------
CREATE TABLE clep.example (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    dataset_version_id  uuid NOT NULL,
    ordinal             integer NOT NULL,
    split               text,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_example__dataset_version
        FOREIGN KEY (organization_id, dataset_version_id)
        REFERENCES clep.dataset_version (organization_id, id),
    CONSTRAINT ck_example__split
        CHECK (split IS NULL OR split IN ('train', 'validation', 'test', 'holdout')),
    CONSTRAINT uq_example__version_ordinal
        UNIQUE (organization_id, dataset_version_id, ordinal),
    CONSTRAINT uq_example__organization_id_id UNIQUE (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- example_content — the PAYLOAD. Separately destructible (ADR-005, I-8).
--
-- The nullable payload_ref is the entire point of the ADR-005 split: erasure
-- sets it NULL and records erased_at, leaving the example record and every
-- decision that referenced it intact.
--
-- Content lives in the object store (ADR-013 O-1); this table holds only the
-- reference and the digest.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.example_content (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    example_id       uuid NOT NULL,
    content_digest   text NOT NULL,
    payload_ref      text,
    byte_size        bigint NOT NULL,
    erased_at        timestamptz,
    erasure_audit_id uuid,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_example_content__example
        FOREIGN KEY (organization_id, example_id)
        REFERENCES clep.example (organization_id, id),
    CONSTRAINT ck_example_content__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_example_content__byte_size_non_negative
        CHECK (byte_size >= 0),
    -- erased content has no payload, and present payload is not erased
    CONSTRAINT ck_example_content__erasure_consistent
        CHECK ((erased_at IS NULL) = (payload_ref IS NOT NULL)),
    -- an erasure must name the audit record that authorised it (REQ-N-PRIV-3)
    CONSTRAINT ck_example_content__erasure_audited
        CHECK ((erased_at IS NULL) = (erasure_audit_id IS NULL)),
    CONSTRAINT uq_example_content__example UNIQUE (organization_id, example_id),
    CONSTRAINT uq_example_content__organization_id_id UNIQUE (organization_id, id)
);

CREATE INDEX ix_example_content__organization_digest
    ON clep.example_content (organization_id, content_digest);

COMMENT ON TABLE clep.example_content IS
    'ADR-005 content/record split. Erasure nulls payload_ref and stamps '
    'erased_at; the example record and every referencing decision survive, and '
    'affected runs are demoted from reproducible to auditable.';

-- -----------------------------------------------------------------------------
-- dataset_label (N-11: not a bare `value` column)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.dataset_label (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    example_id       uuid NOT NULL,
    label_key        text NOT NULL,
    label_value      text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_dataset_label__example
        FOREIGN KEY (organization_id, example_id)
        REFERENCES clep.example (organization_id, id),
    CONSTRAINT uq_dataset_label__example_key
        UNIQUE (organization_id, example_id, label_key)
);

-- -----------------------------------------------------------------------------
-- dataset_lineage — provenance and usage terms (REQ-F-05-2)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.dataset_lineage (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    dataset_version_id  uuid NOT NULL,
    source_description  text NOT NULL,
    provenance          text NOT NULL,
    usage_terms         text,
    recorded_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_dataset_lineage__dataset_version
        FOREIGN KEY (organization_id, dataset_version_id)
        REFERENCES clep.dataset_version (organization_id, id),
    CONSTRAINT uq_dataset_lineage__dataset_version
        UNIQUE (organization_id, dataset_version_id)
);

-- -----------------------------------------------------------------------------
-- quality_check_result — must run before approval (REQ-F-05-6)
--
-- A blocking finding prevents release. Severity is constrained, not free text,
-- so a typo cannot silently downgrade a blocking finding to advisory.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.quality_check_result (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    dataset_version_id  uuid NOT NULL,
    check_kind          text NOT NULL,
    severity            text NOT NULL,
    detail              text NOT NULL,
    example_id          uuid,
    detected_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_quality_check_result__dataset_version
        FOREIGN KEY (organization_id, dataset_version_id)
        REFERENCES clep.dataset_version (organization_id, id),
    CONSTRAINT ck_quality_check_result__kind
        CHECK (check_kind IN ('duplicate', 'leakage', 'malformed', 'stale', 'contamination')),
    CONSTRAINT ck_quality_check_result__severity
        CHECK (severity IN ('blocking', 'advisory'))
);

CREATE INDEX ix_quality_check_result__version_severity
    ON clep.quality_check_result (organization_id, dataset_version_id, severity);

-- -----------------------------------------------------------------------------
-- dataset_approval — audit class, append-only (I-7, REQ-F-12-7)
-- -----------------------------------------------------------------------------
CREATE TABLE clep.dataset_approval (
    id                     uuid PRIMARY KEY,
    organization_id        uuid NOT NULL,
    dataset_version_id     uuid NOT NULL,
    actor_id               uuid NOT NULL,
    target_content_digest  text NOT NULL,
    justification          text NOT NULL,
    approved_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_dataset_approval__dataset_version
        FOREIGN KEY (organization_id, dataset_version_id)
        REFERENCES clep.dataset_version (organization_id, id),
    CONSTRAINT ck_dataset_approval__digest_form
        CHECK (target_content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT uq_dataset_approval__dataset_version
        UNIQUE (organization_id, dataset_version_id)
);

COMMENT ON COLUMN clep.dataset_approval.target_content_digest IS
    'The digest approved. Recording it means an approval cannot be silently '
    'transferred to different content.';

-- =============================================================================
-- Row-level security — ADR-012 D-1 and D-2
--
-- ENABLE alone is insufficient: table owners bypass policies by default. FORCE
-- is what makes the guarantee hold, and it is applied to every tenant-scoped
-- table without exception.
-- =============================================================================
ALTER TABLE clep.project               ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.project               FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.dataset               ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.dataset               FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.dataset_version       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.dataset_version       FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.example               ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.example               FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.example_content       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.example_content       FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.dataset_label         ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.dataset_label         FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.dataset_lineage       ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.dataset_lineage       FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.quality_check_result  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.quality_check_result  FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.dataset_approval      ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.dataset_approval      FORCE  ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON clep.project
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.dataset
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.dataset_version
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.example
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.example_content
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.dataset_label
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.dataset_lineage
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.quality_check_result
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.dataset_approval
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- WITH CHECK on every policy is what prevents a cross-tenant WRITE. USING alone
-- would filter reads while permitting an insert stamped with another tenant's id.

GRANT SELECT, INSERT, UPDATE, DELETE ON
    clep.project, clep.dataset, clep.dataset_version, clep.example,
    clep.example_content, clep.dataset_label, clep.dataset_lineage,
    clep.quality_check_result
TO clep_runtime;

-- Approvals are audit class: insert and read only, never modified or removed
-- by the role that creates them (REQ-N-COMP-3).
GRANT SELECT, INSERT ON clep.dataset_approval TO clep_runtime;
GRANT SELECT ON clep.organization TO clep_runtime;
