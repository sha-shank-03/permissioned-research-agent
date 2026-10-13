"""Owned GET child, fixed verified TLS origin; explicit loopback tests only."""
import base64
from dataclasses import dataclass
import json
import queue
import subprocess
import sys
import threading
import time

from ...domain import strict_json_loads
from .contract import DriveError, GetRequest, Reply, UserCredential, require

DOUBLE_TOKEN = 'LOCAL_DRIVE_DOUBLE_NOT_A_CREDENTIAL'


@dataclass(frozen=True)
class LoopbackEndpoint:
    port: int
    host: str = '127.0.0.1'

    def __post_init__(self):
        require(self.host == '127.0.0.1' and type(self.port) is int and 1 <= self.port <= 65535)


class DriveHTTPTransport:
    """No proxies, redirects, retries, credential loader or environment lookup."""
    def __init__(self, *, test_endpoint=None, test_stall=None):
        require(test_endpoint is None or type(test_endpoint) is LoopbackEndpoint)
        require(test_stall in (None,'dns','tls','send'))
        require(test_stall is None or test_endpoint is not None)
        self.endpoint, self.stall = test_endpoint, test_stall
        self.evidence_kind = 'local_http_double' if test_endpoint else 'delegated_user_http'
        self.active, self.owned = None, []

    def get(self, request, credential, deadline, cancellation, cleanup_seconds):
        require(type(request) is GetRequest and type(credential) is UserCredential)
        require(self.active is None, 'drive_transport_error')
        require(0 < cleanup_seconds <= 1)
        require(not cancellation.is_set(), 'drive_cancelled')
        require(deadline > time.monotonic(), 'drive_timeout')
        if self.endpoint:
            require(credential.token == DOUBLE_TOKEN, 'drive_authentication_required')
        task = {'request': {'path':request.path,'params':request.params,'limit':request.limit,
                            'content_type':request.content_type},
                'token':credential.token, 'deadline':deadline, 'test_stall':self.stall,
                'test_port':self.endpoint.port if self.endpoint else None}
        wire = json.dumps(task, separators=(',',':')).encode('utf-8') + b'\n'
        require(len(wire) <= 16*1024)
        messages = queue.Queue(maxsize=2)
        try:
            process = subprocess.Popen([sys.executable,'-m','permissioned_research.upgrade.drive.wire'],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        except OSError:
            raise DriveError('drive_transport_error') from None

        def deliver(value):
            try:
                messages.put_nowait(value)
            except queue.Full:
                pass

        def write():
            try:
                process.stdin.write(wire)
                process.stdin.flush()
                process.stdin.close()
            except (OSError, ValueError):
                deliver({'error':'drive_transport_error'})

        def read():
            try:
                line = process.stdout.readline(96*1024+1)
                if not line or len(line) > 96*1024:
                    raise ValueError()
                deliver(strict_json_loads(line))
            except (ValueError,TypeError,UnicodeError,OSError):
                deliver({'error':'drive_transport_error'})

        reader = threading.Thread(target=read, name='drive-wire-read', daemon=True)
        writer = threading.Thread(target=write, name='drive-wire-write', daemon=True)
        owned = (process,reader,writer)
        self.owned.append(owned)
        self.active = owned
        try:
            reader.start()
            writer.start()
            while True:
                require(not cancellation.is_set(), 'drive_cancelled')
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'drive_timeout')
                try:
                    value = messages.get(timeout=min(0.025,remaining))
                except queue.Empty:
                    continue
                require(type(value) is dict, 'drive_transport_error')
                if set(value) == {'error'}:
                    require(value['error'] in ('drive_timeout','drive_schema_invalid','drive_transport_error'),
                            'drive_transport_error')
                    raise DriveError(value['error'])
                require(set(value) == {'status','body','content_type'}, 'drive_transport_error')
                require(type(value['status']) is int and 100 <= value['status'] <= 599
                        and type(value['content_type']) is str, 'drive_transport_error')
                try:
                    body = base64.b64decode(value['body'],validate=True)
                except (ValueError,TypeError):
                    raise DriveError('drive_transport_error') from None
                require(len(body) <= request.limit, 'drive_schema_invalid')
                return Reply(value['status'],body,value['content_type'])
        finally:
            try:
                self._close_owned(owned, cleanup_seconds)
            finally:
                self.active = None

    @staticmethod
    def _close_owned(owned, cleanup_seconds):
        process,reader,writer = owned
        began = time.monotonic()
        try:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=min(0.2,cleanup_seconds/4))
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=min(0.2,cleanup_seconds/4))
            except subprocess.TimeoutExpired:
                raise DriveError('drive_cleanup_failed') from None
        finally:
            for stream in (process.stdin,process.stdout):
                if stream:
                    stream.close()
            for thread in (reader,writer):
                if thread.ident is not None:
                    thread.join(timeout=max(0,min(0.1,cleanup_seconds-(time.monotonic()-began))))
        require(process.poll() is not None and not reader.is_alive() and not writer.is_alive(),
                'drive_cleanup_failed')

    def close(self):
        for owned in self.owned:
            self._close_owned(owned,1)

    @property
    def owned_children_remaining(self):
        return sum(p.poll() is None for p,_,_ in self.owned)

    @property
    def owned_threads_remaining(self):
        return sum(t.is_alive() for _,r,w in self.owned for t in (r,w))
