"""Spike S-2 - model and provider abstraction (ADR-003).

ADR-003 asked four questions and refused to answer any of them from
documentation:

  Q1  Are per-call token and cost figures retrievable, and do they reconcile
      against provider-reported usage?                        REQ-F-07-6
  Q2  Are the four failure modes distinguishable programmatically?  REQ-N-REL-4
  Q3  Can one candidate's failure be isolated from its siblings?    REQ-F-02-6
  Q4  Does any credential appear in logs or serialised errors?      REQ-N-SEC-5

Three approaches are put through all four:

  A  aggregation library used directly
  B  internal adapter behind a project-owned port
  C  aggregation library behind a project-owned port

and against three endpoints:

  hosted      a real commercial provider, over the public internet
  self-hosted llama.cpp serving Qwen2.5-0.5B-Instruct in a container - a real
              local inference server with real tokenisation      REQ-F-02-4
  stub        an endpoint this spike controls, because a real provider cannot be
              asked to return malformed JSON

Note on the word "candidate": in this project a candidate is a model under
evaluation. The three things being compared here are called APPROACHES
throughout, so that Q3 - isolating a failing candidate - stays readable.

Cost: this script attempts a small number of real calls with max_tokens=4 against
the cheapest available hosted model. Billable token counts are printed, so the
spend is auditable rather than asserted.
"""
import io
import json
import os
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import adapters                                                   # noqa: E402
from port import (Completion, ModelUnavailable, ProviderFailure,   # noqa: E402
                  ProviderMalformedResponse, ProviderOutage,
                  ProviderRateLimited, classify)

sys.stdout.reconfigure(encoding="utf-8")

OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
HOSTED_BASE = "https://api.openai.com/v1"
HOSTED_MODEL = os.environ.get("SPIKE_HOSTED_MODEL", "gpt-4.1-nano")
SELF_BASE = f"http://localhost:{os.environ.get('SPIKE_SELF_PORT', '8100')}/v1"
SELF_MODEL = os.environ.get(
    "SPIKE_SELF_MODEL", "/models/qwen2.5-0.5b-instruct-q4_k_m.gguf")
STUB_PORT = int(os.environ.get("SPIKE_STUB_PORT", "8099"))
STUB_BASE = f"http://127.0.0.1:{STUB_PORT}/v1"
PROMPT = "Reply with the single word OK."

EXPECTED = {
    "outage": ProviderOutage,
    "rate limiting": ProviderRateLimited,
    "malformed response": ProviderMalformedResponse,
    "model deprecation": ModelUnavailable,
}

report = {"usage": [], "failures": [], "isolation": [], "credential": [], "cost": []}


def log(m=""):
    print(m, flush=True)


# --------------------------------------------------------------- approach shims
def call(approach, base, key, model, prompt, prefix):
    """Invoke one approach. Approach A has no port, so its raw return value and
    raw exceptions are what a caller would actually face."""
    if approach == "A":
        r = adapters.litellm_call(base, key, model, prompt, prefix)
        u = r.usage
        from port import Usage
        return Completion(text=r.choices[0].message.content,
                          model=getattr(r, "model", model),
                          usage=Usage(u.prompt_tokens, u.completion_tokens,
                                      u.total_tokens),
                          raw_usage=dict(u) if not isinstance(u, dict) else u)
    if approach == "B":
        return adapters.internal_adapter(base, key, model, prompt)
    return adapters.litellm_behind_port(base, key, model, prompt, prefix)


def structured_signals(exc):
    """Every structured signal a caller could branch on, without reading prose.

    Collected in full and recorded, so that "the library cannot distinguish these"
    is a claim about the evidence rather than about the thoroughness of the person
    writing the adapter. If two different failures produce identical signals here,
    they are indistinguishable no matter how the mapping is written."""
    return {
        "class": type(exc).__name__,
        "status_code": getattr(exc, "status_code", None),
        "cause": type(exc.__cause__).__name__ if exc.__cause__ else None,
        "code": getattr(exc, "code", None),
        "llm_provider": getattr(exc, "llm_provider", None),
    }


def a_classify(exc):
    """How approach A's raw exception would have to be classified by a caller.
    Recorded honestly: if the only available signal is the message text, that is
    what gets written down."""
    import litellm.exceptions as lex
    mapping = [(lex.RateLimitError, ProviderRateLimited),
               (lex.NotFoundError, ModelUnavailable),
               ((lex.APIConnectionError, lex.Timeout), ProviderOutage),
               (lex.APIError, ProviderMalformedResponse)]
    for cls, target in mapping:
        if isinstance(exc, cls):
            return target, f"litellm class {type(exc).__name__}"
    return None, f"unmapped {type(exc).__name__}"


# ------------------------------------------------------------------- Q1 + cost
def q1_usage(approach, label, base, key, model, prefix, paid):
    try:
        c = call(approach, base, key, model, PROMPT, prefix)
    except Exception as e:
        report["usage"].append({"approach": approach, "endpoint": label,
                                "retrievable": False,
                                "error": f"{type(e).__name__}: {e}"[:160]})
        log(f"    {approach} / {label:<11} CALL FAILED  {type(e).__name__}")
        return
    ref = adapters.raw_reference_usage(base, key, model, PROMPT)
    got = c.usage
    # Reconciliation compares the approach's figures against the provider's own
    # usage object taken straight off the wire by a separate raw call.
    #
    # prompt_tokens is compared exactly: tokenisation of a fixed prompt is
    # deterministic, and it is the figure that drives input cost. completion
    # tokens are NOT compared across the two calls, because sampling can legally
    # produce a different number of output tokens each time - an equality test
    # there would report a spurious failure and would be testing the model, not
    # the abstraction. What is required instead is that the approach reports a
    # completion count at all, and that its own three figures are self-consistent.
    consistent = (got is not None
                  and got.prompt_tokens + got.completion_tokens == got.total_tokens)
    reconciles = (got is not None
                  and got.prompt_tokens == ref["prompt_tokens"]
                  and got.completion_tokens is not None
                  and consistent)
    report["usage"].append({
        "approach": approach, "endpoint": label, "retrievable": got is not None,
        "reported": None if got is None else [got.prompt_tokens, got.completion_tokens,
                                              got.total_tokens],
        "provider_reference_prompt_tokens": ref["prompt_tokens"],
        "self_consistent": bool(consistent),
        "reconciles": bool(reconciles)})
    if paid:
        report["cost"].append({"approach": approach, "endpoint": label, "calls": 2,
                               "prompt_tokens": got.prompt_tokens * 2 if got else None,
                               "completion_tokens": got.completion_tokens * 2 if got else None})
    log(f"    {approach} / {label:<11} usage={got.prompt_tokens}/{got.completion_tokens}"
        f"  provider={ref['prompt_tokens']}/{ref['completion_tokens']}"
        f"  reconciles={reconciles}")


# ---------------------------------------------------------------------- Q2
def q2_failures(approach, stub_proc_ctl):
    for mode, model in (("rate limiting", "ratelimit"),
                        ("malformed response", "malformed"),
                        ("model deprecation", "deprecated")):
        _q2_one(approach, mode, STUB_BASE, "stub-key", model)
    # Outage is induced by taking the endpoint away, not by asking for it.
    stub_proc_ctl("stop")
    _q2_one(approach, "outage", STUB_BASE, "stub-key", "ok")
    stub_proc_ctl("start")


def _q2_one(approach, mode, base, key, model):
    expected = EXPECTED[mode]
    try:
        call(approach, base, key, model, PROMPT, "openai/")
        report["failures"].append({"approach": approach, "mode": mode,
                                   "classified": None, "correct": False,
                                   "evidence": "call unexpectedly succeeded"})
        log(f"    {approach} / {mode:<19} NO FAILURE RAISED")
        return
    except Exception as e:
        if approach == "A":
            got, evidence = a_classify(e)
        else:
            got, evidence = classify(e), getattr(e, "evidence", "")
        correct = got is expected
        report["failures"].append({
            "approach": approach, "mode": mode,
            "raw_exception": type(e).__name__,
            "classified": None if got is None else got.__name__,
            "expected": expected.__name__, "correct": bool(correct),
            "evidence": evidence,
            "signals": structured_signals(e),
            "structural": bool(evidence and "message" not in evidence.lower())})
        log(f"    {approach} / {mode:<19} raw={type(e).__name__:<24} "
            f"-> {got.__name__ if got else 'UNCLASSIFIED':<26} "
            f"{'ok' if correct else 'WRONG'}   [{evidence}]")


# --------------------------------------------------------------------- Q2b
REAL_PROVIDERS = [
    ("OpenAI", "https://api.openai.com/v1", os.environ.get("OPENAI_API_KEY", ""),
     "gpt-4.1-nano"),
    ("Perplexity", "https://api.perplexity.ai", os.environ.get("PPLX_API_KEY", ""),
     "sonar"),
]


def q2b_real_provider_errors():
    """Two real hosted providers, put into the SAME semantic condition, to test
    whether the four-mode taxonomy survives contact with actual providers.

    This leg exists because the stub cannot lie convincingly about what real
    providers do. Whatever condition the live credentials are in, the raw
    HTTP status and error code are recorded verbatim - that is the evidence."""
    import urllib.error
    import urllib.request
    rows = []
    for name, base, key, model in REAL_PROVIDERS:
        if not key:
            rows.append({"provider": name, "reached": False,
                         "note": "no credential in environment"})
            log(f"    {name:<12} no credential")
            continue
        body = json.dumps({"model": model, "max_tokens": 4,
                           "messages": [{"role": "user", "content": PROMPT}]}).encode()
        url = base.rstrip("/") + ("/chat/completions" if not base.endswith("/chat/completions") else "")
        req = urllib.request.Request(url, data=body, headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + key})
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                d = json.loads(r.read())
            rows.append({"provider": name, "reached": True, "http_status": 200,
                         "usage": d.get("usage")})
            log(f"    {name:<12} HTTP 200  usage={d.get('usage')}")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            err = {}
            try:
                err = json.loads(detail).get("error") or {}
            except Exception:
                pass
            rows.append({"provider": name, "reached": True, "http_status": e.code,
                         "error_type": err.get("type"), "error_code": err.get("code"),
                         "retry_after": e.headers.get("Retry-After")})
            log(f"    {name:<12} HTTP {e.code}  type={err.get('type')!r} "
                f"code={err.get('code')!r}  Retry-After={e.headers.get('Retry-After')!r}")
        except Exception as e:
            rows.append({"provider": name, "reached": False,
                         "note": f"{type(e).__name__}"})
            log(f"    {name:<12} unreachable: {type(e).__name__}")
    report["real_providers"] = rows

    # The question this leg was built to answer.
    seen = {(r.get("http_status"), r.get("error_type")) for r in rows if r.get("reached")}
    by_type = {}
    for r in rows:
        if r.get("reached") and r.get("error_type"):
            by_type.setdefault(r["error_type"], set()).add(r["http_status"])
    ambiguous = {t: sorted(s) for t, s in by_type.items() if len(s) > 1}
    report["status_code_ambiguity"] = ambiguous
    if ambiguous:
        for t, codes in ambiguous.items():
            log(f"    -> condition {t!r} arrives as HTTP {codes} depending on provider")
    return rows


# ---------------------------------------------------------------------- Q3
def q3_isolation(approach):
    """Four evaluation candidates, one of which is broken. The requirement is
    that the broken one does not take its siblings down with it."""
    candidates = [("cand-1", "ok"), ("cand-2", "deprecated"),
                  ("cand-3", "ok"), ("cand-4", "ok")]
    good, bad = [], []
    for name, model in candidates:
        try:
            c = call(approach, STUB_BASE, "stub-key", model, PROMPT, "openai/")
            good.append({"candidate": name, "tokens": c.usage.total_tokens})
        except Exception as e:
            bad.append({"candidate": name, "failure": type(e).__name__})
    isolated = len(good) == 3 and len(bad) == 1
    report["isolation"].append({"approach": approach, "succeeded": len(good),
                                "failed": len(bad), "isolated": isolated,
                                "failures": bad})
    log(f"    {approach}  {len(good)} sibling(s) valid, {len(bad)} failed"
        f"  isolated={isolated}")


# ---------------------------------------------------------------------- Q4
def q4_credentials(approach):
    """Drive a failure with a real-shaped secret and inspect everything a caller
    or an operator could plausibly see: stdout, stderr, str(), repr() and the
    formatted traceback."""
    secret = "sk-proj-SPIKECANARY" + "0" * 40
    buf_out, buf_err = io.StringIO(), io.StringIO()
    surfaces = {}
    try:
        with redirect_stdout(buf_out), redirect_stderr(buf_err):
            try:
                call(approach, STUB_BASE, secret, "deprecated", PROMPT, "openai/")
            except Exception as e:
                surfaces["str"] = str(e)
                surfaces["repr"] = repr(e)
                surfaces["traceback"] = "".join(
                    traceback.format_exception(type(e), e, e.__traceback__))
    finally:
        surfaces["stdout"] = buf_out.getvalue()
        surfaces["stderr"] = buf_err.getvalue()

    leaked = sorted(k for k, v in surfaces.items() if secret in v)
    partial = sorted(k for k, v in surfaces.items()
                     if secret not in v and secret[8:24] in v)
    report["credential"].append({"approach": approach, "leaked_in": leaked,
                                 "partial_in": partial,
                                 "surfaces_checked": sorted(surfaces)})
    log(f"    {approach}  canary in: {leaked or 'none'}"
        f"   partial: {partial or 'none'}   "
        f"(checked {', '.join(sorted(surfaces))})")


def q4_selftest():
    """A leak detector that has never reported a leak has not been shown to work.

    This plants the canary on every surface the real check inspects and requires
    the detector to find it on all of them. If this fails, every 'no leak' result
    above is worthless and the spike says so rather than reporting a clean run."""
    secret = "sk-proj-SPIKECANARY" + "0" * 40
    buf_out, buf_err = io.StringIO(), io.StringIO()
    surfaces = {}
    with redirect_stdout(buf_out), redirect_stderr(buf_err):
        try:
            print(f"leaking to stdout: {secret}")
            print(f"leaking to stderr: {secret}", file=sys.stderr)
            raise RuntimeError(f"auth failed for {secret}")
        except Exception as e:
            surfaces["str"] = str(e)
            surfaces["repr"] = repr(e)
            surfaces["traceback"] = "".join(
                traceback.format_exception(type(e), e, e.__traceback__))
    surfaces["stdout"] = buf_out.getvalue()
    surfaces["stderr"] = buf_err.getvalue()
    found = sorted(k for k, v in surfaces.items() if secret in v)
    ok = found == sorted(surfaces)
    report["credential_selftest"] = {"surfaces": sorted(surfaces), "detected_on": found,
                                     "detector_works": ok}
    log(f"    self-test: planted the canary on {len(surfaces)} surfaces, "
        f"detector found it on {len(found)} -> {'PASS' if ok else 'FAIL'}")
    return ok


# --------------------------------------------------------------------- driver
class Stub:
    def __init__(self):
        self.p = None

    def __call__(self, action):
        if action == "stop" and self.p:
            self.p.kill(); self.p.wait(); self.p = None
            time.sleep(0.6)
        elif action == "start" and not self.p:
            self.p = subprocess.Popen([sys.executable, str(HERE / "stub_provider.py"),
                                       str(STUB_PORT)],
                                      stdout=subprocess.DEVNULL,
                                      stderr=subprocess.DEVNULL)
            time.sleep(1.2)


def self_hosted_ready():
    try:
        adapters.raw_reference_usage(SELF_BASE, "local-no-auth", SELF_MODEL, "hi")
        return True
    except Exception as e:
        log(f"  self-hosted endpoint unavailable: {type(e).__name__}: {str(e)[:120]}")
        return False


def main():
    import litellm
    litellm.suppress_debug_info = True

    log("=" * 78)
    log("SPIKE S-2 - MODEL AND PROVIDER ABSTRACTION (ADR-003)")
    log("=" * 78)
    log(f"aggregation library : litellm {litellm.__version__ if hasattr(litellm, '__version__') else 'n/a'}")
    log(f"hosted endpoint     : {HOSTED_BASE} model {HOSTED_MODEL} (real, paid)")
    log(f"self-hosted endpoint: {SELF_BASE} model {SELF_MODEL}")
    log(f"fault endpoint      : {STUB_BASE} (spike-controlled)")
    log()

    stub = Stub()
    stub("start")
    have_self = self_hosted_ready()
    have_hosted = bool(OPENAI_KEY)

    log("-" * 78)
    log("Q1  per-call usage retrievable and reconciling with provider-reported usage")
    log("-" * 78)
    for ap in ("A", "B", "C"):
        if have_hosted:
            q1_usage(ap, "hosted", HOSTED_BASE, OPENAI_KEY, HOSTED_MODEL,
                     "openai/", paid=True)
        if have_self:
            q1_usage(ap, "self-hosted", SELF_BASE, "local-no-auth", SELF_MODEL,
                     "openai/", paid=False)
        # The stub leg checks plumbing only: it reconciles an approach against
        # usage figures this spike itself wrote. It cannot establish fidelity to
        # a real provider and is not counted as if it could.
        q1_usage(ap, "stub", STUB_BASE, "stub-key", "ok", "openai/", paid=False)
    log()

    log("-" * 78)
    log("Q2  the four failure modes, induced deliberately, one at a time")
    log("-" * 78)
    for ap in ("A", "B", "C"):
        q2_failures(ap, stub)
        log()

    log("-" * 78)
    log("Q2b real hosted providers: does the taxonomy survive contact with them?")
    log("-" * 78)
    q2b_real_provider_errors()
    log()

    log("-" * 78)
    log("Q3  one failing evaluation candidate must not invalidate its siblings")
    log("-" * 78)
    for ap in ("A", "B", "C"):
        q3_isolation(ap)
    log()

    log("-" * 78)
    log("Q4  credential exposure in logs and serialised errors")
    log("-" * 78)
    detector_ok = q4_selftest()
    for ap in ("A", "B", "C"):
        q4_credentials(ap)
    log("    with provider debug logging enabled (what an operator does when a "
        "run starts failing):")
    for ap in ("A", "B", "C"):
        p = subprocess.run([sys.executable, str(HERE / "leak_probe.py"), ap, STUB_BASE],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", cwd=str(HERE), timeout=180)
        line = (p.stdout or "").strip().splitlines()[-1] if p.stdout.strip() else "no output"
        report["credential"].append({"approach": ap, "debug_logging": line})
        log(f"    {ap}  {line}")
    if not detector_ok:
        log("    WARNING: the detector failed its own self-test; the results above "
            "establish nothing")
    log()

    stub("stop")
    (HERE / "s2-results.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    # ------------------------------------------------------------- summary
    log("-" * 78)
    log("SUMMARY")
    log("-" * 78)
    names = {"A": "aggregation library, used directly",
             "B": "internal adapter behind the port",
             "C": "aggregation library behind the port"}
    verdicts = {}
    for ap in ("A", "B", "C"):
        u = [r for r in report["usage"] if r["approach"] == ap]
        f = [r for r in report["failures"] if r["approach"] == ap]
        iso = next(r for r in report["isolation"] if r["approach"] == ap)
        cred = next(r for r in report["credential"] if r["approach"] == ap)
        # An endpoint that could not be reached at all did not fail to
        # reconcile - it was never asked. Scoring it as a reconciliation failure
        # would reject an approach for an environmental problem, so unreachable
        # endpoints are separated out and reported as a gap instead.
        real = [r for r in u if r["endpoint"] != "stub"]
        reached = [r for r in real if r.get("retrievable")]
        unreachable = [r for r in real if not r.get("retrievable")]
        usage_ok = bool(reached) and all(r.get("reconciles") for r in reached)
        dbg = next((r for r in report["credential"]
                    if r["approach"] == ap and "debug_logging" in r), None)
        leaks_on_debug = bool(dbg and "LEAKED=none" not in dbg["debug_logging"])
        modes_ok = sum(1 for r in f if r["correct"])
        structural = sum(1 for r in f if r["correct"] and r.get("structural"))
        # The decision rule rejects on the two capability tests. Credential
        # exposure is a requirement violation in its own right and is reported
        # alongside, not folded into, that verdict.
        rejected = (not usage_ok) or modes_ok < 4
        verdicts[ap] = not rejected
        log(f"{ap}  {names[ap]}")
        log(f"     per-call usage reconciles                   : {usage_ok} "
            f"({len(reached)} real endpoint(s) reached, "
            f"{len(unreachable)} unreachable, {len(u) - len(real)} stub)")
        log(f"     failure modes correctly distinguished       : {modes_ok}/4"
            f"  (structural signal, not message text: {structural}/4)")
        log(f"     failing candidate isolated                  : {iso['isolated']}")
        log(f"     credential canary, default logging          : "
            f"{cred['leaked_in'] or 'no surface'}")
        log(f"     credential canary, debug logging on         : "
            f"{'LEAKED' if leaks_on_debug else 'no surface'}"
            f"   {'<- REQ-N-SEC-5 violation' if leaks_on_debug else ''}")
        log(f"     ADR-003 decision rule                       : "
            f"{'SURVIVES' if not rejected else 'REJECTED'}")
        log()

    # Which failure modes produced identical structured signals? This is the
    # measurement that decides whether an approach can distinguish them at all.
    log("indistinguishable failure modes (identical structured signals):")
    for ap in ("A", "B", "C"):
        sig = {}
        for r in report["failures"]:
            if r["approach"] == ap:
                sig.setdefault(json.dumps(r["signals"], sort_keys=True), []).append(r["mode"])
        collisions = [v for v in sig.values() if len(v) > 1]
        if collisions:
            for c in collisions:
                log(f"     {ap}: {' == '.join(c)}  (same class, status and cause)")
        else:
            log(f"     {ap}: none - all four modes carry distinct structured signals")
    log()

    log("cost of this run (real paid endpoint only):")
    tot_p = sum(c["prompt_tokens"] or 0 for c in report["cost"])
    tot_c = sum(c["completion_tokens"] or 0 for c in report["cost"])
    log(f"     {sum(c['calls'] for c in report['cost'])} calls, "
        f"{tot_p} prompt tokens, {tot_c} completion tokens")
    return 0


if __name__ == "__main__":
    sys.exit(main())


