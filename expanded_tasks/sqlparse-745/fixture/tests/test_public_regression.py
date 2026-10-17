"""Regression functions copied verbatim from the upstream fix commit."""
import pytest
import sqlparse
from sqlparse import sql, tokens as T

def test_grouping_nested_identifier_with_order():
    # issue745
    p = sqlparse.parse('(a desc)')[0]
    assert isinstance(p.tokens[0], sql.Parenthesis)
    assert isinstance(p.tokens[0].tokens[1], sql.Identifier)
    assert str(p.tokens[0].tokens[1]) == 'a desc'

