"""Result cache that cannot change an outcome.

`REQ-F-07-4`: "shall ensure that result caching never changes the outcome of an
evaluation relative to an uncached execution, and shall record whether a result
was served from cache."

Two rules make the first half true, and they are the whole design:

  1. The key covers every input that can change the output. If it covered less,
     a hit would answer a different question than the one asked, and the result
     would still look entirely plausible.
  2. Only a deterministic configuration is eligible. Caching a sampled
     configuration replaces a fresh draw from a distribution with one fixed draw
     — the outcome *does* change, in expectation, and no key can fix that.

Rule 2 is also enforced by a trigger in the schema, because this module is not
the only thing that can insert into the table.
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from clep.identity import ulid_to_uuid


class CacheKeyIncomplete(ValueError):
    """Raised rather than computing a key from partial inputs.

    A key silently derived from whatever the caller happened to pass is the
    failure REQ-F-07-4 is about. Better to refuse to cache than to cache wrongly.
    """


#: Every field here can change the output. Adding an input that can change the
#: output without adding it here is the one way to break this cache.
KEY_FIELDS = ("model_configuration_digest", "prompt_version_digest",
              "example_content_digest", "integration_tier")


def cache_key(**parts: object) -> str:
    missing = [f for f in KEY_FIELDS if parts.get(f) in (None, "")]
    if missing:
        raise CacheKeyIncomplete(
            f"cannot build a cache key without {', '.join(missing)}; a key that "
            f"omits an output-affecting input returns the answer to a different "
            f"question")
    unexpected = set(parts) - set(KEY_FIELDS)
    if unexpected:
        raise CacheKeyIncomplete(
            f"unrecognised cache key field(s): {', '.join(sorted(unexpected))}. "
            f"If one of these affects the output it belongs in KEY_FIELDS; if it "
            f"does not, it must not vary the key")
    material = "\x1f".join(str(parts[f]) for f in KEY_FIELDS)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CachedResult:
    output_text: str
    prompt_tokens: int
    completion_tokens: int


class ResultCache:
    """Tenant-scoped by the session, like every other store access."""

    def __init__(self, conn, organization_id: str):
        self._conn = conn
        self._org = str(organization_id)

    def get(self, key: str) -> CachedResult | None:
        row = self._conn.execute(
            "SELECT output_text, prompt_tokens, completion_tokens "
            "FROM clep.result_cache WHERE organization_id = %s AND cache_key = %s",
            (self._org, key)).fetchone()
        if row is None:
            return None
        return CachedResult(output_text=row[0], prompt_tokens=row[1],
                            completion_tokens=row[2])

    def put(self, key: str, *, model_configuration_id: str, output_text: str,
            prompt_tokens: int, completion_tokens: int) -> bool:
        """Returns whether the row was stored.

        `ON CONFLICT DO NOTHING` rather than an upsert: overwriting a cached
        result would let the same key answer differently over time, which is the
        property the cache exists to rule out. A concurrent writer that got there
        first wrote the same value, because the key covers everything that could
        make it different.
        """
        digest = "sha256:" + hashlib.sha256(output_text.encode("utf-8")).hexdigest()
        result = self._conn.execute(
            "INSERT INTO clep.result_cache (id, organization_id, cache_key, "
            "model_configuration_id, output_text, output_digest, prompt_tokens, "
            "completion_tokens) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (organization_id, cache_key) DO NOTHING",
            (uuid.uuid4(), self._org, key,
             ulid_to_uuid(model_configuration_id), output_text, digest,
             prompt_tokens, completion_tokens))
        return result.rowcount == 1
