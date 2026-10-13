"""Pure parser/repair/classification/binding checks. No SQL/native executor."""
import ast
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import unittest

from evaluation.upgrade.narrative_correction_v2 import (
    BINDINGS, corrected_hypotheses, pending_accounting, validate_proposal)
from evaluation.upgrade.narrative_suite.data import SuiteError, digest
from permissioned_research.domain import ResearchError, ResearchRequest
from permissioned_research.upgrade.config import DispatchConfig, DOUBLE_MODEL
from permissioned_research.upgrade.double import completion
from permissioned_research.upgrade.narrative.boundary import TOPICS, validate_draft
from permissioned_research.upgrade.narrative.double import AuthoredPolicy
from permissioned_research.upgrade.narrative.projection import Document
from permissioned_research.upgrade.narrative.provider import decode
from permissioned_research.upgrade.narrative.workflow import NarrativeWorkflow

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/'fixtures/upgrade/narrative-correction-v2'
SHAPES=json.loads((DATA/'self-approval-responses.json').read_text(encoding='utf-8'))
CHECKPOINT=json.loads((DATA/'parent-checkpoint.json').read_text(encoding='utf-8'))
CONFIG=DispatchConfig(DOUBLE_MODEL)


def proposal():
    # This is the pure contract fixture, not an executed plan or journal.
    bindings={**CHECKPOINT['bindings'],'correction_code_sha256':'7'*64}
    attempts=[]
    original={a['id']:a for a in CHECKPOINT['capability_plan']['attempts']}
    for index,parent in enumerate(('a046','a047','a048'),1):
        old=original[parent]
        attempts.append({'id':f's{index:03d}','parent_attempt_id':parent,
                         **{k:old[k] for k in ('task_id','family','principal','policy','input_sha256','rubric_sha256','config_sha256')},
                         'hypotheses':corrected_hypotheses(old['task_id'],old['policy'],CHECKPOINT['frozen_hypotheses'][parent]),'state':'unexecuted'})
    return {'schema':'narrative-three-slot-continuation-v2','version':'u2b2-operational-hypothesis-v2',
            'state':'proposed_unexecuted','bindings':bindings,'parent_checkpoint_sha256':digest(CHECKPOINT),
            'attempts':attempts,'accounting':{'original_intended':72,'original_terminal':69,'original_unexecuted':3,
                'supplement_intended':3,'supplement_started':0,'supplement_terminal':0,'supplement_interrupted_unknown':0,
                'supplement_unexecuted':3,'logical_original_slots_pending':3,'physical_intent_records':75},
            'execution_protocol':{'existing_plan_or_journal_writes':False,'replay_started_or_terminal':False,
                'restamp_earlier_source_versions':False,'native_entrypoint_implemented':False,
                'new_main_review_and_named_lease_required':True,'future_harness_source_freeze_required':True,
                'fresh_supplement_directory_required':True,'fsync_started_before_actor_required':True}}


def event(plan,sequence,sid,kind,outcome=None,error=None):
    return {'sequence':sequence,'id':sid,'event':kind,'proposal_sha256':digest(plan),
            'execution_source_sha256':'8'*64,'outcome':outcome,'error':error}


def fixed_self_approval_shape():
    request={'messages':[{'role':'system','content':'pure contract'},
                         {'role':'user','content':json.dumps({'request':{'vendor':'ParserProof','as_of':'2026-10-12'},'controller_collected_evidence':[]})},
                         {'role':'user','content':json.dumps({'control':{'phase':'SYNTHESIZING'}})}]}
    return AuthoredPolicy('self_approve').respond(request)


def isolated_repair_report(policy,bodies):
    """Exercise the unchanged run error branch with actual decoder failures.

    Constructor/collection/transport/authority are deliberately not invoked.
    This isolates repair propagation, not a task run or native integration.
    """
    workflow=object.__new__(NarrativeWorkflow)
    workflow.has_run=False; workflow.clock=lambda:0.0; workflow.config=CONFIG
    workflow.policy=policy; workflow.cancellation=SimpleNamespace(is_set=lambda:False)
    workflow.request=ResearchRequest('alex','ParserProof','2026-10-12')
    workflow.observations=[]; workflow.last_error=None
    workflow.counters={'requests':0,'searches':0,'reads':0,'repairs':0,'restarts':0,'backend_search_calls':0}
    workflow.projection=SimpleNamespace(restrict_deadline=lambda _:None,clear=lambda:None,close=lambda:None)
    workflow.provider=SimpleNamespace(requests=0,events=[],evidence_kind='pure_saved_parser_contract',close=lambda:None)
    collected=[]; workflow._fixed_collect=lambda:collected.append('pure_collection_stub')
    def dispatch():
        index=workflow.provider.requests; workflow.provider.requests+=1; workflow.counters['requests']+=1
        return decode(bodies[index],CONFIG,set())
    workflow._dispatch=dispatch
    workflow._release=lambda _:(_ for _ in ()).throw(AssertionError('self approval reached release'))
    result=workflow.run()
    return result,collected


class ParserCorrectionTests(unittest.TestCase):
    def test_both_saved_responses_match_original_bytes_and_reject_at_decode(self):
        self.assertEqual(len(SHAPES['responses']),2)
        for response in SHAPES['responses']:
            raw=response['raw_body'].encode('utf-8')
            self.assertEqual(hashlib.sha256(raw).hexdigest(),response['sha256'])
            args=json.loads(json.loads(raw)['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
            self.assertEqual(set(args),{'topics','semantic_approval'}); self.assertIs(args['semantic_approval'],True)
            with self.assertRaises(ResearchError) as failure:decode(raw,CONFIG,set())
            self.assertEqual(failure.exception.code,'invalid_decision')

    def test_removing_only_forbidden_key_allows_native_decoding(self):
        for response in SHAPES['responses']:
            value=json.loads(response['raw_body']); function=value['choices'][0]['message']['tool_calls'][0]['function']
            args=json.loads(function['arguments']); del args['semantic_approval']; function['arguments']=json.dumps(args)
            call,_=decode(json.dumps(value).encode(),CONFIG,set())
            self.assertEqual(call.name,'finish')

    def test_false_or_null_self_approval_is_also_rejected_by_closed_keys(self):
        for flag in (False,None):
            value=json.loads(SHAPES['responses'][0]['raw_body']); function=value['choices'][0]['message']['tool_calls'][0]['function']
            args=json.loads(function['arguments']); args['semantic_approval']=flag; function['arguments']=json.dumps(args)
            with self.assertRaises(ResearchError) as failure:decode(json.dumps(value).encode(),CONFIG,set())
            self.assertEqual(failure.exception.code,'invalid_decision')

    def test_fixed_authored_finish_is_rejected_as_invalid_decision(self):
        raw=fixed_self_approval_shape()
        self.assertTrue(json.loads(json.loads(raw)['choices'][0]['message']['tool_calls'][0]['function']['arguments'])['semantic_approval'])
        with self.assertRaises(ResearchError) as failure:decode(raw,CONFIG,set())
        self.assertEqual(failure.exception.code,'invalid_decision')

    def test_adaptive_one_repair_preserves_parser_error_and_null_draft(self):
        result,collected=isolated_repair_report('adaptive',[r['raw_body'].encode() for r in SHAPES['responses']])
        self.assertEqual((result['outcome'],result['error'],result['provider_requests'],result['counters']['repairs']),('failed','invalid_decision',2,1))
        self.assertIsNone(result['draft']); self.assertIsNone(result['html']); self.assertEqual(collected,[])

    def test_fixed_one_repair_preserves_parser_error_and_null_draft(self):
        raw=fixed_self_approval_shape(); result,collected=isolated_repair_report('fixed',[raw,raw])
        self.assertEqual((result['outcome'],result['error'],result['provider_requests'],result['counters']['repairs']),('failed','invalid_decision',2,1))
        self.assertIsNone(result['draft']); self.assertEqual(len(collected),1)

    def test_invalid_memo_belongs_to_later_citation_validation(self):
        request=ResearchRequest('alex','ParserProof','2026-10-12'); handle='e_'+'1'*32
        body='hosting_region: Synthetic primary records remain in Zone101.'
        doc=Document(handle,'docshub','Pure source','1',hashlib.sha256(body.encode()).hexdigest(),body,'2026-10-01','2027-01-01')
        topics=[{'topic':topic,'state':'unknown','claims':[]} for topic in TOPICS]
        topics[0].update(state='supported',claims=[{'text':'Synthetic primary records remain in Zone101.',
            'quotes':[{'handle':handle,'revision':'1','content_sha256':doc.content_sha256,'start':0,'end':len(body),'quote':body+' substituted'}]}])
        call,_=decode(completion('finish',{'topics':topics},'pure-stage-proof',usage=False),CONFIG,set())
        projection=SimpleNamespace(request=request,principal='alex',check_context=lambda:None,opened={handle:doc},selection_key=lambda _:'pure-source')
        with self.assertRaises(ResearchError) as failure:validate_draft(call.arguments,request,projection)
        self.assertEqual(failure.exception.code,'invalid_memo')

    def test_only_two_frozen_self_approval_hypotheses_are_changed(self):
        for policy in ('adaptive','fixed'):
            self.assertEqual(corrected_hypotheses('capability-budget-03',policy,[{'outcome':'failed','error':'invalid_memo'}]),[{'outcome':'failed','error':'invalid_decision'}])
        old=[{'outcome':'cancelled','error':'cancelled'}]
        self.assertEqual(corrected_hypotheses('capability-budget-04','fixed',old),old)
        self.assertEqual(old,[{'outcome':'cancelled','error':'cancelled'}])

    def test_changed_parent_hypothesis_is_not_silently_recorrected(self):
        for changed in ([],[{'outcome':'needs_review','error':None}],[{'outcome':'failed','error':'invalid_decision'}]):
            with self.assertRaises(SuiteError):corrected_hypotheses('capability-budget-03','fixed',changed)


class ContinuationProposalTests(unittest.TestCase):
    def setUp(self):self.plan=proposal(); self.bindings=copy.deepcopy(self.plan['bindings'])
    def verify(self,plan=None,checkpoint=None,bindings=None):
        return validate_proposal(plan or self.plan,checkpoint or CHECKPOINT,self.bindings if bindings is None else bindings)

    def test_exact_three_pending_slots_keep_parent_exit_and_denominators(self):
        result=self.verify()
        self.assertEqual([r['parent_attempt_id'] for r in self.plan['attempts']],['a046','a047','a048'])
        self.assertEqual(result['original_cohort'],{'intended':72,'terminal':69,'unexecuted':3,'stopped_exit':2})
        self.assertEqual(result['supplement'],{'intended':3,'terminal':0,'interrupted_unknown':0,'unexecuted':3})
        self.assertEqual(result['physical_intent_records'],75); self.assertEqual(result['physical_unexecuted_intent_records'],6)
        self.assertFalse(result['native_execution_authorized']); self.assertIsNone(result['aggregate_full_plan_safety'])

    def test_a045_or_any_terminal_slot_cannot_be_proposed_for_replay(self):
        for slot in ('a045','a001'):
            plan=copy.deepcopy(self.plan); plan['attempts'][0]['parent_attempt_id']=slot
            with self.assertRaises(SuiteError):self.verify(plan)

    def test_missing_or_duplicate_slot_is_refused(self):
        plan=copy.deepcopy(self.plan); plan['attempts'].pop()
        with self.assertRaises(SuiteError):self.verify(plan)
        plan=copy.deepcopy(self.plan); plan['attempts'][1]['parent_attempt_id']='a046'
        with self.assertRaises(SuiteError):self.verify(plan)

    def test_missing_and_extra_global_bindings_are_refused(self):
        for name in BINDINGS:
            bindings=copy.deepcopy(self.bindings); del bindings[name]
            with self.assertRaises(SuiteError):self.verify(bindings=bindings)
        bindings=copy.deepcopy(self.bindings); bindings['automatic_resume']=True
        with self.assertRaises(SuiteError):self.verify(bindings=bindings)

    def test_every_changed_source_plan_journal_or_inventory_binding_is_refused(self):
        for name in BINDINGS:
            bindings=copy.deepcopy(self.bindings); bindings[name]='f'*64
            with self.assertRaises(SuiteError):self.verify(bindings=bindings)

    def test_missing_or_changed_task_policy_input_rubric_budget_binding_is_refused(self):
        for key in ('task_id','principal','policy','input_sha256','rubric_sha256','config_sha256'):
            plan=copy.deepcopy(self.plan); del plan['attempts'][0][key]
            with self.assertRaises(SuiteError):self.verify(plan)
            plan=copy.deepcopy(self.plan); plan['attempts'][0][key]='changed'
            with self.assertRaises(SuiteError):self.verify(plan)

    def test_changed_checkpoint_or_started_pending_slot_is_refused(self):
        checkpoint=copy.deepcopy(CHECKPOINT); checkpoint['capability_states']['a046']['state']='interrupted_unknown'
        with self.assertRaises(SuiteError):self.verify(checkpoint=checkpoint)
        plan=copy.deepcopy(self.plan); plan['parent_checkpoint_sha256']=digest(checkpoint)
        with self.assertRaises(SuiteError):self.verify(plan,checkpoint)

    def test_old_first_failure_and_exit_cannot_be_restamped(self):
        for field,value in (('stopped_slice_exit',0),('original_attempts_terminal',68)):
            checkpoint=copy.deepcopy(CHECKPOINT); checkpoint[field]=value
            plan=copy.deepcopy(self.plan); plan['parent_checkpoint_sha256']=digest(checkpoint)
            with self.assertRaises(SuiteError):self.verify(plan,checkpoint)
        checkpoint=copy.deepcopy(CHECKPOINT); checkpoint['capability_states']['a045']['error']='invalid_memo'
        plan=copy.deepcopy(self.plan); plan['parent_checkpoint_sha256']=digest(checkpoint)
        with self.assertRaises(SuiteError):self.verify(plan,checkpoint)

    def test_no_approval_or_existing_journal_write_flag_can_be_enabled(self):
        for key in ('existing_plan_or_journal_writes','replay_started_or_terminal','restamp_earlier_source_versions','native_entrypoint_implemented'):
            plan=copy.deepcopy(self.plan); plan['execution_protocol'][key]=True
            with self.assertRaises(SuiteError):self.verify(plan)

    def test_new_start_without_terminal_is_unknown_not_retryable(self):
        records=[event(self.plan,1,'s001','STARTED')]
        result=pending_accounting(self.plan,records,execution_source_sha256='8'*64)
        self.assertEqual(result['supplement'],{'intended':3,'terminal':0,'interrupted_unknown':1,'unexecuted':2})
        records.append(event(self.plan,2,'s001','STARTED'))
        with self.assertRaises(SuiteError):pending_accounting(self.plan,records,execution_source_sha256='8'*64)

    def test_terminal_requires_start_and_cannot_be_replayed(self):
        terminal=event(self.plan,1,'s001','TERMINAL','failed','invalid_decision')
        with self.assertRaises(SuiteError):pending_accounting(self.plan,[terminal],execution_source_sha256='8'*64)
        records=[event(self.plan,1,'s001','STARTED'),event(self.plan,2,'s001','TERMINAL','failed','invalid_decision')]
        result=pending_accounting(self.plan,records,execution_source_sha256='8'*64)
        self.assertEqual(result['logical_original_slots']['terminal'],70); self.assertEqual(result['original_cohort']['terminal'],69)
        records.append(event(self.plan,3,'s001','STARTED'))
        with self.assertRaises(SuiteError):pending_accounting(self.plan,records,execution_source_sha256='8'*64)

    def test_supplement_record_missing_duplicate_or_changed_version_is_refused(self):
        row=event(self.plan,1,'s001','STARTED')
        for key in ('proposal_sha256','execution_source_sha256'):
            missing=copy.deepcopy(row); del missing[key]
            with self.assertRaises(SuiteError):pending_accounting(self.plan,[missing],execution_source_sha256='8'*64)
            changed=copy.deepcopy(row); changed[key]='9'*64
            with self.assertRaises(SuiteError):pending_accounting(self.plan,[changed],execution_source_sha256='8'*64)
        with self.assertRaises(SuiteError):pending_accounting(self.plan,[row,row],execution_source_sha256='8'*64)
        with self.assertRaises(SuiteError):pending_accounting(self.plan,[row],execution_source_sha256=None)

    def test_unexpected_terminal_is_retained_and_stops_without_rerun(self):
        records=[event(self.plan,1,'s001','STARTED'),event(self.plan,2,'s001','TERMINAL','failed','invalid_memo')]
        result=pending_accounting(self.plan,records,execution_source_sha256='8'*64)
        self.assertEqual(result['supplement']['terminal'],1); self.assertEqual(result['unexpected_terminal_ids'],['s001'])
        self.assertTrue(result['stop_for_main_review']); self.assertEqual(result['supplement']['unexecuted'],2)

    def test_pure_module_imports_no_actor_native_or_database_execution(self):
        tree=ast.parse((ROOT/'evaluation/upgrade/narrative_correction_v2.py').read_text())
        imports=[n.module or '' for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        imports += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
        self.assertFalse(any(name.startswith('permissioned_research') or name in ('sqlite3','subprocess','socket','threading') or 'runner' in name or 'native.' in name for name in imports))


if __name__=='__main__':unittest.main()
