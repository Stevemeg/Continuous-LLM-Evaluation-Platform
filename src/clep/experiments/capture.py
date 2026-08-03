"""Assembling a run's identity from what the registry actually holds.

The digest is derived from stored content digests, never from identifiers alone.
Two runs naming the same dataset version identifier measured the same thing only
if that version's *content* is the same, and Phase 6's immutability triggers make
that true going forward — but a restored backup or a pre-Phase-6 row could still
carry a changed body under an unchanged identifier. Reading the digests closes
that gap rather than assuming it away.
"""
from __future__ import annotations

from clep.experiments.identity import IdentityBuilder, RunIdentity
from clep.identity import ulid_to_uuid, uuid_to_ulid


class MissingIdentityComponent(RuntimeError):
    """A run cannot be given an identity that names something absent.

    Raised before the run is created rather than after: a run whose identity is
    partly unknown is not reproducible, and recording it as though it were is
    the failure REQ-F-07-1 exists to prevent.
    """


def build_run_identity(conn, organization_id: str, *, suite_version_id: str,
                       dataset_version_id: str, integration_tier: str,
                       candidates: list[dict],
                       include_environment: bool = True) -> RunIdentity:
    org = str(organization_id)
    builder = IdentityBuilder()

    builder.add("dataset_version", dataset_version_id,
                _digest(conn, org, "dataset_version", dataset_version_id))
    builder.add("suite_version", suite_version_id,
                _digest(conn, org, "suite_version", suite_version_id))
    builder.add_literal("integration_tier", integration_tier)

    for candidate in candidates:
        configuration_id = candidate.get("modelConfigurationId")
        if configuration_id:
            builder.add("model_configuration", configuration_id,
                        _digest(conn, org, "model_configuration", configuration_id))
            seed = _seed_of(conn, org, configuration_id)
            if seed is not None:
                builder.add_literal("seed", seed)
        prompt_version_id = candidate.get("promptVersionId")
        if prompt_version_id:
            builder.add("prompt_version", prompt_version_id,
                        _digest(conn, org, "prompt_version", prompt_version_id))
        system_version_id = candidate.get("systemVersionId")
        if system_version_id:
            builder.add("system_version", system_version_id,
                        _digest(conn, org, "system_version", system_version_id))

    # Evaluator versions come from the suite, not from the request. A caller
    # cannot choose which evaluators the identity records, or the identity would
    # describe what was claimed rather than what ran.
    for evaluator_version_id, digest in _suite_evaluators(conn, org,
                                                          suite_version_id):
        builder.add("evaluator_version", evaluator_version_id, digest)

    if include_environment:
        builder.add_environment()
    return builder.build()


def _digest(conn, org: str, table: str, ref: str) -> str:
    # `table` is a module-local constant at every call site; the tenant and the
    # identifier are bound parameters.
    row = conn.execute(
        f"SELECT content_digest FROM clep.{table} "
        f"WHERE id = %s AND (organization_id = %s OR organization_id IS NULL)",
        (ulid_to_uuid(ref), org)).fetchone()
    if row is None:
        raise MissingIdentityComponent(
            f"{table} {ref} does not exist in this tenant; a run identity "
            f"cannot name something that is not there")
    return row[0]


def _seed_of(conn, org: str, configuration_id: str):
    row = conn.execute(
        "SELECT seed FROM clep.model_configuration "
        "WHERE organization_id = %s AND id = %s",
        (org, ulid_to_uuid(configuration_id))).fetchone()
    return row[0] if row else None


def _suite_evaluators(conn, org: str, suite_version_id: str):
    rows = conn.execute(
        "SELECT ev.id, ev.content_digest FROM clep.suite_evaluator se "
        "JOIN clep.evaluator_version ev ON ev.id = se.evaluator_version_id "
        "WHERE se.organization_id = %s AND se.suite_version_id = %s "
        "ORDER BY ev.id",
        (org, ulid_to_uuid(suite_version_id))).fetchall()
    return [(uuid_to_ulid(r[0]), r[1]) for r in rows]
