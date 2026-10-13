"""Additive scalar controller. The frozen authority and span guard stay in use."""
from dataclasses import asdict
import hashlib
import json
import sqlite3
import threading

from ..agent import ResearchAgent
from ..domain import ResearchError, ResearchRequest
from . import VERSION
from .events import Events, code_digest
from .gateway import GatewayProvider
from .protocol import PROMPT_SHA256
from .transport import GatewayTransport
from .tools import ScalarTools

HANDOFF = frozenset({'gateway_authentication_required', 'gateway_budget_required',
                     'gateway_verification_required', 'gateway_allocation_required'})


def fixture_digest(fixture):
    return hashlib.sha256(json.dumps(fixture, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


class ScalarAgent(ResearchAgent):
    def __init__(self, *args, config, **kwargs):
        self.dispatch_config = config
        super().__init__(*args, max_decisions=config.max_requests,
                         timeout_seconds=config.total_seconds, **kwargs)

    def _new_tools(self):
        return ScalarTools(self.store, self.request.principal, self.caches,
                           self.provider.identity, self.observer)

    def _reset(self):
        # Clear retained function arguments even when a second epoch change aborts.
        self.provider.reset_history()
        super()._reset()

    def _tool(self, action):
        began = self.clock()
        counter, limit = (('searches', self.dispatch_config.max_searches) if action.name == 'search'
                          else ('reads', self.dispatch_config.max_reads))
        for attempt in range(2):
            self._check_budget()
            if self.counters[counter] >= limit or self.clock() - began >= 1:
                raise ResearchError('budget_exhausted')
            self.counters[counter] += 1
            try:
                self._hook('before_tool')
                with self.store.boundary():
                    self.tools.check_context()
                    self.store.progress_attempt(self.run_id, self.counters)
                    result = getattr(self.tools, action.name)(**action.arguments)
                    self._observe(action, result, len(result.get('hits', [])) if action.name == 'search'
                                  else int(result['status'] == 'ok'))
                self._hook('after_tool')
                if self.clock() - began >= 1:
                    raise ResearchError('budget_exhausted')
                return result
            except sqlite3.OperationalError as error:
                if attempt or not ('locked' in str(error).lower() or 'busy' in str(error).lower()):
                    raise ResearchError('storage_error') from None
            except ResearchError as error:
                if error.code != 'tool_unavailable' or attempt:
                    raise
            # Only the existing local read-only tool retry is allowed.
            self.counters['retries'] += 1
            self.store.audit(self.run_id, 'retry', 'ok')
        raise ResearchError('tool_unavailable')


def run_scalar(store, fixture, principal, config, endpoint, *, event_path=None,
               observer=None, hooks=None, cancellation=None, transport=None):
    cancellation = cancellation or threading.Event()
    events = Events(config, fixture_digest(fixture), code_digest(), event_path)
    provider = None
    try:
        transport = transport or GatewayTransport(config, endpoint=endpoint, cancellation=cancellation)
        provider = GatewayProvider(config, transport, events)

        def observe(surface, envelope):
            events.observe(surface, envelope)
            if observer:
                # Test-only in-memory disclosure inspection, never the event writer.
                observer(surface, envelope)

        actor = ScalarAgent(store, ResearchRequest(principal, fixture['vendor'], fixture['as_of']),
                            provider, config=config, observer=observe, hooks=hooks,
                            cancellation=cancellation)
        events.bind(actor.run_id)
        result = actor.run()
        provider.close()
        finish_count = sum(r['event'] == 'decision_received' and r.get('action') == 'finish' for r in events.rows)
        delivered = int(result['memo'] is not None)
        events.emit('finished', outcome=result['outcome'], error=result['error'], count=delivered)
        return {'schema': 'research-run-u1-v1', 'version': VERSION,
                'scenario': fixture['scenario'], 'mode': 'local_double', 'live_model_executed': False,
                'code_sha256': code_digest(), 'prompt_sha256': PROMPT_SHA256,
                'dataset_sha256': fixture_digest(fixture), 'config_sha256': config.sha256,
                'config': asdict(config), 'model': config.model, 'provider': 'owned_gateway_double',
                'generation_id': None, 'actual_cost': None, 'estimated_cost': None,
                'usage_kind': 'authored_double_or_unknown', 'result': result,
                'gateway_reported_tokens': {name: sum(r[name] for r in events.rows if r['event'] == 'decision_received' and
                                                        r.get(name) is not None)
                                            if any(r['event'] == 'decision_received' and r.get(name) is not None for r in events.rows)
                                            else None for name in ('prompt_tokens', 'output_tokens')},
                'operational_status': 'human_handoff' if result['error'] in HANDOFF else result['outcome'],
                'metrics': {'attempts': 1, 'provider_requests': provider.requests,
                            'automatic_inference_transport_retries': provider.transport_retries,
                            'finish_proposals': finish_count, 'guarded_releases': delivered,
                            'blocked_finish_proposals': finish_count - delivered,
                            'unsafe_deliveries': None, 'semantic_groundedness': None,
                            'owned_children_remaining': transport.owned_children_remaining,
                            'owned_client_threads_remaining': transport.owned_threads_remaining},
                'resume_supported': False, 'state_recovery': 'fresh_attempt_only'}
    finally:
        try:
            if provider is not None:
                provider.close()
        finally:
            events.close()
