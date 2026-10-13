"""Future owned loopback double. Construction requires a new native lease."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading

from permissioned_research.upgrade.config import DOUBLE_MODEL
from permissioned_research.upgrade.narrative.double import AuthoredPolicy
from permissioned_research.upgrade.narrative.provider import TOOLS
from permissioned_research.upgrade.transport import DoubleEndpoint
from .data import require, unique_json


@contextmanager
def suite_http_double(provider_fault=None,transport_fault=None):
    require(provider_fault in (None,'malformed_once','self_approve') and transport_fault in (None,401,429))
    policy=AuthoredPolicy(provider_fault); records=[]; failures=[]; stop=threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            try:
                self.connection.settimeout(1); length=int(self.headers.get('Content-Length','-1'))
                require(self.path=='/v1/chat/completions' and 0<=length<=49152)
                request=unique_json(self.rfile.read(length).decode())
                require(request['model']==DOUBLE_MODEL and request['tools']==TOOLS and request['stream'] is False and request['parallel_tool_calls'] is False and request['tool_choice']=='required')
                record={'request':request,'http_status':None,'body':None}; records.append(record)
                status=transport_fault or 200; body=b'{"error":"authored_transport_fault"}' if transport_fault else policy.respond(request)
                record.update(http_status=status,body=body.decode('utf-8'))
                self.send_response(status); self.send_header('Content-Type','application/json')
                self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            except (BrokenPipeError,ConnectionResetError):pass
            except (ValueError,TypeError,KeyError,OSError):
                failures.append('authored_double_invalid'); self.close_connection=True
    server=HTTPServer(('127.0.0.1',0),Handler); server.timeout=0.05
    def serve():
        while not stop.is_set():server.handle_request()
    thread=threading.Thread(target=serve,name='u2b2-owned-narrative-double',daemon=True); thread.start()
    try:yield DoubleEndpoint(server.server_port),records
    finally:
        stop.set(); thread.join(timeout=1.2); server.server_close()
        require(not thread.is_alive() and not failures,'native_double_cleanup_failed')
