-- =============================================================================
-- 03 — Benchmark Registry foundation
--
-- SPECIFICATION ARTIFACT. Implements domain-model.md section 5 and invariants
-- I-12, I-13, I-14.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- benchmark_suite — reusable, versioned definition (REQ-F-06-1, REQ-F-06-2)
-- Ownership is recorded on the suite and on every version.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.benchmark_suite (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    project_id       uuid NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    owner_actor_id   uuid NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_benchmark_suite__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_benchmark_suite__project_slug
        UNIQUE (organization_id, project_id, slug),
    CONSTRAINT uq_benchmark_suite__organization_id_id UNIQUE (organization_id, id)
);

-- -----------------------------------------------------------------------------
-- suite_version — frozen once used by an approved baseline (I-12, REQ-F-06-5)
--
-- is_frozen is a genuine two-state property, so a boolean is correct here under
-- N-7. It is set when a baseline first pins the version and is never unset;
-- unfreezing would retroactively change what a past run measured.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.suite_version (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    benchmark_suite_id  uuid NOT NULL,
    version_number      integer NOT NULL,
    content_digest      text NOT NULL,
    owner_actor_id      uuid NOT NULL,
    is_frozen           boolean NOT NULL DEFAULT false,
    frozen_at           timestamptz,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_suite_version__benchmark_suite
        FOREIGN KEY (organization_id, benchmark_suite_id)
        REFERENCES clep.benchmark_suite (organization_id, id),
    CONSTRAINT ck_suite_version__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_suite_version__frozen_at_matches_flag
        CHECK (is_frozen = (frozen_at IS NOT NULL)),
    CONSTRAINT uq_suite_version__suite_version_number
        UNIQUE (organization_id, benchmark_suite_id, version_number),
    CONSTRAINT uq_suite_version__organization_id_id UNIQUE (organization_id, id)
);

COMMENT ON COLUMN clep.suite_version.is_frozen IS
    'Set when an approved baseline first pins this version (I-12). Never unset: '
    'unfreezing would retroactively change what a past run measured.';

-- -----------------------------------------------------------------------------
-- suite_member — the dataset versions a suite evaluates against
--
-- Composite foreign keys on BOTH sides keep the suite and the dataset version
-- inside one tenant (P-5). Without this a suite could reference another
-- tenant's dataset version and no single-table policy would reject it.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.suite_member (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    suite_version_id    uuid NOT NULL,
    dataset_version_id  uuid NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_suite_member__suite_version
        FOREIGN KEY (organization_id, suite_version_id)
        REFERENCES clep.suite_version (organization_id, id),
    CONSTRAINT fk_suite_member__dataset_version
        FOREIGN KEY (organization_id, dataset_version_id)
        REFERENCES clep.dataset_version (organization_id, id),
    CONSTRAINT uq_suite_member__version_dataset
        UNIQUE (organization_id, suite_version_id, dataset_version_id)
);

-- -----------------------------------------------------------------------------
-- suite_grant — sharing within a tenant only (I-13, REQ-F-06-4)
--
-- There is deliberately no target_organization_id column. Cross-tenant sharing
-- is not merely rejected, it is unrepresentable: the schema offers nowhere to
-- put another tenant's identifier.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.suite_grant (
    id                 uuid PRIMARY KEY,
    organization_id    uuid NOT NULL,
    suite_version_id   uuid NOT NULL,
    target_project_id  uuid NOT NULL,
    granted_by_actor_id uuid NOT NULL,
    justification      text NOT NULL,
    granted_at         timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_suite_grant__suite_version
        FOREIGN KEY (organization_id, suite_version_id)
        REFERENCES clep.suite_version (organization_id, id),
    CONSTRAINT fk_suite_grant__target_project
        FOREIGN KEY (organization_id, target_project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT uq_suite_grant__version_target
        UNIQUE (organization_id, suite_version_id, target_project_id)
);

COMMENT ON TABLE clep.suite_grant IS
    'I-13. Both foreign keys carry organization_id, so a grant cannot leave the '
    'tenant. No column exists for a foreign organization.';

-- -----------------------------------------------------------------------------
-- evaluator_definition / evaluator_version (REQ-F-AG-7, I-14)
--
-- scope distinguishes built-in evaluators from tenant-authored ones. Built-ins
-- carry a NULL organization_id and are the enumerated global exception under
-- ADR-010 rule 4; custom evaluators are tenant-scoped.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.evaluator_definition (
    id               uuid PRIMARY KEY,
    organization_id  uuid,
    scope            text NOT NULL,
    slug             text NOT NULL,
    display_name     text NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_evaluator_definition__scope
        CHECK (scope IN ('builtin', 'custom')),
    CONSTRAINT ck_evaluator_definition__scope_matches_tenancy
        CHECK ((scope = 'builtin') = (organization_id IS NULL)),
    CONSTRAINT uq_evaluator_definition__organization_id_id UNIQUE (organization_id, id)
);

CREATE UNIQUE INDEX uq_evaluator_definition__builtin_slug
    ON clep.evaluator_definition (slug) WHERE organization_id IS NULL;
CREATE UNIQUE INDEX uq_evaluator_definition__custom_slug
    ON clep.evaluator_definition (organization_id, slug) WHERE organization_id IS NOT NULL;

CREATE TABLE clep.evaluator_version (
    id                       uuid PRIMARY KEY,
    organization_id          uuid,
    evaluator_definition_id  uuid NOT NULL,
    version_number           integer NOT NULL,
    content_digest           text NOT NULL,
    input_schema_ref         text NOT NULL,
    output_schema_ref        text NOT NULL,
    declared_permissions     text NOT NULL,
    is_deterministic         boolean NOT NULL,
    cost_class               text NOT NULL,
    created_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_evaluator_version__definition
        FOREIGN KEY (organization_id, evaluator_definition_id)
        REFERENCES clep.evaluator_definition (organization_id, id),
    CONSTRAINT ck_evaluator_version__digest_form
        CHECK (content_digest ~ '^sha256:[0-9a-f]{64}$'),
    CONSTRAINT ck_evaluator_version__cost_class
        CHECK (cost_class IN ('free', 'compute', 'model_call')),
    CONSTRAINT uq_evaluator_version__definition_version
        UNIQUE (evaluator_definition_id, version_number),
    CONSTRAINT uq_evaluator_version__organization_id_id UNIQUE (organization_id, id)
);

COMMENT ON COLUMN clep.evaluator_version.is_deterministic IS
    'Deterministic evaluators and probabilistic judges are structurally '
    'separate (REQ-F-08-6). This flag is declared, and I-14 requires observed '
    'behaviour to be validated against the declaration rather than trusted.';

-- -----------------------------------------------------------------------------
-- suite_evaluator — evaluator versions bound into a suite version
-- -----------------------------------------------------------------------------
CREATE TABLE clep.suite_evaluator (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL,
    suite_version_id      uuid NOT NULL,
    evaluator_version_id  uuid NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_suite_evaluator__suite_version
        FOREIGN KEY (organization_id, suite_version_id)
        REFERENCES clep.suite_version (organization_id, id),
    CONSTRAINT uq_suite_evaluator__version_evaluator
        UNIQUE (organization_id, suite_version_id, evaluator_version_id)
);

-- -----------------------------------------------------------------------------
-- threshold — per-metric absolute and relative bounds (REQ-F-08-1)
--
-- numeric, never floating point (N-9): a gate decision must not turn on binary
-- representation error.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.threshold (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL,
    suite_version_id    uuid NOT NULL,
    metric_key          text NOT NULL,
    absolute_floor      numeric(18, 9),
    relative_tolerance  numeric(18, 9),
    direction           text NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT fk_threshold__suite_version
        FOREIGN KEY (organization_id, suite_version_id)
        REFERENCES clep.suite_version (organization_id, id),
    CONSTRAINT ck_threshold__direction
        CHECK (direction IN ('higher_is_better', 'lower_is_better')),
    CONSTRAINT ck_threshold__at_least_one_bound
        CHECK (absolute_floor IS NOT NULL OR relative_tolerance IS NOT NULL),
    CONSTRAINT uq_threshold__version_metric
        UNIQUE (organization_id, suite_version_id, metric_key)
);

-- =============================================================================
-- Row-level security (ADR-012 D-1, D-2)
-- =============================================================================
ALTER TABLE clep.benchmark_suite  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.benchmark_suite  FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.suite_version    ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.suite_version    FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.suite_member     ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.suite_member     FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.suite_grant      ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.suite_grant      FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.suite_evaluator  ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.suite_evaluator  FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.threshold        ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.threshold        FORCE  ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON clep.benchmark_suite
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.suite_version
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.suite_member
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.suite_grant
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.suite_evaluator
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.threshold
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- Custom evaluator rows are tenant-scoped; built-ins are globally readable and
-- are never writable by the runtime role.
ALTER TABLE clep.evaluator_definition ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.evaluator_definition FORCE  ROW LEVEL SECURITY;
ALTER TABLE clep.evaluator_version    ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.evaluator_version    FORCE  ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON clep.evaluator_definition
    USING (organization_id IS NULL OR organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());
CREATE POLICY tenant_isolation ON clep.evaluator_version
    USING (organization_id IS NULL OR organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- The asymmetry is deliberate: built-ins are readable by every tenant, and the
-- WITH CHECK clause has no NULL branch, so the runtime role cannot create or
-- modify one.

GRANT SELECT, INSERT, UPDATE, DELETE ON
    clep.benchmark_suite, clep.suite_version, clep.suite_member,
    clep.suite_evaluator, clep.threshold,
    clep.evaluator_definition, clep.evaluator_version
TO clep_runtime;

-- Grants are audit class: recorded, never rewritten.
GRANT SELECT, INSERT ON clep.suite_grant TO clep_runtime;
