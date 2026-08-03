"""Persistence for the prompt, model and system registry.

The registry's job is to make `REQ-F-07-1` possible. A run identity is only
meaningful if every element it names is a versioned, immutable thing — a digest
over rows that can still change is a digest over nothing.

Immutability itself is not implemented here. It is enforced by triggers in
`06-registry-and-experiments.sql`, because this module is not the only thing that
can write to these tables and a rule that lives only in the application is a rule
that holds only while everyone remembers it. What this module does is refuse to
*try*, so the common case produces a clear error rather than a trigger message.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass

import psycopg

from clep.identity import new_ulid, ulid_to_uuid, uuid_to_ulid


class RegistryError(RuntimeError):
    pass


class VersionFrozen(RegistryError):
    """Raised instead of attempting a write the store would refuse."""


def content_digest(*parts: object) -> str:
    material = "\x1f".join("" if p is None else str(p) for p in parts)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def canonical_parameters(parameters: dict) -> str:
    """Output-affecting parameters, encoded so the digest is stable.

    Sorted keys and explicit separators for the same reason the run identity uses
    them: a digest that depends on dict ordering is not reproducible in another
    process, and the whole point of the column is that it is.
    """
    return json.dumps(parameters, ensure_ascii=True, separators=(",", ":"),
                      sort_keys=True)


@dataclass(frozen=True)
class VersionRow:
    id: str
    version_number: int
    content_digest: str
    state: str

    @property
    def is_published(self) -> bool:
        return self.state == "published"


class RegistryRepository:
    """Tenant comes from the session context, never from a parameter."""

    def __init__(self, conn: psycopg.Connection, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    # --------------------------------------------------------------- prompts
    def create_prompt(self, *, project_id: str, slug: str,
                      display_name: str) -> str:
        prompt_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.prompt (id, organization_id, project_id, slug, "
            "display_name) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, project_id, slug) DO NOTHING",
            (ulid_to_uuid(prompt_id), self._org, ulid_to_uuid(project_id), slug,
             display_name))
        row = self._conn.execute(
            "SELECT id FROM clep.prompt WHERE organization_id = %s "
            "AND project_id = %s AND slug = %s",
            (self._org, ulid_to_uuid(project_id), slug)).fetchone()
        return uuid_to_ulid(row[0])

    def add_prompt_version(self, prompt_id: str, *, body: str,
                           created_by: str) -> str:
        """Version numbers are allocated from what is already stored.

        Taken inside the caller's transaction so two concurrent writers cannot
        both read the same maximum: the unique constraint on
        (organization_id, prompt_id, version_number) is what actually decides,
        and the loser gets an integrity error rather than a silently reused
        number.
        """
        next_number = self._next_version_number("prompt_version", "prompt_id",
                                                prompt_id)
        version_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.prompt_version (id, organization_id, prompt_id, "
            "version_number, content_digest, body, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(version_id), self._org, ulid_to_uuid(prompt_id),
             next_number, content_digest(body), body, uuid.UUID(str(created_by))))
        return version_id

    def get_prompt_version(self, version_id: str) -> VersionRow | None:
        row = self._conn.execute(
            "SELECT id, version_number, content_digest, state "
            "FROM clep.prompt_version WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(version_id))).fetchone()
        return self._version_row(row)

    def publish_prompt_version(self, version_id: str) -> None:
        self._publish("prompt_version", version_id)

    # ---------------------------------------------------- providers and models
    def create_provider(self, *, slug: str, display_name: str,
                        endpoint_kind: str) -> str:
        if endpoint_kind not in ("hosted", "self_hosted"):
            raise RegistryError(
                f"endpoint_kind {endpoint_kind!r} is neither hosted nor "
                f"self_hosted; REQ-F-02-4 makes self-hosted first-class and the "
                f"CHECK constraint permits exactly these two")
        provider_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.provider (id, organization_id, slug, display_name, "
            "endpoint_kind) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, slug) DO NOTHING",
            (ulid_to_uuid(provider_id), self._org, slug, display_name,
             endpoint_kind))
        row = self._conn.execute(
            "SELECT id FROM clep.provider WHERE organization_id = %s AND slug = %s",
            (self._org, slug)).fetchone()
        return uuid_to_ulid(row[0])

    def create_model(self, provider_id: str, *, model_identifier: str,
                     display_name: str) -> str:
        model_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.model (id, organization_id, provider_id, "
            "model_identifier, display_name) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, provider_id, model_identifier) "
            "DO NOTHING",
            (ulid_to_uuid(model_id), self._org, ulid_to_uuid(provider_id),
             model_identifier, display_name))
        row = self._conn.execute(
            "SELECT id FROM clep.model WHERE organization_id = %s "
            "AND provider_id = %s AND model_identifier = %s",
            (self._org, ulid_to_uuid(provider_id), model_identifier)).fetchone()
        return uuid_to_ulid(row[0])

    def add_model_configuration(self, model_id: str, *, parameters: dict,
                                created_by: str, seed: int | None = None,
                                is_deterministic: bool | None = None) -> str:
        """`is_deterministic` is derived when it is not stated.

        A configuration is treated as deterministic only when the sampling
        parameters say so. Defaulting the other way would let a sampled
        configuration into the cache, and `REQ-F-07-4` is then false in the one
        direction nobody notices — the results still look plausible.
        """
        if is_deterministic is None:
            is_deterministic = _looks_deterministic(parameters, seed)
        encoded = canonical_parameters(parameters)
        next_number = self._next_version_number("model_configuration", "model_id",
                                                model_id)
        configuration_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.model_configuration (id, organization_id, model_id, "
            "version_number, output_affecting_parameters, content_digest, seed, "
            "is_deterministic, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(configuration_id), self._org, ulid_to_uuid(model_id),
             next_number, encoded, content_digest(encoded, seed), seed,
             is_deterministic, uuid.UUID(str(created_by))))
        return configuration_id

    def get_model_configuration(self, configuration_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, version_number, content_digest, state, "
            "output_affecting_parameters, seed, is_deterministic "
            "FROM clep.model_configuration "
            "WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(configuration_id))).fetchone()
        if row is None:
            return None
        return {"id": uuid_to_ulid(row[0]), "versionNumber": row[1],
                "contentDigest": row[2], "state": row[3], "parameters": row[4],
                "seed": row[5], "isDeterministic": row[6]}

    def publish_model_configuration(self, configuration_id: str) -> None:
        self._publish("model_configuration", configuration_id)

    # ---------------------------------------------------------------- systems
    def create_system(self, *, project_id: str, slug: str, display_name: str,
                      kind: str = "prompt") -> str:
        system_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.system_definition (id, organization_id, project_id, "
            "slug, display_name, kind) VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, project_id, slug) DO NOTHING",
            (ulid_to_uuid(system_id), self._org, ulid_to_uuid(project_id), slug,
             display_name, kind))
        row = self._conn.execute(
            "SELECT id FROM clep.system_definition WHERE organization_id = %s "
            "AND project_id = %s AND slug = %s",
            (self._org, ulid_to_uuid(project_id), slug)).fetchone()
        return uuid_to_ulid(row[0])

    def add_system_version(self, system_id: str, *, model_configuration_id: str,
                           prompt_version_id: str | None,
                           created_by: str) -> str:
        """A system version composes the things under test.

        Its digest is derived from the digests of its parts rather than from
        their identifiers, so two system versions assembled from the same content
        agree even when the rows were created separately.
        """
        parts = []
        configuration = self.get_model_configuration(model_configuration_id)
        if configuration is None:
            raise RegistryError(
                f"model configuration {model_configuration_id} does not exist in "
                f"this tenant; a system version cannot name what is not there")
        parts.append(configuration["contentDigest"])
        if prompt_version_id is not None:
            prompt_version = self.get_prompt_version(prompt_version_id)
            if prompt_version is None:
                raise RegistryError(
                    f"prompt version {prompt_version_id} does not exist in this "
                    f"tenant")
            parts.append(prompt_version.content_digest)
        next_number = self._next_version_number(
            "system_version", "system_definition_id", system_id)
        version_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.system_version (id, organization_id, "
            "system_definition_id, version_number, prompt_version_id, "
            "model_configuration_id, content_digest, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (ulid_to_uuid(version_id), self._org, ulid_to_uuid(system_id),
             next_number,
             ulid_to_uuid(prompt_version_id) if prompt_version_id else None,
             ulid_to_uuid(model_configuration_id), content_digest(*parts),
             uuid.UUID(str(created_by))))
        return version_id

    def get_system_version(self, version_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT id, version_number, content_digest, state, "
            "prompt_version_id, model_configuration_id FROM clep.system_version "
            "WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(version_id))).fetchone()
        if row is None:
            return None
        return {"id": uuid_to_ulid(row[0]), "versionNumber": row[1],
                "contentDigest": row[2], "state": row[3],
                "promptVersionId": uuid_to_ulid(row[4]) if row[4] else None,
                "modelConfigurationId": uuid_to_ulid(row[5])}

    def publish_system_version(self, version_id: str) -> None:
        self._publish("system_version", version_id)

    # ------------------------------------------------------------ experiments
    def create_experiment(self, *, project_id: str, slug: str, display_name: str,
                          created_by: str, hypothesis: str | None = None) -> str:
        experiment_id = new_ulid()
        self._conn.execute(
            "INSERT INTO clep.experiment (id, organization_id, project_id, slug, "
            "display_name, hypothesis, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, project_id, slug) DO NOTHING",
            (ulid_to_uuid(experiment_id), self._org, ulid_to_uuid(project_id),
             slug, display_name, hypothesis, uuid.UUID(str(created_by))))
        row = self._conn.execute(
            "SELECT id FROM clep.experiment WHERE organization_id = %s "
            "AND project_id = %s AND slug = %s",
            (self._org, ulid_to_uuid(project_id), slug)).fetchone()
        return uuid_to_ulid(row[0])

    # --------------------------------------------------------------- internals
    def _next_version_number(self, table: str, parent_column: str,
                             parent_id: str) -> int:
        # The table and column names are module constants at every call site,
        # never caller input; the tenant and parent are still bound parameters.
        row = self._conn.execute(
            f"SELECT COALESCE(MAX(version_number), 0) + 1 FROM clep.{table} "
            f"WHERE organization_id = %s AND {parent_column} = %s",
            (self._org, ulid_to_uuid(parent_id))).fetchone()
        return int(row[0])

    def _publish(self, table: str, version_id: str) -> None:
        current = self._conn.execute(
            f"SELECT state FROM clep.{table} WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(version_id))).fetchone()
        if current is None:
            raise RegistryError(f"{table} {version_id} does not exist in this tenant")
        if current[0] == "published":
            # Already in the desired state. Attempting the UPDATE would be
            # refused by the trigger, and reporting "already published" as an
            # error would make a retried publish look like a failure.
            return
        self._conn.execute(
            f"UPDATE clep.{table} SET state = 'published', published_at = now() "
            f"WHERE organization_id = %s AND id = %s",
            (self._org, ulid_to_uuid(version_id)))

    @staticmethod
    def _version_row(row) -> VersionRow | None:
        if row is None:
            return None
        return VersionRow(id=uuid_to_ulid(row[0]), version_number=row[1],
                          content_digest=row[2], state=row[3])


def _looks_deterministic(parameters: dict, seed: int | None) -> bool:
    """Deterministic only when the parameters actually say so.

    Temperature zero is not by itself a guarantee — provider infrastructure
    varies — but a non-zero temperature without a seed is a positive statement
    that the output will vary, and that is enough to keep it out of the cache.
    """
    temperature = parameters.get("temperature")
    top_p = parameters.get("top_p")
    if temperature is None and seed is None:
        return False
    if temperature is not None and float(temperature) != 0.0:
        return seed is not None
    if top_p is not None and float(top_p) != 1.0 and seed is None:
        return False
    return True
