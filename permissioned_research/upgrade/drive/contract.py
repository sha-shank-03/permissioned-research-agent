"""Trusted operator scope and closed Google Drive v3 request contracts."""
from dataclasses import dataclass, field
from datetime import date
import math
import re
from urllib.parse import urlencode

from ...domain import strict_json_loads

READONLY = 'https://www.googleapis.com/auth/drive.readonly'
FIELDS = 'id,name,mimeType,parents,trashed,version,size,md5Checksum,capabilities(canDownload)'
LIST_FIELDS = 'nextPageToken,incompleteSearch,files(' + FIELDS + ')'
ID = re.compile(r'[A-Za-z0-9_-]{1,128}\Z')


class DriveError(Exception):
    CODES = frozenset({'drive_invalid_request', 'drive_authentication_required',
                       'drive_not_available', 'drive_state_changed', 'drive_schema_invalid',
                       'drive_budget_exhausted', 'drive_timeout', 'drive_cancelled',
                       'drive_transport_error', 'drive_rate_limited', 'drive_unavailable',
                       'drive_page_rejected', 'drive_cleanup_failed'})

    def __init__(self, code):
        if code not in self.CODES:
            raise ValueError('Unknown Drive error')
        self.code = code
        super().__init__(code)


def require(condition, code='drive_invalid_request'):
    if not condition:
        raise DriveError(code)


def valid_id(value):
    return type(value) is str and ID.fullmatch(value) is not None


@dataclass(frozen=True)
class FilePolicy:
    file_id: str
    folder_id: str
    effective_at: str
    review_until: str

    def __post_init__(self):
        require(valid_id(self.file_id) and valid_id(self.folder_id))
        try:
            dates = [date.fromisoformat(v) for v in (self.effective_at, self.review_until)]
            require([v.isoformat() for v in dates] == [self.effective_at, self.review_until])
            require(dates[0] <= dates[1])
        except (ValueError, TypeError):
            raise DriveError('drive_invalid_request') from None


@dataclass(frozen=True)
class OperatorScope:
    """Construct only from trusted server/operator configuration, never tool arguments."""
    principal: str
    account_subject: str
    folders: tuple[str, ...]
    files: tuple[FilePolicy, ...]

    def __post_init__(self):
        require(self.principal in ('alex', 'priya', 'sam'))
        require(type(self.account_subject) is str and re.fullmatch(r'[0-9]{1,128}', self.account_subject))
        require(type(self.folders) is tuple and 1 <= len(self.folders) <= 3)
        require(all(valid_id(f) for f in self.folders) and len(set(self.folders)) == len(self.folders))
        require(type(self.files) is tuple and 1 <= len(self.files) <= 12)
        require(all(type(f) is FilePolicy and f.folder_id in self.folders for f in self.files))
        require(len({f.file_id for f in self.files}) == len(self.files))


@dataclass(frozen=True)
class UserCredential:
    """An approved loader must verify token/subject binding before constructing this DTO.

    This DTO does not authenticate a token itself. No loader, OAuth flow, owner
    fallback, refresh, environment lookup or credential storage is supplied.
    """
    principal: str
    subject: str
    token: str = field(repr=False)
    expires_at: float
    scopes: frozenset[str]
    issuer: str = 'https://accounts.google.com'
    kind: str = 'delegated_user'

    def validate(self, scope, wall_now):
        require(type(scope) is OperatorScope, 'drive_authentication_required')
        require(self.kind == 'delegated_user' and self.issuer == 'https://accounts.google.com',
                'drive_authentication_required')
        require(self.principal == scope.principal and self.subject == scope.account_subject,
                'drive_authentication_required')
        require(type(self.scopes) is frozenset and self.scopes == frozenset({READONLY}),
                'drive_authentication_required')
        require(type(self.expires_at) in (int, float) and math.isfinite(self.expires_at)
                and self.expires_at > wall_now + 1, 'drive_authentication_required')
        require(type(self.token) is str and re.fullmatch(r'[\x21-\x7e]{1,4096}', self.token),
                'drive_authentication_required')


@dataclass(frozen=True)
class Limits:
    total_seconds: float = 120
    request_seconds: float = 10
    cleanup_seconds: float = 1
    max_attempts: int = 180
    max_pages: int = 2
    page_size: int = 25
    max_results_per_folder: int = 50
    max_response_bytes: int = 64 * 1024
    max_file_bytes: int = 32 * 1024
    max_content_bytes: int = 256 * 1024
    max_get_retries: int = 2
    requests_per_second: int = 5

    def __post_init__(self):
        for name, cap in {'total_seconds':120, 'request_seconds':30, 'cleanup_seconds':1}.items():
            v = getattr(self, name)
            require(type(v) in (float, int) and math.isfinite(v) and 0 < v <= cap)
        require(self.cleanup_seconds < self.total_seconds)
        for name, cap in {'max_attempts':180, 'max_pages':2, 'page_size':25,
                          'max_results_per_folder':50, 'max_response_bytes':65536,
                          'max_file_bytes':32768, 'max_content_bytes':262144,
                          'requests_per_second':5}.items():
            v = getattr(self, name)
            require(type(v) is int and 1 <= v <= cap)
        require(type(self.max_get_retries) is int and 0 <= self.max_get_retries <= 2)


def search_terms(query):
    require(type(query) is str)
    try:
        require(0 < len(query.encode('utf-8')) <= 512)
    except UnicodeError:
        raise DriveError('drive_invalid_request') from None
    terms = query.split()
    require(1 <= len(terms) <= 8 and all(len(t) <= 64 for t in terms))
    require(not any(ord(c) < 32 or ord(c) == 127 for c in query))
    return tuple(t.replace('\\', '\\\\').replace("'", "\\'") for t in terms)


@dataclass(frozen=True)
class GetRequest:
    """No method, host, URL, header or credential selector enters tool arguments."""
    path: str
    params: tuple[tuple[str, str], ...]
    limit: int
    content_type: str

    def __post_init__(self):
        require(type(self.path) is str and re.fullmatch(r'/drive/v3/files(?:/[A-Za-z0-9_-]{1,128})?', self.path))
        require(type(self.params) is tuple and all(type(p) is tuple and len(p) == 2 and
                all(type(v) is str for v in p) for p in self.params))
        q = dict(self.params)
        require(len(q) == len(self.params) and sum(len(k)+len(v) for k,v in self.params) <= 4096)
        if self.path == '/drive/v3/files':
            require(set(q) in ({'q','fields','spaces','corpora','pageSize'},
                               {'q','fields','spaces','corpora','pageSize','pageToken'}))
            require(q['fields'] == LIST_FIELDS and q['spaces'] == 'drive' and q['corpora'] == 'user')
            require(q['pageSize'].isdigit() and 1 <= int(q['pageSize']) <= 25)
            require(len(q.get('pageToken','')) <= 2048)
            require(self.content_type == 'application/json')
        else:
            require(q == {'fields': FIELDS} or q == {'alt': 'media'})
            require(self.content_type == ('text/plain' if q == {'alt':'media'} else 'application/json'))
        require(type(self.limit) is int and 1 <= self.limit <= 65536)

    @property
    def target(self):
        return self.path + '?' + urlencode(self.params)


@dataclass(frozen=True)
class Reply:
    status: int
    body: bytes = field(repr=False)
    content_type: str = 'application/json'


@dataclass(frozen=True)
class Metadata:
    file_id: str
    name: str
    folder_id: str
    version: str
    size: int
    md5: str


def json_body(reply, limit):
    require(type(reply.body) is bytes and len(reply.body) <= limit, 'drive_schema_invalid')
    require(reply.content_type == 'application/json', 'drive_schema_invalid')
    try:
        return strict_json_loads(reply.body.decode('utf-8', errors='strict'))
    except (ValueError, TypeError, UnicodeError):
        raise DriveError('drive_schema_invalid') from None


def metadata(value, policy, limits):
    code = 'drive_schema_invalid'
    require(type(value) is dict and set(value) == set(FIELDS.replace('capabilities(canDownload)', 'capabilities').split(',')), code)
    require(value['id'] == policy.file_id and value['parents'] == [policy.folder_id], 'drive_not_available')
    require(value['mimeType'] == 'text/plain' and value['trashed'] is False, 'drive_not_available')
    require(type(value['capabilities']) is dict and set(value['capabilities']) == {'canDownload'}
            and value['capabilities']['canDownload'] is True, 'drive_not_available')
    require(type(value['name']) is str, code)
    try:
        require(0 < len(value['name'].encode('utf-8')) <= 512, code)
    except UnicodeError:
        raise DriveError(code) from None
    require(type(value['version']) is str and re.fullmatch(r'[1-9][0-9]{0,18}', value['version']), code)
    require(int(value['version']) <= 2**63-1, code)
    require(type(value['size']) is str and re.fullmatch(r'0|[1-9][0-9]{0,8}', value['size']), code)
    require(int(value['size']) <= limits.max_file_bytes, 'drive_budget_exhausted')
    require(type(value['md5Checksum']) is str and re.fullmatch(r'[0-9a-f]{32}', value['md5Checksum']), code)
    return Metadata(value['id'], value['name'], policy.folder_id, value['version'], int(value['size']), value['md5Checksum'])
