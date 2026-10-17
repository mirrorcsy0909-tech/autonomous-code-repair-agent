# Three-task historical pilot provenance

All tasks come from https://github.com/pallets/itsdangerous and retain the upstream
BSD-3-Clause license in each fixture's `LICENSE.rst`.

| Task | Buggy source commit | Reference commit | Public tests | Supplemental | Total |
| --- | --- | --- | ---: | ---: | ---: |
| #126 | 1a9b8d1f9968eb248bda69f39d373a6330b692ba | c30678d19e37011890e2374cca04f7789e101793 | 415 | 53 | 468 |
| #124 | b11475aa0e1534f1fe7736d56140d81e872b1f5f | 526b1ea02934f529606dcbf3c3340ab429dcde04 | 416 | 30 | 446 |
| #237 | 15a2e0d9b4bdaa38144c947a9aa04ffd49826700 | 41ec419632aae3533ff12838044eeddd9cd1311f | 423 | 49 | 472 |

Issues:

- https://github.com/pallets/itsdangerous/issues/126
- https://github.com/pallets/itsdangerous/issues/124
- https://github.com/pallets/itsdangerous/issues/237

Every source file in `fixture/src` was compared byte-for-byte with the recorded
buggy Git commit. Source files are not deliberately mutated to create tasks.
Each snapshot includes src, tests, setup.cfg, setup.py, README.rst, and LICENSE.rst.
`pytest.ini` is a harness addition that imports src directly and preserves
testpaths=tests and filterwarnings=error, avoiding installation during execution.
Per-task SHA-256 inventories record every fixture file.

For #126 and #124, the public timed test file is taken from the reference commit;
all other upstream tests remain byte-identical to the buggy commit. The test-only
diff introduces the regression, not the source fix. #124 adds checks for missing,
malformed, future, and bad-signature datetime metadata; only the bad-signature
regression fails on its selected baseline. The source-only reference diff remains
outside the Agent container.

The #237 reference commit has no upstream test changes. The two public tests in
`tests/test_issue_237.py` were written for this harness from the reported issue:
Signer and Serializer must round-trip with explicit salt=None. They are not
represented as upstream-authored tests.

All supplemental tests are harness-authored. #124 checks exact UTC datetimes
including epoch zero, payloads, serializer propagation, and existing error paths.
#237 checks legacy signatures, omitted/empty/nonempty salts, custom signers,
timed/URL-safe serializers and key rotation. #126 retains the previously used
53-case time-boundary supplement.

Only each task's fixture is seeded into its Agent container. Full metadata,
reference patch, supplementary tests, issue URLs, source history, reports and
this provenance document remain on the host. The prompt includes paraphrased
symptoms and expected API behavior plus source allowlist, without reference code.
Independent verification replays only the allowed source diff onto a clean
fixture and adds the supplement in a separate container.

The reference patch is used only for deterministic smoke checks and local
preparation validation. Source validation was performed on Python 3.13.16 with
pytest 9.1.1 and freezegun 1.5.5. The existing Docker image has those dependencies;
the runner records its immutable resolved image ID. No new Docker/model result
is claimed by source validation. An uploaded real-001 live result was statically
reviewed, with patch/report/test-output consistency checked.

The tasks are small, publicly solved, related issues in one repository. The model
may have seen upstream solutions during training. This is a regression-assisted
engineering pilot, not a blind issue-only benchmark or a multi-repository study.
