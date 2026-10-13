# Publication packaging correction v5 — 13 October 2026

Main's clean Git-index export failed its five-module pure command at import: the v4 test module read private sibling coordination receipts that are absent in a fresh checkout. Further review found equivalent v3 context tests copying ignored native work captures and a private population review. V4 scratch setup also assumed work already existed. The original failure, original staged tree and accepted native results remain preserved. This correction changes public test input packaging only; no actor, policy, prompt, data rubric, scorer or evaluation population changes.

The original two test files are preserved byte-for-byte under `evaluation/upgrade/freezes/publication-packaging-v5/*.original.txt`, with their historical paths and hashes in the correction seal. Existing source freezes and native command manifests retain their original bytes and meanings. Two current test paths now use repo-local contract inputs. All original eligibility, ordering, replay, redaction, denominator and source/parent/proposal drift assertions continue to execute. No missing-evidence contract is skipped.

`fixtures/upgrade/publication-contract-v5/reference-projections.json` contains selected nonsecret accounting fields from the already-reviewed interruption receipts, with source hashes and explicit `is_full_receipt: false`. A projection is not byte-equivalent to a private receipt and does not verify its full-file hash. It supports in-memory shape/eligibility tests only. Missing or changed local provenance is an error; there is no private-folder fallback.

`evaluation/upgrade/public_contract_fixtures_v5.py` builds owned temporary synthetic file graphs from repo-local source. It recalculates their source/plan/proposal/journal/reference hashes and runs the unchanged v3/v4 Context validators against those actual files. Fixed historical schema identifiers are compatibility inputs; provenance states synthetic contract fixture, not native evidence or accepted main receipt. Synthetic journal rows are written as data without running/replaying an actor. An artifact named corpus.sqlite is opaque text, not a database. Generated receipt inputs live within the synthetic checkout. No private sibling, native capture, SQLite connection, HTTP service, child or provider is needed by the new builder/demo.

The old module-import assertions were incompatible with the combined public suite, which intentionally imports native helper definitions for other unit contracts. Their replacements spy on adapter execution during the context contracts. Default adapters remain forbidden in the injected-controller tests. The correction tests additionally enforce repo/owned-input-only reads, no SQL/socket/child calls in the builder, archived-byte identity, no native-evidence claims and actual parent-drift rejection. The unchanged older inspector unit suite uses in-memory SQLite; it executes no native task adapter or external service.

From a fresh checkout, run:

```text
py -3.11 -X utf8 -B -m unittest tests.test_upgrade_narrative_continuation_v4_pure tests.test_upgrade_narrative_correction_pure tests.test_upgrade_narrative_pure tests.test_upgrade_narrative_suite_pure tests.test_upgrade_narrative_supplement_pure -v
py -3.11 -X utf8 -B -m unittest tests.test_publication_contract_v5 -v
py -3.11 -X utf8 -B -m evaluation.upgrade.public_contract_fixtures_v5 --summary
```

The demo reports synthetic binding checks, zero actor runs and no historical native-evidence validation. It is not the native supplement CLI. Frozen operator/native commands still require their original private evidence and exact source version; the revised current test paths intentionally no longer match the old freeze at those paths. Historical reproduction requires explicitly materializing the archived original bytes. There is no automatic archive fallback in production validators, no rewritten old seal and no authority to rerun a started slot.

The local evidence remains 72 logical slots: 71 terminal and one historical unknown; 77 physical intentions are aliases, not samples. Original a045 failure/exit 2, interrupted s001/a046 claim/lock and both v4 cancellations remain immutable. The correction does not improve quality, approve full-plan safety, establish unseen/live/Google results or supply token/cost receipts. Main owns independent clean-export review, staging, commit and push; this worker leaves the index untouched.
