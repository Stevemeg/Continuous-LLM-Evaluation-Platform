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

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from clep.providers.port import (CompletionRequest, CompletionResult,
                                 ProviderFailure)


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

    @property
    def succeeded(self) -> bool:
        return self.result is not None

    @property
    def failure_kind(self) -> str | None:
        return self.failure.kind if self.failure else None


class ProviderGateway:
    def __init__(self, adapters: dict[str, object], price_book: PriceBook | None = None):
        self._adapters = dict(adapters)
        self._prices = price_book or PriceBook()

    def endpoints(self) -> list[str]:
        return sorted(self._adapters)

    def invoke(self, invocation: CandidateInvocation) -> CandidateOutcome:
        adapter = self._adapters.get(invocation.endpoint_name)
        if adapter is None:
            raise LookupError(f"no adapter for endpoint {invocation.endpoint_name!r}")
        try:
            result = adapter.complete(invocation.request)
        except ProviderFailure as failure:
            return CandidateOutcome(invocation.candidate_label, failure=failure)

        model = result.model
        if not self._prices.has(model):
            # Reported, not guessed. The sample still has a result; what it
            # lacks is a defensible cost, and that distinction is preserved.
            return CandidateOutcome(invocation.candidate_label, result=result,
                                    unpriced=True)
        cost = self._prices.cost_of(model, result.usage.prompt_tokens,
                                    result.usage.completion_tokens)
        return CandidateOutcome(invocation.candidate_label, result=result, cost=cost)

    def invoke_all(self, invocations: Iterable[CandidateInvocation]) -> list[CandidateOutcome]:
        """REQ-F-02-6. One candidate's failure never propagates to its siblings.

        Note what is NOT here: no early return, no exception escaping the loop,
        no shared mutable state between candidates. Isolation that depends on
        remembering to catch is isolation that lasts until someone forgets.
        """
        return [self.invoke(i) for i in invocations]
