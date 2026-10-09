"""Deterministic final release checks over identity-bound evidence."""

from __future__ import annotations

from typing import Any

from .connectors import ConnectorTools
from .domain import TOPICS, ResearchError, ResearchRequest, exact_keys, fact_value

REASONS = {
    "supported": "current_evidence",
    "conflict": "unresolved_conflict",
    "stale_only": "evidence_not_current",
    "unknown": "insufficient_accessible_evidence",
}


def validate_memo(proposals: list[dict[str, Any]], request: ResearchRequest,
                  tools: ConnectorTools) -> dict[str, Any]:
    tools.check_context()
    if type(proposals) is not list or len(proposals) != len(TOPICS):
        raise ResearchError("invalid_memo")
    result: dict[str, dict[str, Any]] = {}
    total_claims = 0
    for proposal in proposals:
        exact_keys(proposal, {"topic", "state", "reason", "claims"}, "invalid_memo")
        topic, state = proposal["topic"], proposal["state"]
        if type(topic) is not str or topic not in TOPICS or topic in result:
            raise ResearchError("invalid_memo")
        if type(state) is not str or state not in REASONS or proposal["reason"] != REASONS[state]:
            raise ResearchError("invalid_memo")
        claims = proposal["claims"]
        if type(claims) is not list or len(claims) > 2:
            raise ResearchError("invalid_memo")
        total_claims += len(claims)
        if total_claims > 8 or (state == "unknown") != (not claims):
            raise ResearchError("invalid_memo")
        accepted = []
        for claim in claims:
            exact_keys(claim, {"value", "handle", "start", "end"}, "invalid_memo")
            handle = claim["handle"]
            if type(handle) is not str or handle not in tools.read_handles:
                raise ResearchError("invalid_memo")
            evidence = tools.evidence[handle]
            source = tools.store.source_for(request.principal, evidence.source.object_id)
            if source != evidence.source or source.subject != request.vendor:
                raise ResearchError("source_state_changed")
            if source.effective_at > request.as_of:
                raise ResearchError("invalid_memo")
            start, end = claim["start"], claim["end"]
            if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(source.body):
                raise ResearchError("invalid_memo")
            quote = source.body[start:end]
            expected = fact_value(topic, quote)
            if expected is None or type(claim["value"]) is not type(expected) or claim["value"] != expected:
                raise ResearchError("invalid_memo")
            current = source.is_current_at(request.as_of)
            if state in {"supported", "conflict"} and not current:
                raise ResearchError("invalid_memo")
            if state == "stale_only" and current:
                raise ResearchError("invalid_memo")
            accepted.append({"value": expected, "citation": {
                "connector": source.connector, "source_id": source.object_id,
                "revision": source.revision, "title": source.title,
                "start": start, "end": end, "quote": quote,
            }})
        values = {(type(c["value"]).__name__, str(c["value"])) for c in accepted}
        if state == "supported" and len(values) != 1:
            raise ResearchError("invalid_memo")
        if state == "conflict" and len(values) != 2:
            raise ResearchError("invalid_memo")
        result[topic] = {"topic": topic, "state": state, "reason": REASONS[state], "claims": accepted}
    # Final current-access check immediately before handing the result to caller.
    # The actor holds the same BEGIN IMMEDIATE through the caller handoff.
    tools.check_context()
    outcome = "ready_for_review" if all(t["state"] == "supported" for t in result.values()) else "needs_follow_up"
    return {"vendor": request.vendor, "as_of": request.as_of, "outcome": outcome,
            "topics": [result[topic] for topic in TOPICS]}
