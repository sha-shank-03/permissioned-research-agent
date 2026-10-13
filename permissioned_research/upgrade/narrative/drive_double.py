"""Authored Drive GET fixture seam. Not OAuth or real Google authorization."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import json
import re
import threading
import time
from urllib.parse import parse_qs, urlsplit

from ..drive.adapter import DriveAdapter
from ..drive.contract import FilePolicy, OperatorScope, READONLY, Reply, UserCredential
from ..drive.http import DOUBLE_TOKEN, DriveHTTPTransport, LoopbackEndpoint
from .fixtures import demo_fixture
from .projection import require


class DriveFixture:
    def __init__(self, principal, case='normal'):
        fixture=demo_fixture(case)
        require(principal in fixture['principals'])
        self.principal,self.subject = principal,{'alex':'101','priya':'102','sam':'103'}[principal]
        self.groups=set(fixture['principals'][principal]); self.files={}; self.revoked=set()
        for source in fixture['sources']:
            folder='procurement' if source['allow_groups']==['procurement'] else 'general'
            self.files[source['object_id']]={**source,'folder':folder,'version':str(source['revision'])}
        allowed=[source for source in self.files.values() if self.groups.intersection(source['allow_groups'])]
        self.scope=OperatorScope(principal,self.subject,tuple(sorted({s['folder'] for s in allowed})),
                                 tuple(FilePolicy(s['object_id'],s['folder'],s['effective_at'],s['review_until']) for s in allowed))

    def revoke(self, file_id):
        self.revoked.add(file_id)

    def replace(self, file_id, body):
        s=self.files[file_id]; s['body']=body; s['version']=str(int(s['version'])+1)

    def metadata(self, source):
        body=source['body'].encode('utf-8')
        return {'id':source['object_id'],'name':source['title'],'mimeType':'text/plain',
                'parents':[source['folder']],'trashed':False,'version':source['version'],
                'size':str(len(body)),'md5Checksum':hashlib.md5(body).hexdigest(),
                'capabilities':{'canDownload':True}}

    def answer(self, path, params):
        if path=='/drive/v3/files':
            q=params.get('q',''); parent=re.search(r"'([A-Za-z0-9_-]+)' in parents",q)
            require(parent is not None)
            terms=re.findall(r"fullText contains '((?:\\.|[^'])*)'",q)
            terms=[t.replace("\\'","'").replace('\\\\','\\').casefold() for t in terms]
            rows=[]
            for source in self.files.values():
                if (source['folder']==parent.group(1) and source['object_id'] not in self.revoked
                        and self.groups.intersection(source['allow_groups'])
                        and all(t in (source['title']+' '+source['body']).casefold() for t in terms)):
                    rows.append(self.metadata(source))
            return Reply(200,json.dumps({'files':rows[:int(params.get('pageSize','25'))],
                                        'incompleteSearch':False}).encode())
        file_id=path.removeprefix('/drive/v3/files/')
        source=self.files.get(file_id)
        if source is None or file_id in self.revoked or not self.groups.intersection(source['allow_groups']):
            return Reply(404,b'{"error":{"message":"authored unavailable"}}')
        if params=={'alt':'media'}:
            return Reply(200,source['body'].encode('utf-8'),'text/plain')
        return Reply(200,json.dumps(self.metadata(source)).encode())


class FixtureDriveTransport:
    evidence_kind='authored_in_memory_contract'
    network_enabled=False
    def __init__(self, fixture):
        self.fixture,self.calls,self.closed=fixture,[],False
    def get(self, request, credential, deadline, cancel, cleanup):
        require(credential.subject==self.fixture.subject and credential.principal==self.fixture.principal)
        self.calls.append((request.path,dict(request.params)))
        return self.fixture.answer(request.path,dict(request.params))
    def close(self):
        self.closed=True


def memory_drive_adapter(principal, case='normal', *, clock=time.monotonic, cancellation=None):
    fixture=DriveFixture(principal,case)
    credential=UserCredential(principal,fixture.subject,DOUBLE_TOKEN,time.time()+300,frozenset({READONLY}))
    adapter=DriveAdapter(fixture.scope,credential,FixtureDriveTransport(fixture),clock=clock,cancellation=cancellation)
    return adapter,fixture


@contextmanager
def drive_http_double(principal, case='normal'):
    """Native authored server; create only under a currently assigned lease."""
    fixture=DriveFixture(principal,case); calls=[]; failures=[]; stop=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):
            pass
        def do_GET(self):
            try:
                require(self.headers.get('Authorization')=='Bearer '+DOUBLE_TOKEN)
                url=urlsplit(self.path); query=parse_qs(url.query,strict_parsing=True)
                require(all(len(v)==1 for v in query.values()))
                calls.append(('GET',url.path))
                response=fixture.answer(url.path,{k:v[0] for k,v in query.items()})
                self.send_response(response.status); self.send_header('Content-Type',response.content_type)
                self.send_header('Content-Length',str(len(response.body))); self.end_headers(); self.wfile.write(response.body)
            except (BrokenPipeError,ConnectionResetError):
                pass
            except Exception:
                failures.append('authored_get_invalid'); self.close_connection=True
    server=HTTPServer(('127.0.0.1',0),Handler); server.timeout=0.05
    def serve():
        while not stop.is_set():
            server.handle_request()
    thread=threading.Thread(target=serve,name='narrative-drive-double',daemon=True); thread.start()
    credential=UserCredential(principal,fixture.subject,DOUBLE_TOKEN,time.time()+300,frozenset({READONLY}))
    transport=DriveHTTPTransport(test_endpoint=LoopbackEndpoint(server.server_port))
    adapter=DriveAdapter(fixture.scope,credential,transport)
    try:
        yield adapter,fixture,calls
    finally:
        try:
            adapter.close()
        finally:
            stop.set(); thread.join(timeout=1.2); server.server_close()
        require(not thread.is_alive() and not failures,'provider_error')
