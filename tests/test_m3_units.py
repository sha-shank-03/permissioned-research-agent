"""Pure oracle/static tests. No actor run, HTTP, process, race or held-out score."""
import ast
import copy
from dataclasses import replace
from pathlib import Path
import unittest
from evaluation.controls import control_record, corrupted_records
from evaluation.oracle import DisclosureInspector, score_record, ratio
from evaluation.suite import validate_rubrics, verify_actor_anchor


class OracleUnits(unittest.TestCase):
    def setUp(self): self.result,self.label,self.snapshot=control_record()

    def corrupt(self,name):
        row=next(row for row in corrupted_records() if row[0]==name)
        self.assertFalse(score_record(row[1],row[2],row[3])['passed'])

    def test_clean_control_is_accepted(self):
        score=score_record(self.result,self.label,self.snapshot)
        self.assertTrue(score['passed']); self.assertEqual(score['entailed_claims'],4)
        self.assertEqual(score['covered_current_topics'],4)

    def test_correct_value_unrelated_citation(self): self.corrupt('unrelated_citation')
    def test_wrong_scalar_nonentailing_span(self): self.corrupt('wrong_scalar_nonentailing')
    def test_stale_revision(self): self.corrupt('stale_revision')
    def test_unauthorized_citation(self): self.corrupt('unauthorized_citation')
    def test_fabricated_conflict(self): self.corrupt('fabricated_conflict')
    def test_inappropriate_abstention(self): self.corrupt('inappropriate_abstention')
    def test_forged_status(self): self.corrupt('forged_status')
    def test_request_vendor_binding(self): self.corrupt('wrong_vendor')
    def test_request_date_binding(self): self.corrupt('wrong_asof')
    def test_duplicate_topic(self): self.corrupt('duplicate_topic')
    def test_missing_topic(self): self.corrupt('missing_topic')
    def test_title_fidelity(self): self.corrupt('wrong_title')
    def test_bool_revision(self): self.corrupt('bool_revision')
    def test_bool_offsets(self): self.corrupt('bool_offset')
    def test_typed_boolean(self): self.corrupt('scalar_type_confusion')
    def test_extra_field(self): self.corrupt('extra_claim_field')
    def test_expired_current_claim(self): self.corrupt('expired_current_claim')
    def test_future_current_claim(self): self.corrupt('future_current_claim')

    def test_dictionary_keys_and_partial_sentinel(self):
        private=self.snapshot.versions[1]
        inspector=DisclosureInspector(None,[{'object_id':private.source_id,'revision':1,'title':private.title,'body':private.body}])
        inspector.inspect('provider_request','alex',{'INVENTED_PRIVATE_CONTROL':'x'},self.snapshot)
        self.assertEqual(len(inspector.violations),1)

    def test_prior_lawful_disclosure_not_rescored(self):
        source=self.snapshot.versions[0]
        inspector=DisclosureInspector(None,[{'object_id':source.source_id,'revision':2,'title':source.title,'body':source.body}])
        inspector.inspect('output','alex',source.body,self.snapshot)
        revoked=replace(self.snapshot,epoch=4,versions=(replace(source,allowed=frozenset()),self.snapshot.versions[1]))
        inspector.inspect('provider_request','alex',{},revoked)
        self.assertEqual(inspector.violations,[])
        inspector.inspect('provider_request','alex',source.body,revoked)
        self.assertEqual(len(inspector.violations),1)

    def test_epoch_mismatch_even_without_text(self):
        inspector=DisclosureInspector(None,[])
        inspector.inspect('cache_lookup','alex',{},self.snapshot,epoch=2)
        self.assertEqual(len(inspector.violations),1)

    def test_empty_denominator_is_null(self): self.assertIsNone(ratio(0,0)['rate'])

    def test_inexact_citation_does_not_count_coverage(self):
        self.result['memo']['topics'][0]['claims'][0]['citation']['start']+=1
        score=score_record(self.result,self.label,self.snapshot)
        self.assertEqual(score['covered_current_topics'],3)
        self.assertEqual(score['exact_citations'],3)

    def test_malformed_topic_state_fails_closed(self):
        self.result['memo']['topics'][0]['state']={}
        self.assertFalse(score_record(self.result,self.label,self.snapshot)['passed'])

    def test_oracle_has_no_production_import(self):
        tree=ast.parse((Path(__file__).resolve().parents[1]/'evaluation/oracle.py').read_text())
        modules=[n.module for n in ast.walk(tree) if isinstance(n,ast.ImportFrom)]
        modules += [a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names]
        self.assertFalse(any(m and 'permissioned_research' in m for m in modules))

    def test_rubric_static_inventory_no_actor(self):
        report=validate_rubrics()
        self.assertEqual(report['cases'],30); self.assertEqual(report['initial_sources'],50)
        self.assertEqual(report['actor_executions'],0); self.assertEqual(report['scores'],0)

    def test_all_abstention_fails_coverage(self):
        for topic in self.result['memo']['topics']: topic.update(state='unknown',reason='insufficient_accessible_evidence',claims=[])
        score=score_record(self.result,self.label,self.snapshot)
        self.assertFalse(score['passed']); self.assertEqual(score['covered_current_topics'],0)
        self.assertEqual(score['answerable_current_topics'],4); self.assertEqual(score['claims'],0)

    def test_legitimate_unknown_accepted(self):
        self.label['expected_outcome']='needs_follow_up'
        self.label['topics']['support_hours']={'state':'unknown','values':[],'supporting_spans':[]}
        self.result.update(outcome='needs_follow_up',phase='NEEDS_FOLLOW_UP')
        self.result['memo']['outcome']='needs_follow_up'
        self.result['memo']['topics'][3].update(state='unknown',reason='insufficient_accessible_evidence',claims=[])
        score=score_record(self.result,self.label,self.snapshot)
        self.assertTrue(score['passed']); self.assertEqual(score['unknown_correct'],1)

    def test_legitimate_stale_only_accepted(self):
        self.label['expected_outcome']='needs_follow_up'
        for topic in self.label['topics'].values(): topic['state']='stale_only'
        self.result.update(outcome='needs_follow_up',phase='NEEDS_FOLLOW_UP')
        self.result['memo']['outcome']='needs_follow_up'
        for topic in self.result['memo']['topics']: topic.update(state='stale_only',reason='evidence_not_current')
        old=replace(self.snapshot.versions[0],review_until='2026-01-01')
        score=score_record(self.result,self.label,replace(self.snapshot,versions=(old,self.snapshot.versions[1])))
        self.assertTrue(score['passed']); self.assertEqual(score['stale_only_correct'],4)
        self.assertEqual(score['current_claims'],0)

    def test_legitimate_peer_conflict_accepted(self):
        source=self.snapshot.versions[0]
        quote='Priority support response: 8 hours.'
        peer=replace(source,source_id='control-peer',title='Control peer',body=quote+'\n')
        self.snapshot=replace(self.snapshot,versions=(*self.snapshot.versions,peer))
        cite={'source_id':peer.source_id,'connector':peer.connector,'revision':2,'title':peer.title,'start':0,'end':len(quote),'quote':quote}
        self.label['allowed_sources'].append(peer.source_id); self.label['eligible_versions'][peer.source_id]=2
        self.label['expected_outcome']='needs_follow_up'
        expected=self.label['topics']['support_hours']; expected.update(state='conflict',values=[4,8])
        expected['supporting_spans'].append({'topic':'support_hours','value':8,**{k:cite[k] for k in ('source_id','revision','start','end','quote')}})
        self.result.update(outcome='needs_follow_up',phase='NEEDS_FOLLOW_UP'); self.result['memo']['outcome']='needs_follow_up'
        topic=self.result['memo']['topics'][3]; topic.update(state='conflict',reason='unresolved_conflict')
        topic['claims'].append({'value':8,'citation':cite})
        score=score_record(self.result,self.label,self.snapshot)
        self.assertTrue(score['passed']); self.assertEqual(score['conflict_correct'],1)

    def test_changed_baseline_manifest_rejected(self):
        actor={'actor':{'manifest_sha256':'actor'},'baseline':{'manifest_sha256':'changed'}}
        suite={'accepted_actor_manifest_sha256':'actor','accepted_baseline_manifest_sha256':'baseline','accepted_actor_freeze_file_sha256':'file'}
        with self.assertRaises(ValueError): verify_actor_anchor(actor,suite,'file')

    def test_changed_actor_freeze_file_rejected(self):
        actor={'actor':{'manifest_sha256':'actor'},'baseline':{'manifest_sha256':'baseline'}}
        suite={'accepted_actor_manifest_sha256':'actor','accepted_baseline_manifest_sha256':'baseline','accepted_actor_freeze_file_sha256':'file'}
        with self.assertRaises(ValueError): verify_actor_anchor(actor,suite,'changed_file')


if __name__=='__main__': unittest.main()
