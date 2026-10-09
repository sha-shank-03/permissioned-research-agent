"""Fixed one-query/top-one retrieval baseline; same external permission gates."""

from permissioned_research.domain import CONNECTORS, TOPICS
from permissioned_research.providers import ScriptedResearchProvider, encode_action


class BaselineProvider:
    identity = "top-one-baseline-m2-v1"

    def decide(self, context):
        observations = context["observations"]
        if not observations:
            return encode_action("plan", topics=list(TOPICS))
        read = {o["arguments"]["handle"] for o in observations if o["action"] == "read"}
        for observation in observations:
            if observation["action"] == "search":
                for hit in observation["result"]["hits"]:
                    if hit["handle"] not in read:
                        return encode_action("read", handle=hit["handle"])
        for connector in CONNECTORS:
            if not any(o["action"] == "search" and o["arguments"]["connector"] == connector for o in observations):
                return encode_action("search", connector=connector, query=context["request"]["vendor"], limit=1)
        proposals = ScriptedResearchProvider._proposals(observations, context["request"])
        return encode_action("finish", topics=proposals)
