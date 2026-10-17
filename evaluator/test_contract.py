"""Supplemental contract tests; copied to a fresh verifier, not the agent repo."""
import pytest
from batching import make_batches


@pytest.mark.parametrize("n", range(13))
@pytest.mark.parametrize("size", [1, 2, 4, 5, 20])
def test_preserves_all_records(n, size):
    records = [{"record_id": i} for i in range(n)]
    batches = make_batches(records, size)
    assert isinstance(batches, list)
    assert [record for batch in batches for record in batch] == records
    assert len(batches) == (n + size - 1) // size
    assert all(isinstance(batch, list) and 0 < len(batch) <= size for batch in batches)
    assert all(len(batch) == size for batch in batches[:-1])


@pytest.mark.parametrize("size", [0, -1, -4])
def test_invalid_size(size):
    with pytest.raises(ValueError):
        make_batches([1, 2], size)


def test_input_unchanged():
    records = [1, 2, 3, 4, 5]
    original = records.copy()
    make_batches(records, 3)
    assert records == original
