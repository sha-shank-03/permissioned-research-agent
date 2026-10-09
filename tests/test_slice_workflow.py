"""M1 acceptance batch: run only after an assigned integration lease."""

import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path

from permissioned_research.__main__ import main
from permissioned_research.agent import ResearchAgent
from permissioned_research.domain import ResearchRequest
from permissioned_research.providers import ScriptedResearchProvider
from permissioned_research.store import CorpusStore, load_fixture


class CapturingProvider:
    """Memory-only observation capture for independent test assertions."""

    def __init__(self):
        self.scripted = ScriptedResearchProvider()
        self.contexts = []
        self.decisions = []

    def decide(self, context):
        self.contexts.append(copy.deepcopy(context))
        raw = self.scripted.decide(context)
        self.decisions.append(json.loads(raw))
        return raw


class WorkflowAcceptanceTests(unittest.TestCase):
    def run_case(self, principal, scenario="vendor-review", provider=None):
        fixture = load_fixture(scenario)
        store = CorpusStore(":memory:", fixture)
        self.addCleanup(store.close)
        actor = provider or CapturingProvider()
        result = ResearchAgent(store, ResearchRequest(principal, fixture["vendor"], fixture["as_of"]), actor).run()
        return result, store, actor

    def assert_citations(self, result, store, expected_sources):
        # Independent SQL/span checks. Do not use production validation/parsing
        # functions to decide whether a returned citation is valid.
        for topic in result["memo"]["topics"]:
            for claim in topic["claims"]:
                citation = claim["citation"]
                self.assertIn(citation["source_id"], expected_sources)
                row = store.connection.execute(
                    "SELECT body FROM versions WHERE object_id=? AND revision=?",
                    (citation["source_id"], citation["revision"])).fetchone()
                self.assertIsNotNone(row)
                self.assertEqual(row[0][citation["start"]:citation["end"]], citation["quote"])

    def test_priya_completes_four_topics_using_both_connectors(self):
        result, store, actor = self.run_case("priya")
        self.assertEqual(result["outcome"], "ready_for_review")
        topics = {t["topic"]: t for t in result["memo"]["topics"]}
        expected = {"hosting_region": "EU", "sso": True, "onboarding_days": 10, "support_hours": 4}
        for topic, value in expected.items():
            self.assertEqual(topics[topic]["state"], "supported")
            self.assertEqual(topics[topic]["claims"][0]["value"], value)
        searched = {d["arguments"]["connector"] for d in actor.decisions if d["action"] == "search"}
        cited = {c["citation"]["connector"] for t in topics.values() for c in t["claims"]}
        self.assertEqual(searched, {"docshub", "teamchat"})
        self.assertEqual(cited, {"docshub", "teamchat"})
        self.assert_citations(result, store, {"cedar-general", "cedar-general-message", "cedar-contract", "cedar-procurement-message"})
        self.assertNotIn("SECURITY_ONLY_CEDAR", json.dumps(actor.contexts) + json.dumps(result))

    def test_alex_has_no_procurement_text_in_context_or_output(self):
        result, store, actor = self.run_case("alex")
        self.assertEqual(result["outcome"], "needs_follow_up")
        topics = {t["topic"]: t for t in result["memo"]["topics"]}
        self.assertEqual(topics["onboarding_days"]["state"], "unknown")
        self.assertEqual(topics["support_hours"]["state"], "unknown")
        captured = json.dumps(actor.contexts) + json.dumps(result)
        for forbidden in ("PROCUREMENT_ONLY_CEDAR", "PROCUREMENT_CHAT_ONLY", "cedar-contract", "cedar-procurement-message", "SECURITY_ONLY_CEDAR"):
            self.assertNotIn(forbidden, captured)
        self.assert_citations(result, store, {"cedar-general", "cedar-general-message"})

    def test_conflict_keeps_both_values_and_changes_search_decisions(self):
        result, store, actor = self.run_case("priya", "conflicting-review")
        self.assertEqual(result["outcome"], "needs_follow_up")
        support = result["memo"]["topics"][-1]
        self.assertEqual(support["state"], "conflict")
        self.assertEqual({c["value"] for c in support["claims"]}, {4, 8})
        self.assertTrue(any(d["action"] == "search" and d["arguments"]["query"].endswith(" resolution") for d in actor.decisions))
        self.assert_citations(result, store, {"cedar-general", "cedar-general-message", "cedar-contract", "cedar-procurement-message"})

    def test_invalid_provider_response_fails_without_persisting_it(self):
        class BadProvider:
            def decide(self, context):
                return "PRIVATE_RAW_PROVIDER_RESPONSE"
        result, store, _ = self.run_case("priya", provider=BadProvider())
        self.assertEqual((result["outcome"], result["error"], result["memo"]), ("failed", "invalid_decision", None))
        events = [dict(row) for row in store.connection.execute("SELECT * FROM audit_events")]
        self.assertNotIn("PRIVATE_RAW_PROVIDER_RESPONSE", json.dumps(events) + json.dumps(result))

    def test_success_audit_has_only_operational_fields(self):
        result, store, _ = self.run_case("priya")
        events = [dict(row) for row in store.connection.execute("SELECT * FROM audit_events")]
        serialized = json.dumps(events)
        for forbidden in ("CedarBridge", "PROCUREMENT", "SSO support", "Priority support", "cedar-contract"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(store.connection.execute("SELECT outcome FROM run_receipts WHERE run_id=?", (result["run_id"],)).fetchone()[0], "ready_for_review")

    def test_cli_json_and_existing_fixture_mismatch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            stdout, stderr = io.StringIO(), io.StringIO()
            with redirect_stdout(stdout), redirect_stderr(stderr):
                code = main(["demo", "--principal", "priya", "--state-dir", temp_dir])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())["outcome"], "ready_for_review")
            self.assertIn("NO MODEL CALLED", stderr.getvalue())
            database = Path(temp_dir) / "corpus.sqlite3"
            before = database.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                code = main(["demo", "--scenario", "conflicting-review", "--state-dir", temp_dir])
            self.assertEqual(code, 2)
            self.assertEqual(database.read_bytes(), before)
            with closing(sqlite3.connect(database)) as check:
                self.assertEqual(check.execute("SELECT COUNT(*) FROM run_receipts").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
