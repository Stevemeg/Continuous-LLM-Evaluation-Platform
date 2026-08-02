"""Adapter for OpenAI-compatible chat-completion endpoints.

One adapter, serving both hosted and self-hosted endpoints, because
`REQ-F-02-4` makes self-hosted models first-class rather than a variant: the only
difference is a base URL and whether a credential is sent.

Standard library only. `docs/dependencies.md` records why: the egress path has no
third-party code between the domain and the provider, so there is nothing else
that can decide to log the request.

Classification order is deliberate and is the opposite of the obvious one. The
provider's own `error.type`/`error.code` is consulted BEFORE the HTTP status,
because the ADR-003 spike observed the same `insufficient_quota` condition
arriving as 429 from one provider and 401 from another. Status-first
classification is correct until it silently is not.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from clep.providers.port import (CompletionRequest, CompletionResult,
                                 ModelUnavailable, ProviderMalformedResponse,
                                 ProviderOutage, ProviderRateLimited,
                                 QuotaExhausted, Usage)

DEFAULT_TIMEOUT = 60.0

#: Provider error codes that mean "this will not succeed on retry, ever, until a
#: human does something". Matched before status codes.
_QUOTA_CODES = {"insufficient_quota", "billing_hard_limit_reached",
                "account_deactivated"}
_MODEL_CODES = {"model_not_found", "model_not_available", "deprecated_model"}
_RATE_CODES = {"rate_limit_exceeded", "requests_limit_reached",
               "tokens_limit_reached"}


class OpenAICompatibleAdapter:
    """Implements `ProviderPort` for any endpoint speaking the OpenAI shape."""

    def __init__(self, endpoint, timeout: float = DEFAULT_TIMEOUT, opener=None):
        self._endpoint = endpoint
        self._timeout = timeout
        # Injected in tests so failure modes can be induced without a network.
        self._opener = opener or urllib.request.urlopen

    def __repr__(self) -> str:
        # The endpoint's own __str__ omits the key; do not reconstruct it here.
        return f"OpenAICompatibleAdapter({self._endpoint})"

    # ------------------------------------------------------------------ public
    def complete(self, request: CompletionRequest) -> CompletionResult:
        body = {
            "model": request.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            **request.extra_parameters,
        }
        headers = {"Content-Type": "application/json"}
        if self._endpoint.api_key:
            headers["Authorization"] = "Bearer " + self._endpoint.api_key

        req = urllib.request.Request(
            self._endpoint.base_url.rstrip("/") + "/chat/completions",
            data=json.dumps(body).encode("utf-8"), headers=headers)
        try:
            with self._opener(req, timeout=self._timeout) as response:
                payload = response.read()
        except urllib.error.HTTPError as e:
            raise self._classify_http(e) from None
        except urllib.error.URLError as e:
            raise ProviderOutage(
                "endpoint unreachable",
                evidence=f"transport error {type(e.reason).__name__}") from None
        except TimeoutError:
            raise ProviderOutage(
                "endpoint timed out", evidence="socket timeout") from None

        return self._parse(payload, request)

    # ----------------------------------------------------------- classification
    def _classify_http(self, error: urllib.error.HTTPError):
        raw = error.read().decode("utf-8", "replace")
        code, etype = "", ""
        try:
            err = (json.loads(raw).get("error") or {})
            code = str(err.get("code") or "")
            etype = str(err.get("type") or "")
        except (ValueError, AttributeError):
            pass
        signals = {code, etype}
        retry_after = self._retry_after(error)

        # Provider-declared condition first. See the module docstring.
        if signals & _QUOTA_CODES:
            return QuotaExhausted(
                "provider quota exhausted",
                evidence=f"error.type/code in {sorted(signals & _QUOTA_CODES)}, "
                         f"HTTP {error.code}")
        if signals & _MODEL_CODES:
            return ModelUnavailable(
                "model unavailable",
                evidence=f"error.type/code in {sorted(signals & _MODEL_CODES)}, "
                         f"HTTP {error.code}")
        if signals & _RATE_CODES:
            return ProviderRateLimited(
                "rate limited",
                evidence=f"error.type/code in {sorted(signals & _RATE_CODES)}, "
                         f"HTTP {error.code}", retry_after=retry_after)

        # Only then the status code, which is ambiguous across providers.
        if error.code == 429:
            return ProviderRateLimited(
                "rate limited", evidence="HTTP status 429, no provider error code",
                retry_after=retry_after)
        if error.code == 404:
            return ModelUnavailable(
                "model unavailable", evidence="HTTP status 404")
        if error.code in (401, 403):
            return QuotaExhausted(
                "endpoint refused the credential",
                evidence=f"HTTP status {error.code}, no provider error code")
        if 500 <= error.code < 600:
            return ProviderOutage(
                "endpoint returned a server error",
                evidence=f"HTTP status {error.code}", retry_after=retry_after)
        return ProviderMalformedResponse(
            f"unexpected status {error.code}", evidence=f"HTTP status {error.code}")

    @staticmethod
    def _retry_after(error) -> float | None:
        value = error.headers.get("Retry-After") if error.headers else None
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    # ----------------------------------------------------------------- parsing
    def _parse(self, payload: bytes, request: CompletionRequest) -> CompletionResult:
        try:
            data = json.loads(payload)
        except ValueError as e:
            raise ProviderMalformedResponse(
                "response body was not JSON",
                evidence=f"parse error {type(e).__name__}") from None
        try:
            text = data["choices"][0]["message"]["content"]
            raw_usage = data["usage"]
            usage = Usage(
                prompt_tokens=int(raw_usage["prompt_tokens"]),
                completion_tokens=int(raw_usage["completion_tokens"]),
                total_tokens=int(raw_usage["total_tokens"]),
            )
        except (KeyError, IndexError, TypeError, ValueError) as e:
            raise ProviderMalformedResponse(
                "response was not a usable completion",
                evidence=f"shape error {type(e).__name__}") from None
        if not usage.is_self_consistent:
            # REQ-F-07-6 is per-sample accounting. A provider whose own figures
            # disagree is reported, not quietly reconciled by us.
            raise ProviderMalformedResponse(
                "provider usage figures are not self-consistent",
                evidence=f"{usage.prompt_tokens}+{usage.completion_tokens}"
                         f"!={usage.total_tokens}")
        return CompletionResult(
            text=text, model=data.get("model", request.model), usage=usage,
            endpoint_name=self._endpoint.name, endpoint_kind=self._endpoint.kind)
