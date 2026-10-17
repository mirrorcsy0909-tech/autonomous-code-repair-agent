"""Independent salt compatibility cases authored for this harness."""
import pytest
from itsdangerous import Serializer, Signer, TimedSerializer, TimestampSigner
from itsdangerous import URLSafeSerializer, URLSafeTimedSerializer

SERIALIZERS = [Serializer, TimedSerializer, URLSafeSerializer, URLSafeTimedSerializer]


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: 1600000000)


@pytest.mark.parametrize("cls", [Signer, TimestampSigner])
@pytest.mark.parametrize("value", [b"", b"value", b"a.b.c", "你好"])
def test_none_uses_signer_default(cls, value):
    implicit = cls("salt-secret", salt=None)
    explicit = cls("salt-secret", salt=b"itsdangerous.Signer")
    signed = implicit.sign(value)
    assert signed == explicit.sign(value)
    assert implicit.unsign(signed) == (value.encode() if isinstance(value, str) else value)


@pytest.mark.parametrize("cls", SERIALIZERS)
@pytest.mark.parametrize("value", [None, [1, 2, 3, 4], {"x": "你好"}, "a.b.c"])
def test_serializer_none_preserves_legacy_signatures(cls, value):
    implicit = cls("salt-secret", salt=None)
    explicit = cls("salt-secret", salt=b"itsdangerous.Signer")
    signed = implicit.dumps(value)
    assert signed == explicit.dumps(value)
    assert implicit.loads(signed) == value
    assert explicit.loads(signed) == value


@pytest.mark.parametrize("cls", SERIALIZERS)
@pytest.mark.parametrize("salt", ["", b"", "namespace", b"namespace"])
def test_explicit_salts_are_preserved(cls, salt):
    obj = cls("salt-secret", salt=salt)
    assert obj.make_signer().salt == (salt.encode() if isinstance(salt, str) else salt)
    assert obj.loads(obj.dumps({"x": 1})) == {"x": 1}


@pytest.mark.parametrize("cls", SERIALIZERS)
def test_omitted_serializer_salt_keeps_its_own_default(cls):
    omitted = cls("salt-secret")
    explicit = cls("salt-secret", salt=b"itsdangerous")
    assert omitted.dumps([1, 2]) == explicit.dumps([1, 2])
    assert omitted.make_signer().salt == b"itsdangerous"


@pytest.mark.parametrize("cls", SERIALIZERS)
def test_none_with_key_rotation(cls):
    old = cls("old-key", salt=None)
    rotated = cls(["old-key", "new-key"], salt=None)
    assert rotated.loads(old.dumps({"x": 1})) == {"x": 1}
    assert rotated.dumps({"x": 1}) == cls("new-key", salt=None).dumps({"x": 1})


def test_serializer_passes_none_to_custom_signer():
    class CustomSigner(Signer):
        def __init__(self, secret_key, salt=None, **kwargs):
            super().__init__(secret_key, salt=b"custom" if salt is None else salt, **kwargs)
    obj = Serializer("salt-secret", salt=None, signer=CustomSigner)
    assert obj.make_signer().salt == b"custom"
    assert obj.dumps([1]) == Serializer("salt-secret", salt=b"custom", signer=CustomSigner).dumps([1])
