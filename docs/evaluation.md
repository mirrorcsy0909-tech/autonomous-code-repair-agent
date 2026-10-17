# Evaluation contract

## Prepared task set

| Repository | Historical tasks | Task type | Fresh-verifier pass counts |
| --- | --- | --- | --- |
| ItsDangerous | 126, 124, 237 | 3 bug fixes | 468, 446, 472 |
| python-dotenv | 256, 361 | 2 bug fixes | 77, 84 |
| sqlparse | 701, 745, 782, 784 | 4 bug fixes | 453, 439, 452, 454 |
| sqlparse | 742 | Feature/compatibility: optional trailing-semicolon stripping | 435 |

Each task records the upstream project, issue, buggy commit, reference commit, exact fixture hashes, selected tests and test counts. The original source/test/data bytes are preserved except for documented path relocation; public regressions and any required new public assets are added separately. Source-only reference patches and supplemental tests remain outside the Agent's container.

These are selected test suites, not every upstream project test. The dotenv fixtures select parser/utils/variables/public tests. sqlparse excludes CLI tests requiring external entrypoint installation and deselects exactly three historical xfail-marked tests. Both baseline and verification must match their declared counts; an extra skip/deselection or incomplete run is rejected.

Source validation requires a reproducible broken baseline and a passing upstream reference. Negative controls modify known trusted reference behavior to check that supplemental tests reject incomplete repairs. Negative controls and reference smoke are harness tests, not model repair results.

Docker image IDs are recorded per run and matched within each pair. The recipes use a moving python:3.13-slim tag and platform-specific builds; a later rebuild is not guaranteed to reproduce an old image ID. Keep image identity separate from pinned Python-package versions.

## Workflows and denominators

The paired dotenv/sqlparse workflow alternates execution order by task/repeat. Each attempt receives a fresh process, SDK context and task container; each submitted patch gets a separate verifier container. The one-shot prompt includes selected original source, public tests and baseline output. The iterative Agent retrieves files through tools and can run the configured public suite. It is a workflow comparison with different context delivery and total compute, not an isolated causal test of tool use.

All started model attempts remain in the denominator, including request errors, incomplete attempts and timeouts. Prepare/auth failures before dispatch are reported separately. A repeat is another attempt on the same task, not another unique task. No automatic experiment rerun happens after an invalid submission or timeout. Keep failed batches when conducting a later fresh batch.

Legacy records retain their original bytes. Report-time normalization corrects unsupported subscription cap claims and adds derived quality/clock fields; it cannot recover missing historical exception types, candidates or token usage. A zero SDK usage record after a request error does not prove the provider consumed no tokens.

## Two independent judgments

- **Test result:** exact configured public and supplemental tests completed with the declared passes/exclusions in a fresh container and a permitted source patch.
- **Patch review hint:** static scope/size/documentation/function changes deserve inspection. `review_required` does not turn a passing test result into failure; `no_heuristic_flags` does not certify a patch for merging.

Quality policy v1 is heuristic and intentionally does not try to estimate correctness or semantic equivalence. The 701 patches in expanded-002 demonstrate why passing tests alone is insufficient for merge approval.

## Recorded evidence and limits

Curated summaries preserve three batches: compare-002, expanded-001 and expanded-002. Their source ZIP SHA-256 values identify the reviewed input artifacts. The summaries are content-limited examples, not a substitute for complete raw review archives and fixture metadata.

Recorded latency excludes prepare/auth. UTC-clock gaps can indicate suspend or clock adjustment; they are diagnostic, not proof of cause. The second sqlparse batch still has a roughly 94-second gap in its 701 Agent attempt. Compare both timing fields when assessing elapsed time.

The selected tasks are old public issues, so memorization is a possible confound. Repeats, related repository versions, a small curated task set and differing token budgets limit generalization. On the completed illustrated batches, both workflows passed; One-shot used fewer SDK-reported tokens and lower recorded median latency. Do not claim an Agent success-rate advantage, a general 100% repair rate, or measured subscription billing from these observations.
