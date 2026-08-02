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
