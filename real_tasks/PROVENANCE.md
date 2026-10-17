# Historical task provenance

- Repository: https://github.com/pallets/itsdangerous
- Historical issue: https://github.com/pallets/itsdangerous/issues/126
- Buggy source snapshot: `1a9b8d1f9968eb248bda69f39d373a6330b692ba`
- Upstream reference fix: `c30678d19e37011890e2374cca04f7789e101793`
- Upstream PR: https://github.com/pallets/itsdangerous/pull/133
- Merge commit: `542fad1` (first parent is the selected buggy snapshot).
- License: BSD-3-Clause; the original `LICENSE.rst` is preserved in the fixture.

The fixture contains `src/`, `tests/`, `setup.cfg`, `setup.py`, `README.rst`, and
`LICENSE.rst` from the buggy snapshot. All 9 source modules are byte-for-byte
identical to that snapshot. No source fix is applied to the model workspace.

The sole changed upstream test file is `tests/test_itsdangerous/test_timed.py`,
which is byte-for-byte identical to the reference commit's test file. It adds
`TestTimestampSigner.test_future_age`. The full public test collection is 415
tests: 414 pass and this added regression fails before repair. Other test files
are identical to the buggy snapshot.

A harness-only `pytest.ini` is added with `pythonpath=src`, `testpaths=tests`,
and `filterwarnings=error` (the last two preserve the upstream pytest settings).
This imports the historical package directly without installing it at task
runtime. The source and test semantics are otherwise unchanged.

`fixture-sha256.json` records SHA-256 checksums for all fixture files. Byte-level
comparison against Git objects was performed during package preparation.

`evaluator/upstream-source.patch` is the upstream source-only diff between
the two full commits above. Only the deterministic smoke check uses it.
`evaluator/test_time_boundaries.py` is a new 53-case supplement: signer and
serializer behavior for future, current, boundary-age and expired signatures;
omitted/zero/positive max_age; exception payload/date; validate; and tampering.
It is not an upstream-authored test and is only seeded into the independent
verification container after the Agent generates a patch.

`task.json`, the reference patch, supplemental tests, this document, and
checksums remain on the host. They are not seeded into the Agent container.
The Agent prompt contains a paraphrased symptom and API behavior requirements,
not an issue URL, reference PR, source diff, or suggested implementation.

This is a regression-test-assisted replay of a small, public historical bug.
It is not a blind issue-only benchmark, a new vulnerability, an upstream code
contribution, or evidence that the model had never seen the fix during training.
The task count is one; success-rate claims require a larger evaluated task set.

The harness remains the project's constrained repository tool, with no shell,
network, or package installation during Agent execution. Model and OAuth
credentials remain on the host. The image uses Python 3.13-slim and pins
pytest 9.1.1 / freezegun 1.5.5; each run stores its resolved Docker image ID.
