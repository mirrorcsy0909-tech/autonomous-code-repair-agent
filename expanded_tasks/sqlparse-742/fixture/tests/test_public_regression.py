"""Regression functions copied verbatim from the upstream fix commit."""
import pytest
import sqlparse
from sqlparse import sql, tokens as T

@pytest.mark.parametrize('sql, expected', [
    ('select * from foo;', ['select * from foo']),
    ('select * from foo', ['select * from foo']),
    ('select * from foo; select * from bar;', [
        'select * from foo',
        'select * from bar',
    ]),
    ('  select * from foo;\n\nselect * from bar;\n\n\n\n', [
        'select * from foo',
        'select * from bar',
    ]),
    ('select * from foo\n\n;  bar', ['select * from foo', 'bar']),
])
def test_split_strip_semicolon(sql, expected):
    stmts = sqlparse.split(sql, strip_semicolon=True)
    assert len(stmts) == len(expected)
    for idx, expectation in enumerate(expected):
        assert stmts[idx] == expectation


def test_split_strip_semicolon_procedure(load_file):
    stmts = sqlparse.split(load_file('mysql_handler.sql'),
                           strip_semicolon=True)
    assert len(stmts) == 2
    assert stmts[0].endswith('end')
    assert stmts[1].endswith('end')

