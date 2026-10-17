"""Harness-authored checks of the historical OVER token-tree contract."""
import pytest
import sqlparse
from sqlparse import sql


def functions_in(node):
    for token in getattr(node, 'tokens', []):
        if isinstance(token, sql.Function):
            yield token
        elif token.is_group:
            yield from functions_in(token)


@pytest.mark.parametrize('window', ['win_a', '(partition by dept)', '(order by created)',
                                   '(partition by dept order by created)'])
@pytest.mark.parametrize('spacing', [' ', '  '])
@pytest.mark.parametrize('keyword', ['over', 'OVER'])
def test_window_belongs_to_function(window, spacing, keyword):
    expression = f'foo(amount){spacing}{keyword}{spacing}{window}'
    query = f'SELECT {expression} AS metric FROM records'
    statement = sqlparse.parse(query)[0]
    functions = list(functions_in(statement))
    assert len(functions) == 1
    function = functions[0]
    assert str(function) == expression
    over = function.token_next_by(i=sql.Over)[1]
    assert over is not None
    assert str(statement) == query
    assert function.parent.get_alias() == 'metric'


@pytest.mark.parametrize('expression', ['foo(x)', 'sum(amount)', 'coalesce(a,b)'])
def test_ordinary_functions_are_preserved(expression):
    query = f'SELECT {expression} AS metric FROM records'
    function = list(functions_in(sqlparse.parse(query)[0]))[0]
    assert str(function) == expression
    assert function.parent.get_alias() == 'metric'
