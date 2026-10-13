# U2-A Drive read-only source checkpoint

12 October 2026. This additive slice supplies the Google Drive v3 adapter and
authored contracts. It is not connected to the accepted U1 scalar controller,
a model, narrative synthesis, scoring or a live account. U1 candidate 4 and its
source manifest remain unchanged. No external Drive integration has run.

The 34 authored in-memory contract tests passed once on Python 3.11.9. The nine
native HTTP test methods are authored and syntax-inspected but unexecuted.
These checks establish local contract behavior; neither proves Google account
access, remote authorization, provider quality or production operation.

```powershell
# Pure contracts: no sockets/process transport, credentials or live calls.
py -3.11 -m unittest discover -s tests -p "test_upgrade_drive_contract.py" -v

# Proposed native local HTTP batch: execute only under a current named lease.
py -3.11 -m unittest discover -s tests -p "test_upgrade_drive_http.py" -v
```

## Authority and supported data

`OperatorScope` is trusted server/operator configuration for one of Alex, Priya
or Sam. It pins a verified Google account subject, one to three flat My Drive
folder IDs and one to twelve allowed file IDs with authored effective/review
dates. Neither model text nor returned URLs can select authority. The adapter
does not recurse into folders or follow shortcuts. Folder access cannot expand
the file allowlist. Shared drives, native Docs, exports, PDF and OCR are excluded.

An approved external loader must verify the actual delegated-user token's
subject/issuer binding before constructing `UserCredential`. The DTO alone is
not authentication proof. The adapter compares that binding with operator
configuration before any GET and every subsequent request, checks expiry and
requires exactly `drive.readonly`. Missing, mismatched, owner, service-account,
domain-delegated or broadened credentials fail closed. No loader, OAuth flow,
refresh-token persistence, environment lookup or account setup is implemented.
The token is excluded from representations and events and is passed to the
owned child through stdin, never argv. Loopback tests accept only an authored
placeholder; they cannot accept a real token.

Search constructs escaped bounded `fullText` terms, trusted parent, MIME and
trash filters, with explicit fields, `corpora=user` and `spaces=drive`. Search
returns untrusted titles with session-local handles and versions, without body
snippets or raw remote IDs. Incomplete search and pagination caps remain visible.
Only previously issued handles can be opened; IDs, URLs and borrowed handles
return generic unavailability without an HTTP request.

Read performs metadata -> `alt=media` -> metadata. Parent, MIME, download
capability, version, size and MD5 must agree; text decoding is strict UTF-8 with
no normalization. A snapshot binds the version, SHA256 of exact UTF-8 content,
Unicode text, projection epoch and trusted authored effective/review dates.
Drive `modifiedTime` is not fact freshness and is not used as authority.

Before each future provider disclosure and final handoff, the caller must invoke
`check_context()` over all retained search/read dependencies. No provider path
is supplied in U2-A. Denial, expiry, schema failure or version/parent/MIME drift
clears retained handles and text and increments the projection epoch. Counters,
retry allowance and deadline survive invalidation. There is no cross-run body
cache or stale-data fallback. Once returned, an immutable snapshot cannot be
recalled from a caller; it must not be reused after invalidation.

## Bounds and failure behavior

Defaults: 120 seconds total including a one-second cleanup reservation, ten
seconds per GET (configurable up to thirty), 180 attempts including rechecks,
five attempts/second, two pages of twenty-five results per folder, fifty results
per folder, 64 KiB JSON response, 32 KiB per text file and 256 KiB cumulative
content received. Bounds may be lowered, not raised beyond reviewed caps.

Only GET is sent, to fixed verified TLS `www.googleapis.com:443`. There is no
proxy inheritance, redirect following, arbitrary URL/header selection or write
method. An owned child bounds network work; the parent supervises cancellation
and deadline and terminates/waits/closes its child and pipe threads. Cleanup
failure remains a failure rather than an asserted success. Native behavior is
pending its separately leased HTTP batch; synthetic DNS/TLS/send stalls test
parent supervision only and do not prove real network failure handling.

401 is an authentication handoff. Permission 403 and 404 share generic denial.
Only documented quota 403 reasons, 429 and selected 5xx responses may retry safe
GETs, with at most two retries cumulatively in the whole session and bounded
exponential waits. Transport loss/timeouts do not retry. A rejected continuation
page may restart once with discarded partial results; repeated rejection stops.
Duplicate JSON keys, unexpected fields, malformed MIME/UTF-8, oversized or
truncated content, pagination loops and schema drift fail closed. Provider
messages, URLs, IDs, titles, queries, bodies and credentials never enter events.

## Genuine delegated-user integration prerequisites — unexecuted

Main must authorize a named external integration and supply a dedicated OAuth
client/consent setup, approved verified per-user credential loader and three
separate empty synthetic test users. `drive.readonly` is a restricted whole-user
scope, not a folder scope; consent, verification and any applicable data-transfer
assessment need review before use. Do not substitute the fixture owner or a
service account for any of the three users, or widen scopes after denial.

The fixture owner must separately provision/share twelve authored UTF-8 files
in three flat folders: general for all three, procurement for Priya, security
for Sam. Avoid a common shared parent because inherited access propagates.
Creation, sharing, replacement and revocation are owner/manual control-plane
actions outside actor tools and require their own assignment. Actual IDs,
account subjects and effective/review dates must be operator-pinned.

The future external batch must inspect actual project quota, confirm three-user
search/read/denied-handle behavior and observe owner-controlled revocation and
replacement. Record the delegated identity proof, HTTP outcome, content/version
hash, propagation delay and remaining race. No such actions, credentials,
downloads or account calls occurred in this slice. Local authored doubles do
not satisfy this gate.

Remote ACL changes cannot be atomically ordered with a later provider send or
caller handoff. Rechecks reduce stale exposure but leave an inter-request race,
permission propagation/index lag and already released snapshots. Current file
version/MD5 rechecks do not establish narrative entailment or exhaustive search.

## Official contracts checked on 12 October 2026

- [files.list](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/list): GET, explicit query/fields, continuation tokens and incomplete search.
- [files.get](https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get): GET metadata or stored-file `alt=media`; native Docs use export.
- [File resource](https://developers.google.com/workspace/drive/api/reference/rest/v3/files): string version changes with server mutations, parents, size/MD5 and per-user download capability.
- [Search](https://developers.google.com/workspace/drive/api/guides/search-files): escaped apostrophes/backslashes and parent/fullText filters.
- [Errors](https://developers.google.com/workspace/drive/api/guides/handle-errors): authentication, quota reasons, generic inaccessible/missing 404 and retry guidance.
- [Scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth): broad restricted `drive.readonly` and applicable verification/assessment requirements.
- [Sharing](https://developers.google.com/workspace/drive/api/guides/manage-sharing): parent permission propagation and per-user capabilities.
- [Quotas](https://developers.google.com/workspace/drive/api/guides/limits): method-specific quota units and project/user limits; inspect the real project's entitlement before integration.

All fixture content and tests are authored for this repository under its existing
license. This slice adds no dependency, SDK, framework or runtime migration.
