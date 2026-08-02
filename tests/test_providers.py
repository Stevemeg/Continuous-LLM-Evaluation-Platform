"""Provider gateway: the five failure modes, isolation, usage and credentials.

These tests exist because the ADR-003 spike showed the aggregation library could
not distinguish an outage from a malformed response, and leaked the API key under
debug logging. Having rejected it for that, the replacement has to be held to the
standard that decided the ADR — otherwise the decision bought nothing.
"""
from __future__ import annotations

import io
import json
import logging
import urllib.error
from decimal import Decimal

import pytest

from clep.config import ProviderEndpoint
from clep.providers.gateway import (CandidateInvocation, Cost, Price, PriceBook,
                                    ProviderGateway, UnpricedModel)
from clep.providers.openai_compatible import OpenAICompatibleAdapter
from clep.providers.port import (CompletionRequest, ModelUnavailable,
                                 ProviderMalformedResponse, ProviderOutage,
                                 ProviderRateLimited, QuotaExhausted, TAXONOMY,
                                 Usage)

# A deliberately fake value, planted so the leak checks below have something to
# find. Named for what it is: a canary, not a credential.
CANARY = "sk-canary-" + "0" * 40
ENDPOINT = ProviderEndpoint(name="test", base_url="http://endpoint.invalid/v1",
                            api_key=CANARY, kind="hosted")
REQUEST = CompletionRequest(model="test-model", prompt="hello")


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def responder(body: dict | bytes):
    payload = body if isinstance(body, bytes) else json.dumps(body).encode()

    def _open(req, timeout=None):
        return _Response(payload)
    return _open


def http_error(status: int, body: dict, headers: dict | None = None):
    def _open(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, status, "err", headers or {},
            io.BytesIO(json.dumps(body).encode()))
    return _open


def url_error(reason=ConnectionRefusedError("refused")):
    def _open(req, timeout=None):
        raise urllib.error.URLError(reason)
    return _open


OK_BODY = {"choices": [{"message": {"content": "OK"}}],
           "model": "test-model",
           "usage": {"prompt_tokens": 11, "completion_tokens": 3, "total_tokens": 14}}


# ------------------------------------------------------------------ successes
def test_usage_is_taken_from_the_provider_not_estimated():
    adapter = OpenAICompatibleAdapter(ENDPOINT, opener=responder(OK_BODY))
    result = adapter.complete(REQUEST)
    assert result.usage == Usage(11, 3, 14)
    assert result.usage.is_self_consistent


def test_self_hosted_endpoints_use_the_same_adapter():
    """REQ-F-02-4: first-class, not a special case. The only difference is that
    no credential is sent."""
    endpoint = ProviderEndpoint(name="local", base_url="http://localhost:8100/v1",
                                kind="self_hosted")
    adapter = OpenAICompatibleAdapter(endpoint, opener=responder(OK_BODY))
    result = adapter.complete(REQUEST)
    assert result.endpoint_kind == "self_hosted"
    assert result.usage.total_tokens == 14


def test_inconsistent_provider_usage_is_reported_not_reconciled():
    body = dict(OK_BODY, usage={"prompt_tokens": 11, "completion_tokens": 3,
                                "total_tokens": 99})
    adapter = OpenAICompatibleAdapter(ENDPOINT, opener=responder(body))
    with pytest.raises(ProviderMalformedResponse):
        adapter.complete(REQUEST)


# -------------------------------------------------------------- failure modes
@pytest.mark.parametrize("opener,expected", [
    (url_error(), ProviderOutage),
    (http_error(429, {"error": {"type": "rate_limit_error",
                                "code": "rate_limit_exceeded"}}), ProviderRateLimited),
    (http_error(404, {"error": {"type": "invalid_request_error",
                                "code": "model_not_found"}}), ModelUnavailable),
    (responder(b'{"choices": [ {"mesage": '), ProviderMalformedResponse),
    (http_error(429, {"error": {"type": "insufficient_quota",
                                "code": "insufficient_quota"}}), QuotaExhausted),
    (http_error(401, {"error": {"type": "insufficient_quota",
                                "code": 401}}), QuotaExhausted),
])
def test_each_failure_mode_maps_to_exactly_one_taxonomy_member(opener, expected):
    adapter = OpenAICompatibleAdapter(ENDPOINT, opener=opener)
    with pytest.raises(expected) as caught:
        adapter.complete(REQUEST)
    assert caught.value.evidence, "a classification must record what it rested on"


def test_the_two_modes_the_aggregation_library_could_not_separate():
    """The finding that decided ADR-003: an outage and a malformed response
    arrived as one class with one status. Here they must not."""
    outage = OpenAICompatibleAdapter(ENDPOINT, opener=url_error())
    malformed = OpenAICompatibleAdapter(
        ENDPOINT, opener=responder(b'{"choices": [ {"mesage": '))
    with pytest.raises(ProviderOutage) as a:
        outage.complete(REQUEST)
    with pytest.raises(ProviderMalformedResponse) as b:
        malformed.complete(REQUEST)
    assert type(a.value) is not type(b.value)


def test_exhausted_quota_is_terminal_and_a_rate_limit_is_not():
    """Both arrive as HTTP 429 from the same provider. Classifying quota
    exhaustion as retryable produces an infinite retry against a condition no
    retry can change - the live bug the ADR-003 spike found."""
    quota = OpenAICompatibleAdapter(
        ENDPOINT, opener=http_error(429, {"error": {"type": "insufficient_quota"}}))
    rate = OpenAICompatibleAdapter(
        ENDPOINT, opener=http_error(429, {"error": {"code": "rate_limit_exceeded"}},
                                    {"Retry-After": "2"}))
    with pytest.raises(QuotaExhausted) as q:
        quota.complete(REQUEST)
    with pytest.raises(ProviderRateLimited) as r:
        rate.complete(REQUEST)
    assert q.value.is_retryable is False
    assert r.value.is_retryable is True
    assert r.value.retry_after == 2.0


def test_the_provider_error_code_beats_the_status_code():
    """Perplexity reported an exhausted quota as 401 and OpenAI as 429. A
    status-first classifier gets one of them wrong, whichever order it picks."""
    adapter = OpenAICompatibleAdapter(
        ENDPOINT, opener=http_error(401, {"error": {"type": "insufficient_quota"}}))
    with pytest.raises(QuotaExhausted) as caught:
        adapter.complete(REQUEST)
    assert "insufficient_quota" in caught.value.evidence


def test_a_timeout_is_an_outage_not_a_malformed_response():
    def _open(req, timeout=None):
        raise TimeoutError("socket timed out")
    adapter = OpenAICompatibleAdapter(ENDPOINT, opener=_open)
    with pytest.raises(ProviderOutage, match="timed out"):
        adapter.complete(REQUEST)


def test_a_server_error_is_an_outage_and_is_retryable():
    adapter = OpenAICompatibleAdapter(
        ENDPOINT, opener=http_error(503, {"error": {}}, {"Retry-After": "5"}))
    with pytest.raises(ProviderOutage) as caught:
        adapter.complete(REQUEST)
    assert caught.value.is_retryable and caught.value.retry_after == 5.0


def test_an_unexpected_status_is_malformed_rather_than_guessed_at():
    adapter = OpenAICompatibleAdapter(ENDPOINT, opener=http_error(418, {"error": {}}))
    with pytest.raises(ProviderMalformedResponse, match="418"):
        adapter.complete(REQUEST)


def test_a_bare_404_without_an_error_code_is_still_a_missing_model():
    adapter = OpenAICompatibleAdapter(ENDPOINT, opener=http_error(404, {}))
    with pytest.raises(ModelUnavailable):
        adapter.complete(REQUEST)


def test_an_unparseable_error_body_does_not_defeat_classification():
    """A provider that returns a rate-limit status with an HTML error page still
    has to be classified. Falling through to `unclassified` would leave the
    caller with nothing to decide on."""
    def _open(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 429, "err", {},
                                     io.BytesIO(b"<html>too many</html>"))
    adapter = OpenAICompatibleAdapter(ENDPOINT, opener=_open)
    with pytest.raises(ProviderRateLimited):
        adapter.complete(REQUEST)


def test_a_malformed_retry_after_is_ignored_rather_than_crashing():
    adapter = OpenAICompatibleAdapter(
        ENDPOINT, opener=http_error(429, {"error": {}}, {"Retry-After": "soon"}))
    with pytest.raises(ProviderRateLimited) as caught:
        adapter.complete(REQUEST)
    assert caught.value.retry_after is None


def test_a_response_missing_usage_is_malformed_not_costed_at_zero():
    adapter = OpenAICompatibleAdapter(
        ENDPOINT, opener=responder({"choices": [{"message": {"content": "x"}}]}))
    with pytest.raises(ProviderMalformedResponse, match="usable completion"):
        adapter.complete(REQUEST)


def test_negative_token_counts_are_refused():
    with pytest.raises(ValueError):
        Usage(-1, 0, -1)


def test_the_taxonomy_is_exhaustive_and_each_member_declares_retryability():
    assert len(TAXONOMY) == 5
    assert {t.kind for t in TAXONOMY} == {
        "provider_outage", "provider_rate_limited", "quota_exhausted",
        "provider_malformed", "model_unavailable"}
    assert [t.is_retryable for t in TAXONOMY].count(True) == 2


# ----------------------------------------------------------------- isolation
def test_one_candidate_failing_leaves_its_siblings_valid():
    """REQ-F-02-6, and the reason resolution lives on the sample."""
    prices = PriceBook({"test-model": Price(Decimal("0.001"), Decimal("0.002"))})
    gateway = ProviderGateway(
        {"good": OpenAICompatibleAdapter(ENDPOINT, opener=responder(OK_BODY)),
         "bad": OpenAICompatibleAdapter(ENDPOINT, opener=url_error())}, prices)
    outcomes = gateway.invoke_all([
        CandidateInvocation("a", "good", REQUEST),
        CandidateInvocation("b", "bad", REQUEST),
        CandidateInvocation("c", "good", REQUEST),
    ])
    assert [o.succeeded for o in outcomes] == [True, False, True]
    assert outcomes[1].failure_kind == "provider_outage"
    assert outcomes[0].cost.amount == Decimal("0.001") * 11 / 1000 + \
        Decimal("0.002") * 3 / 1000


# ---------------------------------------------------------------------- cost
def test_an_unpriced_model_is_reported_unpriced_never_costed_at_zero():
    gateway = ProviderGateway(
        {"e": OpenAICompatibleAdapter(ENDPOINT, opener=responder(OK_BODY))},
        PriceBook())
    outcome = gateway.invoke(CandidateInvocation("a", "e", REQUEST))
    assert outcome.succeeded and outcome.unpriced and outcome.cost is None
    with pytest.raises(UnpricedModel):
        PriceBook().cost_of("test-model", 1, 1)


def test_cost_is_exact_decimal_arithmetic():
    book = PriceBook({"m": Price(Decimal("0.15"), Decimal("0.60"))})
    cost = book.cost_of("m", 1000, 1000)
    assert cost == Cost(Decimal("0.75"), "USD")
    assert isinstance(cost.amount, Decimal)


# --------------------------------------------------------------- credentials
def test_the_credential_never_appears_in_a_repr_or_a_log(caplog):
    """REQ-N-SEC-5. The detector is proven able to fail below, because a leak
    check that has never reported a leak has not been shown to work."""
    canary = ENDPOINT.api_key
    adapter = OpenAICompatibleAdapter(ENDPOINT, opener=url_error())
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logging.getLogger().addHandler(handler)
    logging.getLogger().setLevel(logging.DEBUG)
    try:
        with pytest.raises(ProviderOutage) as caught:
            adapter.complete(REQUEST)
        surfaces = {"repr(adapter)": repr(adapter), "str(endpoint)": str(ENDPOINT),
                    "repr(endpoint)": repr(ENDPOINT), "str(error)": str(caught.value),
                    "repr(error)": repr(caught.value),
                    "evidence": caught.value.evidence, "log": stream.getvalue()}
    finally:
        logging.getLogger().removeHandler(handler)
    leaked = [name for name, text in surfaces.items() if canary in text]
    assert not leaked, f"credential appeared in {leaked}"


def test_the_credential_detector_can_actually_detect_one():
    canary = ENDPOINT.api_key
    assert canary in f"Authorization: Bearer {canary}"
    assert canary not in str(ENDPOINT)
