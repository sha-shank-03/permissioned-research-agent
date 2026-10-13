"""Light metadata validation and synthetic corruption; no private inputs/runtime."""
import copy
import unittest

from evaluation.upgrade.review_projection_v6 import PROJECTION,digest,load,summarize,validate


class ExistingReviewProjectionTests(unittest.TestCase):
    def setUp(self):self.value=load(PROJECTION)
    def row(self,slot):return next(r for r in self.value['rows'] if r['slot']==slot)
    def resign(self,recompute=True):
        if recompute:self.value['summary']=summarize(self.value)
        self.value['payload_sha256']=digest({k:v for k,v in self.value.items() if k!='payload_sha256'})
    def refused(self,recompute=True):
        self.resign(recompute)
        with self.assertRaises(ValueError):validate(self.value)
    def change_physical(self,row):
        p=next(p for p in self.value['physical_intentions'] if row['physical_origin']==p['cohort']+'/'+p['physical_id'])
        for key in ('state','outcome','error'):p[key]=row[key]

    def test_existing_projection_has_four_distinct_denominators(self):
        result=validate(self.value); self.assertEqual(result['logical_slots'],72); self.assertEqual(result['physical_intentions'],77)
        s=self.value['summary']['scopes']
        self.assertEqual((s['development']['intended'],s['development']['reviewed_narrative_completions']),(24,21))
        self.assertEqual((s['capability']['intended'],s['capability']['known_terminal'],s['capability']['reviewed_narrative_completions']),(48,47,35))
        self.assertEqual((s['known_terminal']['intended'],s['known_terminal']['reviewed_narrative_completions']),(71,56))
        self.assertEqual((s['complete_logical']['intended'],s['complete_logical']['interrupted_unknown']),(72,1))
        self.assertIsNone(s['complete_logical']['complete_logical_completion_fraction'])
    def test_quality_scope_is_56_main_model_ratings_not_human_approval(self):
        q=self.value['summary']['scopes']['complete_logical']['quality_conditional_on_reviewed_drafts']
        self.assertEqual((q['reviewed_drafts'],q['claims'],q['eligible_relationships'],q['topic_decisions']),(56,485,485,392))
        self.assertFalse(q['human_approval']); self.assertIsNone(q['reviewer_agreement'])
        self.assertEqual(self.value['summary']['matched_pairs']['reviewed_draft_pairs'],25)
        self.assertIsNone(self.value['summary']['matched_pairs']['full_matrix_quality_delta'])
    def test_physical_alias_inflation_rejected_with_fresh_checksum(self):
        self.value['physical_intentions'].append(copy.deepcopy(self.value['physical_intentions'][0])); self.refused()
    def test_logical_alias_as_new_sample_rejected_with_fresh_checksum(self):
        self.value['rows'].append(copy.deepcopy(self.value['rows'][0])); self.refused()
    def test_erased_unknown_rejected_even_when_physical_and_summary_changed(self):
        r=self.row('capability/a046'); r.update(state='terminal',outcome='failed',error='runner_error',provider_requests=0)
        self.change_physical(r); self.refused()
    def test_unknown_cannot_be_cancelled_or_zero_requests(self):
        self.row('capability/a046')['provider_requests']=0; self.refused()
    def test_failure_cannot_be_relabelled_cancellation(self):
        r=self.row('development/a021'); r.update(outcome='cancelled',error='cancelled'); self.change_physical(r); self.refused()
    def test_failure_cannot_be_relabelled_reviewed_narrative(self):
        r=self.row('development/a021'); mate=self.row('development/a022')
        r.update(outcome='needs_review',error=None,main_review=copy.deepcopy(mate['main_review']))
        r['evidence']['draft_canonical_sha256']=mate['evidence']['draft_canonical_sha256']; self.change_physical(r); self.refused()
    def test_a045_failure_and_initial_mismatch_cannot_be_rewritten(self):
        self.row('capability/a045')['matches_initial_hypothesis']=True; self.refused()
    def test_review_draft_binding_mismatch_rejected_after_resigning(self):
        self.row('development/a001')['main_review']['bindings']['draft_sha256']='0'*64; self.refused()
    def test_review_input_binding_mismatch_rejected(self):
        self.row('development/a001')['main_review']['bindings']['input_sha256']=self.row('capability/a001')['main_review']['bindings']['input_sha256']; self.refused()
    def test_review_receipt_name_cannot_collide_across_populations(self):
        self.row('capability/a001')['main_review']['record_name']='a001-main-review.json'; self.refused()
    def test_missing_review_cannot_be_dropped_from_review_denominator(self):
        self.row('development/a001')['main_review']=None; self.refused()
    def test_null_cost_cannot_be_invented_zero(self):
        self.value['gates']['actual_cost']=0; self.refused()
    def test_null_estimated_cost_and_tokens_cannot_be_invented_zero(self):
        original=copy.deepcopy(self.value)
        for field in ('estimated_cost','model_tokens'):
            with self.subTest(field=field):self.value=copy.deepcopy(original); self.value['gates'][field]=0; self.refused()
    def test_cross_population_same_attempt_ID_is_not_same_slot(self):
        r=self.row('development/a001'); r.update(population='capability',task_id='capability-qual-01',slot='capability/a001'); self.refused(recompute=False)
    def test_join_by_attempt_ID_alone_loses_24_slots_and_is_rejected(self):
        collapsed={r['original_attempt']:r for r in self.value['rows']}; self.assertEqual(len(collapsed),48)
        self.value['rows']=list(collapsed.values()); self.refused(recompute=False)
    def test_unequal_policy_configuration_rejected(self):
        self.row('development/a002')['config_sha256']='0'*64; self.refused()
    def test_cross_population_source_lineage_overlap_rejected(self):
        development=next(t for t in self.value['tasks'] if t['population']=='development')
        capability=next(t for t in self.value['tasks'] if t['population']=='capability')
        capability['lineage_id']=development['lineage_id']; self.refused()
    def test_full72_request_total_cannot_be_claimed_from_known71(self):
        self.value['summary']['scopes']['complete_logical']['provider_requests_complete_logical']=238; self.refused(recompute=False)
    def test_expected_non_narrative_terminal_is_not_narrative_success(self):
        self.assertEqual(self.value['summary']['non_narrative_outcomes']['failed'],13)
        self.assertEqual(self.value['summary']['non_narrative_outcomes']['cancelled'],2)
        self.assertFalse(self.value['summary']['non_narrative_outcomes']['are_successful_narrative_completion'])
        self.assertEqual(self.value['summary']['non_narrative_outcomes']['matches_corrected_contract'],15)
    def test_full_matrix_quality_delta_remains_pending(self):
        self.value['summary']['matched_pairs']['full_matrix_quality_delta']=0; self.refused(recompute=False)
    def test_model_review_cannot_open_human_live_unseen_or_demo_gate(self):
        original=copy.deepcopy(self.value)
        for field in ('human_signoff','live_model','Google_sandbox','main_unseen','demo_ready','publication_ready','final_acceptance'):
            with self.subTest(field=field):self.value=copy.deepcopy(original); self.value['gates'][field]=True; self.refused()
    def test_reviewer_agreement_remains_unknown(self):
        self.row('development/a001')['main_review']['reviewer_agreement']=1; self.refused()
    def test_public_check_does_not_claim_private_signature_authentication(self):
        result=validate(self.value); self.assertFalse(result['private_receipt_authentication_performed'])
        self.assertFalse(result['publication_or_demo_authorized']); self.assertEqual(result['new_actor_runs'],0)


if __name__=='__main__':unittest.main()
