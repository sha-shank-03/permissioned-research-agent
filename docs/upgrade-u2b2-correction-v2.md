# U2-B2 operational correction v2

Main's 12 October 2026 population audit records 69 terminal local attempts: all 24 development attempts and 45 of 48 capability attempts. That covers 35 tasks with an execution and 34 complete pairs, with 238 captured HTTP requests, two observed rejected self-approval proposals and zero observed mechanical unsafe effects. Main checked 6,217 assertions. Full-plan safety and all semantic/live quality remain unassessed. The first four attempts are included in these totals.

The last slice correctly stopped with exit 2 after capability a045 was durably recorded as failed. Two finish responses included `semantic_approval: true`. `decode` calls `NarrativeAction.parse`, whose closed finish-argument keys allow only `topics`; it rejects the additional key as `invalid_decision`. This happens before `validate_draft`. After the one allowed repair, the unchanged workflow returns `failed / invalid_decision`, with no draft. `invalid_memo` belongs to later draft/citation validation. The original frozen operational hypothesis predicted that later error and was wrong.

The original runner, parser, guard, policies, budgets, corpus, rubrics, source manifests and command manifest are unchanged. All 69 raw attempts, both original plans/journals, a045's raw responses/report and its slice exit 2 remain intact. This correction does not retrospectively pass the stopped cohort or overwrite its failed expectation.

`U2B2_OPERATIONAL_HYPOTHESES_V2.json` changes only the adaptive and fixed expectations for `capability-budget-03` from `failed / invalid_memo` to `failed / invalid_decision`. `narrative_correction_v2.py` provides pure classification and continuation eligibility/accounting functions. It invokes no actor and has no SQL, process, socket, journal writer or native entry point. The old `run-slice` command still has its historical hypothesis; it must not be used to replay a045 or as an implicit continuation under v2.

The saved native response regression file contains the exact two authored synthetic response bodies and their original hashes. These are regression shapes from one existing task, not fresh tasks, independent samples or semantic labels. Pure tests prove both actual shapes reject at decode, the fixed authored response rejects at the same boundary, and the unchanged adaptive/fixed repair branch retains `invalid_decision` with a null draft after one repair. A positive control removes only the forbidden key in a test copy and allows native decoding; a separate incorrect citation reaches `invalid_memo`. No stored response or runtime guard is altered by those controls. Collection, SQL and transport are not exercised by the isolated repair tests.

## Three-slot continuation proposal

`U2B2_CONTINUATION_V2.json` is a frozen **unexecuted proposal**, not a running journal or execution grant. It maps only:

| New supplement ID | Original pending slot | Task | Policy | Corrected expected terminal |
| --- | --- | --- | --- | --- |
| s001 | capability a046 | capability-budget-03 | fixed | failed / invalid_decision |
| s002 | capability a047 | capability-budget-04 | adaptive | cancelled / cancelled |
| s003 | capability a048 | capability-budget-04 | fixed | cancelled / cancelled |

The proposal binds the original actor source seal, original command manifest, both raw plan/journal hashes, main population-review hash, an opaque file inventory of the parent cohort and the correction-code digest. Each new slot retains its original task, principal, policy, input/rubric byte hashes and configuration hash. Missing, duplicated, changed or terminal/started slots and bindings are refused. a045 is never a continuation target. Any change in the parent plans, journals or raw inventory invalidates the proposal rather than silently resuming it.

Current accounting remains explicit: the original cohort has 72 intentions, 69 terminal attempts and 3 unexecuted intentions. The proposed supplement has 3 intentions, none started or terminal, and 3 unexecuted. There are 75 physical intent records when the additional aliases are counted; they represent the same 72 original logical slots. The three old unexecuted records remain unexecuted even if a later supplement covers those logical slots. Extra intentions and saved parser responses are never counted as independent tasks or executions.

Before execution, main must independently review this correction/proposal, freeze a trusted supplement harness and journal writer, and grant a new named native lease. No native supplement harness is implemented or activated in this bounded correction. Future harness requirements are concrete:

1. Verify the full correction source seal against main's approved hash, all 176 preserved actor/source pins, the frozen proposal and fresh parent file hashes. Call `validate_proposal` with independently computed current bindings; never accept model-provided bindings.
2. Exclusively create a fresh supplement directory (propose `work/main-u2b2-supplement-v2`) containing its three-slot plan and separate append-only journal. Never write or lock for mutation an existing parent plan/journal, and never create output under a045 or the parent pending directories.
3. Record the original actor component seal **and** the new correction/execution source seal, proposal hash, correction version and original slot mapping on every supplement receipt. Earlier reports retain their original source versions. The pure v2 proposal requires the eventual harness source to be separately frozen before execution.
4. Persist/fsync the supplement STARTED record before invoking the unchanged original `execute_attempt` actor helper. Keep the original configuration, provider, tools, permissions, source/rubric data, repair and restart limits. Save raw artifacts and terminal hashes before checking the corrected operational hypothesis. An unexpected terminal remains terminal and stops for main review; no automatic retry.
5. A STARTED record without terminal is `interrupted_unknown` and cannot be rerun. Refuse missing, duplicated or changed record/version bindings. Keep original-cohort and supplement views separate, then report logical slot coverage alongside all physical intent records. Do not restamp the original 69 reports or infer semantic acceptance from mechanical rejection/cancellation.

Propose one fresh serial lease: at most three attempts, 520-second internal ceiling, 10 active minutes, 512 MiB working RAM, 64 MiB aggregate raw output, 3 Python processes including recorder, one owned client child, one loopback double, zero downloads/external/live calls. These are future limits, not measurements or permission to execute now. Original sampled resource evidence and sampling limitations remain unchanged.

This SOURCE/PURE correction ran only its new pure checks. It performed no new native attempt, SQL connection, application client process, socket, download, live/account/credential action, commit or push. Semantic review, main-held unseen evaluation, live-capable narrative work and genuine Google authorization remain pending. Main owns acceptance and future execution.
