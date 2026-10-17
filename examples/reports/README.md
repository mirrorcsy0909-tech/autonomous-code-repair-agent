# Curated observed reports

These JSON examples summarize real reviewed user-machine runs. They are not fresh model runs, simulated successes or the entire project evidence history. Each file records the SHA-256 of its reviewed source archive and retains every started attempt in that batch.

| File | Meaning |
| --- | --- |
| compare-002.json | Two dotenv tasks × three repeats × two workflows; twelve successful attempts |
| expanded-001.json | First sqlparse batch; three verified successes and seven pre-verification execution failures |
| expanded-002.json | Later sqlparse batch; ten verified successes, with both 701 patches flagged for static review |

Included fields cover task/base/image identity, outcomes, recorded timing, test counts, SDK usage and derived quality hints. Omitted material includes raw model responses, reasoning, worker logs, credential data, private host paths and full patch source. A reviewer needs the complete review archive and trusted task fixtures for a full patch audit.

Program latency is not complete elapsed-clock time on a sleeping Mac. SDK usage can be incomplete on errors. No billing amounts are established. Repeats do not increase the count of unique tasks, and task scope/test-suite exclusions remain as documented in docs/evaluation.md.

`test_project.py` recomputes counts, medians and token totals to check these examples for internal consistency. That check does not independently rerun or certify the historical model results.
