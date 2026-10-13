"""Portable explicit configuration; no environment or credential inspection."""
from dataclasses import asdict, dataclass
import hashlib
import json
import re

from ..domain import ResearchError
from .errors import UpgradeError

DOUBLE_MODEL = 'test/synthetic-scalar'
GATEWAY_ORIGIN = 'https://ai-gateway.vercel.sh/v1/chat/completions'


@dataclass(frozen=True)
class DispatchConfig:
    model: str
    max_requests: int = 12
    max_searches: int = 6
    max_reads: int = 8
    total_seconds: float = 120.0
    request_seconds: float = 30.0
    submit_seconds: float = 5.0
    cleanup_seconds: float = 1.0
    max_context_bytes: int = 48 * 1024
    max_response_bytes: int = 64 * 1024
    max_action_bytes: int = 16 * 1024
    max_output_tokens: int = 2048

    def __post_init__(self):
        if not isinstance(self.model, str) or not re.fullmatch(r'[a-z0-9][a-z0-9.-]{0,63}/[a-z0-9][a-z0-9._-]{0,95}', self.model):
            raise ResearchError('invalid_request')
        limits = {'max_requests': 12, 'max_searches': 6, 'max_reads': 8,
                  'max_context_bytes': 49152, 'max_response_bytes': 65536,
                  'max_action_bytes': 16384, 'max_output_tokens': 2048}
        if any(type(getattr(self, k)) is not int or not 1 <= getattr(self, k) <= v for k, v in limits.items()):
            raise ResearchError('invalid_request')
        for name, maximum in [('total_seconds', 120), ('request_seconds', 30), ('submit_seconds', 5), ('cleanup_seconds', 1)]:
            value = getattr(self, name)
            if type(value) not in (int, float) or not 0 < value <= maximum:
                raise ResearchError('invalid_request')
        if self.total_seconds <= self.cleanup_seconds or self.submit_seconds > self.request_seconds:
            raise ResearchError('invalid_request')

    @property
    def sha256(self):
        return hashlib.sha256(json.dumps(asdict(self), sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def require_local_model(config):
    if config.model != DOUBLE_MODEL:
        raise ResearchError('invalid_request')


def live_preflight(config, credential_loader=None):
    """Main has allocated this worker zero INR. Never invoke the supplied loader."""
    raise UpgradeError('gateway_allocation_required')
