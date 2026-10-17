import logging
import os
import mock
import pytest
import dotenv


@pytest.mark.parametrize(
    "before,key,value,expected,after",
    [
        ("", "a", "", (True, "a", ""), "a=''\n"),
        ("", "a", "b", (True, "a", "b"), "a='b'\n"),
        ("", "a", "'b'", (True, "a", "'b'"), "a='\\'b\\''\n"),
        ("", "a", "\"b\"", (True, "a", '"b"'), "a='\"b\"'\n"),
        ("", "a", "b'c", (True, "a", "b'c"), "a='b\\'c'\n"),
        ("", "a", "b\"c", (True, "a", "b\"c"), "a='b\"c'\n"),
        ("a=b", "a", "c", (True, "a", "c"), "a='c'\n"),
        ("a=b\n", "a", "c", (True, "a", "c"), "a='c'\n"),
        ("a=b\n\n", "a", "c", (True, "a", "c"), "a='c'\n\n"),
        ("a=b\nc=d", "a", "e", (True, "a", "e"), "a='e'\nc=d"),
        ("a=b\nc=d\ne=f", "c", "g", (True, "c", "g"), "a=b\nc='g'\ne=f"),
        ("a=b\n", "c", "d", (True, "c", "d"), "a=b\nc='d'\n"),
        ("a=b", "c", "d", (True, "c", "d"), "a=b\nc='d'\n"),
    ],
)
def test_set_key(dotenv_file, before, key, value, expected, after):
    logger = logging.getLogger("dotenv.main")
    with open(dotenv_file, "w") as f:
        f.write(before)

    with mock.patch.object(logger, "warning") as mock_warning:
        result = dotenv.set_key(dotenv_file, key, value)

    assert result == expected
    assert open(dotenv_file, "r").read() == after
    mock_warning.assert_not_called()
