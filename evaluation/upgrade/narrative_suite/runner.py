"""Future explicit local native runner. No rubric contents or live route.

Source/pure delivery does not authorize calling plan/run_slice. A new assignment
and lease are required for native evaluation. Paired policies share the exact
accepted provider, tools, configurations and ceilings; their collection differs.
"""
from contextlib import ExitStack
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

from permissioned_research.domain import ResearchRequest
from permissioned_research.store import CorpusStore
from permissioned_research.upgrade.config import DispatchConfig, DOUBLE_MODEL, require_local_model
from permissioned_research.upgrade.narrative.projection import CorpusProjection
from permissioned_research.upgrade.narrative.provider import NarrativeProvider, PROMPT_SHA256
from permissioned_research.upgrade.narrative.workflow import NarrativeWorkflow
from permissioned_research.upgrade.transport import GatewayTransport
from .data import InputCatalog, digest, require, unique_json
from .ledger import ERRORS, Ledger, POLICIES, write_exclusive
from .native import suite_http_double
from .snapshot import raw_snapshot

ROOT=Path(__file__).resolve().parents[3]
MANIFEST=ROOT/'evaluation/upgrade/U2B2_SOURCE_MANIFEST.json'
MAX_OUTPUT_BYTES=64*1024*1024


def source_identity():
    raw=MANIFEST.read_bytes(); manifest=unique_json(raw.decode())
    require(manifest['manifest_id']=='u2b2-source-pure-v1','source_manifest_invalid')
    require(manifest['code_sha256']==digest(manifest['files']) and manifest['preserved_sha256']==digest(manifest['preserved_files']),'source_manifest_invalid')
    require(len(manifest['files'])==manifest['new_file_count'] and len(manifest['preserved_files'])==86,'source_manifest_invalid')
    for entry in manifest['files']+manifest['preserved_files']:
        path=(ROOT/entry['path']).resolve()
        require(path.is_relative_to(ROOT) and path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256'],'source_hash_mismatch')
    return hashlib.sha256(raw).hexdigest()


def config_for(task):
    config=DispatchConfig(DOUBLE_MODEL,**task['execution']['config_overrides']); require_local_model(config)
    return config


def intended_plan(catalog,split,manifest_sha256):
    attempts=[]
    for entry in catalog.freeze['entries']:
        if entry['split']!=split:continue
        task=catalog.load_input(entry['id'])
        for policy in POLICIES:
            ordinal=len(attempts)+1
            attempts.append({'ordinal':ordinal,'id':f'a{ordinal:03d}','task_id':task['id'],'family':task['family'],
                             'principal':task['principal'],'policy':policy,'input_sha256':entry['input_sha256'],
                             'rubric_sha256':entry['rubric_sha256'],'config_sha256':config_for(task).sha256})
    return {'schema':'narrative-intended-plan-v1','split':split,
            'bindings':{'pack_sha256':catalog.freeze_sha256,'source_manifest_sha256':manifest_sha256,
                        'prompt_sha256':PROMPT_SHA256,'provider':'owned_narrative_gateway_double','model':DOUBLE_MODEL,'repeats':1},
            'attempts':attempts}


def controls_for(task,store,applied):
    controls=task['execution']; hooks={}
    for phase in ('before_send','after_submit','before_release'):
        def fire(actor,phase=phase):
            # At most one event per hook invocation; a second source change can
            # occur only after another context has opened its trusted target.
            for event in controls['events']:
                if event['id'] in applied or event['phase']!=phase:continue
                if event['trigger']=='target_opened' and event['target'] not in {actor.projection.selection_key(h) for h in actor.projection.opened}:continue
                if event['kind']=='revoke_membership':changed=store.revoke_membership(event['principal'],event['group'],event['id'])
                else:changed=store.replace_source(event['target'],event['body'],event['effective_at'],event['review_until'],event['id'])
                require(changed,'native_control_not_applied'); applied.append(event['id']); break
        hooks[phase]=fire
    return hooks


def _handle_frame(projection,connection,task):
    return {'snapshot':raw_snapshot(connection,task['principal'],task['fixture']['as_of']),
            'handles':{h:e.source.object_id for h,e in projection.tools.evidence.items()},
            'opened_handles':list(projection.opened)}


def bind_captures(frames,records,expected_requests):
    """Keep unpaired/torn transport evidence; unknown capture is not zero risk."""
    complete=len(frames)==len(records)==expected_requests and all(type(r.get('http_status')) is int and type(r.get('body')) is str for r in records)
    paired=[{**frames[i],**record} for i,record in enumerate(records) if i<len(frames)]
    return {'capture_complete':complete,'responses':paired,'disclosures':paired,
            'unpaired_native_records':records[len(frames):],
            'unpaired_authority_frames':frames[len(records):]}


def execute_attempt(task,policy,attempt_dir,bindings):
    """Only run_slice calls this after STARTED. Never used by pure tests."""
    attempt_dir.mkdir(exist_ok=False); config=config_for(task); controls=task['execution']
    request=ResearchRequest(task['principal'],task['fixture']['vendor'],task['fixture']['as_of'])
    frames=[]; applied=[]; cancellation=threading.Event()
    if controls['cancel_before_send']:cancellation.set()
    with ExitStack() as stack:
        store=CorpusStore(attempt_dir/'corpus.sqlite',task['fixture']); stack.callback(store.close)
        projection=CorpusProjection(store,request)
        endpoint,records=stack.enter_context(suite_http_double(controls['provider_fault'],controls['transport_fault']))
        transport=GatewayTransport(config,endpoint=endpoint,cancellation=cancellation); stack.callback(transport.close)
        provider=NarrativeProvider(config,transport)
        def observe(surface,context):
            require(surface=='provider_disclosure'); frames.append(_handle_frame(projection,store.connection,task))
        report=NarrativeWorkflow(projection,request,provider,config,policy=policy,cancellation=cancellation,
                                 hooks=controls_for(task,store,applied),observer=observe).run()
        final=raw_snapshot(store.connection,task['principal'],request.as_of)
    require(transport.owned_children_remaining==0 and transport.owned_threads_remaining==0,'native_cleanup_failed')
    trace={'schema':'narrative-private-trace-v1',**bind_captures(frames,records,report['provider_requests']),
           'applied_event_ids':applied,'final_snapshot':final,
           'observed_native_requests':len(records),'actor_rubric_contents_loaded':False}
    report.update(schema='narrative-task-run-v1',task_id=task['id'],split=task['split'],family=task['family'],
                  input_sha256=digest(task),config=asdict(config),config_sha256=config.sha256,
                  bindings=bindings,applied_event_count=len(applied),source_contract='local_only',
                  semantic_judgments=None,model_tokens=None,owned_processes_remaining=0)
    report['intended_event_count']=len(controls['events']); report['unexecuted_event_count']=len(controls['events'])-len(applied)
    # All raw artifacts remain private operator material; the journal has only
    # closed metrics and hashes. No request/context/prose is printed.
    report_raw=json.dumps(report,ensure_ascii=False,indent=2).encode(); trace_raw=json.dumps(trace,ensure_ascii=False,indent=2).encode()
    require(len(report_raw)<=128*1024 and len(trace_raw)<=4*1024*1024,'native_output_bound')
    require((attempt_dir/'corpus.sqlite').stat().st_size<=512*1024,'native_output_bound')
    artifacts={'report_sha256':write_exclusive(attempt_dir/'report.json',report),
               'trace_sha256':write_exclusive(attempt_dir/'trace.json',trace),
               'corpus_sha256':hashlib.sha256((attempt_dir/'corpus.sqlite').read_bytes()).hexdigest()}
    counters=report['counters']; error=report['error'] if report['error'] in ERRORS else 'runner_error'
    metrics={'outcome':report['outcome'],'error':error,'provider_requests':report['provider_requests'],
             **{k:counters[k] for k in ('searches','reads','repairs','restarts','backend_search_calls')},
             'elapsed_seconds':report['elapsed_seconds'],'model_tokens':None,'actual_cost':None,'estimated_cost':None,
             'unsafe_attempts':None,'unsafe_effects':None,'semantic_correctness':None,'coverage':None,'owned_processes_remaining':0}
    return metrics,artifacts


def output_size(root):
    return sum(p.stat().st_size for p in root.rglob('*') if p.is_file())


def expected_terminals(task,policy):
    """Frozen operational hypotheses, never semantic labels or acceptance."""
    c=task['execution']
    if c['cancel_before_send']:return {('cancelled','cancelled')}
    if c['transport_fault']:return {('failed','gateway_authentication_required' if c['transport_fault']==401 else 'gateway_rate_limited')}
    if c['provider_fault']=='self_approve':return {('failed','invalid_memo')}
    if c['config_overrides'].get('max_requests')==2 and policy=='adaptive':return {('failed','budget_exhausted')}
    if c['events']:
        if policy=='fixed':return {('failed','budget_exhausted')}
        if len(c['events'])==2:return {('failed','source_state_changed'),('failed','budget_exhausted')}
        return {('needs_review',None),('failed','budget_exhausted'),('failed','source_state_changed')}
    return {('needs_review',None)}


def run_slice(ledger,catalog,*,start,count,slice_seconds=520,max_output_bytes=MAX_OUTPUT_BYTES):
    require(sys.version_info[:3]==(3,11,9),'native_python_version')
    require(type(start) is int and type(count) is int and 1<=count<=4 and 1<=start<=len(ledger.plan['attempts']) and start+count-1<=len(ledger.plan['attempts']),'native_slice_invalid')
    require(type(slice_seconds) is int and slice_seconds==520 and max_output_bytes==MAX_OUTPUT_BYTES,'native_slice_invalid')
    expected=intended_plan(catalog,ledger.plan['split'],source_identity()); require(ledger.plan==expected,'native_plan_binding_changed')
    deadline=time.monotonic()+slice_seconds
    chosen=ledger.plan['attempts'][start-1:start-1+count]
    with ledger.writer():
        states=ledger.inspect()['attempts']; require(all(states[a['id']]['state']=='unexecuted' for a in chosen),'native_attempt_not_unexecuted')
        for a in chosen:
            task=catalog.load_input(a['task_id']); config=config_for(task)
            require(time.monotonic()+config.total_seconds+5<deadline,'native_slice_budget')
            require(output_size(ledger.root.parent)+6*1024*1024<=max_output_bytes,'native_storage_budget')
            path=ledger.root/a['id']; require(not path.exists(),'native_attempt_output_exists')
            ledger.start(a['id'])  # fsync happens before SQLite/provider/server construction.
            metrics,artifacts=execute_attempt(task,a['policy'],path,ledger.plan['bindings'])
            require(output_size(ledger.root.parent)<=max_output_bytes,'native_storage_budget')
            ledger.terminal(a['id'],metrics,artifacts)
            require((metrics['outcome'],metrics['error']) in expected_terminals(task,a['policy']),'native_unexpected_terminal')
    return public_account(ledger)


def public_account(ledger):
    state=ledger.inspect()
    return {k:state[k] for k in ('schema','intended','counts','torn_tail','journal_sha256','plan_sha256','semantic_correctness','coverage')}
