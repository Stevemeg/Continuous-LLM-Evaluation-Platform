"""The result cache — REQ-F-07-4.

"shall ensure that result caching never changes the outcome of an evaluation
relative to an uncached execution, and shall record whether a result was served
from cache."

The dangerous failure here is not a cache miss. It is a hit that answers a
different question than the one asked, because the key covered less than the
inputs did — the result still looks entirely plausible, and nothing anywhere
reports a problem.
"""
from __future__ import annotations

import psycopg
import pytest

from clep.db.session import tenant_session
from clep.experiments.cache import (KEY_FIELDS, CacheKeyIncomplete, ResultCache,
                                    cache_key)
from clep.identity import ulid_to_uuid
from clep.registry.repository import RegistryRepository
from tests.conftest import requires_postgres

BASE = {"model_configuration_digest": "sha256:" + "a" * 64,
        "prompt_version_digest": "sha256:" + "b" * 64,
        "example_content_digest": "sha256:" + "c" * 64,
        "integration_tier": "output_only"}


# ------------------------------------------------------------------ the key
def test_the_same_question_produces_the_same_key():
    assert cache_key(**BASE) == cache_key(**dict(BASE))


@pytest.mark.parametrize("field", KEY_FIELDS)
def test_changing_any_output_affecting_input_changes_the_key(field):
    """Every field in KEY_FIELDS, one at a time. A field that could change and
    leave the key alone is the whole bug this requirement is about."""
    altered = dict(BASE)
    altered[field] = altered[field] + "-changed"
    assert cache_key(**altered) != cache_key(**BASE)


def test_a_key_cannot_be_built_from_partial_inputs():
    incomplete = dict(BASE)
    incomplete["prompt_version_digest"] = None
    with pytest.raises(CacheKeyIncomplete, match="prompt_version_digest"):
        cache_key(**incomplete)


def test_an_unrecognised_field_is_refused_rather_than_ignored():
    """Silently ignoring it would mean an input the caller believed was in the
    key was not, which is exactly the invisible failure."""
    with pytest.raises(CacheKeyIncomplete, match="temperature"):
        cache_key(**BASE, temperature="0.7")


# --------------------------------------------------------------- the store
@pytest.mark.integration
@requires_postgres
def test_a_stored_result_is_returned_unchanged(migrated_database, seeded):
    key = cache_key(**BASE)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        cache = ResultCache(conn, seeded["organization"])
        assert cache.get(key) is None
        assert cache.put(key,
                         model_configuration_id=seeded["model_configuration"],
                         output_text="Paris", prompt_tokens=10,
                         completion_tokens=2) is True
        hit = cache.get(key)
    assert hit.output_text == "Paris"
    assert (hit.prompt_tokens, hit.completion_tokens) == (10, 2)


@pytest.mark.integration
@requires_postgres
def test_a_second_write_to_the_same_key_does_not_overwrite(migrated_database, seeded):
    """The same key must not answer differently over time."""
    key = cache_key(**BASE)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        cache = ResultCache(conn, seeded["organization"])
        cache.put(key, model_configuration_id=seeded["model_configuration"],
                  output_text="first", prompt_tokens=1, completion_tokens=1)
        stored_again = cache.put(
            key, model_configuration_id=seeded["model_configuration"],
            output_text="second", prompt_tokens=9, completion_tokens=9)
        hit = cache.get(key)
    assert stored_again is False
    assert hit.output_text == "first"


@pytest.mark.integration
@requires_postgres
def test_a_nondeterministic_configuration_cannot_be_cached(migrated_database, seeded):
    """Rule 2, enforced by the store. Caching a sampled configuration replaces a
    fresh draw from a distribution with one fixed draw — the outcome does change,
    and no cache key can fix that."""
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        registry = RegistryRepository(conn, seeded["organization"])
        provider_id = registry.create_provider(slug="sampled", display_name="S",
                                               endpoint_kind="hosted")
        model_id = registry.create_model(provider_id, model_identifier="m",
                                         display_name="M")
        sampled = registry.add_model_configuration(
            model_id, parameters={"temperature": 0.9},
            created_by=seeded["organization"])
        assert registry.get_model_configuration(sampled)["isDeterministic"] is False

    with pytest.raises(psycopg.errors.Error, match="REQ-F-07-4"):
        with tenant_session(migrated_database, seeded["organization"]) as conn:
            ResultCache(conn, seeded["organization"]).put(
                cache_key(**BASE), model_configuration_id=sampled,
                output_text="a sample", prompt_tokens=1, completion_tokens=1)


@pytest.mark.integration
@requires_postgres
def test_another_tenant_cannot_read_a_cached_result(migrated_database, seeded,
                                                    second_organization):
    """A cached completion is model output about the tenant's own data. It is
    exactly as sensitive as the sample it came from."""
    key = cache_key(**BASE)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        ResultCache(conn, seeded["organization"]).put(
            key, model_configuration_id=seeded["model_configuration"],
            output_text="private", prompt_tokens=1, completion_tokens=1)
    with tenant_session(migrated_database, second_organization) as conn:
        assert ResultCache(conn, second_organization).get(key) is None


@pytest.mark.integration
@requires_postgres
def test_a_cache_row_records_the_digest_of_what_it_stores(migrated_database, seeded):
    key = cache_key(**BASE)
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        ResultCache(conn, seeded["organization"]).put(
            key, model_configuration_id=seeded["model_configuration"],
            output_text="Paris", prompt_tokens=1, completion_tokens=1)
        digest, text = conn.execute(
            "SELECT output_digest, output_text FROM clep.result_cache "
            "WHERE organization_id = %s AND cache_key = %s",
            (seeded["organization"], key)).fetchone()
    import hashlib
    assert digest == "sha256:" + hashlib.sha256(text.encode()).hexdigest()


@pytest.mark.integration
@requires_postgres
def test_the_sample_records_whether_it_was_served_from_cache(migrated_database,
                                                             seeded):
    """The second half of REQ-F-07-4. A cached result that is indistinguishable
    from a fresh one in the record cannot be audited."""
    with tenant_session(migrated_database, seeded["organization"]) as conn:
        column = conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = 'clep' AND table_name = 'run_sample' "
            "AND column_name = 'is_served_from_cache'").fetchone()
    assert column is not None
