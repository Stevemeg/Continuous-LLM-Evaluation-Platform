"""Per-tenant rate limiting (ADR-021).

A token bucket in Redis, keyed by tenant, refilled from an injected clock.

Redis rather than a process-local counter because a per-process limiter is not a
limit when there is more than one process, and there is: the API and the worker
already share this broker.

The clock is injected because a limiter that reads the wall clock can only be
tested by sleeping, so in practice it is tested loosely or not at all. Every
property below — that the bucket empties, that it refills, that one tenant's
exhaustion leaves another's untouched — is asserted at an exact instant.

**It fails closed** (ADR-021 rule 5). If the coordination store is unreachable,
requests are refused. The usual argument for failing open assumes the protected
resource is the platform's own capacity, which recovers. Here the resource is
money spent at a provider, which does not.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

#: Lua rather than GET-then-SET. Two callers that each read a bucket and then
#: wrote it would both see room only one of them had; the whole point of a
#: shared limiter is that they do not. Redis runs a script atomically, so the
#: refill, the test and the decrement are one operation.
_BUCKET = """
local key      = KEYS[1]
local capacity = tonumber(ARGV[1])
local per_sec  = tonumber(ARGV[2])
local now      = tonumber(ARGV[3])
local ttl      = tonumber(ARGV[4])

local state  = redis.call('HMGET', key, 'tokens', 'at')
local tokens = tonumber(state[1])
local at     = tonumber(state[2])
if tokens == nil or at == nil then
    tokens = capacity
    at = now
end
local refill = (now - at) * per_sec
if refill > 0 then
    tokens = math.min(capacity, tokens + refill)
    at = now
end
local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end
redis.call('HMSET', key, 'tokens', tokens, 'at', at)
redis.call('EXPIRE', key, ttl)
return {allowed, tostring(tokens)}
"""


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    remaining: float
    detail: str = ""


class LimiterUnavailable(RuntimeError):
    """The coordination store could not be reached.

    Raised rather than swallowed. The caller converts it into a refusal, which
    is rule 5: absent under load is the one state this control must not have.
    """


class RateLimiter:
    """One bucket per tenant.

    `limit_for` is a callable rather than a number because the limit is tenant
    configuration and this class must not hold a copy of it — a cached limit is
    a limit that keeps applying after an operator changed it.
    """

    def __init__(self, redis_client, limit_for, *, clock=time.time,
                 prefix: str = "clep:ratelimit"):
        self._redis = redis_client
        self._limit_for = limit_for
        self._clock = clock
        self._prefix = prefix

    def check(self, organization_id: str) -> Verdict:
        capacity = int(self._limit_for(organization_id))
        if capacity < 1:
            # ADR-021 rule 6. Reached only if a limit was written around the
            # store's own constraint, and refusing is the fail-closed reading:
            # a capacity of zero denies rather than admits.
            return Verdict(False, 0.0, "no requests are permitted for this "
                                       "tenant; the configured limit is empty")
        per_second = capacity / 60.0
        # A bucket that has been idle longer than it takes to refill completely
        # is indistinguishable from a fresh one, so it costs nothing to forget.
        ttl = max(60, int(capacity / per_second) * 2)
        try:
            allowed, remaining = self._redis.eval(
                _BUCKET, 1, f"{self._prefix}:{organization_id}",
                capacity, per_second, self._clock(), ttl)
        except Exception as exc:  # noqa: BLE001 - any failure is unavailability
            raise LimiterUnavailable(
                "the rate limiter's coordination store is unreachable; the "
                "request is refused rather than admitted, because a limiter "
                "that fails open is absent exactly when it is needed") from exc
        remaining = float(remaining)
        if int(allowed) == 1:
            return Verdict(True, remaining)
        return Verdict(
            False, remaining,
            f"rate limit reached: this tenant may make {capacity} request(s) "
            f"per minute. The allowance refills continuously; one more request "
            f"is available in {_seconds_until_a_token(remaining, per_second)} "
            f"second(s).")


def _seconds_until_a_token(remaining: float, per_second: float) -> int:
    """Rounded up, and never zero: telling a caller to retry in no time at all
    is telling them to retry immediately, which is what filled the bucket."""
    import math
    return max(1, math.ceil((1.0 - remaining) / per_second))
