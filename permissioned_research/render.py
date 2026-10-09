"""Render accepted domain records; raw provider prose is not an output surface."""

from __future__ import annotations

import json
import re
from typing import Any

LABELS = {"hosting_region": "Hosting region", "sso": "SSO support",
          "onboarding_days": "Onboarding lead time (business days)", "support_hours": "Priority support response (hours)"}


def plain_text(text: str) -> str:
    text = "".join(c if ord(c) >= 32 and ord(c) != 127 else " " for c in text)
    return re.sub(r"([\\`*_{}\[\]<>#|])", r"\\\1", text)


def render(result: dict[str, Any], output_format: str = "json") -> str:
    if output_format == "json":
        # ensure_ascii escapes terminal control characters and Unicode separators.
        return json.dumps(result, ensure_ascii=True, indent=2)
    memo = result["memo"]
    if memo is None:
        return f"Outcome: {result['outcome']}\nError: {result['error']}"
    lines = [f"Vendor: {plain_text(memo['vendor'])}", f"As of: {memo['as_of']}", f"Outcome: {memo['outcome']}", ""]
    for topic in memo["topics"]:
        lines.append(f"- {LABELS[topic['topic']]}: {topic['state']}")
        for claim in topic["claims"]:
            citation = claim["citation"]
            lines.append(f"  Value: {claim['value']}; {plain_text(citation['title'])} "
                         f"({citation['connector']}/{citation['source_id']} v{citation['revision']}, "
                         f"characters {citation['start']}:{citation['end']})")
            lines.append(f"  Quote: {plain_text(citation['quote'])}")
    return "\n".join(lines)
