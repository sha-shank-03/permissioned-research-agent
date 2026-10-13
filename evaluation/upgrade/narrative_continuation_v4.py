"""Two never-started aliases only. Immutable v3 unknown is never recovered.

Source implementation does not authorize native execution or a resource lease.
Only file/journal tests with injected actors are permitted in this delivery.
"""
import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time

from .narrative_suite.data import digest, require, unique_json
from .narrative_correction_v2 import ENTRY_KEYS
from .narrative_supplement.contract import Context as ParentContext, file_hash, read_json
from .narrative_supplement.journal import Journal as ParentJournal, _apply, sha, write_exclusive
from .narrative_supplement.runner import RAW_FILES, USAGE_KEYS, native_actor, verify_receipt

ROOT=Path(__file__).resolve().parents[2]
SEAL='evaluation/upgrade/U2B2_CONTINUATION_SOURCE_MANIFEST_V4.json'
PROPOSAL='evaluation/upgrade/U2B2_CONTINUATION_V4.json'
CHECKPOINT='fixtures/upgrade/narrative-continuation-v4/partial-checkpoint.json'
PARENT='work/main-u2b2-supplement-v3'
OUTPUT='work/main-u2b2-continuation-v4'
LIMITS={'run_seconds':520,'slot_seconds':120,'raw_output_bytes':64*1024*1024,
    'slot_reserve_bytes':6*1024*1024,'working_ram_mib':512,
    'downloads':0,'external_calls':0,'live_model_calls':0}
BINDINGS={'v3_source_manifest_sha256','v3_command_manifest_sha256','v3_plan_sha256',
    'v3_journal_sha256','v3_inventory_sha256','main_review_sha256',
    'main_read_only_report_sha256','main_execution_summary_sha256','original_parent_bindings_sha256'}
PLAN_KEYS={'schema','execution_source_manifest_sha256','execution_code_sha256',
    'actor_source_manifest_sha256','proposal_sha256','checkpoint_sha256','parent_bindings',
    'original_actor_bindings','attempts','limits','lease_label','lease_label_is_not_authorization',
    'output_relative_root','parent_relative_root','semantic_quality','aggregate_full_plan_safety'}


def validate_attempts(entries):
    require(type(entries) is list and len(entries)==2,'continuation_two_slots_required')
    for index,entry in enumerate(entries):
        require(type(entry) is dict and set(entry)==ENTRY_KEYS|{'prior_supplement_id'},'continuation_entry_invalid')
        require(entry['id']==f'c{index+1:03d}' and entry['prior_supplement_id']==f's{index+2:03d}' and entry['parent_attempt_id']==f'a{index+47:03d}','continuation_alias_replay_refused')
        require(entry['task_id']=='capability-budget-04' and entry['family']=='budget_failure' and entry['principal']=='priya' and entry['policy']==('adaptive' if index==0 else 'fixed'),'continuation_entry_invalid')
        require(entry['state']=='unexecuted' and entry['hypotheses']==[{'outcome':'cancelled','error':'cancelled'}],'continuation_entry_invalid')
        require(all(sha(entry[k]) for k in ('input_sha256','rubric_sha256','config_sha256')),'continuation_entry_invalid')


def validate_partial(checkpoint,proposal,plan,state,inventory,current_bindings,review,report,execution):
    """Pure closed eligibility check. Neither success nor report grants execution."""
    require(type(proposal) is dict and set(proposal)=={'schema','state','checkpoint_file_sha256',
        'parent_bindings','attempts','physical_intentions_after_prepare','logical_intended',
        'permanently_retained_unknown','native_authorized','main_resource_accounting_correction_and_review_required'},'continuation_proposal_invalid')
    require(proposal['schema']=='narrative-two-slot-continuation-v4' and proposal['state']=='proposed_unexecuted_source_only' and proposal['native_authorized'] is False and proposal['main_resource_accounting_correction_and_review_required'] is True,'continuation_proposal_invalid')
    require(checkpoint['schema']=='narrative-partial-parent-checkpoint-v4' and checkpoint['parent_relative_root']==PARENT and checkpoint['forbidden_replays']==['s001','a046'] and checkpoint['only_never_started_prior_slots']==['s002','s003'],'continuation_checkpoint_invalid')
    require(checkpoint['accounting']=={'original_intended':72,'original_terminal':69,'original_interrupted_unknown':0,'original_unexecuted':3,'v3_intended':3,'v3_terminal':0,'v3_interrupted_unknown':1,'v3_unexecuted':2,'physical_intentions_before':75,'logical_intended':72,'logical_terminal':69,'logical_interrupted_unknown':1,'logical_unexecuted':2},'continuation_denominator_changed')
    summary=checkpoint['main_review_summary']
    require(summary['classification']=='main_resource_interruption' and summary['run_exit']==15 and summary['supervisor_exit']==1 and summary['domain_outcome'] is None and summary['provider_requests'] is None and summary['process_roles_established'] is False and summary['python_count_established'] is False,'continuation_interruption_changed')
    require(set(current_bindings)==set(proposal['parent_bindings'])==BINDINGS and all(sha(v) for v in current_bindings.values()) and current_bindings==proposal['parent_bindings'],'continuation_parent_binding_changed')
    validate_attempts(proposal['attempts'])
    require(plan==checkpoint['v3_plan'] and digest(plan['parent_bindings'])==current_bindings['original_parent_bindings_sha256'],'continuation_parent_plan_changed')
    require({k:state[k] for k in ('counts','slots','intended_complete','torn_tail')}==checkpoint['v3_state'],'continuation_prior_state_changed')
    require(state['counts']=={'terminal':0,'interrupted_unknown':1,'unexecuted':2} and state['intended_complete'] is True and state['torn_tail'] is False,'continuation_prior_state_changed')
    require(set(state['slots'])=={'s001','s002','s003'} and state['slots']['s001']['state']=='interrupted_unknown' and all(state['slots'][sid]['state']=='unexecuted' for sid in ('s002','s003')),'continuation_started_alias_refused')
    require(all(s['metrics'] is None and s['artifacts'] is None and s['classified'] is None and s['returned_sha256'] is None for s in state['slots'].values()),'continuation_prior_state_changed')
    expected=[]
    for index,entry in enumerate(plan['attempts'][1:],1):
        expected.append({**entry,'id':f'c{index:03d}','prior_supplement_id':entry['id']})
    require(proposal['attempts']==expected,'continuation_actor_input_or_gold_changed')
    require(inventory==checkpoint['partial_inventory'] and len(inventory)==6 and digest(inventory)==current_bindings['v3_inventory_sha256'],'continuation_partial_capture_changed')
    require({e['path'] for e in inventory}=={'plan.json','journal.jsonl','run.claim.json','writer.lock','s001.execution.json','s001/corpus.sqlite'},'continuation_partial_capture_changed')
    require(review['raw_partial_inventory']=={e['path']:e['sha256'] for e in inventory} and review['status']=='partial_accounting_verified_main_resource_interruption_continuation_blocked','continuation_main_review_changed')
    logical={'intended':72,'terminal':69,'interrupted_unknown':1,'unexecuted':2}
    require(review['logical_counts']==report['logical_original_slots']==logical and review['physical_intention_records']==report['physical_intent_records']==75,'continuation_denominator_changed')
    require(report['dispatch_authorized'] is False and {k:report['supplement'][k] for k in state['counts']}==state['counts'],'continuation_report_invalid')
    require(report['supplement']['plan_sha256']==current_bindings['v3_plan_sha256'] and report['supplement']['journal_sha256']==current_bindings['v3_journal_sha256'],'continuation_main_report_changed')
    require(execution['commands'][-1]['id']=='run' and execution['commands'][-1]['exit_code']==15 and execution['commands'][-1]['limit_exceeded']=='process_or_listener_cap','continuation_interruption_changed')
    require(proposal['physical_intentions_after_prepare']==77 and proposal['logical_intended']==72 and proposal['permanently_retained_unknown']=={'prior_supplement_id':'s001','original_slot':'a046','state':'interrupted_unknown','domain_outcome':None,'provider_requests':None},'continuation_denominator_changed')
    return True


def inventory(root):
    root=Path(root); require(root.is_dir() and not root.is_symlink(),'continuation_parent_missing')
    values=[]
    for path in sorted(root.rglob('*')):
        require(not path.is_symlink(),'continuation_parent_path_invalid')
        if path.is_file():values.append({'path':path.relative_to(root).as_posix(),'sha256':file_hash(path),'bytes':path.stat().st_size})
    return values


class Context:
    def __init__(self,root,source_sha256,population_review,lease_id):
        self.root=Path(root).resolve(); self.execution_sha256=source_sha256
        require(sha(source_sha256) and type(lease_id) is str and re.fullmatch('[a-z][a-z0-9_-]{2,79}',lease_id),'continuation_version_or_lease_invalid')
        require(file_hash(self.root/SEAL)==source_sha256,'continuation_source_changed')
        self.seal=read_json(self.root/SEAL); self.checkpoint=read_json(self.root/CHECKPOINT); self.proposal=read_json(self.root/PROPOSAL)
        require(self.seal['manifest_id']=='u2b2-two-slot-continuation-source-v4' and self.seal['code_sha256']==digest(self.seal['files']) and self.seal['preserved_sha256']==digest(self.seal['preserved_files']) and len(self.seal['preserved_files'])==194,'continuation_source_invalid')
        self.parent=ParentContext(self.root,self.checkpoint['parent_source_manifest_sha256'],population_review,self.checkpoint['v3_plan']['lease_label'])
        self.out=self.root/OUTPUT
        self.blueprint={'schema':'narrative-two-slot-plan-v4','execution_source_manifest_sha256':source_sha256,
            'execution_code_sha256':self.seal['code_sha256'],'actor_source_manifest_sha256':self.checkpoint['v3_plan']['actor_source_manifest_sha256'],
            'proposal_sha256':file_hash(self.root/PROPOSAL),'checkpoint_sha256':file_hash(self.root/CHECKPOINT),
            'parent_bindings':copy.deepcopy(self.proposal['parent_bindings']),
            'original_actor_bindings':copy.deepcopy(self.checkpoint['v3_plan']['original_actor_bindings']),
            'attempts':copy.deepcopy(self.proposal['attempts']),'limits':copy.deepcopy(LIMITS),
            'lease_label':lease_id,'lease_label_is_not_authorization':True,'output_relative_root':OUTPUT,
            'parent_relative_root':PARENT,'semantic_quality':None,'aggregate_full_plan_safety':None}

    def verify(self):
        require(file_hash(self.root/SEAL)==self.execution_sha256,'continuation_source_changed')
        for entry in self.seal['files']+self.seal['preserved_files']:
            relative=Path(entry['path']); path=self.root/relative
            require(not relative.is_absolute() and path.resolve().is_relative_to(self.root) and file_hash(path)==entry['sha256'],'continuation_source_changed')
        require(read_json(self.root/CHECKPOINT)==self.checkpoint and read_json(self.root/PROPOSAL)==self.proposal and file_hash(self.root/CHECKPOINT)==self.proposal['checkpoint_file_sha256'],'continuation_proposal_changed')
        self.parent.verify(); prior=ParentJournal(self.root/PARENT); state=prior.inspect(); raw=inventory(self.root/PARENT)
        references=[]
        for entry in self.checkpoint['main_references']:
            path=self.root.parent/entry['path']
            require(path.resolve().is_relative_to(self.root.parent) and file_hash(path)==entry['sha256'],'continuation_main_reference_changed')
            references.append(read_json(path,256*1024))
        current={'v3_source_manifest_sha256':file_hash(self.root/'evaluation/upgrade/U2B2_SUPPLEMENT_SOURCE_MANIFEST.json'),
            'v3_command_manifest_sha256':file_hash(self.root/'evaluation/upgrade/U2B2_SUPPLEMENT_COMMANDS_V3.json'),
            'v3_plan_sha256':prior.plan_sha256,'v3_journal_sha256':state['journal_sha256'],
            'v3_inventory_sha256':digest(raw),'main_review_sha256':self.checkpoint['main_references'][0]['sha256'],
            'main_read_only_report_sha256':self.checkpoint['main_references'][1]['sha256'],
            'main_execution_summary_sha256':self.checkpoint['main_references'][2]['sha256'],
            'original_parent_bindings_sha256':digest(prior.plan['parent_bindings'])}
        validate_partial(self.checkpoint,self.proposal,prior.plan,state,raw,current,*references)
        require(not self.out.is_symlink() and (not self.out.exists() or all(not p.is_symlink() for p in self.out.rglob('*'))),'continuation_output_invalid')
        for entry in self.blueprint['attempts']:self.load_task(entry)
        return True

    def load_task(self,entry):return self.parent.load_task(entry)
    def generated_bytes(self):
        prior=sum(e['bytes'] for e in self.parent.checkpoint['parent_cohort_inventory'])+sum(e['bytes'] for e in self.checkpoint['partial_inventory'])
        return prior+sum(p.stat().st_size for p in self.out.rglob('*') if p.is_file()) if self.out.exists() else prior


def validate_plan(plan):
    require(type(plan) is dict and set(plan)==PLAN_KEYS and plan['schema']=='narrative-two-slot-plan-v4','continuation_plan_invalid')
    require(all(sha(plan[k]) for k in PLAN_KEYS if k.endswith('_sha256')),'continuation_plan_invalid')
    require(set(plan['parent_bindings'])==BINDINGS and all(sha(v) for v in plan['parent_bindings'].values()),'continuation_plan_invalid')
    validate_attempts(plan['attempts'])
    require(plan['limits']==LIMITS and plan['parent_relative_root']==PARENT and plan['output_relative_root']==OUTPUT,'continuation_plan_invalid')
    require(type(plan['lease_label']) is str and re.fullmatch('[a-z][a-z0-9_-]{2,79}',plan['lease_label']) and plan['lease_label_is_not_authorization'] is True,'continuation_plan_invalid')
    require(plan['semantic_quality'] is None and plan['aggregate_full_plan_safety'] is None,'continuation_plan_invalid')
    bindings=plan['original_actor_bindings']
    require(type(bindings) is dict and set(bindings)=={'pack_sha256','source_manifest_sha256','prompt_sha256','provider','model','repeats'} and all(sha(bindings[k]) for k in ('pack_sha256','source_manifest_sha256','prompt_sha256')),'continuation_plan_invalid')
    require(bindings['source_manifest_sha256']==plan['actor_source_manifest_sha256'] and bindings['provider']=='owned_narrative_gateway_double' and bindings['model']=='test/synthetic-scalar' and type(bindings['repeats']) is int and bindings['repeats']==1,'continuation_plan_invalid')
    return plan


class Journal(ParentJournal):
    """Reuse frozen locking/transitions; new closed plan, event version and count."""
    def __init__(self,root,hook=None):
        self.root=Path(root).resolve(); self.hook=hook; self.owned=False
        self.plan=validate_plan(read_json(self.root/'plan.json')); self.plan_sha256=file_hash(self.root/'plan.json')
    @classmethod
    def create(cls,root,plan,hook=None):
        validate_plan(plan); root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=False)
        write_exclusive(root/'plan.json',plan)
        with (root/'journal.jsonl').open('xb') as stream:stream.flush(); os.fsync(stream.fileno())
        journal=cls(root,hook)
        with journal.writer(initializing=True):
            for entry in plan['attempts']:journal._append('INTENDED',entry['id'],{'parent_attempt_id':entry['parent_attempt_id']})
        require(journal.inspect()['intended_complete'],'continuation_intentions_incomplete'); return journal
    def _append(self,event,sid,details):
        require(self.owned,'continuation_writer_required'); state=self.inspect(); require(not state['torn_tail'],'continuation_torn_tail')
        _apply(self.plan,state['slots'],set(state['intended_ids']),list(state['halt_codes']),event,sid,details)
        records=state['records']; previous=records[-1]['sha256'] if records else '0'*64
        row={'schema':'narrative-continuation-event-v4','sequence':len(records)+1,'previous_sha256':previous,
            'plan_sha256':self.plan_sha256,'execution_source_manifest_sha256':self.plan['execution_source_manifest_sha256'],
            'binding_sha256':digest(self.plan['parent_bindings']),'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'event':event,'id':sid,'details':details}; row['sha256']=digest(row)
        if self.hook:self.hook(event,'before_write')
        with (self.root/'journal.jsonl').open('ab') as stream:
            stream.write((json.dumps(row,sort_keys=True,allow_nan=False)+'\n').encode()); stream.flush(); os.fsync(stream.fileno())
        if self.hook:self.hook(event,'after_fsync')
    def inspect(self):
        require(file_hash(self.root/'plan.json')==self.plan_sha256,'continuation_plan_changed')
        require(not (self.root/'journal.jsonl').is_symlink(),'continuation_journal_invalid')
        raw=(self.root/'journal.jsonl').read_bytes(); require(len(raw)<=1024*1024,'continuation_journal_invalid')
        lines=raw.splitlines(keepends=True); torn=bool(lines and not lines[-1].endswith(b'\n'))
        slots={e['id']:{'state':'unexecuted','metrics':None,'artifacts':None,'classified':None,'returned_sha256':None} for e in self.plan['attempts']}
        intended=set(); halt=[]; records=[]; previous='0'*64
        for line in lines[:-1] if torn else lines:
            row=unique_json(line.decode()); require(type(row) is dict,'continuation_journal_invalid'); checksum=row.pop('sha256',None)
            require(set(row)=={'schema','sequence','previous_sha256','plan_sha256','execution_source_manifest_sha256','binding_sha256','timestamp_utc','event','id','details'} and row['schema']=='narrative-continuation-event-v4','continuation_journal_invalid')
            require(type(row['sequence']) is int and row['sequence']==len(records)+1 and row['previous_sha256']==previous and checksum==digest(row),'continuation_journal_invalid')
            require(row['plan_sha256']==self.plan_sha256 and row['execution_source_manifest_sha256']==self.plan['execution_source_manifest_sha256'] and row['binding_sha256']==digest(self.plan['parent_bindings']),'continuation_journal_binding_changed')
            require(type(row['timestamp_utc']) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00',row['timestamp_utc']),'continuation_journal_invalid')
            _apply(self.plan,slots,intended,halt,row['event'],row['id'],row['details'])
            records.append({**row,'sha256':checksum}); previous=checksum
        if torn:
            halt.append('durability_unknown')
            for slot in slots.values():
                if slot['state']!='terminal':slot['state']='interrupted_unknown'
        return {'schema':'narrative-continuation-accounting-v4','intended':2,'slots':slots,
            'counts':{key:sum(s['state']==key for s in slots.values()) for key in ('terminal','interrupted_unknown','unexecuted')},
            'intended_complete':intended==set(slots),'intended_ids':sorted(intended),'halt_codes':halt,'torn_tail':torn,
            'unresolved':any(s['state']=='interrupted_unknown' or s['state']=='terminal' and s['classified'] is not True for s in slots.values()),
            'records':records,'plan_sha256':self.plan_sha256,'journal_sha256':hashlib.sha256(raw).hexdigest()}


def prepare(context,*,hook=None):
    context.verify(); require(context.generated_bytes()+LIMITS['slot_reserve_bytes']<=LIMITS['raw_output_bytes'],'continuation_disk_reservation')
    return Journal.create(context.out,context.blueprint,hook)


def run(context,*,actor=None,hook=None,clock=time.monotonic):
    if actor is None:require(sys.version_info[:3]==(3,11,9),'continuation_python_version')
    context.verify(); journal=Journal(context.out,hook); require(journal.plan==context.blueprint,'continuation_plan_binding_changed')
    deadline=clock()+LIMITS['run_seconds']
    with journal.writer():
        state=journal.inspect(); require(state['intended_complete'] and not state['halt_codes'] and state['counts']=={'terminal':0,'interrupted_unknown':0,'unexecuted':2},'continuation_batch_replay_refused')
        require({p.name for p in journal.root.iterdir()}=={'plan.json','journal.jsonl','writer.lock'},'continuation_batch_replay_refused')
        write_exclusive(journal.root/'run.claim.json',{'schema':'narrative-continuation-claim-v4','plan_sha256':journal.plan_sha256,'execution_source_manifest_sha256':context.execution_sha256,'single_invocation':True})
        for entry in journal.plan['attempts']:
            sid=entry['id']; stage='preflight_drift'
            try:
                context.verify(); require(Journal(journal.root).plan==context.blueprint,'continuation_plan_changed')
                stage='budget_stop'; require(clock()+LIMITS['slot_seconds']+5<deadline and context.generated_bytes()+LIMITS['slot_reserve_bytes']<=LIMITS['raw_output_bytes'],'continuation_reservation_failed')
                path=journal.root/sid; require(not path.exists(),'continuation_attempt_replay_refused')
                stage='preflight_drift'; task=context.load_task(entry)
                execution={'schema':'narrative-continuation-execution-v4','id':sid,'prior_supplement_id':entry['prior_supplement_id'],
                    'parent_attempt_id':entry['parent_attempt_id'],'plan_sha256':journal.plan_sha256,
                    'execution_source_manifest_sha256':context.execution_sha256,'parent_bindings':journal.plan['parent_bindings'],
                    'input_sha256':entry['input_sha256'],'actor_input_content_sha256':digest(task),
                    'config_sha256':entry['config_sha256'],'original_actor_bindings':journal.plan['original_actor_bindings']}
                stage='durability_unknown'; execution_sha=write_exclusive(journal.root/(sid+'.execution.json'),execution); journal.start(sid)
                stage='actor_outcome_unknown'; result=(actor or native_actor)(task,entry['policy'],path,journal.plan['original_actor_bindings'])
                stage='receipt_incomplete'; returned_sha=write_exclusive(journal.root/(sid+'.returned.json'),{'schema':'narrative-continuation-returned-v4','actor_return':result})
                stage='durability_unknown'; journal.returned(sid,returned_sha)
                stage='receipt_invalid'; require(type(result) in (tuple,list) and len(result)==2,'continuation_receipt_invalid'); metrics,artifacts=result
                from .narrative_suite.ledger import validate_metrics
                validate_metrics(metrics)
                usage_sha=write_exclusive(journal.root/(sid+'.usage.json'),{'schema':'narrative-continuation-usage-v4','id':sid,**{key:metrics[key] for key in USAGE_KEYS},'billing_receipt_available':False})
                stage='receipt_incomplete'; verify_receipt(journal,entry,task,metrics,artifacts)
                stage='durability_unknown'; journal.terminal(sid,metrics,{**artifacts,'returned_sha256':returned_sha,'usage_sha256':usage_sha,'execution_sha256':execution_sha})
                stage='preflight_drift'; context.verify()
                stage='budget_stop'; require(clock()<deadline and context.generated_bytes()<=LIMITS['raw_output_bytes'],'continuation_budget_stop')
                stage='cleanup_unconfirmed'; require(metrics['owned_processes_remaining']==0,'continuation_cleanup_unconfirmed')
                stage='durability_unknown'; matches={'outcome':metrics['outcome'],'error':metrics['error']} in entry['hypotheses']; journal.classify(sid,matches)
                if not matches:return 2
            except Exception:
                try:journal.halt(stage,sid)
                except Exception:pass  # Durable claim/STARTED or torn tail still blocks replay.
                return 2
    return 0


def reconcile(root,source_sha256,*,context=None):
    journal=Journal(root); state=journal.inspect(); require(journal.plan['execution_source_manifest_sha256']==source_sha256,'continuation_report_version_changed')
    current=False
    if context is not None:
        try:current=context.verify() is True and context.blueprint==journal.plan
        except Exception:current=False
    rows=[]; errors=[]; unexpected=[]
    for entry in journal.plan['attempts']:
        sid=entry['id']; slot=state['slots'][sid]; evidence=None
        if slot['state']=='terminal':
            try:
                hashes={key:file_hash(journal.root/sid/name,bound) for key,(name,bound) in RAW_FILES.items()}
                for key,suffix in (('returned_sha256','returned'),('usage_sha256','usage'),('execution_sha256','execution')):hashes[key]=file_hash(journal.root/(sid+'.'+suffix+'.json'),128*1024)
                evidence=hashes==slot['artifacts']
            except Exception:evidence=False
            if not evidence:errors.append(sid)
            if {'outcome':slot['metrics']['outcome'],'error':slot['metrics']['error']} not in entry['hypotheses']:unexpected.append(sid)
        rows.append({'id':sid,'prior_supplement_id':entry['prior_supplement_id'],'original_slot':entry['parent_attempt_id'],
            'state':slot['state'],'metrics':slot['metrics'],'artifacts':slot['artifacts'],'classified':slot['classified'],'raw_evidence_hashes_match':evidence})
    counts=state['counts']; claimed=(journal.root/'run.claim.json').exists()
    stop=bool(state['halt_codes'] or state['unresolved'] or not current or errors or unexpected or not state['intended_complete'] or claimed and counts['terminal']!=2)
    return {'schema':'narrative-combined-reconciliation-v4','dispatch_authorized':False,
        'original_cohort':{'intended':72,'terminal':69,'unexecuted':3,'stopped_exit':2,'source_manifest_sha256':journal.plan['actor_source_manifest_sha256'],
            'a045':{'outcome':'failed','error':'invalid_decision','original_expected_error':'invalid_memo'}},
        'v3_frozen_cohort':{'intended':3,'terminal':0,'interrupted_unknown':1,'unexecuted':2,'parent_bindings':journal.plan['parent_bindings'],
            's001':{'original_slot':'a046','state':'interrupted_unknown','domain_outcome':None,'provider_requests':None,'interruption':'main_resource_supervisor'}},
        'continuation':{'intended':2,**counts,'persisted_intention_records':len(state['intended_ids']),'intended_complete':state['intended_complete'],
            'execution_source_manifest_sha256':source_sha256,'plan_sha256':state['plan_sha256'],'journal_sha256':state['journal_sha256']},
        'logical_original_slots':{'intended':72,'terminal':69+counts['terminal'],'interrupted_unknown':1+counts['interrupted_unknown'],'unexecuted':counts['unexecuted']},
        'physical_intent_records':77,'physical_terminal_records':69+counts['terminal'],
        'physical_interrupted_unknown_records':1+counts['interrupted_unknown'],'physical_unexecuted_intent_records':5+counts['unexecuted'],
        'aliases_are_additional_samples':False,'slots':rows,'unexpected_terminal_ids':unexpected,'raw_evidence_error_ids':errors,
        'current_source_and_all_parent_bindings_match':current,'halt_codes':state['halt_codes'],'torn_tail':state['torn_tail'],
        'batch_claim_present':claimed,'continuation_stop_for_main_review':stop,'overall_main_review_required':True,
        'permanently_retained_unknown_original_slot':'a046','process_roles_established_by_this_source':False,
        'semantic_quality':None,'aggregate_full_plan_safety':None,'actual_cost':None,'live_or_google_acceptance':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Two never-started aliases only; main resource accounting review and a new named lease required.')
    parser.add_argument('command',choices=('verify-source','prepare','run','report'))
    parser.add_argument('--source-sha256',required=True); parser.add_argument('--population-review',required=True); parser.add_argument('--lease-id',required=True)
    parser.add_argument('--provider',choices=('gateway-double',)); parser.add_argument('--model',choices=('test/synthetic-scalar',))
    args=parser.parse_args(argv)
    try:
        context=None
        try:context=Context(ROOT,args.source_sha256,args.population_review,args.lease_id)
        except Exception:
            if args.command!='report':raise
        if args.command=='report':output=reconcile(ROOT/OUTPUT,args.source_sha256,context=context); code=0
        else:
            context.verify()
            if args.command=='verify-source':output={'schema':'continuation-source-check-v4','all_bindings_match':True,'native_runs':0,'dispatch_authorized':False,'retained_unknown_original_slot':'a046'}; code=0
            else:
                require(args.provider=='gateway-double' and args.model=='test/synthetic-scalar','continuation_local_route_required')
                if args.command=='prepare':prepare(context); code=0
                else:code=run(context)
                output=reconcile(context.out,args.source_sha256,context=context)
        print(json.dumps(output,sort_keys=True,allow_nan=False)); return code
    except Exception:
        print(json.dumps({'schema':'continuation-command-error-v4','error':'continuation_command_refused','dispatch_authorized':False},sort_keys=True)); return 2


if __name__=='__main__':raise SystemExit(main())
