"""Schema conformance checker.

Verifies that the Phase 4 schema specification actually satisfies the decisions
it claims to implement: ADR-012's four conditions, the Phase 3 tenancy rules, the
naming standards, and the domain model.

This exists because the failure mode it guards is silent. A table added without
FORCE ROW LEVEL SECURITY, or granted UPDATE on an audit table, produces no error
and no failing query -- it produces a schema that looks correct and does not
isolate. Prose review does not reliably catch a missing line in one of nineteen
tables; a parser does.

Usage: python check_schema_conformance.py <repo_root>
Exits non-zero on any failure.
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1]).resolve()
SCHEMA_DIR = ROOT / "docs" / "data" / "schema"
DOMAIN = ROOT / "docs" / "data" / "domain-model.md"

# The tenant root itself is not tenant-scoped: it *is* the tenant.
NOT_TENANT_SCOPED = {"organization"}

# ADR-010 rule 4 enumerated exception: built-in evaluator catalogue rows are
# global and carry a nullable organization_id. Any addition here is a reviewable
# change, which is the point of enumerating rather than inferring.
NULLABLE_ORG = {"evaluator_definition", "evaluator_version"}

# ADR-011 audit class: the runtime role may insert and read, never update or
# delete. An actor must not be able to remove the record of their own action.
AUDIT_TABLES = {"audit_event", "dataset_approval", "suite_grant"}

# Domain-model entities the Phase 4 foundation must realise.
REQUIRED_ENTITIES = {
    "organization", "project", "dataset", "dataset_version", "example",
    "example_content", "dataset_approval", "dataset_lineage",
    "quality_check_result", "benchmark_suite", "suite_version", "suite_grant",
    "evaluator_definition", "evaluator_version", "threshold",
    "artifact", "audit_event",
}

MONEY_TOKEN_HINTS = ("cost", "price", "amount", "token", "spend", "tolerance",
                     "floor", "budget")
FLOATING = ("real", "double precision", "float")
BANNED_BARE_COLUMNS = {"data", "info", "metadata", "value"}

failures = []
notes = []


def fail(check, detail):
    failures.append((check, detail))


def load():
    sql = ""
    files = sorted(SCHEMA_DIR.glob("*.sql"))
    for p in files:
        sql += "\n" + p.read_text(encoding="utf-8")
    return sql, files


def strip_comments(sql):
    return re.sub(r"--[^\n]*", "", sql)


def parse_tables(sql):
    """Return {table: body} for every CREATE TABLE."""
    out = {}
    for m in re.finditer(r"CREATE TABLE\s+clep\.(\w+)\s*\((.*?)\n\);", sql, re.S):
        out[m.group(1)] = m.group(2)
    return out


def parse_columns(body):
    """Return [(name, definition)] for top-level column definitions."""
    cols, depth, buf = [], 0, ""
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cols.append(buf.strip())
            buf = ""
        else:
            buf += ch
    if buf.strip():
        cols.append(buf.strip())
    out = []
    for c in cols:
        c = c.strip()
        if re.match(r"^(CONSTRAINT|PRIMARY KEY|UNIQUE|CHECK|FOREIGN KEY)\b", c, re.I):
            continue
        name = c.split()[0]
        out.append((name, c))
    return out


def main():
    sql_raw, files = load()
    if not files:
        fail("schema", "no .sql files found")
        report()
        return 1
    sql = strip_comments(sql_raw)
    tables = parse_tables(sql)
    notes.append(f"{len(files)} schema file(s), {len(tables)} table(s) parsed")

    # ---------------------------------------------------------------- roles
    roles = dict(re.findall(r"CREATE ROLE\s+(\w+)\s+([^;]+);", sql))
    for role in ("clep_migration", "clep_runtime"):
        if role not in roles:
            fail("ADR-012 D-3/D-4", f"role {role} not declared")
            continue
        attrs = roles[role].upper()
        for required in ("NOSUPERUSER", "NOBYPASSRLS"):
            if required not in attrs:
                fail("ADR-012 D-3", f"role {role} is not {required}")
    if "clep_migration" in roles and "clep_runtime" in roles:
        if not re.search(r"AUTHORIZATION\s+clep_migration", sql):
            fail("ADR-012 D-4", "schema is not owned by the migration role")

    # ---------------------------------------------------------------- tenancy
    tenant_tables = sorted(t for t, b in tables.items()
                           if "organization_id" in b and t not in NOT_TENANT_SCOPED)
    notes.append(f"{len(tenant_tables)} tenant-scoped table(s)")

    for t in sorted(tables):
        if t in NOT_TENANT_SCOPED:
            continue
        body = tables[t]
        if "organization_id" not in body:
            fail("P-1/N-4", f"{t} has no organization_id column")
            continue
        cols = dict(parse_columns(body))
        org = cols.get("organization_id", "")
        if t in NULLABLE_ORG:
            if "NOT NULL" in org.upper():
                fail("ADR-010 rule 4", f"{t} is an enumerated global exception "
                                       f"but organization_id is NOT NULL")
        elif "NOT NULL" not in org.upper():
            fail("P-1/N-4", f"{t}.organization_id is nullable")

    enabled = set(re.findall(r"ALTER TABLE\s+clep\.(\w+)\s+ENABLE ROW LEVEL SECURITY", sql))
    forced = set(re.findall(r"ALTER TABLE\s+clep\.(\w+)\s+FORCE\s+ROW LEVEL SECURITY", sql))
    policies = {}
    for m in re.finditer(r"CREATE POLICY\s+(\w+)\s+ON\s+clep\.(\w+)(.*?);", sql, re.S):
        policies.setdefault(m.group(2), []).append(m.group(3))

    for t in tenant_tables:
        if t not in enabled:
            fail("ADR-012 D-1", f"{t} does not ENABLE ROW LEVEL SECURITY")
        if t not in forced:
            fail("ADR-012 D-2", f"{t} does not FORCE ROW LEVEL SECURITY -- table "
                                f"owners bypass policies without it")
        if t not in policies:
            fail("P-2", f"{t} has row-level security but no policy, so all access is denied")
            continue
        for body in policies[t]:
            if "USING" not in body.upper():
                fail("P-2", f"policy on {t} has no USING clause")
            if "WITH CHECK" not in body.upper():
                fail("P-2", f"policy on {t} has no WITH CHECK clause -- reads would "
                            f"be filtered while a cross-tenant write is permitted")

    # ---------------------------------------------------------------- P-5 composite FKs
    for t, body in tables.items():
        for m in re.finditer(r"FOREIGN KEY\s*\(([^)]*)\)\s*REFERENCES\s+clep\.(\w+)", body):
            cols_ = [c.strip() for c in m.group(1).split(",")]
            target = m.group(2)
            if target in NOT_TENANT_SCOPED or t in NOT_TENANT_SCOPED:
                continue
            if target in NULLABLE_ORG or t in NULLABLE_ORG:
                continue
            if "organization_id" not in cols_:
                fail("P-5", f"{t} references {target} without carrying organization_id; "
                            f"a cross-tenant link would not be rejected")

    # ---------------------------------------------------------------- audit grants
    grants = re.findall(r"GRANT\s+([A-Z, ]+?)\s+ON\s+(.*?)\s+TO\s+(\w+);", sql, re.S)
    for privs, targets, role in grants:
        if role != "clep_runtime":
            continue
        named = set(re.findall(r"clep\.(\w+)", targets))
        privset = {p.strip().upper() for p in privs.split(",")}
        for t in named & AUDIT_TABLES:
            for forbidden in ("UPDATE", "DELETE"):
                if forbidden in privset:
                    fail("I-33/REQ-N-COMP-3",
                         f"runtime role granted {forbidden} on audit-class table {t}")
    granted_tables = set()
    for privs, targets, role in grants:
        if role == "clep_runtime":
            granted_tables |= set(re.findall(r"clep\.(\w+)", targets))
    for t in AUDIT_TABLES:
        if t in tables and t not in granted_tables:
            notes.append(f"audit table {t} has no runtime grant at all")

    # ---------------------------------------------------------------- naming
    for t, body in tables.items():
        if not re.fullmatch(r"[a-z][a-z0-9_]*", t):
            fail("N-1", f"table {t} is not snake_case")
        if t.endswith("s") and not t.endswith("ss"):
            fail("N-1", f"table {t} looks plural; tables are singular")
        if "id uuid PRIMARY KEY" not in re.sub(r"\s+", " ", body):
            fail("N-2", f"{t} has no `id ... PRIMARY KEY` column")
        for name, definition in parse_columns(body):
            low = name.lower()
            if low in BANNED_BARE_COLUMNS:
                fail("N-11", f"{t}.{name} is an unqualified column name")
            if low.endswith("_at") and "timestamptz" not in definition:
                fail("N-6", f"{t}.{name} ends in _at but is not timestamptz")
            if re.match(r"^(is_|has_)", low) and "boolean" not in definition:
                fail("N-7", f"{t}.{name} uses a boolean prefix but is not boolean")
            if low.endswith("_digest") and low != "content_digest" \
                    and not low.endswith("_content_digest"):
                notes.append(f"{t}.{name} is a digest column not named content_digest")
            if any(h in low for h in MONEY_TOKEN_HINTS):
                if any(f in definition.lower() for f in FLOATING):
                    fail("N-9", f"{t}.{name} is a quantity stored as floating point")

    for m in re.finditer(r"CONSTRAINT\s+(\w+)\s+(PRIMARY KEY|UNIQUE|FOREIGN KEY|CHECK)", sql):
        name, kind = m.group(1), m.group(2).upper()
        expected = {"UNIQUE": "uq_", "FOREIGN KEY": "fk_", "CHECK": "ck_"}.get(kind)
        if expected and not name.startswith(expected):
            fail("N-10", f"constraint {name} ({kind}) does not use the {expected} prefix")
    for m in re.finditer(r"CREATE (?:UNIQUE )?INDEX\s+(\w+)", sql):
        name = m.group(1)
        if not name.startswith(("ix_", "uq_")):
            fail("N-10", f"index {name} does not use the ix_ or uq_ prefix")

    # ---------------------------------------------------------------- N-8 enumerated states
    for t, body in tables.items():
        for name, definition in parse_columns(body):
            if name in ("state", "severity", "scope", "direction", "artifact_class",
                        "check_kind", "cost_class", "referencing_type", "split"):
                if not re.search(rf"CHECK\s*\(\s*{name}\s+IS NULL OR {name} IN|"
                                 rf"CHECK\s*\(\s*{name}\s+IN", body):
                    fail("N-8", f"{t}.{name} is an enumerated state with no CHECK constraint")

    # ---------------------------------------------------------------- entity coverage
    missing = sorted(e for e in REQUIRED_ENTITIES if e not in tables)
    if missing:
        fail("domain coverage", f"entities with no table: {missing}")

    report()
    return 1 if failures else 0


def report():
    print("=" * 78)
    print("SCHEMA CONFORMANCE")
    print("=" * 78)
    for n in notes:
        print(f"  note: {n}")
    print()
    if failures:
        by_check = {}
        for check, detail in failures:
            by_check.setdefault(check, []).append(detail)
        for check in sorted(by_check):
            print(f"[FAIL] {check}")
            for d in by_check[check]:
                print(f"         - {d}")
    else:
        print("[PASS] ADR-012 D-1 enable row level security")
        print("[PASS] ADR-012 D-2 force row level security")
        print("[PASS] ADR-012 D-3 runtime role cannot bypass")
        print("[PASS] ADR-012 D-4 migration and runtime roles separated")
        print("[PASS] P-1/N-4 organization_id present and not nullable")
        print("[PASS] P-2 every tenant-scoped table has a policy with USING and WITH CHECK")
        print("[PASS] P-5 cross-tenant links rejected by composite foreign keys")
        print("[PASS] I-33 audit tables never granted UPDATE or DELETE")
        print("[PASS] N-1,2,6,7,8,9,10,11 naming and typing standards")
        print("[PASS] domain entity coverage")
    print("-" * 78)
    print(f"SUMMARY: {'PASS' if not failures else f'FAIL ({len(failures)} defect(s))'}")


if __name__ == "__main__":
    sys.exit(main())
