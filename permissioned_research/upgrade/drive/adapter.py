"""Session-only Drive projection; remote ACL checks remain non-atomic snapshots."""
from dataclasses import dataclass, field
import hashlib
import threading
import time
import uuid

from .contract import (DriveError, FIELDS, LIST_FIELDS, FilePolicy, GetRequest,
                       Limits, OperatorScope, Reply, UserCredential, json_body,
                       metadata, require, search_terms, valid_id)


@dataclass(frozen=True)
class TextSnapshot:
    handle: str
    version: str
    content_sha256: str
    text: str = field(repr=False)
    effective_at: str
    review_until: str
    epoch: int


class DriveAdapter:
    """One trusted principal, one deadline, no cross-run cache or scalar wiring.

    The caller must run check_context before every disclosure/final handoff.
    This source-only slice offers no provider send or narrative entry point.
    """
    def __init__(self, scope, credential, transport, *, limits=None, cancellation=None,
                 clock=time.monotonic, wall_clock=time.time):
        require(type(scope) is OperatorScope)
        require(type(credential) is UserCredential, 'drive_authentication_required')
        credential.validate(scope, wall_clock())
        self._scope, self._credential, self._transport = scope, credential, transport
        self.limits = limits if limits is not None else Limits()
        require(type(self.limits) is Limits)
        self.cancellation = cancellation if cancellation is not None else threading.Event()
        self.clock, self.wall_clock = clock, wall_clock
        self.deadline = clock() + self.limits.total_seconds
        self._policies = {p.file_id:p for p in scope.files}
        self._handles, self._opened = {}, {}
        self.epoch, self.attempts, self.retries, self.content_bytes = 0, 0, 0, 0
        self._last_attempt = None
        self.events, self.closed = [], False

    def _check(self):
        require(not self.closed, 'drive_not_available')
        require(not self.cancellation.is_set(), 'drive_cancelled')
        require(self.clock() < self.deadline - self.limits.cleanup_seconds, 'drive_timeout')
        self._credential.validate(self._scope, self.wall_clock())

    def _wait(self, seconds):
        self._check()
        require(self.clock() + seconds < self.deadline - self.limits.cleanup_seconds, 'drive_timeout')
        if self.cancellation.wait(seconds):
            raise DriveError('drive_cancelled')
        self._check()

    def _invalidate(self, code):
        self.epoch += 1
        self._handles.clear()
        self._opened.clear()
        # Enum/counters only: never persist IDs, names, queries, body, headers or credentials.
        self.events.append({'event':'invalidated', 'code':code, 'epoch':self.epoch})
        self.events = self.events[-256:]

    def _get(self, request):
        while True:
            self._check()
            require(self.attempts < self.limits.max_attempts, 'drive_budget_exhausted')
            if self._last_attempt is not None:
                self._wait(max(0, 1/self.limits.requests_per_second - (self.clock()-self._last_attempt)))
            self._last_attempt = self.clock()
            self.attempts += 1
            self.events.append({'event':'get_started', 'attempt':self.attempts})
            self.events = self.events[-256:]
            active = min(self.deadline - self.limits.cleanup_seconds,
                         self.clock() + self.limits.request_seconds)
            reply = self._transport.get(request, self._credential, active,
                                        self.cancellation, self.limits.cleanup_seconds)
            self._check()
            require(type(reply) is Reply and type(reply.status) is int and 100 <= reply.status <= 599,
                    'drive_schema_invalid')
            require(type(reply.body) is bytes and len(reply.body) <= request.limit, 'drive_schema_invalid')
            if dict(request.params) == {'alt':'media'}:
                self.content_bytes += len(reply.body)
                require(self.content_bytes <= self.limits.max_content_bytes, 'drive_budget_exhausted')
            if reply.status == 200:
                require(reply.content_type == request.content_type, 'drive_schema_invalid')
                return reply
            if reply.status == 401:
                raise DriveError('drive_authentication_required')
            quota = False
            if reply.status == 403:
                # Inspect only bounded, documented reason enums; discard all provider messages.
                try:
                    error = json_body(reply, request.limit)
                    reasons = error['error']['errors']
                    quota = type(reasons) is list and 0 < len(reasons) <= 8 and all(
                        type(r) is dict and r.get('reason') in ('rateLimitExceeded','userRateLimitExceeded')
                        for r in reasons)
                except (DriveError, KeyError, TypeError):
                    quota = False
            retryable = quota or reply.status in (429,500,502,503,504)
            if retryable:
                if self.retries >= self.limits.max_get_retries:
                    raise DriveError('drive_rate_limited' if quota or reply.status == 429 else 'drive_unavailable')
                self.retries += 1
                self.events.append({'event':'get_retry', 'retry':self.retries})
                self._wait(0.2 * (2 ** (self.retries-1)))
                continue
            if reply.status in (403,404):
                raise DriveError('drive_not_available')
            if reply.status == 400 and 'pageToken' in dict(request.params):
                raise DriveError('drive_page_rejected')
            raise DriveError('drive_schema_invalid')

    def _metadata(self, policy):
        request = GetRequest('/drive/v3/files/'+policy.file_id, (('fields',FIELDS),),
                             self.limits.max_response_bytes, 'application/json')
        return metadata(json_body(self._get(request), request.limit), policy, self.limits)

    def search(self, query):
        try:
            self._check()
            terms = search_terms(query)
            found, complete, restarted = {}, True, False
            for folder in self._scope.folders:
                while True:
                    page_token, seen_tokens, folder_found = None, set(), {}
                    folder_complete = True
                    try:
                        for page in range(self.limits.max_pages):
                            q = "'"+folder+"' in parents and trashed = false and mimeType = 'text/plain'"
                            q += ''.join(" and fullText contains '"+t+"'" for t in terms)
                            params = [('q',q),('fields',LIST_FIELDS),('spaces','drive'),
                                      ('corpora','user'),('pageSize',str(self.limits.page_size))]
                            if page_token is not None:
                                params.append(('pageToken',page_token))
                            request = GetRequest('/drive/v3/files', tuple(params),
                                                 self.limits.max_response_bytes, 'application/json')
                            value = json_body(self._get(request), request.limit)
                            require(type(value) is dict and set(value) <= {'files','nextPageToken','incompleteSearch'}
                                    and 'files' in value, 'drive_schema_invalid')
                            require(type(value['files']) is list and len(value['files']) <= self.limits.page_size,
                                    'drive_schema_invalid')
                            require(type(value.get('incompleteSearch',False)) is bool, 'drive_schema_invalid')
                            folder_complete &= not value.get('incompleteSearch',False)
                            for raw in value['files']:
                                require(type(raw) is dict and valid_id(raw.get('id')), 'drive_schema_invalid')
                                policy = self._policies.get(raw['id'])
                                if policy is None:
                                    continue  # Folder access does not expand the trusted file allowlist.
                                require(policy.folder_id == folder, 'drive_not_available')
                                meta = metadata(raw, policy, self.limits)
                                require(meta.file_id not in folder_found or folder_found[meta.file_id] == meta,
                                        'drive_state_changed')
                                folder_found[meta.file_id] = meta
                            require(len(folder_found) <= self.limits.max_results_per_folder, 'drive_budget_exhausted')
                            page_token = value.get('nextPageToken')
                            if page_token is None:
                                break
                            require(type(page_token) is str and 0 < len(page_token) <= 2048
                                    and page_token not in seen_tokens, 'drive_schema_invalid')
                            seen_tokens.add(page_token)
                        if page_token is not None:
                            folder_complete = False
                        found.update(folder_found)
                        complete &= folder_complete
                        break
                    except DriveError as error:
                        if error.code != 'drive_page_rejected' or restarted:
                            raise
                        restarted = True  # One clean restart; no partial handles escaped.
            # Revalidate older snapshots, then publish this search's handles as one projection.
            self._context()
            for file_id, meta in found.items():
                for retained in self._handles.values():
                    require(retained.file_id != file_id or retained == meta, 'drive_state_changed')
            hits = []
            for meta in found.values():
                handle = next((h for h,m in self._handles.items() if m == meta), None)
                if handle is None:
                    handle = 'd_' + uuid.uuid4().hex
                    self._handles[handle] = meta
                hits.append({'handle':handle,'title':meta.name,'version':meta.version})
            return {'status':'ok','hits':hits,'complete':complete,'pagination_restarted':restarted,
                    'evidence_kind':self._transport.evidence_kind}
        except DriveError as error:
            self._invalidate(error.code)
            raise

    def read(self, handle):
        try:
            self._check()
            if type(handle) is not str or handle not in self._handles:
                return {'status':'not_available'}
            retained = self._handles[handle]
            policy = self._policies[retained.file_id]
            before = self._metadata(policy)
            require(before == retained, 'drive_state_changed')
            request = GetRequest('/drive/v3/files/'+policy.file_id, (('alt','media'),),
                                 self.limits.max_file_bytes, 'text/plain')
            body = self._get(request).body
            after = self._metadata(policy)
            require(after == before, 'drive_state_changed')
            require(len(body) == before.size and hashlib.md5(body).hexdigest() == before.md5,
                    'drive_state_changed')
            try:
                text = body.decode('utf-8', errors='strict')
            except UnicodeError:
                raise DriveError('drive_schema_invalid') from None
            snapshot = TextSnapshot(handle, before.version, hashlib.sha256(body).hexdigest(), text,
                                    policy.effective_at, policy.review_until, self.epoch)
            self._opened[handle] = snapshot
            return snapshot
        except DriveError as error:
            self._invalidate(error.code)
            raise

    def check_context(self):
        try:
            self._context()
        except DriveError as error:
            self._invalidate(error.code)
            raise

    def _context(self):
        self._check()
        for retained in self._handles.values():
            require(self._metadata(self._policies[retained.file_id]) == retained, 'drive_state_changed')

    def close(self):
        try:
            self._transport.close()
        finally:
            self.closed = True
            self._handles.clear()
            self._opened.clear()
