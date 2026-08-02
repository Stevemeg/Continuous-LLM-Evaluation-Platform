"""The project-owned provider port, and the failure taxonomy it must express.

ADR-003's interim architectural constraint is that a project-owned port sits
between the domain and any provider library, whatever the spike concludes. This
module is that port. It exists so that approaches B and C can be compared on
equal terms: the same port, two different adapters behind it.

The taxonomy is the point. `REQ-N-REL-4` requires defined, tested behaviour for
outage, rate limiting, malformed response, and model deprecation *individually*.
If an approach cannot map a real failure onto exactly one of these without
inspecting message text, then it has not distinguished the failure modes - it has
guessed at them, and the guess will rot the first time a provider rewords an
error string.
"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class Completion:
    text: str
    model: str
    usage: Optional[Usage]
    raw_usage: dict = field(default_factory=dict)


class ProviderFailure(Exception):
    """Base. Never raised directly - the taxonomy below is exhaustive by design."""
    def __init__(self, message: str, *, evidence: str = ""):
        super().__init__(message)
        self.evidence = evidence


class ProviderOutage(ProviderFailure):
    """The endpoint could not be reached at all."""


class ProviderRateLimited(ProviderFailure):
    """The endpoint refused the call for quota reasons and may accept it later."""
    def __init__(self, message, *, retry_after=None, evidence=""):
        super().__init__(message, evidence=evidence)
        self.retry_after = retry_after


class ProviderMalformedResponse(ProviderFailure):
    """The endpoint answered, but the answer was not a usable completion."""


class ModelUnavailable(ProviderFailure):
    """The named model does not exist or has been withdrawn."""


TAXONOMY = (ProviderOutage, ProviderRateLimited,
            ProviderMalformedResponse, ModelUnavailable)


def classify(exc: Exception) -> Optional[type]:
    """Which taxonomy member is this? None means unclassified."""
    for t in TAXONOMY:
        if isinstance(exc, t):
            return t
    return None
