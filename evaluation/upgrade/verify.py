"""Read-only U1 inspection from raw SQL and public development labels.

No acting controller, runtime ACL filter, runtime memo guard or policy imports.
This checks the four authored scalar relationships, not narrative entailment.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

from evaluation.oracle import Snapshot, typed

ROOT = Path(__file__).resolve().parents[2]
REASONS = {'supported': 'current_evidence', 'conflict': 'unresolved_conflict',
           'stale_only': 'evidence_not_current', 'unknown': 'insufficient_accessible_evidence'}
PATTERNS = {'hosting_region': r'Hosting region: (EU|US|IN)\.', 'sso': r'SSO support: (yes|no)\.',
            'onboarding_days': r'Onboarding lead time: ([0-9]{1,3}) business days\.',
            'support_hours': r'Priority support response: ([0-9]{1,3}) hours\.'}


def source_check():
    manifest = json.loads((ROOT / 'evaluation/upgrade/U1_SOURCE_MANIFEST.json').read_text())
    failures, checked = [], 0
    for entry in manifest['files'] + manifest['preserved_v1_files'] + manifest['preserved_v1_freeze_artifacts']:
        path = ROOT / entry['path']
        checked += 1
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry['sha256']:
            failures.append('source_hash_mismatch:' + entry['path'])
    return {'mode': 'source_inspection', 'manifest_id': manifest['manifest_id'],
            'manifest_sha256': hashlib.sha256((ROOT / 'evaluation/upgrade/U1_SOURCE_MANIFEST.json').read_bytes()).hexdigest(),
            'passed': not failures, 'files_checked': checked, 'errors': failures}


def inspect_report(path):
    report = json.loads(path.read_text(encoding='utf-8'))
    result = report['result']
    errors = []
    def check(condition, code):
        if not condition:
            errors.append(code)
    check(report['schema'] == 'research-run-u1-v1' and report['mode'] == 'local_double' and
          report['live_model_executed'] is False and result['live_model_executed'] is False and
          result['evidence_kind'] == 'local_double', 'evidence_mode_invalid')
    check(report['actual_cost'] is None and report['generation_id'] is None and
          report['usage_kind'] == 'authored_double_or_unknown', 'fabricated_live_metadata')
    labels = json.loads((ROOT / 'evaluation/labels/dev.json').read_text())['cases']
    label = next(x for x in labels if x['scenario'] == report['scenario'] and x['principal'] == result['principal'])
    fixture = json.loads((ROOT / ('fixtures/dev/' + report['scenario'] + '.json')).read_text())
    dataset_hash = hashlib.sha256(json.dumps(fixture, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    check(report['dataset_sha256'] == dataset_hash, 'fixture_hash_mismatch')
    connection = sqlite3.connect(path.with_suffix('.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        snapshot = Snapshot.from_sql(connection)
        receipt = connection.execute('SELECT principal,outcome,decisions,searches,reads FROM run_receipts WHERE run_id=?',
                                     (result['run_id'],)).fetchone()
        counters = result['counters']
        check(receipt == (result['principal'], result['outcome'], counters['decisions'], counters['searches'], counters['reads']),
              'raw_receipt_mismatch')
        terminal = connection.execute('SELECT outcome,ended_at,decisions FROM run_attempts WHERE attempt_id=?',
                                      (result['run_id'],)).fetchone()
        check(terminal is not None and terminal[0] == result['outcome'] and terminal[1] is not None and
              terminal[2] == counters['decisions'], 'raw_attempt_mismatch')
        check(result['outcome'] == label['expected_outcome'] and result['error'] is None, 'wrong_guarded_outcome')
        check(all(type(counters[k]) is int and 0 <= counters[k] <= n for k, n in
                  [('provider_requests', 12), ('decisions', 12), ('searches', 6), ('reads', 8), ('restarts', 1), ('repairs', 1)]),
              'cumulative_budget_invalid')
        metrics = report['metrics']
        check(metrics['automatic_inference_transport_retries'] == 0 and metrics['owned_children_remaining'] == 0 and
              metrics['owned_client_threads_remaining'] == 0 and metrics['owned_loopback_doubles_remaining'] == 0 and
              report['resume_supported'] is False, 'retry_cleanup_or_resume_invalid')
        rows = [json.loads(line) for line in path.with_suffix('.events.jsonl').read_text().splitlines()]
        starts = [r for r in rows if r['event'] == 'request_started']
        sent = [r for r in rows if r['event'] == 'request_sent']
        check(len(starts) == len(sent) == report['observed_double_requests'] == metrics['provider_requests'] == counters['provider_requests'],
              'request_counts_mismatch')
        check([r['request_number'] for r in starts] == list(range(1, len(starts) + 1)), 'request_sequence_mismatch')
        check(all(r['run_id'] == result['run_id'] and r['config_sha256'] == report['config_sha256'] and
                  r['prompt_sha256'] == report['prompt_sha256'] and r['dataset_sha256'] == dataset_hash and
                  r['code_sha256'] == report['code_sha256'] for r in rows), 'event_binding_mismatch')
        allowed = {'schema', 'version', 'mode', 'live_model_executed', 'model', 'provider', 'config_sha256',
                   'prompt_sha256', 'dataset_sha256', 'code_sha256', 'generation_id', 'actual_cost', 'estimated_cost',
                   'usage_kind', 'run_id', 'sequence', 'event', 'elapsed_seconds', 'request_number', 'bytes',
                   'prompt_tokens', 'output_tokens', 'http_status', 'error', 'outcome', 'possibly_sent', 'action', 'count'}
        check(all(set(r) <= allowed for r in rows), 'event_schema_has_content_fields')
        event_text = json.dumps(rows)
        check(all(s['title'] not in event_text and s['body'] not in event_text and s['object_id'] not in event_text
                  for s in fixture['sources']), 'source_text_in_events')
        memo = result['memo']
        check(memo['vendor'] == fixture['vendor'] and memo['as_of'] == fixture['as_of'] and
              memo['outcome'] == result['outcome'], 'memo_binding_mismatch')
        topics = {t['topic']: t for t in memo['topics']}
        check(len(memo['topics']) == len(topics) == 4 and set(topics) == set(PATTERNS), 'topic_inventory_invalid')
        citations = 0
        for topic, expected in label['topics'].items():
            actual = topics[topic]
            check(actual['state'] == expected['state'] and actual['reason'] == REASONS[expected['state']], 'wrong_topic_state')
            check({typed(c['value']) for c in actual['claims']} == {typed(v) for v in expected['values']}, 'wrong_scalar_values')
            for claim in actual['claims']:
                citations += 1
                cite = claim['citation']
                source = snapshot.current_source(cite['source_id'])
                permitted = source is not None and result['principal'] in source.allowed and source.source_id in label['allowed_sources']
                check(permitted, 'unauthorized_citation')
                if not permitted:
                    continue
                check(type(cite['start']) is int and type(cite['end']) is int and
                      0 <= cite['start'] < cite['end'] <= len(source.body) and
                      source.body[cite['start']:cite['end']] == cite['quote'] and source.revision == cite['revision'] and
                      source.connector == cite['connector'] and source.title == cite['title'], 'inexact_citation')
                match = re.fullmatch(PATTERNS[topic], cite['quote'])
                value = None
                if match:
                    value = match[1] == 'yes' if topic == 'sso' else int(match[1]) if topic in {'onboarding_days', 'support_hours'} else match[1]
                check(match is not None and typed(value) == typed(claim['value']), 'unsupported_scalar_quote')
                fresh = source.effective_at <= fixture['as_of'] <= source.review_until
                check(fresh if actual['state'] in {'supported', 'conflict'} else
                      source.effective_at <= fixture['as_of'] and not fresh, 'wrong_citation_freshness')
        return {'mode': 'local_double_scalar_inspection', 'passed': not errors,
                'citations_checked': citations, 'requests_checked': len(starts), 'errors': sorted(set(errors)),
                'wire_disclosure_inspection': 'requires_in_memory_transport_tests', 'narrative_entailment_checked': False}
    finally:
        connection.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description='Independent U1 scalar SQL/span inspection; no actor execution.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--source', action='store_true')
    group.add_argument('--run', nargs='+', type=Path)
    args = parser.parse_args(argv)
    try:
        records = [source_check()] if args.source else [inspect_report(p) for p in args.run]
        print(json.dumps({'passed': all(r['passed'] for r in records), 'checks': records}, sort_keys=True, indent=2))
        return 0 if all(r['passed'] for r in records) else 3
    except (OSError, ValueError, KeyError, TypeError, StopIteration, sqlite3.Error):
        print(json.dumps({'passed': False, 'error': 'verification_input_invalid'}))
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
