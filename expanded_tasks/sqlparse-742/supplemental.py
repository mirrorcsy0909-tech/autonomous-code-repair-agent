"""Harness-authored opt-in delimiter, literal, default and empty-input checks."""
import pytest
import sqlparse


@pytest.mark.parametrize('literal', ["'x;y'", "'a  b'", "'BEGIN;END'"])
@pytest.mark.parametrize('tail', ['', ' ', '\n'])
def test_only_statement_delimiters_are_removed(literal, tail):
    query = f'SELECT {literal}; SELECT 42;{tail}'
    assert sqlparse.split(query, strip_semicolon=True) == [f'SELECT {literal}', 'SELECT 42']
    assert sqlparse.split(query) == [f'SELECT {literal};', 'SELECT 42;']


@pytest.mark.parametrize('strip', [True, False])
@pytest.mark.parametrize('text', ['', ' ', '\n\n'])
def test_empty_inputs_return_no_statements(strip, text):
    assert sqlparse.split(text, strip_semicolon=strip) == []


@pytest.mark.parametrize('strip', [True, False])
def test_input_without_delimiter_keeps_its_text(strip):
    assert sqlparse.split('SELECT 42', strip_semicolon=strip) == ['SELECT 42']


def test_procedure_inner_delimiters_are_preserved():
    query = 'CREATE PROCEDURE answer() BEGIN SELECT 1; SELECT 2; END;'
    assert sqlparse.split(query, strip_semicolon=True) == [query[:-1]]
    assert sqlparse.split(query, strip_semicolon=False) == [query]
