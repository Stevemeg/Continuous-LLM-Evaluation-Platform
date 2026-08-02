"""Three approaches to the provider abstraction, behind (or not behind) the port.

  A  aggregation library used directly        - litellm.completion, no port
  B  internal adapter behind the port         - plain HTTP, project-owned mapping
  C  aggregation library behind the port      - litellm, project-owned mapping

Approaches B and C are asked to map failures onto the taxonomy WITHOUT reading
message text. Every mapping is annotated with the structured signal it used, so
the evidence records not merely whether the mapping succeeded but what it stood
on. A mapping that stands on a substring is recorded as such and counts as a
failure to distinguish, because it is a mapping that a provider can break by
editing prose.
"""
import json
import os
import urllib.error
import urllib.request

from port import (Completion, ModelUnavailable, ProviderMalformedResponse,
                  ProviderOutage, ProviderRateLimited, Usage)

TIMEOUT = 30


# --------------------------------------------------------------------------- B
def internal_adapter(base_url: str, api_key: str, model: str, prompt: str):
    """Approach B. Everything is ours, including the mistakes."""
    body = json.dumps({"model": model, "max_tokens": 4,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            payload = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        code = ""
        try:
            code = (json.loads(detail).get("error") or {}).get("code") or ""
        except Exception:
            pass
        if e.code == 429:
            raise ProviderRateLimited(
                "rate limited", retry_after=e.headers.get("Retry-After"),
                evidence="HTTP status 429") from None
        if e.code == 404 or code == "model_not_found":
            raise ModelUnavailable(
                "model unavailable",
                evidence=f"HTTP status {e.code}, error.code={code!r}") from None
        raise ProviderMalformedResponse(
            f"unexpected status {e.code}", evidence=f"HTTP status {e.code}") from None
    except urllib.error.URLError as e:
        raise ProviderOutage("endpoint unreachable",
                             evidence=f"transport error {type(e.reason).__name__}") from None

    try:
        data = json.loads(payload)
        text = data["choices"][0]["message"]["content"]
        u = data["usage"]
        usage = Usage(u["prompt_tokens"], u["completion_tokens"], u["total_tokens"])
    except Exception as e:
        raise ProviderMalformedResponse(
            "response was not a usable completion",
            evidence=f"parse/shape error {type(e).__name__}") from None
    return Completion(text=text, model=data.get("model", model),
                      usage=usage, raw_usage=u)


# ----------------------------------------------------------------------- A / C
def litellm_call(base_url, api_key, model, prompt, provider_prefix):
    """Shared transport for approaches A and C. A consumes the result and the
    raw exception; C wraps it with the mapping below."""
    import litellm
    kwargs = dict(model=f"{provider_prefix}{model}",
                  messages=[{"role": "user", "content": prompt}],
                  max_tokens=4, api_key=api_key, num_retries=0, timeout=TIMEOUT)
    if base_url:
        kwargs["api_base"] = base_url
    return litellm.completion(**kwargs)


def litellm_behind_port(base_url, api_key, model, prompt, provider_prefix):
    """Approach C. The mapping may use exception classes and status codes only."""
    import litellm.exceptions as lex
    try:
        r = litellm_call(base_url, api_key, model, prompt, provider_prefix)
    except lex.RateLimitError as e:
        raise ProviderRateLimited(
            "rate limited", evidence=f"litellm class {type(e).__name__}") from None
    except lex.NotFoundError as e:
        raise ModelUnavailable(
            "model unavailable", evidence=f"litellm class {type(e).__name__}") from None
    except (lex.APIConnectionError, lex.Timeout) as e:
        raise ProviderOutage(
            "endpoint unreachable", evidence=f"litellm class {type(e).__name__}") from None
    except lex.APIError as e:
        raise ProviderMalformedResponse(
            "unusable response", evidence=f"litellm class {type(e).__name__}") from None
    except Exception as e:
        raise ProviderMalformedResponse(
            "unusable response", evidence=f"unmapped {type(e).__name__}") from None

    try:
        text = r.choices[0].message.content
        u = r.usage
        usage = Usage(u.prompt_tokens, u.completion_tokens, u.total_tokens)
        raw = dict(u) if not isinstance(u, dict) else u
    except Exception as e:
        raise ProviderMalformedResponse(
            "response was not a usable completion",
            evidence=f"shape error {type(e).__name__}") from None
    return Completion(text=text, model=getattr(r, "model", model),
                      usage=usage, raw_usage=raw)


def raw_reference_usage(base_url, api_key, model, prompt):
    """Ground truth: the provider's own usage object, straight off the wire, with
    no library between it and the comparison. Reconciliation is meaningless if
    both sides of it come from the same abstraction."""
    body = json.dumps({"model": model, "max_tokens": 4,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/chat/completions", data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + api_key})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read())["usage"]
