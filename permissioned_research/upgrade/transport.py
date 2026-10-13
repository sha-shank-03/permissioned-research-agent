"""An owned client child acknowledges a complete send before collection."""
import base64
from dataclasses import dataclass
import json
import queue
import subprocess
import sys
import threading
import time

from ..domain import ResearchError, strict_json_loads
from .config import DOUBLE_MODEL, live_preflight
from .errors import UpgradeError


@dataclass(frozen=True)
class DoubleEndpoint:
    port: int
    host: str = '127.0.0.1'

    def __post_init__(self):
        if self.host != '127.0.0.1' or type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ResearchError('invalid_request')


class Halt(UpgradeError):
    def __init__(self, code, *, possibly_sent=True, status=None):
        super().__init__(code)
        self.possibly_sent, self.status = possibly_sent, status


def http_error(status):
    if status == 401:
        return 'gateway_authentication_required'
    if status == 402:
        return 'gateway_budget_required'
    if status == 403:
        return 'gateway_verification_required'
    if status == 429:
        return 'gateway_rate_limited'
    return 'gateway_unavailable' if 500 <= status <= 599 else 'gateway_response_invalid'


class Pending:
    def __init__(self, process, messages, reader, writer, config, deadline, cancellation, clock=time.monotonic):
        self.process, self.messages = process, messages
        self.reader, self.writer, self.config = reader, writer, config
        self.deadline, self.cancellation, self.clock = deadline, cancellation, clock
        self.sent, self.closed = False, False

    def wait(self, deadline):
        while True:
            if self.cancellation.is_set():
                raise Halt('cancelled', possibly_sent=True)
            remaining = min(deadline, self.deadline) - self.clock()
            if remaining <= 0:
                raise Halt('provider_timeout', possibly_sent=True)
            try:
                value = self.messages.get(timeout=min(0.025, remaining))
            except queue.Empty:
                continue
            if value.get('event') == 'error':
                raise Halt(value['error'], possibly_sent=value.get('possibly_sent', True))
            return value

    def close(self):
        if self.closed:
            return
        began = self.clock()
        try:
            if self.process.poll() is None:
                self.process.terminate()
            self.process.wait(timeout=min(0.2, self.config.cleanup_seconds / 4))
        except subprocess.TimeoutExpired:
            self.process.kill()
            try:
                self.process.wait(timeout=min(0.2, self.config.cleanup_seconds / 4))
            except subprocess.TimeoutExpired:
                # Never claim cleanup success if an owned process still exists.
                raise Halt('provider_error') from None
        finally:
            for stream in (self.process.stdin, self.process.stdout):
                if stream:
                    stream.close()
            for thread in (self.reader, self.writer):
                if thread.ident is not None:
                    thread.join(timeout=max(0, min(0.1, self.config.cleanup_seconds - (self.clock() - began))))
        if self.process.poll() is None or self.reader.is_alive() or self.writer.is_alive():
            raise Halt('provider_error')
        self.closed = True


class GatewayTransport:
    evidence_kind = 'local_double'
    live_model_executed = False

    def __init__(self, config, *, endpoint=None, cancellation=None, test_stall=None):
        if endpoint is None:
            live_preflight(config)  # Zero allocation; no credential loader/network.
        if not isinstance(endpoint, DoubleEndpoint) or config.model != DOUBLE_MODEL:
            raise ResearchError('invalid_request')
        if test_stall not in (None, 'dns', 'tls', 'send'):
            raise ResearchError('invalid_request')
        self.config, self.endpoint = config, endpoint
        self.cancellation = cancellation or threading.Event()
        self.test_stall, self.active, self.owned = test_stall, None, []

    def submit(self, request, deadline):
        if self.active is not None:
            raise ResearchError('provider_error')
        if self.cancellation.is_set():
            raise Halt('cancelled', possibly_sent=False)
        now = time.monotonic()
        active_deadline = min(deadline - self.config.cleanup_seconds, now + self.config.request_seconds)
        send_deadline = min(active_deadline, now + self.config.submit_seconds)
        if send_deadline <= now:
            raise Halt('provider_timeout', possibly_sent=False)
        task = {'host': self.endpoint.host, 'port': self.endpoint.port, 'request': request,
                'send_deadline': send_deadline, 'deadline': active_deadline,
                'response_limit': self.config.max_response_bytes, 'test_stall': self.test_stall}
        wire = json.dumps(task, ensure_ascii=False).encode() + b'\n'
        messages = queue.Queue(maxsize=4)
        process = subprocess.Popen([sys.executable, '-m', 'permissioned_research.upgrade.wire_worker'],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))

        def deliver(value):
            try:
                messages.put_nowait(value)
            except queue.Full:
                pass  # A saturated channel cannot cause unbounded allocation.

        def write():
            try:
                process.stdin.write(wire)
                process.stdin.flush()
                process.stdin.close()
            except (OSError, ValueError):
                deliver({'event': 'error', 'error': 'gateway_response_lost', 'possibly_sent': True})

        def read():
            try:
                while True:
                    line = process.stdout.readline(128 * 1024 + 1)
                    if not line:
                        break
                    if len(line) > 128 * 1024:
                        raise ValueError()
                    value = strict_json_loads(line)
                    if type(value) is not dict or value.get('event') not in {'sent', 'response', 'error', 'http_error'}:
                        raise ValueError()
                    deliver(value)
            except (OSError, ValueError, TypeError, ResearchError):
                deliver({'event': 'error', 'error': 'gateway_response_invalid', 'possibly_sent': True})
            finally:
                deliver({'event': 'error', 'error': 'gateway_response_lost', 'possibly_sent': True})

        reader = threading.Thread(target=read, name='research-wire-read', daemon=True)
        writer = threading.Thread(target=write, name='research-wire-write', daemon=True)
        pending = Pending(process, messages, reader, writer, self.config, active_deadline, self.cancellation)
        self.active = pending
        self.owned.append(pending)
        try:
            reader.start()
            writer.start()
            acknowledgement = pending.wait(send_deadline)
            if acknowledgement != {'event': 'sent'}:
                raise Halt('gateway_response_invalid')
            pending.sent = True
            return pending
        except BaseException:
            try:
                pending.close()
            finally:
                self.active = None
            raise

    def collect(self, pending, deadline):
        try:
            response = pending.wait(deadline)
            if response.get('event') == 'http_error':
                status = response.get('status')
                if type(status) is not int or not 100 <= status <= 599:
                    raise Halt('gateway_response_invalid')
                raise Halt(http_error(status), status=status)
            if set(response) != {'event', 'body'} or response['event'] != 'response':
                raise Halt('gateway_response_invalid')
            try:
                body = base64.b64decode(response['body'], validate=True)
            except (ValueError, TypeError):
                raise Halt('gateway_response_invalid') from None
            if len(body) > self.config.max_response_bytes:
                raise Halt('gateway_response_invalid')
            return body
        finally:
            # The actor finally closes this same pending after collection.
            self.active = None

    def close(self):
        self.active = None
        for pending in self.owned:
            pending.close()

    @property
    def owned_children_remaining(self):
        return sum(p.process.poll() is None for p in self.owned)

    @property
    def owned_threads_remaining(self):
        return sum(t.is_alive() for p in self.owned for t in (p.reader, p.writer))
