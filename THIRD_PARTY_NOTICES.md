# Third-party attribution

This harness uses [OpenHands software-agent-sdk](https://github.com/OpenHands/software-agent-sdk), pinned to version 1.53.0 together with openhands-tools. OpenHands supplies the agent runtime and associated SDK capabilities. Task-specific tooling, dataset preparation, independent evaluation and reporting are separate project code.

The historical fixtures and reference changes originate from:

- [ItsDangerous](https://github.com/pallets/itsdangerous): signing and timestamped-data validation.
- [python-dotenv](https://github.com/theskumar/python-dotenv): .env parsing and environment loading.
- [sqlparse](https://github.com/andialbrecht/sqlparse): SQL splitting, formatting and token grouping.

Each task's task.json identifies its issue, buggy base and reference commit. fixture-sha256.json verifies the distributed task bytes; upstream-sha256.json and preparation notes provide additional mappings where available. Preserve the upstream license files and copyright notices already inside the fixtures. Public regression functions extracted from upstream references remain attributed to those projects; separately authored supplemental checks belong to the harness preparation workflow.

Reference fixes, trusted negative controls and fixture data are not model-generated successes. The synthetic batch-service example was authored for wiring checks and is not an upstream historical task.

This notice identifies provenance and does not replace the applicable upstream licenses. It does not apply a new blanket license to third-party code.
