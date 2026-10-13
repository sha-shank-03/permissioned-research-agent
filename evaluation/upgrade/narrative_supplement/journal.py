"""Exclusive fsynced supplement journal; no parent writer or actor imports."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re

from ..narrative_correction_v2 import BINDINGS, ENTRY_KEYS, VERSION
from ..narrative_suite.data import digest, require, unique_json
from ..narrative_suite.ledger import validate_metrics
from .contract import LIMITS, OUTPUT, PARENT, read_json

ARTIFACTS={'report_sha256','trace_sha256','corpus_sha256','returned_sha256','usage_sha256','execution_sha256'}
HALT_CODES={'preflight_drift','actor_outcome_unknown','receipt_incomplete','receipt_invalid',
            'unexpected_terminal','durability_unknown','budget_stop','cleanup_unconfirmed'}
PLAN_KEYS={'schema','execution_source_manifest_sha256','execution_code_sha256',
    'actor_source_manifest_sha256','correction_source_manifest_sha256','proposal_file_sha256',
    'proposal_content_sha256','parent_bindings','original_actor_bindings','attempts','limits',
    'parent_relative_root','output_relative_root','lease_label','lease_label_is_not_authorization',
    'hypothesis_version','semantic_quality','aggregate_full_plan_safety'}


def sha(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None


def validate_plan(plan):
    require(type(plan) is dict and set(plan)==PLAN_KEYS,'supplement_plan_invalid')
    require(plan['schema']=='narrative-supplement-plan-v3' and plan['hypothesis_version']==VERSION,'supplement_plan_invalid')
    for key in PLAN_KEYS:
        if key.endswith('_sha256'):require(sha(plan[key]),'supplement_plan_invalid')
    require(type(plan['parent_bindings']) is dict and set(plan['parent_bindings'])==BINDINGS and all(sha(v) for v in plan['parent_bindings'].values()),'supplement_plan_invalid')
    bindings=plan['original_actor_bindings']
    require(type(bindings) is dict and set(bindings)=={'pack_sha256','source_manifest_sha256','prompt_sha256','provider','model','repeats'},'supplement_plan_invalid')
    require(all(sha(bindings[k]) for k in ('pack_sha256','source_manifest_sha256','prompt_sha256')) and bindings['provider']=='owned_narrative_gateway_double' and bindings['model']=='test/synthetic-scalar' and type(bindings['repeats']) is int and bindings['repeats']==1,'supplement_plan_invalid')
    require(bindings['source_manifest_sha256']==plan['actor_source_manifest_sha256']==plan['parent_bindings']['actor_source_manifest_sha256'],'supplement_plan_invalid')
    require(plan['parent_relative_root']==PARENT and plan['output_relative_root']==OUTPUT and plan['limits']==LIMITS,'supplement_plan_invalid')
    require(type(plan['lease_label']) is str and re.fullmatch('[a-z][a-z0-9_-]{2,79}',plan['lease_label']) and plan['lease_label_is_not_authorization'] is True,'supplement_plan_invalid')
    require(plan['semantic_quality'] is None and plan['aggregate_full_plan_safety'] is None,'supplement_plan_invalid')
    entries=plan['attempts']; require(type(entries) is list and len(entries)==3,'supplement_plan_invalid')
    for index,entry in enumerate(entries):
        require(type(entry) is dict and set(entry)==ENTRY_KEYS,'supplement_plan_invalid')
        require(entry['id']==f's{index+1:03d}' and entry['parent_attempt_id']==f'a{index+46:03d}' and entry['state']=='unexecuted','supplement_plan_invalid')
        require(entry['task_id']==('capability-budget-03' if index==0 else 'capability-budget-04') and entry['family']=='budget_failure','supplement_plan_invalid')
        require(entry['principal']==('alex' if index==0 else 'priya') and entry['policy']==('adaptive' if index==1 else 'fixed'),'supplement_plan_invalid')
        require(all(sha(entry[k]) for k in ('input_sha256','rubric_sha256','config_sha256')),'supplement_plan_invalid')
        expected=[{'outcome':'failed','error':'invalid_decision'}] if index==0 else [{'outcome':'cancelled','error':'cancelled'}]
        require(entry['hypotheses']==expected,'supplement_plan_invalid')
    return plan


def write_exclusive(path,value,maximum=128*1024):
    raw=(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    require(len(raw)<=maximum,'supplement_output_bound')
    with Path(path).open('xb') as stream:
        stream.write(raw); stream.flush(); os.fsync(stream.fileno())
    return hashlib.sha256(raw).hexdigest()


def _apply(plan,slots,intended,halt,kind,sid,details):
    require(type(details) is dict,'supplement_journal_invalid')
    if kind=='HALTED':
        require(sid is None or sid in slots,'supplement_journal_invalid')
        require(set(details)=={'code'} and details['code'] in HALT_CODES,'supplement_journal_invalid')
        halt.append(details['code']); return
    require(sid in slots,'supplement_replay_refused'); slot=slots[sid]
    if kind=='INTENDED':
        require(not halt and all(s['state']=='unexecuted' for s in slots.values()),'supplement_journal_invalid')
        require(set(details)=={'parent_attempt_id'} and sid not in intended and details['parent_attempt_id']==next(e['parent_attempt_id'] for e in plan['attempts'] if e['id']==sid),'supplement_journal_invalid')
        intended.add(sid)
    elif kind=='STARTED':
        require(not halt and not details and intended==set(slots) and slot['state']=='unexecuted','supplement_replay_refused')
        require(all(slots[p]['state']=='terminal' and slots[p]['classified'] is True for p in slots if p<sid),'supplement_prior_slot_unresolved')
        require(all(s['state']!='interrupted_unknown' for s in slots.values()),'supplement_prior_slot_unresolved')
        slot['state']='interrupted_unknown'
    elif kind=='RETURNED':
        require(slot['state']=='interrupted_unknown' and slot['returned_sha256'] is None and set(details)=={'returned_sha256'} and sha(details['returned_sha256']),'supplement_journal_invalid')
        slot['returned_sha256']=details['returned_sha256']
    elif kind=='TERMINAL':
        require(slot['state']=='interrupted_unknown' and slot['returned_sha256'] is not None and set(details)=={'metrics','artifacts'},'supplement_terminal_invalid')
        validate_metrics(details['metrics']); artifacts=details['artifacts']
        require(type(artifacts) is dict and set(artifacts)==ARTIFACTS and all(sha(v) for v in artifacts.values()) and artifacts['returned_sha256']==slot['returned_sha256'],'supplement_terminal_invalid')
        slot.update(state='terminal',metrics=details['metrics'],artifacts=artifacts)
    elif kind=='CLASSIFIED':
        require(not halt and slot['state']=='terminal' and slot['classified'] is None and set(details)=={'matches_hypothesis'} and type(details['matches_hypothesis']) is bool,'supplement_classification_invalid')
        expected=next(e['hypotheses'] for e in plan['attempts'] if e['id']==sid)
        actual={'outcome':slot['metrics']['outcome'],'error':slot['metrics']['error']}
        require(details['matches_hypothesis']==(actual in expected),'supplement_classification_invalid')
        slot['classified']=details['matches_hypothesis']
        if not slot['classified']:halt.append('unexpected_terminal')
    else:require(False,'supplement_journal_invalid')


class Journal:
    def __init__(self,root,hook=None):
        self.root=Path(root).resolve(); self.hook=hook; self.owned=False
        self.plan=validate_plan(read_json(self.root/'plan.json'))
        self.plan_sha256=hashlib.sha256((self.root/'plan.json').read_bytes()).hexdigest()

    @classmethod
    def create(cls,root,plan,hook=None):
        validate_plan(plan); root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=False)
        write_exclusive(root/'plan.json',plan)
        with (root/'journal.jsonl').open('xb') as stream:stream.flush(); os.fsync(stream.fileno())
        obj=cls(root,hook)
        with obj.writer(initializing=True):
            for entry in obj.plan['attempts']:obj._append('INTENDED',entry['id'],{'parent_attempt_id':entry['parent_attempt_id']})
        require(obj.inspect()['intended_complete'],'supplement_intentions_incomplete')
        return obj

    @contextmanager
    def writer(self,*,initializing=False):
        require(not self.owned,'supplement_writer_conflict'); lock=self.root/'writer.lock'
        try:
            with lock.open('x',encoding='ascii') as stream:stream.write('exclusive-supplement-writer\n'); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError:require(False,'supplement_writer_conflict')
        self.owned=True
        try:
            if not initializing:
                state=self.inspect(); require(not state['torn_tail'] and state['intended_complete'],'supplement_journal_incomplete')
            yield self
        finally:self.owned=False; lock.unlink()

    def _append(self,event,sid,details):
        require(self.owned,'supplement_writer_required')
        state=self.inspect(); require(not state['torn_tail'],'supplement_torn_tail')
        # Validate the transition and closed/redacted shape BEFORE any bytes.
        _apply(self.plan,state['slots'],set(state['intended_ids']),list(state['halt_codes']),event,sid,details)
        records=state['records']; previous=records[-1]['sha256'] if records else '0'*64
        row={'schema':'narrative-supplement-event-v3','sequence':len(records)+1,'previous_sha256':previous,
             'plan_sha256':self.plan_sha256,'execution_source_manifest_sha256':self.plan['execution_source_manifest_sha256'],
             'binding_sha256':digest(self.plan['parent_bindings']),'timestamp_utc':datetime.now(timezone.utc).isoformat(),
             'event':event,'id':sid,'details':details}
        row['sha256']=digest(row)
        if self.hook:self.hook(event,'before_write')
        with (self.root/'journal.jsonl').open('ab') as stream:
            stream.write((json.dumps(row,sort_keys=True,allow_nan=False)+'\n').encode()); stream.flush(); os.fsync(stream.fileno())
        if self.hook:self.hook(event,'after_fsync')

    def inspect(self):
        require(hashlib.sha256((self.root/'plan.json').read_bytes()).hexdigest()==self.plan_sha256,'supplement_plan_binding_changed')
        require(not (self.root/'journal.jsonl').is_symlink(),'supplement_journal_invalid')
        raw=(self.root/'journal.jsonl').read_bytes(); require(len(raw)<=1024*1024,'supplement_journal_invalid')
        lines=raw.splitlines(keepends=True); torn=bool(lines and not lines[-1].endswith(b'\n'))
        slots={e['id']:{'state':'unexecuted','metrics':None,'artifacts':None,'classified':None,'returned_sha256':None} for e in self.plan['attempts']}
        intended=set(); halt=[]; previous='0'*64; records=[]
        for line in lines[:-1] if torn else lines:
            row=unique_json(line.decode()); require(type(row) is dict,'supplement_journal_invalid'); checksum=row.pop('sha256',None)
            require(set(row)=={'schema','sequence','previous_sha256','plan_sha256','execution_source_manifest_sha256','binding_sha256','timestamp_utc','event','id','details'},'supplement_journal_invalid')
            require(row['schema']=='narrative-supplement-event-v3' and type(row['sequence']) is int and row['sequence']==len(records)+1 and row['previous_sha256']==previous and checksum==digest(row),'supplement_journal_invalid')
            require(type(row['timestamp_utc']) is str and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?\+00:00',row['timestamp_utc']),'supplement_journal_invalid')
            require(row['plan_sha256']==self.plan_sha256 and row['execution_source_manifest_sha256']==self.plan['execution_source_manifest_sha256'] and row['binding_sha256']==digest(self.plan['parent_bindings']),'supplement_journal_binding_changed')
            _apply(self.plan,slots,intended,halt,row['event'],row['id'],row['details'])
            records.append({**row,'sha256':checksum}); previous=checksum
        if torn:
            halt.append('durability_unknown')
            for slot in slots.values():
                if slot['state']!='terminal':slot['state']='interrupted_unknown'
        complete=intended==set(slots)
        unresolved=any(s['state']=='interrupted_unknown' or s['state']=='terminal' and s['classified'] is not True for s in slots.values())
        return {'schema':'narrative-supplement-accounting-v3','intended':3,'slots':slots,
                'counts':{k:sum(s['state']==k for s in slots.values()) for k in ('terminal','interrupted_unknown','unexecuted')},
                'intended_complete':complete,'intended_ids':sorted(intended),'torn_tail':torn,'halt_codes':halt,'unresolved':unresolved,
                'records':records,'journal_sha256':hashlib.sha256(raw).hexdigest(),'plan_sha256':self.plan_sha256}

    def start(self,sid):self._append('STARTED',sid,{})
    def returned(self,sid,checksum):self._append('RETURNED',sid,{'returned_sha256':checksum})
    def terminal(self,sid,metrics,artifacts):self._append('TERMINAL',sid,{'metrics':metrics,'artifacts':artifacts})
    def classify(self,sid,matches):self._append('CLASSIFIED',sid,{'matches_hypothesis':matches})
    def halt(self,code,sid=None):self._append('HALTED',sid,{'code':code})
