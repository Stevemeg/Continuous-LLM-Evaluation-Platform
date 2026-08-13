"""The Phase 4 schema checker's scope model, exercised over synthetic schemas.

Phase 12 corrected a defect in the checker rather than in the schema. It modelled
two scope categories — the tenant root, and dual-scoped rows carrying a nullable
`organization_id` — and had never been told about the third that
`data-model.md` P-4 has always permitted: tables that are globally scoped and
carry no tenant column at all. When Phase 12 realised the canonical global
entities, the checker reported them as P-1/N-4 defects. The specification allowed
them; the checker had not caught up.

Correcting a validator is more dangerous than correcting an implementation,
because a validator that has been loosened stops reporting the thing it exists
for and nothing else changes. So the exemption is asserted from both sides here,
against a checker executed as a subprocess over schemas written for the purpose:

  A. a canonical global table is accepted;
  B. an ordinary table with no tenant column is still rejected;
  C. a table cannot become exempt by *resembling* a global one;
  D. a table declared global cannot quietly acquire a tenant column.

C is the one that matters most. An exemption implemented as a prefix or a
pattern is an exemption a future table can satisfy by accident, and the accident
would be silent — which is precisely the failure mode the checker exists to
prevent.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "docs" / "evidence" / "phase-4" / "check_schema_conformance.py"

#: Enough of a schema for the checker to parse and reach the tenancy rules. It
#: will report plenty of other defects — no roles, no policies, missing domain
#: entities — and that is fine: every assertion below is about the presence or
#: absence of one specific line, never about the exit code. A test that demanded
#: a clean run would be a test about the fixture rather than about the rule.
PREAMBLE = """
CREATE SCHEMA IF NOT EXISTS clep AUTHORIZATION clep_migration;
"""

TENANT_TABLE = """
CREATE TABLE clep.{name} (
    id               uuid PRIMARY KEY,
    organization_id  uuid NOT NULL,
    label            text NOT NULL
);
"""

UNSCOPED_TABLE = """
CREATE TABLE clep.{name} (
    id     uuid PRIMARY KEY,
    label  text NOT NULL
);
"""


def run_checker(tmp_path: Path, *table_sql: str) -> str:
    schema_dir = tmp_path / "docs" / "data" / "schema"
    schema_dir.mkdir(parents=True)
    (schema_dir / "01-synthetic.sql").write_text(
        PREAMBLE + "\n".join(table_sql), encoding="utf-8")
    (tmp_path / "docs" / "data" / "domain-model.md").write_text(
        "# synthetic", encoding="utf-8")
    result = subprocess.run([sys.executable, str(CHECKER), str(tmp_path)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    return result.stdout + result.stderr


def missing_tenant_column(output: str, table: str) -> bool:
    return f"{table} has no organization_id column" in output


# ------------------------------------------------------------------------ A
@pytest.mark.parametrize("table", ["app_user", "role", "role_permission"])
def test_a_canonical_global_table_is_accepted_without_a_tenant_column(
        tmp_path, table):
    """`data-model.md` P-4. These three are the physical realisation of the
    canonical global category, and the checker must not demand a tenant column
    of a table the specification says has no tenant."""
    output = run_checker(tmp_path, UNSCOPED_TABLE.format(name=table))
    assert not missing_tenant_column(output, table)


# ------------------------------------------------------------------------ B
def test_an_ordinary_table_with_no_tenant_column_is_still_rejected(tmp_path):
    """The rule the exemption must not have weakened. If this ever stops
    failing, the checker has been turned into one that accepts anything."""
    output = run_checker(tmp_path, UNSCOPED_TABLE.format(name="widget"))
    assert missing_tenant_column(output, "widget")


def test_a_tenant_table_that_keeps_its_column_is_unaffected(tmp_path):
    output = run_checker(tmp_path, TENANT_TABLE.format(name="widget"))
    assert not missing_tenant_column(output, "widget")


# ------------------------------------------------------------------------ C
@pytest.mark.parametrize("impostor", [
    "app_user_note",        # a prefix of a global name
    "user_role",            # the words, rearranged
    "role_permissions",     # one character away, and plural
    "my_role",              # a suffix of a global name
    "app_user2",            # the name with a digit
    "ROLE",                 # the name in another case
])
def test_a_table_cannot_become_exempt_by_resembling_a_global_one(tmp_path,
                                                                 impostor):
    """The exemption is exact set membership and nothing else.

    Implemented as a prefix or a pattern it would be an exemption a future table
    could satisfy by accident — and the accident would be a table with no tenant
    column that nobody was warned about, which is the entire class of defect this
    checker exists to make impossible.
    """
    output = run_checker(tmp_path, UNSCOPED_TABLE.format(name=impostor))
    assert missing_tenant_column(output, impostor), (
        f"{impostor} was exempted; the global set is matching by shape rather "
        f"than by identity")


# ------------------------------------------------------------------------ D
@pytest.mark.parametrize("table", ["app_user", "role", "role_permission"])
def test_a_global_table_that_acquires_a_tenant_column_is_reported(tmp_path,
                                                                  table):
    """The exemption checked in the other direction, which is what keeps it
    honest. A table declared global that grows an `organization_id` means the
    schema and the enumeration disagree about its scope, and either could be the
    one that is wrong."""
    output = run_checker(tmp_path, TENANT_TABLE.format(name=table))
    assert f"{table} is an enumerated global table but carries an" in output


# ------------------------------------------------------- the real schema too
def test_the_live_schema_passes_the_corrected_checker():
    """Not a synthetic fixture: the schema that is actually deployed.

    The three canonical globals live in it, and so do eighty tenant-scoped
    tables that must still be checked for the column this exemption withholds.
    """
    result = subprocess.run([sys.executable, str(CHECKER), str(ROOT)],
                            capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=300)
    assert result.returncode == 0, result.stdout[-3000:]
    assert "SUMMARY: PASS" in result.stdout
