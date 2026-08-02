"""Configuration, read from the environment and validated once at startup.

`REQ-N-OPS-2` requires environment-based configuration. Canonical §15 requires
that no secret is committed. Both are easy to satisfy and easy to undermine with
a convenient default, so there are none: a missing credential is an error at
startup, not a silent fallback discovered in production.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from decimal import Decimal


class ConfigurationError(RuntimeError):
    """Raised at startup. Never caught to substitute a default."""


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(
            f"{name} is not set. There is no default for this setting; a "
            f"configuration that falls back to a built-in value ships one."
        )
    return value


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass(frozen=True)
class ProviderEndpoint:
    """A configured endpoint. `kind` distinguishes hosted from self-hosted,
    which `REQ-F-02-4` makes a first-class distinction rather than a footnote."""
    name: str
    base_url: str
    api_key: str = field(repr=False, default="")
    kind: str = "hosted"

    def __post_init__(self):
        if self.kind not in ("hosted", "self_hosted"):
            raise ConfigurationError(f"unknown endpoint kind {self.kind!r}")

    def __str__(self) -> str:
        # Never interpolate the key. This object ends up in log lines and in
        # exception context, which is exactly where REQ-N-SEC-5 is lost.
        return f"ProviderEndpoint(name={self.name!r}, base_url={self.base_url!r})"


@dataclass(frozen=True)
class Config:
    env: str
    migration_dsn: str = field(repr=False)
    runtime_dsn: str = field(repr=False)
    redis_url: str
    default_budget_limit: Decimal
    default_budget_currency: str
    endpoints: tuple[ProviderEndpoint, ...] = ()

    def endpoint(self, name: str) -> ProviderEndpoint:
        for e in self.endpoints:
            if e.name == name:
                return e
        raise ConfigurationError(
            f"no endpoint named {name!r} is configured; configured: "
            f"{[e.name for e in self.endpoints]}"
        )


def load(require_database: bool = True) -> Config:
    endpoints = []
    openai_key = _optional("CLEP_PROVIDER_OPENAI_KEY")
    if openai_key:
        endpoints.append(ProviderEndpoint(
            name="openai",
            base_url=_optional("CLEP_PROVIDER_OPENAI_BASE",
                               "https://api.openai.com/v1"),
            api_key=openai_key, kind="hosted"))
    self_hosted = _optional("CLEP_PROVIDER_SELF_HOSTED_BASE")
    if self_hosted:
        endpoints.append(ProviderEndpoint(
            name="self-hosted", base_url=self_hosted, kind="self_hosted"))

    currency = _optional("CLEP_DEFAULT_BUDGET_CURRENCY", "USD")
    if len(currency) != 3:
        raise ConfigurationError("CLEP_DEFAULT_BUDGET_CURRENCY must be 3 characters")

    return Config(
        env=_optional("CLEP_ENV", "local"),
        migration_dsn=_require("CLEP_MIGRATION_DSN") if require_database else "",
        runtime_dsn=_require("CLEP_RUNTIME_DSN") if require_database else "",
        redis_url=_optional("CLEP_REDIS_URL", "redis://localhost:6399"),
        default_budget_limit=Decimal(_optional("CLEP_DEFAULT_BUDGET_LIMIT", "1.00")),
        default_budget_currency=currency,
        endpoints=tuple(endpoints),
    )
