"""Application service for the registry and experiment operations.

Same rule as `RunService`: every method opens a tenant-bound session and the
organization arrives from the ingress principal. Responses are assembled in the
contract's vocabulary.

Every write that changes a registry record also writes an audit event
(`REQ-F-01-6`), inside the same transaction. An audit trail written afterwards is
an audit trail that is missing exactly the events that failed.
"""
from __future__ import annotations

import uuid

from clep.db.session import tenant_session
from clep.experiments import reproduction
from clep.experiments.identity import IdentityBuilder
from clep.identity import ulid_to_uuid
from clep.registry.repository import RegistryRepository


class RegistryService:
    def __init__(self, runtime_dsn: str):
        self._dsn = runtime_dsn

    # -------------------------------------------------------------- prompts
    def create_prompt(self, *, organization_id: str, project_id: str, slug: str,
                      display_name: str, actor_id: str) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegistryRepository(conn, organization_id)
            prompt_id = repo.create_prompt(project_id=project_id, slug=slug,
                                           display_name=display_name)
            _audit(conn, organization_id, actor_id, "prompt.created", "prompt",
                   prompt_id)
            return {"id": prompt_id, "projectId": project_id, "slug": slug,
                    "displayName": display_name}

    def add_prompt_version(self, *, organization_id: str, prompt_id: str,
                           body: str, actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegistryRepository(conn, organization_id)
            version_id = repo.add_prompt_version(prompt_id, body=body,
                                                 created_by=str(actor_uuid(actor_id)))
            _audit(conn, organization_id, actor_id, "prompt_version.created",
                   "prompt_version", version_id)
            return self._present_version(conn, organization_id, version_id)

    def get_prompt_version(self, organization_id: str,
                           version_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            return self._present_version(conn, organization_id, version_id)

    def publish_prompt_version(self, *, organization_id: str, version_id: str,
                               actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegistryRepository(conn, organization_id)
            if repo.get_prompt_version(version_id) is None:
                return None
            repo.publish_prompt_version(version_id)
            _audit(conn, organization_id, actor_id, "prompt_version.published",
                   "prompt_version", version_id)
            return self._present_version(conn, organization_id, version_id)

    # ---------------------------------------------------------- experiments
    def create_experiment(self, *, organization_id: str, project_id: str,
                          slug: str, display_name: str, actor_id: str,
                          hypothesis: str | None = None) -> dict:
        with tenant_session(self._dsn, organization_id) as conn:
            repo = RegistryRepository(conn, organization_id)
            experiment_id = repo.create_experiment(
                project_id=project_id, slug=slug, display_name=display_name,
                created_by=str(actor_uuid(actor_id)), hypothesis=hypothesis)
            _audit(conn, organization_id, actor_id, "experiment.created",
                   "experiment", experiment_id)
            body = {"id": experiment_id, "projectId": project_id, "slug": slug,
                    "displayName": display_name}
            if hypothesis:
                body["hypothesis"] = hypothesis
            return body

    def reproduce_run(self, *, organization_id: str, run_id: str,
                      actor_id: str) -> dict | None:
        with tenant_session(self._dsn, organization_id) as conn:
            exists = conn.execute(
                "SELECT 1 FROM clep.run WHERE organization_id = %s AND id = %s",
                (str(organization_id), ulid_to_uuid(run_id))).fetchone()
            if exists is None:
                return None
            current = IdentityBuilder().add_environment().build()
            attempt = reproduction.reproduce(
                conn, organization_id, run_id, current_environment=current)
            _audit(conn, organization_id, actor_id, "run.reproduction_assessed",
                   "run", run_id)
            return {"id": attempt["id"], "originalRunId": run_id,
                    "replayRunId": None, "outcome": attempt["outcome"],
                    "gaps": attempt["gaps"]}

    # ------------------------------------------------------------- internals
    @staticmethod
    def _present_version(conn, organization_id: str,
                         version_id: str) -> dict | None:
        row = conn.execute(
            "SELECT id, prompt_id, version_number, content_digest, state, body, "
            "created_by, created_at, published_at FROM clep.prompt_version "
            "WHERE organization_id = %s AND id = %s",
            (str(organization_id), ulid_to_uuid(version_id))).fetchone()
        if row is None:
            return None
        from clep.identity import uuid_to_ulid
        return {"id": uuid_to_ulid(row[0]), "promptId": uuid_to_ulid(row[1]),
                "versionNumber": row[2], "contentDigest": row[3], "state": row[4],
                "body": row[5], "createdBy": str(row[6]),
                "createdAt": row[7].isoformat() if row[7] else None,
                "publishedAt": row[8].isoformat() if row[8] else None}


#: Namespace for deriving a stable actor identifier from a credential subject.
#: Token issuance and a real principal directory are Phase 12; until then a
#: subject that is not already a UUID is mapped deterministically, so the same
#: caller produces the same actor_id every time and the history of a prompt is
#: still attributable. A random identifier per request would satisfy the column
#: and destroy the requirement.
ACTOR_NAMESPACE = uuid.UUID("6f9619ff-8b86-d011-b42d-00c04fc964ff")


def actor_uuid(subject: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(subject))
    except (ValueError, AttributeError, TypeError):
        return uuid.uuid5(ACTOR_NAMESPACE, str(subject))


def _audit(conn, organization_id: str, actor_id: str, action: str,
           target_type: str, target_id: str) -> None:
    """REQ-F-01-6, and I-33: the runtime role has INSERT and no DELETE, so an
    actor cannot remove the record of their own change."""
    conn.execute(
        "INSERT INTO clep.audit_event (id, organization_id, actor_id, action, "
        "target_type, target_id) VALUES (%s, %s, %s, %s, %s, %s)",
        (uuid.uuid4(), str(organization_id), actor_uuid(actor_id), action,
         target_type, ulid_to_uuid(target_id)))
