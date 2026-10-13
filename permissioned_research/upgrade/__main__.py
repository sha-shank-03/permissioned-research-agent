"""U1 offline doctor and explicit authored Gateway-double scalar demonstration."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys

from ..domain import ResearchError
from ..store import CorpusStore, SCENARIOS, load_fixture
from . import VERSION
from .config import DispatchConfig, live_preflight, require_local_model
from .double import gateway_double
from .events import code_digest
from .protocol import PROMPT_SHA256
from .runner import run_scalar


def reserve_output(path):
    """Never resume/reset cumulative bounds by overwriting an interrupted prefix."""
    path = path.resolve()
    event_path, state_path = path.with_suffix('.events.jsonl'), path.with_suffix('.sqlite3')
    if any(p.exists() for p in (path, event_path, state_path)):
        raise ResearchError('attempt_state_conflict')
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.open('x', encoding='utf-8', newline='\n'), event_path, state_path


def save(handle, value):
    handle.write(json.dumps(value, sort_keys=True, indent=2) + '\n')
    handle.flush()
    os.fsync(handle.fileno())


def main(argv=None):
    parser = argparse.ArgumentParser(description='U1 scalar journey through an authored Gateway-native local double.')
    sub = parser.add_subparsers(dest='command', required=True)
    doctor = sub.add_parser('doctor')
    doctor.add_argument('--mode', choices=['offline'], required=True)
    demo = sub.add_parser('demo')
    demo.add_argument('--provider', choices=['gateway-double', 'gateway'], required=True)
    demo.add_argument('--model', required=True, help='Explicit provider/model; double requires test/synthetic-scalar.')
    demo.add_argument('--scenario', choices=SCENARIOS, required=True)
    demo.add_argument('--principal', choices=['alex', 'priya', 'sam'], required=True)
    demo.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    if sys.version_info[:2] != (3, 11):
        print(json.dumps({'error': 'python_3_11_required'}), file=sys.stderr)
        return 2
    if args.command == 'doctor':
        print(json.dumps({'version': VERSION, 'python': sys.version.split()[0], 'sqlite': sqlite3.sqlite_version,
                          'dependencies': 'standard_library_only', 'mode': 'offline', 'network_used': False,
                          'live_requests_enabled': False, 'worker_allocation_inr': 0,
                          'automatic_inference_transport_retries': 0, 'resume_supported': False,
                          'code_sha256': code_digest(), 'prompt_sha256': PROMPT_SHA256}, indent=2))
        return 0
    output = store = None
    try:
        config = DispatchConfig(args.model)
        if args.provider == 'gateway':
            live_preflight(config)  # Must precede output creation and credential access.
        require_local_model(config)
        output, event_path, state_path = reserve_output(args.out)
        fixture = load_fixture(args.scenario)
        store = CorpusStore(state_path, fixture)
        with gateway_double() as (endpoint, captured):
            report = run_scalar(store, fixture, args.principal, config, endpoint, event_path=event_path)
        report['observed_double_requests'] = len(captured)
        report['metrics']['owned_loopback_doubles_remaining'] = 0  # Context exit checked server-thread cleanup.
        save(output, report)
        # This explicit user-directed export contains only the released memo.
        print(json.dumps(report, sort_keys=True, indent=2))
        return 3 if report['result']['outcome'] in {'failed', 'cancelled'} else 0
    except (ResearchError, OSError, sqlite3.Error) as error:
        safe = {'schema': 'research-run-u1-incomplete-v1', 'mode': 'local_double',
                'live_model_executed': False,
                'error': error.code if isinstance(error, ResearchError) else 'storage_error',
                'attempt_preserved': output is not None, 'resume_supported': False}
        if output is not None and output.tell() == 0:
            save(output, safe)
        print(json.dumps(safe), file=sys.stderr)
        return 2 if output is None else 3
    finally:
        if store is not None:
            store.close()
        if output is not None:
            output.close()


if __name__ == '__main__':
    raise SystemExit(main())
