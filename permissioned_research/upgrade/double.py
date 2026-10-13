"""Owned, authored Gateway HTTP double. Construct only under an integration lease."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import socket
import threading

from ..domain import Action, ResearchError, strict_json_loads
from ..providers import ScriptedResearchProvider
from .config import DOUBLE_MODEL
from .protocol import TOOLS, canonical, context_from_messages
from .transport import DoubleEndpoint


def completion(name, arguments, identifier='call_1', *, usage=True):
    result = {'id': 'authored_double_response', 'model': DOUBLE_MODEL,
              'choices': [{'index': 0, 'finish_reason': 'tool_calls',
                           'message': {'role': 'assistant', 'content': None,
                                       'tool_calls': [{'id': identifier, 'type': 'function', 'function':
                                                       {'name': name, 'arguments': canonical(arguments)}}]}}]}
    if usage:
        result['usage'] = {'prompt_tokens': 11, 'completion_tokens': 7, 'total_tokens': 18}
    return canonical(result).encode()


@contextmanager
def gateway_double(*, fault=None, handler=None):
    """Captured requests remain in memory; no scenario, fixture, SQL or labels."""
    if fault not in (None, 401, 402, 403, 429, 500, 'drop', 'headers', 'body', 'oversize', 'malformed', 'false_finish'):
        raise ResearchError('invalid_request')
    captured, failures = [], []
    stop = threading.Event()
    policy = ScriptedResearchProvider()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def log_message(self, *args):
            pass

        def do_POST(self):
            try:
                self.connection.settimeout(1)
                length = int(self.headers.get('Content-Length', '-1'))
                if self.path != '/v1/chat/completions' or not 0 <= length <= 49152:
                    raise ValueError()
                request = strict_json_loads(self.rfile.read(length))
                if (request['model'] != DOUBLE_MODEL or request['tools'] != TOOLS or
                        request['tool_choice'] != 'required' or request['parallel_tool_calls'] is not False or
                        request['stream'] is not False):
                    raise ValueError()
                captured.append(request)
                if fault == 'drop':
                    self.connection.shutdown(socket.SHUT_RDWR)
                    return
                if fault == 'headers' and stop.wait(3):
                    return
                if type(fault) is int:
                    status, body = fault, b'UNTRUSTED_REMOTE_ERROR_CANARY'
                elif fault == 'malformed':
                    status, body = 200, b'{"choices":[]}'
                elif fault == 'oversize':
                    status, body = 200, b'x' * 65537
                else:
                    context = context_from_messages(request)
                    action = Action.parse(policy.decide(context))
                    if fault == 'false_finish' and action.name == 'finish':
                        # Valid scalar shape, deliberately unsupported proposal.
                        for topic in action.arguments['topics']:
                            if topic['topic'] == 'onboarding_days' and topic['claims']:
                                topic['claims'][0]['value'] = 999
                    status, body = 200, completion(action.name, action.arguments, 'call_' + str(len(captured)))
                if handler is not None:
                    status, body = handler(request, len(captured), (status, body))
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if fault == 'body':
                    for octet in body:
                        if stop.wait(0.1):
                            break
                        self.wfile.write(bytes([octet]))
                        self.wfile.flush()
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass  # Expected client cancellation/deadline in fault cases.
            except (ResearchError, ValueError, TypeError, KeyError, OSError):
                failures.append('double_request_invalid')
                self.close_connection = True

    server = HTTPServer(('127.0.0.1', 0), Handler)
    server.timeout = 0.05

    def serve():
        while not stop.is_set():
            server.handle_request()

    thread = threading.Thread(target=serve, name='research-gateway-double', daemon=True)
    thread.start()
    try:
        yield DoubleEndpoint(server.server_port), captured
    finally:
        stop.set()
        thread.join(timeout=1.2)
        server.server_close()
        if thread.is_alive():
            raise ResearchError('provider_error')
        if failures:
            raise ResearchError('provider_error')
