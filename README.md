# Permissioned Research Agent

A small Python 3.11 workflow that researches a fictional vendor across two local enterprise connectors and returns a four-topic memo with exact source citations. Documents use object ACLs; messages use channel ACLs. Three simulated identities have different, nonhierarchical access.

**Current stage: final local MVP acceptance passed independently on Windows.** All 128 local tests passed. Across 30 same-author actor-held-out examples and three paired repeats, scripted passed 90/90 primary attempts and baseline passed 78/90. Both passed citation permission and cross-identity authority checks, with zero detected forbidden disclosures in 9,609 scripted and 7,614 baseline surface observations. All 18 oracle corruption controls and eight disclosure corruption controls were rejected; the Windows clean-copy checks passed. Main accepted all 30 rubrics and candidate3 evaluator source before scoring. Hosted CI remains unexecuted. See [EVIDENCE.md](EVIDENCE.md) for denominators, command receipts and limitations.

All decisions are explicitly **scripted**. The actor uses an observation-driven provider interface and executes actual local search/read tools. This demonstrates workflow wiring and enforcement; it provides no evidence of live model quality. No model, network service, credentials or pip dependency is required.

## Run from a checkout

Use Windows PowerShell from the repository root:

```powershell
py -3.11 -m permissioned_research doctor
py -3.11 -m permissioned_research demo --scenario vendor-review --principal priya --provider scripted --state-dir .local/m1-priya
py -3.11 -m permissioned_research demo --scenario vendor-review --principal alex --provider scripted --state-dir .local/m1-alex
py -3.11 -m permissioned_research demo --scenario conflicting-review --principal priya --provider scripted --state-dir .local/m1-conflict
py -3.11 -m unittest discover -s tests -p "test_slice_*.py" -v
```

The commands above passed for the M1 predecessor, including main's independent review. The M2 full suite also exercises those normal/conflict/identity workflows and CLI compatibility. Separate M1 doctor/demo commands were not rerun in M2. On other operating systems select Python 3.11 explicitly, then use that interpreter with the same module arguments. The CLI refuses other minor versions to keep the documented target clear.

The demo writes a SQLite corpus and safe audit to the named state directory. Each directory is bound to a fixture fingerprint. Reusing it with a different scenario fails without replacing its data. Attempt IDs and timestamps vary; scripted domain results should remain equal. The fictional corpus itself includes restricted data and belongs to the trusted local process.

Use `--request-key review-1` to bind a stable key to identity, normalized vendor/date/scope and policy version. Changed binding fails with `request_key_conflict`. Omitting the flag creates a new key. Valid reuse starts a new attempt with fresh research, clearing warm caches even for API callers; no memo is saved in a receipt. One active attempt is allowed per local database. After an abandoned process releases ownership, the next owner records the pending attempt as `interrupted` and starts with fresh context. These behaviors passed local CLI/process and API integration checks.

Default stdout is JSON. A `NO MODEL CALLED` banner goes to stderr. `--format markdown` produces a readable memo. The application does not persist memo text, prompts, model responses or source snippets as logs. A terminal display or a user-directed export is an already released snapshot and cannot be recalled.

Observed scripted domain outcomes:

| Scenario and identity | Expected memo |
| --- | --- |
| `vendor-review`, `priya` | EU hosting, SSO enabled, 10 onboarding business days, 4-hour priority support; ready for review |
| `vendor-review`, `alex` | General hosting/SSO evidence; onboarding and support unknown; needs follow-up |
| `conflicting-review`, `priya` | Support sources disagree between 4 and 8 hours; both cited; needs follow-up |

`sam` can see employee and security evidence, but not procurement material. These are fictional identities selected by a trusted CLI, not production login/authentication.

Exit `0` means the command finished, including a valid `needs_follow_up` memo. Exit `2` means invalid input/configuration or incompatible state. Exit `3` means a run/storage failure, cancellation or exhausted budget. Domain status appears separately in JSON; command success does not mean all topics are answerable.

## How the agent works

The provider receives the vendor question, four-topic scope, tool descriptions and previously authorized tool observations. It emits exactly one versioned JSON action: `plan`, `search`, `read`, `finish` or one `revise_plan`. The controller validates each envelope, binds tool access to the original identity, calls the connector and returns its result for the next decision.

The scripted policy reads observed handles, searches both connectors and uses follow-up searches for visible gaps/conflicts. It never reads scenario files or answer labels. The normal case draws hosting evidence from TeamChat and the remaining facts from DocsHub; the conflict case requires both connectors' citations.

SQL ACL filtering precedes text ranking. M2 holds a short SQLite write transaction through current-context checks and provider submission, releases it during response collection, then rejects responses from an invalidated epoch. One complete restart clears observations, handles and both bounded in-memory caches. The final gate and caller handoff share a transaction. Deterministic local barrier tests passed for these ordering boundaries.

The final gate checks current access/revision, exact character offsets, a small typed fact grammar and freshness. Renderer text comes from accepted domain records, not arbitrary provider prose. No provider-supplied identity, file path or URL becomes authority. Cache keys include corpus namespace, identity, epoch, policy, provider and query/request parameters; cache reuse reauthorizes dependencies and creates fresh handles.

The four allowed topics are hosting region, SSO, onboarding business days and priority support hours. Sources use short authored fact lines; the validator does not prove entailment for arbitrary prose. Overall readiness is computed by the controller, not selected by the provider.

See [architecture](docs/architecture.md) and [provider protocol](docs/provider-protocol.md) for the boundaries and tradeoffs.

## Evidence and project contribution

The intended contribution is a reproducible research workflow that tests permissions alongside conflict, stale evidence and citation fidelity. M1 establishes the basic loop and release gates. Its independent workflow tests use explicit expected values and SQL/source-span assertions rather than a model's own answer.

M2 source adds cache invalidation, concurrent ordering boundaries, a loopback Ollama wire adapter, an owned HTTP test double, bounded failure handling, a top-one retrieval baseline and development labels/verifier. Four families (normal, conflict, stale, missing) across three identities define 12 development cases. Evaluation labels are outside the actor's registered tools. The evaluator uses independent SQL ACL/span checks; the baseline shares the same grammar and guards but performs one query/top-one result per connector.

M2 integration commands executed locally under the named lease:

```powershell
py -3.11 -m unittest discover -s tests -p "test_permissions_*.py" -v
py -3.11 -m unittest discover -s tests -p "test_provider_*.py" -v
py -3.11 -m unittest discover -s tests -p "test_failures_workflow.py" -v
py -3.11 -m unittest discover -s tests -p "test_failures_persistence.py" -v
py -3.11 -m permissioned_research demo --scenario revoked-resolution --principal priya --provider scripted --state-dir .local/m2-revocation --request-key m2-revocation-1
py -3.11 -m evaluation.evaluate --split dev --providers scripted,baseline --repeat 1 --output .local/m2-evaluation.json
py -3.11 -m unittest discover -s tests -v
```

The evaluation command ran 12 cases per policy (24 total). Scripted passed 12/12, with 29/29 valid citations and 19/19 answerable current topics covered; baseline passed 9/12, with 25/25 valid citations and 17/19 current topics covered. Both had zero detected violations in the checked surfaces. These are tiny authored development cases, not held-out or live-model quality results. `evaluation/FROZEN_ACTOR.json` records candidate3; candidate1/2 are retained under `evaluation/freezes/`. Its original pre-integration flags remain historical metadata; M2 acceptance is recorded here and in EVIDENCE.

## M3 results and reproduction

Main accepted all [30 rubrics](docs/m3-rubrics.md) as written and candidate3 evaluator source before first scoring. `evaluation/FROZEN_M3.json` pins their unchanged bytes and the revised evaluation source after the accepted actor/baseline freeze. The [evaluation method](docs/evaluation-method.md) defines every metric, event and disclosure boundary. These are same-author actor-held-out cases, not a blind benchmark. No actor, baseline, fixture, label, rubric or evaluator source changed after scoring.

Source-only validation:

```powershell
py -3.11 -m unittest discover -s tests -p "test_m3*_units.py" -v
py -3.11 -m evaluation.evaluate --verify-freeze
py -3.11 -m evaluation.release_check
```

The following integration commands executed once under the named M3 lease, all with native exit 0. The original first-command sampler exit 124 is retained and explained in EVIDENCE; doctor itself succeeded and was not rerun. The three candidate3 output paths below now exist in the worker checkout. Use new versioned paths for any reproduction there; the CLI preserves existing evidence by refusing to overwrite it.

```powershell
py -3.11 -m permissioned_research doctor
py -3.11 -m unittest discover -s tests -v
py -3.11 -m evaluation.evaluate --split heldout --providers scripted,baseline --repeat 3 --output .local/final-evaluation-candidate3.json
py -3.11 -m evaluation.evaluate --verify-corruptions --output .local/verifier-controls-candidate3.json
py -3.11 -m evaluation.release_check --run --output .local/release-check-candidate3.json
git diff --check
git status --short
```

The paired batch contains **30 examples and 180 planned primary case-runs**, plus separately reported cache warm-ups and cross-identity probes. Reports provide actual task success, supported-claim precision, coverage, citation fidelity, permission/freshness/conflict/abstention correctness, per-surface violations, all denominators, counters and variation by identity/family/repeat. Empty denominators are N/A. Both policies must preserve permission, disclosure and probe authority. Baseline task/coverage failures, ties or regressions remain visible without requiring baseline superiority. No live-model quality or adaptive-reasoning advantage follows from this scripted comparison.

All 180 primary attempts completed; 18 auxiliary cache warm-ups are separate. Scripted passed 30/30 and baseline 26/30 in each repeat. Supported claims and citation fidelity were 294/294 versus 279/279; current answerable coverage was 228/228 versus 219/228. The same four baseline cases failed in every repeat, yielding 78 paired ties and 12 scripted-only successes. Every case/provider domain result was unchanged across repeats. These repetitions do not increase the independent example count.

Each evaluation output must use a new path. The held-out command reserves the report plus an append-only `.attempts.jsonl` sidecar, saves the complete planned identities and denominators before any case, and persists start/completion records after each boundary. An unexpected case failure retains prior metrics and the failed case/provider/identity/repeat/exception type, marks the batch incomplete and exits nonzero. Planned task and labeled opportunity denominators stay fixed; failed/unexecuted attempts receive no fabricated outcomes or counters. No exception message, source, prompt, memo or traceback is saved. Existing output or sidecar evidence is refused with exit `2`; a reviewed retry must select a fresh versioned output path.

The workflow in `.github/workflows/ci.yml` uses standard `ubuntu-24.04` and `windows-2025` runners, exact Python `3.11.9`, full action commit pins, read-only repository permissions and a ten-minute job limit. It installs no pip packages and uses no credentials, paid APIs, external model or model download. Hosted setup may obtain the pinned Python runtime from GitHub's tool cache/distribution; this is distinct from a model download. One repeat produces 60 primary case-runs per OS. No hosted workflow has been triggered; Ubuntu/POSIX execution remains untested. Local Windows clean-copy verification passed: it copied only shipping files to one checked owned temporary directory, ran doctor/demo/freeze/controls with an explicit interpreter, verified copy hashes and removed that directory. It did not repeat the held-out batch. Final documentation was updated afterward; main's independent final check is pending.

Current limitations include trusted local identity selection, tiny authored English sources and a four-scalar memo grammar. Dropping references does not securely erase memory or recall prior disclosures. The actor has no arbitrary filesystem/network/shell tool, but it is not a sandbox against a hostile local administrator or provider executable. The CLI exposes only scripted decisions. The optional HTTP adapter is disabled by default and has never contacted a model. No production connector, general entailment, live model performance or remote CI result is claimed. The [threat model and tradeoffs](docs/threat-model.md) define trusted actors, revocation boundaries and exclusions.

Original source and authored fictional data are MIT licensed. [DATA_LICENSE.md](DATA_LICENSE.md) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) record provenance and runtime attribution.
