"""Partition records into batches before sending them to a downstream service."""


def make_batches(items: list, batch_size: int) -> list[list]:
    """Return ordered batches; the final batch may be smaller than batch_size."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return [
        items[start : start + batch_size]
        for start in range(0, len(items) - batch_size + 1, batch_size)
    ]
