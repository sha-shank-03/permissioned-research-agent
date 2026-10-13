"""No SQLite connection, HTTP, subprocess, native actor or inference allowed."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from evaluation.upgrade import narrative_continuation_v4 as v4
from evaluation.upgrade.narrative_suite.data import InputCatalog, SuiteError
from evaluation.upgrade.narrative_supplement.journal import ARTIFACTS
from tests.test_upgrade_narrative_supplement_pure import metrics, raw_actor
from evaluation.upgrade.public_contract_fixtures_v5 import build_contract_root, projections, POPULATION

CP=v4.read_json(v4.ROOT/v4.CHECKPOINT)
PROPOSAL=v4.read_json(v4.ROOT/v4.PROPOSAL)
REFS=projections(v4.ROOT)


def blueprint():
    return {'schema':'narrative-two-slot-plan-v4','execution_source_manifest_sha256':'a'*64,
        'execution_code_sha256':'b'*64,'actor_source_manifest_sha256':CP['v3_plan']['actor_source_manifest_sha256'],
        'proposal_sha256':v4.file_hash(v4.ROOT/v4.PROPOSAL),'checkpoint_sha256':v4.file_hash(v4.ROOT/v4.CHECKPOINT),
        'parent_bindings':copy.deepcopy(PROPOSAL['parent_bindings']),
        'original_actor_bindings':copy.deepcopy(CP['v3_plan']['original_actor_bindings']),
        'attempts':copy.deepcopy(PROPOSAL['attempts']),'limits':copy.deepcopy(v4.LIMITS),
        'lease_label':'pure-v4-only','lease_label_is_not_authorization':True,
        'output_relative_root':v4.OUTPUT,'parent_relative_root':v4.PARENT,
        'semantic_quality':None,'aggregate_full_plan_safety':None}


class FakeContext:
    def __init__(self,out):
        self.out=out; self.blueprint=blueprint(); self.execution_sha256='a'*64
        self.verifications=0; self.fail_on=None; self.byte_count=0
    def verify(self):
        self.verifications+=1
        if self.verifications==self.fail_on:raise SuiteError('injected_parent_drift')
        return True
    def generated_bytes(self):return self.byte_count
    def load_task(self,entry):return InputCatalog(v4.ROOT/'fixtures/upgrade/narrative-v2').load_input(entry['task_id'])


class EligibilityTests(unittest.TestCase):
    def setUp(self):
        self.cp=copy.deepcopy(CP); self.proposal=copy.deepcopy(PROPOSAL)
        self.plan=copy.deepcopy(CP['v3_plan']); self.state=copy.deepcopy(CP['v3_state'])
        self.inventory=copy.deepcopy(CP['partial_inventory']); self.bindings=copy.deepcopy(PROPOSAL['parent_bindings'])
        self.refs=copy.deepcopy(REFS)
    def check(self):return v4.validate_partial(self.cp,self.proposal,self.plan,self.state,self.inventory,self.bindings,*self.refs)
    def test_exact_snapshot_only_allows_two_never_started_slots(self):
        self.assertTrue(self.check())
        self.assertEqual([(a['id'],a['prior_supplement_id'],a['parent_attempt_id']) for a in self.proposal['attempts']],[('c001','s002','a047'),('c002','s003','a048')])
    def test_s001_or_a046_recovery_alias_is_refused(self):
        for field,value in (('prior_supplement_id','s001'),('parent_attempt_id','a046'),('id','s001')):
            with self.subTest(field=field):
                self.proposal=copy.deepcopy(PROPOSAL); self.proposal['attempts'][0][field]=value
                with self.assertRaises(SuiteError):self.check()
    def test_duplicate_prior_or_original_alias_is_refused(self):
        for field in ('prior_supplement_id','parent_attempt_id','id'):
            with self.subTest(field=field):
                self.proposal=copy.deepcopy(PROPOSAL); self.proposal['attempts'][1][field]=self.proposal['attempts'][0][field]
                with self.assertRaises(SuiteError):self.check()
    def test_prior_started_or_terminal_pending_slot_is_refused(self):
        for state in ('interrupted_unknown','terminal'):
            with self.subTest(state=state):
                self.state=copy.deepcopy(CP['v3_state']); self.state['slots']['s002']['state']=state
                with self.assertRaises(SuiteError):self.check()
    def test_s001_unknown_cannot_become_failure_or_success(self):
        for state in ('terminal','unexecuted'):
            self.state=copy.deepcopy(CP['v3_state']); self.state['slots']['s001']['state']=state
            with self.assertRaises(SuiteError):self.check()
    def test_domain_outcome_and_request_count_remain_unknown(self):
        for key,value in (('domain_outcome','failed'),('provider_requests',0),('process_roles_established',True),('python_count_established',True)):
            with self.subTest(key=key):
                self.cp=copy.deepcopy(CP); self.cp['main_review_summary'][key]=value
                with self.assertRaises(SuiteError):self.check()
    def test_every_parent_binding_change_refused(self):
        for key in v4.BINDINGS:
            with self.subTest(key=key):
                self.bindings=copy.deepcopy(PROPOSAL['parent_bindings']); self.bindings[key]='0'*64
                with self.assertRaises(SuiteError):self.check()
    def test_changed_partial_lock_claim_or_corpus_hash_refused(self):
        for name in ('writer.lock','run.claim.json','s001/corpus.sqlite'):
            with self.subTest(name=name):
                self.inventory=copy.deepcopy(CP['partial_inventory']); next(e for e in self.inventory if e['path']==name)['sha256']='0'*64
                with self.assertRaises(SuiteError):self.check()
    def test_changed_actor_data_rubric_policy_or_budget_refused(self):
        for key,value in (('input_sha256','0'*64),('rubric_sha256','0'*64),('config_sha256','0'*64),('policy','fixed')):
            with self.subTest(key=key):
                self.proposal=copy.deepcopy(PROPOSAL); self.proposal['attempts'][0][key]=value
                with self.assertRaises(SuiteError):self.check()
    def test_77_physical_intentions_cannot_be_77_logical_samples(self):
        self.proposal['logical_intended']=77
        with self.assertRaises(SuiteError):self.check()
    def test_fabricated_main_completed_run_refused(self):
        self.refs[2]['commands'][-1]['exit_code']=0
        with self.assertRaises(SuiteError):self.check()
    def test_report_cannot_grant_dispatch(self):
        self.refs[1]['dispatch_authorized']=True
        with self.assertRaises(SuiteError):self.check()


class ControllerTests(unittest.TestCase):
    def setUp(self):
        scratch=v4.ROOT/'work'; scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='u2b2-v4-pure-',dir=scratch)
        self.base=Path(self.temp.name).resolve(); self.assertTrue(self.base.is_relative_to(scratch.resolve()))
        self.context=FakeContext(self.base/'continuation'); self.calls=[]
        self.guards=[patch('evaluation.upgrade.narrative_continuation_v4.native_actor',side_effect=AssertionError('native actor forbidden')),
            patch('sqlite3.connect',side_effect=AssertionError('SQLite connection forbidden')),
            patch('socket.socket',side_effect=AssertionError('socket integration forbidden')),
            patch('subprocess.Popen',side_effect=AssertionError('subprocess integration forbidden'))]
        self.mocks=[p.start() for p in self.guards]
    def tearDown(self):
        for mock in self.mocks:self.assertEqual(mock.call_count,0)
        for guard in reversed(self.guards):guard.stop()
        self.assertTrue(self.base.is_relative_to((v4.ROOT/'work').resolve())); self.temp.cleanup()
    def actor(self,*args):self.calls.append(args); return raw_actor(*args)
    def journal(self):return v4.prepare(self.context)
    def report(self):return v4.reconcile(self.context.out,'a'*64,context=self.context)
    def no_replay(self):
        before=(self.context.out/'journal.jsonl').read_bytes(); count=len(self.calls)
        with self.assertRaises(SuiteError):v4.run(self.context,actor=self.actor)
        self.assertEqual((self.context.out/'journal.jsonl').read_bytes(),before); self.assertEqual(len(self.calls),count)
    def hook(self,event,phase):
        called=[]
        def fail(kind,stage):
            if kind==event and stage==phase and not called:called.append(True); raise OSError('Bearer private-credential-shaped-test')
        return fail
    def test_prepare_persists_both_intentions_and_full_bindings(self):
        journal=self.journal(); state=journal.inspect()
        self.assertEqual([r['event'] for r in state['records']],['INTENDED','INTENDED'])
        self.assertEqual(state['intended'],2); self.assertTrue(state['intended_complete'])
        self.assertEqual(journal.plan['parent_bindings'],PROPOSAL['parent_bindings'])
        self.assertEqual(journal.plan['original_actor_bindings'],CP['v3_plan']['original_actor_bindings'])
    def test_prepare_never_overwrites_existing_root(self):
        self.journal(); before=(self.context.out/'plan.json').read_bytes()
        with self.assertRaises(FileExistsError):v4.prepare(self.context)
        self.assertEqual((self.context.out/'plan.json').read_bytes(),before)
    def test_actor_starts_only_after_both_intentions_and_started_ack(self):
        self.journal()
        def actor(task,policy,path,bindings):
            state=v4.Journal(self.context.out).inspect()
            self.assertTrue(state['intended_complete']); self.assertEqual(state['slots'][path.name]['state'],'interrupted_unknown')
            self.assertEqual(task['id'],'capability-budget-04'); self.assertNotIn('required_relationships',task)
            self.assertEqual(bindings,CP['v3_plan']['original_actor_bindings'])
            return self.actor(task,policy,path,bindings)
        self.assertEqual(v4.run(self.context,actor=actor),0); self.assertEqual(len(self.calls),2)
    def test_two_terminals_retain_s001_unknown_and_honest_denominators(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor),0); report=self.report()
        self.assertEqual(report['logical_original_slots'],{'intended':72,'terminal':71,'interrupted_unknown':1,'unexecuted':0})
        self.assertEqual(report['physical_intent_records'],77)
        self.assertEqual(report['physical_terminal_records'],71); self.assertEqual(report['physical_interrupted_unknown_records'],1)
        self.assertEqual(report['physical_unexecuted_intent_records'],5)
        self.assertEqual(report['v3_frozen_cohort']['s001']['state'],'interrupted_unknown')
        self.assertIsNone(report['v3_frozen_cohort']['s001']['domain_outcome']); self.assertIsNone(report['v3_frozen_cohort']['s001']['provider_requests'])
        self.assertFalse(report['dispatch_authorized']); self.assertTrue(report['overall_main_review_required'])
        self.assertEqual(report['original_cohort']['terminal'],69); self.assertEqual(report['original_cohort']['stopped_exit'],2)
        self.assertFalse(report['aliases_are_additional_samples']); self.no_replay()
    def test_failed_actor_unknown_adds_to_frozen_unknown_not_domain_failure(self):
        self.journal()
        def actor(*args):self.calls.append(args); raise RuntimeError('private-credential-shaped-test')
        self.assertEqual(v4.run(self.context,actor=actor),2); report=self.report()
        self.assertEqual(report['logical_original_slots'],{'intended':72,'terminal':69,'interrupted_unknown':2,'unexecuted':1})
        self.assertEqual(report['physical_interrupted_unknown_records'],2); self.assertEqual(report['continuation']['terminal'],0)
        self.assertNotIn('private-credential',json.dumps(report)); self.no_replay()
    def test_unexpected_terminal_saved_before_classification_and_stops_second(self):
        self.journal()
        def actor(*args):self.calls.append(args); return raw_actor(*args,outcome='failed',error='budget_exhausted')
        self.assertEqual(v4.run(self.context,actor=actor),2); self.assertEqual(len(self.calls),1)
        state=v4.Journal(self.context.out).inspect(); self.assertEqual([r['event'] for r in state['records']][-3:],['RETURNED','TERMINAL','CLASSIFIED'])
        self.assertEqual(state['slots']['c001']['metrics']['error'],'budget_exhausted'); self.assertFalse(state['slots']['c001']['classified'])
        self.assertEqual(self.report()['unexpected_terminal_ids'],['c001']); self.no_replay()
    def test_all_old_original_and_prior_alias_ids_refused_before_bytes(self):
        journal=self.journal(); before=(journal.root/'journal.jsonl').read_bytes()
        with journal.writer():
            for sid in ('s001','s002','s003','a045','a046','a047','a048','c002'):
                with self.subTest(sid=sid),self.assertRaises(SuiteError):journal.start(sid)
        self.assertEqual((journal.root/'journal.jsonl').read_bytes(),before)
    def test_duplicate_started_returned_terminal_and_classified_refused(self):
        journal=self.journal()
        with journal.writer():
            journal.start('c001')
            with self.assertRaises(SuiteError):journal.start('c001')
            journal.returned('c001','c'*64)
            with self.assertRaises(SuiteError):journal.returned('c001','c'*64)
            journal.terminal('c001',metrics('cancelled','cancelled'),{k:'c'*64 for k in ARTIFACTS})
            with self.assertRaises(SuiteError):journal.terminal('c001',metrics('cancelled','cancelled'),{k:'c'*64 for k in ARTIFACTS})
            journal.classify('c001',True)
            with self.assertRaises(SuiteError):journal.classify('c001',True)
    def test_false_matching_classification_cannot_start_later_slot(self):
        journal=self.journal()
        with journal.writer():
            journal.start('c001'); journal.returned('c001','c'*64)
            journal.terminal('c001',metrics('failed','budget_exhausted'),{k:'c'*64 for k in ARTIFACTS})
            before=(journal.root/'journal.jsonl').read_bytes()
            with self.assertRaises(SuiteError):journal.classify('c001',True)
            with self.assertRaises(SuiteError):journal.start('c002')
            self.assertEqual((journal.root/'journal.jsonl').read_bytes(),before)
        self.assertFalse(self.report()['dispatch_authorized'])
    def test_lost_started_ack_retains_new_unknown_without_actor(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor,hook=self.hook('STARTED','after_fsync')),2)
        self.assertEqual(self.calls,[]); self.assertEqual(self.report()['logical_original_slots']['interrupted_unknown'],2); self.no_replay()
    def test_started_write_failure_retains_two_pending_intentions(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor,hook=self.hook('STARTED','before_write')),2)
        self.assertEqual(self.calls,[]); self.assertEqual(self.report()['logical_original_slots']['unexecuted'],2); self.no_replay()
    def test_lost_returned_ack_preserves_receipt_and_unknown(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor,hook=self.hook('RETURNED','after_fsync')),2)
        self.assertTrue((self.context.out/'c001.returned.json').is_file()); self.assertEqual(self.report()['continuation']['interrupted_unknown'],1); self.no_replay()
    def test_lost_terminal_ack_retains_terminal_but_no_later_dispatch(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor,hook=self.hook('TERMINAL','after_fsync')),2)
        report=self.report(); self.assertEqual(report['continuation']['terminal'],1); self.assertIsNone(report['slots'][0]['classified'])
        self.assertEqual(report['logical_original_slots']['interrupted_unknown'],1); self.assertEqual(len(self.calls),1); self.no_replay()
    def test_terminal_write_failure_retains_usage_and_unknown(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor,hook=self.hook('TERMINAL','before_write')),2)
        self.assertTrue((self.context.out/'c001.usage.json').is_file()); self.assertEqual(self.report()['continuation']['interrupted_unknown'],1); self.no_replay()
    def test_lost_classification_ack_stops_batch_even_for_expected_terminal(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor,hook=self.hook('CLASSIFIED','after_fsync')),2)
        self.assertIn('durability_unknown',self.report()['halt_codes']); self.assertEqual(len(self.calls),1); self.no_replay()
    def test_partial_terminal_retains_both_new_slots_unknown_plus_frozen_unknown(self):
        journal=self.journal()
        with journal.writer():journal.start('c001')
        with (journal.root/'journal.jsonl').open('ab') as stream:stream.write(b'{"event":"TERMINAL"')
        before=(journal.root/'journal.jsonl').read_bytes(); report=self.report()
        self.assertEqual(report['logical_original_slots'],{'intended':72,'terminal':69,'interrupted_unknown':3,'unexecuted':0})
        self.assertEqual((journal.root/'journal.jsonl').read_bytes(),before); self.assertTrue(report['torn_tail'])
        with self.assertRaises(SuiteError):v4.run(self.context,actor=self.actor)
    def test_missing_trace_retains_receipt_and_usage_without_fabricated_terminal(self):
        self.journal()
        def actor(*args):result=self.actor(*args); (args[2]/'trace.json').unlink(); return result
        self.assertEqual(v4.run(self.context,actor=actor),2)
        self.assertTrue((self.context.out/'c001.returned.json').is_file()); self.assertTrue((self.context.out/'c001.usage.json').is_file())
        self.assertEqual(self.report()['continuation']['interrupted_unknown'],1); self.no_replay()
    def test_invalid_closed_metrics_are_not_published(self):
        self.journal()
        def actor(*args):return raw_actor(*args,mutate=lambda m,r,t:m.update(error='private-credential-shaped-test'))
        self.assertEqual(v4.run(self.context,actor=actor),2)
        self.assertNotIn('private-credential',json.dumps(self.report())); self.assertEqual(self.report()['continuation']['terminal'],0)
    def test_pre_dispatch_parent_drift_stops_before_actor(self):
        self.journal(); self.context.fail_on=3
        self.assertEqual(v4.run(self.context,actor=self.actor),2); self.assertEqual(self.calls,[])
        self.assertEqual(self.report()['continuation']['unexecuted'],2); self.no_replay()
    def test_post_return_parent_drift_keeps_terminal_but_blocks_next(self):
        self.journal(); self.context.fail_on=4
        self.assertEqual(v4.run(self.context,actor=self.actor),2)
        self.assertEqual(self.report()['continuation']['terminal'],1); self.assertEqual(len(self.calls),1); self.no_replay()
    def test_disk_reservation_stops_before_actor(self):
        self.journal(); self.context.byte_count=v4.LIMITS['raw_output_bytes']
        self.assertEqual(v4.run(self.context,actor=self.actor),2); self.assertEqual(self.calls,[])
        self.assertIn('budget_stop',self.report()['halt_codes'])
    def test_deadline_reservation_stops_before_actor(self):
        self.journal(); times=iter((0,400))
        self.assertEqual(v4.run(self.context,actor=self.actor,clock=lambda:next(times)),2)
        self.assertEqual(self.calls,[]); self.assertIn('budget_stop',self.report()['halt_codes'])
    def test_claim_without_started_cannot_be_reused(self):
        self.journal(); v4.write_exclusive(self.context.out/'run.claim.json',{'single_invocation':True})
        report=self.report(); self.assertEqual(report['logical_original_slots']['unexecuted'],2)
        self.assertTrue(report['continuation_stop_for_main_review']); self.no_replay()
    def test_incomplete_intentions_have_denominators_but_no_dispatch(self):
        with self.assertRaises(OSError):v4.prepare(self.context,hook=self.hook('INTENDED','after_fsync'))
        report=self.report(); self.assertEqual(report['continuation']['persisted_intention_records'],1)
        self.assertEqual(report['continuation']['intended'],2); self.assertEqual(report['physical_intent_records'],77)
        with self.assertRaises(SuiteError):v4.run(self.context,actor=self.actor)
    def test_new_writer_lock_is_not_taken_over(self):
        journal=self.journal(); (journal.root/'writer.lock').write_text('unknown owner')
        with self.assertRaises(SuiteError):v4.run(self.context,actor=self.actor)
        self.assertEqual((journal.root/'writer.lock').read_text(),'unknown owner')
    def test_read_only_report_never_writes_or_dispatches(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor),0)
        before=(self.context.out/'journal.jsonl').read_bytes(); count=len(self.calls)
        for _ in range(2):
            report=self.report(); self.assertFalse(report['dispatch_authorized']); self.assertTrue(report['overall_main_review_required'])
        self.assertEqual((self.context.out/'journal.jsonl').read_bytes(),before); self.assertEqual(len(self.calls),count)
    def test_public_redaction_and_unknown_cost_preserved(self):
        self.journal(); self.assertEqual(v4.run(self.context,actor=self.actor),0)
        self.assertNotIn('private-credential',json.dumps(self.report()))
        self.assertNotIn('private-credential',(self.context.out/'journal.jsonl').read_text())
        usage=v4.read_json(self.context.out/'c001.usage.json'); self.assertIsNone(usage['actual_cost']); self.assertIsNone(usage['model_tokens'])
        self.assertFalse((self.context.out/'c001/corpus.sqlite').read_bytes().startswith(b'SQLite format'))
    def test_wrong_execution_source_report_refused(self):
        self.journal()
        with self.assertRaises(SuiteError):v4.reconcile(self.context.out,'b'*64)
    def test_extra_secret_plan_and_journal_fields_refused_before_bytes(self):
        plan=blueprint(); plan['token']='private-credential'
        with self.assertRaises(SuiteError):v4.validate_plan(plan)
        journal=self.journal(); before=(journal.root/'journal.jsonl').read_bytes()
        with journal.writer():
            with self.assertRaises(SuiteError):journal._append('STARTED','c001',{'body':'private-credential'})
        self.assertEqual((journal.root/'journal.jsonl').read_bytes(),before)
    def test_context_contract_never_calls_native_adapter(self):
        self.journal()
        self.assertFalse(self.report()['dispatch_authorized'])
        self.mocks[0].assert_not_called()


class FrozenContextReadOnlyTests(unittest.TestCase):
    def test_public_synthetic_sealed_context_hashes_inputs_and_preserves_lock(self):
        scratch=v4.ROOT/'work'; scratch.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='v4-public-context-',dir=scratch) as temp:
            base=Path(temp).resolve(); self.assertTrue(base.is_relative_to(scratch.resolve()))
            with patch('sqlite3.connect',side_effect=AssertionError('SQLite connection forbidden')) as blocked:
                root=build_contract_root(base/'checkout',v4.ROOT,include_v4=True)
                before=v4.inventory(root/v4.PARENT)
                context=v4.Context(root,v4.file_hash(root/v4.SEAL),root/POPULATION,'public-v4-context')
                self.assertTrue(context.verify()); self.assertFalse(context.out.exists()); blocked.assert_not_called()
            self.assertEqual(v4.inventory(root/v4.PARENT),before)
            self.assertEqual(v4.file_hash(root/v4.PARENT/'writer.lock'),next(e['sha256'] for e in before if e['path']=='writer.lock'))
            self.assertTrue(base.is_relative_to(scratch.resolve()))


if __name__=='__main__':unittest.main()
