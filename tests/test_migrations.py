"""Migrations, and the rule that stops two environments diverging silently."""
from __future__ import annotations

from pathlib import Path

import pytest

from clep.db import migrations
from tests.conftest import MIGRATION_DSN, requires_postgres

ROOT = Path(__file__).resolve().parents[1]


def test_the_schema_specification_is_the_migration_set():
    """There is no second copy of the DDL. If there were, the specification and
    what is deployed could disagree, and only one of them would be reviewed."""
    found = migrations.discover(migrations.schema_dir(ROOT))
    assert [m.filename for m in found] == [
        "01-roles-and-conventions.sql", "02-golden-dataset.sql",
        "03-benchmark-registry.sql", "04-artifacts-and-audit.sql",
        "05-run-and-execution.sql", "06-registry-and-experiments.sql"]


def test_files_must_be_ordered(tmp_path):
    (tmp_path / "no-prefix.sql").write_text("SELECT 1", encoding="utf-8")
    with pytest.raises(migrations.MigrationError, match="ordered"):
        migrations.discover(tmp_path)


def test_an_empty_directory_is_an_error_not_an_empty_plan(tmp_path):
    with pytest.raises(migrations.MigrationError, match="no schema files"):
        migrations.discover(tmp_path)


@requires_postgres
def test_editing_an_applied_file_is_refused(migrated_database):
    """The check that matters. Editing applied history is how one environment
    ends up with a constraint another does not have, with no error anywhere."""
    import psycopg
    found = migrations.discover(migrations.schema_dir(ROOT))
    tampered = [migrations.Migration(found[0].filename,
                                     found[0].sql + "\n-- edited after the fact\n")
                ] + found[1:]
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        with pytest.raises(migrations.MigrationError, match="have changed"):
            migrations.plan(conn, tampered)


@requires_postgres
def test_applying_twice_is_a_no_op(migrated_database):
    import psycopg
    found = migrations.discover(migrations.schema_dir(ROOT))
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        assert migrations.plan(conn, found) == []
        assert migrations.apply(conn, found) == []


@requires_postgres
def test_every_applied_file_is_recorded_with_its_content_hash(migrated_database):
    import psycopg
    found = migrations.discover(migrations.schema_dir(ROOT))
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        recorded = migrations.applied(conn)
    assert {m.filename for m in found} <= set(recorded)
    assert all(recorded[m.filename] == m.content_sha for m in found)
