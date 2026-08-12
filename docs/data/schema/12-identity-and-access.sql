-- =============================================================================
-- 12 — Principals, credentials, authorization, and tenant governance policy
--
-- SPECIFICATION ARTIFACT. Implements domain-model.md section 1 (Organizations,
-- identity, access) and invariants I-2, I-3, I-4, together with the parts of
-- section 10 that Phase 12 makes load-bearing (EvaluatorInvocation).
--
-- Realises ADR-019 (credential verification), ADR-020 (authorization model),
-- ADR-021 (rate limits and quotas), within ADR-010 unchanged.
--
-- Until this file, every row-level security policy in the schema was correct and
-- every one of them was evaluated against a tenant the caller had simply
-- asserted. The policies did not change. What changes here is that the tenant
-- context is now derived from a credential the store can verify.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- app_user — global identity, tenant-scoped visibility
--
-- Global by ADR-010 rule 4 and data-model.md P-4, which enumerate `user` among
-- the exceptions: one person may hold memberships in several organizations, so
-- the identity cannot carry one organization_id.
--
-- Named app_user rather than user because `user` is a reserved word in SQL and
-- `clep."user"` would have to be quoted at every use site — a quoting rule that
-- is remembered until it is not.
--
-- Global does NOT mean world-readable, and this is the one place that
-- distinction is load-bearing. ADR-010 rule 4's examples are role definitions
-- and feature flags — data with no confidentiality. A user directory is not
-- that: it carries the subject identifiers of real people. So the row carries no
-- organization_id, and its VISIBILITY is derived from membership. A tenant sees
-- the users who are members of it and no others, which is what a tenant column
-- would have given if a tenant column had been expressible.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.app_user (
    id                uuid PRIMARY KEY,
    external_subject  text NOT NULL,
    display_name      text NOT NULL,
    created_at        timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_app_user__external_subject UNIQUE (external_subject),
    CONSTRAINT ck_app_user__external_subject_not_blank
        CHECK (length(btrim(external_subject)) > 0)
);

COMMENT ON TABLE clep.app_user IS
    'Global identity under ADR-010 rule 4. Visibility is scoped by membership '
    'rather than by an organization_id column, because a user directory is not '
    'the low-confidentiality kind of global data rule 4 had in mind.';

-- -----------------------------------------------------------------------------
-- role and role_permission — the global catalogue (ADR-020 rule 3)
--
-- A role is a named permission set. Genuinely global, genuinely
-- low-confidentiality, and — unlike app_user — readable by every tenant: this is
-- the case ADR-010 rule 4 was written for.
--
-- Seeded by migration and read-only at runtime. There is no INSERT grant and no
-- write policy, so a tenant cannot mint a role carrying permissions nobody
-- reviewed. ADR-020 defers tenant-defined roles for exactly that reason.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.role (
    slug          text PRIMARY KEY,
    display_name  text NOT NULL,
    description   text NOT NULL,
    CONSTRAINT ck_role__slug_form CHECK (slug ~ '^[a-z][a-z_]*$')
);

CREATE TABLE clep.role_permission (
    role_slug   text NOT NULL REFERENCES clep.role (slug),
    permission  text NOT NULL,
    CONSTRAINT pk_role_permission PRIMARY KEY (role_slug, permission),
    -- The closed vocabulary of ADR-020 rule 1. It is spelled out here rather
    -- than left to the application, so a permission the platform does not
    -- recognise cannot be granted by writing a row. The same set is declared in
    -- the API contract and mirrored in code; the phase validator compares all
    -- three as sets rather than searching for names.
    CONSTRAINT ck_role_permission__vocabulary CHECK (permission IN (
        'run:create', 'run:read', 'run:cancel', 'run:reproduce',
        'dataset:read', 'dataset:write', 'dataset:approve', 'dataset:erase',
        'prompt:read', 'prompt:write', 'prompt:publish',
        'experiment:write',
        'baseline:create', 'baseline:approve',
        'gate:configure', 'gate:evaluate', 'gate:read', 'gate:except',
        'judge:configure', 'judge:read', 'escalation:review',
        'plan:read', 'plan:draft', 'plan:accept',
        'memory:read',
        'schedule:write', 'release:observe',
        'analytics:read',
        'alert:configure', 'alert:read', 'alert:evaluate',
        'audit:read', 'credential:manage', 'role:grant',
        'governance:configure'))
);

COMMENT ON CONSTRAINT ck_role_permission__vocabulary ON clep.role_permission IS
    'ADR-020 rule 1. A permission outside this set is not storable, so a role '
    'cannot be given an authority the platform has no enforcement point for.';

-- The catalogue itself. Five roles, each with a stated reason to exist. A sixth
-- would need a governed action none of these should hold.
INSERT INTO clep.role (slug, display_name, description) VALUES
    ('owner',      'Owner',
     'Full authority, including granting roles, managing credentials, setting '
     'governance policy, and requesting erasure.'),
    ('maintainer', 'Maintainer',
     'Every operational action: runs, prompts, baselines, gates, judges, plans, '
     'schedules and alerts. Cannot grant roles, manage credentials, change '
     'governance policy, or erase content.'),
    ('analyst',    'Analyst',
     'Reads evidence and starts runs. Approves nothing and configures nothing.'),
    ('auditor',    'Auditor',
     'Reads everything, including the audit trail. Writes nothing at all — the '
     'role REQ-N-COMP-1 exists for.'),
    ('service',    'Service account',
     'A CI credential: submit a run, read it, evaluate a gate, read the result. '
     'Deliberately cannot approve a baseline or waive a policy, because a '
     'pipeline that can waive its own gate is not a gate.');

INSERT INTO clep.role_permission (role_slug, permission)
SELECT 'owner', p FROM (VALUES
    ('run:create'), ('run:read'), ('run:cancel'), ('run:reproduce'),
    ('dataset:read'), ('dataset:write'), ('dataset:approve'), ('dataset:erase'),
    ('prompt:read'), ('prompt:write'), ('prompt:publish'), ('experiment:write'),
    ('baseline:create'), ('baseline:approve'),
    ('gate:configure'), ('gate:evaluate'), ('gate:read'), ('gate:except'),
    ('judge:configure'), ('judge:read'), ('escalation:review'),
    ('plan:read'), ('plan:draft'), ('plan:accept'), ('memory:read'),
    ('schedule:write'), ('release:observe'), ('analytics:read'),
    ('alert:configure'), ('alert:read'), ('alert:evaluate'),
    ('audit:read'), ('credential:manage'), ('role:grant'),
    ('governance:configure')) AS v(p);

INSERT INTO clep.role_permission (role_slug, permission)
SELECT 'maintainer', p FROM (VALUES
    ('run:create'), ('run:read'), ('run:cancel'), ('run:reproduce'),
    ('dataset:read'), ('dataset:write'), ('dataset:approve'),
    ('prompt:read'), ('prompt:write'), ('prompt:publish'), ('experiment:write'),
    ('baseline:create'), ('baseline:approve'),
    ('gate:configure'), ('gate:evaluate'), ('gate:read'), ('gate:except'),
    ('judge:configure'), ('judge:read'), ('escalation:review'),
    ('plan:read'), ('plan:draft'), ('plan:accept'), ('memory:read'),
    ('schedule:write'), ('release:observe'), ('analytics:read'),
    ('alert:configure'), ('alert:read'), ('alert:evaluate')) AS v(p);

INSERT INTO clep.role_permission (role_slug, permission)
SELECT 'analyst', p FROM (VALUES
    ('run:create'), ('run:read'), ('run:reproduce'), ('dataset:read'),
    ('prompt:read'), ('gate:read'), ('judge:read'), ('plan:read'),
    ('memory:read'), ('analytics:read'), ('alert:read')) AS v(p);

INSERT INTO clep.role_permission (role_slug, permission)
SELECT 'auditor', p FROM (VALUES
    ('run:read'), ('dataset:read'), ('prompt:read'), ('gate:read'),
    ('judge:read'), ('plan:read'), ('memory:read'), ('analytics:read'),
    ('alert:read'), ('audit:read')) AS v(p);

INSERT INTO clep.role_permission (role_slug, permission)
SELECT 'service', p FROM (VALUES
    ('run:create'), ('run:read'), ('gate:evaluate'), ('gate:read'),
    ('analytics:read')) AS v(p);

-- -----------------------------------------------------------------------------
-- membership — binds a user to an organization (I-3)
--
-- Revocation is state, never deletion: the audit trail records what a member
-- did, and a deleted membership makes that trail unreadable.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.membership (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES clep.organization (id),
    app_user_id      uuid NOT NULL REFERENCES clep.app_user (id),
    state            text NOT NULL DEFAULT 'active',
    created_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz,
    CONSTRAINT uq_membership__org_id UNIQUE (organization_id, id),
    CONSTRAINT uq_membership__organization_user UNIQUE (organization_id, app_user_id),
    CONSTRAINT ck_membership__state CHECK (state IN ('active', 'revoked')),
    CONSTRAINT ck_membership__revoked_at_matches_state
        CHECK ((state = 'revoked') = (revoked_at IS NOT NULL))
);

-- -----------------------------------------------------------------------------
-- service_account — the non-human principal
-- -----------------------------------------------------------------------------
CREATE TABLE clep.service_account (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES clep.organization (id),
    slug             text NOT NULL,
    display_name     text NOT NULL,
    state            text NOT NULL DEFAULT 'active',
    created_by       uuid NOT NULL,
    created_at       timestamptz NOT NULL DEFAULT now(),
    revoked_at       timestamptz,
    CONSTRAINT uq_service_account__org_id UNIQUE (organization_id, id),
    CONSTRAINT uq_service_account__organization_slug UNIQUE (organization_id, slug),
    CONSTRAINT ck_service_account__state CHECK (state IN ('active', 'revoked')),
    CONSTRAINT ck_service_account__revoked_at_matches_state
        CHECK ((state = 'revoked') = (revoked_at IS NOT NULL)),
    CONSTRAINT ck_service_account__slug_form CHECK (slug ~ '^[a-z0-9][a-z0-9_-]*$')
);

-- -----------------------------------------------------------------------------
-- api_key — the credential (ADR-019, I-2)
--
-- The secret is NOT here, and there is no column it could be in. What is stored
-- is a PBKDF2-HMAC-SHA256 verifier over a per-key salt, together with the
-- derivation parameters used for THIS key — ADR-019 rule 6, so that raising the
-- work factor later does not invalidate keys issued under the old one.
--
-- The row id is the key identifier the caller presents (ADR-019 rule 2), which
-- makes verification one indexed lookup rather than a derivation against every
-- stored verifier. Knowing an id grants nothing (rule 4).
--
-- Revocation and expiry are separate columns with the same effect and different
-- causes. Collapsing them would give an auditor one answer to two questions:
-- "the operator withdrew this" and "this aged out" are not the same finding.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.api_key (
    id                    uuid PRIMARY KEY,
    organization_id       uuid NOT NULL REFERENCES clep.organization (id),
    principal_kind        text NOT NULL,
    app_user_id           uuid REFERENCES clep.app_user (id),
    service_account_id    uuid,
    display_name          text NOT NULL,
    verifier              bytea NOT NULL,
    salt                  bytea NOT NULL,
    kdf                   text NOT NULL,
    kdf_iterations        integer NOT NULL,
    created_by            uuid NOT NULL,
    created_at            timestamptz NOT NULL DEFAULT now(),
    expires_at            timestamptz,
    revoked_at            timestamptz,
    revocation_reason     text,
    rotated_to_api_key_id uuid,

    CONSTRAINT uq_api_key__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_api_key__service_account
        FOREIGN KEY (organization_id, service_account_id)
        REFERENCES clep.service_account (organization_id, id),
    CONSTRAINT fk_api_key__rotated_to
        FOREIGN KEY (organization_id, rotated_to_api_key_id)
        REFERENCES clep.api_key (organization_id, id),
    CONSTRAINT ck_api_key__principal_kind
        CHECK (principal_kind IN ('user', 'service_account')),
    -- Exactly one principal, matching the declared kind. A key bound to both, or
    -- to neither, would make every audit record ambiguous about who acted.
    CONSTRAINT ck_api_key__exactly_one_principal CHECK (
        (principal_kind = 'user'
         AND app_user_id IS NOT NULL AND service_account_id IS NULL)
        OR (principal_kind = 'service_account'
            AND service_account_id IS NOT NULL AND app_user_id IS NULL)),
    CONSTRAINT ck_api_key__kdf CHECK (kdf = 'pbkdf2_sha256'),
    -- A work factor recorded as zero is a verifier that is a plain hash.
    CONSTRAINT ck_api_key__kdf_iterations_meaningful
        CHECK (kdf_iterations >= 100000),
    CONSTRAINT ck_api_key__salt_length CHECK (octet_length(salt) >= 16),
    CONSTRAINT ck_api_key__verifier_length CHECK (octet_length(verifier) >= 32),
    CONSTRAINT ck_api_key__revocation_reason_matches_state
        CHECK ((revoked_at IS NULL) = (revocation_reason IS NULL)),
    CONSTRAINT ck_api_key__revocation_reason
        CHECK (revocation_reason IS NULL
               OR revocation_reason IN ('revoked', 'rotated')),
    -- A rotation replaces a key, so the superseded row must say so and must be
    -- revoked. A key that names its successor and is still live is two live
    -- credentials where the operator was told there was one.
    CONSTRAINT ck_api_key__rotation_revokes
        CHECK (rotated_to_api_key_id IS NULL
               OR (revoked_at IS NOT NULL AND revocation_reason = 'rotated'))
);

CREATE INDEX ix_api_key__organization_created
    ON clep.api_key (organization_id, created_at DESC);

COMMENT ON TABLE clep.api_key IS
    'I-2: the secret is never persisted and never retrievable after issue. '
    'There is no column it could occupy and no operation that returns it a '
    'second time.';

COMMENT ON COLUMN clep.api_key.kdf_iterations IS
    'Stored per key rather than fixed platform-wide (ADR-019 rule 6): raising '
    'the work factor must not invalidate keys already issued.';

-- -----------------------------------------------------------------------------
-- role_binding — principal to role, within a scope (ADR-020 rules 3, 4, 5)
--
-- Two scopes and no tree. A hierarchy would need a resolution order, and a
-- resolution order is where an inherited deny quietly becomes an inherited
-- allow.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.role_binding (
    id                  uuid PRIMARY KEY,
    organization_id     uuid NOT NULL REFERENCES clep.organization (id),
    role_slug           text NOT NULL REFERENCES clep.role (slug),
    principal_kind      text NOT NULL,
    app_user_id         uuid REFERENCES clep.app_user (id),
    service_account_id  uuid,
    scope_kind          text NOT NULL,
    project_id          uuid,
    state               text NOT NULL DEFAULT 'active',
    created_by          uuid NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    revoked_at          timestamptz,

    CONSTRAINT uq_role_binding__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_role_binding__project
        FOREIGN KEY (organization_id, project_id)
        REFERENCES clep.project (organization_id, id),
    CONSTRAINT fk_role_binding__service_account
        FOREIGN KEY (organization_id, service_account_id)
        REFERENCES clep.service_account (organization_id, id),
    CONSTRAINT ck_role_binding__principal_kind
        CHECK (principal_kind IN ('user', 'service_account')),
    CONSTRAINT ck_role_binding__exactly_one_principal CHECK (
        (principal_kind = 'user'
         AND app_user_id IS NOT NULL AND service_account_id IS NULL)
        OR (principal_kind = 'service_account'
            AND service_account_id IS NOT NULL AND app_user_id IS NULL)),
    CONSTRAINT ck_role_binding__scope_kind
        CHECK (scope_kind IN ('organization', 'project')),
    -- A project scope without a project is an organization scope by accident,
    -- which is the widest possible failure of a narrowing mechanism.
    CONSTRAINT ck_role_binding__project_matches_scope
        CHECK ((scope_kind = 'project') = (project_id IS NOT NULL)),
    CONSTRAINT ck_role_binding__state CHECK (state IN ('active', 'revoked')),
    CONSTRAINT ck_role_binding__revoked_at_matches_state
        CHECK ((state = 'revoked') = (revoked_at IS NOT NULL))
);

-- NULLS NOT DISTINCT because the principal columns and project_id are nullable
-- by construction: with the default NULL semantics, granting the same role twice
-- at organization scope would produce two rows the store considers different.
CREATE UNIQUE INDEX uq_role_binding__active
    ON clep.role_binding (organization_id, role_slug, principal_kind,
                          app_user_id, service_account_id, project_id)
    NULLS NOT DISTINCT
    WHERE state = 'active';

CREATE INDEX ix_role_binding__organization_principal
    ON clep.role_binding (organization_id, principal_kind, app_user_id,
                          service_account_id)
    WHERE state = 'active';

-- I-4, in the store rather than in a service (ADR-020 rule 9).
--
-- Service code is what an operator bypasses with a direct connection at 3am. An
-- organization that has revoked its last role:grant binding is not recoverable
-- by any operation the platform exposes, because an operation that could
-- recover it would be an operation that grants administrative authority without
-- holding it.
CREATE FUNCTION clep.refuse_last_administrative_binding() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    remaining integer;
BEGIN
    IF NEW.state <> 'revoked' OR OLD.state = 'revoked' THEN
        RETURN NEW;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM clep.role_permission
                   WHERE role_slug = OLD.role_slug AND permission = 'role:grant') THEN
        RETURN NEW;
    END IF;
    SELECT count(*) INTO remaining
      FROM clep.role_binding b
      JOIN clep.role_permission p ON p.role_slug = b.role_slug
     WHERE b.organization_id = OLD.organization_id
       AND b.state = 'active'
       AND b.id <> OLD.id
       AND p.permission = 'role:grant';
    IF remaining = 0 THEN
        RAISE EXCEPTION
            'I-4: % is the last binding carrying role:grant in organization %; '
            'revoking it would leave the organization with no principal able to '
            'grant a role, and no operation can restore one',
            OLD.id, OLD.organization_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_role_binding__keep_an_administrator
    BEFORE UPDATE ON clep.role_binding
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_last_administrative_binding();

-- -----------------------------------------------------------------------------
-- retention_policy — per tenant, floored (REQ-F-12-6, REQ-N-COMP-3, I-34)
--
-- The audit floor is a platform constant, not a tenant setting, and the CHECK
-- below is what makes I-34 unlowerable rather than merely documented. A tenant
-- may set audit retention ABOVE the floor — some regulators require longer —
-- and cannot set it below.
-- -----------------------------------------------------------------------------
CREATE FUNCTION clep.audit_retention_floor_days() RETURNS integer
    LANGUAGE sql IMMUTABLE
    AS $$ SELECT 2555 $$;

COMMENT ON FUNCTION clep.audit_retention_floor_days() IS
    'Seven years, the floor below which tenant retention policy may not reach '
    '(I-34). IMMUTABLE so it can be named in a CHECK constraint: a floor '
    'enforced by application code is a floor the application can forget.';

CREATE TABLE clep.retention_policy (
    id                       uuid PRIMARY KEY,
    organization_id          uuid NOT NULL REFERENCES clep.organization (id),
    decision_retention_days  integer NOT NULL,
    content_retention_days   integer NOT NULL,
    audit_retention_days     integer NOT NULL DEFAULT 2555,
    updated_by               uuid NOT NULL,
    updated_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_retention_policy__organization UNIQUE (organization_id),
    CONSTRAINT ck_retention_policy__decision_positive
        CHECK (decision_retention_days > 0),
    CONSTRAINT ck_retention_policy__content_positive
        CHECK (content_retention_days > 0),
    CONSTRAINT ck_retention_policy__audit_floor
        CHECK (audit_retention_days >= clep.audit_retention_floor_days())
);

COMMENT ON CONSTRAINT ck_retention_policy__audit_floor ON clep.retention_policy IS
    'I-34 and REQ-N-COMP-3. Audit retention is subordinate to no tenant policy. '
    'Expressed as a constraint because REQ-F-12-6 explicitly makes tenant '
    'retention policy subject to this floor, and a floor that only the service '
    'layer knows about is a floor a second writer walks straight through.';

-- -----------------------------------------------------------------------------
-- usage_limit and quota_consumption — ADR-021
--
-- The rate limit's own counter lives in Redis, because it is ephemeral
-- coordination measured in seconds. The QUOTA counter lives here, because it is
-- a governance figure a tenant is accountable to and must survive a restart of
-- anything.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.usage_limit (
    id                   uuid PRIMARY KEY,
    organization_id      uuid NOT NULL REFERENCES clep.organization (id),
    requests_per_minute  integer NOT NULL,
    runs_per_period      integer NOT NULL,
    period_days          integer NOT NULL,
    updated_by           uuid NOT NULL,
    updated_at           timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_usage_limit__organization UNIQUE (organization_id),
    -- No value means unlimited. REQ-F-12-8 forbids governance some deployments
    -- do without, and a limit of zero would deny everything rather than allow
    -- everything, so neither end of the range is an escape hatch.
    CONSTRAINT ck_usage_limit__requests_positive CHECK (requests_per_minute > 0),
    CONSTRAINT ck_usage_limit__runs_positive CHECK (runs_per_period > 0),
    CONSTRAINT ck_usage_limit__period_positive CHECK (period_days > 0)
);

CREATE TABLE clep.quota_consumption (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL REFERENCES clep.organization (id),
    period_start     date NOT NULL,
    runs_started     integer NOT NULL DEFAULT 0,
    updated_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_quota_consumption__organization_period
        UNIQUE (organization_id, period_start),
    CONSTRAINT ck_quota_consumption__runs_non_negative CHECK (runs_started >= 0)
);

-- -----------------------------------------------------------------------------
-- evaluator_invocation — ADR-006 rule 6, REQ-F-12-9
--
-- The domain model has declared this entity since Phase 3 ("Evaluator version,
-- permissions used, outcome"). Phase 12 is the phase that has something to
-- record in it: until there was a permission boundary, "permissions used" had
-- no possible value.
--
-- granted_permissions records what the invocation was ACTUALLY allowed, which
-- may be narrower than what the evaluator version declared. Recording the
-- declaration would record an intention; recording the grant records the
-- boundary the code ran inside.
-- -----------------------------------------------------------------------------
CREATE TABLE clep.evaluator_invocation (
    id                     uuid PRIMARY KEY,
    organization_id        uuid NOT NULL REFERENCES clep.organization (id),
    run_sample_id          uuid NOT NULL,
    evaluator_version_id   uuid NOT NULL,
    granted_permissions    text NOT NULL,
    outcome                text NOT NULL,
    correlation_id         text NOT NULL,
    invoked_at             timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_evaluator_invocation__org_id UNIQUE (organization_id, id),
    CONSTRAINT fk_evaluator_invocation__run_sample
        FOREIGN KEY (organization_id, run_sample_id)
        REFERENCES clep.run_sample (organization_id, id),
    CONSTRAINT ck_evaluator_invocation__outcome
        CHECK (outcome IN ('scored', 'abstained', 'unavailable', 'refused')),
    CONSTRAINT ck_evaluator_invocation__correlation_present
        CHECK (length(btrim(correlation_id)) > 0)
);

CREATE INDEX ix_evaluator_invocation__organization_sample
    ON clep.evaluator_invocation (organization_id, run_sample_id);

CREATE TRIGGER trg_evaluator_invocation__immutable
    BEFORE UPDATE OR DELETE ON clep.evaluator_invocation
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_change_to_audit_record();

-- =============================================================================
-- D-1 — a tenant-carrying guarantee where a composite foreign key is unformable
--
-- `clep.comparison` names the evaluator version whose scores it compared as a
-- plain uuid. Every other cross-table link in this schema carries the tenant, so
-- a row cannot cite another tenant's row even if application code asks it to.
-- The composite foreign key cannot be formed here: `evaluator_version` is
-- dual-scoped, a built-in carries a NULL organization_id under the ADR-010 rule
-- 4 exception, and a composite key from a NOT NULL column cannot reference a
-- NULL one.
--
-- Phase 7 recorded this as debt and Phase 12 owns it. The fix is NOT to amend
-- ADR-010 rule 4 — that is an accepted decision, and narrowing it would be a
-- change proposal rather than a milestone. The fix is to obtain the same
-- guarantee by the mechanism that remains available: a store-level trigger.
--
-- What it actually enforces is worth stating precisely, because it is stronger
-- than it first appears. The lookup below runs under the caller's own tenant
-- context and under FORCE ROW LEVEL SECURITY, so another tenant's
-- evaluator_version is not merely rejected — it is invisible, and the row is
-- refused for not existing. A built-in, whose organization_id is NULL, remains
-- visible through the policy's NULL branch and is accepted. That is exactly the
-- rule a composite foreign key would have expressed.
--
-- The protection therefore moves out of the derivation path and into the store,
-- which is what D-1 said was missing.
-- =============================================================================
CREATE FUNCTION clep.refuse_foreign_tenant_evaluator_version() RETURNS trigger
    LANGUAGE plpgsql AS $$
DECLARE
    owner uuid;
    seen  boolean := false;
BEGIN
    SELECT organization_id, true INTO owner, seen
      FROM clep.evaluator_version
     WHERE id = NEW.evaluator_version_id;
    IF NOT seen THEN
        RAISE EXCEPTION
            'D-1: evaluator version % is not visible under organization %; a '
            'row may cite a built-in evaluator or one of its own tenant, and '
            'nothing else',
            NEW.evaluator_version_id, NEW.organization_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    IF owner IS NOT NULL AND owner <> NEW.organization_id THEN
        RAISE EXCEPTION
            'D-1: evaluator version % belongs to organization %, not %',
            NEW.evaluator_version_id, owner, NEW.organization_id
            USING ERRCODE = 'foreign_key_violation';
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION clep.refuse_foreign_tenant_evaluator_version() IS
    'D-1. The tenant-carrying guarantee that a composite foreign key would give, '
    'obtained by a trigger because the composite key is unformable while '
    'built-in evaluators are global rows under ADR-010 rule 4.';

CREATE TRIGGER trg_comparison__evaluator_version_is_reachable
    BEFORE INSERT OR UPDATE ON clep.comparison
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_foreign_tenant_evaluator_version();

CREATE TRIGGER trg_evaluator_invocation__evaluator_version_is_reachable
    BEFORE INSERT OR UPDATE ON clep.evaluator_invocation
    FOR EACH ROW EXECUTE FUNCTION clep.refuse_foreign_tenant_evaluator_version();

-- =============================================================================
-- Row-level security
-- =============================================================================

-- app_user is global and NOT world-readable: visibility comes from membership.
ALTER TABLE clep.app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.app_user FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.app_user
    USING (EXISTS (SELECT 1 FROM clep.membership m
                    WHERE m.app_user_id = app_user.id
                      AND m.organization_id = clep.current_organization_id()));

-- The role catalogue is the ADR-010 rule 4 case proper: readable by everyone,
-- writable by no tenant. ENABLE and FORCE are still set, because a table with
-- neither is a table nobody re-examined; the policy states the intent instead of
-- the absence of one implying it.
ALTER TABLE clep.role ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.role FORCE  ROW LEVEL SECURITY;
CREATE POLICY catalogue_is_readable ON clep.role FOR SELECT USING (true);

ALTER TABLE clep.role_permission ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.role_permission FORCE  ROW LEVEL SECURITY;
CREATE POLICY catalogue_is_readable ON clep.role_permission FOR SELECT USING (true);

ALTER TABLE clep.membership ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.membership FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.membership
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.service_account ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.service_account FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.service_account
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.api_key ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.api_key FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.api_key
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.role_binding ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.role_binding FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.role_binding
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.retention_policy ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.retention_policy FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.retention_policy
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.usage_limit ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.usage_limit FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.usage_limit
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.quota_consumption ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.quota_consumption FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.quota_consumption
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

ALTER TABLE clep.evaluator_invocation ENABLE ROW LEVEL SECURITY;
ALTER TABLE clep.evaluator_invocation FORCE  ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON clep.evaluator_invocation
    USING (organization_id = clep.current_organization_id())
    WITH CHECK (organization_id = clep.current_organization_id());

-- =============================================================================
-- Runtime grants
--
-- The shape of this list is the authorization model expressed a second time, in
-- the only place that cannot be talked around.
--
--   * The role catalogue is SELECT only. A tenant cannot mint a role.
--   * app_user and membership are SELECT only. Creating a global identity, and
--     binding a person to a tenant, are provisioning operations — the same
--     category as creating the tenant root itself. No requirement asks for a
--     user-invitation API, and a route that mints global rows is a surface with
--     nothing behind it.
--   * api_key takes UPDATE because revocation and rotation are updates (I-3),
--     and never DELETE, for the same reason audit_event never does.
--   * evaluator_invocation is append-only: it is the record of untrusted code
--     having run.
-- =============================================================================
GRANT SELECT                 ON clep.app_user             TO clep_runtime;
GRANT SELECT                 ON clep.role                 TO clep_runtime;
GRANT SELECT                 ON clep.role_permission      TO clep_runtime;
GRANT SELECT                 ON clep.membership           TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.service_account      TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.api_key              TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.role_binding         TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.retention_policy     TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.usage_limit          TO clep_runtime;
GRANT SELECT, INSERT, UPDATE ON clep.quota_consumption    TO clep_runtime;
GRANT SELECT, INSERT         ON clep.evaluator_invocation TO clep_runtime;
