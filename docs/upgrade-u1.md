# U1 scalar Gateway protocol slice

Status: source and pure units only. Transport tests, full regressions and the
commands below are authored but **unexecuted** pending main's named U1 lease.
Main has not accepted an upgrade milestone. Original MVP evidence remains in
README/EVIDENCE and the byte-identical v1 freezes.

This additive Python 3.11, standard-library path exchanges documented Vercel
AI Gateway Chat Completions messages with an owned loopback HTTP double. It sends
native assistant `tool_calls`, correlated `tool` messages, explicit `model`, closed
function schemas and `tool_choice: required`. The double uses the original
observation-driven authored scalar policy. It sees no scenario, principal,
fixture, SQL connection or gold label. This is authored local-double evidence;
it is not a real model, real external connector or deployment.

The protocol follows the official [REST API](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/rest-api)
and [function calling](https://vercel.com/docs/ai-gateway/sdks-and-apis/openai-chat-completions/tool-calling)
documentation inspected on 11 October 2026. No dependency or model download is
needed. The synthetic double model is `test/synthetic-scalar`; it is not a
catalog model or the worker chat model.

## Intended walkthrough, pending execution

Run from the repository root on Python 3.11. Use a fresh output name for every
attempt. On non-Windows systems use the installed Python 3.11 interpreter in
place of `py -3.11`.

```powershell
py -3.11 -m permissioned_research.upgrade doctor --mode offline
py -3.11 -m permissioned_research.upgrade demo --provider gateway-double --model test/synthetic-scalar --scenario vendor-review --principal priya --out work/upgrade-u1/priya-v1.json
py -3.11 -m permissioned_research.upgrade demo --provider gateway-double --model test/synthetic-scalar --scenario vendor-review --principal alex --out work/upgrade-u1/alex-v1.json
py -3.11 -m permissioned_research.upgrade demo --provider gateway-double --model test/synthetic-scalar --scenario conflicting-review --principal priya --out work/upgrade-u1/conflict-v1.json
py -3.11 -m evaluation.upgrade.verify --run work/upgrade-u1/priya-v1.json work/upgrade-u1/alex-v1.json work/upgrade-u1/conflict-v1.json
```

Expected, not observed in U1: Priya obtains all four supported topics; Alex has
two unknown procurement topics; conflicting accessible support values remain a
conflict and cause a follow-up search. Normal, gap and conflict branches use
actual ACL-filtered tools, not answer labels embedded in prompts. The separate
verifier reads raw SQLite memberships/grants/versions and public development
labels, checks final scalar values/freshness/spans and reconciles counts with
SQL receipts and metadata events. It does not import the actor/guard, infer
general narrative entailment or prove wire disclosure safety from a report.
The transport tests inspect actual captured messages and disclosure surfaces
in memory for that last boundary.

## Authority, transport and failure behavior

The frozen controller binds principal outside all model arguments. A SQLite
`BEGIN IMMEDIATE` gate checks current authorization before constructing a
request. The owned client child acknowledges only after the complete HTTP body
has been sent. Submit waits for that acknowledgement inside the gate. Headers
and body are collected after the gate is released, allowing an ACL writer to
commit; the controller rechecks epoch before using the result. An epoch change
discards old evidence, calls/history and cache contents. At most one restart
is allowed, with the same request/tool counters and monotonic task deadline.

Caps: 12 decisions/requests, six searches, eight reads, 48 KiB native request,
64 KiB response, 16 KiB action and 2,048 reported output tokens per response.
The task deadline is 120 seconds, with a one-second cleanup reserve; per-request
active transport is at most 30 seconds and submit at most five seconds.
The parent kills its owned client on a deadline or cancellation, including
synthetic blocked DNS/TLS/send phases and slow headers/body. Cleanup reports
success only if the process and pipe threads have stopped. These bounds await
Windows transport execution; source and fake-process units are not timing proof.

Automatic inference transport retries are zero, including 429, 5xx, timeout and
ambiguous response loss. 401/402/403 halt with typed authentication/budget/
verification handoff. HTTP 403 alone does not diagnose the account's exact
verification problem. A malformed decision may get one controller repair as a
new bounded turn; this is distinct from transport retry. Existing local read-only
tool retries remain bounded and are counted separately. Unsupported finish
proposals are guarded by the original exact scalar/span validator; proposed and
released counts are separate.

The CLI does not resume interrupted attempts. Existing report, events or SQLite
sidecars fail before starting another request. A failure preserves its prefix
and events; it cannot reset the same attempt's bounds through overwrite. A new
output name is an explicitly new research attempt, not an exactly-once resume.
The original v1 fresh-attempt recovery remains unchanged. Durable cross-process
continuation is not implemented in U1.

`Ctrl+C` during a controller run returns the existing cancellation outcome and
closes owned clients. An already sent request remains potentially billable;
cancellation does not prove zero charge. Process kill/crash leaves an incomplete
prefix and durable metadata, which must remain available for inspection.

## Evidence and spend

`*.events.jsonl` contains only allowlisted enums, bounded counts, timings,
run/version and code/prompt/config/dataset hashes. It never copies provider or
source envelopes, titles, bodies, prompts, arguments, memo text, real identity
strings or remote error text. Each started request is flushed/fsynced before
transmission. The test-only synthetic principal stays in the SQL receipt and
user-directed report, outside the operational events and model arguments.

The explicit JSON report/stdout export may contain the released memo and its
authorized quotes; it is not an operational trace. SQLite is a trusted authored
corpus containing restricted fixture text, not a redacted event log. Both remain
in ignored output directories. Captured requests are test-only in memory.

Double token counts are authored, not measured model usage. Reported Gateway
tokens across decoded responses are separate from the frozen controller's
accepted-turn token totals, which can omit an epoch-invalidated response.
Generation ID, estimated/actual cost and semantic groundedness stay null.
`unsafe_deliveries` stays null until independent disclosure inspection; a guard
reject count alone is not an unsafe-action rate.

The portfolio has INR1000 total authorization across ten repositories; this
worker has zero allocated inference. `--provider gateway` always fails with
`gateway_allocation_required` before output creation, credential loading or
network. No credentials are read, no environment is dumped, and no accounts,
keys, top-ups or ledger are created. U1 implements the native protocol through
local doubles only; production HTTPS binding/secure loading/pricing reservations
and positive allocation require a later main assignment. There is no fallback
to scripted mode on a Gateway error.

Narrative synthesis, Drive, fresh U2 datasets/baselines and U3 paid evaluation
are not implemented. No unseen-input or production-quality claim is made.

## Checks and provenance

Executed: `py -3.11 -m unittest discover -s tests -p "test_upgrade_gateway_units.py" -v`
passed 26 pure test methods on the first run. After extending cleanup to pipe
threads that never started, a new regression unit was added; the final receipt
records the 27-method check separately. They use fake queues/processes,
synthetic contexts and temporary metadata files: no sockets, real subprocesses
or complete actor workflows. The final three file-writing assertions were
checked again after restricting their temporary directories to ignored `work/`
inside the checkout. Prior OS-temporary fixtures were owned and removed by the
tests. Exact receipts live in the private worker upgrade
evidence and is not shipped in this repository.

Unexecuted lease checks:

```powershell
py -3.11 -m unittest discover -s tests -p "test_upgrade_gateway_transport.py" -v
py -3.11 -m unittest discover -s tests -v
py -3.11 -m evaluation.evaluate --verify-freeze
py -3.11 -m evaluation.upgrade.verify --source
```

The additive U1 source manifest retains the original published commit
`b515ac02bfd9fdb694cc7e285a1260ae49b0003e` and all v1 frozen file hashes.
It does not replace the old freeze or assert that the upgrade passed integration.
All new code/tests and reused fictional development fixtures use the repository
MIT license. Python and SQLite are existing runtimes; no new third-party package
or copied external corpus was added.
