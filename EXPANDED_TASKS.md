# Historical sqlparse tasks

The `sqlparse` catalog adds five historical tasks to the three ItsDangerous and two python-dotenv tasks. The complete catalog contains ten tasks across three repositories: nine bug fixes and one feature/compatibility task. OpenHands SDK and tools remain pinned to `1.53.0`.

## Task scope

| Task | Behavior | Baseline failed / passed | Reference public passes | Supplemental tests | Independent verifier passes | Reference source files changed |
| --- | --- | --- | --- | --- | --- | --- |
| sqlparse-701 | Token grouping for window-function `OVER` clauses | 1 / 433 | 434 | 19 | 453 | 2 |
| sqlparse-745 | Grouping nested `ASC` / `DESC` ordering tokens | 1 / 418 | 419 | 20 | 439 | 1 |
| sqlparse-782 | Whitespace cleanup at group boundaries | 1 / 436 | 437 | 15 | 452 | 1 |
| sqlparse-784 | Splitting consecutive `CASE` expressions inside `BEGIN` | 1 / 435 | 436 | 18 | 454 | 1 |
| sqlparse-742 | Optional removal of trailing statement semicolons | 6 / 411 | 417 | 18 | 435 | 4 |

Task 742 is a feature/compatibility change; the other four are bug fixes. The source-file counts describe upstream reference patches, not a required implementation or file count for model submissions. The variety of issue types and two multi-file reference fixes do not establish that these tasks are harder for a model.

## Validate without a model

Run from the project root:

```bash
uv run python project.py check
uv run python project.py build --catalog sqlparse
uv run python project.py smoke --catalog sqlparse --id expanded-smoke-001
```

Run these commands in order and stop on failure. The image is `code-repair-python:expanded-v1`; its build context contains only the Dockerfile. The recipe uses Python 3.13 with pinned pytest/freezegun/click/mock versions. None of these commands calls a model.

Smoke checks all five failing baselines, rejects unchanged candidates, replays trusted upstream reference patches in fresh containers, runs selected public and external supplemental tests, and checks container cleanup. Expected completion is worker exit code `0`, `cleanup=confirmed` and `SMOKE PASSED`. Use a fresh ID for another attempt. Detailed output remains in `runs/execution-control/<id>/smoke/worker.log`.

The original `expanded_agent.py build`, `smoke`, `compare`, `report` and `export` commands remain supported. The expanded data directory is passed to prepare/run workers; the other catalogs keep their own task directories.

## Test selection and verification

Historical upstream source, tests and data retain their original bytes, with documented source-path relocation. Public regression functions are extracted from their corresponding upstream fix commits into separate modules. Each task records attribution and SHA-256 hashes in `task.json`, `upstream-sha256.json` and fixture manifests. Non-English samples in these tests are intentional encoding coverage.

The fixture pytest configuration adds the `src/` import path, collects `tests/`, excludes `test_cli.py` because it requires external console-script installation, and uses `-m "not xfail"` to deselect exactly three upstream expected-failure tests. The exact nodes appear in each task's `known_excluded_tests`. These are selected suites, not the complete upstream test suite.

The verifier requires the declared pass count, zero failures/errors/skips and exactly three deselections. An extra deselected test or incomplete run fails verification. CLI tests are not collected and do not contribute to the pass count. Earlier task catalogs retain their own declared contracts.

Reference patches and supplemental tests remain outside the Agent's container. Candidate edits must stay within the source allowlist; final patches are replayed into a separate clean container for verification.

The existing 30,000-character per-file read/write limit and snapshot-size limits still apply. Some historical `keywords.py` files exceed this limit and are excluded from editable context; none of the selected reference fixes needs that module. The package remains importable inside the container. One-shot receives editable source and selected public context; Agent retrieves public files with tools. Their context and total compute differ.

## Live comparison

After smoke succeeds, a fresh comparison schedules five tasks with both workflows, for ten attempts:

```bash
uv run python project.py compare --catalog sqlparse --prefix sqlparse-new-001
uv run python project.py report --catalog sqlparse --prefix sqlparse-new-001
uv run python project.py export --catalog sqlparse --prefix sqlparse-new-001
```

Keep the model and authentication settings in local `.env`. Defaults are 300 seconds per run, a 60-second SDK request/stream idle timeout, two SDK attempts including the initial request, and 20 Agent iterations. Subscription mode has no verified service-enforced 8192-token output cap. Heartbeats report the current stage every 15 seconds; raw SDK output stays in local worker logs.

Reports can be inspected from another terminal while a batch runs. Started failures, timeouts and incomplete attempts remain in the denominator; a failed experiment is not automatically rerun. New comparisons need new prefixes. Repeats are not new tasks, and test counts must not be summed into a task count.

## Validation and observed experiments

Initial trusted-source validation used Python 3.13.16 and pytest 9.1.1: all five baselines failed as expected, all five reference fixes passed the configured public/supplemental checks, and targeted negative controls were rejected. See the task validation records and `SOURCE_VALIDATION.json`, which also records a rejected candidate reference fix. Reference validation and negative controls are not Agent successes.

Subsequent Docker checks and live comparisons were completed on the development Mac. `expanded-001` retained two passing one-shot attempts and one passing Agent attempt out of five each, with request/conversation errors on the remaining attempts. The fresh `expanded-002` batch passed all five tasks with both workflows. Both task-701 patches still triggered static review hints. See the [curated reports](examples/reports/README.md) and [evaluation contract](docs/evaluation.md) for timing gaps, usage and limitations. These observations establish no Agent success-rate advantage or general repair rate.

Each task includes its upstream issue and full commit links. Upstream BSD licenses and source headers are retained in the fixtures. See [sqlparse release notes](https://sqlparse.readthedocs.io/en/stable/changes.html) for upstream history.
