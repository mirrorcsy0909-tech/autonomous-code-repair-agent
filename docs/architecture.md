# Architecture and execution boundaries

The public `project.py` interface launches the existing catalog CLI as a subprocess. It does not create another agent runtime or replace the worker supervisor. Existing CLI names, prefixes, task paths and evidence paths remain valid.

## One attempt

1. Prepare validates trusted fixture hashes, creates a Git baseline and reproduces the declared public failures inside Docker.
2. The host SDK either asks for a one-shot JSON source submission or starts an iterative Conversation using the four-operation repository tool.
3. Each successful source write triggers a source-only host checkpoint. The container snapshot must match the allowlist and original baseline hashes; test/config changes and source deletions are rejected.
4. On normal submission, the harness exports source changes, replays their Git patch on a fresh trusted fixture, adds supplemental tests unavailable to the Agent, and runs the declared test contract in another container.
5. The report combines the worker outcome, model-start marker, independent verification, SDK usage and observational quality hints. An errored/interrupted worker cannot be promoted to success by a saved candidate or stale passing artifact.

## Ownership

| Component | Owns | Does not establish |
| --- | --- | --- |
| OpenHands SDK 1.53.0 | Agent/Conversation runtime, model transport/auth, tool framework, existing workspace options | This project's task provenance or independent evaluation |
| Task fixtures | Immutable buggy snapshots, extracted public regressions, allowlists, reference and supplemental evaluation data | A broad or unbiased benchmark distribution |
| Repository tool | Bounded list/read/write/test operations inside Docker | General-purpose host terminal access |
| Worker supervisor | Process-group termination, exact-name container registry/cleanup, deadline metadata | Immediate termination while the computer is asleep |
| Fresh verifier | Patch scope, replay and exact pass/exclusion counts | Correctness on every possible input or code-review approval |
| Quality analysis | Static change-size, docstring/comment and function-change hints | Semantic equivalence, issue relevance or merge readiness |

## Containers and candidate data

Task containers use `--network none`, UID/GID 65534, capability drop, no-new-privileges, read-only root, CPU/memory/process limits and bounded tmpfs. No host directories, credentials or Docker socket are mounted. Fixture contents are seeded through stdin into tmpfs rather than through host mounts.

The model connection and OAuth/API credentials belong to the host SDK. The target code's lack of network access therefore does not prevent the SDK from requesting model responses.

A successful source-write checkpoint saves `candidate.diff`, an allowlisted source ZIP and hash metadata as host data. It leaves the host workspace baseline unchanged until normal submission. Candidate text is not imported or executed on the host. A kill during a write/checkpoint may leave the prior completed checkpoint or an incomplete save; hashes and save status distinguish them. Error-path checkpoint saving is best effort and never counts as verified repair.

The fixed Docker resource and sandbox policies are intentionally narrow. Docker is not a perfect defense against all adversarial code. This project studies trusted historical tasks with generated candidate repairs, not arbitrary multi-tenant code hosting.

## Compatibility

The early synthetic `batch-service` fixture is a wiring example. Historical task workflows use the current Docker/supervision path. The early standalone repair.py/docker_agent.py/real_task.py execution commands retain their earlier behavior and should not be presented as having every later guarantee.

No physical relocation of existing modules or experiment data is part of this update. A deeper package migration would need explicit tests of stored run compatibility and worker import paths before changing the public structure.
