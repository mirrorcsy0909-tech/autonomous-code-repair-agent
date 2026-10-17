"""Regression functions copied verbatim from the upstream fix commit."""
import pytest
import sqlparse
from sqlparse import sql, tokens as T

def test_strip_ws_removes_trailing_ws_in_groups():  # issue782
    formatted = sqlparse.format('( where foo = bar  ) from',
                                strip_whitespace=True)
    expected = '(where foo = bar) from'
    assert formatted == expected

