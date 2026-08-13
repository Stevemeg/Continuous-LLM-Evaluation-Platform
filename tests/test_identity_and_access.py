"""Credentials, authentication and authorization, exercised rather than read.

Every property here is asserted by doing the thing: a forged credential is
presented to a real application, an unauthorized principal is refused by a real
route, the last administrator is revoked against a real database and the store
says no.

The unit half runs anywhere. The integration half needs PostgreSQL, and needs it
for a reason that is not convenience: row-level security is what makes a
credential naming the wrong organization fail, and a fake cannot enforce a
policy.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from clep.db import provision
from clep.db.session import tenant_session
from clep.identity import new_ulid, ulid_to_uuid
from clep.security import credentials as creds
from clep.security.rbac import (PERMISSIONS, Authorization, AuthorizationError,
                                Grant)
from clep.security.repository import (AuthenticationError, SecurityError,
                                      SecurityRepository, authenticate)
from tests.conftest import MIGRATION_DSN, requires_postgres

ORG = new_ulid()


# ============================================================ the credential
def test_a_minted_credential_parses_back_to_what_was_minted():
    minted = creds.mint(ORG)
    parsed = creds.parse(minted.presented)
    assert parsed.organization_id == ORG
    assert parsed.key_id == minted.key_id
    assert parsed.secret == minted.secret


def test_the_secret_verifies_and_a_near_miss_does_not():
    minted = creds.mint(ORG)
    assert creds.verify(minted.secret, minted.salt, minted.verifier,
                        minted.kdf_iterations)
    wrong = minted.secret[:-1] + ("0" if minted.secret[-1] != "0" else "1")
    assert not creds.verify(wrong, minted.salt, minted.verifier,
                            minted.kdf_iterations)


def test_two_credentials_share_no_material():
    a, b = creds.mint(ORG), creds.mint(ORG)
    assert a.secret != b.secret
    assert a.salt != b.salt
    assert a.verifier != b.verifier


def test_the_same_secret_under_a_different_salt_derives_differently():
    """Why the salt is per key. Without it, two tenants choosing the same secret
    would store the same verifier, and one disclosed row would be two."""
    minted = creds.mint(ORG)
    other_salt = creds.os.urandom(creds.SALT_BYTES)
    assert creds.derive(minted.secret, minted.salt) != \
        creds.derive(minted.secret, other_salt)


@pytest.mark.parametrize("presented", [
    "",
    "not-a-credential",
    "clep_short_short_short",
    f"clep_{ORG}_{new_ulid()}",                       # no secret
    f"clep_{ORG}_{new_ulid()}_{new_ulid()}_extra",    # too many segments
    f"bear_{ORG}_{new_ulid()}_{'0' * 32}",            # wrong prefix
    f"clep_{ORG}_{ORG}_{'U' * 32}",                   # excluded letter
    f"clep_{'Z' * 26}_{new_ulid()}_{'0' * 32}",       # 26 chars, over 128 bits
])
def test_a_malformed_credential_is_refused_before_any_lookup(presented):
    with pytest.raises(creds.CredentialError):
        creds.parse(presented)


def test_a_credential_is_refused_when_it_is_not_even_a_string():
    with pytest.raises(creds.CredentialError):
        creds.parse(None)


def test_deriving_at_a_weak_work_factor_is_refused():
    """The floor the schema also enforces. Refusing at the call site says why;
    refusing only at the store says `check constraint violated`."""
    with pytest.raises(creds.CredentialError, match="plain hash"):
        creds.derive("secret", b"0" * 16, iterations=1000)


def test_verification_of_a_weakly_derived_verifier_fails_rather_than_raising():
    minted = creds.mint(ORG)
    assert not creds.verify(minted.secret, minted.salt, minted.verifier,
                            iterations=1)


def test_the_secret_is_absent_from_every_rendering_of_the_key():
    """REQ-N-SEC-5. These objects reach log lines and exception context, which
    is exactly where a credential is lost."""
    minted = creds.mint(ORG)
    assert minted.secret not in repr(minted)
    assert minted.secret not in str(minted)
    assert minted.secret not in f"{minted}"
    parsed = creds.parse(minted.presented)
    assert parsed.secret not in repr(parsed)
    assert parsed.secret not in str(parsed)


def test_minting_without_an_organization_is_refused():
    with pytest.raises(creds.CredentialError):
        creds.mint("not-an-organization")


# =========================================================== the decision
def _authorization(*grants):
    return Authorization(grants=tuple(grants))


def _grant(*permissions, scope="organization", project=None, role="owner"):
    return Grant(role_slug=role, scope_kind=scope, project_id=project,
                 permissions=frozenset(permissions))


def test_a_principal_with_no_binding_may_do_nothing():
    empty = _authorization()
    assert empty.is_empty
    for permission in PERMISSIONS:
        assert not empty.allows(permission)
        assert not empty.allows(permission, project_id=new_ulid())


def test_an_organization_scoped_grant_answers_for_any_project():
    granted = _authorization(_grant("run:create"))
    assert granted.allows("run:create")
    assert granted.allows("run:create", project_id=new_ulid())
    assert not granted.allows("run:cancel")


def test_a_project_scoped_grant_answers_only_for_its_own_project():
    mine, yours = new_ulid(), new_ulid()
    granted = _authorization(_grant("run:create", scope="project", project=mine))
    assert granted.allows("run:create", project_id=mine)
    assert not granted.allows("run:create", project_id=yours)


def test_a_project_scoped_grant_is_not_authority_over_the_organization():
    """A route that names no project is an organization-wide operation, and a
    grant on one project is not authority over the organization."""
    granted = _authorization(
        _grant("credential:manage", scope="project", project=new_ulid()))
    assert not granted.allows("credential:manage")


def test_an_unrecognised_permission_is_an_error_rather_than_a_verdict():
    """Defaulting it either way is worse: True opens a surface, False hides a
    typo behind a plausible 403."""
    with pytest.raises(AuthorizationError):
        _authorization(_grant("run:create")).allows("run:incinerate")


def test_require_raises_and_names_the_permission():
    with pytest.raises(AuthorizationError) as raised:
        _authorization().require("baseline:approve")
    assert raised.value.permission == "baseline:approve"


# =================================================== against the real store
pytestmark_integration = [pytest.mark.integration, requires_postgres]


@pytest.fixture
def repository(migrated_database, organization):
    with tenant_session(migrated_database, organization) as conn:
        yield SecurityRepository(conn, organization), conn


@pytest.mark.integration
@requires_postgres
def test_a_credential_the_caller_invented_is_refused(migrated_database,
                                                     organization):
    forged = creds.mint(_as_ulid(organization))
    with pytest.raises(AuthenticationError):
        authenticate(migrated_database, forged.presented)


@pytest.mark.integration
@requires_postgres
def test_a_real_credential_resolves_to_its_principal_and_its_grants(
        migrated_database, organization):
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        user_id, presented = provision.bootstrap_organization(
            conn, organization, external_subject="owner@example.invalid")
    principal, granted = authenticate(migrated_database, presented)
    assert principal.organization_id == organization
    assert principal.subject == user_id
    assert principal.kind == "user"
    assert granted.allows("role:grant")
    assert granted.allows("run:create", project_id=new_ulid())


@pytest.mark.integration
@requires_postgres
def test_a_credential_presented_against_the_wrong_organization_is_refused(
        migrated_database, organization, second_organization):
    """ADR-019 rule 3, which is the whole reason the organization is in the
    credential. The key is real; the organization it names is not the one that
    owns it, so row-level security hides the row and the lookup finds nothing."""
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        _user, presented = provision.bootstrap_organization(
            conn, organization, external_subject="owner@example.invalid")
    parsed = creds.parse(presented)
    relabelled = (f"{creds.PREFIX}_{_as_ulid(second_organization)}"
                  f"_{parsed.key_id}_{parsed.secret}")
    with pytest.raises(AuthenticationError):
        authenticate(migrated_database, relabelled)


@pytest.mark.integration
@requires_postgres
def test_a_real_key_with_the_wrong_secret_is_refused(migrated_database,
                                                     organization):
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        _user, presented = provision.bootstrap_organization(
            conn, organization, external_subject="owner@example.invalid")
    parsed = creds.parse(presented)
    wrong = (f"{creds.PREFIX}_{parsed.organization_id}_{parsed.key_id}"
             f"_{creds.new_secret()}")
    with pytest.raises(AuthenticationError):
        authenticate(migrated_database, wrong)


@pytest.mark.integration
@requires_postgres
def test_a_revoked_credential_is_refused(migrated_database, organization):
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        _user, presented = provision.bootstrap_organization(
            conn, organization, external_subject="owner@example.invalid")
    parsed = creds.parse(presented)
    authenticate(migrated_database, presented)          # works before
    with tenant_session(migrated_database, organization) as conn:
        assert SecurityRepository(conn, organization).revoke_api_key(parsed.key_id)
    with pytest.raises(AuthenticationError):
        authenticate(migrated_database, presented)


@pytest.mark.integration
@requires_postgres
def test_an_expired_credential_is_refused(migrated_database, organization):
    user_id, presented = _bootstrap(organization)
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        key_id, expiring = repo.issue_api_key(
            principal_kind="user", subject_id=user_id, display_name="short",
            actor_id=user_id,
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(AuthenticationError):
        authenticate(migrated_database, expiring)
    # And the unexpired one still works, so the refusal was the expiry.
    authenticate(migrated_database, presented)


@pytest.mark.integration
@requires_postgres
def test_the_store_holds_no_secret_anywhere_in_the_key_row(migrated_database,
                                                           organization):
    """I-2, checked against every text-bearing column rather than against the
    one we expect. A secret that leaked into `display_name` would satisfy a
    check that only looked at `verifier`."""
    _user, presented = _bootstrap(organization)
    secret = creds.parse(presented).secret
    with tenant_session(migrated_database, organization) as conn:
        row = conn.execute("SELECT * FROM clep.api_key").fetchone()
        rendered = " ".join(
            v.hex() if isinstance(v, (bytes, memoryview)) else str(v)
            for v in row)
    assert secret not in rendered
    assert presented not in rendered


@pytest.mark.integration
@requires_postgres
def test_issuing_returns_the_credential_once_and_listing_never_does(
        migrated_database, organization):
    user_id, _presented = _bootstrap(organization)
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        key_id, issued = repo.issue_api_key(
            principal_kind="user", subject_id=user_id, display_name="ci",
            actor_id=user_id)
        listed = repo.list_api_keys()
    secret = creds.parse(issued).secret
    assert secret not in str([vars(k) for k in listed])
    assert any(k.id == key_id for k in listed)


@pytest.mark.integration
@requires_postgres
def test_rotation_issues_a_new_key_and_revokes_the_old_one(migrated_database,
                                                           organization):
    user_id, presented = _bootstrap(organization)
    old_id = creds.parse(presented).key_id
    with tenant_session(migrated_database, organization) as conn:
        new_id, replacement = SecurityRepository(conn, organization) \
            .rotate_api_key(old_id, actor_id=user_id)
    assert new_id != old_id
    principal, _granted = authenticate(migrated_database, replacement)
    assert principal.subject == user_id
    with pytest.raises(AuthenticationError):
        authenticate(migrated_database, presented)
    with tenant_session(migrated_database, organization) as conn:
        rows = {k.id: k for k in
                SecurityRepository(conn, organization).list_api_keys()}
    assert rows[old_id].revocation_reason == "rotated"
    assert rows[old_id].rotated_to == new_id
    assert rows[new_id].state == "active"


@pytest.mark.integration
@requires_postgres
def test_rotating_an_already_revoked_key_is_refused(migrated_database,
                                                    organization):
    user_id, presented = _bootstrap(organization)
    key_id = creds.parse(presented).key_id
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        repo.revoke_api_key(key_id)
        with pytest.raises(SecurityError, match="already been revoked"):
            repo.rotate_api_key(key_id, actor_id=user_id)


@pytest.mark.integration
@requires_postgres
def test_a_key_cannot_be_issued_for_a_principal_of_another_tenant(
        migrated_database, organization, second_organization):
    _mine, _presented = _bootstrap(organization)
    theirs, _their_key = _bootstrap(second_organization,
                                    subject="them@example.invalid")
    with tenant_session(migrated_database, organization) as conn:
        with pytest.raises(SecurityError, match="not an active member"):
            SecurityRepository(conn, organization).issue_api_key(
                principal_kind="user", subject_id=theirs,
                display_name="borrowed", actor_id=_mine)


@pytest.mark.integration
@requires_postgres
def test_the_last_administrative_binding_cannot_be_revoked(migrated_database,
                                                           organization):
    """I-4, enforced by the store rather than by the service (ADR-020 rule 9).

    The repository is not asked politely; the trigger refuses, and the refusal
    arrives as a `RestrictViolation` the repository translates.
    """
    user_id, _presented = _bootstrap(organization)
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        bindings = repo.list_role_bindings()
        assert len(bindings) == 1 and bindings[0].role_slug == "owner"
        with pytest.raises(SecurityError, match="role:grant"):
            repo.revoke_role_binding(bindings[0].id)


@pytest.mark.integration
@requires_postgres
def test_the_last_administrator_may_be_revoked_once_a_second_one_exists(
        migrated_database, organization):
    """The invariant is "at least one", not "the first one forever"."""
    user_id, _presented = _bootstrap(organization)
    second = _bootstrap(organization, subject="second@example.invalid")[0]
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        first = [b for b in repo.list_role_bindings()
                 if b.subject == "owner@example.invalid"][0]
        assert repo.revoke_role_binding(first.id)
        remaining = [b for b in repo.list_role_bindings() if b.state == "active"]
    assert remaining and all(b.role_slug == "owner" for b in remaining)


@pytest.mark.integration
@requires_postgres
def test_a_binding_cannot_name_a_role_that_does_not_exist(migrated_database,
                                                          organization):
    user_id, _presented = _bootstrap(organization)
    with tenant_session(migrated_database, organization) as conn:
        with pytest.raises(SecurityError, match="no such role"):
            SecurityRepository(conn, organization).create_role_binding(
                role_slug="superuser", principal_kind="user",
                subject_id=user_id, scope_kind="organization", project_id=None,
                actor_id=user_id)


@pytest.mark.integration
@requires_postgres
def test_a_project_scoped_binding_needs_a_project_and_the_reverse(
        migrated_database, organization, seeded):
    user_id, _presented = _bootstrap(organization)
    with tenant_session(migrated_database, organization) as conn:
        repo = SecurityRepository(conn, organization)
        with pytest.raises(SecurityError, match="project-scoped binding"):
            repo.create_role_binding(
                role_slug="analyst", principal_kind="user", subject_id=user_id,
                scope_kind="project", project_id=None, actor_id=user_id)
        with pytest.raises(SecurityError, match="project-scoped binding"):
            repo.create_role_binding(
                role_slug="analyst", principal_kind="user", subject_id=user_id,
                scope_kind="organization", project_id=seeded["project"],
                actor_id=user_id)


@pytest.mark.integration
@requires_postgres
def test_the_role_catalogue_is_readable_and_not_writable(migrated_database,
                                                         organization):
    """ADR-010 rule 4 done properly: a catalogue every tenant reads and none
    writes. The refusal is a privilege error, not an application check."""
    with tenant_session(migrated_database, organization) as conn:
        roles = {r["slug"] for r in SecurityRepository(conn, organization).roles()}
    assert roles == {"owner", "maintainer", "analyst", "auditor", "service"}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with tenant_session(migrated_database, organization) as conn:
            conn.execute("INSERT INTO clep.role (slug, display_name, description) "
                         "VALUES ('superuser', 'Superuser', 'everything')")


@pytest.mark.integration
@requires_postgres
@pytest.mark.parametrize("table", ["role", "role_permission"])
def test_the_catalogue_tables_are_keyed_on_id_like_every_other_table(
        migrated_database, table):
    """N-2, read from the live catalogue rather than from the DDL.

    Both tables were first written with a natural key — `slug` for one, the
    `(role_slug, permission)` pair for the other — which the Phase 4 conformance
    checker correctly rejected. Asserted here against `pg_index` because the
    question is what the database enforces, not what the file says.
    """
    with psycopg.connect(migrated_database) as conn:
        columns = [r[0] for r in conn.execute(
            "SELECT a.attname FROM pg_index i "
            "  JOIN pg_class c ON c.oid = i.indrelid "
            "  JOIN pg_namespace n ON n.oid = c.relnamespace "
            "  JOIN pg_attribute a ON a.attrelid = c.oid "
            "                     AND a.attnum = ANY(i.indkey) "
            " WHERE n.nspname = 'clep' AND c.relname = %s AND i.indisprimary "
            " ORDER BY a.attname", (table,)).fetchall()]
    assert columns == ["id"], f"{table} is keyed on {columns}"


@pytest.mark.integration
@requires_postgres
def test_the_semantics_the_natural_keys_carried_are_still_enforced(
        migrated_database, organization):
    """Changing the primary key must not have loosened anything.

    A surrogate key trivially permits two rows that a natural key forbade, so
    the uniqueness those keys carried is asserted by attempting the duplicates
    the old keys made impossible.
    """
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(MIGRATION_DSN) as conn:
            conn.execute(
                "INSERT INTO clep.role (id, slug, display_name, description) "
                "VALUES (gen_random_uuid(), 'owner', 'Duplicate', 'x')")
    with pytest.raises(psycopg.errors.UniqueViolation):
        with psycopg.connect(MIGRATION_DSN) as conn:
            conn.execute(
                "INSERT INTO clep.role_permission (id, role_slug, permission) "
                "VALUES (gen_random_uuid(), 'owner', 'run:create')")


@pytest.mark.integration
@requires_postgres
def test_every_seeded_catalogue_row_received_a_distinct_identifier(
        migrated_database):
    """The seed inserts call `gen_random_uuid()` once per row. A single shared
    identifier would satisfy a primary key of one row and fail on the second, so
    this would have failed loudly — but it is asserted rather than assumed,
    because the seed is the only place these identifiers are ever generated."""
    with psycopg.connect(migrated_database) as conn:
        for table in ("role", "role_permission"):
            rows, distinct = conn.execute(
                f"SELECT count(*), count(DISTINCT id) FROM clep.{table}"
            ).fetchone()
            assert rows == distinct > 0, f"{table}: {rows} rows, {distinct} ids"


@pytest.mark.integration
@requires_postgres
def test_a_role_binding_still_reaches_its_role_through_the_slug(
        migrated_database, organization):
    """The foreign key targets `slug`, which remains unique. A surrogate primary
    key does not mean an opaque reference: `role_binding.role_slug` stays
    readable, and the store still refuses a role that does not exist."""
    user_id, _presented = _bootstrap(organization)
    with tenant_session(migrated_database, organization) as conn:
        holders = {b.role_slug for b in
                   SecurityRepository(conn, organization).list_role_bindings()}
    assert holders == {"owner"}
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with psycopg.connect(MIGRATION_DSN) as conn:
            conn.execute(
                "INSERT INTO clep.role_binding (id, organization_id, role_slug, "
                "principal_kind, app_user_id, scope_kind, created_by) "
                "VALUES (%s, %s, 'sovereign', 'user', %s, 'organization', %s)",
                (ulid_to_uuid(new_ulid()), organization, ulid_to_uuid(user_id),
                 ulid_to_uuid(user_id)))


@pytest.mark.integration
@requires_postgres
def test_every_role_grants_only_permissions_the_platform_recognises(
        migrated_database, organization):
    with tenant_session(migrated_database, organization) as conn:
        for role in SecurityRepository(conn, organization).roles():
            unknown = set(role["permissions"]) - set(PERMISSIONS)
            assert not unknown, f"{role['slug']} grants {unknown}"


@pytest.mark.integration
@requires_postgres
def test_the_service_role_cannot_approve_a_baseline_or_waive_a_gate():
    """Stated in the catalogue's own description, and asserted here: a pipeline
    that can waive its own gate is not a gate."""
    from tests.conftest import MIGRATION_DSN as _dsn
    with psycopg.connect(_dsn) as conn:
        granted = {r[0] for r in conn.execute(
            "SELECT permission FROM clep.role_permission "
            " WHERE role_slug = 'service'").fetchall()}
    assert "baseline:approve" not in granted
    assert "gate:except" not in granted
    assert "role:grant" not in granted
    assert "credential:manage" not in granted


@pytest.mark.integration
@requires_postgres
def test_a_second_tenant_sees_none_of_the_first_tenants_credentials(
        migrated_database, organization, second_organization):
    _bootstrap(organization)
    _bootstrap(second_organization, subject="them@example.invalid")
    with tenant_session(migrated_database, second_organization) as conn:
        keys = SecurityRepository(conn, second_organization).list_api_keys()
        bindings = SecurityRepository(conn, second_organization).list_role_bindings()
    assert len(keys) == 1
    assert len(bindings) == 1
    assert all(k.subject == "them@example.invalid" for k in keys)


@pytest.mark.integration
@requires_postgres
def test_a_user_directory_does_not_leak_across_tenants(migrated_database,
                                                       organization,
                                                       second_organization):
    """`app_user` carries no organization_id, which is what ADR-010 rule 4
    permits. Its visibility comes from membership, which is what stops a global
    table from being a directory of everybody's people."""
    _bootstrap(organization, subject="mine@example.invalid")
    _bootstrap(second_organization, subject="theirs@example.invalid")
    with tenant_session(migrated_database, organization) as conn:
        visible = {r[0] for r in conn.execute(
            "SELECT external_subject FROM clep.app_user").fetchall()}
    assert visible == {"mine@example.invalid"}


# ------------------------------------------------------------------ helpers
def _as_ulid(organization_id: str) -> str:
    from clep.identity import uuid_to_ulid
    return uuid_to_ulid(uuid.UUID(str(organization_id)))


def _bootstrap(organization_id: str, subject: str = "owner@example.invalid"):
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        return provision.bootstrap_organization(
            conn, organization_id, external_subject=subject)
