"""Independent cases authored for this harness, not upstream tests."""
from datetime import datetime, timezone

import pytest
from itsdangerous import Signer, TimedSerializer, TimestampSigner
from itsdangerous.exc import BadTimeSignature, SignatureExpired


@pytest.fixture(params=[0, 1000000, 1600000000, 1900000000])
def clock(request, monkeypatch):
    now = [request.param]
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: now[0])
    return now


@pytest.mark.parametrize("max_age", [None, 10])
@pytest.mark.parametrize("return_timestamp", [False, True])
def test_bad_signature_preserves_exact_signing_datetime(clock, max_age, return_timestamp):
    signer = TimestampSigner("metadata-secret")
    signed = signer.sign(b"original").replace(b"original", b"changed", 1)
    expected = datetime.fromtimestamp(clock[0], timezone.utc)
    clock[0] += 100
    with pytest.raises(BadTimeSignature) as caught:
        signer.unsign(signed, max_age=max_age, return_timestamp=return_timestamp)
    assert type(caught.value) is BadTimeSignature
    assert caught.value.date_signed == expected
    assert isinstance(caught.value.date_signed, datetime)
    assert caught.value.payload == b"changed"


def test_valid_timestamp_still_roundtrips(clock):
    signer = TimestampSigner("metadata-secret")
    signed = signer.sign(b"value")
    assert signer.unsign(signed, return_timestamp=True) == (
        b"value", datetime.fromtimestamp(clock[0], timezone.utc))


def test_expired_metadata_remains_datetime(clock):
    signer = TimestampSigner("metadata-secret")
    signed = signer.sign(b"value")
    signed_at = clock[0]
    clock[0] += 11
    with pytest.raises(SignatureExpired) as caught:
        signer.unsign(signed, max_age=10)
    assert caught.value.date_signed == datetime.fromtimestamp(signed_at, timezone.utc)
    assert caught.value.payload == b"value"


def test_serializer_propagates_bad_signature_metadata(clock):
    serializer = TimedSerializer("metadata-secret")
    signed = serializer.dumps({"value": 1})
    payload, stamp, sig = signed.rsplit(".", 2)
    wrong = "X" if sig[0] != "X" else "Y"
    with pytest.raises(BadTimeSignature) as caught:
        serializer.loads(".".join([payload, stamp, wrong + sig[1:]]))
    assert caught.value.date_signed == datetime.fromtimestamp(clock[0], timezone.utc)


@pytest.mark.parametrize("value", [b"missing", b"value.____________"])
def test_missing_or_malformed_timestamp_has_no_datetime(value):
    signed = Signer("metadata-secret").sign(value)
    with pytest.raises(BadTimeSignature) as caught:
        TimestampSigner("metadata-secret").unsign(signed)
    assert caught.value.date_signed is None
