"""Pure U1 units: no sockets, child processes, corpus actor runs or live keys."""
import copy
import io
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import Mock

from permissioned_research.domain import ResearchError, TOPICS
from permissioned_research.upgrade.__main__ import reserve_output
from permissioned_research.upgrade.config import DOUBLE_MODEL, DispatchConfig, live_preflight
from permissioned_research.upgrade.double import completion
from permissioned_research.upgrade.events import Events, safe_fields
from permissioned_research.upgrade.gateway import GatewayProvider
from permissioned_research.upgrade.protocol import History, ToolCall, canonical, context_from_messages, decode
from permissioned_research.upgrade.transport import DoubleEndpoint, GatewayTransport, Halt, Pending, http_error


def context(observations=None, error=None):
    return {'request': {'vendor': 'CedarBridge', 'as_of': '2026-10-08'},
            'phase': 'PLANNING', 'observations': observations or [],
            'remaining_decisions': 12, 'last_error': error}


def events(config):
    value = Events(config, 'd' * 64, 'c' * 64)
    value.bind('a' * 32)
    return value


def temporary_directory():
    # Keep even ephemeral test writes inside the assigned repository.
    root = Path(__file__).resolve().parents[1] / 'work/upgrade-u1-pure-units'
    root.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(dir=root)


class ConfigUnits(unittest.TestCase):
    def test_explicit_model_and_bounded_config(self):
        self.assertEqual(DispatchConfig(DOUBLE_MODEL).sha256, DispatchConfig(DOUBLE_MODEL).sha256)
        for fields in ({'model': 'worker-chat-model'}, {'max_requests': 13}, {'max_reads': True},
                       {'max_searches': 7}, {'total_seconds': float('nan')}, {'submit_seconds': 6},
                       {'total_seconds': 1}, {'max_response_bytes': 65537}, {'max_output_tokens': 2049}):
            with self.subTest(fields=fields), self.assertRaises(ResearchError):
                DispatchConfig(**{'model': DOUBLE_MODEL, **fields})

    def test_zero_allocation_never_calls_credential_loader(self):
        loader = Mock(side_effect=AssertionError('credential access'))
        with self.assertRaisesRegex(ResearchError, 'gateway_allocation_required'):
            live_preflight(DispatchConfig('provider/explicit-model'), loader)
        loader.assert_not_called()

    def test_no_endpoint_is_live_block_before_transport(self):
        with self.assertRaisesRegex(ResearchError, 'gateway_allocation_required'):
            GatewayTransport(DispatchConfig(DOUBLE_MODEL))

    def test_arbitrary_origin_or_live_model_cannot_use_double(self):
        for fields in ({'host': 'example.org', 'port': 80}, {'host': '127.0.0.1', 'port': True}):
            with self.assertRaises(ResearchError):
                DoubleEndpoint(**fields)
        with self.assertRaises(ResearchError):
            GatewayTransport(DispatchConfig('provider/live'), endpoint=DoubleEndpoint(8080))

    def test_existing_output_or_sidecar_cannot_reset_attempt(self):
        with temporary_directory() as directory:
            path = Path(directory) / 'run.json'
            handle, event, state = reserve_output(path)
            handle.close()
            with self.assertRaisesRegex(ResearchError, 'attempt_state_conflict'):
                reserve_output(path)
            other = Path(directory) / 'other.json'
            other.with_suffix('.events.jsonl').write_text('partial metadata\n')
            with self.assertRaises(ResearchError):
                reserve_output(other)
            self.assertFalse(other.exists())


class DecoderUnits(unittest.TestCase):
    def setUp(self):
        self.config = DispatchConfig(DOUBLE_MODEL)
        self.body = completion('plan', {'topics': list(TOPICS)})

    def test_one_native_function_decodes_with_authored_usage(self):
        decoded = decode(self.body, self.config, set())
        self.assertEqual(decoded.call, ToolCall('call_1', 'plan', {'topics': list(TOPICS)}))
        self.assertEqual((decoded.response.prompt_tokens, decoded.response.output_tokens), (11, 7))

    def test_absent_usage_remains_unknown(self):
        result = decode(completion('plan', {'topics': list(TOPICS)}, usage=False), self.config, set())
        self.assertIsNone(result.response.prompt_tokens)
        self.assertIsNone(result.response.output_tokens)

    def test_nested_malformed_shapes_fail_as_typed_errors(self):
        value = json.loads(self.body)
        mutations = [lambda v: v.update(choices=[[]]),
                     lambda v: v['choices'][0].update(message=[]),
                     lambda v: v['choices'][0]['message'].update(tool_calls=[[]]),
                     lambda v: v['choices'][0]['message']['tool_calls'][0].update(function=[])]
        for mutation in mutations:
            changed = copy.deepcopy(value)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaisesRegex(ResearchError, '^invalid_decision$'):
                decode(canonical(changed).encode(), self.config, set())

    def test_multiple_unknown_or_authority_arguments_are_rejected(self):
        variants = [completion('arbitrary_shell', {}),
                    completion('plan', {'topics': list(TOPICS), 'principal': 'priya'}),
                    completion('search', {'connector': 'docshub', 'query': 'CedarBridge', 'limit': True})]
        value = json.loads(self.body)
        value['choices'][0]['message']['tool_calls'] *= 2
        variants.append(canonical(value).encode())
        for body in variants:
            with self.assertRaises(ResearchError):
                decode(body, self.config, set())

    def test_refusal_truncation_wrong_model_and_prose_rejected(self):
        for where, name, replacement in [('message', 'refusal', 'CANARY'), ('message', 'content', 'CANARY'),
                                          ('choice', 'finish_reason', 'length'), ('root', 'model', 'provider/other')]:
            value = json.loads(self.body)
            target = value if where == 'root' else value['choices'][0] if where == 'choice' else value['choices'][0]['message']
            target[name] = replacement
            with self.assertRaises(ResearchError):
                decode(canonical(value).encode(), self.config, set())

    def test_duplicate_keys_call_ids_nonfinite_and_response_cap(self):
        for body, used in [(b'{"model":1,"model":2}', set()), (self.body, {'call_1'}),
                           (b'{"usage":NaN}', set()), (b'x' * 65537, set())]:
            with self.assertRaises(ResearchError):
                decode(body, self.config, used)

    def test_bad_usage_never_becomes_reported_tokens(self):
        for value in [True, -1, 10**9 + 1, '11']:
            body = json.loads(self.body)
            body['usage']['prompt_tokens'] = value
            with self.assertRaises(ResearchError):
                decode(canonical(body).encode(), self.config, set())
        body = json.loads(self.body)
        body['usage']['completion_tokens'] = 2049
        with self.assertRaises(ResearchError):
            decode(canonical(body).encode(), self.config, set())


class HistoryUnits(unittest.TestCase):
    def setUp(self):
        self.config, self.history = DispatchConfig(DOUBLE_MODEL), History()

    def test_native_correlation_replays_only_actual_observation(self):
        call = ToolCall('call_plan', 'plan', {'topics': list(TOPICS)})
        self.history.build(context(), self.config)
        self.history.received(call)
        observations = [{'action': 'plan', 'arguments': call.arguments, 'result': {'status': 'ok'}}]
        request = self.history.build(context(observations), self.config)
        self.assertEqual(request['messages'][2]['tool_calls'][0]['id'], request['messages'][3]['tool_call_id'])
        self.assertEqual(context_from_messages(request)['observations'], observations)
        self.assertNotIn('principal', canonical(request))
        self.assertNotIn('scenario', canonical(request))
        self.assertIs(request['parallel_tool_calls'], False)

    def test_repair_is_correlated_error_without_uncorrelated_result(self):
        call = ToolCall('finish_bad', 'finish', {'topics': []})
        self.history.build(context(), self.config)
        self.history.received(call)
        request = self.history.build(context(error='invalid_memo'), self.config)
        tool = request['messages'][3]
        self.assertEqual(tool['tool_call_id'], 'finish_bad')
        self.assertEqual(json.loads(tool['content']), {'error': 'invalid_memo', 'status': 'error'})
        self.assertEqual(context_from_messages(request)['observations'], [])

    def test_reset_drops_private_arguments_and_keeps_id_uniqueness(self):
        self.history.received(ToolCall('private_call', 'search', {'query': 'PRIVATE_QUERY_CANARY'}))
        self.history.clear()
        self.assertNotIn('PRIVATE_QUERY_CANARY', canonical(self.history.build(context(), self.config)))
        self.assertIn('private_call', self.history.used_ids)

    def test_mismatched_or_unmatched_observation_is_not_tool_result(self):
        self.history.build(context(), self.config)
        self.history.received(ToolCall('p', 'plan', {'topics': list(TOPICS)}))
        with self.assertRaises(ResearchError):
            self.history.build(context([{'action': 'read', 'arguments': {}, 'result': {'status': 'ok'}}]), self.config)

    def test_native_wire_size_has_a_separate_cap(self):
        with self.assertRaisesRegex(ResearchError, 'budget_exhausted'):
            self.history.build(context(), DispatchConfig(DOUBLE_MODEL, max_context_bytes=100))


class ProviderUnits(unittest.TestCase):
    def setUp(self):
        self.config = DispatchConfig(DOUBLE_MODEL, max_requests=2)
        self.events = events(self.config)
        self.transport = Mock(evidence_kind='local_double', live_model_executed=False)
        self.pending = Mock(sent=True)
        self.transport.submit.return_value = self.pending
        self.provider = GatewayProvider(self.config, self.transport, self.events)

    def test_http_halt_is_not_retried_or_relabelled(self):
        for status, expected in [(401, 'gateway_authentication_required'), (402, 'gateway_budget_required'),
                                 (403, 'gateway_verification_required'), (429, 'gateway_rate_limited'),
                                 (500, 'gateway_unavailable')]:
            self.assertEqual(http_error(status), expected)
        self.transport.collect.side_effect = Halt('gateway_rate_limited', status=429)
        pending = self.provider.submit(context(), 100)
        with self.assertRaisesRegex(ResearchError, 'gateway_rate_limited'):
            self.provider.collect(pending, 100)
        self.assertEqual(self.transport.submit.call_count, 1)
        self.assertEqual(self.transport.collect.call_count, 1)
        self.assertEqual(self.provider.transport_retries, 0)

    def test_epoch_history_reset_keeps_cumulative_request_cap(self):
        self.provider.submit(context(), 100)
        self.provider.reset_history()
        self.provider.submit(context(), 100)
        self.provider.reset_history()
        with self.assertRaisesRegex(ResearchError, 'budget_exhausted'):
            self.provider.submit(context(), 100)
        self.assertEqual(self.transport.submit.call_count, 2)

    def test_missing_send_ack_fails_before_return(self):
        self.pending.sent = False
        with self.assertRaisesRegex(ResearchError, 'gateway_response_invalid'):
            self.provider.submit(context(), 100)
        self.pending.close.assert_called()

    def test_event_failure_after_send_closes_owned_pending(self):
        actual = self.events.emit
        def emit(event, **fields):
            if event == 'request_sent':
                raise OSError('UNTRUSTED_STORAGE_CANARY')
            actual(event, **fields)
        self.events.emit = emit
        with self.assertRaises(OSError):
            self.provider.submit(context(), 100)
        self.pending.close.assert_called_once()


class MetadataAndDeadlineUnits(unittest.TestCase):
    def test_allowlist_discards_entire_untrusted_envelope(self):
        writer = events(DispatchConfig(DOUBLE_MODEL))
        envelope = {'principal': 'REAL_ID_CANARY', 'payload': {'title': 'TITLE_CANARY', 'body': 'BODY_CANARY',
                    'prompt': 'PROMPT_CANARY', 'query': 'QUERY_CANARY', 'key': 'SECRET_CANARY', 'error': 'ERROR_CANARY'}}
        for surface in ['provider_request', 'output', 'tool_output', 'cache_lookup', 'invalidated']:
            writer.observe(surface, envelope)
        serialized = canonical(writer.rows)
        self.assertNotIn('CANARY', serialized)
        self.assertTrue(all(row['actual_cost'] is None and row['generation_id'] is None for row in writer.rows))

    def test_dynamic_fields_and_enum_values_cannot_be_serialized(self):
        for fields in [{'body': 'CANARY'}, {'error': 'CANARY'}, {'outcome': []}, {'bytes': True},
                       {'action': {}}, {'request_number': 10**9 + 1}]:
            with self.assertRaises(ResearchError):
                safe_fields(fields)

    def test_persistent_events_are_metadata_only_and_exclusive(self):
        with temporary_directory() as directory:
            path = Path(directory) / 'events.jsonl'
            writer = Events(DispatchConfig(DOUBLE_MODEL), 'd' * 64, 'c' * 64, path)
            writer.bind('a' * 32)
            writer.emit('request_started', request_number=1, bytes=400)
            writer.observe('provider_request', {'payload': 'SOURCE_CANARY'})
            writer.close()
            self.assertNotIn('CANARY', path.read_text())
            with self.assertRaises(FileExistsError):
                Events(DispatchConfig(DOUBLE_MODEL), 'd' * 64, 'c' * 64, path)

    def test_parent_deadline_and_cancel_are_pure_monotonic_checks(self):
        pending = Pending(Mock(), queue.Queue(), Mock(), Mock(), DispatchConfig(DOUBLE_MODEL),
                          10, threading.Event(), clock=lambda: 11)
        with self.assertRaisesRegex(ResearchError, 'provider_timeout'):
            pending.wait(20)
        pending.cancellation.set()
        with self.assertRaisesRegex(ResearchError, 'cancelled') as caught:
            pending.wait(20)
        self.assertTrue(caught.exception.possibly_sent)

    def test_cleanup_reports_success_only_after_process_and_threads_stop(self):
        process, reader, writer = Mock(), Mock(), Mock()
        process.poll.return_value = 0
        process.stdin, process.stdout = io.BytesIO(), io.BytesIO()
        reader.is_alive.return_value = writer.is_alive.return_value = False
        pending = Pending(process, queue.Queue(), reader, writer, DispatchConfig(DOUBLE_MODEL),
                          10, threading.Event(), clock=lambda: 0)
        pending.close()
        pending.close()
        self.assertTrue(pending.closed)
        self.assertEqual(process.wait.call_count, 1)
        self.assertTrue(process.stdin.closed)
        reader.is_alive.return_value = True
        pending.closed = False
        with self.assertRaisesRegex(ResearchError, 'provider_error'):
            pending.close()
        self.assertFalse(pending.closed)

    def test_cleanup_handles_pipe_threads_that_never_started(self):
        process = Mock()
        process.poll.return_value = 0
        process.stdin, process.stdout = io.BytesIO(), io.BytesIO()
        reader, writer = threading.Thread(target=lambda: None), threading.Thread(target=lambda: None)
        pending = Pending(process, queue.Queue(), reader, writer, DispatchConfig(DOUBLE_MODEL),
                          10, threading.Event(), clock=lambda: 0)
        pending.close()
        self.assertTrue(pending.closed)
        self.assertIsNone(reader.ident)
        self.assertIsNone(writer.ident)


if __name__ == '__main__':
    unittest.main()
