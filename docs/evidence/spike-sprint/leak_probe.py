"""Credential exposure with provider debug logging turned ON.

The main Q4 check runs with the aggregation library's debug output suppressed,
which is the library's default and not what an operator does when a run starts
failing. `REQ-N-SEC-5` says a credential must never be logged - not "never logged
unless someone was debugging". This probe therefore enables the most verbose
logging each approach offers and re-runs the same canary.

Run as a subprocess, because logging configuration is global and has to be set
before the library is imported to take effect.

Usage: leak_probe.py <approach A|B|C> <stub_base>
"""
import io
import os
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

APPROACH = sys.argv[1]
STUB_BASE = sys.argv[2]
CANARY = "sk-proj-SPIKECANARY" + "0" * 40

os.environ["LITELLM_LOG"] = "DEBUG"
sys.path.insert(0, str(Path(__file__).resolve().parent))

buf_out, buf_err = io.StringIO(), io.StringIO()
surfaces = {}
with redirect_stdout(buf_out), redirect_stderr(buf_err):
    try:
        import logging
        logging.basicConfig(level=logging.DEBUG, stream=sys.stderr, force=True)
        import litellm
        litellm.set_verbose = True
        import adapters
        try:
            if APPROACH == "A":
                adapters.litellm_call(STUB_BASE, CANARY, "deprecated", "hi", "openai/")
            elif APPROACH == "B":
                adapters.internal_adapter(STUB_BASE, CANARY, "deprecated", "hi")
            else:
                adapters.litellm_behind_port(STUB_BASE, CANARY, "deprecated", "hi",
                                             "openai/")
        except Exception as e:
            surfaces["str"] = str(e)
            surfaces["repr"] = repr(e)
            surfaces["traceback"] = "".join(
                traceback.format_exception(type(e), e, e.__traceback__))
    except Exception as e:  # import-time or config-time failure
        surfaces["setup_error"] = f"{type(e).__name__}: {e}"

surfaces["stdout"] = buf_out.getvalue()
surfaces["stderr"] = buf_err.getvalue()

leaked = sorted(k for k, v in surfaces.items() if CANARY in v)
sizes = {k: len(v) for k, v in surfaces.items()}
print(f"APPROACH={APPROACH} LEAKED={','.join(leaked) if leaked else 'none'} "
      f"LOGBYTES={sizes.get('stdout', 0) + sizes.get('stderr', 0)}")
