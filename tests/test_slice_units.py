"""Small contract/ACL units; full agent/CLI acceptance is a separate batch."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

from permissioned_research.agent import ResearchAgent  # import-only syntax check
from permissioned_research.connectors import ConnectorTools
from permissioned_research.domain import Action, TOPICS, ResearchError, ResearchRequest, visible_facts
from permissioned_research.guard import REASONS, validate_memo
from permissioned_research.providers import ScriptedResearchProvider, encode_action
from permissioned_research.store import CorpusStore, load_fixture


class ActionContractTests(unittest.TestCase):
    def test_search_contract_and_principal_injection(self):
        raw = encode_action("search", connector="docshub", query="CedarBridge", limit=4)
        self.assertEqual(Action.parse(raw).name, "search")
        envelope = json.loads(raw)
        envelope["arguments"]["principal"] = "priya"
        with self.assertRaisesRegex(ResearchError, "invalid_decision"):
            Action.parse(json.dumps(envelope))

    def test_unknown_tool_and_boolean_limit_are_rejected(self):
        for raw in (encode_action("grant", principal="alex"),
                    encode_action("search", connector="teamchat", query="CedarBridge", limit=True)):
            with self.assertRaisesRegex(ResearchError, "invalid_decision"):
                Action.parse(raw)

    def test_malformed_and_oversize_responses_are_rejected(self):
        for raw in ("not JSON", " " * 20000):
            with self.assertRaisesRegex(ResearchError, "invalid_decision"):
                Action.parse(raw)

    def test_scripted_policy_changes_action_from_visible_observation(self):
        provider = ScriptedResearchProvider()
        base = {"request": {"vendor": "CedarBridge", "as_of": "2026-10-08"},
                "observations": [{"action": "plan", "arguments": {}, "result": {"status": "ok"}}]}
        self.assertEqual(Action.parse(provider.decide(base)).name, "search")
        with_hit = copy.deepcopy(base)
        with_hit["observations"].append({"action": "search", "arguments": {"connector": "docshub", "query": "CedarBridge"},
                                       "result": {"status": "ok", "hits": [{"handle": "e_" + "1" * 32}]}})
        action = Action.parse(provider.decide(with_hit))
        self.assertEqual((action.name, action.arguments["handle"]), ("read", "e_" + "1" * 32))


class CorpusPermissionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture("vendor-review")
        self.store = CorpusStore(":memory:", self.fixture)
        self.addCleanup(self.store.close)

    def test_nonhierarchical_acl_sets(self):
        expected = {
            "alex": {"cedar-general", "cedar-general-message"},
            "priya": {"cedar-general", "cedar-general-message", "cedar-contract", "cedar-procurement-message"},
            "sam": {"cedar-general", "cedar-general-message", "cedar-security"},
        }
        for principal, source_ids in expected.items():
            self.assertEqual({s.object_id for s in self.store.authorized_sources(principal)}, source_ids)

    def test_acl_filtering_before_text_search_and_handle_binding(self):
        alex = ConnectorTools(self.store, "alex")
        self.assertEqual(alex.search("docshub", "PROCUREMENT_ONLY_CEDAR", 4)["hits"], [])
        priya = ConnectorTools(self.store, "priya")
        handle = priya.search("docshub", "PROCUREMENT_ONLY_CEDAR", 4)["hits"][0]["handle"]
        self.assertEqual(alex.read(handle), alex.read("e_" + "0" * 32))
        self.assertEqual(alex.read(handle), {"status": "not_available"})

    def test_search_snippets_are_rechecked_before_context(self):
        tools = ConnectorTools(self.store, "priya")
        tools.search("docshub", "CedarBridge", 4)
        with self.store.connection:
            self.store.connection.execute("DELETE FROM memberships WHERE principal=? AND group_name=?", ("priya", "procurement"))
        with self.assertRaisesRegex(ResearchError, "source_state_changed"):
            tools.check_context()

    def test_persisted_audit_cannot_accept_source_text(self):
        self.store.audit("a" * 32, "search", "ok", 2)
        with self.assertRaises(ValueError):
            self.store.audit("a" * 32, "PROCUREMENT_ONLY_CEDAR", "ok")
        with self.assertRaises(ValueError):
            self.store.audit("a" * 32, "search", "private provider response")
        row = dict(self.store.connection.execute("SELECT * FROM audit_events").fetchone())
        self.assertEqual(set(row), {"sequence", "created_at", "run_id", "action", "status", "item_count", "epoch"})
        self.assertNotIn("PROCUREMENT", json.dumps(row))

    def test_unknown_principal_is_denied(self):
        with self.assertRaisesRegex(ResearchError, "invalid_request"):
            ConnectorTools(self.store, "nobody")

    def test_fixture_mismatch_does_not_overwrite_owned_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "corpus.sqlite3"
            first = CorpusStore(path, self.fixture)
            first.close()
            before = path.read_bytes()
            with self.assertRaisesRegex(ResearchError, "state_incompatible"):
                CorpusStore(path, load_fixture("conflicting-review"))
            self.assertEqual(path.read_bytes(), before)


class SpanAndReleaseTests(unittest.TestCase):
    def setUp(self):
        self.store = CorpusStore(":memory:", load_fixture("vendor-review"))
        self.addCleanup(self.store.close)
        self.request = ResearchRequest("priya", "CedarBridge", "2026-10-08")
        self.tools = ConnectorTools(self.store, "priya")
        hit = self.tools.search("docshub", "PROCUREMENT_ONLY_CEDAR", 1)["hits"][0]
        self.source = self.tools.read(hit["handle"])
        quote = "Priority support response: 4 hours."
        start = self.source["body"].index(quote)
        self.claim = {"value": 4, "handle": hit["handle"], "start": start, "end": start + len(quote)}

    def proposal(self, claim):
        proposals = [{"topic": t, "state": "unknown", "reason": REASONS["unknown"], "claims": []} for t in TOPICS]
        proposals[-1] = {"topic": "support_hours", "state": "supported", "reason": REASONS["supported"], "claims": [claim]}
        return proposals

    def test_character_offsets_and_false_boolean_are_preserved(self):
        body = "Résumé note.\nSSO support: no.\n"
        fact = visible_facts(body)[0]
        self.assertIs(fact["value"], False)
        self.assertEqual(body[fact["start"]:fact["end"]], "SSO support: no.")

    def test_wrong_units_are_not_fixture_facts(self):
        self.assertEqual(visible_facts("Priority support response: 4 days.\n"), [])

    def test_valid_span_is_released_and_wrong_value_is_rejected(self):
        memo = validate_memo(self.proposal(self.claim), self.request, self.tools)
        citation = memo["topics"][-1]["claims"][0]["citation"]
        self.assertEqual((citation["source_id"], citation["revision"], citation["quote"]),
                         ("cedar-contract", 3, "Priority support response: 4 hours."))
        wrong = dict(self.claim, value=99)
        with self.assertRaisesRegex(ResearchError, "invalid_memo"):
            validate_memo(self.proposal(wrong), self.request, self.tools)

    def test_final_gate_rechecks_access_and_rejects_unread_handle(self):
        unread = dict(self.claim, handle="e_" + "a" * 32)
        with self.assertRaisesRegex(ResearchError, "invalid_memo"):
            validate_memo(self.proposal(unread), self.request, self.tools)
        with self.store.connection:
            self.store.connection.execute("DELETE FROM memberships WHERE principal=? AND group_name=?", ("priya", "procurement"))
        with self.assertRaisesRegex(ResearchError, "source_state_changed"):
            validate_memo(self.proposal(self.claim), self.request, self.tools)


if __name__ == "__main__":
    unittest.main()
