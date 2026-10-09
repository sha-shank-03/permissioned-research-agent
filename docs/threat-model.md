# Threat model and tradeoffs

The trusted local CLI binds an identity once. SQLite owns ACLs, current revisions and epoch state. Untrusted source prose and provider decisions cannot supply a different identity, URL, shell command or filesystem path as authority. Tools are a closed plan/search/read/finish protocol. This project simulates DocsHub and TeamChat; it has no production identity provider or enterprise connection.

The principal threat is a provider retrieving, retaining or releasing restricted or superseded evidence through search ranking, snippets, opened bodies, citations, memo caches or operational logs. Revocation/version commits may occur between tool calls, during response collection or before final handoff. Prompt injection is treated as source data. The controller reauthorizes all observed dependencies and gates typed facts and citations. Operational persistence is metadata only; the trusted corpus intentionally contains restricted fictional bytes.

Ordering is defined at the committed local boundary. SQL ACL filtering precedes ranking. Provider submission and caller handoff hold short write transactions; inference collection does not hold the database lock. A change invalidates the entire observed context, and one restart clears current handles, observations and both caches. A result already handed to a caller remains a lawful released snapshot. Clearing references is not secure erasure and cannot recall earlier outputs.

| Choice | Benefit | Limit |
| --- | --- | --- |
| Python 3.11 standard library and SQLite | Small reproducible local system, no pip/model dependency | One database/process owner; no distributed service or real login |
| Global epoch for all source/ACL changes | Simple conservative invalidation, including unopened snippets | Unrelated changes invalidate useful research; unsuitable for large corpora without refinement |
| Object ACLs and channel ACLs | Different connector access rules with three nonhierarchical identities | Flat authored groups; no nested production directory or policy engine |
| Exact four-topic scalar grammar | Deterministic typed values, spans and falsifiable oracle | No general prose entailment or free-form research judgment |
| Shared caches scoped by namespace/identity/epoch/policy/provider | Useful reauthorization and isolation tests | Process-local memory, bounded TTL; no disk cache or erasure guarantee |
| Metadata-only attempt receipts and OS ownership lock | Detect active owner, recover abandoned attempt, preserve immutable history | Fresh research after recovery; no saved reasoning context or exactly-once inference |
| Frozen scripted and retrieval baseline | Repeatable policy comparison under the same safety gates | No live-model quality, reasoning advantage or external validity demonstrated |

The local process, fixture author, administrator, provider executable and evaluation host are trusted. A hostile plugin executable can access this process's files/memory; this is not a sandbox against that adversary. SQLite writer privileges are trusted control-plane privileges. Side channels, remote compromise, source-truth verification, confidential report exports and distributed authorization propagation are outside scope. The optional HTTP adapter permits explicit numeric loopback only and has been tested with an owned wire double; actual Ollama/server/model compatibility remains unexecuted.
