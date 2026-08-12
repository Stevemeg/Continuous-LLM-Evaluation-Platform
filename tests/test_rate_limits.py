"""The per-tenant rate limiter (ADR-021), driven at exact instants.

Against real Redis, because the property being tested is that two processes
sharing a broker share a bucket, and a fake broker proves only that the fake was
called. The clock is injected, so nothing here sleeps and every boundary is
asserted at the moment it matters rather than near it.
"""
from __future__ import annotations

import pytest

from clep.security.limits import LimiterUnavailable, RateLimiter
from tests.conftest import REDIS_URL, requires_redis

pytestmark = [pytest.mark.integration, requires_redis]


class Clock:
    """Time as a value. A limiter that read the wall clock could only be tested
    by sleeping, so in practice it would be tested loosely or not at all."""

    def __init__(self, now: float = 1_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def redis_client():
    import redis
    client = redis.Redis.from_url(REDIS_URL)
    yield client
    for key in client.scan_iter("clep:ratelimit:test:*"):
        client.delete(key)


def limiter(redis_client, clock, limit=5, prefix="clep:ratelimit:test"):
    return RateLimiter(redis_client, lambda org: limit, clock=clock,
                       prefix=prefix)


def test_the_bucket_admits_its_capacity_and_then_refuses(redis_client):
    clock = Clock()
    limits = limiter(redis_client, clock, limit=5)
    tenant = f"t-{clock.now}"
    assert all(limits.check(tenant).allowed for _ in range(5))
    refused = limits.check(tenant)
    assert not refused.allowed
    assert "5 request(s) per minute" in refused.detail


def test_a_refusal_says_when_the_caller_may_try_again(redis_client):
    """`REQ-N-USE-3`: a failure message states what the caller can do about it.
    'You are rate limited' without a reset time does not."""
    clock = Clock()
    limits = limiter(redis_client, clock, limit=6)
    tenant = f"t-reset-{clock.now}"
    for _ in range(6):
        limits.check(tenant)
    detail = limits.check(tenant).detail
    assert "second(s)" in detail
    assert "in 0 second(s)" not in detail, \
        "telling a caller to retry immediately is what filled the bucket"


def test_the_bucket_refills_continuously_rather_than_in_steps(redis_client):
    clock = Clock()
    limits = limiter(redis_client, clock, limit=60)   # one per second
    tenant = f"t-refill-{clock.now}"
    for _ in range(60):
        assert limits.check(tenant).allowed
    assert not limits.check(tenant).allowed
    clock.advance(1.0)
    assert limits.check(tenant).allowed, "one second buys exactly one token"
    assert not limits.check(tenant).allowed


def test_the_bucket_never_refills_past_its_capacity(redis_client):
    """An idle tenant does not accumulate a burst the limit never authorised."""
    clock = Clock()
    limits = limiter(redis_client, clock, limit=5)
    tenant = f"t-cap-{clock.now}"
    limits.check(tenant)
    clock.advance(3600.0)
    assert sum(1 for _ in range(20) if limits.check(tenant).allowed) == 5


def test_one_tenant_cannot_exhaust_another_tenants_allowance(redis_client):
    """`REQ-N-SCALE-2`, asserted rather than inferred from the key looking
    right."""
    clock = Clock()
    limits = limiter(redis_client, clock, limit=3)
    mine, yours = f"a-{clock.now}", f"b-{clock.now}"
    for _ in range(3):
        assert limits.check(mine).allowed
    assert not limits.check(mine).allowed
    assert limits.check(yours).allowed


def test_the_limit_is_read_per_request_so_an_operator_change_takes_effect(
        redis_client):
    """The limiter holds a callable rather than a number. A cached limit is a
    limit that keeps applying after somebody changed it.

    Raising the ceiling does not conjure tokens into an empty bucket — the
    tenant already spent them — but it does change the rate at which the bucket
    refills and the ceiling it refills to, both immediately. Asserting that
    rather than instant relief is the difference between testing the limiter
    and testing a wish.
    """
    clock = Clock()
    configured = {"value": 1}
    limits = RateLimiter(redis_client, lambda org: configured["value"],
                         clock=clock, prefix="clep:ratelimit:test")
    tenant = f"t-config-{clock.now}"
    assert limits.check(tenant).allowed
    assert not limits.check(tenant).allowed

    configured["value"] = 60
    assert "60 request(s) per minute" in limits.check(tenant).detail
    clock.advance(1.0)
    assert limits.check(tenant).allowed, \
        "the new refill rate is in force at once, not after the old window"
    clock.advance(60.0)
    assert sum(1 for _ in range(70) if limits.check(tenant).allowed) == 60, \
        "and the new ceiling is the one the bucket refills to"


def test_the_limiter_fails_closed_when_the_broker_is_unreachable():
    """ADR-021 rule 5. The usual argument for failing open assumes the resource
    being protected recovers; here it is money spent at a provider, and it does
    not."""
    class Broken:
        def eval(self, *args, **kwargs):
            raise ConnectionError("no route to broker")

    limits = RateLimiter(Broken(), lambda org: 10, clock=Clock())
    with pytest.raises(LimiterUnavailable, match="refused rather than admitted"):
        limits.check("any-tenant")


def test_a_configured_limit_of_zero_denies_rather_than_admits(redis_client):
    """Not reachable through the store, which refuses it. Reached only if a
    limit were written around that constraint, and the fail-closed reading is
    the one that does not turn a misconfiguration into an open door."""
    limits = RateLimiter(redis_client, lambda org: 0, clock=Clock(),
                         prefix="clep:ratelimit:test")
    verdict = limits.check("any-tenant")
    assert not verdict.allowed
    assert "no requests are permitted" in verdict.detail
