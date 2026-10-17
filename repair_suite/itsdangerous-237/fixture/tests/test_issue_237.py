"""Public reproductions written for this harness from upstream issue #237."""
from itsdangerous import Serializer, Signer


def test_signer_accepts_none_salt():
    signer = Signer("secret-key", salt=None)
    assert signer.unsign(signer.sign(b"value")) == b"value"


def test_serializer_accepts_none_salt():
    serializer = Serializer("secret-key", salt=None)
    value = [1, 2, 3, 4]
    assert serializer.loads(serializer.dumps(value)) == value
