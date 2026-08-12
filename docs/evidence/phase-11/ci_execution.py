"""M11.2 — the CLI as an actual installed console script, in a clean environment.

Every earlier proof that the CLI works ran it as `main(argv)` or `python -m`,
inside the development environment, with the working tree on `sys.path`. None of
those is what a CI job does, and each of them hides a different failure: a
missing `[project.scripts]` entry, a package that only imports because the
repository happens to be the working directory, a module that resolves through an
editable install.

So this script builds the thing a pipeline actually has.

    git clone            a clean checkout, committed content only
      -> python -m venv  an isolated interpreter, no site packages inherited
      -> pip install .   a real installation, NOT editable
      -> clep            the console script, by name, resolved from PATH
      -> exit code       captured from the process, never inferred
      -> a CI decision   computed the way a shell does: zero continues, else stop

**This is a local CI-style environment, not hosted CI.** It is labelled that way
in the evidence it writes, and nothing here claims a run on GitHub Actions or any
other service. What it does claim is the part that matters and that a hosted
runner would not make truer: the package installs from a clean checkout, the
console script resolves, and the exit code a pipeline reads is the one the gate
decided.

Three refusals keep it honest.

  * No database, no evidence. The script exits non-zero and writes nothing
    rather than reporting a CI run that did not evaluate anything.
  * The installed package must resolve inside the venv. If `clep.__file__` points
    at the working tree, the isolation failed and the result would be about the
    development environment.
  * Exit codes are read from the process. There is no path here that computes
    what the code should have been.

**Paths are normalised before anything is written.** The evidence has to show
that the package resolved inside an isolated environment and not in the working
tree, and the honest way to show that is the path — but the real path also
carries the operator's account name and temp-directory layout, which is private
local detail that no reader of this repository needs. Every recorded string is
therefore rewritten through a deterministic token table before it is persisted:

    <repo>       the repository root
    <work>       the throwaway directory this run created
    <checkout>   the clean git clone inside it
    <venv>       the isolated environment inside it
    <workspace>  the neutral directory the CLI is invoked from
    <tmp>        the system temporary directory
    <home>       the user's home directory

Longest path wins, so `<venv>` is never reported as `<work>/venv`. The claim is
preserved rather than weakened: `<venv>/Lib/site-packages/clep/__init__.py` says
exactly what the raw path said, and the boolean it supports — *is this inside the
isolated environment* — is still computed from the raw path before substitution.

`_refuse_local_paths` is the backstop. It runs over the finished evidence and
raises rather than writing if a drive-letter path, the account name or a temp
directory survived, so this script cannot emit leaky evidence even if a new
field is added later and someone forgets.

Usage: python docs/evidence/phase-11/ci_execution.py <repo_root> [--keep]
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from decimal import Decimal
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
KEEP = "--keep" in sys.argv
sys.path.insert(0, str(ROOT / "src"))

MIGRATION_DSN = os.environ.get("CLEP_MIGRATION_DSN",
                               "postgresql://postgres@localhost:5439/clep")
RUNTIME_DSN = os.environ.get("CLEP_RUNTIME_DSN",
                             "postgresql://clep_app@localhost:5439/clep")

#: What a CI job is told. Recorded with every invocation so a reader can see the
#: contract was one integer and nothing else.
BLOCKS = "the pipeline stops"
CONTINUES = "the pipeline continues"

steps: list[dict] = []
failures: list[str] = []

#: (real path, token), longest first. Populated once, in `main`.
REDACTIONS: list[tuple[str, str]] = []


def register_redactions(pairs: list[tuple[Path, str]]) -> None:
    """Fix the token table for this run.

    Sorted by descending length so a nested directory is matched before its
    parent — otherwise the environment would be reported as `<work>/venv`, and
    the claim that the package resolved *inside the isolated environment* would
    rest on the reader reassembling it.
    """
    global REDACTIONS
    resolved = [(str(Path(p).resolve()).replace("\\", "/"), token)
                for p, token in pairs]
    REDACTIONS = sorted(resolved, key=lambda kv: -len(kv[0]))


def redact(value):
    """Rewrite local paths as tokens. Recurses through the evidence structure."""
    if isinstance(value, str):
        out = value.replace("\\\\", "/").replace("\\", "/")
        for real, token in REDACTIONS:
            out = re.sub(re.escape(real), token, out, flags=re.IGNORECASE)
        return out
    if isinstance(value, list):
        return [redact(v) for v in value]
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    return value


def _refuse_local_paths(evidence: dict) -> None:
    """Refuse to write evidence that still carries private local detail.

    A backstop, not the mechanism. If this fires, a field was added that the
    token table never saw — and writing the file anyway is how the disclosure
    reaches the repository a second time.

    Separators are normalised first. The fields this exists to catch are exactly
    the ones that did NOT go through `redact`, so they still carry backslashes —
    and `json.dumps` escapes each one again. The first version of this searched
    for `C:/` and passed a literal `C:\\Users\\...` straight through, which the
    proof in the self-test caught. A guard aimed only at the shape its own
    mechanism produces is a guard that cannot fire.
    """
    body = json.dumps(evidence).replace("\\\\", "/").replace("\\", "/")
    account = Path.home().name
    for pattern, what in ((r"[A-Za-z]:/", "a drive-letter path"),
                          (r"(?i)AppData/Local/Temp", "the temp directory"),
                          (rf"(?i)\b{re.escape(account)}\b", "the account name"),
                          (r"(?i)/home/[a-z0-9._\-]+/", "a POSIX home directory"),
                          (r"(?i)/Users/[a-z0-9._\-]+/", "a macOS home directory")):
        if re.search(pattern, body):
            raise SystemExit(
                f"REFUSING to write evidence: {what} survived redaction. The "
                f"token table in `register_redactions` does not cover a field "
                f"this run recorded.")


def note(name: str, ok: bool, detail: str, extra: dict | None = None) -> None:
    detail = redact(detail)
    steps.append({"step": name, "ok": bool(ok), "detail": detail,
                  **redact(extra or {})})
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(f"{name}: {detail}")


def run(cmd, cwd=None, env=None, timeout=1800):
    p = subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None,
                       env=env, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or ""), (p.stderr or "")


def script_path(venv: Path, name: str) -> Path:
    folder = "Scripts" if os.name == "nt" else "bin"
    suffix = ".exe" if os.name == "nt" else ""
    return venv / folder / f"{name}{suffix}"


def on_path(env: dict, name: str) -> str:
    """Resolve a command the way a shell does, against the pipeline's own PATH.

    Windows' `CreateProcess` searches the *parent* process's PATH, not the one
    handed to the child, so passing `env` alone would look up `clep` in this
    development environment and prove nothing. Resolving here makes the search
    explicit and the answer checkable: the path returned must be inside the
    isolated environment or the step is meaningless.
    """
    found = shutil.which(name, path=env["PATH"])
    if not found:
        raise FileNotFoundError(
            f"{name!r} is not on the pipeline PATH; the console script did not "
            f"install")
    return found


def pipeline_env(venv: Path, organization: str) -> dict:
    """The environment a CI step runs in, and deliberately not this one.

    `PYTHONPATH` and `PYTHONHOME` are removed rather than overwritten: an
    inherited `PYTHONPATH` pointing at the working tree is exactly how a CLI
    that is not really installed appears to work.
    """
    folder = "Scripts" if os.name == "nt" else "bin"
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
                        "CLEP_MIGRATION_DSN")}
    env["PATH"] = str(venv / folder) + os.pathsep + env.get("PATH", "")
    env["CLEP_RUNTIME_DSN"] = RUNTIME_DSN
    env["CLEP_ORGANIZATION"] = organization
    env["CLEP_ACTOR"] = "ci"
    return env


# ============================================================ 1. the database
def database_is_reachable() -> bool:
    try:
        import psycopg
        with psycopg.connect(MIGRATION_DSN, connect_timeout=5):
            return True
    except Exception as e:  # noqa: BLE001 - reported, not swallowed
        print(f"REFUSING: PostgreSQL is not reachable at {MIGRATION_DSN}: {e}")
        print("A CI evidence file describing an evaluation that never ran would "
              "be worse than no evidence at all. Start the stack with "
              "`docker compose up -d` and run this again.")
        return False


def prepare_database() -> dict:
    """A schema, a tenant, and three gate decisions waiting to be read.

    Seeded here rather than through the CLI on purpose: `clep` has three
    read-only subcommands and REQ-F-10-3 is the reason. A CLI that could create
    the run it then judges would be a CLI that can change what it is judged on.
    """
    import psycopg

    from clep.db import migrations, provision
    from clep.identity import new_ulid, ulid_to_uuid

    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        conn.execute("DROP SCHEMA IF EXISTS clep CASCADE")
        conn.execute("DROP TABLE IF EXISTS clep_schema_history")
        for role in ("clep_app", "clep_runtime", "clep_migration"):
            conn.execute(
                f"DO $$BEGIN IF EXISTS (SELECT 1 FROM pg_roles WHERE "
                f"rolname='{role}') THEN EXECUTE 'DROP OWNED BY {role}'; "
                f"EXECUTE 'DROP ROLE {role}'; END IF; END$$;")
        applied = migrations.apply(conn,
                                   migrations.discover(migrations.schema_dir(ROOT)))
        provision.ensure_login_roles(conn, os.environ.get("CLEP_RUNTIME_PASSWORD", ""))

    organization = str(uuid.uuid4())
    ids = {k: new_ulid() for k in
           ("project", "dataset", "dataset_version", "suite", "suite_version",
            "provider", "model", "model_configuration", "evaluator_definition",
            "evaluator_version")}
    examples = [new_ulid() for _ in range(10)]
    u = ulid_to_uuid
    with psycopg.connect(MIGRATION_DSN, autocommit=True) as conn:
        conn.execute("INSERT INTO clep.organization (id, slug, display_name)"
                     " VALUES (%s,%s,'CI Tenant')",
                     (organization, f"ci-{organization[:8]}"))
        conn.execute("INSERT INTO clep.project (id, organization_id, slug,"
                     " display_name) VALUES (%s,%s,'ci','CI Project')",
                     (u(ids["project"]), organization))
        conn.execute("INSERT INTO clep.dataset (id, organization_id, project_id,"
                     " slug, display_name) VALUES (%s,%s,%s,'ds','Dataset')",
                     (u(ids["dataset"]), organization, u(ids["project"])))
        conn.execute("INSERT INTO clep.dataset_version (id, organization_id,"
                     " dataset_id, version_number, content_digest, schema_ref,"
                     " state) VALUES (%s,%s,%s,1,%s,'schema://example/v1','draft')",
                     (u(ids["dataset_version"]), organization, u(ids["dataset"]),
                      "sha256:" + "a" * 64))
        for ordinal, example_id in enumerate(examples, start=1):
            conn.execute("INSERT INTO clep.example (id, organization_id,"
                         " dataset_version_id, ordinal, split)"
                         " VALUES (%s,%s,%s,%s,'test')",
                         (u(example_id), organization, u(ids["dataset_version"]),
                          ordinal))
        conn.execute("INSERT INTO clep.benchmark_suite (id, organization_id,"
                     " project_id, slug, display_name, owner_actor_id)"
                     " VALUES (%s,%s,%s,'suite','Suite',%s)",
                     (u(ids["suite"]), organization, u(ids["project"]),
                      uuid.uuid4()))
        conn.execute("INSERT INTO clep.suite_version (id, organization_id,"
                     " benchmark_suite_id, version_number, content_digest,"
                     " owner_actor_id) VALUES (%s,%s,%s,1,%s,%s)",
                     (u(ids["suite_version"]), organization, u(ids["suite"]),
                      "sha256:" + "d" * 64, uuid.uuid4()))
        conn.execute("INSERT INTO clep.suite_member (id, organization_id,"
                     " suite_version_id, dataset_version_id) VALUES (%s,%s,%s,%s)",
                     (uuid.uuid4(), organization, u(ids["suite_version"]),
                      u(ids["dataset_version"])))
        conn.execute("INSERT INTO clep.evaluator_definition (id, organization_id,"
                     " scope, slug, display_name) VALUES (%s,NULL,'builtin',%s,"
                     "'Exact Match')",
                     (u(ids["evaluator_definition"]),
                      f"exact_match_ci_{ids['evaluator_definition'][-8:].lower()}"))
        conn.execute("INSERT INTO clep.evaluator_version (id, organization_id,"
                     " evaluator_definition_id, version_number, content_digest,"
                     " input_schema_ref, output_schema_ref, declared_permissions,"
                     " is_deterministic, cost_class) VALUES (%s,NULL,%s,1,%s,"
                     "'schema://in/v1','schema://out/v1','none',true,'free')",
                     (u(ids["evaluator_version"]), u(ids["evaluator_definition"]),
                      "sha256:" + "c" * 64))
        conn.execute("INSERT INTO clep.suite_evaluator (id, organization_id,"
                     " suite_version_id, evaluator_version_id)"
                     " VALUES (%s,%s,%s,%s)",
                     (uuid.uuid4(), organization, u(ids["suite_version"]),
                      u(ids["evaluator_version"])))
        conn.execute("INSERT INTO clep.provider (id, organization_id, slug,"
                     " display_name, endpoint_kind)"
                     " VALUES (%s,%s,'stub','Stub','hosted')",
                     (u(ids["provider"]), organization))
        conn.execute("INSERT INTO clep.model (id, organization_id, provider_id,"
                     " model_identifier, display_name) VALUES (%s,%s,%s,'m','M')",
                     (u(ids["model"]), organization, u(ids["provider"])))
        conn.execute("INSERT INTO clep.model_configuration (id, organization_id,"
                     " model_id, version_number, output_affecting_parameters,"
                     " content_digest, seed, is_deterministic, created_by)"
                     " VALUES (%s,%s,%s,1,'{\"temperature\": 0}',%s,7,true,%s)",
                     (u(ids["model_configuration"]), organization, u(ids["model"]),
                      "sha256:" + "e" * 64, uuid.uuid4()))
        metric = conn.execute(
            "SELECT slug FROM clep.evaluator_definition WHERE id = %s",
            (u(ids["evaluator_definition"]),)).fetchone()[0]

    ids["organization"] = organization
    ids["examples"] = examples
    ids["metric"] = metric
    return ids


def build_run(seeded, scores, *, key) -> str:
    from clep.db.session import tenant_session
    from clep.experiments.identity import IdentityBuilder, digest_of
    from clep.experiments.repository import IdentityRepository
    from clep.orchestration.repository import RunRepository

    org = seeded["organization"]
    with tenant_session(RUNTIME_DSN, org) as conn:
        repo = RunRepository(conn, org)
        run_id = repo.create_run(
            project_id=seeded["project"],
            suite_version_id=seeded["suite_version"],
            dataset_version_id=seeded["dataset_version"],
            identity_digest="sha256:" + "0" * 64, integration_tier="output_only",
            idempotency_key=key, trigger_kind="pull_request")
        candidate_id = repo.add_candidate(
            run_id, label="a",
            model_configuration_id=seeded["model_configuration"],
            endpoint_kind="hosted")
        for index, (example_id, score) in enumerate(zip(seeded["examples"], scores)):
            sample_id, _ = repo.record_sample(
                run_id=run_id, candidate_id=candidate_id, candidate_label="a",
                example_id=example_id, sample_index=index, resolution="scored",
                score=score, model_latency_ms=40 + index)
            repo.record_evaluator_outcome(
                sample_id=sample_id,
                evaluator_version_id=seeded["evaluator_version"],
                resolution="scored", score=score, unavailable_reason=None,
                duration_ms=12)
        repo.finish_run(run_id, "complete")
        identity = (IdentityBuilder()
                    .add("dataset_version", seeded["dataset_version"], digest_of("dsv"))
                    .add("suite_version", seeded["suite_version"], digest_of("sv"))
                    .add("evaluator_version", seeded["evaluator_version"],
                         digest_of("ev"))
                    .add("model_configuration", seeded["model_configuration"],
                         digest_of("mc"))
                    .add_literal("integration_tier", "output_only")
                    .build())
        IdentityRepository(conn, org).capture(run_id, identity)
    return run_id


def published_policy(seeded, **overrides) -> str:
    from clep.db.session import tenant_session
    from clep.identity import new_ulid
    from clep.regression.repository import RegressionRepository

    criterion = dict(metric_key=seeded["metric"], dimension="quality",
                     source="evaluator", direction="higher_is_better",
                     precision_threshold=Decimal("0.5"),
                     on_regression="hard_fail",
                     on_insufficient_evidence="warning",
                     on_not_comparable="hard_fail")
    criterion.update(overrides)
    with tenant_session(RUNTIME_DSN, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        policy_id = repo.create_gate_policy(
            project_id=seeded["project"], slug="ci-" + new_ulid()[-8:].lower(),
            display_name="CI gate")
        version_id = repo.add_policy_version(
            policy_id, confidence_level=Decimal("0.95"), resample_count=200,
            bootstrap_seed=20260811, created_by="ci")
        repo.add_criterion(version_id, **criterion)
        repo.publish_policy_version(version_id)
    return version_id


def approved_baseline(seeded, run_id) -> str:
    from clep.db.session import tenant_session
    from clep.regression.repository import RegressionRepository
    with tenant_session(RUNTIME_DSN, seeded["organization"]) as conn:
        repo = RegressionRepository(conn, seeded["organization"])
        baseline_id = repo.create_baseline(run_id=run_id, created_by="ci",
                                           label="ci-baseline")
        repo.approve_baseline(baseline_id, approved_by="ci")
    return baseline_id


# ================================================== 2. the isolated CI environment
def clean_checkout(work: Path) -> Path:
    checkout = work / "checkout"
    code, out, err = run(["git", "clone", "--quiet", "--no-hardlinks", str(ROOT),
                          str(checkout)])
    note("clean checkout", code == 0 and checkout.exists(),
         f"git clone -> {checkout.name} (exit {code}) {err.strip()[:120]}")
    # Committed content only: an untracked build artefact or a virtualenv coming
    # along would mean the install was not from a clean checkout.
    strays = [p.name for p in (checkout,).__iter__() for p in p.iterdir()
              if p.name in (".venv", "venv", "build", "dist")]
    egg = list(checkout.glob("src/*.egg-info"))
    dirty = run(["git", "status", "--porcelain"], cwd=checkout)[1].strip()
    note("checkout is clean", not strays and not egg and not dirty,
         f"stray directories: {strays or 'none'}; egg-info: "
         f"{[str(p.name) for p in egg] or 'none'}; uncommitted: "
         f"{len(dirty.splitlines())}")
    return checkout


def isolated_install(work: Path, checkout: Path) -> Path:
    venv = work / "venv"
    code, out, err = run([sys.executable, "-m", "venv", str(venv)])
    note("isolated interpreter", code == 0, f"python -m venv (exit {code})")
    python = script_path(venv, "python")
    code, out, err = run([python, "-m", "pip", "install", "--quiet",
                          "--disable-pip-version-check", str(checkout)])
    note("package installed", code == 0,
         f"pip install <checkout> (exit {code}) {err.strip()[-160:]}")
    return venv


def entry_point_resolves(venv: Path) -> dict:
    python = script_path(venv, "python")
    clep = script_path(venv, "clep")
    note("console script exists", clep.exists(),
         f"{clep.name} present in the environment's script directory")

    probe = (
        "import json, sys\n"
        "from importlib.metadata import entry_points, version\n"
        "import clep, clep.cli.main\n"
        "eps = [e for e in entry_points(group='console_scripts') "
        "       if e.name == 'clep']\n"
        "print(json.dumps({'version': version('clep'),\n"
        "                  'entry_points': [f'{e.name}={e.value}' for e in eps],\n"
        "                  'package_file': clep.__file__,\n"
        "                  'main_is_callable': callable(clep.cli.main.main)}))\n")
    code, out, err = run([python, "-c", probe], cwd=venv)
    body = json.loads(out.strip().splitlines()[-1]) if code == 0 else {}
    note("entry point declared", body.get("entry_points") == ["clep=clep.cli.main:main"],
         f"console_scripts: {body.get('entry_points')} (exit {code}) {err[-120:]}")
    # Decided on the RAW path, recorded as a token. Redacting first would make
    # the check compare a token against a token and always pass.
    inside = str(venv).lower().replace("\\", "/") in \
        str(body.get("package_file", "")).lower().replace("\\", "/")
    note("package resolves inside the environment", inside,
         f"clep.__file__ = {redact(str(body.get('package_file')))}")
    return redact(body)


def cli_help(venv: Path, organization: str, neutral: Path) -> None:
    """The console script by name, from PATH, with the repository nowhere near."""
    env = pipeline_env(venv, organization)
    resolved = on_path(env, "clep")
    # Same rule: the decision uses the raw path, the record uses the token.
    note("console script resolves on PATH",
         str(venv).lower().replace("\\", "/") in resolved.lower().replace("\\", "/"),
         f"`clep` -> {redact(resolved)}")
    code, out, err = run([resolved, "--help"], cwd=neutral, env=env)
    note("console script runs by name", code == 0 and "clep gate" in out,
         f"`clep --help` from {neutral.name} (exit {code})")


# ============================================================ 3. the CI steps
def ci_step(venv: Path, organization: str, neutral: Path, argv: list[str]) -> dict:
    """One pipeline step: run the command, read the code, decide.

    The decision is computed from the process's exit status and nothing else —
    no parsing of the output, no second opinion — because that is the whole
    contract a CI job has with this tool.
    """
    env = pipeline_env(venv, organization)
    code, out, err = run([on_path(env, "clep"), *argv], cwd=neutral, env=env)
    return {"argv": ["clep", *argv], "exitCode": code,
            "ciDecision": CONTINUES if code == 0 else BLOCKS,
            "stdout": redact(out.strip().splitlines()),
            "stderr": redact(err.strip().splitlines()[:6])}


def main() -> int:
    if not database_is_reachable():
        return 2

    work = Path(tempfile.mkdtemp(prefix="clep-ci-"))
    neutral = work / "workspace"
    neutral.mkdir()
    # Registered before anything is recorded, and covering the nested
    # directories explicitly so the longest match wins over `<work>`.
    register_redactions([
        (work / "checkout", "<checkout>"), (work / "venv", "<venv>"),
        (neutral, "<workspace>"), (work, "<work>"), (ROOT, "<repo>"),
        (Path(tempfile.gettempdir()), "<tmp>"), (Path.home(), "<home>"),
    ])
    evidence: dict = {"environment": "local CI-style, not hosted CI",
                      "note": ("An isolated venv and a clean git clone on this "
                               "machine. No hosted CI service ran this, and "
                               "nothing here claims one did."),
                      "platform": sys.platform,
                      "python": sys.version.split()[0]}
    try:
        checkout = clean_checkout(work)
        venv = isolated_install(work, checkout)
        evidence["installation"] = entry_point_resolves(venv)
        cli_help(venv, "00000000-0000-0000-0000-000000000000", neutral)

        seeded = prepare_database()
        organization = seeded["organization"]
        note("database prepared", True,
             f"schema applied, tenant {organization[:8]}…, "
             f"{len(seeded['examples'])} examples")

        good = [Decimal("0.80"), Decimal("0.82"), Decimal("0.79"),
                Decimal("0.81"), Decimal("0.83"), Decimal("0.80"),
                Decimal("0.78"), Decimal("0.82"), Decimal("0.81"),
                Decimal("0.79")]
        worse = [s - Decimal("0.50") for s in good]

        baseline_run = build_run(seeded, good, key="ci-baseline")
        baseline_id = approved_baseline(seeded, baseline_run)
        unchanged_run = build_run(seeded, good, key="ci-unchanged")
        regressed_run = build_run(seeded, worse, key="ci-regressed")

        passing_policy = published_policy(seeded)
        # An abstention: the policy asks for more paired samples than exist, so
        # the comparison declines to classify. REQ-F-08-4 keeps that distinct
        # from a pass, and the exit code is what makes the distinction reach CI.
        abstaining_policy = published_policy(seeded, minimum_sample_size=1000)

        runs = {
            "successful evaluation": (unchanged_run, passing_policy, 0),
            "blocking evaluation": (regressed_run, passing_policy, 1),
            "abstention blocks": (unchanged_run, abstaining_policy, 70),
        }
        evidence["steps"] = {}
        for label, (run_id, policy_id, expected) in runs.items():
            result = ci_step(venv, organization, neutral, [
                "gate", "--project", seeded["project"], "--run", run_id,
                "--policy", policy_id, "--baseline", baseline_id])
            evidence["steps"][label] = result
            note(label, result["exitCode"] == expected,
                 f"exit {result['exitCode']} (expected {expected}); "
                 f"{result['ciDecision']}")

        # Deterministic failure interpretation: the same evaluation, twice,
        # through two separate processes. A gate whose verdict moved between
        # identical inputs would be unusable in a pipeline.
        repeat = ci_step(venv, organization, neutral, [
            "gate", "--project", seeded["project"], "--run", regressed_run,
            "--policy", passing_policy, "--baseline", baseline_id])
        first = evidence["steps"]["blocking evaluation"]
        note("deterministic interpretation",
             repeat["exitCode"] == first["exitCode"]
             and repeat["ciDecision"] == first["ciDecision"],
             f"re-run exit {repeat['exitCode']} matches {first['exitCode']}")
        evidence["steps"]["blocking evaluation, re-run"] = repeat

        # A platform failure is not a quality verdict, and CI must be able to
        # tell them apart. 78, and never 0 or 1.
        broken = ci_step(venv, organization, neutral, [
            "gate", "--project", seeded["project"], "--run", "not-a-ulid",
            "--policy", passing_policy, "--baseline", baseline_id])
        evidence["steps"]["malformed input is a platform failure"] = broken
        note("platform failure is distinguishable", broken["exitCode"] == 78,
             f"exit {broken['exitCode']} (expected 78); {broken['ciDecision']}")

        evidence["ok"] = not failures
        evidence["checks"] = steps
        write(evidence)
        print("-" * 78)
        print(f"SUMMARY: {json.dumps({'OK': len(steps) - len(failures), 'FAIL': len(failures)}, sort_keys=True)}")
        return 1 if failures else 0
    finally:
        if not KEEP:
            shutil.rmtree(work, ignore_errors=True)


def write(evidence: dict) -> None:
    _refuse_local_paths(evidence)
    out = ROOT / "docs" / "evidence" / "phase-11"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ci-execution-output.json").write_text(
        json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        newline="\n")
    lines = ["CI EXECUTION — installed console script, local CI-style environment",
             "=" * 78,
             evidence["note"], ""]
    for check in evidence["checks"]:
        lines.append(f"[{'OK  ' if check['ok'] else 'FAIL'}] {check['step']}: "
                     f"{check['detail']}")
    lines += ["", "Pipeline steps", "-" * 78]
    for label, step in evidence["steps"].items():
        lines.append(f"$ {' '.join(step['argv'])}")
        for line in step["stdout"]:
            lines.append(f"  {line}")
        for line in step["stderr"]:
            lines.append(f"  ! {line}")
        lines.append(f"  exit {step['exitCode']} -> {step['ciDecision']}")
        lines.append("")
    (out / "ci-execution-output.txt").write_text("\n".join(lines) + "\n",
                                                 encoding="utf-8", newline="\n")


if __name__ == "__main__":
    sys.exit(main())
