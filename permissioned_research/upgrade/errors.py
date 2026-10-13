"""Add Gateway enums without changing the frozen v1 public error contract."""
from ..domain import ResearchError


class UpgradeError(ResearchError):
    CODES = ResearchError.CODES | frozenset({
        'gateway_allocation_required', 'gateway_authentication_required',
        'gateway_budget_required', 'gateway_verification_required', 'gateway_rate_limited',
        'gateway_unavailable', 'gateway_response_invalid', 'gateway_response_lost',
    })
