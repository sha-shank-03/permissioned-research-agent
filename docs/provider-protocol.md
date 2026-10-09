# Decision provider protocol: research-action-v1

The actor accepts one strict JSON action per response. `DecisionProvider.decide(context)` is adapted to `submit(context, deadline)` and `collect(pending, deadline)`. For scripted policies, submit only retains a defensive context copy; inference/policy execution occurs during collect outside the SQLite transaction. `pending.close()` drops owned context or closes the HTTP connection.

```json
{
  "protocol": "research-action-v1",
  "action": "search",
  "arguments": {"connector": "docshub", "query": "CedarBridge", "limit": 4}
}
```

The context contains vendor/date, phase, declared tool descriptions, previously authorized observations, remaining decisions and an optional allowlisted error. It contains no principal authority, request key/binding hash, attempt persistence tool, SQLite connection, fixture/scenario ID, evaluator labels or control-plane tools. Interrupted recovery starts a new decision context under current permissions; it cannot load a saved prompt or memo.

| Action | Required fields |
| --- | --- |
| `plan` | `topics`: all four topic names in declared order |
| `revise_plan` | Same scope, at most once per context |
| `search` | Connector enum, query length 1-128, integer limit 1-4 |
| `read` | A fresh opaque session `e_` handle |
| `finish` | Four topic proposals with topic, state, reason and claims |

A claim has exactly `value`, `handle`, `start`, `end`. Supported/conflict/stale states cite opened evidence; unknown has no claims. States/reasons are enums and the overall outcome is derived by the controller. Duplicate JSON keys, nonfinite numbers, unknown fields/tools, booleans used as integers, forged handles, wrong spans, inaccessible revisions and oversize responses fail closed. One malformed-action/memo repair may receive only an error code, never the malformed body. Raw prose/thinking are not persisted or returned.

## Optional HTTP route

`OllamaWireProvider` sends `POST /api/chat` with one stateless system/user context, native closed tool schemas and `stream:false`. It accepts exactly one assistant tool call in a completed JSON response, validates optional nonnegative integer usage counts and converts the function into the same action envelope. It discards assistant content/thinking.

The route requires explicit `enable_network=True`, a numeric loopback HTTP endpoint without credentials, path, query or fragment, and a purpose of `contract_stub` or `authorized_local`. Cloud model names are rejected. The CLI exposes only `--provider scripted`; there is no implicit fallback or model download. `authorized_local` is an unexecuted extension point requiring separately authorized server/model setup. Wire compatibility with a real server and model quality are unknown.

The two-second total request-send budget is enforced under the authorization transaction; response collection occurs outside it with a 20-second/global deadline, 64 KiB cap, JSON media-type/status checks and declared-length checks. No redirects or proxies are followed. Contract-stub responses must include the project test-double protocol header. A timeout is a typed failure and never automatically resubmitted; a server may already have acted on bytes sent before a timeout.

`evaluation.protocol_server` owns an ephemeral loopback server and uses the scripted policy to return Ollama-shaped tool responses. Its counts 11/7 are invented protocol fixtures, not observed model usage. Evidence kind is `http_contract_stub` and `live_model_executed:false`. Four actual HTTP methods passed locally, covering multi-request continuation, identity filtering and invalid status/media-type/JSON/size/closed-connection failures. Expected Windows pipe/reset/aborted peer-close conditions are handled by the owned double; other errors still propagate. The actor tracks live response receipt separately so a route configured but never collected does not claim execution; a successful wire response does not establish model quality.

## Scripted policy and baseline

The scripted policy reads newly observed handles, searches both connectors and performs follow-up retrieval for visible gaps/disagreements. It extracts the explicit fact-line grammar without reading fixtures or labels directly. This is deterministic observation-driven workflow policy, not a recorded model trace or evidence of model reasoning.

The development baseline uses the same guard and grammar with one vendor query/top-one result per connector, no adaptive follow-up. All decisions remain scripted. M1 full workflows were accepted; M2 worker integration passed, including actual loopback protocol traffic and 12 scripted versus 12 baseline development outcomes. Held-out scoring and live-model outcomes remain unexecuted.
