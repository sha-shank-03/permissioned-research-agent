"""Ollama-shaped loopback HTTP test double. It executes no model inference."""

import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from permissioned_research.domain import PROTOCOL
from permissioned_research.providers import ScriptedResearchProvider


@contextmanager
def protocol_server(responder=None):
    requests = []
    policy = ScriptedResearchProvider()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def handle(self):
            try:
                super().handle()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # Rejection/oversize clients may close before another HTTP/1.1
                # request line. Only expected peer-close errors are suppressed.
                pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            if self.path != "/api/chat" or not 0 < length <= 65536:
                self.send_error(400)
                return
            body = self.rfile.read(length)
            request = json.loads(body)
            requests.append(request)
            if responder is None:
                context = json.loads(request["messages"][-1]["content"])
                decision = json.loads(policy.decide(context))
                status, payload = 200, {"done": True, "message": {"role": "assistant", "content": "",
                    "tool_calls": [{"function": {"name": decision["action"], "arguments": decision["arguments"]}}]},
                    "prompt_eval_count": 11, "eval_count": 7}
                encoded = json.dumps(payload).encode()
                content_type = "application/json"
            else:
                status, encoded, content_type = responder(request)
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("X-Research-Test-Double", PROTOCOL)
            self.end_headers()
            try:
                self.wfile.write(encoded)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                # A client rejecting an oversize/error response closes early.
                # Avoid a traceback from this owned test-double connection.
                pass

        def log_message(self, format, *args):
            # Never write the request/source/response into an access log.
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if thread.is_alive():
            raise RuntimeError("Owned HTTP double did not stop")
