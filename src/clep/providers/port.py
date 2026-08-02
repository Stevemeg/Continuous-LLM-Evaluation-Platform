"""The provider port, and the failure taxonomy the domain reasons about.

ADR-003 selected internal adapters behind a project-owned port on measured
evidence. The port is here, above every adapter, so that no dependency defines
the domain's model of a provider call.

The taxonomy has five members, not the four `REQ-N-REL-4` enumerates. The fifth
came out of the spike: two real providers reported an exhausted quota as HTTP 429
and HTTP 401 respectively — the same condition arriving as a rate limit from one
and an authentication failure from the other. Treating it as a rate limit means
retrying a condition that cannot resolve itself until someone pays a bill.

`is_retryable` is a property of the failure, not a decision made at the call
site, so that no caller has to remember which is which.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Usage:
    """What the provider reported, not what we estimated. REQ-F-07-6."""
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int

    def __post_init__(self):
        if self.prompt_tokens < 0 or self.completion_tokens < 0:
            raise ValueError("token counts cannot be negative")

    @property
    def is_self_consistent(self) -> bool:
        return self.prompt_tokens + self.completion_tokens == self.total_tokens


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    prompt: str
    max_tokens: int = 256
    temperature: float = 0.0
    # REQ-F-02-2: every parameter that affects output is recorded, so that a run
    # can be reproduced from its record rather than from someone's memory.
    extra_parameters: dict = field(default_factory=dict)


@dataclass(frozen=True)
class CompletionResult:
    text: str
    model: str
    usage: Usage
    endpoint_name: str
    endpoint_kind: str


class ProviderFailure(Exception):
    """Base of an exhaustive taxonomy. Never raised directly."""

    #: Whether retrying the identical call could succeed without intervention.
    is_retryable: bool = False
    #: Stable identifier written to `run_sample.failure_kind`.
    kind: str = "provider_failure"

    def __init__(self, message: str, *, evidence: str = "", retry_after: float | None = None):
        super().__init__(message)
        #: The structured signal the classification rested on. Recorded so that a
        #: later reader can tell a real classification from a lucky guess.
        self.evidence = evidence
        self.retry_after = retry_after


class ProviderOutage(ProviderFailure):
    """The endpoint could not be reached. Retrying may work."""
    is_retryable = True
    kind = "provider_outage"


class ProviderRateLimited(ProviderFailure):
    """Refused for rate reasons and expected to accept the call later."""
    is_retryable = True
    kind = "provider_rate_limited"


class QuotaExhausted(ProviderFailure):
    """Refused because the account has no spendable quota.

    Terminal. This is the member `REQ-N-REL-4`'s enumeration does not contain,
    and the one that costs the most to get wrong: classified as a rate limit it
    produces an infinite retry against a condition no retry can change.
    """
    is_retryable = False
    kind = "quota_exhausted"


class ProviderMalformedResponse(ProviderFailure):
    """The endpoint answered, but not with a usable completion.

    Not retryable by default: a provider returning an unparseable body is a
    defect, and hammering it converts one bad response into many.
    """
    is_retryable = False
    kind = "provider_malformed"


class ModelUnavailable(ProviderFailure):
    """The named model does not exist or has been withdrawn. Terminal."""
    is_retryable = False
    kind = "model_unavailable"


TAXONOMY: tuple[type[ProviderFailure], ...] = (
    ProviderOutage, ProviderRateLimited, QuotaExhausted,
    ProviderMalformedResponse, ModelUnavailable,
)


class ProviderPort(Protocol):
    """What the domain is allowed to know about a provider."""

    def complete(self, request: CompletionRequest) -> CompletionResult:
        ...
