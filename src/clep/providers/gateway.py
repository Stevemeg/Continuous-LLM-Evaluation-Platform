"""The Provider Gateway: sole egress to model providers.

Two responsibilities the domain must not do for itself.

`REQ-F-02-6` — a failure belongs to the candidate that caused it. `invoke_all`
returns one outcome per candidate and never raises for a single candidate's
failure, so one dead endpoint cannot invalidate its siblings' results.

`REQ-F-07-6` — cost is attributed per call from the provider's own token counts
and a declared price. Prices are data, and unpriced models are reported as
unpriced rather than costed at zero: a silent zero is a budget that never trips.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from clep.providers.port import (CompletionRequest, CompletionResult,
                                 ProviderFailure)
from clep.telemetry import NULL_TELEMETRY


class UnpricedModel(LookupError):
    """Raised when cost is requested for a model with no declared price."""


@dataclass(frozen=True)
class Price:
    """Cost per one thousand tokens, exact. Never floating point."""
    prompt_per_1k: Decimal
    completion_per_1k: Decimal
    currency: str = "USD"


@dataclass(frozen=True)
class Cost:
    amount: Decimal
    currency: str


class PriceBook:
    """Declared prices. Deliberately not fetched from a provider at runtime: a
    cost figure that changes because a remote page changed is not auditable."""

    def __init__(self, prices: dict[str, Price] | None = None):
        self._prices = dict(prices or {})

    def declare(self, model: str, price: Price) -> None:
        self._prices[model] = price

    def has(self, model: str) -> bool:
        return model in self._prices

    def cost_of(self, model: str, prompt_tokens: int, completion_tokens: int) -> Cost:
        price = self._prices.get(model)
        if price is None:
            raise UnpricedModel(
                f"no declared price for {model!r}; refusing to record a cost of "
                f"zero, which would be a budget that never trips")
        amount = (price.prompt_per_1k * Decimal(prompt_tokens)
                  + price.completion_per_1k * Decimal(completion_tokens)) / Decimal(1000)
        return Cost(amount=amount, currency=price.currency)


@dataclass(frozen=True)
class CandidateInvocation:
    """One candidate's request within a run."""
    candidate_label: str
    endpoint_name: str
    request: CompletionRequest


@dataclass(frozen=True)
class CandidateOutcome:
    """One candidate's result. Exactly one of `result` or `failure` is set."""
    candidate_label: str
    result: CompletionResult | None = None
    failure: ProviderFailure | None = None
    cost: Cost | None = None
    unpriced: bool = False
    #: Wall-clock milliseconds the provider call took, measured here because
    #: `REQ-F-11-3` asks for latency distributions and the gateway is the sole
    #: egress (ADR-003). A second clock further up would time the queue, the
    #: evaluators and the parse as well, and report the total as the provider's.
    #: Present for a failure too: how long an outage took to declare itself is
    #: the most useful latency figure a run produces.
    latency_ms: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.result is not None

    @property
    def failure_kind(self) -> str | None:
        return self.failure.kind if self.failure else None


class ProviderGateway:
    def __init__(self, adapters: dict[str, object], price_book: PriceBook | None = None,
                 *, clock=None, telemetry=None):
        self._adapters = dict(adapters)
        self._prices = price_book or PriceBook()
        #: Injected so a test can assert a recorded latency rather than assert
        #: that some number was produced. Monotonic by default.
        self._clock = clock or time.monotonic
        #: The gateway is the sole egress (ADR-003), which makes it the only
        #: place provider behaviour can be observed without double-counting.
        #: A second clock or counter further up would time the queue, the
        #: evaluators and the parse as well, and report the total as the
        #: provider's — the blending ADR-023 rule 5 forbids.
        self._telemetry = telemetry or NULL_TELEMETRY

    def endpoints(self) -> list[str]:
        return sorted(self._adapters)

    def invoke(self, invocation: CandidateInvocation) -> CandidateOutcome:
        adapter = self._adapters.get(invocation.endpoint_name)
        if adapter is None:
            raise LookupError(f"no adapter for endpoint {invocation.endpoint_name!r}")
        # Monotonic, not wall clock: a clock correction mid-call would otherwise
        # produce a negative duration, and the store's CHECK would refuse the
        # sample over a fact about NTP.
        started = self._clock()
        try:
            result = adapter.complete(invocation.request)
        except ProviderFailure as failure:
            elapsed = self._elapsed(started)
            # How long an outage took to declare itself is the most useful
            # latency figure a run produces, so the failure path is measured too.
            self._observe(failure.kind, elapsed)
            self._telemetry.observe(
                "clep_retry_total", 1, surface="provider",
                retryable="retryable" if failure.is_retryable else "terminal")
            return CandidateOutcome(invocation.candidate_label, failure=failure,
                                    latency_ms=elapsed)
        latency_ms = self._elapsed(started)
        self._observe("ok", latency_ms)
        self._telemetry.observe("clep_model_tokens_total",
                                result.usage.prompt_tokens, direction="prompt")
        self._telemetry.observe("clep_model_tokens_total",
                                result.usage.completion_tokens,
                                direction="completion")

        model = result.model
        if not self._prices.has(model):
            # Reported, not guessed. The sample still has a result; what it
            # lacks is a defensible cost, and that distinction is preserved.
            self._telemetry.observe("clep_model_call_priced_total", 1,
                                    priced="unpriced")
            return CandidateOutcome(invocation.candidate_label, result=result,
                                    unpriced=True, latency_ms=latency_ms)
        cost = self._prices.cost_of(model, result.usage.prompt_tokens,
                                    result.usage.completion_tokens)
        self._telemetry.observe("clep_model_call_priced_total", 1,
                                priced="priced")
        return CandidateOutcome(invocation.candidate_label, result=result,
                                cost=cost, latency_ms=latency_ms)

    def _observe(self, outcome: str, latency_ms: int) -> None:
        self._telemetry.observe("clep_provider_call_total", 1, outcome=outcome)
        self._telemetry.observe("clep_model_call_duration_ms", latency_ms,
                                outcome=outcome)

    def _elapsed(self, started: float) -> int:
        return max(0, int((self._clock() - started) * 1000))

    def invoke_all(self, invocations: Iterable[CandidateInvocation]) -> list[CandidateOutcome]:
        """REQ-F-02-6. One candidate's failure never propagates to its siblings.

        Note what is NOT here: no early return, no exception escaping the loop,
        no shared mutable state between candidates. Isolation that depends on
        remembering to catch is isolation that lasts until someone forgets.
        """
        return [self.invoke(i) for i in invocations]
