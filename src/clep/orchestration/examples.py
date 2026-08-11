"""Loading the examples a run evaluates, for a caller that has none to give.

Every run before Phase 11 was started by someone who already held the examples
and passed them in. A schedule has no such caller: `REQ-F-10-1` requires the run
to happen without human initiation, so something has to read the dataset.

The split here follows ADR-005 exactly, because the store does.

  * The **record** — which examples exist, in what order, under which dataset
    version, and the digest of each one's content — is in PostgreSQL, and this
    module reads it directly.
  * The **payload** is not. `example_content` holds a `payload_ref` and a digest;
    the bytes live in the object store (ADR-013 O-1), whose adapter belongs to
    the deployment phase. So the payload arrives through a port.

Two refusals are deliberate, and both exist because the alternative is a run that
looks like it measured something.

**No default reader.** A source constructed without one raises rather than
returning examples with empty prompts. An evaluation of the empty string scores
perfectly consistently and means nothing.

**The digest is checked.** A payload whose content does not hash to the digest
the dataset version recorded is refused, not used. `REQ-F-07-1` freezes run
identity over that digest, so a run that evaluated different bytes under the same
identity would be irreproducible in the one way nobody thinks to check.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from clep.identity import uuid_to_ulid
from clep.orchestration.runner import Example

#: The payload shape `dataset_version.schema_ref` names in the seeded form:
#: one JSON object per example, with the prompt and the expected answer.
EXAMPLE_SCHEMA_REF = "schema://example/v1"


class ExampleUnavailable(RuntimeError):
    """An example that cannot be loaded is never quietly skipped.

    A run that evaluated four of five examples because the fifth would not load
    reports a smaller sample size with no reason attached, and the reader has no
    way to tell that from a dataset with four examples in it.
    """


def digest_of(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def file_payload_reader(payload_ref: str) -> bytes:
    """Read a payload from the local filesystem.

    A `file://` reference is what a development deployment has while the object
    store is not stood up; the S3-compatible adapter ADR-013 selected arrives
    with the deployment phase. Anything else is refused rather than guessed at,
    so a misconfigured deployment fails at the first scheduled run instead of
    evaluating nothing successfully.
    """
    parsed = urlparse(payload_ref)
    if parsed.scheme != "file":
        raise ExampleUnavailable(
            f"{payload_ref!r} is not a file reference; this reader serves local "
            f"development and the object-store adapter (ADR-013) is not this "
            f"phase's work")
    # `url2pathname` rather than stripping the leading slash by hand: the two
    # differ on every platform, and getting it wrong turns an absolute POSIX
    # path into a relative one that resolves against the worker's directory.
    path = Path(url2pathname(unquote(parsed.path)))
    try:
        return path.read_bytes()
    except OSError as e:
        raise ExampleUnavailable(f"{payload_ref}: {e}") from e


@dataclass(frozen=True)
class StoredExampleSource:
    """Examples as the dataset version records them, in ordinal order."""

    payload_reader: object = None

    def load(self, conn, organization_id: str, dataset_version_id: str
             ) -> list[Example]:
        if self.payload_reader is None:
            raise ExampleUnavailable(
                "no payload reader is configured, so the dataset's content "
                "cannot be read; refusing rather than evaluating empty prompts")
        from clep.identity import ulid_to_uuid
        rows = conn.execute(
            "SELECT e.id, e.ordinal, c.content_digest, c.payload_ref, c.erased_at "
            "FROM clep.example e "
            "LEFT JOIN clep.example_content c "
            "  ON c.organization_id = e.organization_id AND c.example_id = e.id "
            "WHERE e.organization_id = %s AND e.dataset_version_id = %s "
            "ORDER BY e.ordinal, e.id",
            (str(organization_id), ulid_to_uuid(dataset_version_id))).fetchall()
        if not rows:
            raise ExampleUnavailable(
                f"dataset version {dataset_version_id} has no examples; a run "
                f"over nothing would report a complete evaluation of zero "
                f"samples")
        out = []
        for example_id, _ordinal, content_digest, payload_ref, erased_at in rows:
            reference = uuid_to_ulid(example_id)
            if erased_at is not None or payload_ref is None:
                # ADR-005: erasure nulls the payload and the record survives. A
                # run cannot evaluate it, and saying so is the requirement.
                raise ExampleUnavailable(
                    f"example {reference} has no payload; its content was "
                    f"erased, and a scheduled run must not silently evaluate "
                    f"the remainder as though the dataset were whole")
            payload = self.payload_reader(payload_ref)
            actual = digest_of(payload)
            if actual != content_digest:
                raise ExampleUnavailable(
                    f"example {reference} content digest {actual} does not "
                    f"match the {content_digest} the dataset version recorded; "
                    f"the run identity is frozen over that digest")
            body = _decode(reference, payload)
            out.append(Example(id=reference, prompt=body["prompt"],
                               expected=body.get("expected"),
                               content_digest=content_digest))
        return out


def _decode(reference: str, payload: bytes) -> dict:
    try:
        body = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise ExampleUnavailable(f"example {reference}: {e}") from e
    if not isinstance(body, dict) or not str(body.get("prompt", "")).strip():
        raise ExampleUnavailable(
            f"example {reference} carries no prompt; {EXAMPLE_SCHEMA_REF} "
            f"requires one, and an empty prompt scores consistently and means "
            f"nothing")
    return body
