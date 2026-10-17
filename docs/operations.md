# Operations

## Commands

| Command | Model calls | Docker required | Purpose |
| --- | --- | --- | --- |
| `project.py list --catalog all` | No | No | Show all ten task IDs |
| `project.py doctor --catalog all --offline` | No | No | Check interpreter, pinned SDK packages, core files and fixture hashes |
| `project.py doctor --catalog sqlparse` | No | Yes | Also check Docker reachability and selected image |
| `project.py check-fixtures --catalog all` | No | No | Verify trusted fixture hashes only |
| `project.py check` | No | No daemon | Run explicit harness tests; some launch trusted test subprocesses |
| `project.py build --catalog sqlparse` | No | Yes | Build the selected image, using network for dependency installation |
| `project.py smoke --catalog sqlparse --id NAME` | No | Yes | Baseline/no-patch/reference verification |
| `project.py compare --catalog sqlparse --prefix NAME` | Yes | Yes | Paired historical-task attempts |
| `project.py batch --catalog itsdangerous --prefix NAME` | Yes | Yes | Agent-only historical pilot |
| `project.py report --catalog sqlparse --prefix NAME` | No | No | Recompute summaries and static quality hints from saved records |
| `project.py export --catalog sqlparse --prefix NAME` | No | No | Export whitelisted review evidence |

Paired commands also accept `--catalog dotenv`. Build/smoke/doctor/list/check-fixtures support `--catalog all`; combined smoke derives unique IDs with catalog suffixes. Use a sufficiently short base ID. Selecting task IDs requires one catalog. `prepare` and `run` require explicit task, workflow and run ID; the original CLI validates that they match the manifest.

## Typical checks after installing an update

```bash
uv run python project.py doctor --catalog all --offline
uv run python project.py check
uv run python project.py smoke --catalog sqlparse --id project-smoke-001
uv run python project.py report --catalog sqlparse --prefix expanded-002
```

No model is called. The last command still reports the existing ten attempts rather than starting new repairs. If the smoke ID already exists, choose a new ID; do not remove run records to reuse it.

## Reading outcomes

`passed` requires independent verification, not the Agent's statement that it fixed the bug. `failed` can reflect execution/request/verification failure. Use failure type, underlying exception chain, stage and worker metadata to distinguish them.

`candidate_status=saved` means a completed source checkpoint exists and its artifact hashes match. It remains unverified. `checkpoint_incomplete`, `save_failed`, `no_patch` and `not_recorded` are distinct states; an old run cannot gain a candidate retroactively.

`patch_quality_status=review_required` suggests manual inspection. `not_available` often means the run never exported a final patch, or static analysis could not confirm the baseline. Neither value changes the recorded test pass count.

`clock_gap_detected` can reflect host suspension or clock adjustment. Keep a live experiment's host awake with stable connectivity. The wall-clock-aware deadline is checked after wake; it cannot terminate anything while the CPU is asleep. Do not interpret program timing alone as full overnight elapsed time.

## Failure evidence

Safe metadata records whitelisted exception/code names, not exception message text or response bodies. Raw `worker.log` stays local and is excluded from review exports because it can include tool observations or sensitive transport text. Do not make raw worker logs part of a public example.

For repeated DNS/JWKS failures, model response timeouts or authentication errors, diagnose the host SDK/model connection separately from the task container's intentionally disabled network. The deadline prevents indefinite waiting; it does not repair DNS or authentication.

## CI operation

The GitHub workflow runs locked Python 3.12 harness checks on Ubuntu and macOS. It uses read-only repository permission and does not pass model secrets. The optional `docker_smoke` manual input builds all three images on Ubuntu and exercises trusted reference repairs; it does not call a live model.

The workflow was prepared and validated locally as configuration. A green remote Actions run requires the actual repository contents to be committed and the jobs to execute. Do not claim remote CI success before observing it.

Action references are pinned to the commits documented in [uv's official GitHub Actions guide](https://docs.astral.sh/uv/guides/integration/github/). The uv tool version is pinned to 0.12.23, the version used to check this project update. Workflow event/permission syntax follows [GitHub's official reference](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax).
