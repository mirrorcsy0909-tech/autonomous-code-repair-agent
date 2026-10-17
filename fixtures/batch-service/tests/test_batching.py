import pytest
from batching import make_batches


@pytest.mark.parametrize("items,size,expected", [
    ([], 4, []),
    ([1, 2, 3, 4], 4, [[1, 2, 3, 4]]),
    (list(range(8)), 4, [list(range(4)), list(range(4, 8))]),
    ([1], 4, [[1]]),
    ([1, 2, 3], 4, [[1, 2, 3]]),
    (list(range(7)), 4, [list(range(4)), list(range(4, 7))]),
])
def test_batching(items, size, expected):
    assert make_batches(items, size) == expected
