"""Public action contracts. No authority is inferred from provider output."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

PROTOCOL = "research-action-v1"
TOPICS = ("hosting_region", "sso", "onboarding_days", "support_hours")
CONNECTORS = ("docshub", "teamchat")
MAX_DECISION_BYTES = 16 * 1024
MAX_SOURCE_BYTES = 2 * 1024


class ResearchError(Exception):
    """An allowlisted public error code, without source/provider text."""

    CODES = frozenset({
        "invalid_request", "invalid_decision", "invalid_memo", "invalid_fixture",
        "state_incompatible", "source_state_changed", "provider_error",
        "storage_error", "budget_exhausted", "cancelled", "provider_timeout",
        "tool_unavailable", "request_key_conflict", "attempt_in_progress", "attempt_state_conflict",
    })

    def __init__(self, code: str):
        if code not in self.CODES:
            raise ValueError("Unknown public error code")
        self.code = code
        super().__init__(code)


def iso_date(value: Any) -> date:
    if not isinstance(value, str):
        raise ResearchError("invalid_request")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ResearchError("invalid_request") from None
    if parsed.isoformat() != value:
        raise ResearchError("invalid_request")
    return parsed


def exact_keys(value: Any, keys: set[str], code: str = "invalid_decision") -> None:
    if type(value) is not dict or set(value) != keys:
        raise ResearchError(code)


def strict_json_loads(raw: str | bytes) -> Any:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON member")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("Nonfinite JSON number")
    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)


@dataclass(frozen=True)
class ResearchRequest:
    principal: str
    vendor: str
    as_of: str

    def __post_init__(self) -> None:
        if type(self.principal) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", self.principal):
            raise ResearchError("invalid_request")
        if type(self.vendor) is not str or not re.fullmatch(r"[A-Za-z][A-Za-z0-9 -]{0,63}", self.vendor):
            raise ResearchError("invalid_request")
        iso_date(self.as_of)


@dataclass(frozen=True)
class Source:
    object_id: str
    connector: str
    revision: int
    subject: str
    title: str
    body: str
    effective_at: str
    review_until: str

    def is_current_at(self, as_of: str) -> bool:
        return self.effective_at <= as_of <= self.review_until


@dataclass(frozen=True)
class Evidence:
    handle: str
    source: Source
    epoch: int = 1


@dataclass(frozen=True)
class Action:
    name: str
    arguments: dict[str, Any]

    @classmethod
    def parse(cls, raw: str) -> Action:
        try:
            if type(raw) is not str or len(raw.encode("utf-8")) > MAX_DECISION_BYTES:
                raise ResearchError("invalid_decision")
            envelope = strict_json_loads(raw)
        except (ValueError, RecursionError, UnicodeError):
            raise ResearchError("invalid_decision") from None
        exact_keys(envelope, {"protocol", "action", "arguments"})
        if envelope["protocol"] != PROTOCOL:
            raise ResearchError("invalid_decision")
        name, args = envelope["action"], envelope["arguments"]
        if type(name) is not str or type(args) is not dict:
            raise ResearchError("invalid_decision")
        if name in {"plan", "revise_plan"}:
            exact_keys(args, {"topics"})
            if args["topics"] != list(TOPICS):
                raise ResearchError("invalid_decision")
        elif name == "search":
            exact_keys(args, {"connector", "query", "limit"})
            if args["connector"] not in CONNECTORS:
                raise ResearchError("invalid_decision")
            if type(args["query"]) is not str or not 1 <= len(args["query"]) <= 128:
                raise ResearchError("invalid_decision")
            if any(ord(c) < 32 for c in args["query"]):
                raise ResearchError("invalid_decision")
            if type(args["limit"]) is not int or not 1 <= args["limit"] <= 4:
                raise ResearchError("invalid_decision")
        elif name == "read":
            exact_keys(args, {"handle"})
            if type(args["handle"]) is not str or not re.fullmatch(r"e_[0-9a-f]{32}", args["handle"]):
                raise ResearchError("invalid_decision")
        elif name == "finish":
            exact_keys(args, {"topics"})
            if type(args["topics"]) is not list or len(args["topics"]) != len(TOPICS):
                raise ResearchError("invalid_decision")
        else:
            raise ResearchError("invalid_decision")
        return cls(name, args)


# This fixture grammar is intentionally small. It is not general entailment.
FACT_PATTERNS = {
    "hosting_region": re.compile(r"Hosting region: (EU|US|IN)\."),
    "sso": re.compile(r"SSO support: (yes|no)\."),
    "onboarding_days": re.compile(r"Onboarding lead time: ([0-9]{1,3}) business days\."),
    "support_hours": re.compile(r"Priority support response: ([0-9]{1,3}) hours\."),
}


def fact_value(topic: str, quote: str) -> str | bool | int | None:
    match = FACT_PATTERNS[topic].fullmatch(quote)
    if match is None:
        return None
    value = match.group(1)
    if topic == "sso":
        return value == "yes"
    if topic in {"onboarding_days", "support_hours"}:
        return int(value)
    return value


def visible_facts(body: str) -> list[dict[str, Any]]:
    """Extract only complete authored fact lines, preserving character offsets."""
    found: list[dict[str, Any]] = []
    offset = 0
    for line in body.splitlines(keepends=True):
        quote = line.rstrip("\r\n")
        for topic in TOPICS:
            value = fact_value(topic, quote)
            if value is not None:
                found.append({"topic": topic, "value": value, "start": offset, "end": offset + len(quote)})
        offset += len(line)
    return found


TOOLS = (
    {"name": "plan", "arguments": ["topics"]},
    {"name": "search", "arguments": ["connector", "query", "limit"]},
    {"name": "read", "arguments": ["handle"]},
    {"name": "finish", "arguments": ["topics"]},
    {"name": "revise_plan", "arguments": ["topics"]},
)
