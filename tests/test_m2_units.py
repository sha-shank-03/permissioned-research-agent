"""Pure/in-memory M2 units. No full agent loop, race thread or HTTP request."""

import copy
import json
import threading
import unittest

from permissioned_research.agent import ResearchAgent
from permissioned_research.cache import BoundedCache, RuntimeCaches
from permissioned_research.connectors import ConnectorTools
from permissioned_research.domain import Action, PROTOCOL, TOPICS, ResearchError, ResearchRequest
from permissioned_research.guard import REASONS, validate_memo
from permissioned_research.providers import LocalProviderAdapter, ScriptedResearchProvider, encode_action
from permissioned_research.store import CorpusStore, load_fixture
from permissioned_research.transport import OllamaWireProvider, _SendBoundedConnection, decode_response, loopback_endpoint, native_tools
from evaluation.inspect import SurfaceInspector


class CacheUnits(unittest.TestCase):
    def test_capacity_ttl_and_defensive_copy(self):
        now = [0.0]
        cache = BoundedCache(2, 10, lambda: now[0])
        cache.put(("a",), {"value": [1]})
        value = cache.get(("a",))
        value["value"].append(99)
        self.assertEqual(cache.get(("a",)), {"value": [1]})
        cache.put(("b",), 2)
        cache.put(("c",), 3)
        self.assertIsNone(cache.get(("a",)))
        now[0] = 10
        self.assertIsNone(cache.get(("b",)))
        self.assertIsNone(cache.get(("c",)))

    def test_epoch_change_purges_both_cache_surfaces(self):
        caches = RuntimeCaches()
        caches.align("corpus", 1)
        key = caches.key("corpus", "priya", 1, "script", "query")
        caches.evidence.put(key, "restricted source")
        caches.memos.put(key, "restricted memo")
        self.assertTrue(caches.align("corpus", 2))
        self.assertEqual(len(caches.evidence.entries), 0)
        self.assertEqual(len(caches.memos.entries), 0)

    def test_identity_and_provider_are_parts_of_key(self):
        caches = RuntimeCaches()
        priya = caches.key("corpus", "priya", 1, "script", "query")
        alex = caches.key("corpus", "alex", 1, "script", "query")
        caches.memos.put(priya, "restricted")
        self.assertIsNone(caches.memos.get(alex))
        self.assertNotEqual(priya, caches.key("corpus", "priya", 1, "other", "query"))

    def test_limits_and_epoch_metadata_are_bounded(self):
        for limit in (0, 33):
            with self.assertRaises(ValueError):
                BoundedCache(limit)
        caches = RuntimeCaches()
        for index in range(40):
            caches.align(str(index), 1)
        self.assertLessEqual(len(caches.observed_epochs), 32)


class EpochAndPermissionUnits(unittest.TestCase):
    def setUp(self):
        self.store = CorpusStore(":memory:", load_fixture("vendor-review"))
        self.addCleanup(self.store.close)

    def test_membership_event_is_atomic_and_idempotent(self):
        before = self.store.epoch
        self.assertTrue(self.store.revoke_membership("priya", "procurement", "revoke-1"))
        after = self.store.epoch
        self.assertGreater(after, before)
        self.assertFalse(self.store.revoke_membership("priya", "procurement", "revoke-1"))
        self.assertEqual(self.store.epoch, after)
        self.assertIsNone(self.store.source_for("priya", "cedar-contract"))

    def test_raw_trusted_sql_cannot_skip_epoch_increment(self):
        before = self.store.epoch
        self.store.connection.execute("UPDATE versions SET body=? WHERE object_id=?", ("Changed source", "cedar-contract"))
        self.assertGreater(self.store.epoch, before)

    def test_new_version_invalidates_handles_and_caches(self):
        caches = RuntimeCaches()
        tools = ConnectorTools(self.store, "priya", caches)
        hit = tools.search("docshub", "PROCUREMENT_ONLY_CEDAR", 1)["hits"][0]
        tools.read(hit["handle"])
        self.store.replace_source("cedar-contract", "Priority support response: 6 hours.\n", "2026-10-01", "2026-12-31", "replace-1")
        self.assertEqual(tools.read(hit["handle"]), {"status": "not_available"})
        self.assertFalse(tools.evidence)
        self.assertFalse(caches.evidence.entries)
        self.assertEqual(self.store.source_for("priya", "cedar-contract").revision, 4)

    def test_cached_search_mints_new_session_handles(self):
        caches = RuntimeCaches()
        first = ConnectorTools(self.store, "priya", caches)
        handle = first.search("docshub", "PROCUREMENT_ONLY_CEDAR", 1)["hits"][0]["handle"]
        second = ConnectorTools(self.store, "priya", caches)
        next_handle = second.search("docshub", "PROCUREMENT_ONLY_CEDAR", 1)["hits"][0]["handle"]
        self.assertEqual(second.cache_hits, 1)
        self.assertNotEqual(handle, next_handle)
        self.assertEqual(second.read(handle), {"status": "not_available"})

    def test_cross_identity_cache_attack_returns_no_restricted_hits(self):
        caches = RuntimeCaches()
        priya = ConnectorTools(self.store, "priya", caches)
        private_handle = priya.search("docshub", "PROCUREMENT_ONLY_CEDAR", 1)["hits"][0]["handle"]
        alex = ConnectorTools(self.store, "alex", caches)
        hits = alex.search("docshub", "PROCUREMENT_ONLY_CEDAR", 4)
        self.assertEqual(hits["hits"], [])
        self.assertEqual(alex.read(private_handle), {"status": "not_available"})

    def test_citation_resolver_rechecks_identity_revision_and_epoch(self):
        source = self.store.source_for("priya", "cedar-contract")
        quote = "Priority support response: 4 hours."
        start = source.body.index(quote)
        citation = {"source_id": source.object_id, "revision": source.revision,
                    "start": start, "end": start + len(quote), "quote": quote}
        tools = ConnectorTools(self.store, "priya")
        self.assertEqual(tools.resolve_citation(citation), {"status": "ok", "quote": quote})
        self.assertEqual(ConnectorTools(self.store, "alex").resolve_citation(citation), {"status": "not_available"})
        self.store.revoke_membership("priya", "procurement", "revoke-citation")
        self.assertEqual(tools.resolve_citation(citation), {"status": "not_available"})

    def test_nested_audit_does_not_release_transaction_early(self):
        with self.store.boundary():
            self.store.audit("a" * 32, "dispatch", "ok")
            self.assertTrue(self.store.connection.in_transaction)
            with self.store.boundary():
                self.assertTrue(self.store.connection.in_transaction)
        self.assertFalse(self.store.connection.in_transaction)

    def test_failed_control_transaction_rolls_back_change_epoch_and_event(self):
        before = self.store.epoch
        with self.assertRaisesRegex(RuntimeError, "abort"):
            with self.store.boundary():
                self.store.revoke_membership("priya", "procurement", "rolled-back")
                raise RuntimeError("abort")
        self.assertEqual(self.store.epoch, before)
        self.assertIsNotNone(self.store.source_for("priya", "cedar-contract"))
        self.assertTrue(self.store.revoke_membership("priya", "procurement", "rolled-back"))

    def test_independent_surface_checker_catches_unique_fragment_not_common_fact(self):
        inspector = SurfaceInspector(self.store, load_fixture("vendor-review"))
        inspector("provider_request", {"principal": "alex", "epoch": self.store.epoch,
                                      "payload": {"quote": "SSO support: yes."}})
        self.assertEqual(inspector.violations, [])
        inspector("provider_request", {"principal": "alex", "epoch": self.store.epoch,
                                      "payload": {"quote": "Onboarding lead time: 10 business days."}})
        self.assertTrue(any(v["kind"] == "forbidden_content" for v in inspector.violations))


class ProviderWireUnits(unittest.TestCase):
    def payload(self, calls=None):
        return {"done": True, "message": {"role": "assistant", "thinking": "PRIVATE_THOUGHT", "content": "UNVALIDATED_PROSE",
                "tool_calls": calls or [{"function": {"name": "plan", "arguments": {"topics": list(TOPICS)}}}]},
                "prompt_eval_count": 11, "eval_count": 7}

    def test_decode_discards_thinking_and_prose(self):
        response = decode_response(json.dumps(self.payload()).encode())
        self.assertEqual(Action.parse(response.decision).name, "plan")
        self.assertEqual((response.prompt_tokens, response.output_tokens), (11, 7))
        self.assertNotIn("PRIVATE_THOUGHT", response.decision)
        self.assertNotIn("UNVALIDATED_PROSE", response.decision)

    def test_missing_multiple_calls_and_invalid_usage_are_rejected(self):
        bad = [b"garbage", json.dumps({"done": True}).encode(), b"x" * 65537]
        payload = self.payload()
        payload["message"]["tool_calls"] *= 2
        bad.append(json.dumps(payload).encode())
        payload = self.payload()
        payload["eval_count"] = True
        bad.append(json.dumps(payload).encode())
        for body in bad:
            with self.assertRaises(ResearchError):
                decode_response(body)

    def test_loopback_only_without_proxy_redirect_or_credentials(self):
        self.assertEqual(loopback_endpoint("http://127.0.0.1:11434"), ("127.0.0.1", 11434))
        self.assertEqual(loopback_endpoint("http://[::1]:12345/"), ("::1", 12345))
        for url in ("https://127.0.0.1", "http://localhost", "http://example.com", "http://u:p@127.0.0.1", "http://127.0.0.1/private", "http://127.0.0.1?x=1", "http://127.0.0.1:0"):
            with self.assertRaises(ResearchError):
                loopback_endpoint(url)

    def test_network_and_cloud_models_are_disabled_by_default(self):
        with self.assertRaises(ResearchError):
            OllamaWireProvider("http://127.0.0.1", "stub", purpose="contract_stub")
        with self.assertRaises(ResearchError):
            OllamaWireProvider("http://127.0.0.1", "model:cloud", purpose="authorized_local", enable_network=True)

    def test_local_submit_does_not_run_inference_or_keep_closed_context(self):
        calls = []
        class Policy:
            def decide(self, context):
                calls.append(context)
                return encode_action("plan", topics=list(TOPICS))
        provider = LocalProviderAdapter(Policy())
        pending = provider.submit({"observations": []}, 10)
        self.assertEqual(calls, [])
        self.assertEqual(Action.parse(provider.collect(pending, 10).decision).name, "plan")
        pending.close()
        self.assertIsNone(pending.context)

    def test_declared_native_tools_have_closed_argument_objects(self):
        schemas = native_tools()
        self.assertEqual({s["function"]["name"] for s in schemas}, {"plan", "revise_plan", "search", "read", "finish"})
        self.assertTrue(all(s["function"]["parameters"]["additionalProperties"] is False for s in schemas))

    def test_duplicate_members_and_nonobject_wire_payloads_fail_closed(self):
        raw = '{"protocol":"research-action-v1","action":"read","action":"plan","arguments":{"topics":' + json.dumps(list(TOPICS)) + '}}'
        with self.assertRaisesRegex(ResearchError, "invalid_decision"):
            Action.parse(raw)
        for body in (b"[]", b'{"message":[],"done":true}', b'{"done":true,"done":false}'):
            with self.assertRaisesRegex(ResearchError, "invalid_decision"):
                decode_response(body)

    def test_expired_send_budget_opens_no_socket(self):
        connection = _SendBoundedConnection("127.0.0.1", 11434, 0)
        self.addCleanup(connection.close)
        with self.assertRaisesRegex(ResearchError, "provider_timeout"):
            connection.send(b"bounded request")
        self.assertIsNone(connection.sock)


class BudgetAndFreshnessUnits(unittest.TestCase):
    def test_oversize_context_never_reaches_provider_submit(self):
        store = CorpusStore(":memory:", load_fixture("vendor-review"))
        self.addCleanup(store.close)
        actor = ResearchAgent(store, ResearchRequest("alex", "CedarBridge", "2026-10-08"), ScriptedResearchProvider(), clock=lambda: 0)
        actor.deadline = 10
        actor.observations = [{"result": "x" * (48 * 1024)}]
        with self.assertRaisesRegex(ResearchError, "budget_exhausted"):
            actor._dispatch()
        self.assertEqual(actor.counters["provider_requests"], 0)

    def test_cancellation_and_deadline_checked_without_agent_loop(self):
        store = CorpusStore(":memory:", load_fixture("vendor-review"))
        self.addCleanup(store.close)
        cancel = threading.Event()
        actor = ResearchAgent(store, ResearchRequest("alex", "CedarBridge", "2026-10-08"), ScriptedResearchProvider(), cancellation=cancel, clock=lambda: 5)
        actor.deadline = 10
        actor._check_budget()
        cancel.set()
        with self.assertRaisesRegex(ResearchError, "cancelled"):
            actor._check_budget()
        cancel.clear()
        actor.deadline = 5
        with self.assertRaisesRegex(ResearchError, "budget_exhausted"):
            actor._check_budget()

    def test_four_authored_families_define_twelve_development_cases(self):
        for scenario in ("vendor-review", "conflicting-review", "stale-review", "missing-review"):
            fixture = load_fixture(scenario)
            self.assertEqual(set(fixture["principals"]), {"alex", "priya", "sam"})

    def test_expired_claim_requires_stale_state_and_future_evidence_is_denied(self):
        fixture = load_fixture("stale-review")
        store = CorpusStore(":memory:", fixture)
        self.addCleanup(store.close)
        request = ResearchRequest("priya", "CedarBridge", "2026-10-08")
        tools = ConnectorTools(store, "priya")
        handle = tools.search("docshub", "PROCUREMENT_ONLY_CEDAR", 1)["hits"][0]["handle"]
        source = tools.read(handle)
        quote = "Priority support response: 4 hours."
        start = source["body"].index(quote)
        claim = {"value": 4, "handle": handle, "start": start, "end": start + len(quote)}
        proposals = [{"topic": topic, "state": "unknown", "reason": REASONS["unknown"], "claims": []} for topic in TOPICS]
        proposals[-1] = {"topic": "support_hours", "state": "stale_only", "reason": REASONS["stale_only"], "claims": [claim]}
        self.assertEqual(validate_memo(proposals, request, tools)["topics"][-1]["state"], "stale_only")
        proposals[-1].update(state="supported", reason=REASONS["supported"])
        with self.assertRaisesRegex(ResearchError, "invalid_memo"):
            validate_memo(proposals, request, tools)
        future = copy.deepcopy(load_fixture("vendor-review"))
        for source_record in future["sources"]:
            source_record["effective_at"] = "2026-11-01"
        future_store = CorpusStore(":memory:", future)
        self.addCleanup(future_store.close)
        future_tools = ConnectorTools(future_store, "priya")
        future_handle = future_tools.search("docshub", "PROCUREMENT_ONLY_CEDAR", 1)["hits"][0]["handle"]
        future_tools.read(future_handle)
        claim = dict(claim, handle=future_handle)
        proposals[-1].update(state="stale_only", reason=REASONS["stale_only"], claims=[claim])
        with self.assertRaisesRegex(ResearchError, "invalid_memo"):
            validate_memo(proposals, request, future_tools)


if __name__ == "__main__":
    unittest.main()
