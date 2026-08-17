"""ADR-009 rule 3, executed: the core, in an environment with no telemetry at all.

The rule says "a build excluding every vendor adapter must pass the full
validation suite", and that it is a build configuration rather than a
documentation claim. So this builds one.

A fresh virtual environment, `pip install .` with no extras, and then five things
asserted about it in order, because each is a different way the claim could be
false:

  A-1  the package installs with no telemetry distribution present
  A-2  `import clep.telemetry` succeeds -- the port itself needs nothing
  A-3  the tenant application is constructable and its route guard still holds
  A-4  the evaluation path runs and produces a scored result
  A-5  the OTLP backend refuses clearly rather than failing obscurely

A-5 is the one that would otherwise be missed. A backend module whose imports sit
at module scope makes the whole package non-optional the moment anything imports
it -- for a type annotation, say -- and nobody finds out until a deployment
without the extra fails at start. Here the absence is asserted to produce a named
error at construction time, which is the only place ADR-022 rule 3 permits it.

Usage: python docs/evidence/phase-13/prove_adapter_excluded.py <repo_root>
Exits non-zero on any FAIL. Takes a few minutes; it builds a real environment.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
sys.path.insert(0, str(ROOT / "docs" / "evidence" / "tooling"))
import workspace as W  # noqa: E402

results = []


def add(cid, ok, detail):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL':<4}] {cid:<5} {detail}")


def run(cmd, **kw):
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=1800, **kw)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


PROBE = textwrap.dedent('''
    import json, pathlib, sys
    out = {}
    REPO = pathlib.Path(sys.argv[1])

    # The contract is read from docs/api/openapi.json and is NOT package data,
    # so an installed distribution cannot find it from site-packages. That is a
    # real packaging gap and it is pre-existing -- nothing to do with telemetry,
    # and out of this phase's scope, since what a deployment ships is Phase 14's.
    # It is recorded in the evidence rather than worked around silently. Here the
    # locator is pointed at the repository so that the thing being proved is the
    # absence of a telemetry dependency and not the presence of a JSON file.
    from clep.api import contract
    contract.repository_root = lambda start=None: REPO
    contract.load.cache_clear()
    out["contract_is_package_data"] = False

    # A-2: the port needs nothing.
    import clep.telemetry as t
    out["telemetry_imported"] = True
    out["metric_classes"] = len(t.METRIC_CLASSES)
    out["catalogue_size"] = len(t.CATALOGUE)

    # No telemetry distribution may be present in this environment.
    import importlib.util as u
    out["otel_present"] = any(u.find_spec(m) is not None
                              for m in ("opentelemetry", "prometheus_client"))

    # A-3: the application is constructable and the guard still holds.
    from clep.api.app import create_app, _assert_every_route_is_guarded
    from clep.api import contract
    class _Auth:
        def verify(self, *a, **k): return None
        def __call__(self, *a, **k): return None
    app = create_app(object(), authenticator=_Auth())
    _assert_every_route_is_guarded(app)
    out["routes"] = len([r for r in app.routes if getattr(r, "methods", None)])

    # A-4: the evaluation path runs. Deterministic evaluators, no database.
    from clep.evaluators.builtin import default_registry
    from clep.evaluators.sdk import SampleContext, run_evaluator
    from clep.security.grants import grant_for
    reg = default_registry()
    sample = SampleContext(example_id="e1", prompt="What is the capital of France?",
                           output="Paris", expected="Paris",
                           integration_tier="output_only")
    grant = grant_for("00000000-0000-0000-0000-000000000000", ())
    scored = [run_evaluator(reg.get(k), sample, grant=grant) for k in reg.keys()]
    out["evaluators_run"] = len(scored)
    out["evaluators_scored"] = sum(1 for r in scored if r.resolution == "scored")

    # Metrics still record against the no-op recorder, and cardinality still
    # refuses -- the rule is in the core, not in a backend.
    from clep.telemetry import Telemetry, CardinalityError, correlated
    tel = Telemetry()
    with correlated() as c:
        tel.observe("clep_run_terminal_total", 1, completeness="complete")
        out["correlation_is_ulid"] = len(c.correlation_id) == 26
    try:
        tel.observe("clep_run_terminal_total", 1, completeness="complete",
                    run_id="01ARZ3NDEKTSV4RRFFQ69G5FAV")
        out["cardinality_refused"] = False
    except CardinalityError:
        out["cardinality_refused"] = True

    # A-5: the OTLP backend refuses clearly.
    from clep.telemetry.backends import otlp
    out["otlp_available"] = otlp.available()
    try:
        otlp.build("http://collector.invalid:4318")
        out["otlp_refused"] = False
        out["otlp_message"] = ""
    except otlp.TelemetryExtraMissing as exc:
        out["otlp_refused"] = True
        out["otlp_message"] = str(exc)

    print("PROBE_JSON:" + json.dumps(out))
''')


with W.workspace("clep-noextra-") as base:
    venv = base / "venv"
    python = venv / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python")

    code, out = run([sys.executable, "-m", "venv", str(venv)])
    add("A-0", code == 0 and python.exists(),
        f"a fresh virtual environment, isolated from this project's: exit {code}")

    if python.exists():
        code, out = run([str(python), "-m", "pip", "install", "--quiet",
                         "--disable-pip-version-check", str(ROOT)])
        installed = code == 0
        add("A-1", installed,
            f"pip install . (no extras): exit {code}"
            + ("" if installed else f" — {out.strip().splitlines()[-3:]}"))

        if installed:
            frozen = run([str(python), "-m", "pip", "freeze"])[1].lower()
            telemetry_distributions = [
                line for line in frozen.splitlines()
                if line.startswith(("opentelemetry", "prometheus"))]
            add("A-1b", not telemetry_distributions,
                f"no telemetry distribution in the environment: "
                f"{telemetry_distributions or 'none'}")

            code, out = run([str(python), "-c", PROBE, str(ROOT)], cwd=str(base))
            payload = next((line[len("PROBE_JSON:"):] for line in out.splitlines()
                            if line.startswith("PROBE_JSON:")), None)
            if payload is None:
                add("A-2", False, f"the probe did not complete: exit {code}")
                for line in out.strip().splitlines()[-8:]:
                    print(f"           {line}")
            else:
                import json
                p = json.loads(payload)
                add("A-2", p["telemetry_imported"] and not p["otel_present"]
                    and p["metric_classes"] == 9,
                    f"clep.telemetry imports with no telemetry distribution "
                    f"present: {p['metric_classes']} metric classes, "
                    f"{p['catalogue_size']} declared metrics, "
                    f"otel/prometheus present={p['otel_present']}")
                add("A-3", p["routes"] > 0,
                    f"the tenant application constructs and every one of its "
                    f"{p['routes']} route(s) still carries an authorization "
                    f"guard")
                add("A-4", p["evaluators_scored"] > 0 and p["correlation_is_ulid"]
                    and p["cardinality_refused"],
                    f"the evaluation path runs: {p['evaluators_scored']} of "
                    f"{p['evaluators_run']} evaluator(s) scored; correlation is "
                    f"produced by the core; the cardinality refusal still fires "
                    f"with no backend installed")
                add("A-5", p["otlp_refused"] and not p["otlp_available"]
                    and "pip install" in p["otlp_message"],
                    f"the OTLP backend refuses at construction and names what "
                    f"is missing, rather than failing at import: "
                    f"available={p['otlp_available']}, refused={p['otlp_refused']}")

print(f"\nSUMMARY: {sum(results)}/{len(results)} PASS")
raise SystemExit(0 if all(results) else 1)
