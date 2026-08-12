"""Configuration: no defaults for credentials, and no credential in a repr."""
from __future__ import annotations

from decimal import Decimal

import pytest

from clep import config
from clep.config import ConfigurationError, ProviderEndpoint


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(config.os.environ):
        if name.startswith("CLEP_"):
            monkeypatch.delenv(name, raising=False)


def test_a_missing_database_url_is_an_error_not_a_fallback():
    with pytest.raises(ConfigurationError, match="no default"):
        config.load()


def test_configuration_loads_when_the_environment_is_complete(monkeypatch):
    monkeypatch.setenv("CLEP_MIGRATION_DSN", "postgresql://m/db")
    monkeypatch.setenv("CLEP_RUNTIME_DSN", "postgresql://r/db")
    loaded = config.load()
    assert loaded.env == "local"
    assert loaded.default_budget_limit == Decimal("1.00")
    assert loaded.default_budget_currency == "USD"
    assert loaded.endpoints == ()


def test_endpoints_appear_only_when_configured(monkeypatch):
    monkeypatch.setenv("CLEP_PROVIDER_OPENAI_KEY", "sk-" + "0" * 40)
    monkeypatch.setenv("CLEP_PROVIDER_SELF_HOSTED_BASE", "http://localhost:8100/v1")
    loaded = config.load(require_database=False)
    assert [e.name for e in loaded.endpoints] == ["openai", "self-hosted"]
    assert loaded.endpoint("self-hosted").kind == "self_hosted"
    assert loaded.endpoint("openai").kind == "hosted"


def test_an_absent_credential_disables_its_endpoint_rather_than_substituting_one(
        monkeypatch):
    monkeypatch.setenv("CLEP_PROVIDER_SELF_HOSTED_BASE", "http://localhost:8100/v1")
    loaded = config.load(require_database=False)
    assert [e.name for e in loaded.endpoints] == ["self-hosted"]
    with pytest.raises(ConfigurationError, match="no endpoint named"):
        loaded.endpoint("openai")


def test_a_malformed_currency_is_refused(monkeypatch):
    monkeypatch.setenv("CLEP_DEFAULT_BUDGET_CURRENCY", "DOLLARS")
    with pytest.raises(ConfigurationError, match="3 characters"):
        config.load(require_database=False)


def test_an_unknown_endpoint_kind_is_refused():
    with pytest.raises(ConfigurationError, match="unknown endpoint kind"):
        ProviderEndpoint(name="x", base_url="http://x", kind="somewhere_else")


def test_the_credential_is_absent_from_every_rendering_of_an_endpoint():
    """REQ-N-SEC-5. `str` and `repr` are what end up in log lines and in the
    context of an exception, which is exactly where a key gets lost."""
    canary = "sk-canary-" + "0" * 40
    endpoint = ProviderEndpoint(name="x", base_url="http://x", api_key=canary)
    assert canary not in str(endpoint)
    assert canary not in repr(endpoint)
    assert canary not in f"{endpoint}"
    assert endpoint.api_key == canary, "the value itself must still be usable"


def test_the_config_object_does_not_render_its_connection_strings():
    loaded = config.load(require_database=False)
    assert "postgresql" not in repr(loaded)


# ----------------------------------------------- REQ-N-SEC-6, transit security
@pytest.mark.parametrize("dsn", [
    "postgresql://user@db.example.invalid:5432/clep",
    "postgresql://user@db.example.invalid:5432/clep?sslmode=prefer",
    "postgresql://user@db.example.invalid:5432/clep?sslmode=disable",
    "redis://cache.example.invalid:6379",
])
def test_an_unencrypted_connection_is_refused_outside_a_local_environment(dsn):
    """`REQ-N-SEC-6`, executed rather than inspected.

    `sslmode=prefer` is the case that makes this worth enforcing: it is the
    PostgreSQL default and it falls back to plaintext without an error, so the
    misconfiguration is invisible in every log and in every successful request.
    """
    with pytest.raises(ConfigurationError, match="encrypted"):
        config.require_transport_security("production", "CLEP_RUNTIME_DSN", dsn)


@pytest.mark.parametrize("dsn", [
    "postgresql://user@db.example.invalid:5432/clep?sslmode=require",
    "postgresql://user@db.example.invalid:5432/clep?sslmode=verify-full",
    "rediss://cache.example.invalid:6380",
])
def test_an_encrypted_connection_is_accepted_outside_a_local_environment(dsn):
    config.require_transport_security("production", "CLEP_RUNTIME_DSN", dsn)


def test_the_local_stack_is_exempt_because_it_says_it_is_local():
    """Exempt by the declared environment, never by the hostname. `localhost`
    inside a container is not the same machine, and a check that trusted the
    word would pass in production."""
    config.require_transport_security(
        "local", "CLEP_RUNTIME_DSN", "postgresql://clep_app@localhost:5439/clep")
    with pytest.raises(ConfigurationError):
        config.require_transport_security(
            "production", "CLEP_RUNTIME_DSN",
            "postgresql://clep_app@localhost:5439/clep")


def test_a_scheme_the_platform_cannot_reason_about_is_refused_not_assumed():
    with pytest.raises(ConfigurationError, match="cannot reason about"):
        config.require_transport_security("production", "CLEP_RUNTIME_DSN",
                                          "mysteryproto://somewhere")


def test_loading_in_a_non_local_environment_enforces_it(monkeypatch):
    """Through `load`, not only through the helper: a check nothing calls is a
    check nobody benefits from."""
    monkeypatch.setenv("CLEP_ENV", "production")
    monkeypatch.setenv("CLEP_MIGRATION_DSN",
                       "postgresql://u@h/clep?sslmode=require")
    monkeypatch.setenv("CLEP_RUNTIME_DSN", "postgresql://u@h/clep")
    with pytest.raises(ConfigurationError, match="CLEP_RUNTIME_DSN"):
        config.load()
