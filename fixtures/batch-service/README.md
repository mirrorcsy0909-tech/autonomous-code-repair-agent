# Batch Service

`make_batches(items, batch_size)` divides a list of records into ordered batches
for a downstream API. Keep all records exactly once, preserve order, return
`[]` for empty input, and reject a nonpositive batch size with `ValueError`.
Do not mutate the input. A final partial batch is valid.

Reproduce the reported bug: `python -m pytest -q`.
