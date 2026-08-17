"""The log surface, tested adversarially, because that is the only useful way.

A test that logs a tidy message and finds no secret in it establishes nothing.
Every leak worth preventing happens because the sensitive thing arrived somewhere
nobody was looking: inside an exception message, nested in a dict, wrapped in a
list, attached to an object whose `__str__` was helpful, or in a field somebody
declared as harmless.

So the values driven through here are hostile on purpose, and the assertions are
over the **serialised line** — the thing that reaches the store — rather than
over the record dict, because a leak that survives serialisation is the only kind
that matters.

Every vector below is **assembled from parts**, so that no contiguous run of
characters in this file matches a secret pattern while the redactor still
receives the identical complete string. This file is otherwise guaranteed to
contain credential-shaped text, and a scanner that excused files whose names look
like tests would be a scanner with a way around it — the same conclusion Phase 12
reached about `test_privacy.py`.

Assembly introduces its own risk and it is closed rather than accepted: a vector
split wrongly stops matching the pattern it stands for, the redactor legitimately
leaves it alone, and the test passes because the string was never removable.
`test_every_assembled_vector_still_matches_the_detector` fails loudly on that.
"""
from __future__ import annotations

import json

import pytest

from clep.security.privacy import CLASSES, CREDENTIAL_SHAPES
from clep.telemetry import (Classified, ContentCapture, ListSink,
                            StructuredLogger, correlated)

# Shapes that match CREDENTIAL_SHAPES. Fictional, assembled, never real values.
FAKE_PROVIDER_KEY = "sk-" + "A1b2C3d4E5f6G7h8I9j0"
FAKE_FORGE_TOKEN = "ghp_" + "0Zz9Yy8Xx7Ww6Vv5Uu4T"
FAKE_CLOUD_KEY = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_PASSWORD = "hunter" + "2"
FAKE_URL_CREDENTIAL = "postgres://someone:" + FAKE_PASSWORD + "@db.invalid/clep"
FAKE_PRIVATE_KEY = "-----BEGIN " + "RSA PRIVATE KEY" + "-----"
VECTORS = (FAKE_PROVIDER_KEY, FAKE_FORGE_TOKEN, FAKE_CLOUD_KEY,
           FAKE_PRIVATE_KEY, FAKE_URL_CREDENTIAL)

# DS-5 quotes DS-1 to DS-3 verbatim, which is exactly why it is unloggable.
JUDGE_RATIONALE = ("The answer 'Paris is the capital of France' matches the "
                   "expected passage about French geography.")


def _logger(**kw):
    sink = ListSink()
    return StructuredLogger(sink, **kw), sink


def _assert_clean(sink, *forbidden):
    for secret in forbidden:
        assert secret not in sink.text, f"{secret[:12]}... reached the log"


def test_every_assembled_vector_still_matches_the_detector():
    """The guard on the assembly above.

    A vector split in the wrong place stops matching the pattern it stands for.
    The redactor then legitimately leaves it alone, every redaction test below
    still passes, and the suite has quietly stopped testing anything. This fails
    instead of letting that happen.
    """
    for vector in VECTORS:
        assert any(pattern.search(vector) for pattern, _ in CREDENTIAL_SHAPES), (
            f"{vector[:12]}... no longer matches any credential shape; the "
            f"assembly split it wrongly and the tests below prove nothing")


# ------------------------------------------------------- credentials
@pytest.mark.parametrize("secret", [FAKE_PROVIDER_KEY, FAKE_FORGE_TOKEN,
                                    FAKE_CLOUD_KEY, FAKE_PRIVATE_KEY])
def test_a_credential_in_a_plain_field_never_reaches_the_line(secret):
    log, sink = _logger()
    log.info("provider.configured", detail=f"using {secret} for the endpoint")
    _assert_clean(sink, secret)
    assert "[redacted:" in sink.text


def test_a_credential_inside_an_exception_message_is_scrubbed():
    """The realistic case. Nobody logs a key on purpose; a database driver
    raises "could not connect to" followed by the whole DSN, password included,
    and somebody logs the error object during an incident."""
    log, sink = _logger()
    try:
        raise ConnectionError(f"could not connect to {FAKE_URL_CREDENTIAL}")
    except ConnectionError as exc:
        log.error("db.unreachable", error=exc)
    _assert_clean(sink, FAKE_PASSWORD)
    assert "[redacted: credential in url]" in sink.text


def test_a_credential_nested_in_a_dict_and_a_list_is_scrubbed():
    log, sink = _logger()
    log.warning("retry.exhausted",
                context={"attempts": [{"header": f"Bearer {FAKE_PROVIDER_KEY}"}],
                         "endpoint": {"dsn": FAKE_URL_CREDENTIAL}})
    _assert_clean(sink, FAKE_PROVIDER_KEY, "hunter2")


def test_a_credential_in_the_event_name_itself_is_scrubbed():
    log, sink = _logger()
    log.info(f"auth.failed key={FAKE_PROVIDER_KEY}")
    _assert_clean(sink, FAKE_PROVIDER_KEY)


def test_a_credential_on_an_object_with_a_helpful_repr_is_scrubbed():
    class _Endpoint:
        def __str__(self):
            return f"Endpoint(api_key={FAKE_PROVIDER_KEY})"

    log, sink = _logger()
    log.info("gateway.ready", endpoint=_Endpoint())
    _assert_clean(sink, FAKE_PROVIDER_KEY)


# --------------------------------------------------- content classes
@pytest.mark.parametrize("code", [c for c, d in CLASSES.items() if not d.log])
def test_no_content_class_the_taxonomy_forbids_survives_the_log_surface(code):
    """DS-1 to DS-7 all carry log=False. Parameterised over the taxonomy rather
    than over a list here, so a class added later is covered without anybody
    remembering to extend this test."""
    log, sink = _logger()
    log.info("sample.scored",
             content=Classified("SENTINEL-CONTENT-VALUE", code))
    assert "SENTINEL-CONTENT-VALUE" not in sink.text
    assert f"[withheld: {code}" in sink.text


def test_the_judge_rationale_that_quotes_the_dataset_does_not_reach_the_log():
    """The specific scenario observability-strategy.md §4 names: debugging a
    scoring anomaly by logging the rationale, which quotes DS-1 to DS-3."""
    log, sink = _logger()
    log.debug("judge.disagreed", rationale=Classified(JUDGE_RATIONALE, "DS-5"))
    assert "Paris is the capital of France" not in sink.text
    assert "[withheld: DS-5 judge rationale]" in sink.text


@pytest.mark.parametrize("code", [c for c, d in CLASSES.items() if d.log])
def test_the_two_classes_that_are_loggable_still_reach_the_log(code):
    """DS-8 and DS-9. A redactor that withheld everything would pass every test
    above and make the log surface useless, so the permitted classes are
    asserted to survive."""
    log, sink = _logger()
    log.info("cost.recorded", detail=Classified("0.0034 USD", code))
    assert "0.0034 USD" in sink.text


# ----------------------------------------------- debug content capture
def test_capture_is_absent_by_default_and_content_stays_withheld():
    log, sink = _logger()
    log.debug("judge.disagreed", rationale=Classified(JUDGE_RATIONALE, "DS-5"))
    assert JUDGE_RATIONALE not in sink.text


def test_capture_must_name_an_actor_and_a_justification():
    for actor, justification in (("", "incident 12"), ("ops", "  ")):
        with pytest.raises(ValueError):
            ContentCapture(actor=actor, justification=justification,
                           ttl_seconds=60, data_classes=["DS-5"],
                           audit=lambda **kw: None)


def test_capture_must_be_time_bounded():
    for ttl in (0, -1, 86_401):
        with pytest.raises(ValueError):
            ContentCapture(actor="ops", justification="incident 12",
                           ttl_seconds=ttl, data_classes=["DS-5"],
                           audit=lambda **kw: None)


def test_enabling_capture_is_audited_before_any_content_is_captured():
    recorded = []
    capture = ContentCapture(actor="ops", justification="incident 12",
                             ttl_seconds=60, data_classes=["DS-5"],
                             audit=lambda **kw: recorded.append(kw))
    assert len(recorded) == 1
    assert recorded[0]["actor"] == "ops"
    assert recorded[0]["justification"] == "incident 12"
    assert recorded[0]["data_classes"] == ["DS-5"]
    assert capture.covers("DS-5")


def test_a_capture_whose_audit_write_fails_does_not_come_into_being():
    def _refuses(**kw):
        raise RuntimeError("audit store unavailable")

    with pytest.raises(RuntimeError):
        ContentCapture(actor="ops", justification="incident 12", ttl_seconds=60,
                       data_classes=["DS-5"], audit=_refuses)


def test_capture_covers_only_the_classes_it_named():
    capture = ContentCapture(actor="ops", justification="incident 12",
                             ttl_seconds=60, data_classes=["DS-5"],
                             audit=lambda **kw: None)
    log, sink = _logger(capture=capture)
    log.debug("judge.disagreed", rationale=Classified(JUDGE_RATIONALE, "DS-5"),
              prompt=Classified("SECRET-PROMPT-TEXT", "DS-6"))
    assert "Paris is the capital of France" in sink.text  # named
    assert "SECRET-PROMPT-TEXT" not in sink.text          # not named


def test_capture_goes_inert_when_its_bound_elapses():
    """Nothing has to remember to turn it off, which is the failure mode: the
    person who enabled it during an incident is asleep by the time it matters."""
    now = [1000.0]
    capture = ContentCapture(actor="ops", justification="incident 12",
                             ttl_seconds=30, data_classes=["DS-5"],
                             audit=lambda **kw: None, clock=lambda: now[0])
    log, sink = _logger(capture=capture)
    log.debug("before", rationale=Classified(JUDGE_RATIONALE, "DS-5"))
    assert "Paris is the capital of France" in sink.text

    now[0] += 31
    assert capture.expired
    log2, sink2 = _logger(capture=capture)
    log2.debug("after", rationale=Classified(JUDGE_RATIONALE, "DS-5"))
    assert "Paris is the capital of France" not in sink2.text
    assert "[withheld: DS-5" in sink2.text


def test_capture_does_not_relax_credential_redaction():
    """REQ-N-SEC-5 is absolute. Authorising content capture authorises content,
    never a credential that happened to be sitting inside it."""
    capture = ContentCapture(actor="ops", justification="incident 12",
                             ttl_seconds=60, data_classes=["DS-2"],
                             audit=lambda **kw: None)
    log, sink = _logger(capture=capture)
    log.debug("candidate.output",
              output=Classified(f"the model replied with {FAKE_PROVIDER_KEY}",
                                "DS-2"))
    assert "the model replied with" in sink.text
    _assert_clean(sink, FAKE_PROVIDER_KEY)


# --------------------------------------------------------- structure
def test_a_field_containing_a_newline_cannot_forge_a_second_log_line():
    log, sink = _logger()
    log.info("auth.failed",
             subject='x"}\n{"level":"info","event":"auth.succeeded')
    assert len(sink.lines) == 1
    assert json.loads(sink.lines[0])["event"] == "auth.failed"


def test_every_record_carries_the_correlation_in_scope():
    log, sink = _logger()
    with correlated() as c:
        log.info("run.started")
    record = json.loads(sink.lines[0])
    assert record["correlationId"] == c.correlation_id


def test_a_record_outside_a_scope_says_so_rather_than_inventing_one():
    log, sink = _logger()
    log.info("worker.idle")
    assert json.loads(sink.lines[0])["correlationId"] is None


def test_an_undeclared_content_class_is_refused_at_construction():
    with pytest.raises(Exception):
        Classified("x", "DS-99")


def test_an_unknown_level_is_refused():
    log, _ = _logger()
    with pytest.raises(ValueError):
        log.log("trace", "run.started")
