"""M2 integration-only barriers, source changes and cross-identity cache attacks."""

import json
import tempfile
import threading
import unittest
from pathlib import Path

from evaluation.inspect import SurfaceInspector
from permissioned_research.agent import ResearchAgent
from permissioned_research.cache import RuntimeCaches
from permissioned_research.domain import ResearchRequest
from permissioned_research.providers import LocalProviderAdapter, ScriptedResearchProvider
from permissioned_research.store import CorpusStore, load_fixture


class PermissionIntegrationTests(unittest.TestCase):
    def setUp(self):
        root = Path(".local/m2-tests").resolve()
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.assertTrue(Path(self.temp.name).resolve().is_relative_to(root))
        self.addCleanup(self.temp.cleanup)
        self.fixture = load_fixture("vendor-review")
        self.path = Path(self.temp.name) / "corpus.sqlite3"
        self.store = CorpusStore(self.path, self.fixture)
        self.addCleanup(self.store.close)
        self.caches = RuntimeCaches()

    def actor(self, principal="priya", provider=None, hooks=None):
        inspector = SurfaceInspector(self.store, self.fixture)
        actor = ResearchAgent(self.store, ResearchRequest(principal, "CedarBridge", "2026-10-08"),
                              provider or ScriptedResearchProvider(), caches=self.caches, observer=inspector, hooks=hooks)
        return actor, inspector

    def writer(self):
        start, attempted, committed, ready = (threading.Event() for _ in range(4))
        failures = []
        def run():
            control = None
            try:
                control = CorpusStore(self.path, self.fixture)
                control.connection.set_trace_callback(lambda statement: attempted.set() if statement.startswith("BEGIN IMMEDIATE") else None)
                ready.set()
                if not start.wait(3):
                    raise AssertionError("Writer start barrier timed out")
                control.revoke_membership("priya", "procurement", "race-revoke")
                committed.set()
            except BaseException as exc:
                failures.append(type(exc).__name__)
                committed.set()
            finally:
                ready.set()
                if control is not None:
                    control.close()
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(lambda: thread.join(3))
        self.addCleanup(start.set)
        self.assertTrue(ready.wait(3), "Writer connection initialization timed out")
        self.assertEqual(failures, [], "Writer connection initialization failed")
        return start, attempted, committed, thread, failures

    def test_dispatch_orders_before_revocation_and_collection_discards_old_context(self):
        start, attempted, committed, thread, failures = self.writer()
        test = self
        class BarrierProvider(LocalProviderAdapter):
            def __init__(self):
                super().__init__(ScriptedResearchProvider())
                self.fired = False
            def submit(self, context, deadline):
                if not self.fired and "PROCUREMENT_ONLY_CEDAR" in json.dumps(context):
                    self.fired = True
                    start.set()
                    test.assertTrue(attempted.wait(2))
                    test.assertFalse(committed.is_set(), "Revocation committed while dispatch held authorization lock")
                return super().submit(context, deadline)
            def collect(self, pending, deadline):
                if self.fired:
                    test.assertTrue(committed.wait(2), "Inference collection kept the write lock")
                return super().collect(pending, deadline)
        actor, inspector = self.actor(provider=BarrierProvider())
        result = actor.run()
        thread.join(2)
        self.assertEqual(failures, [])
        self.assertFalse(thread.is_alive())
        self.assertEqual((result["outcome"], result["counters"]["restarts"]), ("needs_follow_up", 1))
        self.assertNotIn("PROCUREMENT", json.dumps(result))
        self.assertEqual(inspector.violations, [])
        current = self.store.epoch
        for surface, record in inspector.events:
            if record["epoch"] == current and surface == "provider_request":
                self.assertNotIn("PROCUREMENT", json.dumps(record))
        self.assertNotIn("PROCUREMENT", repr(self.caches.evidence.entries) + repr(self.caches.memos.entries))

    def test_release_orders_before_later_revocation_and_resolver_denies_afterward(self):
        start, attempted, committed, thread, failures = self.writer()
        def inside_release(actor):
            start.set()
            self.assertTrue(attempted.wait(2))
            self.assertFalse(committed.is_set(), "Revocation committed inside protected caller handoff")
        actor, inspector = self.actor(hooks={"inside_release": inside_release})
        result = actor.run()
        self.assertTrue(committed.wait(2))
        thread.join(2)
        self.assertEqual(failures, [])
        self.assertEqual(result["outcome"], "ready_for_review")
        # This completed memo was handed off before the revoke receipt. It is
        # deliberately *not* claimed to be recalled/erased afterward.
        private = result["memo"]["topics"][-1]["claims"][0]["citation"]
        self.assertEqual(actor.tools.resolve_citation(private), {"status": "not_available"})
        self.assertEqual(inspector.violations, [])
        self.assertEqual(len(self.caches.memos.entries), 0)

    def test_revocation_before_final_gate_restarts_and_never_releases_private_memo(self):
        fired = False
        def before_release(actor):
            nonlocal fired
            if not fired:
                fired = True
                self.store.revoke_membership("priya", "procurement", "before-release")
        actor, inspector = self.actor(hooks={"before_release": before_release})
        result = actor.run()
        self.assertEqual(result["outcome"], "needs_follow_up")
        self.assertEqual(result["counters"]["restarts"], 1)
        self.assertNotIn("PROCUREMENT", json.dumps(result))
        inspector.check_audit()
        self.assertEqual(inspector.violations, [])

    def test_cross_identity_memo_cache_and_post_revoke_cache_attack(self):
        first, _ = self.actor()
        completed = first.run()
        self.assertEqual(completed["outcome"], "ready_for_review")
        repeated, _ = self.actor()
        cached = repeated.run()
        self.assertEqual(cached["counters"]["provider_requests"], 0)
        self.assertEqual(cached["counters"]["cache_hits"], 1)
        alex, inspector = self.actor("alex")
        public = alex.run()
        self.assertEqual(public["outcome"], "needs_follow_up")
        self.assertNotIn("PROCUREMENT", json.dumps(public))
        self.assertEqual(inspector.violations, [])
        self.store.revoke_membership("priya", "procurement", "cache-revoke")
        revoked, inspector = self.actor()
        outcome = revoked.run()
        self.assertEqual(outcome["outcome"], "needs_follow_up")
        self.assertGreater(outcome["counters"]["provider_requests"], 0)
        self.assertNotIn("PROCUREMENT", repr(self.caches.evidence.entries) + repr(self.caches.memos.entries))
        self.assertEqual(inspector.violations, [])

    def test_source_version_change_discards_old_observations_and_spans(self):
        fired = False
        def after_tool(actor):
            nonlocal fired
            last = actor.observations[-1]
            if not fired and last["action"] == "read" and "PROCUREMENT_ONLY_CEDAR" in last["result"].get("body", ""):
                fired = True
                self.store.replace_source("cedar-contract", "Replacement source.\nOnboarding lead time: 12 business days.\nPriority support response: 6 hours.\n", "2026-10-01", "2026-12-31", "version-4")
        actor, inspector = self.actor(hooks={"after_tool": after_tool})
        result = actor.run()
        self.assertEqual(result["counters"]["restarts"], 1)
        self.assertEqual(result["memo"]["topics"][2]["claims"][0]["value"], 12)
        support = result["memo"]["topics"][-1]
        self.assertEqual({c["value"] for c in support["claims"]}, {4, 6})
        self.assertTrue(any(c["citation"]["revision"] == 4 for c in support["claims"]))
        self.assertEqual(inspector.violations, [])

    def test_second_change_exhausts_restart_without_partial_output(self):
        changed = 0
        def after_tool(actor):
            nonlocal changed
            if actor.observations[-1]["action"] == "search" and changed < 2:
                changed += 1
                if changed == 1:
                    self.store.revoke_membership("priya", "procurement", "first-change")
                else:
                    self.store.delete_object("cedar-general", "second-change")
        actor, _ = self.actor(hooks={"after_tool": after_tool})
        result = actor.run()
        self.assertEqual((result["error"], result["memo"]), ("source_state_changed", None))
        self.assertEqual(actor.observations, [])
        self.assertFalse(self.caches.memos.entries)

    def test_revocation_after_search_after_read_and_before_next_dispatch(self):
        for boundary in ("after_search", "after_read", "before_dispatch"):
            with self.subTest(boundary=boundary):
                store = CorpusStore(":memory:", self.fixture)
                try:
                    fired = [False]
                    def revoke(actor):
                        if fired[0]:
                            return
                        observations = actor.observations
                        if not observations or "PROCUREMENT_ONLY_CEDAR" not in json.dumps(observations):
                            return
                        if boundary == "after_search" and observations[-1]["action"] != "search":
                            return
                        if boundary == "after_read" and observations[-1]["action"] != "read":
                            return
                        fired[0] = True
                        store.revoke_membership("priya", "procurement", boundary)
                    inspector = SurfaceInspector(store, self.fixture)
                    hook_name = "before_dispatch" if boundary == "before_dispatch" else "after_tool"
                    actor = ResearchAgent(store, ResearchRequest("priya", "CedarBridge", "2026-10-08"),
                                          ScriptedResearchProvider(), observer=inspector, hooks={hook_name: revoke})
                    result = actor.run()
                    self.assertTrue(fired[0])
                    self.assertEqual((result["outcome"], result["counters"]["restarts"]), ("needs_follow_up", 1))
                    self.assertNotIn("PROCUREMENT", json.dumps(result))
                    inspector.check_audit()
                    self.assertEqual(inspector.violations, [])
                finally:
                    store.close()


if __name__ == "__main__":
    unittest.main()
