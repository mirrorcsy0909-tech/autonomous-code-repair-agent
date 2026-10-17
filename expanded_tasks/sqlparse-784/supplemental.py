"""Harness-authored sequential CASE/block and following-statement checks."""
import pytest
import sqlparse


@pytest.mark.parametrize('count', [1, 2, 3, 4])
@pytest.mark.parametrize('uppercase', [True, False])
@pytest.mark.parametrize('following', [False, True])
def test_sequential_case_does_not_close_outer_block(count, uppercase, following):
    updates = '\n'.join(f'UPDATE t SET c{i} = (CASE WHEN (v={i}) THEN {i+1} END);'
                        for i in range(count))
    trigger = f'CREATE TRIGGER my_trigger AFTER UPDATE ON t BEGIN\n{updates}\nEND;'
    if not uppercase:
        trigger = trigger.lower()
    suffix = '\nSELECT 42;' if following else ''
    statements = sqlparse.split(trigger + suffix)
    assert statements == [trigger] + (['SELECT 42;'] if following else [])


@pytest.mark.parametrize('literal', ["'CASE;END'", "'x;y'"])
def test_literal_semicolons_do_not_split(literal):
    query = f'SELECT {literal}; SELECT 42;'
    assert sqlparse.split(query) == [f'SELECT {literal};', 'SELECT 42;']
