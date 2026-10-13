"""Native function-call provider with cumulative bounds and zero HTTP retries."""
from ..domain import ResearchError
from .errors import UpgradeError
from .protocol import History, canonical, decode
from .transport import Halt


class GatewayProvider:
    evidence_kind = 'local_double'
    live_model_executed = False

    def __init__(self, config, transport, events):
        if transport.evidence_kind != 'local_double' or transport.live_model_executed is not False:
            raise UpgradeError('gateway_allocation_required')
        self.config, self.transport, self.events = config, transport, events
        self.identity = 'gateway-u1-double:' + config.sha256
        self.history, self.requests, self.transport_retries = History(), 0, 0

    def submit(self, context, deadline):
        if self.requests >= self.config.max_requests:
            raise ResearchError('budget_exhausted')
        request = self.history.build(context, self.config)
        self.requests += 1
        self.events.emit('request_started', request_number=self.requests, bytes=len(canonical(request).encode()))
        pending = None
        try:
            pending = self.transport.submit(request, deadline)
            if pending.sent is not True:
                pending.close()
                raise Halt('gateway_response_invalid')
            self.events.emit('request_sent', request_number=self.requests, possibly_sent=True)
            return pending
        except Halt as error:
            if pending is not None:
                pending.close()
            self.events.emit('transport_halted', request_number=self.requests,
                             error=error.code, http_status=error.status, possibly_sent=error.possibly_sent)
            raise
        except BaseException:
            if pending is not None:
                pending.close()
            raise

    def collect(self, pending, deadline):
        try:
            body = self.transport.collect(pending, deadline)
            self.events.emit('response_received', request_number=self.requests, bytes=len(body))
            decoded = decode(body, self.config, self.history.used_ids)
            self.events.emit('decision_received', request_number=self.requests,
                             action=decoded.call.name, prompt_tokens=decoded.response.prompt_tokens,
                             output_tokens=decoded.response.output_tokens)
            self.history.received(decoded.call)
            return decoded.response
        except Halt as error:
            self.events.emit('transport_halted', request_number=self.requests, error=error.code,
                             http_status=error.status, possibly_sent=error.possibly_sent)
            raise
        except ResearchError as error:
            self.events.emit('response_rejected', request_number=self.requests, error=error.code)
            raise

    def reset_history(self):
        self.history.clear()
        self.events.emit('history_reset', count=self.requests)

    def close(self):
        self.transport.close()
        self.history.clear()
