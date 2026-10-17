import logging
import os
import mock
import pytest
import dotenv
from dotenv.compat import StringIO


@pytest.mark.parametrize(
    "env,string,interpolate,expected",
    [
        # Defined in environment, with and without interpolation
        ({"b": "c"}, "a=$b", False, {"a": "$b"}),
        ({"b": "c"}, "a=$b", True, {"a": "$b"}),
        ({"b": "c"}, "a=${b}", False, {"a": "${b}"}),
        ({"b": "c"}, "a=${b}", True, {"a": "c"}),
        ({"b": "c"}, "a=${b:-d}", False, {"a": "${b:-d}"}),
        ({"b": "c"}, "a=${b:-d}", True, {"a": "c"}),

        # Defined in file
        ({}, "b=c\na=${b}", True, {"a": "c", "b": "c"}),

        # Undefined
        ({}, "a=${b}", True, {"a": ""}),
        ({}, "a=${b:-d}", True, {"a": "d"}),

        # With quotes
        ({"b": "c"}, 'a="${b}"', True, {"a": "c"}),
        ({"b": "c"}, "a='${b}'", True, {"a": "c"}),

        # With surrounding text
        ({"b": "c"}, "a=x${b}y", True, {"a": "xcy"}),

        # Self-referential
        ({"a": "b"}, "a=${a}", True, {"a": "b"}),
        ({}, "a=${a}", True, {"a": ""}),
        ({"a": "b"}, "a=${a:-c}", True, {"a": "b"}),
        ({}, "a=${a:-c}", True, {"a": "c"}),

        # Reused
        ({"b": "c"}, "a=${b}${b}", True, {"a": "cc"}),

        # Re-defined and used in file
        ({"b": "c"}, "b=d\na=${b}", True, {"a": "d", "b": "d"}),
    ],
)
def test_dotenv_values_stream(env, string, interpolate, expected):
    with mock.patch.dict(os.environ, env, clear=True):
        stream = StringIO(string)
        stream.seek(0)

        result = dotenv.dotenv_values(stream=stream, interpolate=interpolate)

        assert result == expected
