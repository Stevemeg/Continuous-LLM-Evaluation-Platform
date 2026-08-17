-- =============================================================================
-- 13 — The correlation chain, made queryable after the fact
--
-- Phase 13. ADDs only, and smaller than any earlier phase's file, because almost
-- nothing was missing. `REQ-N-OBS-1` asks for one identifier recoverable through
-- workflow, model call, evaluator, judge, artifact and gate decision, and the
-- schema already links six of those by composite foreign key: a sample belongs
-- to a run, a cost and an evaluator invocation belong to a sample, a judge run
-- belongs to a sample, a gate decision names its candidate run. Copying an
-- identifier onto each of them would create a second thing that can disagree
-- with the first.
--
-- So what is added is only what cannot be reached by a join.
--
--   1. `audit_event.correlation_id`. The audit trail is the one hop of the chain
--      with no foreign key back to anything -- deliberately, since an audit
--      event may be about a route, a principal or a policy rather than a run
--      (`target_id` is nullable for exactly that reason). Without this column an
--      audit event cannot be placed on the chain at all.
--
--   2. Three indexes, because the chain is queried BY the identifier. Without
--      them the demonstration `REQ-N-OBS-1` requires is a sequential scan of
--      every run a tenant has ever made, which is the shape of `REQ-N-PERF-3`
--      degradation rather than a correlation.
--
-- Nullable, not NOT NULL, and that is a decision rather than a convenience.
-- `audit_event` is append-only and carries rows written before this phase
-- existed; NOT NULL would require inventing an identifier for each of them, and
-- an invented correlation is worse than an absent one. It also stays nullable
-- going forward: an event recorded outside any request -- a scheduler waking up,
-- a provisioning step -- genuinely has no correlation, and a column that forces
-- one would be filled with a fresh identifier that correlates nothing.
--
-- No table is added. Service-level indicators are computed from these same
-- durable records under ADR-023 rule 6, never from a parallel telemetry store
-- that could disagree with the audit trail.
-- =============================================================================

-- No IF NOT EXISTS anywhere below. The migration runner records each applied
-- file by content hash and refuses to re-apply one, so a guard against
-- double-application guards against a condition that cannot arise -- and would
-- convert a genuine "this ran twice" into silence. It also confused the schema
-- conformance checker, which read `IF` as the index name.
ALTER TABLE clep.audit_event
    ADD COLUMN correlation_id text;

-- Blank is not absent. An empty string would satisfy a NULL check and correlate
-- nothing, which is the failure mode `evaluator_invocation` already guards.
ALTER TABLE clep.audit_event
    ADD CONSTRAINT ck_audit_event__correlation_not_blank
        CHECK (correlation_id IS NULL OR length(btrim(correlation_id)) > 0);

-- =============================================================================
-- The chain is queried by identifier, so the identifier is indexed. Partial on
-- NOT NULL: a correlation nobody set is not a row anybody will look up by it.
-- =============================================================================
CREATE INDEX ix_run__correlation
    ON clep.run (organization_id, correlation_id)
    WHERE correlation_id IS NOT NULL;

CREATE INDEX ix_audit_event__correlation
    ON clep.audit_event (organization_id, correlation_id)
    WHERE correlation_id IS NOT NULL;

CREATE INDEX ix_evaluator_invocation__correlation
    ON clep.evaluator_invocation (organization_id, correlation_id);
