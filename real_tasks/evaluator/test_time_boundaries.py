"""Supplemental tests; not part of upstream's original test suite.

Only the independent verifier receives these tests, after patch generation.
"""
from datetime import datetime, timezone

import pytest

from itsdangerous import TimestampSigner, TimedSerializer
from itsdangerous.exc import BadSignature, SignatureExpired


@pytest.fixture
def clock(monkeypatch):
    now = [1600000000]
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now[0])
    return now


@pytest.mark.parametrize("kind", ["signer", "serializer"])
@pytest.mark.parametrize("age", [-3600, -10, -1, 0, 1, 10, 11, 3600])
@pytest.mark.parametrize("max_age", [None, 0, 10])
def test_age_boundaries(clock, kind, age, max_age):
    obj = TimestampSigner("boundary-secret") if kind == "signer" else TimedSerializer("boundary-secret")
    expected = b"value" if kind == "signer" else {"value": 42}
    signed = obj.sign(expected) if kind == "signer" else obj.dumps(expected)
    signed_at = clock[0]
    clock[0] += age
    decode = obj.unsign if kind == "signer" else obj.loads
    should_reject = max_age is not None and (age < 0 or age > max_age)
    if should_reject:
        with pytest.raises(SignatureExpired) as caught:
            decode(signed, max_age=max_age)
        assert caught.value.date_signed == datetime.fromtimestamp(signed_at, timezone.utc)
        payload = caught.value.payload
        assert (payload if kind == "signer" else obj.load_payload(payload)) == expected
    else:
        assert decode(signed, max_age=max_age) == expected


@pytest.mark.parametrize("future_seconds", [1, 10, 3600])
def test_validate_future(clock, future_seconds):
    signer = TimestampSigner("boundary-secret")
    signed = signer.sign(b"value")
    clock[0] -= future_seconds
    assert signer.validate(signed, max_age=10) is False
    assert signer.validate(signed) is True


@pytest.mark.parametrize("age", [-1, 0])
def test_tampered_signature_still_rejected(clock, age):
    signer = TimestampSigner("boundary-secret")
    signed = signer.sign(b"value")
    clock[0] += age
    with pytest.raises(BadSignature):
        signer.unsign(b"other" + signed[len(b"value"):], max_age=10)
