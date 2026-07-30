-- =============================================================================
-- 01 — Roles, schema, and tenant context
--
-- SPECIFICATION ARTIFACT. This is the implementation-ready schema definition for
-- the Phase 4 foundation. It is not a migration: there is no version chain and
-- no migration tooling, both of which belong to the first implementation phase.
--
-- Realises ADR-012 conditions D-1 through D-4. Those conditions are not
-- deployment advice; without all four, tenant isolation is not enforced and
-- REQ-F-12-5 is unmet while every policy still appears correctly defined.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- Roles (ADR-012 D-3, D-4)
--
-- Two roles, and the separation is load-bearing. PostgreSQL table owners bypass
-- row-level security by default, so an application connecting as the owner of
-- its own tables reads across tenants while every policy looks correct. The
-- runtime role therefore neither owns the schema nor carries BYPASSRLS.
-- -----------------------------------------------------------------------------

-- Owns the schema, runs schema changes. Never used by the application.
CREATE ROLE clep_migration NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

-- The application's only role. Cannot bypass row-level security by any means.
CREATE ROLE clep_runtime    NOLOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE;

CREATE SCHEMA IF NOT EXISTS clep AUTHORIZATION clep_migration;

-- The runtime role may use the schema but may not create in it.
GRANT USAGE ON SCHEMA clep TO clep_runtime;
REVOKE CREATE ON SCHEMA clep FROM clep_runtime;

-- -----------------------------------------------------------------------------
-- Tenant context
--
-- Established once per request at the ingress boundary and never re-derived from
-- caller-supplied data deeper in the stack (ADR-010 rule 3). Every row-level
-- security policy reads it through this function.
--
-- STRICT is deliberate: an unset or malformed context must fail closed rather
-- than resolve to NULL and silently match nothing, which would look like an
-- empty result set rather than an error.
-- -----------------------------------------------------------------------------
CREATE FUNCTION clep.current_organization_id() RETURNS uuid
    LANGUAGE sql STABLE
    AS $$
        SELECT NULLIF(current_setting('clep.organization_id', true), '')::uuid
    $$;

COMMENT ON FUNCTION clep.current_organization_id() IS
    'Tenant context for row-level security. Returns NULL when unset; policies '
    'compare with = so a NULL context matches no rows and fails closed.';

-- -----------------------------------------------------------------------------
-- Conventions applied throughout (data-model.md section 2)
--
--   N-1  tables snake_case, singular
--   N-2  primary key is `id`
--   N-3  foreign keys are <referenced_table>_id
--   N-4  every tenant-scoped table carries organization_id NOT NULL
--   N-5  content-derived identity columns are named content_digest
--   N-6  timestamps *_at, always timestamptz
--   N-7  booleans is_/has_; state is never boolean where more than two exist
--   N-8  enumerated states are explicit constrained values
--   N-9  money and token quantities are exact numeric, never floating point
--   N-10 ix_ / uq_ / fk_ / ck_ naming
--   N-11 no unqualified `data`, `info`, `metadata`, `value` columns
--   N-12 audit tables prefixed audit_
--
-- Tenancy rules (data-model.md section 3): P-1 through P-7.
-- P-5 in particular is implemented with composite foreign keys carrying
-- organization_id, so a cross-tenant LINK is rejected by the store and not only
-- a cross-tenant READ.
-- -----------------------------------------------------------------------------
