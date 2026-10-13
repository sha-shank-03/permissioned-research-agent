"""UNEXECUTED U1 socket/process/controller tests; require a named heavy lease."""
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest

from evaluation.inspect import SurfaceInspector
from permissioned_research.domain import ResearchError
from permissioned_research.store import CorpusStore, load_fixture
from permissioned_research.upgrade.config import DOUBLE_MODEL, DispatchConfig
from permissioned_research.upgrade.double import gateway_double
from permissioned_research.upgrade.runner import run_scalar
from permissioned_research.upgrade.transport import GatewayTransport


def temporary_directory():
    root = Path(__file__).resolve().parents[1] / 'work/upgrade-u1-transport-tests'
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


class GatewayTransportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture('vendor-review')
        self.store = CorpusStore(':memory:', self.fixture)
        self.addCleanup(self.store.close)

    def run_case(self, *, principal='priya', fault=None, config=None, hooks=None, cancellation=None, handler=None):
        config = config or DispatchConfig(DOUBLE_MODEL)
        inspector = SurfaceInspector(self.store, self.fixture)
        with gateway_double(fault=fault, handler=handler) as (endpoint, captured):
            report = run_scalar(self.store, self.fixture, principal, config, endpoint,
                                observer=inspector, hooks=hooks, cancellation=cancellation)
        self.assertEqual(inspector.violations, [])
        inspector.check_audit()
        self.assertEqual(inspector.violations, [])
        self.assertEqual(report['metrics']['owned_children_remaining'], 0)
        self.assertEqual(report['metrics']['owned_client_threads_remaining'], 0)
        self.assertEqual(report['metrics']['automatic_inference_transport_retries'], 0)
        self.assertFalse(report['live_model_executed'])
        return report, captured

    def test_native_scalar_journey_and_correlated_tools(self):
        report, captured = self.run_case()
        self.assertEqual(report['result']['outcome'], 'ready_for_review')
        self.assertEqual(len(captured), report['metrics']['provider_requests'])
        self.assertEqual(report['gateway_reported_tokens'], {'prompt_tokens': 11 * len(captured), 'output_tokens': 7 * len(captured)})
        for request in captured:
            self.assertNotIn('principal', json.dumps(request))
            self.assertNotIn('scenario', json.dumps(request))
            self.assertIs(request['tool_choice'] == 'required', True)
            messages = request['messages']
            for index in range(2, len(messages) - 1, 2):
                self.assertEqual(messages[index]['tool_calls'][0]['id'], messages[index + 1]['tool_call_id'])
        claims = [c for t in report['result']['memo']['topics'] for c in t['claims']]
        self.assertTrue(all(c['citation']['quote'] for c in claims))
        self.assertEqual(report['metrics']['finish_proposals'], 1)
        self.assertEqual(report['metrics']['guarded_releases'], 1)

    def test_alex_gap_changes_observed_decisions_without_private_text(self):
        report, captured = self.run_case(principal='alex')
        self.assertEqual(report['result']['outcome'], 'needs_follow_up')
        wire = json.dumps(captured)
        self.assertNotIn('PROCUREMENT', wire)
        self.assertNotIn('SECURITY_ONLY', wire)
        self.assertIn('CedarBridge resolution', wire)
        topics = {t['topic']: t for t in report['result']['memo']['topics']}
        self.assertEqual(topics['support_hours']['state'], 'unknown')

    def test_conflict_is_preserved_and_triggers_follow_up(self):
        self.fixture = load_fixture('conflicting-review')
        self.store.close()
        self.store = CorpusStore(':memory:', self.fixture)
        self.addCleanup(self.store.close)
        report, captured = self.run_case()
        self.assertEqual(report['result']['outcome'], 'needs_follow_up',
                         json.dumps({'error': report['result']['error'], 'counters': report['result']['counters']}))
        topics = {t['topic']: t for t in report['result']['memo']['topics']}
        self.assertEqual(topics['support_hours']['state'], 'conflict')
        self.assertEqual({c['value'] for c in topics['support_hours']['claims']}, {4, 8})
        self.assertIn('CedarBridge resolution', json.dumps(captured))
        self.assertLessEqual(report['result']['counters']['provider_requests'], 12)
        self.assertLessEqual(report['result']['counters']['searches'], 6)
        self.assertLessEqual(report['result']['counters']['reads'], 8)

    def test_http_halts_have_one_request_unknown_cost_and_typed_handoff(self):
        for status, code in [(401, 'gateway_authentication_required'), (402, 'gateway_budget_required'),
                             (403, 'gateway_verification_required'), (429, 'gateway_rate_limited'),
                             (500, 'gateway_unavailable')]:
            with self.subTest(status=status):
                report, captured = self.run_case(fault=status)
                self.assertEqual(report['result']['outcome'], 'failed')
                self.assertEqual(report['result']['error'], code)
                self.assertEqual(len(captured), 1)
                self.assertIsNone(report['actual_cost'])
                self.assertNotIn('UNTRUSTED_REMOTE_ERROR_CANARY', json.dumps(report))
                if status in (401, 402, 403):
                    self.assertEqual(report['operational_status'], 'human_handoff')

    def test_ambiguous_drop_is_not_resent(self):
        report, captured = self.run_case(fault='drop')
        self.assertEqual(report['result']['error'], 'gateway_response_lost')
        self.assertEqual(len(captured), 1)
        self.assertIsNone(report['actual_cost'])

    def test_malformed_gets_one_new_repair_decision_and_oversize_gets_none(self):
        report, captured = self.run_case(fault='malformed')
        self.assertEqual(report['result']['error'], 'invalid_decision')
        self.assertEqual(report['result']['counters']['repairs'], 1)
        self.assertEqual(len(captured), 2)
        report, captured = self.run_case(fault='oversize')
        self.assertEqual(report['result']['error'], 'gateway_response_invalid')
        self.assertEqual(len(captured), 1)

    def test_false_finish_is_proposed_twice_but_guard_releases_nothing(self):
        report, captured = self.run_case(fault='false_finish')
        self.assertEqual(report['result']['outcome'], 'failed')
        self.assertEqual(report['result']['error'], 'invalid_memo')
        self.assertIsNone(report['result']['memo'])
        self.assertEqual(report['metrics']['finish_proposals'], 2)
        self.assertEqual(report['metrics']['guarded_releases'], 0)
        self.assertEqual(report['metrics']['blocked_finish_proposals'], 2)

    def test_headers_and_slow_drip_body_obey_total_deadline(self):
        config = DispatchConfig(DOUBLE_MODEL, total_seconds=2, request_seconds=0.7, submit_seconds=0.6, cleanup_seconds=0.5)
        for fault in ['headers', 'body']:
            with self.subTest(fault=fault):
                began = time.monotonic()
                report, captured = self.run_case(fault=fault, config=config)
                self.assertEqual(report['result']['error'], 'provider_timeout')
                self.assertEqual(len(captured), 1)
                self.assertLess(time.monotonic() - began, 2.3)

    def test_owned_blocking_dns_tls_send_phases_are_killed_before_deadline(self):
        # These synthetic stalls prove supervision, not real remote DNS/TLS.
        config = DispatchConfig(DOUBLE_MODEL, total_seconds=2, request_seconds=0.7, submit_seconds=0.3, cleanup_seconds=0.5)
        for phase in ['dns', 'tls', 'send']:
            with self.subTest(phase=phase), gateway_double() as (endpoint, captured):
                transport = GatewayTransport(config, endpoint=endpoint, test_stall=phase)
                began = time.monotonic()
                report = run_scalar(self.store, self.fixture, 'priya', config, endpoint, transport=transport)
                self.assertEqual(report['result']['error'], 'provider_timeout')
                self.assertEqual(captured, [])
                self.assertEqual(report['metrics']['owned_children_remaining'], 0)
                self.assertEqual(report['metrics']['owned_client_threads_remaining'], 0)
                self.assertLess(time.monotonic() - began, 2)

    def test_cancel_before_submit_and_after_actual_send_never_resends(self):
        for cancel_after_send in (False, True):
            with self.subTest(after_send=cancel_after_send):
                cancelled = threading.Event()
                if not cancel_after_send:
                    cancelled.set()
                hooks = {'after_dispatch': lambda actor: cancelled.set()} if cancel_after_send else {}
                report, captured = self.run_case(hooks=hooks, cancellation=cancelled)
                self.assertEqual(report['result']['outcome'], 'cancelled')
                self.assertEqual(len(captured), int(cancel_after_send))
                self.assertIsNone(report['actual_cost'])

    def test_revocation_during_collection_restarts_with_no_old_private_history(self):
        revoked = False
        def after_send(actor):
            nonlocal revoked
            if not revoked and actor.counters['provider_requests'] == 3:
                revoked = True
                self.store.revoke_membership('priya', 'procurement', 'u1-revoke')
        report, captured = self.run_case(hooks={'after_dispatch': after_send})
        self.assertTrue(revoked)
        self.assertEqual(report['result']['counters']['restarts'], 1)
        self.assertEqual(report['result']['outcome'], 'needs_follow_up',
                         json.dumps({'error': report['result']['error'], 'counters': report['result']['counters']}))
        self.assertIn('PROCUREMENT', json.dumps(captured[2]))
        self.assertNotIn('PROCUREMENT', json.dumps(captured[3:]))
        self.assertEqual(len(captured), report['metrics']['provider_requests'])
        self.assertGreaterEqual(report['gateway_reported_tokens']['prompt_tokens'], report['result']['model_tokens']['prompt_tokens'])
        self.assertLessEqual(report['result']['counters']['provider_requests'], 12)
        self.assertLessEqual(report['result']['counters']['searches'], 6)
        self.assertLessEqual(report['result']['counters']['reads'], 8)

    def test_second_epoch_change_aborts_with_cumulative_counters(self):
        def after_send(actor):
            if actor.counters['provider_requests'] in (2, 3):
                self.store.delete_object('cedar-security' if actor.counters['provider_requests'] == 2 else
                                         'cedar-general', 'u1-change-' + str(actor.counters['provider_requests']))
        report, captured = self.run_case(hooks={'after_dispatch': after_send})
        self.assertEqual(report['result']['error'], 'source_state_changed')
        self.assertEqual(report['result']['counters']['restarts'], 1)
        self.assertEqual(len(captured), 3)
        self.assertIsNone(report['result']['memo'])

    def test_send_ack_is_inside_sql_gate_and_collection_allows_writer_commit(self):
        with temporary_directory() as directory:
            path = Path(directory) / 'state.sqlite3'
            store = CorpusStore(path, self.fixture)
            config = DispatchConfig(DOUBLE_MODEL)
            started, committed = threading.Event(), threading.Event()
            failures, checked = [], False

            def writer():
                connection = sqlite3.connect(path, timeout=2, isolation_level=None)
                try:
                    started.set()
                    connection.execute('BEGIN IMMEDIATE')
                    connection.execute("DELETE FROM memberships WHERE principal='priya' AND group_name='procurement'")
                    connection.execute('COMMIT')
                    committed.set()
                except sqlite3.Error:
                    failures.append('writer_failed')
                finally:
                    connection.close()

            writer_thread = threading.Thread(target=writer, name='u1-acl-writer')

            class ObservedTransport(GatewayTransport):
                def submit(inner, request, deadline):
                    nonlocal checked
                    pending = super().submit(request, deadline)
                    if not checked:
                        checked = True
                        writer_thread.start()
                        self.assertTrue(started.wait(0.5))
                        self.assertFalse(committed.wait(0.05))
                        self.assertTrue(pending.sent)
                        self.assertTrue(store.connection.in_transaction)
                    return pending

                def collect(inner, pending, deadline):
                    self.assertFalse(store.connection.in_transaction)
                    self.assertTrue(committed.wait(1))
                    return super().collect(pending, deadline)

            try:
                with gateway_double() as (endpoint, captured):
                    transport = ObservedTransport(config, endpoint=endpoint)
                    report = run_scalar(store, self.fixture, 'priya', config, endpoint, transport=transport)
                self.assertEqual(failures, [])
                self.assertEqual(report['result']['outcome'], 'needs_follow_up')
                self.assertEqual(report['result']['counters']['restarts'], 1)
                self.assertEqual(report['metrics']['owned_children_remaining'], 0)
            finally:
                try:
                    if writer_thread.ident is not None:
                        writer_thread.join(timeout=2.2)
                        self.assertFalse(writer_thread.is_alive())
                finally:
                    store.close()


if __name__ == '__main__':
    unittest.main()
