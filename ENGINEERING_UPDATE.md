# Engineering integration notes

These notes describe the unified command entry point, documentation and CI already included in this repository. For a fresh checkout, follow the [README](README.md).

## Integration scope

- Added `project.py`, `test_project.py`, architecture/evaluation/operations documentation, curated example reports and third-party notices.
- Added Linux/macOS harness checks and optional manual Docker reference smoke in `.github/workflows/ci.yml`.
- Preserved existing dependencies, source locations, personal `.env` configuration and local runtime evidence.

The unified entry point delegates to existing implementations. Integrating these files does not require rebuilding images, rerunning model experiments or moving historical runs. Curated reports in `examples/` are tracked; raw `runs/` directories remain local.

## Validate the integration

Run from the project root:

```bash
uv run python project.py doctor --catalog all --offline
uv run python project.py list --catalog all
uv run python project.py check
```

The offline doctor checks dependencies and fixture integrity without reading `.env`, contacting a model or probing Docker. The catalog lists ten tasks across three repositories, with `sqlparse-742` classified as `feature_compatibility`. The explicit test command runs 78 harness checks.

With Docker available and the sqlparse image built:

```bash
uv run python project.py smoke --catalog sqlparse --id project-smoke-001
```

Expected completion: worker exit code `0`, `SMOKE PASSED` and `cleanup=confirmed`. Use a fresh ID, such as `project-smoke-002`, if the example ID already exists. Smoke checks trusted baseline/reference behavior without calling a model.

For an existing local experiment:

```bash
uv run python project.py report --catalog sqlparse --prefix expanded-002
```

The reviewed `expanded-002` batch has five passing attempts per workflow. Both task-701 patches have `review_required` hints. The original `expanded_agent.py report` command remains supported. A fresh clone contains curated summaries, not raw local `runs/` evidence.

## Observed validation

All 78 harness checks and unified Docker smoke passed on the development Mac. The [initial GitHub Actions run](https://github.com/mirrorcsy0909-tech/autonomous-code-repair-agent/actions/runs/39063840991) passed the Linux and macOS offline jobs. The optional remote Docker job was skipped by configuration; it was not part of that CI result. No additional live-model experiment was needed for this integration.
