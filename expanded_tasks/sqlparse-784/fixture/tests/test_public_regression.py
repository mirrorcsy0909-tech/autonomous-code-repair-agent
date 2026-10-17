"""Regression functions copied verbatim from the upstream fix commit."""
import pytest
import sqlparse
from sqlparse import sql, tokens as T

def test_split_multiple_case_in_begin(load_file):  # issue784
    stmts = sqlparse.split(load_file('multiple_case_in_begin.sql'))
    assert len(stmts) == 1

