"""M2 failure/repair/retry/budget checks, full loops deferred to integration lease."""

import json
import sqlite3
import threading
import unittest

from permissioned_research.agent import ResearchAgent
from permissioned_research.domain import ResearchError, ResearchRequest
from permissioned_research.providers import LocalProviderAdapter, ScriptedResearchProvider, encode_action
from permissioned_research.store import CorpusStore, load_fixture


class FailureWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.store = CorpusStore(":memory:", load_fixture("vendor-review"))
        self.addCleanup(self.store.close)
        self.request = ResearchRequest("priya", "CedarBridge", "2026-10-08")

    def test_malformed_response_repairs_once_then_fails_closed(self):
        class Bad:
            def decide(self, context):
                return "PRIVATE_MALFORMED_RESPONSE"
        result = ResearchAgent(self.store, self.request, Bad()).run()
        self.assertEqual((result["error"], result["counters"]["repairs"], result["memo"]), ("invalid_decision", 1, None))
        self.assertNotIn("PRIVATE_MALFORMED_RESPONSE", json.dumps(result))

    def test_wrong_tool_never_dispatches_and_remains_failed(self):
        class Wrong:
            def decide(self, context):
                return encode_action("grant_access", principal="priya")
        result = ResearchAgent(self.store, self.request, Wrong()).run()
        self.assertEqual(result["error"], "invalid_decision")
        self.assertEqual((result["counters"]["searches"], result["counters"]["reads"]), (0, 0))

    def test_one_transient_tool_retry_and_exhaustion_are_bounded(self):
        actor = ResearchAgent(self.store, self.request, ScriptedResearchProvider())
        original = actor.tools.connectors["docshub"].search
        attempts = 0
        def flaky(query, limit):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise ResearchError("tool_unavailable")
            return original(query, limit)
        actor.tools.connectors["docshub"].search = flaky
        result = actor.run()
        self.assertEqual(result["outcome"], "ready_for_review")
        self.assertEqual((attempts, result["counters"]["retries"]), (2, 1))
        exhausted = ResearchAgent(self.store, self.request, ScriptedResearchProvider())
        def unavailable(query, limit):
            raise ResearchError("tool_unavailable")
        exhausted.tools.connectors["docshub"].search = unavailable
        result = exhausted.run()
        self.assertEqual((result["error"], result["counters"]["retries"]), ("tool_unavailable", 1))

    def test_cancelled_and_step_exhausted_have_no_partial_memo(self):
        cancelled = threading.Event()
        cancelled.set()
        result = ResearchAgent(self.store, self.request, ScriptedResearchProvider(), cancellation=cancelled).run()
        self.assertEqual((result["outcome"], result["memo"]), ("cancelled", None))
        result = ResearchAgent(self.store, self.request, ScriptedResearchProvider(), max_decisions=1).run()
        self.assertEqual((result["error"], result["memo"]), ("budget_exhausted", None))

    def test_provider_timeout_has_no_automatic_resubmission(self):
        class TimeoutProvider(LocalProviderAdapter):
            def collect(self, pending, deadline):
                raise ResearchError("provider_timeout")
        result = ResearchAgent(self.store, self.request, TimeoutProvider(ScriptedResearchProvider())).run()
        self.assertEqual(result["error"], "provider_timeout")
        self.assertEqual(result["counters"]["provider_requests"], 1)

    def test_source_instructions_do_not_add_tools_or_read_evaluator(self):
        store = CorpusStore(":memory:", load_fixture("missing-review"))
        self.addCleanup(store.close)
        result = ResearchAgent(store, self.request, ScriptedResearchProvider()).run()
        self.assertEqual(result["outcome"], "needs_follow_up")
        self.assertEqual(result["memo"]["topics"][1]["state"], "unknown")
        self.assertNotIn("grant_access", json.dumps(result))
        actions = {row[0] for row in store.connection.execute("SELECT action FROM audit_events")}
        self.assertNotIn("grant_access", actions)

    def test_pending_collection_cancellation_closes_context_without_resubmission(self):
        cancellation = threading.Event()
        class Cancelling(LocalProviderAdapter):
            def submit(self, context, deadline):
                self.pending = super().submit(context, deadline)
                return self.pending
            def collect(self, pending, deadline):
                cancellation.set()
                return super().collect(pending, deadline)
        provider = Cancelling(ScriptedResearchProvider())
        result = ResearchAgent(self.store, self.request, provider, cancellation=cancellation).run()
        self.assertEqual((result["outcome"], result["memo"]), ("cancelled", None))
        self.assertEqual(result["counters"]["provider_requests"], 1)
        self.assertIsNone(provider.pending.context)

    def test_deadline_expiry_during_inference_discards_response(self):
        now = [0.0]
        class Expiring(LocalProviderAdapter):
            def collect(self, pending, deadline):
                response = super().collect(pending, deadline)
                now[0] = deadline
                return response
        result = ResearchAgent(self.store, self.request, Expiring(ScriptedResearchProvider()), clock=lambda: now[0], timeout_seconds=1).run()
        self.assertEqual((result["error"], result["memo"]), ("budget_exhausted", None))
        self.assertEqual(result["counters"]["provider_requests"], 1)

    def test_busy_tool_error_retries_once_and_returns_no_partial_memo(self):
        actor = ResearchAgent(self.store, self.request, ScriptedResearchProvider())
        attempts = []
        def busy(query, limit):
            attempts.append(1)
            raise sqlite3.OperationalError("database is locked")
        actor.tools.connectors["docshub"].search = busy
        result = actor.run()
        self.assertEqual((result["error"], result["memo"]), ("storage_error", None))
        self.assertEqual((len(attempts), result["counters"]["retries"]), (2, 1))


if __name__ == "__main__":
    unittest.main()
