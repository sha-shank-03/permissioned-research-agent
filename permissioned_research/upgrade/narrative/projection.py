"""Trusted session projections over frozen local tools or the accepted Drive adapter."""
from contextlib import nullcontext
from dataclasses import asdict, dataclass
import hashlib

from ...domain import CONNECTORS, ResearchError, ResearchRequest
from ..tools import ScalarTools
from ..drive.adapter import DriveAdapter, TextSnapshot
from ..drive.contract import DriveError
from ..drive.http import DriveHTTPTransport


def require(condition, code='invalid_request'):
    if not condition:
        raise ResearchError(code)


@dataclass(frozen=True)
class Document:
    handle: str
    connector: str
    title: str
    revision: str
    content_sha256: str
    body: str
    effective_at: str
    review_until: str

    def current(self, as_of):
        return self.effective_at <= as_of <= self.review_until


class CorpusProjection:
    evidence_kind = 'local_acl_corpus'
    remote_authorization_proven = False

    def __init__(self, store, request):
        require(type(request) is ResearchRequest)
        self.store, self.request = store, request
        self.opened = {}
        self.tools = ScalarTools(store,request.principal,provider_id='research-narrative-v2')

    @property
    def principal(self):
        return self.request.principal

    def boundary(self):
        return self.store.boundary()

    def check_context(self):
        self.tools.check_context()

    def restrict_deadline(self, deadline):
        pass  # Local SQL gates retain their accepted bounded lock policy.

    def search(self, query, limit):
        require(type(query) is str and 1 <= len(query) <= 128 and not any(ord(c)<32 for c in query), 'invalid_decision')
        require(type(limit) is int and 1 <= limit <= 4, 'invalid_decision')
        # One logical search across the two existing ACL-filtered connectors.
        # Both narrative policies have the same backend fan-out and counters.
        hits = []
        for connector in CONNECTORS:
            for hit in self.tools.search(connector,query,limit)['hits']:
                source = self.tools.evidence[hit['handle']].source
                if source.subject == self.request.vendor:
                    hits.append({'handle':hit['handle'],'connector':connector,'title':source.title,
                                 'revision':str(source.revision),'effective_at':source.effective_at,
                                 'review_until':source.review_until})
        return {'status':'ok','hits':hits,'complete':False,'backend_search_calls':len(CONNECTORS)}

    def read(self, handle):
        result = self.tools.read(handle)
        if result['status'] != 'ok':
            return result
        source = self.tools.evidence[handle].source
        require(source.subject == self.request.vendor, 'source_state_changed')
        doc = Document(handle,source.connector,source.title,str(source.revision),
                       hashlib.sha256(source.body.encode('utf-8')).hexdigest(),source.body,
                       source.effective_at,source.review_until)
        self.opened[handle] = doc
        return {'status':'ok',**asdict(doc)}

    def selection_key(self, handle):
        return self.tools.evidence[handle].source.object_id

    def diversity_key(self, handle):
        return self.tools.evidence[handle].source.connector

    def clear(self):
        self.opened.clear()
        self.tools.clear()
        self.tools.caches.clear()
        self.tools = ScalarTools(self.store,self.request.principal,provider_id='research-narrative-v2')

    def close(self):
        self.clear()  # Store ownership stays with the caller.


class DriveProjection:
    """U2-B1 allows only an explicitly authored in-memory or native loopback adapter."""
    evidence_kind = 'local_drive_contract'
    remote_authorization_proven = False

    def __init__(self, adapter, request):
        require(type(adapter) is DriveAdapter and type(request) is ResearchRequest)
        require(adapter._scope.principal == request.principal)
        transport = adapter._transport
        if type(transport) is DriveHTTPTransport:
            require(transport.endpoint is not None)  # Production origin is disabled in this seam.
        else:
            require(getattr(transport,'evidence_kind',None) == 'authored_in_memory_contract'
                    and getattr(transport,'network_enabled',True) is False)
        self.adapter,self.request,self.opened = adapter,request,{}

    @property
    def principal(self):
        return self.request.principal

    def boundary(self):
        # This is deliberately not described as a remote ACL transaction.
        return nullcontext()

    def restrict_deadline(self, deadline):
        self.adapter.deadline = min(self.adapter.deadline,deadline)

    def check_context(self):
        try:
            self.adapter.check_context()
        except DriveError as error:
            self.opened.clear()
            if error.code in ('drive_not_available','drive_state_changed'):
                raise ResearchError('source_state_changed') from None
            raise

    def search(self, query, limit):
        require(type(limit) is int and 1 <= limit <= 4, 'invalid_decision')
        result = self.adapter.search(query)
        # retain/recheck every returned dependency even if only a bounded subset is shown
        hits = []
        for hit in result['hits'][:limit]:
            meta = self.adapter._handles[hit['handle']]
            policy = self.adapter._policies[meta.file_id]
            hits.append({'handle':hit['handle'],'connector':'google_drive','title':hit['title'],
                         'revision':hit['version'],'effective_at':policy.effective_at,
                         'review_until':policy.review_until})
        return {'status':'ok','hits':hits,'complete':result['complete'] and len(result['hits'])<=limit,
                'backend_search_calls':None}

    def read(self, handle):
        snapshot = self.adapter.read(handle)
        if type(snapshot) is not TextSnapshot:
            return snapshot
        meta = self.adapter._handles[handle]
        doc = Document(handle,'google_drive',meta.name,snapshot.version,snapshot.content_sha256,
                       snapshot.text,snapshot.effective_at,snapshot.review_until)
        self.opened[handle] = doc
        return {'status':'ok',**asdict(doc)}

    def selection_key(self, handle):
        return self.adapter._handles[handle].file_id

    def diversity_key(self, handle):
        return self.adapter._handles[handle].folder_id

    def clear(self):
        self.opened.clear()
        self.adapter._invalidate('drive_state_changed')

    def close(self):
        self.opened.clear()
        self.adapter.close()
