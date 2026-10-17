"""Harness-authored boundary whitespace and literal-preservation checks."""
import pytest
import sqlparse


@pytest.mark.parametrize('padding', [' ', '  ', '     '])
@pytest.mark.parametrize('operator', ['=', '<>'])
@pytest.mark.parametrize('value', ['bar', "'a  b'"])
def test_group_boundary_spaces_are_removed(padding, operator, value):
    text = f'({padding}where foo {operator} {value}{padding}) from'
    expected = f'(where foo {operator} {value}) from'
    result = sqlparse.format(text, strip_whitespace=True)
    assert result == expected
    assert sqlparse.format(result, strip_whitespace=True) == result


@pytest.mark.parametrize('literal', ["'a  b'", "'  padded  '", "'x;y'"])
def test_whitespace_inside_literals_remains(literal):
    text = f'SELECT {literal} FROM records'
    assert sqlparse.format(text, strip_whitespace=True) == text
