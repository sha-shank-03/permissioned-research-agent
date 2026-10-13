# Existing local outputs: main review reconciliation candidate v6

Codex main independently rated all **56 existing drafts**, covering 21 development and 35 capability outputs, 485 emitted claims, 485 eligible relationships and 392 topic decisions. Saved inspections report coverage and semantic correctness of 1.0 for those reviewed outputs, with locally reviewed narrative completion for all 56. These are model-assisted operator judgments on author-exposed, scripted local-double outputs. They are not human sign-off, reviewer agreement, unseen evaluation, live-model results or Google sandbox evidence.

This candidate imports the exact recorded judgments without rerating. Every join uses population, task, policy and original attempt together. Input/rubric/draft canonical hashes and report/trace/review file hashes bind the joins. Attempts such as development/a001 and capability/a001 are distinct records. Original `needs_review` outcomes remain immutable; the new review layer does not turn them into operational approvals.

| Denominator | Slots | Known terminals | Main-reviewed narrative completions | Failed | Cancelled | Unknown |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Development | 24 | 24 | 21 | 3 | 0 | 0 |
| Capability | 48 | 47 | 35 | 10 | 2 | 1 |
| Known terminal subset | 71 | 71 | 56 | 13 | 2 | 0 |
| Complete logical population | 72 | 71 | 56 | 13 | 2 | 1 |

Development completion is 21/24 (87.5%) within this exposed local subset. Capability has 35 known completions/48 intended slots (72.9%), or 35/47 known terminals (74.5%). The aggregate has 56/71 known-terminal completions (78.9%). The count fraction 56/72 (77.8%) retains the unknown in its intended denominator; it is **not a complete-population success rate**. The full logical completion fraction remains null because the unknown outcome is unresolved.

| Population / policy | Intended | Terminal | Reviewed completions | Failed | Cancelled | Unknown | Known requests |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Development / adaptive | 12 | 12 | 11 | 1 | 0 | 0 | 67 |
| Development / fixed | 12 | 12 | 10 | 2 | 0 | 0 | 12 |
| Capability / adaptive | 24 | 24 | 19 | 4 | 1 | 0 | 139 |
| Capability / fixed | 24 | 23 | 16 | 6 | 1 | 1 | 20 |
| All / adaptive | 36 | 36 | 30 | 5 | 1 | 0 | 206 |
| All / fixed | 36 | 35 | 26 | 8 | 1 | 1 | 32 |

All 36 authored task pairs retain equal configured policy budgets, input/rubric versions and source lineage. Configured equality does not imply equal realized work. There are 35 complete terminal pairs and 25 pairs with two reviewed drafts. Coverage ties across those 25 reviewed pairs; six other reviewed drafts have failed counterparts, and five tasks have no successful draft pair. The full 36-task quality delta remains null. The differing completion counts and request counts describe these scripted outputs only; they support no quality-uplift inference about real models.

Physical accounting remains **77 intentions = 71 terminal + one unknown + five old unexecuted aliases**. The logical denominator is 72, not 77 independent samples. Original a045 failed `invalid_decision`, with slice exit 2 under the old `invalid_memo` expectation. That mismatch and its raw receipt are retained; the later operational correction only changed the expectation. Fourteen of the 15 non-narrative terminal outcomes match the initial frozen contract; all 15 match the corrected contract. Expected safe failure or cancellation is not successful narrative completion, and no-draft quality is not scored as zero or perfect.

V3 s001/a046 remains historical `interrupted_unknown`, with its partial capture, claim and lock unchanged. It has no returned result or complete request/usage receipt and cannot be retried, recovered or relabelled. V4 c001/a047 and c002/a048 cancelled with zero provider/tool requests and no narrative. There are 238 known provider requests over the 71 terminals; the full-72 total is unknown. Model tokens, actual/estimated cost, reviewer agreement and aggregate full-plan safety remain null. Finite mechanical checks and favorable recorded draft judgments are not universal safety guarantees.

The [compact projection](../evaluation/upgrade/U2B2_LOCAL_REVIEW_PROJECTION_V6.json) carries original identifiers, receipt digest references, the independent main review layer and separate denominator summaries. Original receipt files remain private operator evidence; the projection includes no source bodies, claims or personal filesystem paths. Its [read-only validator](../evaluation/upgrade/review_projection_v6.py) checks metadata/accounting and bound digest consistency, not signatures or private receipt authenticity. Private reconciliation separately verified the exact saved files. Synthetic corruption controls cover alias inflation, erased unknowns, failure relabeling, mismatched review bindings, null-to-zero usage and population-ID collisions.

Read-only checks, with no actor, SQL or private sibling access:

```text
py -3.11 -X utf8 -B -m evaluation.upgrade.review_projection_v6
py -3.11 -X utf8 -B -m unittest tests.test_review_projection_v6 -v
```

This is an additive reviewer-facing candidate for main review. Existing source, scorers, prompts, fixtures, manifests and historical documents were not edited. Live, isolated sandbox, main-unseen, human approval, final demo and publication gates remain closed. No new evaluation population, inference run or publication occurred.
