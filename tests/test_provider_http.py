"""Actual loopback HTTP protocol tests; execute only under M2 integration lease."""

import json
import time
import unittest

from evaluation.inspect import SurfaceInspector
from evaluation.protocol_server import protocol_server
from permissioned_research.agent import ResearchAgent
from permissioned_research.domain import Action, ResearchError, ResearchRequest
from permissioned_research.providers import ScriptedResearchProvider
from permissioned_research.store import CorpusStore, load_fixture
from permissioned_research.transport import OllamaWireProvider


class HttpContractTests(unittest.TestCase):
    def test_actual_http_request_and_tool_response_complete_workflow(self):
        fixture = load_fixture("vendor-review")
        store = CorpusStore(":memory:", fixture)
        self.addCleanup(store.close)
        inspector = SurfaceInspector(store, fixture)
        with protocol_server() as (endpoint, requests):
            provider = OllamaWireProvider(endpoint, "test-double", purpose="contract_stub", enable_network=True)
            result = ResearchAgent(store, ResearchRequest("priya", "CedarBridge", "2026-10-08"), provider, observer=inspector).run()
        self.assertEqual(result["outcome"], "ready_for_review")
        self.assertEqual(result["evidence_kind"], "http_contract_stub")
        self.assertFalse(result["live_model_executed"])
        self.assertEqual(len(requests), result["counters"]["provider_requests"])
        self.assertTrue(all(r["stream"] is False and r["model"] == "test-double" for r in requests))
        self.assertEqual(result["model_tokens"], {"prompt_tokens": 11 * len(requests), "output_tokens": 7 * len(requests)})
        self.assertEqual(inspector.violations, [])

    def test_http_alex_requests_never_include_procurement_source_text(self):
        store = CorpusStore(":memory:", load_fixture("vendor-review"))
        self.addCleanup(store.close)
        with protocol_server() as (endpoint, requests):
            provider = OllamaWireProvider(endpoint, "test-double", purpose="contract_stub", enable_network=True)
            result = ResearchAgent(store, ResearchRequest("alex", "CedarBridge", "2026-10-08"), provider).run()
        self.assertEqual(result["outcome"], "needs_follow_up")
        self.assertNotIn("PROCUREMENT", json.dumps(requests))

    def test_non_json_non_200_malformed_and_oversize_responses_fail_closed(self):
        responses = [(503, b"private unavailable detail", "application/json"),
                     (200, b"private HTML body", "text/html"),
                     (200, b"not JSON", "application/json"),
                     (200, b"x" * 65537, "application/json")]
        for response in responses:
            with self.subTest(status=response[0], content_type=response[2], bytes=len(response[1])):
                with protocol_server(lambda request: response) as (endpoint, _):
                    provider = OllamaWireProvider(endpoint, "test-double", purpose="contract_stub", enable_network=True)
                    pending = provider.submit({"observations": []}, time.monotonic() + 2)
                    with self.assertRaises(ResearchError):
                        provider.collect(pending, time.monotonic() + 2)
                    pending.close()

    def test_closed_connection_is_typed_failure_without_raw_body(self):
        with protocol_server() as (endpoint, _):
            pass
        provider = OllamaWireProvider(endpoint, "test-double", purpose="contract_stub", enable_network=True)
        with self.assertRaisesRegex(ResearchError, "provider_error|provider_timeout"):
            provider.submit({"observations": []}, time.monotonic() + 1)


if __name__ == "__main__":
    unittest.main()
