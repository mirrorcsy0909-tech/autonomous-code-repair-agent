# Historical python-dotenv task provenance and evaluation scope

Repository: https://github.com/theskumar/python-dotenv

| Task | Buggy source commit | Reference commit | Public selected tests | Supplemental | Verifier total |
| --- | --- | --- | ---: | ---: | ---: |
| dotenv-256 | d83c1b65ceab5cf1223c41649a25a142755fe703 | 6712bc8a12db1e8d3f09b7bf7d3c25338992e422 | 57 | 20 | 77 |
| dotenv-361 | fc138ce8a430b758f4f2c89bc8104f259e2cba38 | 45848bb780c26ef0adf7898656f7d3d3f4e2d8ae | 63 | 21 | 84 |

Issue/PR sources:

- https://github.com/theskumar/python-dotenv/issues/256
- https://github.com/theskumar/python-dotenv/pull/361

Each fixture preserves src, tests, setup metadata, README and upstream LICENSE.
All Python source files and original upstream test files are byte-identical to
the buggy Git commit. No source mutation is used to create the bug. Per-task
SHA-256 inventories record fixture contents.

`tests/test_public_regression.py` is a harness wrapper with imports and one
verbatim test function including its parametrization from the reference commit:
`test_dotenv_values_stream` for #256, `test_set_key` for #361. The snippet hash
and function name are recorded in task.json. This exposes the upstream failing
case without applying the upstream source fix. The wrapper imports are ours.

The added pytest.ini deliberately selects test_public_regression, test_parser,
test_utils, test_variables (where present), and the verification-only supplement.
These are selected task suites, not full upstream test suites. CLI, IPython,
the remainder of test_main and broader integration/environment behavior are not
covered. Keeping that scope explicit also avoids platform/permission-dependent
tests unrelated to the two fixes. Original unselected tests remain in the source
snapshot as context but are not collected by the task test command.

The historical #256 fix privileges earlier file bindings over existing process
environment values for expansion. It predates later override-aware interpolation
semantics. Supplemental tests follow that historical contract, not the latest
python-dotenv API behavior. They also check environment fallback, disabled
interpolation, self references, defaults, empty values and load assignment policy.

#361 supplements cover missing/present newlines, empty files, comment tails,
export mode, replacement behavior and quote modes. The model cannot access
supplement contents or the source-only reference patch. They remain on the host
and are only used by smoke checks or independent verification.

The new image pins pytest 9.1.1, freezegun 1.5.5, click 8.1.8 and mock 5.2.0 on
Python 3.13-slim. PYTHONPATH points to /workspace/src for trusted test subprocesses.
It is not a host mount. Candidate code runs only in the existing constrained
Docker tool environment; the model and credentials remain on the host.

## Workflow comparison protocol

One-shot receives original allowlisted source files, selected public tests,
conftest, pytest.ini and the baseline failure output in one prompt. Its response
must contain a JSON mapping of allowed paths to complete replacement contents.
It gets one logical SDK request and no tools/test feedback. Optional transport
retries are SDK behavior, not prompt-based repair retries.

Iterative receives the same symptom/requirements and source allowlist and may
use the existing repository tools to inspect code, reproduce and iterate. Both
use the same model selection, image ID, task snapshot and independent verifier.
The output-token cap is 8192 per request, while total requests/tokens differ.
This is an exploratory workflow comparison, not an equal-compute causal study.
Task/repeat ordering alternates which workflow goes first. A fresh run directory
and model context are used for each trial. Repeats are not extra unique tasks.

SDK usage and estimated cost are reported when available; estimated cost is
not the actual subscription bill. Missing values stay null. Failed and unfinished
started trials stay in each workflow denominator. Task-image-model-output-cap
mismatches are marked instead of treated as matched pairs.

The problems are publicly solved historical bugs and may have appeared in model
training data. Small trial counts do not establish generalization or significance.
