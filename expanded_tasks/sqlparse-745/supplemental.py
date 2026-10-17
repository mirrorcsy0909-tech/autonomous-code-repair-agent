"""Harness-authored nested ASC/DESC grouping checks."""
import pytest
import sqlparse
from sqlparse import sql


@pytest.mark.parametrize('name', ['amount', 't.amount', '"amount"'])
@pytest.mark.parametrize('ordering', ['asc', 'DESC'])
@pytest.mark.parametrize('depth', [1, 2, 3])
def test_ordering_at_nested_depth(name, ordering, depth):
    expression = f'{name} {ordering}'
    query = '(' * depth + expression + ')' * depth
    node = sqlparse.parse(query)[0]
    for unused in range(depth):
        node = node.token_next_by(i=sql.Parenthesis)[1]
        assert node is not None
    ordered = node.token_next_by(i=sql.Identifier)[1]
    assert ordered is not None
    assert str(ordered) == expression
    assert ordered.get_ordering() == ordering.upper()
    assert str(sqlparse.parse(query)[0]) == query


@pytest.mark.parametrize('ordering', ['ASC', 'DESC'])
def test_top_level_ordering_is_preserved(ordering):
    identifier = sqlparse.parse(f'amount {ordering}')[0].tokens[0]
    assert isinstance(identifier, sql.Identifier)
    assert identifier.get_ordering() == ordering
