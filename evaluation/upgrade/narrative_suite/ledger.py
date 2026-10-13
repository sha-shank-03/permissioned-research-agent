"""Exclusive, append-only accounting. No actor, source prose or credentials.

STARTED is fsynced before execution. Recovery reports interrupted_unknown and
never retries that attempt. A torn tail blocks all mutation and conservatively
classifies every nonterminal intent unknown; the original bytes are preserved.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re

from .data import digest, require, unique_json

POLICIES=('adaptive','fixed')
OUTCOMES=('needs_review','failed','cancelled')
ERRORS=(None,'budget_exhausted','invalid_decision','invalid_memo','cancelled',
        'source_state_changed','provider_error','provider_timeout','gateway_authentication_required',
        'gateway_rate_limited','gateway_budget_required','gateway_verification_required',
        'gateway_unavailable','gateway_response_invalid','gateway_response_lost','runner_error','storage_error')
METRICS={'outcome','error','provider_requests','searches','reads','repairs','restarts',
         'backend_search_calls','elapsed_seconds','model_tokens','actual_cost','estimated_cost',
         'unsafe_attempts','unsafe_effects','semantic_correctness','coverage','owned_processes_remaining'}


def write_exclusive(path,value):
    raw=(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n').encode()
    with Path(path).open('xb') as f:f.write(raw); f.flush(); os.fsync(f.fileno())
    return hashlib.sha256(raw).hexdigest()


def validate_metrics(m):
    require(type(m) is dict and set(m)==METRICS,'ledger_metrics_invalid')
    require(m['outcome'] in OUTCOMES and m['error'] in ERRORS,'ledger_metrics_invalid')
    require((m['outcome']=='needs_review')==(m['error'] is None),'ledger_metrics_invalid')
    for k in ('provider_requests','searches','reads','repairs','restarts','backend_search_calls','owned_processes_remaining'):
        require(type(m[k]) is int and 0<=m[k]<=1000,'ledger_metrics_invalid')
    require(type(m['elapsed_seconds']) in (int,float) and math.isfinite(m['elapsed_seconds']) and 0<=m['elapsed_seconds']<=600,'ledger_metrics_invalid')
    # Local authored double has no bill or token receipt. Do not invent zero cost.
    require(all(m[k] is None for k in ('model_tokens','actual_cost','estimated_cost','semantic_correctness','coverage')),'ledger_metrics_invalid')
    for k in ('unsafe_attempts','unsafe_effects'):
        require(m[k] is None or type(m[k]) is int and 0<=m[k]<=1000,'ledger_metrics_invalid')
    return m


def validate_plan(plan):
    require(type(plan) is dict and set(plan)=={'schema','split','bindings','attempts'},'ledger_plan_invalid')
    require(plan['schema']=='narrative-intended-plan-v1' and plan['split'] in ('development','capability'),'ledger_plan_invalid')
    require(set(plan['bindings'])=={'pack_sha256','source_manifest_sha256','prompt_sha256','provider','model','repeats'},'ledger_plan_invalid')
    for k in ('pack_sha256','source_manifest_sha256','prompt_sha256'):
        require(type(plan['bindings'][k]) is str and re.fullmatch('[0-9a-f]{64}',plan['bindings'][k]),'ledger_plan_invalid')
    require(plan['bindings']['provider']=='owned_narrative_gateway_double' and plan['bindings']['model']=='test/synthetic-scalar' and plan['bindings']['repeats']==1,'ledger_plan_invalid')
    attempts=plan['attempts']; n=24 if plan['split']=='development' else 48
    require(type(attempts) is list and len(attempts)==n,'ledger_plan_invalid')
    task_policies={}; pair_bindings={}; family_tasks={}
    for index,a in enumerate(attempts,1):
        require(set(a)=={'ordinal','id','task_id','family','principal','policy','input_sha256','rubric_sha256','config_sha256'},'ledger_plan_invalid')
        require(a['ordinal']==index and a['id']==f'a{index:03d}' and a['policy'] in POLICIES,'ledger_plan_invalid')
        require(type(a['task_id']) is str and re.fullmatch(plan['split']+r'-[a-z]+-[0-9]{2}',a['task_id']),'ledger_plan_invalid')
        require(a['principal'] in ('alex','priya','sam'),'ledger_plan_invalid')
        require(a['family'] in ('qualifiers_dependencies','conflicts','temporal_missing','scope_injection','revocation_replacement','budget_failure'),'ledger_plan_invalid')
        for k in ('input_sha256','rubric_sha256','config_sha256'):
            require(type(a[k]) is str and re.fullmatch('[0-9a-f]{64}',a[k]),'ledger_plan_invalid')
        policies=task_policies.setdefault(a['task_id'],set()); require(a['policy'] not in policies,'ledger_plan_invalid'); policies.add(a['policy'])
        binding=tuple(a[k] for k in ('family','principal','input_sha256','rubric_sha256','config_sha256'))
        require(pair_bindings.setdefault(a['task_id'],binding)==binding,'ledger_policy_budget_mismatch')
        family_tasks.setdefault(a['family'],set()).add(a['task_id'])
    require(len(task_policies)==n//2 and all(p==set(POLICIES) for p in task_policies.values()),'ledger_plan_invalid')
    require(len(family_tasks)==6 and all(len(ids)==n//12 for ids in family_tasks.values()),'ledger_plan_invalid')
    return plan


class Ledger:
    def __init__(self,root):
        self.root=Path(root).resolve(); raw=(self.root/'plan.json').read_bytes()
        require(len(raw)<=128*1024,'ledger_plan_invalid'); self.plan=validate_plan(unique_json(raw.decode()))
        self.plan_sha256=hashlib.sha256(raw).hexdigest(); self.owned=False

    @classmethod
    def create(cls,root,plan):
        validate_plan(plan); root=Path(root).resolve(); root.mkdir(parents=True,exist_ok=False)
        write_exclusive(root/'plan.json',plan)
        with (root/'journal.jsonl').open('xb') as f:f.flush(); os.fsync(f.fileno())
        ledger=cls(root)
        with ledger.writer(initializing=True):
            for a in plan['attempts']:ledger._append({'event':'INTENDED','attempt_id':a['id']})
        return ledger

    @contextmanager
    def writer(self,*,initializing=False):
        require(not self.owned,'ledger_writer_conflict')
        lock=self.root/'writer.lock'
        try:
            with lock.open('x',encoding='ascii') as f:f.write('exclusive-local-writer\n'); f.flush(); os.fsync(f.fileno())
        except FileExistsError:require(False,'ledger_writer_conflict')
        self.owned=True
        try:
            if not initializing:require(not self.inspect()['torn_tail'],'ledger_torn_tail')
            yield self
        finally:
            self.owned=False; lock.unlink()

    def inspect(self):
        raw=(self.root/'journal.jsonl').read_bytes(); require(len(raw)<=4*1024*1024,'ledger_invalid')
        lines=raw.splitlines(keepends=True); torn=bool(lines and not lines[-1].endswith(b'\n'))
        states={a['id']:{'state':'unexecuted','metrics':None,'artifacts':None} for a in self.plan['attempts']}
        intended=set(); previous='0'*64; records=[]
        for line in lines[:-1] if torn else lines:
            row=unique_json(line.decode()); require(type(row) is dict,'ledger_invalid')
            checksum=row.pop('sha256',None)
            require(row.get('sequence')==len(records)+1 and row.get('previous_sha256')==previous and checksum==digest(row),'ledger_invalid')
            require(row.get('plan_sha256')==self.plan_sha256 and row.get('attempt_id') in states,'ledger_invalid')
            base={'sequence','previous_sha256','plan_sha256','timestamp_utc','event','attempt_id'}
            kind=row['event']; a=row['attempt_id']; s=states[a]
            if kind=='INTENDED':
                require(set(row)==base and a not in intended,'ledger_invalid'); intended.add(a)
            elif kind=='STARTED':
                require(set(row)==base and a in intended and s['state']=='unexecuted','ledger_invalid'); s['state']='interrupted_unknown'
            elif kind=='TERMINAL':
                require(set(row)==base|{'metrics','artifacts'} and s['state']=='interrupted_unknown','ledger_invalid')
                validate_metrics(row['metrics']); artifacts=row['artifacts']
                require(set(artifacts)=={'report_sha256','corpus_sha256','trace_sha256'},'ledger_invalid')
                require(all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in artifacts.values()),'ledger_invalid')
                s.update(state='terminal',metrics=row['metrics'],artifacts=artifacts)
            else:require(False,'ledger_invalid')
            records.append({**row,'sha256':checksum}); previous=checksum
        require(torn or intended==set(states),'ledger_invalid')
        if torn:
            for s in states.values():
                if s['state']!='terminal':s['state']='interrupted_unknown'
        counts={k:sum(s['state']==k for s in states.values()) for k in ('terminal','interrupted_unknown','unexecuted')}
        return {'schema':'narrative-attempt-accounting-v1','intended':len(states),'counts':counts,
                'attempts':states,'torn_tail':torn,'records':records,'journal_sha256':hashlib.sha256(raw).hexdigest(),
                'plan_sha256':self.plan_sha256,'semantic_correctness':None,'coverage':None}

    def _append(self,value):
        require(self.owned,'ledger_writer_required')
        # During initial construction INTENDED is incomplete; read only its chain.
        lines=(self.root/'journal.jsonl').read_bytes().splitlines()
        previous=unique_json(lines[-1].decode())['sha256'] if lines else '0'*64
        row={'sequence':len(lines)+1,'previous_sha256':previous,'plan_sha256':self.plan_sha256,
             'timestamp_utc':datetime.now(timezone.utc).isoformat(),**value}
        row['sha256']=digest(row)
        with (self.root/'journal.jsonl').open('ab') as f:
            f.write((json.dumps(row,sort_keys=True,allow_nan=False)+'\n').encode()); f.flush(); os.fsync(f.fileno())

    def start(self,attempt_id):
        state=self.inspect(); require(state['attempts'][attempt_id]['state']=='unexecuted','ledger_attempt_not_unexecuted')
        self._append({'event':'STARTED','attempt_id':attempt_id})

    def terminal(self,attempt_id,metrics,artifacts):
        state=self.inspect(); require(state['attempts'][attempt_id]['state']=='interrupted_unknown','ledger_attempt_not_started')
        validate_metrics(metrics)
        require(set(artifacts)=={'report_sha256','corpus_sha256','trace_sha256'} and all(type(v) is str and re.fullmatch('[0-9a-f]{64}',v) for v in artifacts.values()),'ledger_artifacts_invalid')
        self._append({'event':'TERMINAL','attempt_id':attempt_id,'metrics':metrics,'artifacts':artifacts})
