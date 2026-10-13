"""Three-slot controller. Default native adapter is imported after STARTED only.

Implementing this command does not grant a named lease or native authorization.
Pure verification must supply an injected actor; the CLI has no actor override.
"""
from pathlib import Path
import os
import sys
import time

from ..narrative_suite.data import digest, require
from ..narrative_suite.ledger import ERRORS, validate_metrics
from .contract import file_hash, read_json
from .journal import Journal, write_exclusive

RAW_FILES={'report_sha256':('report.json',128*1024),
           'trace_sha256':('trace.json',4*1024*1024),
           'corpus_sha256':('corpus.sqlite',512*1024)}
USAGE_KEYS=('provider_requests','elapsed_seconds','model_tokens','actual_cost','estimated_cost')


def prepare(context,*,hook=None):
    context.verify()
    require(context.generated_bytes()+context.blueprint['limits']['slot_reserve_bytes']<=context.blueprint['limits']['raw_output_bytes'],'supplement_storage_budget')
    # Exclusive root and plan; all three INTENDED acknowledgements precede run.
    return Journal.create(context.out,context.blueprint,hook)


def native_actor(task,policy,path,bindings):
    require(sys.version_info[:3]==(3,11,9),'supplement_python_version')
    from ..narrative_suite.runner import execute_attempt
    return execute_attempt(task,policy,path,bindings)


def verify_receipt(journal,entry,task,metrics,artifacts):
    """Raw evidence/metric binding only. Does not read expected hypotheses."""
    validate_metrics(metrics)
    require(type(artifacts) is dict and set(artifacts)==set(RAW_FILES),'supplement_receipt_invalid')
    directory=journal.root/entry['id']
    require(directory.is_dir() and not directory.is_symlink(),'supplement_receipt_incomplete')
    require({p.name for p in directory.iterdir()}=={v[0] for v in RAW_FILES.values()},'supplement_receipt_incomplete')
    for key,(name,maximum) in RAW_FILES.items():
        require(file_hash(directory/name,maximum)==artifacts[key],'supplement_receipt_hash_changed')
        # The unchanged actor already fsyncs report/trace and closes SQLite.
        # Explicitly flush each returned raw file as this execution's receipt.
        with (directory/name).open('r+b') as stream:os.fsync(stream.fileno())
    report=read_json(directory/'report.json'); trace=read_json(directory/'trace.json',4*1024*1024)
    require(report['schema']=='narrative-task-run-v1' and report['task_id']==entry['task_id'] and report['split']=='capability' and report['family']==entry['family'],'supplement_receipt_invalid')
    require(report['principal']==entry['principal'] and report['policy']==entry['policy'] and report['input_sha256']==digest(task) and report['config_sha256']==entry['config_sha256'],'supplement_receipt_invalid')
    require(report['bindings']==journal.plan['original_actor_bindings'],'supplement_actor_binding_changed')
    require(report['outcome']==metrics['outcome'] and (report['error'] if report['error'] in ERRORS else 'runner_error')==metrics['error'],'supplement_receipt_invalid')
    require(report['provider_requests']==metrics['provider_requests'] and report['elapsed_seconds']==metrics['elapsed_seconds'] and report['owned_processes_remaining']==metrics['owned_processes_remaining'],'supplement_receipt_invalid')
    require(all(report['counters'][key]==metrics[key] for key in ('searches','reads','repairs','restarts','backend_search_calls')),'supplement_receipt_invalid')
    require(report['live_model_executed'] is False and report['remote_authorization_proven'] is False and all(report[key] is None for key in ('model_tokens','actual_cost','estimated_cost','semantic_correctness','coverage','semantic_judgments')),'supplement_receipt_invalid')
    require(trace['schema']=='narrative-private-trace-v1' and trace['capture_complete'] is True and trace['actor_rubric_contents_loaded'] is False and trace['observed_native_requests']==metrics['provider_requests'],'supplement_receipt_incomplete')
    require(len(trace['responses'])==len(trace['disclosures'])==metrics['provider_requests'] and trace['unpaired_native_records']==[] and trace['unpaired_authority_frames']==[],'supplement_receipt_incomplete')
    from permissioned_research.upgrade.config import DispatchConfig, DOUBLE_MODEL
    config=DispatchConfig(DOUBLE_MODEL,**task['execution']['config_overrides'])
    require(metrics['provider_requests']<=config.max_requests and metrics['searches']<=config.max_searches and metrics['reads']<=config.max_reads and metrics['repairs']<=1 and metrics['restarts']<=1 and metrics['elapsed_seconds']<=config.total_seconds,'supplement_receipt_budget_invalid')
    return True


def run(context,*,actor=None,hook=None,clock=time.monotonic):
    if actor is None:require(sys.version_info[:3]==(3,11,9),'supplement_python_version')
    context.verify(); journal=Journal(context.out,hook)
    require(journal.plan==context.blueprint,'supplement_plan_binding_changed')
    deadline=clock()+journal.plan['limits']['run_seconds']
    with journal.writer():
        initial=journal.inspect()
        require(initial['intended_complete'] and not initial['halt_codes'] and initial['counts']=={'terminal':0,'interrupted_unknown':0,'unexecuted':3},'supplement_batch_replay_refused')
        require({p.name for p in journal.root.iterdir()}=={'plan.json','journal.jsonl','writer.lock'},'supplement_batch_replay_refused')
        # Even a crash before the first STARTED may not silently rerun this batch.
        write_exclusive(journal.root/'run.claim.json',{'schema':'narrative-supplement-claim-v3','plan_sha256':journal.plan_sha256,'execution_source_manifest_sha256':context.execution_sha256,'single_invocation':True})
        for entry in journal.plan['attempts']:
            sid=entry['id']; stage='preflight_drift'
            try:
                context.verify()
                require(Journal(journal.root).plan==context.blueprint,'supplement_plan_binding_changed')
                stage='budget_stop'; limits=journal.plan['limits']
                require(clock()+limits['slot_seconds']+5<deadline,'supplement_deadline')
                require(context.generated_bytes()+limits['slot_reserve_bytes']<=limits['raw_output_bytes'],'supplement_storage_budget')
                path=journal.root/sid; require(not path.exists(),'supplement_attempt_replay_refused')
                stage='preflight_drift'; task=context.load_task(entry)
                execution={'schema':'narrative-supplement-execution-v3','id':sid,'parent_attempt_id':entry['parent_attempt_id'],
                    'plan_sha256':journal.plan_sha256,'execution_source_manifest_sha256':context.execution_sha256,
                    'proposal_file_sha256':journal.plan['proposal_file_sha256'],'input_sha256':entry['input_sha256'],
                    'actor_input_content_sha256':digest(task),'config_sha256':entry['config_sha256'],
                    'original_actor_bindings':journal.plan['original_actor_bindings']}
                stage='durability_unknown'; execution_sha=write_exclusive(journal.root/(sid+'.execution.json'),execution)
                journal.start(sid)  # fsynced, exclusive STARTED before any actor import/construction.
                stage='actor_outcome_unknown'; result=(actor or native_actor)(task,entry['policy'],path,journal.plan['original_actor_bindings'])
                stage='receipt_incomplete'
                returned_sha=write_exclusive(journal.root/(sid+'.returned.json'),{'schema':'narrative-supplement-returned-v3','actor_return':result})
                stage='durability_unknown'; journal.returned(sid,returned_sha)
                stage='receipt_invalid'; require(type(result) in (tuple,list) and len(result)==2,'supplement_receipt_invalid')
                metrics,artifacts=result
                validate_metrics(metrics)
                usage={key:metrics[key] for key in USAGE_KEYS}
                usage_sha=write_exclusive(journal.root/(sid+'.usage.json'),{'schema':'narrative-supplement-usage-v3','id':sid,**usage,'billing_receipt_available':False})
                stage='receipt_incomplete'; verify_receipt(journal,entry,task,metrics,artifacts)
                terminal_artifacts={**artifacts,'returned_sha256':returned_sha,'usage_sha256':usage_sha,'execution_sha256':execution_sha}
                stage='durability_unknown'; journal.terminal(sid,metrics,terminal_artifacts)
                # All raw artifacts and the raw terminal precede expectation checking.
                stage='preflight_drift'; context.verify()
                stage='budget_stop'; require(context.generated_bytes()<=limits['raw_output_bytes'] and clock()<deadline,'supplement_budget_stop')
                stage='cleanup_unconfirmed'; require(metrics['owned_processes_remaining']==0,'supplement_cleanup_unconfirmed')
                stage='durability_unknown'
                matches={'outcome':metrics['outcome'],'error':metrics['error']} in entry['hypotheses']
                journal.classify(sid,matches)
                if not matches:return 2
            except Exception:
                # Never publish exception text, contexts, bodies, paths or secrets.
                try:journal.halt(stage,sid)
                except Exception:pass  # A lost fsync acknowledgement remains blocked by durable STARTED/claim.
                return 2
    return 0
