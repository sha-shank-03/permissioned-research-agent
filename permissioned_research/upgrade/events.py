"""Metadata-only events. Raw provider/source fields are never serialized."""
import hashlib
import json
import os
import re
import time

from ..domain import ResearchError
from . import VERSION
from .protocol import PROMPT_SHA256

EVENTS = frozenset({'request_started', 'request_sent', 'response_received', 'decision_received', 'response_rejected',
                    'transport_halted', 'history_reset', 'provider_request', 'output',
                    'invalidated', 'cache_lookup', 'tool_output', 'finished'})
ERRORS = frozenset({'invalid_request', 'invalid_decision', 'invalid_memo', 'provider_timeout',
                    'provider_error', 'cancelled', 'budget_exhausted', 'source_state_changed',
                    'storage_error', 'tool_unavailable', 'gateway_allocation_required',
                    'gateway_authentication_required', 'gateway_budget_required',
                    'gateway_verification_required', 'gateway_rate_limited',
                    'gateway_unavailable', 'gateway_response_invalid', 'gateway_response_lost'})
OUTCOMES = frozenset({'ready_for_review', 'needs_follow_up', 'failed', 'cancelled', 'authorization_handoff'})
FIELDS = frozenset({'request_number', 'bytes', 'prompt_tokens', 'output_tokens', 'http_status',
                    'error', 'outcome', 'possibly_sent', 'action', 'count'})
ACTIONS = frozenset({'plan', 'search', 'read', 'finish', 'revise_plan'})


def safe_fields(fields):
    if not set(fields) <= FIELDS:
        raise ResearchError('invalid_request')
    result = {}
    for name, value in fields.items():
        if name in {'error', 'outcome', 'action'}:
            allowed = ERRORS if name == 'error' else OUTCOMES if name == 'outcome' else ACTIONS
            if value is not None and (type(value) is not str or value not in allowed):
                raise ResearchError('invalid_request')
        elif name == 'possibly_sent':
            if type(value) is not bool:
                raise ResearchError('invalid_request')
        elif value is not None and (type(value) is not int or not 0 <= value <= 10**9):
            raise ResearchError('invalid_request')
        result[name] = value
    return result


class Events:
    def __init__(self, config, dataset_sha256, code_sha256, path=None, clock=time.monotonic):
        if any(not re.fullmatch(r'[0-9a-f]{64}', s) for s in (dataset_sha256, code_sha256)):
            raise ResearchError('invalid_request')
        self.base = {'schema': 'research-events-u1-v1', 'version': VERSION,
                     'mode': 'local_double', 'live_model_executed': False,
                     'model': config.model, 'provider': 'owned_gateway_double',
                     'config_sha256': config.sha256, 'prompt_sha256': PROMPT_SHA256,
                     'dataset_sha256': dataset_sha256, 'code_sha256': code_sha256,
                     'generation_id': None, 'actual_cost': None, 'estimated_cost': None,
                     'usage_kind': 'authored_double_or_unknown'}
        self.clock, self.started, self.rows, self.run_id = clock, clock(), [], None
        self.handle = path.open('x', encoding='utf-8', newline='\n') if path else None

    def bind(self, run_id):
        if not re.fullmatch(r'[0-9a-f]{32}', run_id) or self.run_id is not None:
            raise ResearchError('invalid_request')
        self.run_id = run_id

    def emit(self, event, **fields):
        if event not in EVENTS or self.run_id is None:
            raise ResearchError('invalid_request')
        row = {**self.base, 'run_id': self.run_id, 'sequence': len(self.rows) + 1,
               'event': event, 'elapsed_seconds': round(max(0, self.clock() - self.started), 6),
               **safe_fields(fields)}
        if self.handle:
            self.handle.write(json.dumps(row, sort_keys=True) + '\n')
            self.handle.flush()
            os.fsync(self.handle.fileno())
        self.rows.append(row)

    def observe(self, surface, envelope):
        if surface in {'provider_request', 'output', 'invalidated', 'cache_lookup', 'tool_output'}:
            # No dictionary keys or strings from the untrusted envelope are copied.
            self.emit(surface)

    def close(self):
        if self.handle:
            self.handle.close()
            self.handle = None


def code_digest():
    from pathlib import Path
    root = Path(__file__).parent
    lines = [p.name + ' ' + hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(root.glob('*.py'))]
    return hashlib.sha256('\n'.join(lines).encode()).hexdigest()
