"""Regression functions copied verbatim from the upstream fix commit."""
import pytest
import sqlparse
from sqlparse import sql, tokens as T

def test_grouping_identifier_function():
    p = sqlparse.parse('foo() as bar')[0]
    assert isinstance(p.tokens[0], sql.Identifier)
    assert isinstance(p.tokens[0].tokens[0], sql.Function)
    p = sqlparse.parse('foo()||col2 bar')[0]
    assert isinstance(p.tokens[0], sql.Identifier)
    assert isinstance(p.tokens[0].tokens[0], sql.Operation)
    assert isinstance(p.tokens[0].tokens[0].tokens[0], sql.Function)
    p = sqlparse.parse('foo(c1) over win1 as bar')[0]
    assert isinstance(p.tokens[0], sql.Identifier)
    assert isinstance(p.tokens[0].tokens[0], sql.Function)
    assert len(p.tokens[0].tokens[0].tokens) == 4
    assert isinstance(p.tokens[0].tokens[0].tokens[3], sql.Over)
    assert isinstance(p.tokens[0].tokens[0].tokens[3].tokens[2],
                      sql.Identifier)
    p = sqlparse.parse('foo(c1) over (partition by c2 order by c3) as bar')[0]
    assert isinstance(p.tokens[0], sql.Identifier)
    assert isinstance(p.tokens[0].tokens[0], sql.Function)
    assert len(p.tokens[0].tokens[0].tokens) == 4
    assert isinstance(p.tokens[0].tokens[0].tokens[3], sql.Over)
    assert isinstance(p.tokens[0].tokens[0].tokens[3].tokens[2],
                      sql.Parenthesis)

