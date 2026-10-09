"""Provider protocol and an explicit, observation-driven scripted policy."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from .domain import CONNECTORS, PROTOCOL, TOPICS, visible_facts
from .guard import REASONS


class DecisionProvider(Protocol):
    def decide(self, context: dict[str, Any]) -> str:
        """Return one research-action-v1 JSON envelope. No tools execute here."""


@dataclass(frozen=True)
class ProviderResponse:
    decision: str
    prompt_tokens: int | None = None
    output_tokens: int | None = None


class LocalPending:
    def __init__(self, context: dict[str, Any]):
        self.context = context

    def close(self) -> None:
        self.context = None


class LocalProviderAdapter:
    """Keep M1 local doubles usable with the split dispatch/collection contract."""
    evidence_kind = "offline_scripted"
    live_model_executed = False

    def __init__(self, policy: DecisionProvider):
        self.policy = policy
        self.identity = getattr(policy, "identity", f"local:{type(policy).__module__}.{type(policy).__qualname__}")

    def submit(self, context: dict[str, Any], deadline: float) -> LocalPending:
        return LocalPending(context)

    def collect(self, pending: LocalPending, deadline: float) -> ProviderResponse:
        return ProviderResponse(self.policy.decide(pending.context))


def encode_action(action: str, **arguments: Any) -> str:
    return json.dumps({"protocol": PROTOCOL, "action": action, "arguments": arguments})


class ScriptedResearchProvider:
    """Deterministic demonstration policy, not a model or an answer replay.

    It sees the same authorized observations a model would see, never fixture
    files, scenario IDs, store connections, permission labels or answer keys.
    """
    identity = "scripted-m2-v1"

    def decide(self, context: dict[str, Any]) -> str:
        observations = context["observations"]
        request = context["request"]
        if not observations:
            return encode_action("plan", topics=list(TOPICS))
        read_handles = {o["arguments"]["handle"] for o in observations if o["action"] == "read"}
        for observation in observations:
            if observation["action"] == "search":
                for hit in observation["result"]["hits"]:
                    if hit["handle"] not in read_handles:
                        return encode_action("read", handle=hit["handle"])
        searches = [o for o in observations if o["action"] == "search"]
        for connector in CONNECTORS:
            if not any(o["arguments"]["connector"] == connector for o in searches):
                return encode_action("search", connector=connector, query=request["vendor"], limit=4)

        proposals = self._proposals(observations, request)
        # A visible disagreement or gap causes a further tool decision. The
        # outcome can therefore branch when observations change.
        needs_follow_up = any(p["state"] != "supported" for p in proposals)
        if needs_follow_up:
            query = request["vendor"] + " resolution"
            for connector in CONNECTORS:
                if not any(o["arguments"]["connector"] == connector and o["arguments"]["query"] == query for o in searches):
                    return encode_action("search", connector=connector, query=query, limit=4)
        return encode_action("finish", topics=proposals)

    @staticmethod
    def _proposals(observations: list[dict[str, Any]], request: dict[str, Any]) -> list[dict[str, Any]]:
        facts: dict[str, list[dict[str, Any]]] = {topic: [] for topic in TOPICS}
        seen_sources: set[str] = set()
        for observation in observations:
            if observation["action"] != "read" or observation["result"]["status"] != "ok":
                continue
            source = observation["result"]
            # The same document can have new handles after a follow-up search.
            # Deduplicate observations without knowing hidden source IDs.
            signature = json.dumps({k: source[k] for k in ("connector", "title", "revision", "body")}, sort_keys=True)
            if signature in seen_sources or source["subject"] != request["vendor"]:
                continue
            seen_sources.add(signature)
            if source["effective_at"] > request["as_of"]:
                continue
            current = source["effective_at"] <= request["as_of"] <= source["review_until"]
            for fact in visible_facts(source["body"]):
                facts[fact["topic"]].append({"value": fact["value"], "handle": source["handle"],
                    "start": fact["start"], "end": fact["end"], "current": current})
        proposals = []
        for topic in TOPICS:
            candidates = facts[topic]
            current = [c for c in candidates if c["current"]]
            active = current or candidates
            by_value: dict[tuple[str, str], dict[str, Any]] = {}
            for candidate in active:
                key = (type(candidate["value"]).__name__, str(candidate["value"]))
                by_value.setdefault(key, candidate)
            if not active:
                state, selected = "unknown", []
            elif not current:
                state, selected = "stale_only", list(by_value.values())[:2]
            elif len(by_value) > 1:
                state, selected = "conflict", list(by_value.values())[:2]
            else:
                state, selected = "supported", list(by_value.values())[:1]
            claims = [{k: c[k] for k in ("value", "handle", "start", "end")} for c in selected]
            proposals.append({"topic": topic, "state": state, "reason": REASONS[state], "claims": claims})
        return proposals
