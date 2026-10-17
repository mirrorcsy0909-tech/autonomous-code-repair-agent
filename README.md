# Autonomous Code Repair Agent

[![Harness checks](https://github.com/mirrorcsy0909-tech/autonomous-code-repair-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/mirrorcsy0909-tech/autonomous-code-repair-agent/actions/workflows/ci.yml)

An experimental code-repair and evaluation harness built on **OpenHands SDK 1.53.0**. It reproduces historical Python issues in Docker, runs an iterative coding agent or a one-shot baseline, and independently verifies submitted source patches against public and supplemental tests.

The prepared catalog contains **10 historical tasks across three repositories: nine bug fixes and one feature/compatibility task**. This is a small, curated engineering evaluation, not a general repair benchmark.

## What this project adds

OpenHands provides the agent runtime, conversation state, model integration, tool interfaces and existing workspace/sandbox capabilities. This project adds:

- A task-specific repository tool with four operations: list files, read files, write allowlisted source files and run the configured test suite.
- Historical fixtures with upstream commit attribution, integrity hashes, reproducible failing baselines and external supplemental tests.
- Fresh-container patch replay, exact test-count contracts, source-scope validation and source-only candidate checkpoints.
- Supervised workers with deadlines, retry metadata, process/container cleanup and safe failure-type records.
- Paired workflow reports covering outcomes, recorded latency, SDK usage and separate static patch-quality hints.

It does not implement a new foundation model or replace the OpenHands agent runtime.

## Quick start

Use macOS or Linux with Python 3.12+, Git, [uv](https://docs.astral.sh/uv/) and Docker. Python 3.12 is the CI runner version; task containers use Python 3.13. Keep the committed `pyproject.toml` and `uv.lock` together.

From the project directory:

```bash
uv sync --frozen
uv run python project.py doctor --catalog all --offline
uv run python project.py list --catalog all
uv run python project.py check
```

These commands do not call a model. The explicit harness checks avoid discovering the intentionally broken fixture tests as host tests.

Build and validate one task catalog:

```bash
uv run python project.py build --catalog sqlparse
uv run python project.py doctor --catalog sqlparse
uv run python project.py smoke --catalog sqlparse --id project-smoke-001
```

The build context contains only its Dockerfile. Smoke reproduces the baseline failures, rejects an unchanged source tree and checks the trusted upstream reference fixes in fresh containers. It uses no model; it is not an agent success measurement. Choose a fresh ID when repeating a smoke.

## Optional live comparison

Only live `compare`, `batch` and `run` actions call a model. For a new checkout, copy `.env.example` to `.env` and configure either the SDK-supported local subscription login or direct API mode. Keep an existing `.env`; the example is not a replacement for personal configuration. The recorded experiments used `openai/gpt-5.6-sol`; model selection remains in local configuration.

```bash
uv run python project.py compare --catalog sqlparse --prefix experiment-001
uv run python project.py report --catalog sqlparse --prefix experiment-001
uv run python project.py export --catalog sqlparse --prefix experiment-001
```

This schedules five tasks × two workflows × one repeat, or ten workflow attempts. An iterative attempt can contain multiple model calls. Report/export do not call a model. Use a new prefix for a new experiment; existing runs are never silently resumed or overwritten.

| Catalog | Tasks | Workflows | Image |
| --- | ---: | --- | --- |
| `itsdangerous` | 3 | Agent-only pilot via `batch` | `code-repair-python:itsdangerous126` |
| `dotenv` | 2 | One-shot and iterative via `compare` | `code-repair-python:dotenv-comparison-v1` |
| `sqlparse` | 5 | One-shot and iterative via `compare` | `code-repair-python:expanded-v1` |

For example, `uv run python project.py batch --catalog itsdangerous --prefix pilot-new` runs the three Agent-only tasks. ItsDangerous supports unified `report`; ZIP export currently belongs to the two paired catalogs. The original `expanded_agent.py`, `comparison_agent.py` and `suite_agent.py` commands remain supported.

## Recorded observations

These are selected observed batches. Curated machine-readable summaries and source-archive hashes are in [examples/reports](examples/reports/README.md); they contain no raw model responses, worker logs or local host paths.

| Batch | Unique tasks | Repeats per workflow | One-shot passed | Agent passed | Recorded median seconds: one-shot / Agent | SDK input + output tokens: one-shot / Agent |
| --- | ---: | ---: | ---: | ---: | --- | --- |
| `compare-002` | 2 dotenv tasks | 3 | 6/6 | 6/6 | 28.59 / 77.26 | 91,770 / 814,787 |
| `expanded-001` | 5 sqlparse tasks | 1 | 2/5 | 1/5 | 87.31 / 95.18 | Usage is incomplete on errors |
| `expanded-002` | 5 sqlparse tasks | 1 | 5/5 | 5/5 | 34.25 / 66.68 | 178,761 / 712,262 |

The interrupted first sqlparse batch is retained. Three one-shot attempts explicitly reported response read timeouts; four Agent attempts reported conversation errors whose underlying types were not captured by the old logger. Large wall-clock gaps suggest suspend or clock adjustment, but do not prove the cause of the errors. The later batch completed all ten attempts.

Recorded program latency excludes prepare/auth and can omit Mac suspend time. `expanded-002` still has one approximately 94-second clock gap; its latency numbers are not complete elapsed-clock measurements. The sample contains historical public issues and may be affected by memorization. One-shot receives supplied source/context while Agent retrieves repository files through tools; this is not an equal-token or equal-compute comparison.

On these completed batches, One-shot matched the observed task pass rate with lower recorded latency and usage. No Agent success-rate advantage has been established. Both 701 patches in `expanded-002` triggered review hints for broad edits/documentation removal despite passing tests. Test success and merge readiness are different properties.

## Repository layout

| Path | Responsibility |
| --- | --- |
| `project.py` | Public command routing, environment checks and fixture integrity checks |
| `suite_agent.py`, `comparison_agent.py`, `expanded_agent.py` | Existing task and workflow implementations |
| `docker_agent.py`, `repair.py`, `real_task.py` | Shared helpers and retained early-stage commands |
| `execution_control.py`, `run_diagnostics.py`, `patch_quality.py` | Worker limits, failure/checkpoint metadata and static review hints |
| `repair_suite/`, `comparison_tasks/`, `expanded_tasks/` | Trusted task fixtures, provenance, public tests, reference fixes and external evaluators |
| `test_*.py` | Explicit harness tests; fixture suites are separate |
| `runs/` | Local runtime evidence; ignored by Git |
| `examples/reports/` | Curated observed summaries intended for review |
| `.github/workflows/ci.yml` | Offline Linux/macOS checks and optional manual Docker reference smoke |

The structure preserves existing file locations and experiment paths. The unified entry point delegates to the tested implementations rather than moving their state or changing their prompts.

## Limits and safeguards

Default worker limits are 300 seconds per run and 120 seconds for prepare, with a 60-second SDK request/stream idle timeout and two SDK attempts including the initial request. A worker deadline also covers authentication and independent verification; termination and cleanup may take additional time. New workers use the greater of monotonic and UTC wall-clock elapsed time; a forward system-clock adjustment can conservatively trigger timeout. A sleeping host cannot perform cleanup until it wakes.

The SDK subscription transport omits the requested output-token limit. `8192` is **not a verified service-enforced cap**. API configuration is also not proof of server enforcement. SDK cost estimates are not subscription bills, and failed-call usage may be incomplete.

Candidate source executes only in restricted Docker containers in the current workflows: network disabled, nonroot, read-only root, bounded resources, no host mounts, host credentials or Docker socket. The SDK remains on the host with its model connection. Source checkpoints and quality analysis handle candidate text as data on the host. This is task-specific isolation, not a claim of a universal security boundary against all container escapes.

See [architecture](docs/architecture.md), [evaluation](docs/evaluation.md), [operations](docs/operations.md) and [third-party attribution](THIRD_PARTY_NOTICES.md). The early standalone host-based `repair.py` command is a historical wiring example; use the unified Docker workflows for current experiments.

## CI

The supplied workflow runs explicit harness checks on Linux and macOS with locked dependencies and no model credentials. Docker builds/reference smoke are opt-in through manual workflow dispatch. It does not run candidate repairs with a live model or publish anything.

The [initial CI run](https://github.com/mirrorcsy0909-tech/autonomous-code-repair-agent/actions/runs/39063840991) passed on both Linux and macOS. The optional Docker job was skipped by configuration; Docker reference smoke was validated separately on the development Mac. The badge links to the latest workflow status.

Project documentation, code comments and command descriptions use English. Non-English strings in fixtures and supplemental tests are intentional Unicode/encoding test data; upstream fixture bytes are preserved for integrity checks.
